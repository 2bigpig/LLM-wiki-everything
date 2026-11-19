"""默认测试使用独立内存库，不连接或清理用户的 PostgreSQL。"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from wiki_app import db, models  # noqa: F401
from wiki_app.config import get_settings
from wiki_app.main import app


@pytest.fixture
def environment(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_MODEL", "")
    monkeypatch.setenv("API_TOKEN", "")
    monkeypatch.setenv("EMBEDDINGS_ENABLED", "false")
    get_settings.cache_clear()
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
