"""LangGraph 节点调用业务服务；持久化证据和业务幂等独立于检查点。"""

import logging
from typing import TypedDict
from uuid import NAMESPACE_URL, uuid5

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select

from wiki_app import db
from wiki_app.config import get_settings
from wiki_app.errors import AppError, BudgetExceeded, ConfigurationRequired
from wiki_app.integrations import llm
from wiki_app.integrations.extraction import extract_record
from wiki_app.integrations.resources import WIKI_LOCK, advisory_slot
from wiki_app.models import Analysis, Attachment, ProcessingRun, Record, Source, WikiPage, now
from wiki_app.modules.knowledge.service import apply_wiki, save_relation, title_key
from wiki_app.modules.search.service import index_sources, retrieve, searchable
from wiki_app.schemas import AnalysisDraft, RelationsDraft, WikiDrafts

logger = logging.getLogger(__name__)


class RunState(TypedDict):
    run_id: str


def context(session, run_id: str, stage: str | None = None):
    run = session.get(ProcessingRun, run_id)
    if not run:
        raise AppError("处理任务不存在。", "cancelled", 404)
    record = session.scalar(
        select(Record).where(Record.id == run.record_id).with_for_update(key_share=True)
    )
    if run.status == "cancelled" or record.archived or record.version != run.version:
        raise AppError("记录已修改、归档或任务已取消，停止旧版本处理。", "cancelled", 409)
    if stage:
        # run_id 对应界面里的任务 ID；出现卡住时可按它追踪最后完成的阶段。
        logger.info("任务阶段 run_id=%s record_id=%s stage=%s", run_id, record.id, stage)
        run.stage, run.status, run.error = stage, "running", None
        run.updated_at, record.status = now(), "running"
    return run, record


def sources_for(session, record_id: str, version: int) -> list[Source]:
    return list(
        session.scalars(
            select(Source)
            .where(
                Source.record_id == record_id,
                Source.version == version,
            )
            .order_by(Source.origin, Source.ordinal)
        )
    )


def source_payload(sources: list[Source]) -> list[dict]:
    return [{"id": s.id, "text": s.text, "locator": s.locator, "method": s.method} for s in sources]


def extract_node(state: RunState):
    with db.transaction() as session:
        run, record = context(session, state["run_id"], "extract")
        if sources_for(session, record.id, run.version):
            return {}
        attachments = list(
            session.scalars(select(Attachment).where(Attachment.record_id == record.id))
        )
    pieces, coverage = extract_record(record, attachments)
    with db.transaction() as session:
        run, record = context(session, state["run_id"])
        for attachment in attachments:
            if not session.get(Attachment, attachment.id):
                session.add(attachment)
        for piece in pieces:
            sid = str(
                uuid5(
                    NAMESPACE_URL, f"{record.id}:{run.version}:{piece['origin']}:{piece['ordinal']}"
                )
            )
            if not session.get(Source, sid):
                session.add(
                    Source(
                        id=sid,
                        record_id=record.id,
                        version=run.version,
                        search_text=searchable(record.title + " " + piece["text"]),
                        **piece,
                    )
                )
        record.coverage = coverage
    return {}


