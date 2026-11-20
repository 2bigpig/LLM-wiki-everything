"""管理接口使用实例口令；访问校验在 main 装配，密钥永不参与序列化。"""

import time

from fastapi import APIRouter
from sqlalchemy import select

from wiki_app import db
from wiki_app.config import get_settings
from wiki_app.errors import AppError
from wiki_app.models import EmbeddingSpace, ManagedModel, ModelConnection, ModelProbe

from . import calls, runtime, service, vectors
from .schemas import BindingsInput, ConnectionInput, ModelInput, ProbeDraft, ProbeInput

router = APIRouter(prefix="/model-settings", tags=["模型管理"])


@router.get("")
def settings_view():
    with db.transaction() as session:
        result = service.overview(session)
        result["spaces"] = vectors.list_spaces(session)
    settings = get_settings()
    return {**result, "presets": settings.model_management.provider_presets,
            "credential_ready": bool(settings.model_credential_key.get_secret_value()),
            "defaults": {"timeout_seconds": settings.llm_timeout_seconds,
                         "max_output_tokens": settings.llm_max_output_tokens}}


@router.post("/connections", status_code=201)
def create_connection(data: ConnectionInput):
    with db.transaction() as session:
        service.preferences(session)
        return service.connection_out(service.save_connection(session, data))


@router.patch("/connections/{identifier}")
def update_connection(identifier: str, data: ConnectionInput):
    with db.transaction() as session:
        return service.connection_out(service.save_connection(session, data, identifier))


@router.post("/connections/{identifier}/discover")
def discover(identifier: str):
    with db.SessionLocal() as session:
        row = session.get(ModelConnection, identifier)
        if not row or not row.enabled:
            raise AppError("连接不存在或已停用")
        config, key = service.connection_out(row), service.decrypt(row.key_cipher)
    try:
        return {"models": runtime.discover(config, key)}
    except Exception as exc:
        raise runtime.model_error(exc) from None


@router.post("/models", status_code=201)
def create_model(data: ModelInput):
    with db.transaction() as session:
        row = service.save_model(session, data)
        return {"id": row.id, "revision_id": row.revision_id}


@router.patch("/models/{identifier}")
def update_model(identifier: str, data: ModelInput):
    with db.transaction() as session:
        row = service.save_model(session, data, identifier)
        return {"id": row.id, "revision_id": row.revision_id}


@router.put("/bindings")
def update_bindings(data: BindingsInput):
    with db.transaction() as session:
        service.config_lock(session)
        for identifier in data.bindings.values():
            if identifier:
                model = session.get(ManagedModel, identifier)
                if not model or model.kind != "llm":
                    raise AppError("只能将 LLM 分配给整理阶段")
                service.revision_config(session, model.revision_id)
        service.preferences(session).bindings = data.bindings
    return {"status": "ok"}


@router.post("/models/{identifier}/test")
def probe(identifier: str, data: ProbeInput):
    with db.SessionLocal() as session:
        model = session.get(ManagedModel, identifier)
        if not model:
            raise AppError("模型不存在", "not_found", 404)
        config = service.revision_config(session, model.revision_id)
    config["timeout_seconds"] = min(config["timeout_seconds"], get_settings().model_management.probe_timeout_seconds)
    start = time.monotonic()
    try:
        if data.kind == "embedding":
            if config["kind"] != "embedding":
                raise AppError("请选择 Embedding 模型")
            calls.embed(config, ["连接测试"])
        else:
            if config["kind"] != "llm":
                raise AppError("请选择 LLM 模型")
            schema = ProbeDraft if data.kind == "json" else None
            def validate(result):
                if schema:
                    draft = ProbeDraft.model_validate_json(result["text"])
                    if not draft.ok:
                        raise ValueError("模型没有遵循测试指令")
                elif not result["text"].strip():
                    raise ValueError("空回复")
                return {"ok": True}
            calls.execute(config, "test", config["max_output_tokens"] + 500,
                lambda key: runtime.chat(config, key, [{"role": "user", "content":
                    '请只输出 JSON 对象 {"ok":true}。' if schema else "请回复连接成功。"}], schema),
                validate=validate)
        succeeded, detail = True, "1024 维向量验证通过" if data.kind == "embedding" else "模型能力测试通过"
    except AppError as exc:
        succeeded, detail = False, exc.message
    elapsed = time.monotonic() - start
    with db.transaction() as session:
        session.add(ModelProbe(revision_id=config["revision_id"], kind=data.kind,
            succeeded=succeeded, detail=detail, duration_seconds=elapsed))
    return {"succeeded": succeeded, "detail": detail, "duration_seconds": elapsed}


@router.post("/spaces", status_code=201)
def create_space(data: dict):
    with db.transaction() as session:
        model = session.get(ManagedModel, data.get("model_id"))
        if not model or model.kind != "embedding":
            raise AppError("请选择 Embedding 模型")
        service.revision_config(session, model.revision_id)
        row = session.scalar(select(EmbeddingSpace).where(EmbeddingSpace.revision_id == model.revision_id))
        if not row:
            row = EmbeddingSpace(revision_id=model.revision_id)
            session.add(row)
            session.flush()
        return {"id": row.id}


@router.post("/spaces/{identifier}/{action}")
def space_action(identifier: str, action: str):
    return vectors.action(identifier, action)
