"""浏览器测试专用入口：独立数据库和明确注入的离线模型，不参与正式部署。"""

import os
import threading
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

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
raw["review"]["review_insights_enabled"] = False
config = folder / "config.yaml"
config.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
from cryptography.fernet import Fernet  # noqa: E402

os.environ.update(WIKI_CONFIG_FILE=str(config), WIKI_RUNTIME="host",
                  POSTGRES_PASSWORD="test-only", API_TOKEN="browser-test-token", LLM_API_KEY="",
                  MODEL_CREDENTIAL_KEY=Fernet.generate_key().decode())

from test_workflow import fake_complete  # noqa: E402

from wiki_app import db, models  # noqa: E402
from wiki_app.config import get_settings  # noqa: E402
from wiki_app.errors import AppError  # noqa: E402

# SQLite 仅存在于测试装配；正式配置只派生 PostgreSQL 连接。
db.engine = db.build_engine("sqlite+pysqlite:///" + str(folder / "test.db"))
db.SessionLocal = sessionmaker(db.engine, expire_on_commit=False)
from wiki_app.integrations import extraction, platform_video, web  # noqa: E402
from wiki_app.main import app  # noqa: E402
from wiki_app.modules.capture.service import create_capture, current_inputs  # noqa: E402
from wiki_app.modules.processing import graph  # noqa: E402
from wiki_app.modules.review.service import generate_daily  # noqa: E402

db.Base.metadata.create_all(db.engine)
graph.llm.complete = fake_complete
from wiki_app.modules.model_management import runtime as model_runtime  # noqa: E402

# 仅浏览器进程注入响应；管理、凭据、修订和计量仍走产品接口。
model_runtime.chat = lambda *args: {"text": '{"ok":true}', "usage": {
    "input_tokens": 10, "output_tokens": 2, "total_tokens": 12}}
model_runtime.embedding = lambda config, key, texts: ([[1.0] + [0.0] * 1023 for _ in texts], {})
saver = InMemorySaver()

# 浏览器验收使用明确的网页/视频/OCR 替身，不访问真实网站或占用用户 GPU。
web_attempts = {}


def read_test_webpage(url):
    """临时失败替身用于浏览器验收逐来源重试，其余来源不受影响。"""
    web_attempts[url] = web_attempts.get(url, 0) + 1
    if "/retry-fixture-" in url and web_attempts[url] == 1:
        raise AppError("测试来源首次读取失败。", "fetch_failed")
    return {"url": url, "text": "Rerank 离线示例网页原文。", "coverage": "测试网页正文。"}


web.read_webpage = read_test_webpage
platform_video.read_platform_video = lambda url, record_id, version: (
    [{"text": "Rerank 离线视频字幕原文。", "start": 0.0, "end": 2.0}], None)
extraction.inference = lambda kind, key: {"segments": [{"text": "Rerank 截图识别验收。"}]}

# 只在这个独立测试数据库准备旧资料，验证回顾无需等待真实的十四天。
with db.transaction() as session:
    for index in range(3):
        record = create_capture(session, text=f"回顾验收旧资料 {index + 1}，保留当时的知识记录。")
        record.created_at = models.now() - timedelta(days=30 + index)
        record.status = "completed"
        record.coverage = {"status": "complete", "source_count": 1, "details": []}
        for item in current_inputs(session, record):
            item.status = "succeeded"
            session.add(models.Source(record_id=record.id, version=1, input_id=item.id,
                                      origin=item.origin, ordinal=0, text=item.text, method="user_text",
                                      locator={"kind": "original_text"}, search_text=item.text))
        for run in session.scalars(select(models.ProcessingRun).where(models.ProcessingRun.record_id == record.id)):
            run.status, run.stage = "completed", "completed"
# 固定到本地当日的生成时间，CI 在凌晨运行也能验证卡片，不依赖真实时刻。
generate_daily(models.now().astimezone(ZoneInfo(get_settings().review_timezone)).replace(hour=9, minute=0))


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