def analyze_node(state: RunState):
    with db.transaction() as session:
        run, record = context(session, state["run_id"], "analyze")
        if session.scalar(select(Analysis.id).where(Analysis.run_id == run.id)):
            return {}
        sources = sources_for(session, record.id, run.version)
    groups, group, length = [], [], 0
    for source in sources:
        if group and length + len(source.text) > 13000:
            groups.append(group)
            group, length = [], 0
        group.append(source)
        length += len(source.text)
    if group:
        groups.append(group)
    parts = []
    for index, group in enumerate(groups):
        result = llm.complete(
            run.id,
            f"analyze:{index}",
            "整理本组资料。根据内容和用户用途选择摘要、学习整理、面试复盘或随笔整理。"
            "面试整理问题与回答，保留引用；不要编造评分。用户自述保留身份。"
            "每个要点必须引用给定 source_ids；AI 推断单独写 observations。",
            {
                "title": record.title,
                "purpose": record.purpose,
                "notes": record.notes,
                "coverage": record.coverage,
                "sources": source_payload(group),
            },
            AnalysisDraft,
        )
        provided = {s.id for s in group}
        if any(sid not in provided for point in result.points for sid in point.source_ids):
            llm.invalidate_result(run.id, f"analyze:{index}")
            raise AppError("分析引用了未提供的片段，结果未保存。", "invalid_evidence", 502)
        parts.append(result)
    result = (
        parts[0]
        if len(parts) == 1
        else llm.complete(
            run.id,
            "analyze:merge",
            "综合各组摘要，保留来源 ID，合并重复要点并保留不同观点。不得增加资料之外的事实。",
            {"parts": [part.model_dump() for part in parts]},
            AnalysisDraft,
        )
    )
    provided = {s.id for s in sources}
    if any(sid not in provided for point in result.points for sid in point.source_ids):
        llm.invalidate_result(run.id, "analyze:merge")
        raise AppError("综合分析引用不合法，结果未保存。", "invalid_evidence", 502)
    with db.transaction() as session:
        context(session, run.id)
        if not session.scalar(select(Analysis.id).where(Analysis.run_id == run.id)):
            session.add(
                Analysis(
                    run_id=run.id,
                    record_id=record.id,
                    version=run.version,
                    model=get_settings().llm_model,
                    **result.model_dump(),
                )
            )
    return {}


def relate_node(state: RunState):
    with db.transaction() as session:
        run, record = context(session, state["run_id"], "relate")
        sources = sources_for(session, record.id, run.version)
        analysis = session.scalar(select(Analysis).where(Analysis.run_id == run.id))
    index_sources(record.id, run.version)
    candidates = retrieve(
        " ".join(analysis.topics) + " " + analysis.summary[:get_settings().relation_summary_chars], limit=get_settings().relation_candidates, exclude_record=record.id
    )
    if not candidates:
        return {}
    highlighted_ids = list(dict.fromkeys(sid for p in analysis.points for sid in p["source_ids"]))[
        :10
    ]
    highlighted = [s for s in sources if s.id in highlighted_ids]
    result = llm.complete(
        run.id,
        "relate",
        "判断当前资料与候选旧资料之间是否存在具体关系。"
        "每条关系给出两侧的逐字原文引用。仅关键词相似不足以建立关系。没有足够证据就返回空列表。",
        {"current": source_payload(highlighted), "candidates": candidates},
        RelationsDraft,
    )
    try:
        with db.transaction() as session:
            context(session, run.id)
            for relation in result.relations:
                save_relation(
                    session,
                    record.id,
                    relation,
                    {s.id: s for s in highlighted},
                    {c["id"]: c for c in candidates},
                )
    except AppError:
        llm.invalidate_result(run.id, "relate")
        raise
    return {}


def wiki_node(state: RunState):
    # ponytail: 个人库自动维护先全局串行，规模扩大后改为按页面锁定并有界重试。
    with advisory_slot(WIKI_LOCK):
        return update_wiki_node(state)


