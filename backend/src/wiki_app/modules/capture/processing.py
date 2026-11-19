"""逐来源读取与提交；每个成功片段和来源状态在同一事务写入，支持中断恢复。"""

from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from wiki_app import db
from wiki_app.errors import AppError, ConfigurationRequired
from wiki_app.models import Attachment, CaptureInput, Record, Source, new_id, now
from wiki_app.modules.capture.rules import link_identity
from wiki_app.modules.capture.service import current_inputs, ensure_inputs, resource_for


def checked_record(session, record_id, version):
    record = session.scalar(select(Record).where(Record.id == record_id).with_for_update(key_share=True))
    if not record or record.archived or record.version != version or record.status == "cancelled":
        raise AppError("记录版本已更新或归档，停止旧来源处理。", "cancelled", 409)
    return record


def process_inputs(record: Record, attachments: list[Attachment]) -> tuple[list[dict], dict]:
    """兼容 extract_record 返回协议；持久化每个输入后再继续下一个输入。"""
    from wiki_app.integrations.extraction import extract_attachment, split_text
    from wiki_app.integrations.platform_video import is_platform_video, read_platform_video
    from wiki_app.integrations.web import fetch_html, read_webpage
    from wiki_app.modules.search.service import searchable

    with db.transaction() as session:
        current = checked_record(session, record.id, record.version)
        inputs = ensure_inputs(session, current)
    for item in inputs:
        if not item.included or item.status in ("succeeded", "failed", "skipped"):
            continue
        raw, details, downloaded, final_url = [], [], None, item.url
        try:
            if item.kind == "text":
                raw = [{"text": part, "method": "user_note" if item.origin == "notes" else "user_text",
                        "locator": {"kind": "personal_note" if item.origin == "notes" else "original_text"}}
                       for part in split_text(item.text)]
            elif item.kind == "url":
                # 短链接的每次跳转仍走公网校验，不能交给未受控的重定向跟随。
                from urllib.parse import urlsplit

                if urlsplit(final_url).hostname in ("b23.tv",):
                    _, final_url = fetch_html(final_url)
                identity, final_url = link_identity(final_url)
                with db.transaction() as session:
                    checked_record(session, record.id, record.version)
                    resource = resource_for(session, "url", identity, final_url, final_url)
                    stored = session.get(CaptureInput, item.id)
                    stored.resource_id, stored.url = resource.id, final_url
                    duplicate = session.scalar(select(CaptureInput.id).where(
                        CaptureInput.record_id == record.id, CaptureInput.version == record.version,
                        CaptureInput.resource_id == resource.id, CaptureInput.status == "succeeded",
                        CaptureInput.id != item.id))
                    if duplicate:
                        stored.status = "skipped"
                        stored.details = ["与本次已经读取的来源相同，未重复读取。"]
                if duplicate:
                    continue
                if is_platform_video(final_url):
                    segments, downloaded = read_platform_video(final_url, record.id, record.version)
                    raw = [{"text": value["text"], "method": "platform_subtitles",
                            "locator": {"url": final_url, "start": value["start"], "end": value["end"]}}
                           for value in segments]
                    if downloaded:
                        downloaded.id = new_id()
                        downloaded.derived = True
                        raw, details = extract_attachment(downloaded)
                    else:
                        details.append("复用平台字幕（可能为自动字幕），未分析视频画面；位置对应原片。")
                else:
                    web = read_webpage(final_url)
                    final_url = web["url"]
                    raw = [{"text": part, "method": "web", "locator": {"url": final_url}}
                           for part in split_text(web["text"])]
                    details.append(web["coverage"])
            else:
                with db.SessionLocal() as session:
                    attachment = session.get(Attachment, item.attachment_id)
                if not attachment:
                    raise AppError("附件记录不存在。", "read_failed")
                raw, details = extract_attachment(attachment)
            raw = [piece for piece in raw if piece["text"].strip()]
            if not raw:
                raise AppError("未取得可读的文字或语音，原始内容已保留。", "read_failed")
            with db.transaction() as session:
                checked_record(session, record.id, record.version)
                stored = session.get(CaptureInput, item.id)
                if item.kind == "url":
                    identity, final_url = link_identity(final_url)
                    stored.resource_id = resource_for(session, "url", identity, final_url, final_url).id
                    stored.url = final_url
                if downloaded:
                    session.add(downloaded)
                    session.flush()
                for ordinal, piece in enumerate(raw):
                    sid = str(uuid5(NAMESPACE_URL, f"{record.id}:{record.version}:{item.origin}:{ordinal}"))
                    if not session.get(Source, sid):
                        session.add(Source(id=sid, record_id=record.id, version=record.version,
                                           input_id=item.id, origin=item.origin, ordinal=ordinal,
                                           text=piece["text"], method=piece["method"], locator=piece["locator"],
                                           search_text=searchable(record.title + " " + piece["text"])))
                stored.status, stored.error, stored.error_code = "succeeded", None, None
                stored.details, stored.fetched_at = details, now()
        except Exception as exc:
            if isinstance(exc, AppError) and exc.code == "cancelled":
                raise
            with db.transaction() as session:
                checked_record(session, record.id, record.version)
                stored = session.get(CaptureInput, item.id)
                stored.status = "failed"
                stored.error = exc.message if isinstance(exc, AppError) else "读取失败，请检查链接或文件后重试。"
                stored.error_code = exc.code if isinstance(exc, AppError) else "read_failed"
                stored.fetched_at = now()
    with db.SessionLocal() as session:
        current = session.get(Record, record.id)
        if current.version != record.version or current.archived or current.status == "cancelled":
            raise AppError("记录已更新或停止处理。", "cancelled", 409)
        inputs = current_inputs(session, current)
        sources = list(session.scalars(select(Source).where(Source.record_id == record.id,
                                                           Source.version == record.version)))
    failures = [f"{item.label}：{item.error}" for item in inputs if item.status == "failed"]
    details = [detail for item in inputs for detail in item.details] + failures
    if not sources and any(item.status == "failed" for item in inputs):
        missing = any(item.error_code == "configuration_required" for item in inputs)
        raise (ConfigurationRequired if missing else AppError)("；".join(failures))
    pieces = [{key: getattr(source, key) for key in ("input_id", "text", "method", "locator", "origin", "ordinal")}
              for source in sources]
    return pieces, {"status": "saved_only" if not sources else "partial" if failures or details else "complete",
                    "details": list(dict.fromkeys(details)), "source_count": len(sources),
                    "wiki_note": "已保留原始输入，未启用可读取来源。" if not sources else ""}
