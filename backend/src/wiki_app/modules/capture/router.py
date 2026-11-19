"""统一 multipart 录入与纯解析预览；旧上传入口复用同一实现。"""

import hashlib
import json
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, File, Form, Header, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from wiki_app import db
from wiki_app.config import get_settings
from wiki_app.errors import AppError
from wiki_app.integrations.storage import file_type, storage_path
from wiki_app.models import Attachment, Record
from wiki_app.modules.capture.rules import recognize
from wiki_app.modules.capture.service import create_capture, history_matches, retry_failed
from wiki_app.presentation import record_out, run_out
from wiki_app.schemas import CapturePreview, Purpose

router = APIRouter(tags=["统一录入"])
ClientKey = Annotated[str | None, Header(alias="Idempotency-Key", max_length=80)]


def capture_out(session, record):
    return {**record_out(record), "history_matches": history_matches(session, record)}


@router.post("/captures/recognize")
def preview(data: CapturePreview):
    settings = get_settings()
    return {**recognize(data.text), "max_links": settings.capture_max_links,
            "max_attachments": settings.capture_max_attachments, "max_upload_mb": settings.max_upload_mb}


async def receive_capture(files, *, text="", title="", notes="", purpose="auto",
                          excluded_urls="[]", idempotency_key=None):
    """事务失败只清理本次写入文件；并发重试返回原事件，不创建额外收藏。"""
    files = files or []
    saved, written, total, hashes = [], [], 0, set()
    settings = get_settings()
    try:
        if len(files) > settings.capture_max_attachments:
            raise AppError(f"一次最多上传 {settings.capture_max_attachments} 个文件。", "capture_limit")
        try:
            excluded = json.loads(excluded_urls)
            if not isinstance(excluded, list) or len(excluded) > 100 or any(
                not isinstance(value, str) or len(value) > 4096 for value in excluded
            ):
                raise ValueError()
        except (ValueError, TypeError):
            raise AppError("取消读取的链接列表无效。") from None
        if idempotency_key:
            with db.SessionLocal() as session:
                existing = session.scalar(select(Record).where(Record.client_key == idempotency_key))
                if existing:
                    return capture_out(session, existing)
        for upload in files:
            filename = Path((upload.filename or "file").replace("\\", "/")).name[-240:]
            suffix, media_type = file_type(filename)
            key = f"uploads/{uuid4()}{suffix}"
            path = storage_path(key)
            path.parent.mkdir(parents=True, exist_ok=True)
            written.append(path)
            checksum, size = hashlib.sha256(), 0
            with path.open("wb") as output:
                while block := await upload.read(1024 * 1024):
                    size += len(block)
                    total += len(block)
                    if total > settings.max_upload_mb * 1024 * 1024:
                        raise AppError("本次上传超过配置的大小上限。", "too_large", 413)
                    checksum.update(block)
                    output.write(block)
            if not size:
                raise AppError(f"{filename} 是空文件。")
            if checksum.hexdigest() in hashes:
                path.unlink()
                continue
            hashes.add(checksum.hexdigest())
            saved.append(Attachment(id=str(uuid4()), filename=filename, storage_key=key,
                                    media_type=media_type, size=size, sha256=checksum.hexdigest()))
        try:
            with db.transaction() as session:
                record = create_capture(session, text=text, title=title, notes=notes, purpose=purpose,
                                        files=saved, excluded=excluded, client_key=idempotency_key)
                result = capture_out(session, record)
        except IntegrityError:
            # 同一幂等键的竞争事务已经提交时，只移除本次临时文件并返回原结果。
            if not idempotency_key:
                raise
            with db.SessionLocal() as session:
                existing = session.scalar(select(Record).where(Record.client_key == idempotency_key))
                if not existing:
                    raise
                result = capture_out(session, existing)
            for path in written:
                path.unlink(missing_ok=True)
        return result
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    finally:
        for upload in files:
            await upload.close()


@router.post("/captures", status_code=201)
async def capture(
    files: Annotated[list[UploadFile] | None, File()] = None,
    text: Annotated[str, Form(max_length=200000)] = "",
    title: Annotated[str, Form(max_length=300)] = "",
    notes: Annotated[str, Form(max_length=20000)] = "",
    purpose: Annotated[Purpose, Form()] = "auto",
    excluded_urls: Annotated[str, Form(max_length=200000)] = "[]",
    idempotency_key: ClientKey = None,
):
    return await receive_capture(files, text=text, title=title, notes=notes, purpose=purpose,
                                 excluded_urls=excluded_urls, idempotency_key=idempotency_key)


@router.post("/records/{record_id}/inputs/{input_id}/retry")
def retry_input(record_id: str, input_id: str):
    with db.transaction() as session:
        return run_out(retry_failed(session, record_id, input_id))