def update_wiki_node(state: RunState):
    with db.transaction() as session:
        run, record = context(session, state["run_id"], "wiki")
        analysis = session.scalar(select(Analysis).where(Analysis.run_id == run.id))
        sources = sources_for(session, record.id, run.version)
        keys = {title_key(topic) for topic in analysis.topics}
        # ponytail: 个人库先按配置上限扫描页标题；规模扩大后增加主题索引召回。
        pages = [
            page
            for page in session.scalars(
                select(WikiPage).order_by(WikiPage.updated_at.desc()).limit(get_settings().wiki_scan_limit)
            )
            if page.title_key in keys
            or any(key in page.title_key or page.title_key in key for key in keys)
        ][:get_settings().wiki_existing_limit]
        versions = {page.id: page.version for page in pages}
        old_ids = set(sid for page in pages for sid in page.source_ids)
        old_sources = (
            list(session.scalars(select(Source).where(Source.id.in_(old_ids)))) if old_ids else []
        )
    allowed = {s.id: s for s in old_sources + sources}
    previous = [
        {
            "id": p.id,
            "title": p.title,
            "version": p.version,
            "generated": p.generated,
            "source_ids": p.source_ids,
        }
        for p in pages
    ]
    if sum(len(p.generated) for p in pages) > get_settings().wiki_context_chars:
        raise AppError(
            "相关 Wiki 已超过本次综合大小上限，旧页面未改动，请拆分主题后重试。", "context_limit"
        )
    result = llm.complete(
        run.id,
        "wiki",
        "根据新资料维护长期 Wiki，优先综合已有页面。"
        "返回需要创建或更新的完整页面 sections，每节都要给出来源 ID。"
        "更新已有页面时保留仍然有效的旧知识、不同观点及其引用，page_id 必须来自 existing_pages。"
        "新页 page_id 为 null。随笔不含可复用知识时可返回空 pages 并解释 reason。"
        "不要创建空泛的大主题，不要改写或输出用户个人笔记。最多 3 页。",
        {
            "analysis": {
                "summary": analysis.summary,
                "topics": analysis.topics,
                "points": analysis.points,
            },
            "purpose": record.purpose,
            "sources": source_payload(list(allowed.values())),
            "existing_pages": previous,
        },
        WikiDrafts,
    )
    identities = [draft.page_id or title_key(draft.title) for draft in result.pages]
    if len(identities) != len(set(identities)):
        llm.invalidate_result(run.id, "wiki")
        raise AppError("模型重复返回了同一 Wiki 页面，请重试。", "invalid_output", 502)
    try:
        with db.transaction() as session:
            run, record = context(session, run.id)
            for draft in result.pages:
                apply_wiki(session, draft, run.id, allowed, versions)
            run.status, run.stage, run.finished_at = "completed", "completed", now()
            record.status = "completed"
            if not result.pages and result.reason:
                record.coverage = {**record.coverage, "wiki_note": result.reason}
    except AppError:
        llm.invalidate_result(state["run_id"], "wiki")
        raise
    return {}


def build_graph(checkpointer):
    builder = StateGraph(RunState)
    for name, node in (
        ("extract", extract_node),
        ("analyze", analyze_node),
        ("relate", relate_node),
        ("wiki", wiki_node),
    ):
        builder.add_node(name, node)
    builder.add_edge(START, "extract")
    builder.add_edge("extract", "analyze")
    builder.add_edge("analyze", "relate")
    builder.add_edge("relate", "wiki")
    builder.add_edge("wiki", END)
    return builder.compile(checkpointer=checkpointer)


def execute_run(run_id: str, checkpointer):
    """恢复同一任务的检查点；业务状态和图检查点分别提交，重试必须复用 run_id。"""
    logger.info("任务开始 run_id=%s", run_id)
    with db.transaction() as session:
        run = session.get(ProcessingRun, run_id)
        if not run or run.status in (
            "completed",
            "cancelled",
            "waiting_configuration",
            "budget_exceeded",
        ):
            return
        context(session, run_id)
        run.status, run.attempts, run.error = "running", run.attempts + 1, None
    try:
        graph = build_graph(checkpointer)
        config = {"configurable": {"thread_id": run_id}, "recursion_limit": get_settings().graph_recursion_limit}
        saved = graph.get_state(config)
        # 恢复时传 None；重新传入初始输入会启动新的执行路径。
        graph.invoke(None if saved.values else {"run_id": run_id}, config=config, durability="sync")
        logger.info("任务执行结束 run_id=%s", run_id)
    except Exception as exc:
        status = (
            "waiting_configuration"
            if isinstance(exc, ConfigurationRequired)
            else "budget_exceeded"
            if isinstance(exc, BudgetExceeded)
            else "cancelled"
            if isinstance(exc, AppError) and exc.code == "cancelled"
            else "failed"
        )
        with db.transaction() as session:
            run = session.get(ProcessingRun, run_id)
            record = session.scalar(
                select(Record).where(Record.id == run.record_id).with_for_update(key_share=True)
            )
            session.refresh(run)
            if run.status == "cancelled":
                status = "cancelled"
            run.status = status
            run.error = (
                exc.message if isinstance(exc, AppError) else "处理异常，请查看服务日志并重试。"
            )
            run.finished_at = now()
            if record.version == run.version and not record.archived:
                record.status = status
        if not isinstance(exc, AppError):
            logger.exception("处理任务异常 run_id=%s", run_id)
            raise
        logger.warning("任务中止 run_id=%s status=%s code=%s", run_id, status, exc.code)
