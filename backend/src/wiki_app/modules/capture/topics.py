"""主题只做名称规范化；资源主题必须来自该来源的证据，避免混合素材互相污染。"""

from collections import defaultdict
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from wiki_app.errors import AppError
from wiki_app.models import CaptureInput, RecordTopic, Source, Topic
from wiki_app.modules.capture.service import insert_once
from wiki_app.modules.knowledge.service import title_key


def validate_topic_evidence(draft, provided: set[str]):
    names = {title_key(name) for name in draft.topics}
    for item in draft.topic_evidence:
        if title_key(item.topic) not in names or any(sid not in provided for sid in item.source_ids):
            raise AppError("主题引用了未提供的来源，结果未保存。", "invalid_evidence", 502)


def sync_topics(session, analysis):
    """旧分析没有来源映射时只建记录主题；同版本更新保留人工反馈。"""
    evidence = {title_key(item["topic"]): item["source_ids"] for item in analysis.topic_evidence}
    for name in analysis.topics:
        key = title_key(name)
        identifier = str(uuid5(NAMESPACE_URL, "wiki-topic:" + key))
        insert_once(session, Topic, {"id": identifier, "name": name[:100], "name_key": key}, ["name_key"])
        topic = session.scalar(select(Topic).where(Topic.name_key == key))
        source_ids = evidence.get(key, [])
        grouped = defaultdict(list)
        for source in session.scalars(select(Source).where(
            Source.id.in_(source_ids), Source.record_id == analysis.record_id,
            Source.version == analysis.version
        )):
            if source.input_id:
                grouped[source.input_id].append(source.id)
        scopes = {"record": source_ids, **grouped}
        for scope, ids in scopes.items():
            row = session.scalar(select(RecordTopic).where(
                RecordTopic.record_id == analysis.record_id, RecordTopic.version == analysis.version,
                RecordTopic.topic_id == topic.id, RecordTopic.scope == scope))
            if row:
                row.source_ids = ids
            else:
                prior = select(RecordTopic).where(RecordTopic.record_id == analysis.record_id,
                    RecordTopic.topic_id == topic.id, RecordTopic.feedback != "suggested")
                if scope == "record":
                    prior = prior.where(RecordTopic.scope == "record")
                else:
                    item = session.get(CaptureInput, scope)
                    prior = prior.join(CaptureInput, CaptureInput.id == RecordTopic.scope).where(
                        CaptureInput.resource_id == item.resource_id)
                previous = session.scalar(prior.order_by(RecordTopic.version.desc()).limit(1))
                session.add(RecordTopic(record_id=analysis.record_id, version=analysis.version,
                                        topic_id=topic.id, scope=scope, source_ids=ids,
                                        feedback=previous.feedback if previous else "suggested"))
