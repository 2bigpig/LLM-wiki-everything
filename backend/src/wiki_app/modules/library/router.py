"""收藏接口：保存与任务待办同事务，上传失败清理已写入的临时文件。"""

from typing import Annotated

from fastapi import APIRouter, File, Form, Header, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from wiki_app import db
from wiki_app.config import get_settings
from wiki_app.errors import AppError
from wiki_app.integrations.storage import storage_path
from wiki_app.models import (
    Analysis,
    Attachment,
    CaptureInsight,
    ProcessingRun,
    Record,
    Relation,
    Source,
    WikiPage,
)
from wiki_app.modules.capture.service import current_inputs, history_matches, input_out
from wiki_app.modules.graph.service import valid_evidence
from wiki_app.modules.library.service import (
    cancel_runs,
    create_record,
    edit_record,
    get_record,
    queue_run,
    retry_record,
)
from wiki_app.modules.review.insights import context_suggestions
from wiki_app.modules.search.service import latest_analyses
from wiki_app.presentation import (
    analysis_out,
    attachments_out,
    columns,
    record_out,
    run_out,
    source_out,
)
from wiki_app.schemas import Purpose, RecordCreate, RecordEdit, RelationFeedback

router = APIRouter(tags=["收藏"])
ClientKey = Annotated[str | None, Header(alias="Idempotency-Key", max_length=80)]


