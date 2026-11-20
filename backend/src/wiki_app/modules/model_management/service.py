"""数据库是运行配置唯一来源；修订无密钥，连接负责凭据轮换与立即停用。"""

import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, text

from wiki_app import db
from wiki_app.config import get_settings
from wiki_app.errors import AppError, ConfigurationRequired
from wiki_app.models import (
    EmbeddingSpace,
    ManagedModel,
    ModelConnection,
    ModelPreferences,
    ModelProbe,
    ModelRevision,
    ProcessingRun,
    Source,
    SourceVector,
    new_id,
)

from .schemas import STAGES, ConnectionInput, ModelInput

LOCK = 81020261120


def config_lock(session):
    """首次初始化、任务快照和切换共用事务锁，不增加进程或消息通道。"""
    if db.engine.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK})


def cipher():
    try:
        return Fernet(get_settings().model_credential_key.get_secret_value().encode())
    except (ValueError, TypeError):
        raise ConfigurationRequired("模型主密钥缺失或无效，请运行 scripts/prepare-model-settings.ps1。") from None


def encrypt(value):
    return cipher().encrypt(value.encode()).decode() if value else ""


def decrypt(value):
    if not value:
        return ""
    try:
        return cipher().decrypt(value.encode()).decode()
    except InvalidToken:
        raise ConfigurationRequired("模型凭据无法解密，请恢复原 MODEL_CREDENTIAL_KEY；不要重新生成覆盖。") from None


def preferences(session):
    row = session.get(ModelPreferences, "personal")
    if row:
        return row
    config_lock(session)
    row = session.get(ModelPreferences, "personal", populate_existing=True)
    if row:
        return row
    row = ModelPreferences(id="personal", bindings={})
    session.add(row)
    session.flush()
    settings = get_settings()
    connection = save_connection(session, ConnectionInput(
        name="本地 BGE-M3", provider="bge", protocol="bge", base_url=settings.embedding_url,
    ))
    model = save_model(session, ModelInput(connection_id=connection.id, name="BGE-M3",
        model=settings.embedding_model, kind="embedding", timeout_seconds=settings.embedding_timeout_seconds))
    space = EmbeddingSpace(revision_id=model.revision_id, status="ready")
    session.add(space)
    session.flush()
    row.active_space_id = space.id if settings.embeddings_enabled else None
    # 只导入原 BGE 的向量，其他模型绝不能借同维度被混入。
    for source in session.scalars(select(Source).where(Source.embedding.is_not(None),
                                                     Source.embedding_model == settings.embedding_model)):
        session.add(SourceVector(space_id=space.id, source_id=source.id, embedding=source.embedding))
    return row


def save_connection(session, data, identifier=None):
    row = session.get(ModelConnection, identifier) if identifier else None
    if identifier and not row:
        raise AppError("连接不存在", "not_found", 404)
    config_lock(session)
    changed = row and (row.base_url != data.base_url or row.protocol != data.protocol)
    if changed and data.api_key is None:
        raise AppError("更换目标地址或协议后必须重新填写密钥；免密服务请显式留空。")
    if not row:
        row = ModelConnection(id=new_id(), endpoint_version=1)
        session.add(row)
    if changed:
        row.endpoint_version += 1
    for name in ("name", "provider", "protocol", "base_url", "enabled"):
        setattr(row, name, getattr(data, name))
    if data.api_key is not None:
        row.key_cipher = encrypt(data.api_key.get_secret_value())
    elif not identifier:
        row.key_cipher = ""
    session.flush()
    return row


def save_model(session, data, identifier=None):
    connection = session.get(ModelConnection, data.connection_id)
    if not connection:
        raise AppError("请先添加连接")
    if connection.protocol == "bge" and data.kind != "embedding":
        raise AppError("BGE 连接仅支持 Embedding")
    if connection.protocol == "anthropic" and data.thinking == "enabled" and data.max_output_tokens <= 1024:
        raise AppError("Claude 思考模式需要输出上限大于 1024")
    if data.kind == "embedding" and connection.protocol not in ("openai", "azure", "google", "bge"):
        raise AppError("此协议尚未提供 Embedding，请使用厂商的兼容 Embedding 连接。")
    row = session.get(ManagedModel, identifier) if identifier else None
    if identifier and not row:
        raise AppError("模型不存在", "not_found", 404)
    config_lock(session)
    if row and row.kind != data.kind:
        raise AppError("模型类型不能修改，请另建模型")
    if not row:
        row = ManagedModel(id=new_id(), revision_id=new_id(), kind=data.kind)
        session.add(row)
    row.connection_id, row.name, row.enabled = data.connection_id, data.name, data.enabled
    session.flush()
    config = data.model_dump(exclude={"name", "enabled"})
    config.update(protocol=connection.protocol, base_url=connection.base_url,
                  endpoint_version=connection.endpoint_version, provider=connection.provider)
    previous = session.get(ModelRevision, row.revision_id)
    if not previous or previous.config != config:
        revision = ModelRevision(id=new_id(), model_id=row.id, config=config)
        session.add(revision)
        session.flush()
        row.revision_id = revision.id
    return row


