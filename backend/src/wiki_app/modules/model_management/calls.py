"""一次预算预留对应一次远程请求；测试、Embedding 和整理共用额度锁。"""

import time

from pydantic import ValidationError
from sqlalchemy import func, select, text

from wiki_app import db
from wiki_app.config import get_settings
from wiki_app.errors import AppError, BudgetExceeded
from wiki_app.integrations.resources import BUDGET_LOCK
from wiki_app.models import ModelCall, ModelConnection, ProcessingRun, Record

from . import runtime
from .service import decrypt, revision_config


def execute(config, operation, reservation, callback, *, run_id=None, stage="", request_hash="", validate=None):
    from wiki_app.integrations.llm import month_start

    settings = get_settings()
    with db.transaction() as session:
        if db.engine.dialect.name == "postgresql":
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": BUDGET_LOCK})
        revision_config(session, config["revision_id"])
        if run_id:
            run = session.get(ProcessingRun, run_id)
            record = session.get(Record, run.record_id) if run else None
            if not run or not record or run.status == "cancelled" or record.archived or record.version != run.version:
                raise AppError("记录已修改或任务已停止。", "cancelled", 409)
            cached = session.scalar(select(ModelCall).where(ModelCall.run_id == run_id,
                ModelCall.request_hash == request_hash, ModelCall.status == "succeeded"))
            if cached:
                return cached.response
            if operation == "llm":
                from .scheduling import authorize

                authorize(session, run, stage)
            count = session.scalar(select(func.count()).select_from(ModelCall).where(
                ModelCall.run_id == run_id, ModelCall.operation == "llm"))
            if operation == "llm" and count >= settings.max_calls_per_run:
                raise BudgetExceeded("本次整理达到模型调用上限，请调整配置后重试。")
        used = session.scalar(select(func.coalesce(func.sum(ModelCall.charged_tokens), 0)).where(
            ModelCall.created_at >= month_start()))
        if reservation and used + reservation > settings.monthly_token_limit:
            raise BudgetExceeded()
        connection = session.get(ModelConnection, config["connection_id"])
        key = decrypt(connection.key_cipher)
        call = ModelCall(run_id=run_id, connection_id=connection.id, revision_id=config["revision_id"],
            operation=operation, stage=stage or operation, model=config["model"],
            request_hash=request_hash, charged_tokens=reservation)
        session.add(call)
        session.flush()
        call_id = call.id
    start, result = time.monotonic(), None
    try:
        result = callback(key)
        if result.get("incomplete"):
            raise AppError("模型输出达到长度上限，已保留用量，请调整输出额度后重新分析。", "invalid_output", 502)
        output = validate(result) if validate else result.get("result")
    except Exception as exc:
        finish(call_id, result, time.monotonic() - start,
               "invalid_output" if isinstance(exc, (ValidationError, ValueError)) or
               isinstance(exc, AppError) and exc.code == "invalid_output" else "unknown")
        if isinstance(exc, AppError):
            raise
        if isinstance(exc, (ValidationError, ValueError)):
            raise AppError("模型输出格式不符合要求，请检查该模型的 JSON 输出能力。", "model_error", 502) from None
        raise runtime.model_error(exc) from None
    finish(call_id, result, time.monotonic() - start, "succeeded", output)
    return output


def finish(identifier, result, elapsed, status, output=None):
    with db.transaction() as session:
        call = session.get(ModelCall, identifier)
        call.status, call.duration_seconds = status, elapsed
        if isinstance(output, dict):
            call.response = output
        usage = result.get("usage") if result else None
        if usage and all(isinstance(usage.get(k), int) and not isinstance(usage.get(k), bool)
                         and usage[k] >= 0 for k in ("input_tokens", "output_tokens")):
            call.input_tokens, call.output_tokens = usage["input_tokens"], usage["output_tokens"]
            call.charged_tokens = call.input_tokens + call.output_tokens
            total = usage.get("total_tokens")
            if isinstance(total, int) and not isinstance(total, bool):
                call.charged_tokens = max(call.charged_tokens, total)


def embed(config, texts):
    reservation = 0 if config["protocol"] == "bge" else sum(len(t.encode()) for t in texts)

    def request(key):
        vectors, usage = runtime.embedding(config, key, texts)
        if config["protocol"] == "bge":
            usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        return {"result": vectors, "usage": usage}

    return execute(config, "embedding", reservation, request)
