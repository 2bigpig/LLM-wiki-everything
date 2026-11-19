"""搜索只召回未归档记录的当前版本；旧引用仍可单独访问。"""

import logging
import math
import re
import time

import httpx
import jieba
from sqlalchemy import case, func, literal_column, or_, select

from wiki_app import db
from wiki_app.config import get_settings
from wiki_app.errors import AppError, ConfigurationRequired
from wiki_app.models import Analysis, Record, Source

logger = logging.getLogger(__name__)


def terms(value: str) -> list[str]:
    return list(
        dict.fromkeys(
            t.lower()
            for t in jieba.cut_for_search(value)
            if re.fullmatch(r"[\w+#.-]+", t) and t.strip(".-")
        )
    )


def searchable(value: str) -> str:
    return " ".join(terms(value))


def embed(texts: list[str]) -> list[list[float]]:
    """调用 Docker 中的 BGE-M3；校验模型、批量和维度，防止错误向量写入索引。"""
    if not texts:
        return []
    settings = get_settings()
    if not settings.embedding_url:
        raise ConfigurationRequired("请配置 EMBEDDING_URL，并启动 bge 容器。")
    started = time.monotonic()
    try:
        # 禁用系统代理，避免本机模型请求被代理转发；首次模型加载允许较长等待。
        with httpx.Client(timeout=httpx.Timeout(180, connect=10), trust_env=False) as client:
            response = client.post(
                settings.embedding_url.rstrip("/") + "/embed",
                json={"texts": texts, "model": settings.embedding_model},
            )
            response.raise_for_status()
            data = response.json()
        vectors = data["embeddings"]
        if data["model"] != settings.embedding_model or len(vectors) != len(texts):
            raise ValueError("向量模型或返回数量不匹配")
        for vector in vectors:
            if not isinstance(vector, list) or len(vector) != 1024 or not all(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) for value in vector
            ):
                raise ValueError("向量必须包含 1024 个有限数值")
        logger.info("BGE 调用完成 model=%s count=%d elapsed=%.2fs",
                    settings.embedding_model, len(texts), time.monotonic() - started)
        return vectors
    except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError) as exc:
        logger.warning("BGE 调用失败 error=%s count=%d elapsed=%.2fs",
                       type(exc).__name__, len(texts), time.monotonic() - started)
        raise AppError(
            "向量服务不可用或返回格式错误，请检查 EMBEDDING_URL 和 bge 容器日志。",
            "embedding_failed", 502,
        ) from exc


def index_sources(record_id: str, version: int):
    if not get_settings().embeddings_enabled:
        return
    with db.SessionLocal() as session:
        sources = list(
            session.scalars(
                select(Source).where(
                    Source.record_id == record_id,
                    Source.version == version,
                    Source.embedding.is_(None),
                )
            )
        )
    # 分批提交；中断后仅补齐尚未生成的向量。
    for offset in range(0, len(sources), 8):
        batch = sources[offset : offset + 8]
        vectors = embed([s.text for s in batch])
        with db.transaction() as session:
            for source, vector in zip(batch, vectors, strict=True):
                stored = session.get(Source, source.id)
                stored.embedding, stored.embedding_model = vector, get_settings().embedding_model


def retrieve(query: str, limit: int = 12, exclude_record: str | None = None) -> list[dict]:
    tokens = terms(query)[:16]
    if not tokens:
        return []
    with db.SessionLocal() as session:
        base = (
            select(Source, Record)
            .join(Record, Record.id == Source.record_id)
            .where(
                Record.archived.is_(False),
                Source.version == Record.version,
            )
        )
        if exclude_record:
            base = base.where(Source.record_id != exclude_record)
        if db.engine.dialect.name == "postgresql":
            document = func.to_tsvector(literal_column("'simple'"), Source.search_text)
            # 每个词按纯文本解析，技术名词中的 +/# 不会变成 tsquery 控制语法。
            query_vector = func.plainto_tsquery(literal_column("'simple'"), tokens[0])
            for token in tokens[1:]:
                query_vector = query_vector.op("||")(
                    func.plainto_tsquery(literal_column("'simple'"), token)
                )
            keyword = (
                base.where(document.op("@@")(query_vector))
                .order_by(
                    func.ts_rank_cd(document, query_vector).desc(),
                    Source.id,
                )
                .limit(40)
            )
        else:
            matches = [Source.search_text.contains(t, autoescape=True) for t in tokens]
            score = sum(case((match, 1), else_=0) for match in matches)
            keyword = base.where(or_(*matches)).order_by(score.desc(), Source.id).limit(40)
        lists = [list(session.execute(keyword))]
        settings = get_settings()
        if settings.embeddings_enabled and db.engine.dialect.name == "postgresql":
            vector = embed([query])[0]
            semantic = (
                base.where(Source.embedding_model == settings.embedding_model)
                .order_by(
                    Source.embedding.cosine_distance(vector),
                    Source.id,
                )
                .limit(40)
            )
            lists.append(list(session.execute(semantic)))
        fused: dict[str, dict] = {}
        for ranked in lists:
            for rank, (source, record) in enumerate(ranked, 1):
                hit = fused.setdefault(
                    source.id,
                    {
                        "id": source.id,
                        "record_id": record.id,
                        "record_title": record.title,
                        "record_kind": record.kind,
                        "text": source.text,
                        "locator": source.locator,
                        "score": 0.0,
                    },
                )
                hit["score"] += 1 / (60 + rank)
        return sorted(fused.values(), key=lambda hit: (-hit["score"], hit["id"]))[:limit]


def latest_analyses(session, record_ids: list[str]) -> dict[str, Analysis]:
    result = {}
    for analysis in session.scalars(
        select(Analysis)
        .where(Analysis.record_id.in_(record_ids))
        .order_by(Analysis.created_at.desc())
    ):
        result.setdefault(analysis.record_id, analysis)
    return result
