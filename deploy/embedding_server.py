"""独立 BGE-M3 服务：模型权重留在 Docker 卷，本地后端只发送文本并接收向量。"""

import json
import logging
import os
import sys
import time
from functools import lru_cache
from threading import Lock
from typing import Annotated

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-m3")
# 单进程串行编码，限制 CPU/内存峰值；健康检查不需要等待模型锁。
model_lock = Lock()
app = FastAPI(title="BGE-M3 本地向量服务")


class EmbeddingInput(BaseModel):
    """限制批量和单段长度，避免一次请求耗尽容器内存。"""

    texts: list[Annotated[str, Field(min_length=1, max_length=100_000)]] = Field(
        min_length=1, max_length=64
    )
    model: str = MODEL


@lru_cache(maxsize=1)
def get_model():
    """首次编码时加载；后续请求复用 CPU 模型，业务后端无需安装 Torch。"""
    from sentence_transformers import SentenceTransformer

    logger.info("加载向量模型 model=%s device=cpu cache=/models/hf", MODEL)
    model = SentenceTransformer(MODEL, device="cpu")
    model.max_seq_length = 1024
    if model.get_sentence_embedding_dimension() != 1024:
        raise RuntimeError("当前数据库要求 1024 维向量，请检查 EMBEDDING_MODEL")
    logger.info("向量模型加载完成 model=%s dimensions=1024", MODEL)
    return model


@app.get("/healthz")
def health():
    """ready 代表 HTTP 就绪；model_loaded 单独说明模型是否完成首次加载。"""
    return {"status": "ready", "model": MODEL, "model_loaded": bool(get_model.cache_info().currsize)}


@app.post("/embed")
def embed(data: EmbeddingInput):
    """返回归一化向量；不记录用户正文，日志只包含模型、批量和耗时。"""
    if data.model != MODEL:
        raise HTTPException(422, "请求模型与服务模型不一致，请检查 EMBEDDING_MODEL")
    started = time.monotonic()
    try:
        with model_lock:
            vectors = get_model().encode(data.texts, batch_size=2, normalize_embeddings=True)
        logger.info("向量生成完成 count=%d elapsed=%.2fs", len(data.texts), time.monotonic() - started)
        return {"model": MODEL, "embeddings": vectors.tolist()}
    except Exception as exc:
        logger.exception("向量生成失败 model=%s count=%d", MODEL, len(data.texts))
        raise HTTPException(503, "向量生成失败，请检查 bge 容器日志") from exc


if __name__ == "__main__":
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if "--prepare" in sys.argv:
        result = embed(EmbeddingInput(texts=["检索与知识整理"]))
        print(json.dumps({"model": MODEL, "dimensions": len(result["embeddings"][0]), "device": "cpu"}))
    else:
        import uvicorn

        uvicorn.run(app, host="0.0.0.0", port=8100)
