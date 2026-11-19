"""独立推理容器入口：子进程完成后退出，避免 OCR 与 ASR 模型同时常驻显存。"""

import json
import logging
import os
import subprocess
import sys
import time
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from wiki_app.integrations.resources import gpu_slot
from wiki_app.integrations.storage import storage_path

app = FastAPI(title="Local inference", docs_url=None, redoc_url=None)
logger = logging.getLogger(__name__)


class InferenceInput(BaseModel):
    storage_key: str = Field(min_length=1, max_length=300)


@app.get("/healthz")
def health():
    """只报告服务就绪，不在健康检查中占用显存加载模型。"""
    return {"status": "ready", "kind": os.environ.get("INFERENCE_KIND", "")}


@app.post("/infer")
def infer(data: InferenceInput, x_request_id: str = Header(default="")):
    """使用相同请求 ID 关联本地 Worker 与容器日志；UUID 标准化防止日志注入。"""
    try:
        request_id = UUID(x_request_id).hex
    except ValueError:
        request_id = uuid4().hex
    path = storage_path(data.storage_key)
    if not path.is_file():
        logger.warning("共享文件不存在 request_id=%s storage_key=%s resolved_path=%s",
                       request_id, data.storage_key, path)
        raise HTTPException(404, "文件不存在")
    kind = os.environ.get("INFERENCE_KIND", "")
    if kind not in ("ocr", "asr"):
        raise HTTPException(503, "未配置推理类型")
    started = time.monotonic()
    logger.info("等待 GPU request_id=%s kind=%s storage_key=%s",
                request_id, kind, data.storage_key)
    # 锁覆盖子进程存活期；本地 Worker 断开不会提前释放单卡互斥。
    with gpu_slot():
        logger.info("开始推理 request_id=%s kind=%s", request_id, kind)
        try:
            result = subprocess.run(
                [sys.executable, "-m", "wiki_app.inference_task", kind, data.storage_key],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=7200,
                check=False,
            )
            if result.returncode:
                logger.error("推理子进程失败 request_id=%s kind=%s exit_code=%d stderr=%s",
                             request_id, kind, result.returncode, result.stderr[-4000:])
                raise HTTPException(503, "推理失败，请检查容器和显卡环境")
            output = json.loads(result.stdout)
            logger.info("推理完成 request_id=%s kind=%s segments=%d elapsed=%.2fs",
                        request_id, kind, len(output.get("segments", [])),
                        time.monotonic() - started)
            return output
        except subprocess.TimeoutExpired as exc:
            logger.warning("推理超时 request_id=%s kind=%s", request_id, kind)
            raise HTTPException(504, "推理超时") from exc


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(app, host="0.0.0.0", port=8100)
