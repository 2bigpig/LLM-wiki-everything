"""按邻居展开查询图谱；容量在服务端限制，证据失效不会成为新关系依据。"""

from collections import deque
from datetime import datetime

from sqlalchemy import cast, func, or_, select, true
from sqlalchemy.dialects.postgresql import JSONB

from wiki_app.config import get_settings
from wiki_app.errors import AppError
from wiki_app.models import (
    CaptureInput,
    Record,
    RecordTopic,
    Relation,
    Resource,
    Source,
    Topic,
    WikiPage,
)
from wiki_app.modules.capture.rules import digest
from wiki_app.modules.knowledge.service import contains_quote
from wiki_app.presentation import record_out

NODE_MODELS = {"record": Record, "resource": Resource, "topic": Topic, "wiki": WikiPage}


def valid_evidence(session, evidence: list[dict]) -> bool:
    """建议必须仍指向当前、未归档来源，并且逐字引用可以核对。"""
    if not evidence:
        return False
    for item in evidence:
        source = session.get(Source, item.get("source_id"))
        record = session.get(Record, source.record_id) if source else None
        if not record or record.archived or source.version != record.version:
            return False
        if item.get("quote") and not contains_quote(source.text, item["quote"]):
            return False
    return True


def citation_rows(session, *, record_ids=None, wiki_id=None, limit=None):
    """在数据库内展开 Wiki 引用 JSON，避免为了查邻居读取全部页面。"""
    settings = get_settings()
    if session.get_bind().dialect.name == "postgresql":
        values = func.jsonb_array_elements_text(cast(WikiPage.source_ids, JSONB)).table_valued("value")
    else:
        values = func.json_each(WikiPage.source_ids).table_valued("value")
    query = select(WikiPage, Source).select_from(WikiPage).join(values, true()).join(
        Source, Source.id == values.c.value)
    if record_ids is not None:
        query = query.where(Source.record_id.in_(record_ids))
    if wiki_id:
        query = query.where(WikiPage.id == wiki_id)
    return list(session.execute(query.order_by(WikiPage.updated_at.desc(), Source.id).limit(
        limit or settings.graph_max_edges + 1)))


def active_record_filters(include_archived=False, since=None, until=None):
    filters = [] if include_archived else [Record.archived.is_(False)]
    if since:
        filters.append(Record.created_at >= since)
    if until:
        filters.append(Record.created_at <= until)
    return filters


def node_data(session, node_id: str, include_archived=False, *, since=None, until=None):
    try:
        kind, identifier = node_id.split(":", 1)
        model = NODE_MODELS[kind]
    except (ValueError, KeyError):
        raise AppError("图谱节点类型无效。") from None
    item = session.get(model, identifier)
    if not item or kind == "record" and item.archived and not include_archived:
        return None
    if kind == "resource":
        referenced = session.scalar(select(CaptureInput.id).join(Record).where(
            CaptureInput.resource_id == identifier, CaptureInput.version == Record.version,
            CaptureInput.included.is_(True), CaptureInput.origin != "notes",
            *active_record_filters(include_archived, since, until)).limit(1))
        if not referenced:
            return None
    if kind == "topic":
        referenced = session.scalar(select(RecordTopic.id).join(Record).where(
            RecordTopic.topic_id == identifier, RecordTopic.version == Record.version,
            RecordTopic.feedback != "ignored", *active_record_filters(include_archived, since, until)).limit(1))
        if not referenced:
            return None
    label = item.title if kind in ("record", "wiki") else item.label if kind == "resource" else item.name
    href = f"/records/{identifier}" if kind == "record" else f"/wiki/{identifier}" if kind == "wiki" else ""
    return {"id": node_id, "kind": kind, "label": label, "href": href}


