"""可选真实 PostgreSQL 验证：只创建和清理本测试生成的独立 schema。"""

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from test_workflow import fake_complete

from wiki_app import db
from wiki_app.config import get_settings
from wiki_app.main import app
from wiki_app.models import ProcessingRun, Source
from wiki_app.modules.processing import graph
from wiki_app.modules.search import service as search


def test_postgres_migration_checkpoint_search_and_queue(monkeypatch, tmp_path):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("设置 TEST_DATABASE_URL 后验证 PostgreSQL、pgvector、检查点与任务队列")
    schema = "wiki_test_" + uuid4().hex
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    parts = urlsplit(url)
    params = dict(parse_qsl(parts.query))
    params["options"] = f"-csearch_path={schema},public"
    test_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), ""))
    monkeypatch.setenv("DATABASE_URL", test_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("API_TOKEN", "")
    monkeypatch.setenv("EMBEDDINGS_ENABLED", "false")
    get_settings.cache_clear()
    engine = db.build_engine(test_url)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(graph.llm, "complete", fake_complete)
    try:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        # 明确限定版本表 schema，防止已有 public.alembic_version 导致误跳过迁移。
        config.attributes["version_table_schema"] = schema
        command.upgrade(config, "head")
        with PostgresSaver.from_conn_string(get_settings().postgres_dsn) as saver:
            saver.setup()
            with TestClient(app) as client:
                response = client.post(
                    "/api/records", json={"content": "Rerank 在检索后对候选结果重新排序。"}
                )
                assert response.status_code == 201, response.text
                saved = response.json()
                run_id = client.get("/api/records/" + saved["id"]).json()["runs"][0]["id"]
                graph.execute_run(run_id, saver)
                assert client.get("/api/records/" + saved["id"]).json()["status"] == "completed"
                assert client.get("/api/search?q=Rerank").json()["items"]
                assert saver.get_tuple({"configurable": {"thread_id": run_id}}) is not None
                vector = [1.0] + [0.0] * 1023
                with db.transaction() as session:
                    source = session.scalar(select(Source))
                    source.embedding, source.embedding_model = vector, "BAAI/bge-m3"
                monkeypatch.setenv("EMBEDDINGS_ENABLED", "true")
                get_settings.cache_clear()
                monkeypatch.setattr(search, "embed", lambda values: [vector for _ in values])
                assert client.get("/api/search?q=相关性").json()["items"]
                monkeypatch.setenv("EMBEDDINGS_ENABLED", "false")
                get_settings.cache_clear()

                # 使用本测试 schema 的独立 App，避免连接到其他开发会话的队列。
                import procrastinate

                from wiki_app import worker

                queue = procrastinate.App(
                    connector=procrastinate.PsycopgConnector(conninfo=get_settings().postgres_dsn)
                )

                @queue.task(queue="verify")
                def process(run_id: str):
                    worker.process_run.func(run_id=run_id)

                async def check_queue():
                    async with queue.open_async():
                        await queue.schema_manager.apply_schema_async()
                        second = client.post(
                            "/api/records",
                            json={"content": "Rerank 的面试回答需要解释候选数量和成本。"},
                        ).json()
                        second_run = client.get("/api/records/" + second["id"]).json()["runs"][0][
                            "id"
                        ]
                        await process.configure(
                            lock=second_run, queueing_lock=second_run
                        ).defer_async(run_id=second_run)
                        await queue.run_worker_async(
                            queues=["verify"],
                            wait=False,
                            concurrency=1,
                            install_signal_handlers=False,
                            listen_notify=False,
                        )
                        with db.SessionLocal() as session:
                            assert session.get(ProcessingRun, second_run).status == "completed"

                with asyncio.Runner(
                    loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None
                ) as runner:
                    runner.run(check_queue())
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
        get_settings.cache_clear()
