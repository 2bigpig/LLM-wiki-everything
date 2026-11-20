"""每个空间只有一种模型；任务锁与事务锁保证恢复、取消和切换不丢失旧索引。"""

from sqlalchemy import func, select

from wiki_app import db
from wiki_app.errors import AppError
from wiki_app.models import (
    EmbeddingSpace,
    ManagedModel,
    ModelRevision,
    Record,
    Source,
    SourceVector,
)

from . import calls, service


def eligible():
    return select(Source).join(Record).where(Record.archived.is_(False), Source.version == Record.version)


def missing(session, space_id, limit=None):
    query = eligible().where(~select(SourceVector.id).where(SourceVector.space_id == space_id,
        SourceVector.source_id == Source.id).exists()).order_by(Source.id)
    if limit is not None:
        query = query.limit(limit)
    return list(session.scalars(query))


def active_config(session):
    preferences = service.preferences(session)
    space = session.get(EmbeddingSpace, preferences.active_space_id) if preferences.active_space_id else None
    return (space.id, service.revision_config(session, space.revision_id)) if space else (None, None)


def list_spaces(session):
    total = session.scalar(select(func.count()).select_from(eligible().subquery()))
    result = []
    for row in session.scalars(select(EmbeddingSpace).order_by(EmbeddingSpace.created_at)):
        revision = session.get(ModelRevision, row.revision_id)
        model = session.get(ManagedModel, revision.model_id)
        indexed = session.scalar(select(func.count()).select_from(SourceVector).join(Source).join(Record)
            .where(SourceVector.space_id == row.id, Record.archived.is_(False), Source.version == Record.version))
        result.append({"id": row.id, "model_name": model.name, "model": revision.config["model"],
            "revision_id": row.revision_id, "status": row.status, "indexed": indexed, "total": total,
            "error": row.error, "activate_when_ready": row.activate_when_ready})
    return result


def store(space_id, sources, values):
    with db.transaction() as session:
        service.config_lock(session)
        for source, vector in zip(sources, values, strict=True):
            present = session.scalar(select(SourceVector.id).where(SourceVector.space_id == space_id,
                                       SourceVector.source_id == source.id))
            if not present and session.get(Source, source.id):
                session.add(SourceVector(space_id=space_id, source_id=source.id, embedding=vector))


def action(identifier, action):
    with db.transaction() as session:
        service.config_lock(session)
        row = session.get(EmbeddingSpace, identifier)
        if not row:
            raise AppError("向量空间不存在", "not_found", 404)
        if action == "cancel":
            row.status, row.activate_when_ready = "cancelled", False
        elif action in ("rebuild", "activate"):
            service.revision_config(session, row.revision_id)
            row.error = None
            row.activate_when_ready = action == "activate"
            if action == "activate" and not missing(session, row.id, 1):
                service.preferences(session).active_space_id = row.id
                row.status, row.activate_when_ready = "ready", False
            else:
                row.status = "building"
        else:
            raise AppError("未知向量空间操作")
        return {"status": row.status}


def rebuild(space_id):
    """队列按 space_id 加锁；每批提交后重新检查取消，断开后只补未完成来源。"""
    from wiki_app.config import get_settings

    try:
        while True:
            with db.transaction() as session:
                service.config_lock(session)
                row = session.get(EmbeddingSpace, space_id)
                if not row or row.status != "building":
                    return
                config = service.revision_config(session, row.revision_id)
                batch = missing(session, row.id, get_settings().index_batch_size)
                if not batch:
                    row.status = "ready"
                    if row.activate_when_ready:
                        service.preferences(session).active_space_id = row.id
                        row.activate_when_ready = False
                    return
            values = calls.embed(config, [s.text for s in batch])
            store(space_id, batch, values)
    except Exception as exc:
        with db.transaction() as session:
            row = session.get(EmbeddingSpace, space_id)
            if row and row.status == "building":
                row.status = "failed"
                row.error = exc.message if isinstance(exc, AppError) else "重建失败，请检查模型连接后重试。"
