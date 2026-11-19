"""持久化队列负责调度；LangGraph 负责流程恢复，执行记录兼作可靠待办。"""

import asyncio
import logging
import signal
import sys
import time

import procrastinate
from langgraph.checkpoint.postgres import PostgresSaver
from procrastinate.worker import Worker
from sqlalchemy import select

from wiki_app import db
from wiki_app.config import get_settings
from wiki_app.logging_config import configure_logging
from wiki_app.models import CaptureInsight, ProcessingRun, Record, now
from wiki_app.modules.processing.graph import execute_run

logger = logging.getLogger("wiki_app.worker")
app = procrastinate.App(
    connector=procrastinate.PsycopgConnector(conninfo=get_settings().postgres_dsn)
)


@app.task(queue="pipeline", retry=procrastinate.RetryStrategy(
    max_attempts=get_settings().worker_max_attempts, wait=get_settings().worker_retry_wait_seconds
))
def process_run(run_id: str):
    """相同执行加锁；业务提交后、检查点前退出也不会重复写结果。"""
    with PostgresSaver.from_conn_string(get_settings().postgres_dsn) as saver:
        execute_run(run_id, saver)


@app.task(queue="pipeline", retry=procrastinate.RetryStrategy(
    max_attempts=get_settings().worker_max_attempts, wait=get_settings().worker_retry_wait_seconds
))
def process_insight(insight_id: str):
    """与正常整理共用 Worker；独立任务和状态确保解释失败不影响保存。"""
    from wiki_app.modules.review.insights import generate_insight

    generate_insight(insight_id)


async def dispatch():
    settings = get_settings()
    last_review_check = float("-inf")
    while True:
        try:
            # 按心跳判断停滞，不能因音视频耗时长而误重试仍在执行的任务。
            stalled = await app.job_manager.get_stalled_jobs(
                queue="pipeline", seconds_since_heartbeat=settings.worker_stalled_seconds
            )
            for job in stalled:
                await app.job_manager.retry_job_by_id_async(job.id, retry_at=now())
            await app.job_manager.prune_stalled_workers(
                seconds_since_heartbeat=settings.worker_stalled_seconds)
            with db.SessionLocal() as session:
                pending = list(
                    session.scalars(
                        select(ProcessingRun.id)
                        .where(
                            ProcessingRun.status.in_(("queued", "running")),
                        )
                        .order_by(ProcessingRun.created_at)
                        .limit(settings.dispatch_batch_size)
                    )
                )
            for run_id in pending:
                try:
                    await process_run.configure(
                        lock=f"run:{run_id}", queueing_lock=f"run:{run_id}"
                    ).defer_async(run_id=run_id)
                except procrastinate.exceptions.AlreadyEnqueued:
                    pass
            if settings.review_insights_enabled:
                from wiki_app.modules.review.insights import prepare_insight

                with db.SessionLocal() as session:
                    missing = list(session.scalars(select(ProcessingRun.id).join(Record).where(
                        ProcessingRun.status == "completed", ProcessingRun.version == Record.version,
                        Record.archived.is_(False), ~select(CaptureInsight.id).where(
                            CaptureInsight.run_id == ProcessingRun.id).exists()
                    ).limit(settings.dispatch_batch_size)))
                for run_id in missing:
                    prepare_insight(run_id)
                with db.SessionLocal() as session:
                    insights = list(session.scalars(select(CaptureInsight.id).where(
                        CaptureInsight.status == "pending").limit(settings.dispatch_batch_size)))
                for identifier in insights:
                    try:
                        await process_insight.configure(lock=f"insight:{identifier}",
                            queueing_lock=f"insight:{identifier}").defer_async(insight_id=identifier)
                    except procrastinate.exceptions.AlreadyEnqueued:
                        pass
            if time.monotonic() - last_review_check >= settings.review_check_interval_seconds:
                from wiki_app.modules.review.service import generate_daily

                generate_daily()
                last_review_check = time.monotonic()
        except Exception:
            logger.exception("队列派发失败，待办仍保存在数据库，将再次尝试")
        await asyncio.sleep(settings.dispatch_interval_seconds)


async def serve():
    settings = get_settings()
    async with app.open_async():
        app.perform_import_paths()
        consumer = Worker(
            app, queues=["pipeline"], concurrency=settings.worker_concurrency,
            install_signal_handlers=False, shutdown_graceful_timeout=settings.worker_shutdown_seconds,
            update_heartbeat_interval=settings.worker_heartbeat_seconds,
            stalled_worker_timeout=settings.worker_stalled_seconds,
        )
        loop = asyncio.get_running_loop()
        # Windows 的事件循环不支持 add_signal_handler；转到事件循环调用公开 stop()，
        # 让消费者先停止领任务，再等待进行中的任务，而非直接取消所有协程。
        handled = (signal.SIGINT, signal.SIGTERM) + ((signal.SIGBREAK,) if sys.platform == "win32" else ())
        previous = {
            sig: signal.signal(sig, lambda *_: loop.call_soon_threadsafe(consumer.stop))
            for sig in handled
        }
        dispatcher = asyncio.create_task(dispatch())
        try:
            await consumer.run()
        finally:
            dispatcher.cancel()
            await asyncio.gather(dispatcher, return_exceptions=True)
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def main():
    """以可导入模块启动，保持队列中的任务函数名稳定，并初始化本地日志。"""
    configure_logging("worker")
    logger.info("Worker 启动 queue=pipeline concurrency=%d data_dir=%s",
                get_settings().worker_concurrency, get_settings().data_dir.resolve())
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(serve())
        logger.info("Worker 已停止，队列连接已关闭")
    except KeyboardInterrupt:
        logger.info("Worker 已收到停止信号")
    except Exception:
        logger.exception("Worker 退出异常，请检查数据库、队列迁移和上述错误")
        raise


if __name__ == "__main__":
    main()