def revision_config(session, revision_id):
    revision = session.get(ModelRevision, revision_id) if revision_id else None
    if not revision:
        raise ConfigurationRequired("未配置该用途的模型，请在设置中分配模型。")
    model = session.get(ManagedModel, revision.model_id)
    connection = session.get(ModelConnection, revision.config["connection_id"])
    if not model.enabled or not connection or not connection.enabled:
        raise ConfigurationRequired("模型或连接已停用，请更换配置后重新分析。")
    if connection.endpoint_version != revision.config["endpoint_version"]:
        raise ConfigurationRequired("连接目标已变更，请重新保存模型并重新分析。")
    return {**revision.config, "revision_id": revision.id, "model_id": model.id}


def snapshot(session, run):
    if run.model_snapshot is None:
        config_lock(session)
        prefs = preferences(session)
        values = {}
        for stage in STAGES:
            identifier = prefs.bindings.get(stage) or prefs.bindings.get("default")
            model = session.get(ManagedModel, identifier) if identifier else None
            values[stage] = model.revision_id if model and model.enabled else None
        # 未配置的阶段保持空值，但首次配置后允许恢复原来等待配置的任务。
        run.model_snapshot = values
    return run.model_snapshot


def resolve_run(session, run_id, stage):
    run = session.get(ProcessingRun, run_id)
    values = snapshot(session, run)
    stage = "analyze" if stage.startswith("analyze") else stage
    identifier = values.get(stage) or (values.get("default") if stage not in STAGES else None)
    if not identifier:
        # 只补原来为空的用途，已执行阶段和其他已固定的修订不随首次配置而改变。
        prefs = preferences(session)
        model_id = prefs.bindings.get(stage) or prefs.bindings.get("default")
        model = session.get(ManagedModel, model_id) if model_id else None
        identifier = model.revision_id if model and model.enabled else None
        if identifier:
            run.model_snapshot = {**values, stage: identifier}
    return revision_config(session, identifier)


def connection_out(row):
    return {name: getattr(row, name) for name in
            ("id", "name", "provider", "protocol", "base_url", "enabled", "endpoint_version")} | {
                "key_configured": bool(row.key_cipher)}


def overview(session):
    prefs = preferences(session)
    models = []
    for row in session.scalars(select(ManagedModel)):
        revision = session.get(ModelRevision, row.revision_id)
        probes = list(session.scalars(select(ModelProbe).where(ModelProbe.revision_id == row.revision_id)
                      .order_by(ModelProbe.created_at.desc()).limit(5)))
        models.append({"id": row.id, "name": row.name, "enabled": row.enabled,
            "revision_id": row.revision_id, **revision.config,
            "probes": [{"kind": p.kind, "succeeded": p.succeeded, "detail": p.detail,
                        "duration_seconds": p.duration_seconds, "created_at": p.created_at} for p in probes]})
    return {"connections": [connection_out(c) for c in session.scalars(select(ModelConnection))],
            "models": models, "bindings": prefs.bindings, "active_space_id": prefs.active_space_id}


def public_status(session):
    prefs = preferences(session)
    row = session.get(ManagedModel, prefs.bindings.get("default")) if prefs.bindings.get("default") else None
    conn = session.get(ModelConnection, row.connection_id) if row else None
    space = session.get(EmbeddingSpace, prefs.active_space_id) if prefs.active_space_id else None
    return {"llm": bool(row and row.enabled and conn and conn.enabled),
            "llm_model": session.get(ModelRevision, row.revision_id).config["model"] if row else "",
            "embeddings": bool(space), "embedding_model":
                session.get(ModelRevision, space.revision_id).config["model"] if space else ""}


def fingerprint(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
