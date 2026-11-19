"""回顾与解释反馈是用户数据，绝不写回部署配置或个人笔记。"""

from datetime import timedelta

from fastapi import APIRouter
from sqlalchemy import select

from wiki_app import db
from wiki_app.config import get_settings
from wiki_app.errors import AppError
from wiki_app.models import CaptureInsight, Record, ResourceReviewState, ReviewPreference, now
from wiki_app.modules.graph.service import valid_evidence
from wiki_app.modules.review.service import daily_cards, feedback, preferences
from wiki_app.schemas import InsightFeedback, ReviewFeedback, ReviewPreferenceEdit

router = APIRouter(tags=["发现与回顾"])


@router.get("/reviews")
def reviews():
    with db.SessionLocal() as session:
        return daily_cards(session)


@router.patch("/reviews/preferences")
def update_preferences(data: ReviewPreferenceEdit):
    with db.transaction() as session:
        # 单例行仍用 UPSERT，避免两个设置页面同时首次保存时发生主键冲突。
        from wiki_app.modules.capture.service import insert_once

        insert_once(session, ReviewPreference, {"id": "personal", "enabled": True}, ["id"])
        preference = session.get(ReviewPreference, "personal")
        if data.enabled is not None:
            preference.enabled = data.enabled
        if data.pause and data.resume:
            raise AppError("不能同时暂停和恢复回顾。")
        if data.pause:
            preference.paused_until = now() + timedelta(days=get_settings().review_pause_days)
        if data.resume:
            preference.paused_until = None
        session.flush()
        return preferences(session)


@router.post("/reviews/{card_id}/feedback")
def review_feedback(card_id: str, data: ReviewFeedback):
    with db.transaction() as session:
        return feedback(session, card_id, data.action)


@router.get("/reviews/blocked")
def blocked_resources():
    from wiki_app.models import Resource

    with db.SessionLocal() as session:
        return {"items": [{"resource_id": resource.id, "label": resource.label}
                           for (resource,) in session.execute(select(Resource).join(ResourceReviewState).where(
                               ResourceReviewState.blocked.is_(True)).order_by(Resource.label)
                               .limit(get_settings().graph_max_nodes))]}


@router.post("/reviews/resources/{resource_id}/restore")
def restore_resource(resource_id: str):
    with db.transaction() as session:
        state = session.get(ResourceReviewState, resource_id)
        if not state:
            raise AppError("没有找到该资源的回顾偏好。", "not_found", 404)
        state.blocked, state.paused_until = False, None
        return {"resource_id": resource_id, "blocked": False}


@router.patch("/insights/{insight_id}")
def insight_feedback(insight_id: str, data: InsightFeedback):
    with db.transaction() as session:
        insight = session.get(CaptureInsight, insight_id)
        if not insight:
            raise AppError("关联提示不存在。", "not_found", 404)
        record = session.get(Record, insight.record_id)
        if data.feedback == "accepted" and (record.archived or record.version != insight.version
                or not valid_evidence(session, insight.evidence)):
            raise AppError("提示来源已经更新，请重新核对。", "version_conflict", 409)
        insight.feedback = data.feedback
        return {"id": insight.id, "feedback": insight.feedback}


@router.post("/insights/{insight_id}/retry")
def retry_insight(insight_id: str):
    with db.transaction() as session:
        insight = session.get(CaptureInsight, insight_id)
        if not insight:
            raise AppError("关联提示不存在。", "not_found", 404)
        record = session.get(Record, insight.record_id)
        if record.archived or record.version != insight.version:
            raise AppError("来源已更新或归档。", "version_conflict", 409)
        if insight.status in ("waiting_configuration", "budget_exceeded", "failed"):
            insight.status, insight.error = "pending", None
        return {"id": insight.id, "status": insight.status}
