"""每次主动收藏独立保存；资源身份只用于关联，不覆盖历史原件。"""

from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from wiki_app.config import get_settings
from wiki_app.errors import AppError
from wiki_app.models import Attachment, CaptureInput, Record, Resource, Source, new_id, now
from wiki_app.modules.capture.rules import digest, link_identity, recognize


def insert_once(session, model, values: dict, keys: list[str]):
    """数据库唯一约束处理并发，不能先查再插导致两个收藏互相回滚。"""
    insert = pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
    return session.execute(insert(model).values(**values).on_conflict_do_nothing(index_elements=keys)).rowcount


def resource_for(session, kind: str, identity: str, label: str, url: str | None = None) -> Resource:
    key = digest(kind + ":" + identity)
    identifier = str(uuid5(NAMESPACE_URL, "wiki-resource:" + key))
    insert_once(session, Resource, {"id": identifier, "identity": key, "kind": kind,
                                  "label": label[:500], "url": url, "created_at": now()}, ["identity"])
    return session.scalar(select(Resource).where(Resource.identity == key))


def current_inputs(session, record: Record) -> list[CaptureInput]:
    return list(session.scalars(select(CaptureInput).where(
        CaptureInput.record_id == record.id, CaptureInput.version == record.version
    ).order_by(CaptureInput.position, CaptureInput.id)))


def ensure_inputs(session, record: Record, excluded: list[str] | None = None, *, legacy=False) -> list[CaptureInput]:
    """创建当前版本来源；已有版本不重建，以保留逐来源状态和重试幂等。"""
    existing = current_inputs(session, record)
    if existing:
        return existing
    parsed = {"body": record.content.strip(), "links": []} if legacy else recognize(record.content)
    links = parsed["links"]
    if record.url:
        identity, url = link_identity(record.url)
        if all(item["identity"] != identity for item in links):
            links.insert(0, {"identity": identity, "url": url, "kind": "web"})
    excluded = set(excluded or [])
    settings = get_settings()
    if sum(item["url"] not in excluded for item in links) > settings.capture_max_links:
        raise AppError(f"一次最多自动读取 {settings.capture_max_links} 个链接。", "capture_limit")
    result = []

    def add(resource, kind, origin, label, **values):
        item = CaptureInput(id=new_id(), record_id=record.id, version=record.version,
                            resource_id=resource.id, kind=kind, origin=origin,
                            label=label[:500], position=len(result), **values)
        session.add(item)
        result.append(item)

    if parsed["body"]:
        resource = resource_for(session, "text", parsed["body"], record.title)
        add(resource, "text", "body", "原始输入", text=parsed["body"])
    if record.notes.strip():
        resource = resource_for(session, "text", record.notes, "收藏原因")
        add(resource, "text", "notes", "收藏原因", text=record.notes)
    for index, link in enumerate(links):
        resource = resource_for(session, "url", link["identity"], link["url"], link["url"])
        included = link["url"] not in excluded
        add(resource, "url", f"link:{index}", link["url"], url=link["url"],
            included=included, status="queued" if included else "skipped")
    attachments = list(session.scalars(select(Attachment).where(Attachment.record_id == record.id)))
    seen = set()
    for attachment in attachments:
        if attachment.derived:
            continue
        if attachment.sha256 in seen:
            continue
        seen.add(attachment.sha256)
        resource = resource_for(session, "file", attachment.sha256, attachment.filename)
        add(resource, "file", attachment.id, attachment.filename, attachment_id=attachment.id)
    session.flush()
    return result


def create_capture(session, *, text="", url=None, title="", notes="", purpose="auto",
                   files=None, excluded=None, client_key=None) -> Record:
    """所有新旧提交入口共用保存逻辑，只有请求幂等键相同才复用收藏事件。"""
    from wiki_app.modules.library.service import normalize_url, queue_run

    if client_key:
        existing = session.scalar(select(Record).where(Record.client_key == client_key))
        if existing:
            return existing
    files = files or []
    if not text.strip() and not url and not files:
        raise AppError("请输入内容、链接或添加附件。")
    if len(files) > get_settings().capture_max_attachments:
        raise AppError(f"一次最多上传 {get_settings().capture_max_attachments} 个文件。", "capture_limit")
    parsed = recognize(text)
    primary = normalize_url(url) if url else (parsed["links"][0]["url"] if parsed["links"] else None)
    record = Record(id=new_id(), client_key=client_key, kind="text", title=title.strip() or (
        parsed["body"].splitlines()[0][:100] if parsed["body"] else files[0].filename if files else primary or "原始输入"
    )[:300], content=text, url=primary, url_key=digest(primary) if primary else None,
        notes=notes, purpose=purpose, version=1)
    session.add(record)
    session.flush()
    for attachment in files:
        attachment.record_id = record.id
        session.add(attachment)
    session.flush()
    inputs = ensure_inputs(session, record, excluded)
    parts = [item for item in inputs if item.origin != "notes"]
    record.kind = "mixed" if len(parts) > 1 else (
        "link" if primary else "file" if files else "text")
    record.coverage = {"details": parsed["warnings"]}
    queue_run(session, record)
    return record


