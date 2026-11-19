"""浏览器测试专用入口：独立数据库和明确注入的离线模型，不参与正式部署。"""

import os
import threading
import time
from pathlib import Path
from uuid import uuid4

import uvicorn
import yaml
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

workspace = Path(__file__).resolve().parents[2]
folder = workspace / "data" / "browser-tests" / str(uuid4())
folder.mkdir(parents=True)
raw = yaml.safe_load(Path(os.environ.get("WIKI_CONFIG_FILE", workspace / "config.yaml")).read_text(encoding="utf-8"))
raw["paths"].update(data_dir=str(folder / "files"), log_dir=str(folder / "logs"))
raw["models"]["embeddings_enabled"] = False
raw["llm"]["llm_model"] = "offline-browser-test"
config = folder / "config.yaml"
config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
os.environ.update(WIKI_CONFIG_FILE=str(config), WIKI_RUNTIME="host",
                  POSTGRES_PASSWORD="test-only", API_TOKEN="", LLM_API_KEY="")

from test_workflow import fake_complete  # noqa: E402

from wiki_app import db, models  # noqa: E402
from wiki_app.config import get_settings  # noqa: E402

# SQLite 仅存在于测试装配；正式配置只派生 PostgreSQL 连接。
db.engine = db.build_engine("sqlite+pysqlite:///" + str(folder / "test.db"))
db.SessionLocal = sessionmaker(db.engine, expire_on_commit=False)
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
    settings = get_settings()
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
