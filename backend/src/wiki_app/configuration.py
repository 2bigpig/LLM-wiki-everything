"""配置命令：安全迁移旧环境、为 Compose 注入配置、为准备脚本输出公共字段。"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml

from wiki_app.config import (
    ROOT,
    SECRET_KEYS,
    PublicConfig,
    Settings,
    get_settings,
    read_private,
    read_public,
)


def compose_environment(settings: Settings) -> dict[str, str]:
    """仅传递容器需要的数据库密码；LLM 与 API 密钥不进入模型容器。"""
    values = {
        "WIKI_CONFIG_SOURCE": settings.config_file.as_posix(),
        "WIKI_DATA_SOURCE": settings.data_dir.as_posix(),
        "WIKI_DATA_TARGET": settings.container_data_dir,
        "WIKI_BIND_HOST": settings.model_bind_host,
        "WIKI_DB_BIND_HOST": settings.db_host,
        "WIKI_DB_PORT": settings.db_port,
        "WIKI_DB_INTERNAL_PORT": settings.db_container_port,
        "WIKI_DB_NAME": settings.db_name,
        "WIKI_DB_USER": settings.db_user,
        "WIKI_OCR_PORT": settings.ocr_port,
        "WIKI_ASR_PORT": settings.asr_port,
        "WIKI_BGE_PORT": settings.embedding_port,
        "WIKI_MODEL_PORT": settings.model_http_port,
        "WIKI_HF_CACHE": settings.hf_cache_dir,
        "WIKI_OCR_CACHE": settings.ocr_cache_dir,
        "WIKI_DOWNLOAD_TIMEOUT": int(settings.model_download_timeout_seconds),
        "WIKI_PG_HEALTH_INTERVAL": settings.postgres_health_interval_seconds,
        "WIKI_PG_HEALTH_TIMEOUT": settings.postgres_health_timeout_seconds,
        "WIKI_PG_HEALTH_RETRIES": settings.postgres_health_retries,
        "WIKI_MODEL_HEALTH_INTERVAL": settings.model_health_interval_seconds,
        "WIKI_MODEL_HEALTH_TIMEOUT": settings.model_health_timeout_seconds,
        "WIKI_MODEL_HEALTH_RETRIES": settings.model_health_retries,
        "POSTGRES_PASSWORD": settings.postgres_password.get_secret_value(),
    }
    # 不继承调用方的普通配置覆盖；内部变量由本次解析结果重新填入。
    environment = {key: value for key, value in os.environ.items()
                   if key not in SECRET_KEYS and not key.startswith("WIKI_")
                   and key not in {"COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES"}}
    return {**environment, **{key: str(value) for key, value in values.items()}}


def run_compose(arguments: list[str]) -> int:
    """所有脚本使用同一 Compose 项目与命名卷，不生成含密钥的中间文件。"""
    settings = get_settings()
    if not settings.postgres_password.get_secret_value():
        raise ValueError("缺少 POSTGRES_PASSWORD，请填写项目 .env。")
    command = ["docker", "compose", "--env-file", os.devnull, "-f", str(ROOT / "compose.yaml"),
               "-f", str(ROOT / "compose.gpu.yaml"), *arguments]
    return subprocess.call(command, cwd=ROOT, env=compose_environment(settings))


def replace_yaml_values(text: str, updates: dict[tuple[str, str], object]) -> str:
    """只替换目标值，保留公共模板的中文注释与其他配置。"""
    document = yaml.compose(text)
    edits = []
    for section, contents in document.value:
        for key, value in contents.value:
            location = (section.value, key.value)
            if location in updates:
                rendered = yaml.safe_dump(updates[location], allow_unicode=True,
                                          default_flow_style=True).removesuffix("...\n").strip()
                edits.append((value.start_mark.index, value.end_mark.index, rendered))
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def migrate_env(path: Path):
    """先完整转换、检查冲突，再备份并写入；写入失败回滚，不接触数据库。"""
    read_public(path)
    env_path = path.parent / ".env"
    original = env_path.read_bytes()
    public_bytes = path.read_bytes()
    old = read_private(env_path)
    if not set(old) - set(SECRET_KEYS):
        print(".env 已是密钥格式，无需迁移。")
        return
    raw = yaml.safe_load(public_bytes.decode("utf-8-sig"))
    locations = {key.upper(): (section, key) for section, items in raw.items() for key in items}
    special = {"DATABASE_URL", "OCR_URL", "ASR_URL", "EMBEDDING_URL", "CORS_ORIGINS"}
    unknown = set(old) - set(SECRET_KEYS) - set(locations) - special
    if unknown:
        raise ValueError("无法迁移的旧配置键：" + ", ".join(sorted(unknown)))
    updates = {}
    for name, location in locations.items():
        if name not in old:
            continue
        value = old[name] or ""
        current = raw[location[0]][location[1]]
        try:
            if isinstance(current, bool):
                if value.lower() not in ("true", "false", "1", "0"):
                    raise ValueError()
                value = value.lower() in ("true", "1")
            elif isinstance(current, int):
                value = int(value)
            elif isinstance(current, float):
                value = float(value)
        except ValueError:
            raise ValueError("旧配置类型错误：" + name) from None
        updates[location] = value
    if old.get("DATABASE_URL"):
        try:
            parsed = urlsplit(old["DATABASE_URL"])
            if parsed.scheme != "postgresql+psycopg" or parsed.query or parsed.fragment:
                raise ValueError()
            if unquote(parsed.password or "") != (old.get("POSTGRES_PASSWORD") or ""):
                raise ValueError()
            db = {"db_host": parsed.hostname, "db_port": parsed.port or 5432,
                  "db_user": unquote(parsed.username or ""), "db_name": unquote(parsed.path[1:])}
            if not all(db.values()):
                raise ValueError()
            if any(("database", key) in updates and updates[("database", key)] != value
                   for key, value in db.items()):
                raise ValueError()
            updates.update({("database", key): value for key, value in db.items()})
        except ValueError:
            raise ValueError("DATABASE_URL 无法迁移或与 POSTGRES_PASSWORD 冲突。") from None
    model_hosts = set()
    for name, port_key, enabled_key in (("OCR_URL", "ocr_port", "ocr_enabled"),
                                        ("ASR_URL", "asr_port", "asr_enabled"),
                                        ("EMBEDDING_URL", "embedding_port", None)):
        if name not in old:
            continue
        if not old[name] and enabled_key:
            updates[("models", enabled_key)] = False
            continue
        try:
            parsed = urlsplit(old[name] or "")
            if parsed.scheme != "http" or parsed.path not in ("", "/") or parsed.query \
                    or parsed.fragment or parsed.username or not parsed.hostname:
                raise ValueError()
            model_hosts.add(parsed.hostname)
            if ("services", port_key) in updates and updates[("services", port_key)] != (parsed.port or 80):
                raise ValueError()
            updates[("services", port_key)] = parsed.port or 80
        except ValueError:
            raise ValueError(name + " 无法迁移为本机模型服务地址。") from None
    if len(model_hosts) > 1:
        raise ValueError("旧模型服务地址不一致，无法迁移到统一 model_bind_host。")
    if model_hosts:
        updates[("services", "model_bind_host")] = model_hosts.pop()
    if "CORS_ORIGINS" in old:
        updates[("services", "extra_cors_origins")] = [
            item.strip() for item in (old["CORS_ORIGINS"] or "").split(",") if item.strip()]
    for (section, key), value in updates.items():
        raw[section][key] = value
    try:
        PublicConfig.model_validate(raw)
    except ValueError:
        raise ValueError("旧参数迁移后不满足 config.yaml 约束；原文件未修改。") from None
    new_public = replace_yaml_values(public_bytes.decode("utf-8-sig"), updates)
    # 模板替换后的内容再校验，防止复杂标量编码破坏结果。
    PublicConfig.model_validate(yaml.safe_load(new_public))
    # dotenv 单引号编码，禁止插值，保留包含引号、反斜杠和 $ 的密钥原值。
    def encoded(value):
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    new_private = "# 私密配置，请勿提交；普通参数在 config.yaml。\n" + "".join(
        f"{name}={encoded(old.get(name) or '')}\n" for name in SECRET_KEYS)
    stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    backup = env_path.with_name(".env.backup." + stamp)
    backup.write_bytes(original)
    backup.with_suffix(backup.suffix + ".yaml").write_bytes(public_bytes)
    try:
        path.write_text(new_public, encoding="utf-8")
        env_path.write_text(new_private, encoding="utf-8")
    except OSError:
        path.write_bytes(public_bytes)
        env_path.write_bytes(original)
        raise ValueError("配置写入失败，已恢复原文件并保留备份。") from None
    print("旧配置迁移完成，已保留 .env.backup.* 备份；未输出任何密钥。")


def main() -> int:
    parser = argparse.ArgumentParser(description="统一配置工具；不输出私密配置")
    parser.add_argument("action", choices=["check", "migrate-env", "public", "compose"])
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.action == "migrate-env":
            migrate_env(Path(os.environ.get("WIKI_CONFIG_FILE", ROOT / "config.yaml")).resolve())
        elif args.action == "compose":
            return run_compose(args.arguments)
        elif args.action == "public":
            settings = get_settings()
            print(json.dumps({"ffmpeg_dir": str(settings.ffmpeg_dir),
                              "ffmpeg_download_url": settings.ffmpeg_download_url,
                              "ffmpeg_download_timeout_seconds": settings.ffmpeg_download_timeout_seconds},
                             ensure_ascii=True))
        else:
            get_settings()
            print("公共配置与私密配置校验通过。")
    except (ValueError, OSError) as exc:
        # 业务错误由上述固定消息提供；系统异常不打印路径/命令环境。
        print(str(exc) if isinstance(exc, ValueError) else "配置命令执行失败，请检查文件权限或 Docker。",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
