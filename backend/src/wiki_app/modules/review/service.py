"""每日回顾只使用已有事实与证据，不额外调用 LLM；时间由调用方传入以便验收。"""

from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import case, distinct, func, or_, select, text
from sqlalchemy.orm import aliased

from wiki_app import db
from wiki_app.config import get_settings
from wiki_app.errors import AppError
from wiki_app.models import (
    CaptureInput,
    Record,
    Relation,
    Resource,
    ResourceReviewState,
    ReviewBatch,
    ReviewCard,
    ReviewPreference,
    now,
)
from wiki_app.modules.capture.service import insert_once
from wiki_app.modules.graph.service import valid_evidence

REVIEW_LOCK = 406011


def utc(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def preferences(session):
    preference = session.get(ReviewPreference, "personal")
    return {"enabled": preference.enabled if preference else True,
            "paused_until": utc(preference.paused_until) if preference else None}


def review_active(session, clock):
    if not get_settings().review_enabled:
        return False
    preference = preferences(session)
    return preference["enabled"] and not (
        preference["paused_until"] and utc(preference["paused_until"]) > clock)


def eligible_resources(session, clock):
    settings = get_settings()
    excluded = select(ResourceReviewState.resource_id).where(or_(
        ResourceReviewState.blocked.is_(True), ResourceReviewState.paused_until > clock,
        ResourceReviewState.last_shown_at > clock - timedelta(days=settings.review_cooldown_days)))
    return select(Resource.id, func.min(Record.created_at).label("first_saved"),
                  func.max(Record.created_at).label("last_saved"),
                  func.count(distinct(Record.id)).label("captures")).join(
        CaptureInput, CaptureInput.resource_id == Resource.id).join(Record).where(
        CaptureInput.version == Record.version, CaptureInput.included.is_(True),
        CaptureInput.origin != "notes", Record.archived.is_(False),
        Resource.id.not_in(excluded)).group_by(Resource.id).having(
        func.min(Record.created_at) <= clock - timedelta(days=settings.review_min_age_days))


def representative(session, resource_id):
    return session.scalar(select(Record).join(CaptureInput).where(
        CaptureInput.resource_id == resource_id, CaptureInput.version == Record.version,
        CaptureInput.included.is_(True), CaptureInput.origin != "notes", Record.archived.is_(False)
    ).order_by(Record.created_at.desc(), Record.id).limit(1))


def generate_daily(clock=None):
    """事务内生成整批；唯一日期和 PG 事务锁保证多 Worker 不重复补发。"""
    clock = utc(clock or now())
    settings = get_settings()
    local = clock.astimezone(ZoneInfo(settings.review_timezone))
    if local.strftime("%H:%M") < settings.review_daily_time:
        return None
    day = local.date().isoformat()
    with db.transaction() as session:
        if not review_active(session, clock):
            return None
        if session.get_bind().dialect.name == "postgresql":
            if not session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": REVIEW_LOCK}):
                return None
        batch = session.scalar(select(ReviewBatch).where(ReviewBatch.local_date == day,
                                                       ReviewBatch.timezone == settings.review_timezone))
        if batch:
            return batch.id
        identifier = str(uuid5(NAMESPACE_URL, "wiki-review:" + settings.review_timezone + ":" + day))
        claimed = insert_once(session, ReviewBatch, {"id": identifier, "local_date": day,
                              "timezone": settings.review_timezone, "created_at": clock}, ["local_date", "timezone"])
        if claimed == 0:
            return identifier
        base = eligible_resources(session, clock)
        eligible = base.subquery()
        recent = clock - timedelta(days=settings.review_recent_days)
        candidates, seen = [], set()

        def add(resource_id, kind, reason, evidence):
            if resource_id not in seen:
                seen.add(resource_id)
                candidates.append((resource_id, kind, reason, evidence))

        # 第一类：近期新资料与旧内容的现有有效关系。
        newer = aliased(Record)
        other_id = case((Relation.record_id == Record.id, Relation.target_id), else_=Relation.record_id)
        query = select(CaptureInput.resource_id, Relation).select_from(Record).join(
            CaptureInput, CaptureInput.record_id == Record.id).join(eligible,
            eligible.c.id == CaptureInput.resource_id).join(Relation,
            or_(Relation.record_id == Record.id, Relation.target_id == Record.id)).join(
            newer, newer.id == other_id).where(
            CaptureInput.version == Record.version, Record.archived.is_(False), newer.archived.is_(False),
            newer.created_at >= recent, Relation.feedback != "ignored"
        ).order_by(Relation.created_at.desc(), Relation.id).limit(settings.graph_max_edges)
        for resource_id, relation in session.execute(query):
            inaccurate = session.scalar(select(ReviewCard.id).where(
                ReviewCard.feedback == "inaccurate", ReviewCard.evidence["relation_id"].as_string() == relation.id).limit(1))
            if not inaccurate and valid_evidence(session, relation.evidence):
                add(resource_id, "related", "近期新资料与你以前收藏的内容存在联系：" + relation.explanation,
                    {"relation_id": relation.id, "sources": relation.evidence})
            if len(candidates) >= settings.review_daily_limit:
                break
        # 第二类：近期再次收藏。按首次时间判断“旧内容”，次数不当成证据强度。
        for row in session.execute(base.having(func.count(distinct(Record.id)) > 1,
                                   func.max(Record.created_at) >= recent).order_by(
                                   func.max(Record.created_at).desc(), Resource.id).limit(settings.graph_max_nodes)):
            add(row.id, "revisit", f"这份内容你主动收藏过 {row.captures} 次，首次收藏于 {row.first_saved.date().isoformat()}。",
                {"captures": row.captures, "first_saved": row.first_saved.isoformat()})
        # 第三类：长期未回顾。排序确定，重启不会重新抽签或堆积历史批次。
        for row in session.execute(base.outerjoin(ResourceReviewState, ResourceReviewState.resource_id == Resource.id).order_by(
            func.min(ResourceReviewState.last_shown_at).asc().nulls_first(), func.min(Record.created_at), Resource.id
        ).limit(settings.graph_max_nodes)):
            add(row.id, "rediscover", f"首次收藏于 {row.first_saved.date().isoformat()}，可以重新看看它现在是否仍有用。",
                {"first_saved": row.first_saved.isoformat()})
        for resource_id, kind, reason, evidence in candidates[:settings.review_daily_limit]:
            record = representative(session, resource_id)
            if record:
                session.add(ReviewCard(batch_id=identifier, resource_id=resource_id, record_id=record.id,
                                       kind=kind, reason=reason, evidence=evidence, created_at=clock))
        return identifier


def daily_cards(session, clock=None):
    clock = utc(clock or now())
    settings = get_settings()
    preference = preferences(session)
    meta = {"preferences": preference, "timezone": settings.review_timezone,
            "daily_time": settings.review_daily_time, "daily_limit": settings.review_daily_limit,
            "pause_days": settings.review_pause_days,
            "configured_enabled": settings.review_enabled}
    if not review_active(session, clock):
        return {**meta, "items": []}
    day = clock.astimezone(ZoneInfo(settings.review_timezone)).date().isoformat()
    query = select(ReviewCard).join(ReviewBatch).where(
        ReviewBatch.local_date == day, ReviewBatch.timezone == settings.review_timezone,
        ReviewCard.feedback.in_(("pending", "helpful"))).order_by(ReviewCard.created_at, ReviewCard.id)
    items = []
    for card in session.scalars(query.limit(settings.review_daily_limit)):
        state = session.get(ResourceReviewState, card.resource_id)
        if state and (state.blocked or state.paused_until and utc(state.paused_until) > clock):
            continue
        if card.evidence.get("sources") and not valid_evidence(session, card.evidence["sources"]):
            continue
        if card.evidence.get("relation_id"):
            relation = session.get(Relation, card.evidence["relation_id"])
            if not relation or relation.feedback == "ignored":
                continue
        record = representative(session, card.resource_id)
        if record:
            items.append({"id": card.id, "resource_id": card.resource_id, "record_id": record.id,
                          "title": record.title, "kind": card.kind, "reason": card.reason,
                          "evidence": card.evidence, "feedback": card.feedback, "shown_at": card.shown_at})
    return {**meta, "items": items}


def feedback(session, card_id, action, clock=None):
    clock = utc(clock or now())
    card = session.get(ReviewCard, card_id)
    if not card:
        raise AppError("回顾卡片不存在。", "not_found", 404)
    insert_once(session, ResourceReviewState, {"resource_id": card.resource_id, "blocked": False}, ["resource_id"])
    state = session.get(ResourceReviewState, card.resource_id)
    if not card.shown_at:
        card.shown_at = clock
    state.last_shown_at = clock
    if action != "seen":
        card.feedback = action
    if action == "snooze":
        state.paused_until = clock + timedelta(days=get_settings().review_pause_days)
    elif action == "never":
        state.blocked = True
    return {"id": card.id, "feedback": card.feedback}