def input_out(item: CaptureInput) -> dict:
    return {key: getattr(item, key) for key in (
        "id", "resource_id", "version", "kind", "origin", "label", "url", "attachment_id", "position",
        "included", "status", "error", "error_code", "details", "fetched_at")}


def history_matches(session, record: Record) -> list[dict]:
    """按资源查历史；当前一次收藏即使重读多次也只计一次。"""
    ids = {item.resource_id for item in current_inputs(session, record) if item.included and item.origin != "notes"}
    if not ids:
        return []
    rows = session.execute(select(CaptureInput.resource_id, Record).join(
        Record, Record.id == CaptureInput.record_id
    ).where(CaptureInput.resource_id.in_(ids), CaptureInput.origin != "notes", Record.id != record.id,
            Record.created_at <= record.created_at,
            CaptureInput.version == Record.version).order_by(Record.created_at.desc(), Record.id)
            .limit(get_settings().graph_max_nodes))
    matches, seen = [], set()
    for resource_id, previous in rows:
        if (resource_id, previous.id) in seen:
            continue
        seen.add((resource_id, previous.id))
        matches.append({"resource_id": resource_id, "record_id": previous.id,
                        "title": previous.title, "created_at": previous.created_at,
                        "archived": previous.archived})
    return matches


def clone_inputs(session, record: Record, old_version: int, failed_only=False, retry_input=None):
    """新版本复制成功片段，旧 Source ID 永远不修改；失败来源重新排队。"""
    old = list(session.scalars(select(CaptureInput).where(
        CaptureInput.record_id == record.id, CaptureInput.version == old_version
    )))
    if not old:
        return ensure_inputs(session, record)
    for item in old:
        values = {key: getattr(item, key) for key in ("resource_id", "origin", "kind", "label",
                  "url", "text", "attachment_id", "position", "included")}
        copy = CaptureInput(id=new_id(), record_id=record.id, version=record.version, **values)
        reuse = failed_only and item.status == "succeeded"
        copy.status = "succeeded" if reuse else "queued" if item.included else "skipped"
        if failed_only and item.status == "failed" and retry_input and item.id != retry_input:
            copy.status, copy.error, copy.error_code = "failed", item.error, item.error_code
        copy.details = list(item.details) if reuse else []
        copy.fetched_at = item.fetched_at if reuse else None
        session.add(copy)
        session.flush()
        if reuse:
            for source in session.scalars(select(Source).where(Source.input_id == item.id)):
                sid = str(uuid5(NAMESPACE_URL, f"{record.id}:{record.version}:{source.origin}:{source.ordinal}"))
                session.add(Source(id=sid, record_id=record.id, version=record.version,
                                   input_id=copy.id, origin=source.origin, ordinal=source.ordinal,
                                   text=source.text, locator=dict(source.locator), method=source.method,
                                   search_text=source.search_text, embedding=source.embedding,
                                   embedding_model=source.embedding_model))


def retry_failed(session, record_id: str, input_id: str):
    """重试只增加读取版本，不增加收藏事件或兴趣次数。"""
    from wiki_app.modules.library.service import ACTIVE, cancel_runs, get_record, queue_run

    record = get_record(session, record_id, lock=True)
    item = session.get(CaptureInput, input_id)
    if not item or item.record_id != record.id or item.version != record.version:
        raise AppError("来源版本已更新，请刷新后重试。", "version_conflict", 409)
    if record.archived or record.status in ACTIVE:
        raise AppError("请等待处理结束或恢复归档后再重试。", "already_running", 409)
    if item.status != "failed":
        raise AppError("只能重试读取失败的来源。")
    cancel_runs(session, record)
    old_version = record.version
    record.version += 1
    record.coverage = {}
    clone_inputs(session, record, old_version, failed_only=True, retry_input=input_id)
    return queue_run(session, record)
