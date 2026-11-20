"""调度使用真实 PostgreSQL、检查点和队列；只操作随机隔离 schema。"""

import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor

import procrastinate
from alembic import command
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.conninfo import make_conninfo
from sqlalchemy import func, select, text
from test_capture_postgres import native as native
from test_llm_schedule import at, setup

from wiki_app import db
from wiki_app.config import get_settings
from wiki_app.main import app
from wiki_app.models import ModelCall, ProcessingRun, Source
from wiki_app.modules.model_management import scheduling


def test_schedule_persists_and_real_queue_resumes_without_duplicate_calls(native, monkeypatch):
    engine, config = native
    command.upgrade(config, "head")
    scheduling.prepare_schema(engine)
    monkeypatch.setenv("MODEL_CREDENTIAL_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    scheduling.prepare_schema(engine)
    with PostgresSaver.from_conn_string(get_settings().postgres_dsn) as saver:
        saver.setup()
    with TestClient(app) as client:
        clock, requests, _ = setup(client, monkeypatch, at(12), end_policy="wait_next_window")
        record = client.post('/api/records', json={"content": "Rerank 是候选重排序。"}).json()
        run_id = client.get('/api/records/' + record['id']).json()['runs'][0]['id']

        from wiki_app import worker

        # 队列函数与枚举只在本测试 schema 解析，避免 public 中的正式队列重名函数。
        queue = procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=make_conninfo(
            get_settings().postgres_dsn, options="-csearch_path=" + config.attributes["version_table_schema"])))
        @queue.task(queue="verify-schedule")
        def process(run_id):
            worker.process_run.func(run_id=run_id)

        async def consume(initialize=False):
            async with queue.open_async():
                if initialize:
                    await queue.schema_manager.apply_schema_async()
                await process.configure(lock=run_id, queueing_lock=run_id).defer_async(run_id=run_id)
                await queue.run_worker_async(queues=["verify-schedule"], wait=False, concurrency=1,
                    install_signal_handlers=False, listen_notify=False)

        def consume_once(initialize=False):
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
                runner.run(consume(initialize))

        consume_once(initialize=True)
        with db.SessionLocal() as session:
            assert session.get(ProcessingRun, run_id).status == "waiting_schedule"
            assert session.scalar(select(func.count()).select_from(ModelCall)) == 0
            source_count = session.scalar(select(func.count()).select_from(Source))
        assert not requests
        # 连接池重建模拟进程重启，等待与策略仍来自数据库。
        engine.dispose()
        clock[0] = at(1, day=21)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: scheduling.tick(), range(2)))
        consume_once()
        count = len(requests)
        consume_once()
        assert len(requests) == count and count > 0
        with db.SessionLocal() as session:
            assert session.get(ProcessingRun, run_id).status == 'completed'
            assert session.scalar(select(func.count()).select_from(Source)) == source_count
            assert set(session.scalars(text("SELECT status::text FROM procrastinate_jobs"))) == {"succeeded"}