@router.get("/records")
def list_records(
    q: str = Query(default="", max_length=200),
    kind: str = "",
    status: str = "",
    archived: bool = False,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    with db.SessionLocal() as session:
        filters = [Record.archived.is_(archived)]
        if q.strip():
            filters.append(
                or_(
                    Record.title.contains(q, autoescape=True),
                    Record.content.contains(q, autoescape=True),
                    Record.notes.contains(q, autoescape=True),
                    Record.id.in_(
                        select(Source.record_id).where(
                            Source.version == Record.version,
                            Source.text.contains(q, autoescape=True),
                        )
                    ),
                )
            )
        if kind:
            filters.append(Record.kind == kind)
        if status:
            filters.append(Record.status == status)
        total = session.scalar(select(func.count()).select_from(Record).where(*filters))
        records = list(
            session.scalars(
                select(Record)
                .where(*filters)
                .order_by(Record.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        analyses = latest_analyses(session, [r.id for r in records])
        return {"items": [record_out(r, analyses.get(r.id)) for r in records], "total": total}


@router.post("/records", status_code=201)
def save_record(data: RecordCreate, idempotency_key: ClientKey = None):
    try:
        with db.transaction() as session:
            return record_out(create_record(session, data, idempotency_key))
    except IntegrityError as exc:
        if idempotency_key:
            with db.SessionLocal() as session:
                existing = session.scalar(select(Record).where(Record.client_key == idempotency_key))
                if existing:
                    return record_out(existing)
        raise AppError("相同链接或提交已保存，请刷新收藏列表。", "duplicate", 409) from exc


@router.post("/records/upload", status_code=201)
async def upload_record(
    files: Annotated[list[UploadFile], File()],
    title: Annotated[str, Form(max_length=300)] = "",
    content: Annotated[str, Form(max_length=200000)] = "",
    notes: Annotated[str, Form(max_length=20000)] = "",
    purpose: Annotated[Purpose, Form()] = "auto",
    idempotency_key: ClientKey = None,
):
    from wiki_app.modules.capture.router import receive_capture

    return await receive_capture(files, text=content, title=title, notes=notes, purpose=purpose,
                                 idempotency_key=idempotency_key)


@router.get("/records/{record_id}")
def record_detail(record_id: str):
    with db.SessionLocal() as session:
        record = get_record(session, record_id)
        analyses = list(
            session.scalars(
                select(Analysis)
                .where(Analysis.record_id == record_id)
                .order_by(Analysis.created_at.desc())
                .limit(get_settings().record_history_limit)
            )
        )
        runs = list(
            session.scalars(
                select(ProcessingRun)
                .where(ProcessingRun.record_id == record_id)
                .order_by(ProcessingRun.created_at.desc())
                .limit(get_settings().record_history_limit)
            )
        )
        sources = list(
            session.scalars(
                select(Source)
                .where(Source.record_id == record_id)
                .order_by(Source.version.desc(), Source.origin, Source.ordinal)
            )
        )
        relations = []
        for relation in session.scalars(
            select(Relation).where(
                or_(Relation.record_id == record_id, Relation.target_id == record_id)
            )
        ):
            other_id = relation.target_id if relation.record_id == record_id else relation.record_id
            other = session.get(Record, other_id)
            evidence_sources = [
                session.get(Source, item["source_id"]) for item in relation.evidence
            ]
            stale = any(
                source is None or source.version != session.get(Record, source.record_id).version
                for source in evidence_sources
            )
            relations.append(
                {
                    **columns(relation, "id kind explanation evidence feedback"),
                    "target_id": other_id,
                    "target_title": other.title,
                    "archived": other.archived,
                    "stale": stale,
                }
            )
        source_ids = {s.id for s in sources}
        pages = [
            columns(page, "id title version updated_at")
            for page in session.scalars(select(WikiPage))
            if source_ids.intersection(page.source_ids)
        ]
        return {
            **record_out(record, analyses[0] if analyses else None),
            "analyses": [analysis_out(a) for a in analyses],
            "runs": [run_out(r) for r in runs],
            "sources": source_out(session, sources),
            "attachments": attachments_out(session, record_id),
            "relations": relations,
            "pages": pages,
            "inputs": [input_out(item) for item in current_inputs(session, record)],
            "history_matches": history_matches(session, record),
            "context_suggestions": context_suggestions(session, record),
            "insights": [{**columns(item, "id version status explanation evidence feedback error created_at"),
                          "stale": record.archived or item.version != record.version or (
                              item.status == "ready" and not valid_evidence(session, item.evidence))}
                         for item in session.scalars(select(CaptureInsight).where(
                             CaptureInsight.record_id == record.id).order_by(CaptureInsight.created_at.desc())
                             .limit(get_settings().record_history_limit))],
        }


@router.put("/records/{record_id}")
def update_record(record_id: str, data: RecordEdit):
    with db.transaction() as session:
        return record_out(edit_record(session, record_id, data))


@router.post("/records/{record_id}/retry")
def retry(record_id: str):
    with db.transaction() as session:
        return run_out(retry_record(session, record_id))


@router.post("/records/{record_id}/reanalyze")
def reanalyze(record_id: str):
    with db.transaction() as session:
        return run_out(retry_record(session, record_id, fresh=True))


@router.post("/records/{record_id}/reread")
def reread(record_id: str):
    with db.transaction() as session:
        record = get_record(session, record_id, lock=True)
        if record.archived:
            raise AppError("请先恢复归档记录。")
        cancel_runs(session, record)
        old_version = record.version
        record.version += 1
        record.coverage = {}
        from wiki_app.modules.capture.service import clone_inputs

        clone_inputs(session, record, old_version)
        return run_out(queue_run(session, record))


@router.post("/records/{record_id}/archive")
def archive(record_id: str):
    with db.transaction() as session:
        record = get_record(session, record_id, lock=True)
        record.archived = not record.archived
        if record.archived:
            cancel_runs(session, record)
            record.status = "cancelled"
        return record_out(record)


@router.get("/files/{attachment_id}")
def download_file(attachment_id: str):
    with db.SessionLocal() as session:
        attachment = session.get(Attachment, attachment_id)
        if not attachment:
            raise AppError("附件不存在。", "not_found", 404)
        path = storage_path(attachment.storage_key)
        if not path.is_file():
            raise AppError("附件文件缺失，请检查持久化目录和备份。", "not_found", 404)
        return FileResponse(
            path,
            filename=attachment.filename,
            media_type=attachment.media_type,
            content_disposition_type="inline",
            headers={"X-Content-Type-Options": "nosniff"},
        )


@router.get("/sources/{source_id}")
def source_detail(source_id: str):
    with db.SessionLocal() as session:
        source = session.get(Source, source_id)
        if not source:
            raise AppError("引用片段不存在。", "not_found", 404)
        return source_out(session, [source])[0]


@router.patch("/relations/{relation_id}")
def relation_feedback(relation_id: str, data: RelationFeedback):
    with db.transaction() as session:
        relation = session.get(Relation, relation_id)
        if not relation:
            raise AppError("联系不存在。", "not_found", 404)
        relation.feedback = data.feedback
        return {"id": relation.id, "feedback": relation.feedback}
