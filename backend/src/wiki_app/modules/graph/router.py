"""图谱查询和建议反馈；鉴权由公共 API 路由统一应用。"""

from datetime import datetime, timezone

from fastapi import APIRouter, Query

from wiki_app import db
from wiki_app.errors import AppError
from wiki_app.models import RecordTopic
from wiki_app.modules.graph.service import graph_data, node_detail, valid_evidence
from wiki_app.schemas import InsightFeedback

router = APIRouter(tags=["知识图谱"])


@router.get("/graph")
def graph(focus: str | None = None, q: str = Query(default="", max_length=200),
          hops: int | None = None, kinds: str = "", since: datetime | None = None,
          until: datetime | None = None, include_archived: bool = False):
    # 无时区的 API 日期按 UTC 处理，避免混合日期输入比较时抛出内部错误。
    since = since.replace(tzinfo=timezone.utc) if since and since.tzinfo is None else since
    until = until.replace(tzinfo=timezone.utc) if until and until.tzinfo is None else until
    if since and until and since > until:
        raise AppError("开始时间不能晚于结束时间。")
    with db.SessionLocal() as session:
        return graph_data(session, focus=focus, q=q, hops=hops,
                          kinds=kinds.split(",") if kinds else None,
                          since=since, until=until, include_archived=include_archived)


@router.get("/graph/nodes/{kind}/{identifier}")
def detail(kind: str, identifier: str, include_archived: bool = False):
    with db.SessionLocal() as session:
        return node_detail(session, kind + ":" + identifier, include_archived)


@router.patch("/graph/topics/{link_id}")
def topic_feedback(link_id: str, data: InsightFeedback):
    from wiki_app.models import Record

    with db.transaction() as session:
        link = session.get(RecordTopic, link_id)
        if not link:
            raise AppError("主题关系不存在。", "not_found", 404)
        record = session.get(Record, link.record_id)
        if record.version != link.version or record.archived:
            raise AppError("关系来源已经更新，请刷新后核对。", "version_conflict", 409)
        if link.source_ids and not valid_evidence(session, [{"source_id": sid} for sid in link.source_ids]):
            raise AppError("关系证据已经失效。", "version_conflict", 409)
        link.feedback = data.feedback
        return {"id": link.id, "feedback": link.feedback}
