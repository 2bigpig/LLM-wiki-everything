"""默认测试使用独立内存库，不连接或清理用户的 PostgreSQL。"""

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from wiki_app import db, models  # noqa: F401
from wiki_app.config import ROOT, get_settings
from wiki_app.main import app


@pytest.fixture(autouse=True)
def configure(monkeypatch, tmp_path):
    """独立 YAML 隔离用户密钥、数据目录和服务开关。"""
    path = tmp_path / "config.yaml"
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    raw["paths"].update(data_dir=str(tmp_path), log_dir=str(tmp_path / "logs"))
    raw["models"]["embeddings_enabled"] = False
    raw["llm"]["llm_model"] = ""
    monkeypatch.setenv("WIKI_CONFIG_FILE", str(path))
    monkeypatch.setenv("WIKI_RUNTIME", "host")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("API_TOKEN", "")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-only")

    def update(**sections):
        for section, values in sections.items():
            raw[section].update(values)
        path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
        get_settings.cache_clear()
        return path

    update()
    yield update
    get_settings.cache_clear()


@pytest.fixture
def environment(monkeypatch, tmp_path, configure):
    engine = db.build_engine("sqlite+pysqlite:///:memory:")
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(engine, expire_on_commit=False))
    yield tmp_path
    engine.dispose()
    get_settings.cache_clear()


@pytest.fixture
def client(environment):
    with TestClient(app) as client:
        yield client
