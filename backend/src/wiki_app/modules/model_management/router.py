"""开发阶段直接访问模型管理；密钥保持加密且永不参与响应序列化。"""

import time

from fastapi import APIRouter
from sqlalchemy import select

from wiki_app import db
from wiki_app.config import LLMScheduleSettings, get_settings
from wiki_app.errors import AppError
from wiki_app.models import ManagedModel, ModelConnection, ModelProbe

from . import calls, runtime, scheduling, service, vectors
from .schemas import BindingsInput, ConnectionInput, ModelInput, ProbeDraft, ProbeInput

router = APIRouter(prefix="/model-settings", tags=["模型管理"])


@router.get("/schedule")
def schedule_view():
    with db.transaction() as session:
        return scheduling.view(session)


@router.put("/schedule")
def schedule_update(data: LLMScheduleSettings):
    with db.transaction() as session:
        return scheduling.update(session, data)


def fixed_service_error():
    return AppError("Embedding 与 OCR 使用项目固定技术栈，用户只能配置 LLM。", "fixed_service_model", 403)


def editable_connection(session, identifier):
    """同时保护 BGE 专用连接和旧版本中被向量模型共用的连接。"""
    row = session.get(ModelConnection, identifier)
    if not row:
        raise AppError("连接不存在", "not_found", 404)
    embedding = session.scalar(select(ManagedModel.id).where(
        ManagedModel.connection_id == identifier, ManagedModel.kind == "embedding").limit(1))
    if row.protocol == "bge" or embedding:
        raise fixed_service_error()
    return row


@router.get("")
def settings_view():
    with db.transaction() as session:
        result = service.overview(session)
        # 内置模型仍供 Worker 使用；页面只管理 LLM，固定连接显式标记为只读。
        fixed_ids = {m["connection_id"] for m in result["models"] if m["kind"] == "embedding"}
        for connection in result["connections"]:
            connection["read_only"] = connection["protocol"] == "bge" or connection["id"] in fixed_ids
        result["models"] = [m for m in result["models"] if m["kind"] == "llm"]
        result["spaces"] = vectors.list_spaces(session)
    settings = get_settings()
    return {**result, "presets": settings.model_management.provider_presets,
            "credential_ready": bool(settings.model_credential_key.get_secret_value()),
            "defaults": {"timeout_seconds": settings.llm_timeout_seconds,
                         "max_output_tokens": settings.llm_max_output_tokens}}


@router.post("/connections", status_code=201)
def create_connection(data: ConnectionInput):
    if data.protocol == "bge" or data.provider == "bge":
        raise fixed_service_error()
    with db.transaction() as session:
        service.preferences(session)
        return service.connection_out(service.save_connection(session, data))


@router.patch("/connections/{identifier}")
def update_connection(identifier: str, data: ConnectionInput):
    if data.protocol == "bge" or data.provider == "bge":
        raise fixed_service_error()
    with db.transaction() as session:
        editable_connection(session, identifier)
        return service.connection_out(service.save_connection(session, data, identifier))


@router.post("/connections/{identifier}/discover")
def discover(identifier: str):
    with db.SessionLocal() as session:
        row = editable_connection(session, identifier)
        if not row or not row.enabled:
            raise AppError("连接不存在或已停用")
        config, key = service.connection_out(row), service.decrypt(row.key_cipher)
    try:
        return {"models": runtime.discover(config, key)}
    except Exception as exc:
        raise runtime.model_error(exc) from None


@router.post("/models", status_code=201)
def create_model(data: ModelInput):
    if data.kind != "llm":
        raise fixed_service_error()
    with db.transaction() as session:
        editable_connection(session, data.connection_id)
        row = service.save_model(session, data)
        return {"id": row.id, "revision_id": row.revision_id}


@router.patch("/models/{identifier}")
def update_model(identifier: str, data: ModelInput):
    if data.kind != "llm":
        raise fixed_service_error()
    with db.transaction() as session:
        existing = session.get(ManagedModel, identifier)
        if existing and existing.kind != "llm":
            raise fixed_service_error()
        editable_connection(session, data.connection_id)
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
        if model.kind != "llm" or data.kind == "embedding":
            raise fixed_service_error()
        editable_connection(session, model.connection_id)
        config = service.revision_config(session, model.revision_id)
    config["timeout_seconds"] = min(config["timeout_seconds"], get_settings().model_management.probe_timeout_seconds)
    start = time.monotonic()
    try:
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
        succeeded, detail = True, "模型能力测试通过"
    except AppError as exc:
        succeeded, detail = False, exc.message
    elapsed = time.monotonic() - start
    with db.transaction() as session:
        session.add(ModelProbe(revision_id=config["revision_id"], kind=data.kind,
            succeeded=succeeded, detail=detail, duration_seconds=elapsed))
    return {"succeeded": succeeded, "detail": detail, "duration_seconds": elapsed}


@router.post("/spaces", status_code=201)
def create_space(data: dict):
    # 保留旧入口给出明确原因，不允许绕过页面创建可替换的向量空间。
    raise fixed_service_error()


@router.post("/spaces/{identifier}/{action}")
def space_action(identifier: str, action: str):
    raise fixed_service_error()
