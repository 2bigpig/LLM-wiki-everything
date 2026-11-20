"""新空间、旧向量迁移和真实队列，仅在测试独立 schema 内执行。"""

import asyncio
import json
import sys

import procrastinate
from alembic import command
from sqlalchemy import func, select, text
from test_capture_postgres import native as native

from wiki_app import db
from wiki_app.config import get_settings
from wiki_app.models import EmbeddingSpace, ModelPreferences, Record, Source, SourceVector
from wiki_app.modules.model_management import calls, service, vectors
from wiki_app.modules.model_management.schemas import ConnectionInput, ModelInput
from wiki_app.modules.search.service import retrieve


def test_old_vector_migration_and_worker_rebuild(native, monkeypatch):
    engine, config = native
    command.upgrade(config, "0002")
    vector = [1.0] + [0.0] * 1023
    # 使用旧表 SQL 写入，证明迁移保留旧向量而非只验证空库建表。
    with engine.begin() as connection:
        connection.execute(text("""INSERT INTO records
            (id,kind,title,content,notes,purpose,version,status,coverage,archived,created_at,updated_at)
            VALUES ('old-vector','text','原始资料','原文','','auto',1,'completed','{}',false,now(),now())"""))
        connection.execute(text("""INSERT INTO sources
            (id,record_id,version,origin,ordinal,text,locator,method,search_text,embedding,embedding_model)
            VALUES ('old-vector-source','old-vector',1,'text',0,'原始证据','{}','text','原始 证据',
                    CAST(:vector AS vector),'BAAI/bge-m3')"""), {"vector": json.dumps(vector)})
    command.upgrade(config, "head")
    from wiki_app.modules.model_management.scheduling import prepare_schema
    prepare_schema(db.engine)
    with db.transaction() as session:
        old = session.scalar(select(EmbeddingSpace))
        assert session.scalar(select(func.count()).select_from(SourceVector)) == 1
        service.preferences(session).active_space_id = old.id
        connection = service.save_connection(session, ConnectionInput(name="新 BGE", provider="bge", protocol="bge",
            base_url="http://127.0.0.1:19991"))
        model = service.save_model(session, ModelInput(connection_id=connection.id, name="新向量模型",
            model="bge-test", kind="embedding"))
        space = EmbeddingSpace(revision_id=model.revision_id)
        session.add(space)
        session.flush()
        identifier, old_id = space.id, old.id
        record = session.get(Record, "old-vector")
        session.add(Source(record_id=record.id, version=1, origin="text", ordinal=1,
            text="新补充来源", locator={}, method="text", search_text="新 补充 来源"))
    assert vectors.action(identifier, "activate")["status"] == "building"
    with db.SessionLocal() as session:
        assert session.get(ModelPreferences, "personal").active_space_id == old_id
    monkeypatch.setattr(calls.runtime, "embedding", lambda c, key, texts: ([vector for _ in texts], None))
    from wiki_app import worker

    queue = procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=get_settings().postgres_dsn))

    @queue.task(queue="verify-models")
    def rebuild(space_id):
        worker.rebuild_space.func(space_id=space_id)

    async def run():
        async with queue.open_async():
            await queue.schema_manager.apply_schema_async()
            await rebuild.configure(lock=identifier, queueing_lock=identifier).defer_async(space_id=identifier)
            await queue.run_worker_async(queues=["verify-models"], wait=False, concurrency=1,
                install_signal_handlers=False, listen_notify=False)

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        runner.run(run())
    with db.SessionLocal() as session:
        assert session.get(ModelPreferences, "personal").active_space_id == identifier
        assert session.scalar(select(func.count()).select_from(SourceVector).where(SourceVector.space_id == identifier)) == 2
    # 新旧空间均有向量也只能查询当前空间，不重复放大证据。
    assert retrieve("完全不同的语义查询")
    vectors.action(old_id, "activate")
    vectors.rebuild(old_id)
    with db.SessionLocal() as session:
        assert session.get(ModelPreferences, "personal").active_space_id == old_id
