"""记录修改使用版本检查；历史读取片段始终保留给旧引用使用。"""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from wiki_app.errors import AppError
from wiki_app.models import ProcessingRun, Record, now
from wiki_app.schemas import RecordCreate, RecordEdit

ACTIVE = ("queued", "running", "waiting_schedule")


def normalize_url(value: str) -> str:
    """清理跟踪参数并拒绝含凭证或非网页协议的输入。"""
    try:
        parts = urlsplit(value.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError
        if parts.username or parts.password or parts.port not in (None, 80, 443):
            raise ValueError
        host = parts.hostname.encode("idna").decode().lower()
        if ":" in host:
            host = f"[{host}]"
        port = (
            f":{parts.port}"
            if parts.port and parts.port != (443 if parts.scheme == "https" else 80)
            else ""
        )
        query = [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in ("spm", "fbclid")
        ]
        return urlunsplit((parts.scheme, host + port, parts.path or "/", urlencode(query), ""))
    except (ValueError, UnicodeError) as exc:
        raise AppError("请输入有效的 http/https 链接，不支持地址中的密码或自定义端口。") from exc


def queue_run(session: Session, record: Record) -> ProcessingRun:
    """执行记录兼作事务内的待办；队列派发失败不会丢失已提交内容。"""
    run = ProcessingRun(record_id=record.id, version=record.version)
    record.status = "queued"
    session.add(run)
    session.flush()
    from wiki_app.modules.model_management.service import snapshot

    snapshot(session, run)
    return run


def create_record(session: Session, data: RecordCreate, client_key: str | None = None) -> Record:
    # 延迟导入防止 URL 规范化规则与统一录入服务形成循环导入。
    from wiki_app.modules.capture.service import create_capture

    return create_capture(session, text=data.content, url=data.url, title=data.title,
                          notes=data.notes, purpose=data.purpose, client_key=client_key)


def get_record(session: Session, record_id: str, lock: bool = False) -> Record:
    query = select(Record).where(Record.id == record_id)
    # 主键不会修改，NO KEY UPDATE 允许其他记录校验关系外键，避免交叉等待。
    record = session.scalar(query.with_for_update(key_share=True) if lock else query)
    if not record:
        raise AppError("记录不存在。", "not_found", 404)
    return record


def edit_record(session: Session, record_id: str, data: RecordEdit) -> Record:
    record = get_record(session, record_id, lock=True)
    if record.version != data.version:
        raise AppError("记录已更新，请刷新后再修改。", "version_conflict", 409)
    if record.kind == "text" and not data.content.strip():
        raise AppError("短文内容不能为空。")
    if record.archived:
        raise AppError("请先恢复归档记录，再修改内容。", "archived", 409)
    from wiki_app.modules.capture.rules import digest, recognize
    from wiki_app.modules.capture.service import current_inputs, ensure_inputs

    previous = recognize(record.content)
    legacy_url = record.url if record.url and all(link["url"] != record.url for link in previous["links"]) else None
    excluded = [item.url for item in current_inputs(session, record) if not item.included and item.url]
    cancel_runs(session, record)
    record.title, record.content = data.title.strip(), data.content
    record.notes, record.purpose = data.notes, data.purpose
    record.version += 1
    record.coverage = {}
    record.updated_at = now()
    links = recognize(record.content)["links"]
    record.url = links[0]["url"] if links else legacy_url
    record.url_key = digest(record.url) if record.url else None
    session.flush()
    ensure_inputs(session, record, excluded)
    queue_run(session, record)
    return record


def cancel_runs(session: Session, record: Record):
    for run in session.scalars(
        select(ProcessingRun).where(
            ProcessingRun.record_id == record.id,
            ProcessingRun.status.in_(
                ("queued", "running", "waiting_schedule", "waiting_configuration", "budget_exceeded")
            ),
        )
    ):
        run.status, run.finished_at, run.scheduled_for = "cancelled", now(), None


def retry_record(session: Session, record_id: str, fresh: bool = False) -> ProcessingRun:
    record = get_record(session, record_id, lock=True)
    if record.archived:
        raise AppError("归档记录不会自动处理，请先恢复。", "archived", 409)
    last = session.scalar(
        select(ProcessingRun)
        .where(
            ProcessingRun.record_id == record.id,
            ProcessingRun.version == record.version,
        )
        .order_by(ProcessingRun.created_at.desc())
    )
    if last and last.status in ACTIVE:
        raise AppError("这条记录正在排队或处理中。", "already_running", 409)
    if last and not fresh and last.status != "completed":
        from wiki_app.models import CaptureInput

        for item in session.scalars(select(CaptureInput).where(
            CaptureInput.record_id == record.id, CaptureInput.version == record.version,
            CaptureInput.status == "failed", CaptureInput.included.is_(True)
        )):
            item.status, item.error, item.error_code = "queued", None, None
        last.status, last.error, last.finished_at = "queued", None, None
        record.status = "queued"
        return last
    return queue_run(session, record)
