"""浏览器测试专用入口：独立数据库和明确注入的离线模型，不参与正式部署。"""

import os
import threading
import time
from pathlib import Path
from uuid import uuid4

import uvicorn
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

workspace = Path(__file__).resolve().parents[2]
folder = workspace / "data" / "browser-tests" / str(uuid4())
folder.mkdir(parents=True)
os.environ.update(
    DATABASE_URL="sqlite+pysqlite:///" + str(folder / "test.db"),
    DATA_DIR=str(folder / "files"),
    API_TOKEN="",
    EMBEDDINGS_ENABLED="false",
    LLM_API_KEY="",
    LLM_MODEL="offline-browser-test",
)

from test_workflow import fake_complete  # noqa: E402

from wiki_app import db, models  # noqa: E402
from wiki_app.main import app  # noqa: E402
from wiki_app.modules.processing import graph  # noqa: E402

db.Base.metadata.create_all(db.engine)
graph.llm.complete = fake_complete
saver = InMemorySaver()


def worker():
    while True:
        with db.SessionLocal() as session:
            ids = list(
                session.scalars(
                    select(models.ProcessingRun.id).where(models.ProcessingRun.status == "queued")
                )
            )
        for run_id in ids:
            graph.execute_run(run_id, saver)
        time.sleep(0.3)


if __name__ == "__main__":
    threading.Thread(target=worker, daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=8000)
