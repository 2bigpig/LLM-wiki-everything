"""复用现有 Worker 的每日时段调度；等待不占消费者，不创建调用或预算预留。"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import inspect, select
from sqlalchemy.schema import CreateColumn

from wiki_app import db
from wiki_app.config import LLMScheduleSettings, get_settings
from wiki_app.errors import AppError
from wiki_app.models import CaptureInsight, ModelPreferences, ProcessingRun, Record, now

from .service import config_lock, preferences

WAITING = "waiting_schedule"


class ScheduleDeferred(AppError):
    def __init__(self, scheduled_for, policy):
        self.scheduled_for = scheduled_for
        local = scheduled_for.astimezone(ZoneInfo(policy.timezone))
        super().__init__(f"等待 LLM 执行时段，预计 {local:%m-%d %H:%M}（{policy.timezone}）继续。", WAITING, 409)


def prepare_schema(engine):
    """开发期幂等补齐当前字段，保留全部数据，不新增版本迁移或迁移记录。"""
    targets = {ModelPreferences: ("llm_schedule",), ProcessingRun: ("llm_admitted_at", "scheduled_for"),
               CaptureInsight: ("llm_admitted_at", "scheduled_for")}
    with engine.begin() as connection:
        # 多个准备入口同时启动时序列化 DDL；SQLite 只用于隔离业务测试。
        if connection.dialect.name == "postgresql":
            connection.exec_driver_sql("SELECT pg_advisory_xact_lock(81020261121)")
        for model, names in targets.items():
            table = model.__table__
            columns = {c["name"] for c in inspect(connection).get_columns(table.name)}
            for name in names:
                if name not in columns:
                    column = str(CreateColumn(table.c[name]).compile(dialect=connection.dialect))
                    connection.exec_driver_sql(f'ALTER TABLE "{table.name}" ADD COLUMN {column}')


def read_policy(session):
    row = preferences(session)
    return LLMScheduleSettings.model_validate(row.llm_schedule or
        get_settings().model_management.schedule_defaults.model_dump())


def is_open(policy, current):
    if not policy.enabled:
        return True
    value = current.astimezone(ZoneInfo(policy.timezone)).strftime("%H:%M")
    return policy.start <= value < policy.end if policy.start < policy.end else value >= policy.start or value < policy.end


def next_open(policy, current):
    """区间左闭右开，跨午夜合法；返回 UTC 时间，避免宿主机时区影响队列。"""
    if is_open(policy, current):
        return current
    zone = ZoneInfo(policy.timezone)
    local = current.astimezone(zone)
    hour, minute = map(int, policy.start.split(":"))
    candidate = datetime(local.year, local.month, local.day, hour, minute, tzinfo=zone).astimezone(timezone.utc)
    if candidate <= current:
        day = local.date() + timedelta(days=1)
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone).astimezone(timezone.utc)
    return candidate


def may_continue(policy, target, current):
    return is_open(policy, current) or policy.end_policy == "finish_batch" and target.llm_admitted_at is not None


def authorize(session, run, stage, current=None):
    """每次真正的后台 LLM 请求前检查；测试和本地向量不经过此门禁。"""
    current = current or now()
    target = session.scalar(select(CaptureInsight).where(CaptureInsight.run_id == run.id)) if stage == "revisit" else run
    if target is None:
        raise AppError("解释任务不存在。", "cancelled", 409)
    policy = read_policy(session)
    if not may_continue(policy, target, current):
        raise ScheduleDeferred(next_open(policy, current), policy)
    if policy.enabled and policy.end_policy == "finish_batch" and target.llm_admitted_at is None:
        target.llm_admitted_at = current
    target.scheduled_for = None


def refresh_waiting(session, policy, current):
    """当前策略即时重算所有等待时间；仅放行本批，不因停机追补旧批次。"""
    run_query = select(ProcessingRun).join(Record).where(Record.archived.is_(False),
        ProcessingRun.version == Record.version, ProcessingRun.status.in_(("queued", "running", WAITING)))
    insight_query = select(CaptureInsight).join(Record).where(Record.archived.is_(False),
        CaptureInsight.version == Record.version, CaptureInsight.status.in_(("pending", WAITING)))
    # 窗口内所有待办同时入批，而非仅让已开始请求的任务在窗口结束后继续。
    for rows, ready in ((session.scalars(run_query), "queued"), (session.scalars(insight_query), "pending")):
        for target in rows:
            created = target.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            # 扫描跨过边界时，不把扫描开始后才提交的任务算入旧批次。
            if created <= current and is_open(policy, current) and policy.enabled and policy.end_policy == "finish_batch" and target.llm_admitted_at is None:
                target.llm_admitted_at = current
            if target.status == WAITING:
                if may_continue(policy, target, current):
                    target.status, target.scheduled_for, target.error = ready, None, None
                    if isinstance(target, ProcessingRun):
                        record = session.get(Record, target.record_id)
                        record.status = ready
                else:
                    target.scheduled_for = next_open(policy, current)
                    target.error = ScheduleDeferred(target.scheduled_for, policy).message


def tick(current=None):
    current = current or now()
    with db.transaction() as session:
        config_lock(session)
        refresh_waiting(session, read_policy(session), current)


def view(session, current=None):
    current = current or now()
    policy = read_policy(session)
    return {"policy": policy.model_dump(), "open_now": is_open(policy, current),
            "next_window_at": next_open(policy, current)}


def update(session, policy, current=None):
    current = current or now()
    config_lock(session)
    preferences(session).llm_schedule = policy.model_dump()
    refresh_waiting(session, policy, current)
    return view(session, current)