def graph_data(session, *, focus=None, q="", hops=None, kinds=None,
               since: datetime | None = None, until: datetime | None = None, include_archived=False):
    settings = get_settings()
    hops = settings.graph_default_hops if hops is None else hops
    if not 1 <= hops <= settings.graph_max_hops:
        raise AppError("图谱展开层数超过配置范围。")
    allowed = set(kinds or NODE_MODELS)
    if not allowed <= set(NODE_MODELS):
        raise AppError("图谱节点筛选类型无效。")
    filters = active_record_filters(include_archived, since, until)
    cap, edge_cap = settings.graph_max_nodes, settings.graph_max_edges
    truncated = False
    nodes, edges, visited = {}, {}, set()

    def record_allowed(record):
        if not record or record.archived and not include_archived:
            return False
        # 日期比较交给同一 SQL 过滤，兼容 SQLite 测试的无时区 datetime。
        return session.scalar(select(Record.id).where(Record.id == record.id, *filters)) is not None

    def edge(source, target, kind, label, *, reference=None, evidence=None, feedback="fact", explanation=""):
        key_reference = reference if kind in ("relation", "topic") else ""
        return {"id": digest(f"{source}:{target}:{kind}:{key_reference or ''}"), "source": source,
                "target": target, "kind": kind, "label": label, "reference_id": reference,
                "evidence": evidence or [], "feedback": feedback, "explanation": explanation}

    def rows(query, maximum=cap):
        nonlocal truncated
        values = list(session.execute(query.limit(maximum + 1)))
        if len(values) > maximum:
            truncated = True
        return values[:maximum]

    def topic_edge(link, record_id, source_node):
        evidence = [{"source_id": sid} for sid in link.source_ids]
        if link.scope != "record" and not valid_evidence(session, evidence):
            return None
        if link.feedback == "ignored":
            return None
        return edge(source_node, "topic:" + link.topic_id, "topic", "主题归属",
                    reference=link.id, evidence=evidence, feedback=link.feedback,
                    explanation="来自该收藏的主题整理；资源主题仅使用该来源的支持片段。")

    def neighbors(node_id):
        kind, identifier = node_id.split(":", 1)
        output = []
        if kind == "record":
            record = session.get(Record, identifier)
            if not record_allowed(record):
                return []
            for (item,) in rows(select(CaptureInput).where(
                CaptureInput.record_id == identifier, CaptureInput.version == record.version,
                CaptureInput.included.is_(True), CaptureInput.origin != "notes").order_by(CaptureInput.position)):
                output.append(edge(node_id, "resource:" + item.resource_id, "capture", "收藏来源",
                                   reference=item.id, explanation="系统事实：这次收藏包含该内容资源。"))
            for (link,) in rows(select(RecordTopic).where(
                RecordTopic.record_id == identifier, RecordTopic.version == record.version,
                RecordTopic.scope == "record")):
                value = topic_edge(link, identifier, node_id)
                if value:
                    output.append(value)
            for page, source in citation_rows(session, record_ids=[identifier]):
                if source.version == record.version:
                    output.append(edge(node_id, "wiki:" + page.id, "citation", "Wiki 引用",
                                       evidence=[{"source_id": source.id}],
                                       explanation="系统事实：Wiki 引用了这次收藏的当前来源。"))
            for (relation,) in rows(select(Relation).where(
                or_(Relation.record_id == identifier, Relation.target_id == identifier),
                Relation.feedback != "ignored").order_by(Relation.created_at.desc()), edge_cap):
                other = relation.target_id if relation.record_id == identifier else relation.record_id
                if record_allowed(session.get(Record, other)) and valid_evidence(session, relation.evidence):
                    output.append(edge("record:" + relation.record_id, "record:" + relation.target_id,
                                       "relation", relation.kind, reference=relation.id,
                                       evidence=relation.evidence, feedback=relation.feedback,
                                       explanation=relation.explanation))
        elif kind == "resource":
            capture_ids = select(CaptureInput.record_id).join(Record).where(
                CaptureInput.resource_id == identifier, CaptureInput.version == Record.version,
                CaptureInput.included.is_(True), CaptureInput.origin != "notes", *filters
            )
            # 用主键子查询去重，PG 的 JSON 列不支持 SELECT DISTINCT 整行。
            captures = rows(select(Record).where(Record.id.in_(capture_ids)).order_by(Record.created_at, Record.id))
            previous = None
            for (record,) in captures:
                target = "record:" + record.id
                output.append(edge(target, node_id, "capture", "收藏来源"))
                if previous:
                    output.append(edge("record:" + previous.id, target, "recapture", "再次收藏",
                                       explanation="系统事实：不同时间主动收藏了同一内容；不代表已确定个人动机。"))
                previous = record
            for (link,) in rows(select(RecordTopic).join(CaptureInput, CaptureInput.id == RecordTopic.scope).join(
                Record, Record.id == RecordTopic.record_id).where(
                CaptureInput.resource_id == identifier, RecordTopic.version == Record.version, *filters)):
                value = topic_edge(link, link.record_id, node_id)
                if value:
                    output.append(value)
        elif kind == "topic":
            for (link,) in rows(select(RecordTopic).join(Record).where(
                RecordTopic.topic_id == identifier, RecordTopic.version == Record.version, *filters)):
                source_node = "record:" + link.record_id
                if link.scope != "record":
                    item = session.get(CaptureInput, link.scope)
                    if not item:
                        continue
                    source_node = "resource:" + item.resource_id
                value = topic_edge(link, link.record_id, source_node)
                if value:
                    output.append(value)
        else:
            for _, source in citation_rows(session, wiki_id=identifier):
                record = session.get(Record, source.record_id)
                if record_allowed(record) and source.version == record.version:
                    output.append(edge("record:" + record.id, node_id, "citation", "Wiki 引用",
                                       evidence=[{"source_id": source.id}]))
        return output

    if focus:
        if not node_data(session, focus, include_archived, since=since, until=until):
            raise AppError("图谱节点不存在或已归档。", "not_found", 404)
        seeds = [focus]
    elif q.strip():
        seeds = []
        per_kind = max(1, cap // 8)
        for kind, model in NODE_MODELS.items():
            column = model.title if kind in ("record", "wiki") else model.label if kind == "resource" else model.name
            query = select(model.id).where(column.contains(q.strip(), autoescape=True))
            if kind == "record":
                query = query.where(*filters)
            seeds.extend(kind + ":" + identifier for (identifier,) in rows(query.order_by(model.id), per_kind))
    else:
        seeds = ["record:" + identifier for (identifier,) in rows(select(Record.id).where(*filters).order_by(
            Record.created_at.desc(), Record.id).limit(cap), max(1, cap // 8))]
    queue = deque((seed, 0) for seed in seeds)
    pending_edges = {}
    while queue:
        if len(visited) >= cap * (hops + 1):
            truncated = True
            break
        node_id, depth = queue.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)
        data = node_data(session, node_id, include_archived, since=since, until=until)
        if not data:
            continue
        if data["kind"] == "record" and not record_allowed(session.get(Record, node_id.split(":", 1)[1])):
            continue
        if data["kind"] in allowed:
            if len(nodes) >= cap:
                truncated = True
                continue
            nodes[node_id] = data
        if depth >= hops:
            continue
        for value in neighbors(node_id):
            if len(pending_edges) >= edge_cap:
                truncated = True
                break
            pending_edges[value["id"]] = value
            for endpoint in (value["source"], value["target"]):
                if endpoint != node_id:
                    queue.append((endpoint, depth + 1))
    for key, value in pending_edges.items():
        if value["source"] in nodes and value["target"] in nodes:
            edges[key] = value
    # 批量补充证据版本供详情直接核对；不把整份原文加载进图谱响应。
    source_ids = {item["source_id"] for value in edges.values() for item in value["evidence"]}
    sources = {source.id: source for source in session.scalars(select(Source).where(Source.id.in_(source_ids)))}
    for value in edges.values():
        value["evidence"] = [{**item, "version": sources[item["source_id"]].version,
                              "record_id": sources[item["source_id"]].record_id}
                             for item in value["evidence"] if item["source_id"] in sources]
    return {"nodes": list(nodes.values()), "edges": list(edges.values()), "truncated": truncated,
            "limits": {"nodes": cap, "edges": edge_cap, "max_hops": settings.graph_max_hops, "hops": hops}}


def node_detail(session, node_id: str, include_archived=False):
    node = node_data(session, node_id, include_archived)
    if not node:
        raise AppError("节点不存在或已归档。", "not_found", 404)
    kind, identifier = node_id.split(":", 1)
    cap = get_settings().graph_max_nodes
    detail = {"node": node, "timeline": [], "truncated": False}
    if kind == "resource":
        resource = session.get(Resource, identifier)
        capture_ids = select(CaptureInput.record_id).join(Record).where(
            CaptureInput.resource_id == identifier, CaptureInput.version == Record.version,
            CaptureInput.origin != "notes", CaptureInput.included.is_(True),
            *active_record_filters(include_archived))
        records = list(session.scalars(select(Record).where(Record.id.in_(capture_ids)).order_by(
            Record.created_at.desc(), Record.id).limit(cap + 1)))
        detail.update(url=resource.url, timeline=[record_out(record) for record in records[:cap]],
                      truncated=len(records) > cap)
    elif kind == "record":
        record = session.get(Record, identifier)
        detail["record"] = record_out(record)
    elif kind == "wiki":
        page = session.get(WikiPage, identifier)
        detail["content"] = page.generated
    return detail
