"""HTTP 入口只负责交互和装配；长任务由独立 Worker 执行。"""

import logging
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from wiki_app import db
from wiki_app.config import get_settings
from wiki_app.errors import AppError
from wiki_app.integrations.llm import month_start
from wiki_app.logging_config import configure_logging
from wiki_app.models import ModelCall, ProcessingRun, Record, Relation, WikiPage
from wiki_app.modules.capture.router import router as capture_router
from wiki_app.modules.graph.router import router as graph_router
from wiki_app.modules.knowledge.router import router as wiki_router
from wiki_app.modules.library.router import router as library_router
from wiki_app.modules.model_management.router import router as models_router
from wiki_app.modules.review.router import router as review_router
from wiki_app.modules.search.service import retrieve
from wiki_app.presentation import columns, run_out

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging("api")
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    logger.info("API 启动 data_dir=%s embeddings_enabled=%s",
                settings.data_dir.resolve(), settings.embeddings_enabled)
    yield
    db.engine.dispose()


app = FastAPI(title="everything · 个人知识库", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def request_log(request: Request, call_next):
    """浏览器响应携带请求 ID，便于按 ID 查日志；不记录查询参数、正文或鉴权头。"""
    request_id, started = uuid4().hex, time.monotonic()
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("HTTP 未处理异常 request_id=%s method=%s path=%s",
                         request_id, request.method, request.url.path)
        raise
    response.headers["X-Request-ID"] = request_id
    logger.info("HTTP request_id=%s method=%s path=%s status=%d elapsed=%.3fs",
                request_id, request.method, request.url.path, response.status_code,
                time.monotonic() - started)
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in get_settings().cors_origins.split(",")],
    allow_methods=["GET", "POST", "PUT", "PATCH"],
    allow_headers=["Content-Type", "Idempotency-Key"],
    expose_headers=["X-Request-ID"],
)


@app.exception_handler(AppError)
async def app_error(_request: Request, exc: AppError):
    return JSONResponse({"detail": exc.message, "code": exc.code}, status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError):
    """FastAPI 默认错误含原始 input，管理表单可能携带密钥，统一只回字段路径。"""
    fields = [".".join(map(str, e["loc"])) for e in exc.errors()]
    return JSONResponse({"detail": "参数校验失败：" + "、".join(fields), "code": "validation_error"}, status_code=422)


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exc: SQLAlchemyError):
    # SQLAlchemy 异常文本可能带 SQL 参数，仅记录类型和请求 ID，避免正文/凭据落盘。
    logger.error("数据库请求失败 request_id=%s error=%s",
                 getattr(request.state, "request_id", ""), type(exc).__name__)
    return JSONResponse(
        {"detail": "数据库暂不可用，请检查数据库服务与迁移状态。", "code": "database_error"},
        status_code=503,
    )


# 开发阶段直接访问；用户登录与 RBAC 后续统一接入，不再维护临时共享口令。
api = APIRouter(prefix="/api")
api.include_router(models_router)
api.include_router(library_router)
api.include_router(wiki_router)


@app.get("/healthz")
def health():
    with db.engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ok"}


@api.get("/overview")
def overview():
    with db.SessionLocal() as session:
        return {
            "records": session.scalar(
                select(func.count()).select_from(Record).where(Record.archived.is_(False))
            ),
            "pages": session.scalar(select(func.count()).select_from(WikiPage)),
            "relations": session.scalar(
                select(func.count()).select_from(Relation).where(Relation.feedback != "ignored")
            ),
            "pending": session.scalar(
                select(func.count())
                .select_from(ProcessingRun)
                .where(ProcessingRun.status.in_(("queued", "running", "waiting_schedule")))
            ),
        }


@api.get("/capabilities")
def capabilities():
    settings = get_settings()
    from wiki_app.modules.model_management.service import public_status

    with db.transaction() as session:
        status = public_status(session)
    return {
        "llm": status["llm"],
        "llm_model": status["llm_model"],
        "ocr": bool(settings.ocr_url),
        "asr": bool(settings.asr_url),
        "embeddings": status["embeddings"],
        "embedding_model": status["embedding_model"],
        "browser": settings.browser_enabled,
        "access_token_required": False,  # 兼容旧客户端字段；开发阶段没有临时口令。
        "max_upload_mb": settings.max_upload_mb,
        "max_media_minutes": settings.max_media_minutes,
        "monthly_token_limit": settings.monthly_token_limit,
        "platforms": ["普通网页正文", "直接输入", "本地文件"],
        "support_note": "平台链接需样本验证；图像默认仅 OCR，视频默认音轨转写与采样帧 OCR。",
    }


@api.get("/usage")
def usage():
    with db.SessionLocal() as session:
        rows = list(
            session.scalars(
                select(ModelCall)
                .where(ModelCall.created_at >= month_start())
                .order_by(ModelCall.created_at.desc())
            )
        )
        return {
            "used_tokens": sum(row.charged_tokens for row in rows),
            "limit": get_settings().monthly_token_limit,
            "has_estimates": any(row.input_tokens is None and row.charged_tokens > 0 for row in rows),
            "calls": [
                columns(
                    row,
                    "id run_id stage model status charged_tokens input_tokens output_tokens duration_seconds created_at connection_id revision_id operation",
                )
                for row in rows[:get_settings().usage_calls_limit]
            ],
        }


@api.get("/runs")
def runs(limit: int = Query(default=100, ge=1, le=200)):
    with db.SessionLocal() as session:
        rows = session.execute(
            select(ProcessingRun, Record.title)
            .join(Record)
            .order_by(ProcessingRun.created_at.desc())
            .limit(limit)
        )
        return {"items": [{**run_out(run), "record_title": title} for run, title in rows]}


@api.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    with db.transaction() as session:
        run = session.get(ProcessingRun, run_id)
        if not run:
            raise AppError("任务不存在。", "not_found", 404)
        record = session.scalar(
            select(Record).where(Record.id == run.record_id).with_for_update(key_share=True)
        )
        session.refresh(run)
        if run.status in ("queued", "running", "waiting_schedule"):
            run.status, run.scheduled_for, run.error = "cancelled", None, None
            if record.version == run.version:
                record.status = "cancelled"
        return run_out(run)


@api.get("/search")
def search(q: str = Query(min_length=1, max_length=300)):
    warnings = []
    items = retrieve(q, limit=get_settings().search_api_limit, warnings=warnings)
    from wiki_app.modules.model_management.service import public_status

    with db.transaction() as session:
        enabled = public_status(session)["embeddings"]
    return {"items": items, "semantic": enabled and not warnings, "warnings": warnings}


api.include_router(capture_router)
api.include_router(graph_router)
api.include_router(review_router)
app.include_router(api)
