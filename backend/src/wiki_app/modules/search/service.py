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
from wiki_app.models import Analysis, CaptureInput, Record, Source, SourceVector
from wiki_app.modules.model_management import calls, vectors

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


def embed(texts: list[str], config=None) -> list[list[float]]:
    """调用 Docker 中的 BGE-M3；校验模型、批量和维度，防止错误向量写入索引。"""
    if not texts:
        return []
    if config is not None:
        return calls.embed(config, texts)
    settings = get_settings()
    if not settings.embedding_url:
        raise ConfigurationRequired("请检查 config.yaml 的模型服务配置，并启动 bge 容器。")
    started = time.monotonic()
    try:
        # 禁用系统代理，避免本机模型请求被代理转发；首次模型加载允许较长等待。
        with httpx.Client(timeout=httpx.Timeout(settings.embedding_timeout_seconds, connect=settings.model_connect_timeout_seconds), trust_env=False) as client:
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
            "向量服务不可用或返回格式错误，请检查 config.yaml 和 bge 容器日志。",
            "embedding_failed", 502,
        ) from exc


def index_sources(record_id: str, version: int):
    with db.transaction() as session:
        try:
            space_id, config = vectors.active_config(session)
        except AppError:
            logger.warning("向量连接配置不可用，保留关键词索引 record_id=%s", record_id)
            return
        if not config:
            return
        sources = list(
            session.scalars(
                select(Source).where(
                    Source.record_id == record_id,
                    Source.version == version,
                    ~select(SourceVector.id).where(SourceVector.source_id == Source.id,
                        SourceVector.space_id == space_id).exists(),
                )
            )
        )
    # 分批提交；中断后仅补齐尚未生成的向量。
    batch_size = get_settings().index_batch_size
    for offset in range(0, len(sources), batch_size):
        batch = sources[offset : offset + batch_size]
        try:
            values = embed([s.text for s in batch], config)
            vectors.store(space_id, batch, values)
        except AppError:
            # 模型异常或预算不足不能阻止已保存内容的关键词检索和后续 Wiki 整理。
            logger.warning("向量索引暂不可用 record_id=%s space_id=%s", record_id, space_id)
            return


def retrieve(query: str, limit: int | None = None, exclude_record: str | None = None,
             warnings: list[str] | None = None) -> list[dict]:
    settings = get_settings()
    limit = settings.search_default_limit if limit is None else limit
    tokens = terms(query)[:settings.search_max_terms]
    if not tokens:
        return []
    with db.transaction() as session:
        # 先在数据库中选出同资源、同原文的一个代表，再做关键词/向量召回。
        # 原始事件与备注仍独立持久化，检索不把相同原文当成多份证据。
        identity = func.coalesce(CaptureInput.resource_id, Source.id)
        representatives = select(Source.id.label("source_id"), func.row_number().over(
            partition_by=(identity, Source.text), order_by=(Record.created_at.desc(), Source.id)
        ).label("position")).join(Record).outerjoin(CaptureInput, CaptureInput.id == Source.input_id).where(
            Record.archived.is_(False), Source.version == Record.version)
        if exclude_record:
            representatives = representatives.where(Source.record_id != exclude_record)
        representatives = representatives.subquery()
        base = (
            select(Source, Record)
            .join(Record, Record.id == Source.record_id)
            .join(representatives, representatives.c.source_id == Source.id)
            .where(
                representatives.c.position == 1,
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
                .limit(settings.search_candidates)
            )
        else:
            matches = [Source.search_text.contains(t, autoescape=True) for t in tokens]
            score = sum(case((match, 1), else_=0) for match in matches)
            keyword = base.where(or_(*matches)).order_by(score.desc(), Source.id).limit(settings.search_candidates)
        lists = [list(session.execute(keyword))]
        settings = get_settings()
        if db.engine.dialect.name == "postgresql":
            try:
                space_id, config = vectors.active_config(session)
                if config:
                    vector = embed([query], config)[0]
                    semantic = base.join(SourceVector, SourceVector.source_id == Source.id).where(
                        SourceVector.space_id == space_id).order_by(
                        SourceVector.embedding.cosine_distance(vector), Source.id).limit(settings.search_candidates)
                    lists.append(list(session.execute(semantic)))
            except AppError:
                if warnings is not None:
                    warnings.append("语义检索暂不可用，本次使用关键词检索。")
                logger.warning("语义检索暂不可用，本次使用关键词")
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
                hit["score"] += 1 / (settings.search_rrf_k + rank)
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
