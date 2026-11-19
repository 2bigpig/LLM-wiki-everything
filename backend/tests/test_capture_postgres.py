"""真实 PostgreSQL 旧数据升级与并发验收，仅操作随机 schema。"""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from wiki_app import db
from wiki_app.config import get_settings
from wiki_app.main import app
from wiki_app.models import (
    CaptureInput,
    Record,
    RecordTopic,
    ReviewBatch,
    ReviewCard,
    Source,
    WikiPage,
)
from wiki_app.modules.capture.service import create_capture
from wiki_app.modules.graph.service import graph_data
from wiki_app.modules.review.service import generate_daily


@pytest.fixture
def native(monkeypatch, configure):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("设置 TEST_DATABASE_URL 验证原生旧数据迁移与并发")
    schema = "wiki_capture_test_" + uuid4().hex
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    parts = urlsplit(url)
    params = dict(parse_qsl(parts.query))
    params["options"] = f"-csearch_path={schema},public"
    scoped = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), ""))
    parsed = make_url(scoped)
    configure(database={"db_host": parsed.host, "db_port": parsed.port or 5432,
                        "db_user": parsed.username, "db_name": parsed.database,
                        "db_options": params["options"]})
    monkeypatch.setenv("POSTGRES_PASSWORD", parsed.password or "")
    get_settings.cache_clear()
    engine = db.build_engine(scoped)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(engine, expire_on_commit=False))
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["version_table_schema"] = schema
    try:
        yield engine, config
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
        get_settings.cache_clear()


def test_legacy_data_backfill_keeps_ids_dates_and_citations(native):
    engine, config = native
    command.upgrade(config, "0001")
    metadata = MetaData()
    metadata.reflect(engine, only=["records", "sources", "attachments", "analyses", "processing_runs", "wiki_pages"])
    clock = datetime(2025, 1, 1, tzinfo=timezone.utc)
    # 以反射旧表写入，不让新 ORM 的额外列掩盖兼容性问题。
    with engine.begin() as connection:
        for identifier, url in (("legacy-video", "https://youtu.be/legacy"), ("legacy-long", None)):
            connection.execute(metadata.tables["records"].insert().values(
                id=identifier, kind="link" if url else "text", title=identifier,
                content="旧正文 " + " ".join(f"https://example.com/{i}" for i in range(50)) if not url else "",
                url=url, url_key="legacy-key" if url else None, notes="保留旧备注", purpose="auto", version=1,
                status="completed", coverage={}, archived=False, created_at=clock, updated_at=clock))
        connection.execute(metadata.tables["processing_runs"].insert().values(
            id="legacy-run", record_id="legacy-video", version=1, status="completed", stage="completed",
            attempts=1, created_at=clock, updated_at=clock, finished_at=clock))
        connection.execute(metadata.tables["sources"].insert().values(
            id="legacy-source", record_id="legacy-video", version=1, origin="url", ordinal=0,
            text="旧字幕引用保持不变", locator={"url": "https://youtu.be/legacy"}, method="platform_subtitles", search_text="旧字幕"))
        connection.execute(metadata.tables["attachments"].insert().values(
            id="legacy-media", record_id="legacy-video", filename="video.mp4", storage_key="derived/platform-test.mp4",
            media_type="video/mp4", size=100, sha256="a" * 64))
        connection.execute(metadata.tables["sources"].insert().values(
            id="legacy-asr", record_id="legacy-video", version=1, origin="legacy-media", ordinal=0,
            text="旧转写引用保持不变", locator={"attachment_id": "legacy-media"}, method="asr", search_text="旧转写"))
        connection.execute(metadata.tables["analyses"].insert().values(
            id="legacy-analysis", run_id="legacy-run", record_id="legacy-video", version=1,
            kind="learning", summary="旧分析", topics=["Rerank"], points=[], observations="", model="test", created_at=clock))
        connection.execute(metadata.tables["wiki_pages"].insert().values(
            id="legacy-wiki", title="旧 Wiki", title_key="旧wiki", generated="旧文本", personal="个人笔记",
            version=1, source_ids=["legacy-source"], updated_at=clock))
    command.upgrade(config, "head")
    with db.transaction() as session:
        record = session.get(Record, "legacy-video")
        assert record.created_at == clock and record.notes == "保留旧备注"
        assert session.get(Source, "legacy-source").input_id
        assert session.get(Source, "legacy-asr").input_id == session.get(Source, "legacy-source").input_id
        assert session.get(WikiPage, "legacy-wiki").source_ids == ["legacy-source"]
        assert session.scalar(select(RecordTopic.id).where(RecordTopic.record_id == record.id))
        assert session.scalar(select(func.count()).select_from(CaptureInput).where(
            CaptureInput.record_id == "legacy-long", CaptureInput.kind == "url")) == 0
        second = create_capture(session, text="https://www.youtube.com/watch?v=legacy")
        old_resource = session.scalar(select(CaptureInput.resource_id).where(
            CaptureInput.record_id == record.id, CaptureInput.kind == "url"))
        assert session.scalar(select(CaptureInput.resource_id).where(CaptureInput.record_id == second.id)) == old_resource
        data = graph_data(session, focus="record:legacy-video", hops=2)
        assert any(edge["kind"] == "citation" for edge in data["edges"])
    generate_daily(datetime(2026, 11, 19, 2, tzinfo=timezone.utc))
    with db.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ReviewCard)) > 0


def test_concurrent_captures_and_daily_batch_are_idempotent(native):
    _, config = native
    command.upgrade(config, "head")
    with TestClient(app) as client:
        def submit(key):
            response = client.post("/api/captures", data={"text": "https://example.com/concurrent"},
                                   headers={"Idempotency-Key": key})
            assert response.status_code in (200, 201), response.text
            return response.json()["id"]

        with ThreadPoolExecutor(max_workers=4) as pool:
            same = list(pool.map(submit, ["same-key"] * 4))
        assert len(set(same)) == 1
        with ThreadPoolExecutor(max_workers=4) as pool:
            different = list(pool.map(submit, [f"active-{n}" for n in range(4)]))
        assert len(set(different)) == 4
    clock = datetime(2026, 11, 19, 2, tzinfo=timezone.utc)
    with db.transaction() as session:
        for record in session.scalars(select(Record)):
            record.created_at = clock - timedelta(days=30)
        assert session.scalar(select(func.count(func.distinct(CaptureInput.resource_id)))) == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(generate_daily, [clock] * 4))
    with db.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(ReviewBatch)) == 1
        assert session.scalar(select(func.count()).select_from(ReviewCard)) == 1
