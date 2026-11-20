"""本机幂等准备：先备份，导入成功才移除旧 LLM 密钥，不输出秘密值。"""

import os
from datetime import datetime

from cryptography.fernet import Fernet
from sqlalchemy import select

from wiki_app.config import SECRET_KEYS, get_settings, read_private


def write_private(path, values):
    """先写同目录临时文件再替换，避免进程中断写坏 .env。"""
    import json

    temporary = path.with_name(path.name + ".preparing")
    temporary.write_text("# 私密配置，请勿提交或分享。\n" + "\n".join(
        f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in values.items()) + "\n", encoding="utf-8")
    temporary.replace(path)


def prepare():
    settings = get_settings()
    path = settings.config_file.parent / ".env"
    original = path.read_bytes() if path.exists() else None
    values = read_private(path)
    # 同当前加载器一致，显式进程密钥优先；不把其他环境变量复制到文件。
    for name in SECRET_KEYS:
        if name in os.environ:
            values[name] = os.environ[name]
    if not values.get("MODEL_CREDENTIAL_KEY"):
        values["MODEL_CREDENTIAL_KEY"] = Fernet.generate_key().decode()
    try:
        Fernet(values["MODEL_CREDENTIAL_KEY"].encode())
    except ValueError:
        raise ValueError("已有主密钥格式无效，未修改文件。") from None
    backup = None
    if original is not None:
        backup = path.with_name(path.name + ".backup.models." + datetime.now().strftime("%Y%m%d%H%M%S%f"))
        backup.write_bytes(original)
    # 主密钥先落盘，保证数据库提交后即使文件收尾失败也能恢复凭据。
    write_private(path, values)
    os.environ["MODEL_CREDENTIAL_KEY"] = values["MODEL_CREDENTIAL_KEY"]
    get_settings.cache_clear()
    committed = False
    try:
        from wiki_app import db
        from wiki_app.models import ModelConnection
        from wiki_app.modules.model_management.scheduling import prepare_schema
        from wiki_app.modules.model_management.schemas import ConnectionInput, ModelInput
        from wiki_app.modules.model_management.service import (
            decrypt,
            preferences,
            save_connection,
            save_model,
        )

        prepare_schema(db.engine)
        with db.transaction() as session:
            prefs = preferences(session)
            # 验证原主密钥可解密已有数据；错误时绝不导入或替换。
            for connection in session.scalars(select(ModelConnection)):
                decrypt(connection.key_cipher)
            if values.get("LLM_API_KEY") and settings.llm_model and not prefs.bindings.get("default"):
                connection = save_connection(session, ConnectionInput(name="原 LLM 配置", provider="custom",
                    protocol="openai", base_url=settings.llm_base_url, api_key=values["LLM_API_KEY"]))
                model = save_model(session, ModelInput(connection_id=connection.id, name=settings.llm_model,
                    model=settings.llm_model, timeout_seconds=settings.llm_timeout_seconds,
                    max_output_tokens=settings.llm_max_output_tokens))
                prefs.bindings = {"default": model.id}
            elif values.get("LLM_API_KEY"):
                # 没有模型名称，或页面已配置其他默认模型时，不能丢弃尚未导入的旧密钥。
                matched = any(c.name == "原 LLM 配置" and c.base_url == settings.llm_base_url.rstrip("/")
                    and decrypt(c.key_cipher) == values["LLM_API_KEY"]
                    for c in session.scalars(select(ModelConnection)))
                if not matched:
                    raise ValueError("旧 LLM 密钥尚未导入：请补齐初始模型名称，或在确认旧连接后手动清理旧键。")
        committed = True
        values.pop("LLM_API_KEY", None)
        write_private(path, values)
    except Exception:
        # 数据库事务已经回滚；原文件原样恢复，备份继续保留。
        if committed:
            # DB 已提交时保留已落盘的新主密钥，不能恢复旧文件使新凭据不可恢复。
            write_private(path, {**values, "LLM_API_KEY": settings.llm_api_key.get_secret_value()})
        elif original is not None:
            path.write_bytes(original)
        else:
            path.unlink(missing_ok=True)
        get_settings.cache_clear()
        raise
    get_settings.cache_clear()
    print("模型管理准备完成；加密主密钥保存在 .env，打开设置页即可配置模型。")
    if backup:
        print("原私密配置已在同目录创建 .env.backup.models.* 备份，请妥善保管。")


if __name__ == "__main__":
    try:
        prepare()
    except Exception as exc:
        # 数据库底层异常可能带连接信息，只显示可控中文错误或类型。
        print(str(exc) if isinstance(exc, ValueError) else f"准备失败（{type(exc).__name__}），请先迁移数据库并检查配置。")
        raise SystemExit(1) from None
