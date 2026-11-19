"""可选深层解释复用原整理任务的预算记录，不阻塞资料保存和 Wiki。"""

from sqlalchemy import select

from wiki_app import db
from wiki_app.config import get_settings
from wiki_app.errors import AppError, BudgetExceeded, ConfigurationRequired
from wiki_app.integrations import llm
from wiki_app.models import CaptureInsight, ProcessingRun, Record, Source
from wiki_app.modules.capture.service import history_matches
from wiki_app.modules.graph.service import valid_evidence
from wiki_app.schemas import InsightDraft


def insight_context(session, record, run):
    matches = history_matches(session, record)
    ids = list(dict.fromkeys(item["record_id"] for item in matches if not item["archived"]))
    if not ids:
        return None
    # 只分析两次收藏的明确文字/备注，不凭视频内容推断个人情感。
    query = select(Source).join(Record).where(
        Source.version == Record.version,
        Record.archived.is_(False), Source.method.in_(("user_text", "user_note"))
    ).order_by((Source.method == "user_note").desc(), Record.created_at.desc(), Source.id)
    # 为新旧两次表达各保留额度，避免当前长文分段挤掉历史备注。
    limit = max(1, get_settings().search_candidates // 2)
    sources = [*session.scalars(query.where(Source.record_id == record.id).limit(limit)),
               *session.scalars(query.where(Source.record_id.in_(ids)).limit(limit))]
    if record.id not in {source.record_id for source in sources} or not any(
        source.record_id != record.id for source in sources
    ):
        return None
    return {"current_record_id": record.id, "events": [
        {"id": item.id, "created_at": item.created_at.isoformat()}
        for item in session.scalars(select(Record).where(Record.id.in_([record.id, *ids])))
    ], "sources": [{"id": source.id, "record_id": source.record_id, "text": source.text}
                   for source in sources]}, {source.id: source for source in sources}


def prepare_insight(run_id):
    if not get_settings().review_insights_enabled:
        return
    with db.transaction() as session:
        run = session.get(ProcessingRun, run_id)
        record = session.get(Record, run.record_id) if run else None
        if not record or record.archived or record.version != run.version or run.status != "completed":
            return
        if session.scalar(select(CaptureInsight.id).where(CaptureInsight.run_id == run_id)):
            return
        ignored = session.scalar(select(CaptureInsight.id).where(
            CaptureInsight.record_id == record.id, CaptureInsight.feedback == "ignored").limit(1))
        status = "pending" if not ignored and insight_context(session, record, run) else "insufficient_evidence"
        session.add(CaptureInsight(run_id=run_id, record_id=record.id, version=record.version,
                                   status=status, feedback="ignored" if ignored else "suggested"))


def generate_insight(insight_id):
    """每条解释至少引用两次收藏的个人表达；证据不足时保持事实提示。"""
    with db.SessionLocal() as session:
        insight = session.get(CaptureInsight, insight_id)
        if not insight or insight.status not in ("pending", "failed"):
            return
        record, run = session.get(Record, insight.record_id), session.get(ProcessingRun, insight.run_id)
        context = None if record.archived or record.version != insight.version else insight_context(session, record, run)
    if not context:
        with db.transaction() as session:
            session.get(CaptureInsight, insight_id).status = "insufficient_evidence"
        return
    payload, sources = context
    try:
        draft = llm.complete(
            insight.run_id, "revisit",
            "用户在不同时间再次收藏了相同内容。比较两次明确文字/备注，可提出有依据的关注点变化。"
            "必须标明可能性，不断言喜欢程度、情感或人格，不把视频观点当作用户观点。"
            "每个解释至少给出当前和以前两次收藏的逐字个人表达。没有足够依据时返回空 explanation 和 evidence。",
            payload, InsightDraft,
        )
        if not draft.explanation:
            status = "insufficient_evidence"
        else:
            evidence = [item.model_dump() for item in draft.evidence]
            if any(item["source_id"] not in sources for item in evidence):
                raise AppError("解释引用了未提供的个人表达。", "invalid_evidence", 502)
            event_ids = {sources[item["source_id"]].record_id for item in evidence}
            with db.SessionLocal() as session:
                if record.id not in event_ids or len(event_ids) < 2 or not valid_evidence(session, evidence):
                    raise AppError("再次关注解释的证据不足或已过期。", "invalid_evidence", 502)
            status = "ready"
        with db.transaction() as session:
            stored = session.get(CaptureInsight, insight_id)
            current = session.get(Record, stored.record_id)
            if current.version != stored.version or current.archived:
                stored.status = "stale"
                return
            stored.status, stored.error = status, None
            stored.explanation = draft.explanation if status == "ready" else ""
            stored.evidence = [item.model_dump() for item in draft.evidence] if status == "ready" else []
    except Exception as exc:
        if isinstance(exc, AppError) and exc.code == "invalid_evidence":
            llm.invalidate_result(insight.run_id, "revisit")
        with db.transaction() as session:
            stored = session.get(CaptureInsight, insight_id)
            stored.status = "waiting_configuration" if isinstance(exc, ConfigurationRequired) else (
                "budget_exceeded" if isinstance(exc, BudgetExceeded) else "failed")
            stored.error = exc.message if isinstance(exc, AppError) else "解释暂时不可用，原始收藏不受影响。"


def context_suggestions(session, record):
    from wiki_app.models import Relation

    result = []
    relations = session.scalars(select(Relation).where(
        (Relation.record_id == record.id) | (Relation.target_id == record.id),
        Relation.feedback != "ignored").order_by(Relation.created_at.desc()).limit(get_settings().graph_max_edges))
    for relation in relations:
        if not valid_evidence(session, relation.evidence):
            continue
        target_id = relation.target_id if relation.record_id == record.id else relation.record_id
        other = session.get(Record, target_id)
        if other and not other.archived and other.created_at <= record.created_at:
            result.append({"record_id": other.id, "title": other.title, "relation_id": relation.id,
                           "explanation": relation.explanation, "evidence": relation.evidence,
                           "feedback": relation.feedback})
        if len(result) >= get_settings().review_context_limit:
            break
    return result
