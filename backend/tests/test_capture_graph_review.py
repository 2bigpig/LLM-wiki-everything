"""统一录入与发现流程的业务验收；外部来源均为明确的离线替身。"""

from datetime import datetime, timedelta, timezone

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select
from test_workflow import fake_complete

from wiki_app import db
from wiki_app.errors import AppError, BudgetExceeded
from wiki_app.integrations import web
from wiki_app.models import (
    CaptureInput,
    CaptureInsight,
    Record,
    Relation,
    Resource,
    ResourceReviewState,
    ReviewBatch,
    ReviewCard,
    Source,
)
from wiki_app.modules.capture.rules import link_identity, recognize
from wiki_app.modules.capture.service import create_capture
from wiki_app.modules.processing import graph
from wiki_app.modules.review import insights
from wiki_app.modules.review.service import daily_cards, feedback, generate_daily
from wiki_app.modules.search.service import retrieve


def submit(client, text="Rerank 的使用心得。", **data):
    response = client.post("/api/captures", data={"text": text, **data})
    assert response.status_code == 201, response.text
    return response.json()


def execute(client, record_id):
    detail = client.get("/api/records/" + record_id).json()
    graph.execute_run(detail["runs"][0]["id"], InMemorySaver())
    return client.get("/api/records/" + record_id).json()


def test_recognize_is_local_and_platform_identity_is_stable(client, monkeypatch):
    monkeypatch.setattr(web, "public_address", lambda *a: pytest.fail("识别阶段不能联网"))
    preview = client.post("/api/captures/recognize", json={"text":
        "分享心得 https://youtu.be/abc?t=12， https://www.youtube.com/watch?v=abc&utm_source=share\nwww.example.com/a。"})
    assert preview.status_code == 200
    links = preview.json()["links"]
    assert len(links) == 2 and links[0]["kind"] == "video"
    assert preview.json()["body"] == "分享心得"
    assert link_identity("https://www.bilibili.com/video/BVabc?p=2")[0] != link_identity(
        "https://www.bilibili.com/video/BVabc?p=1")[0]
    assert not recognize("file:///etc/passwd")["links"]


def test_cross_year_capture_and_network_retry_are_different(client):
    first = submit(client, "https://youtu.be/abc", notes="去年用于准备面试。")
    with db.transaction() as session:
        session.get(Record, first["id"]).created_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    second = submit(client, "https://www.youtube.com/watch?v=abc", notes="今年用于项目实践。")
    assert first["id"] != second["id"]
    assert second["history_matches"][0]["record_id"] == first["id"]
    key = {"Idempotency-Key": "capture-retry"}
    a = client.post("/api/captures", data={"text": "https://youtu.be/abc"}, headers=key)
    b = client.post("/api/captures", data={"text": "https://youtu.be/abc"}, headers=key)
    assert a.json()["id"] == b.json()["id"]
    with db.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(Resource).where(Resource.kind == "url")) == 1


def test_mixed_upload_exclusion_and_raw_input(client, monkeypatch):
    raw = "  原文说明\nhttps://example.com/one\nhttps://example.com/two  "
    response = client.post("/api/captures", data={"text": raw,
        "excluded_urls": '["https://example.com/two"]'}, files=[
            ("files", ("first.txt", b"source one", "text/plain")),
            ("files", ("same.txt", b"source one", "text/plain")),
        ])
    assert response.status_code == 201, response.text
    detail = client.get("/api/records/" + response.json()["id"]).json()
    assert detail["content"] == raw and detail["kind"] == "mixed"
    assert len(detail["attachments"]) == 1
    assert len([item for item in detail["inputs"] if item["kind"] == "url"]) == 2
    urls = []
    monkeypatch.setattr(web, "read_webpage", lambda url: urls.append(url) or {
        "text": "Rerank 网页原文。", "url": url, "coverage": "只读取网页正文。"})
    detail = execute(client, detail["id"])
    assert urls == ["https://example.com/one"]
    assert any(item["status"] == "skipped" for item in detail["inputs"])


def test_partial_failure_retry_preserves_old_ids_and_successful_sources(client, monkeypatch):
    calls = []
    working = False

    def read(url):
        calls.append(url)
        if url.endswith("bad") and not working:
            raise AppError("示例网页暂时不可用。", "fetch_failed")
        return {"text": "Rerank 网页的真实原文。", "url": url, "coverage": "网页正文。"}

    monkeypatch.setattr(web, "read_webpage", read)
    monkeypatch.setattr(graph.llm, "complete", fake_complete)
    record = submit(client, "https://example.com/good\nhttps://example.com/bad")
    detail = execute(client, record["id"])
    assert detail["status"] == "completed" and detail["coverage"]["status"] == "partial"
    old_ids = [source["id"] for source in detail["sources"]]
    failed = next(item for item in detail["inputs"] if item["status"] == "failed")
    working = True
    response = client.post(f"/api/records/{record['id']}/inputs/{failed['id']}/retry")
    assert response.status_code == 200
    detail = execute(client, record["id"])
    assert detail["version"] == 2 and detail["status"] == "completed"
    assert calls.count("https://example.com/good") == 1
    assert calls.count("https://example.com/bad") == 2
    assert all(client.get("/api/sources/" + sid).json()["stale"] for sid in old_ids)
    assert client.get("/api/records").json()["total"] == 1


def test_all_urls_failed_does_not_analyze_url_string(client, monkeypatch):
    monkeypatch.setattr(web, "read_webpage", lambda url: (_ for _ in ()).throw(AppError("读取失败。")))
    monkeypatch.setattr(graph.llm, "complete", lambda *a: pytest.fail("URL 字符串不是已读网页"))
    record = submit(client, "https://example.com/no")
    detail = execute(client, record["id"])
    assert detail["status"] == "failed" and not detail["analyses"]
    assert not detail["sources"] and detail["inputs"][0]["status"] == "failed"


def test_pure_excluded_url_is_saved_without_fabricated_wiki(client, monkeypatch):
    monkeypatch.setattr(graph.llm, "complete", lambda *a: pytest.fail("仅保存原文不调用模型"))
    record = submit(client, "https://example.com/no", excluded_urls='["https://example.com/no"]')
    detail = execute(client, record["id"])
    assert detail["status"] == "completed" and detail["coverage"]["status"] == "saved_only"
    assert not detail["analyses"] and not detail["sources"]


def test_input_limits_empty_and_upload_cleanup(client, configure):
    assert client.post("/api/captures").status_code == 400
    configure(capture={"capture_max_links": 1, "capture_max_attachments": 1})
    response = client.post("/api/captures", data={"text": "https://example.com/a https://example.com/b"},
                           files={"files": ("file.txt", b"temporary", "text/plain")})
    assert response.status_code == 400
    from wiki_app.config import get_settings

    assert not list((get_settings().data_dir / "uploads").glob("*"))


def test_graph_all_four_types_bounded_and_evidence_checked(client, monkeypatch, configure):
    monkeypatch.setattr(graph.llm, "complete", fake_complete)
    record = submit(client)
    detail = execute(client, record["id"])
    result = client.get("/api/graph", params={"hops": 2})
    assert result.status_code == 200, result.text
    data = result.json()
    assert {node["kind"] for node in data["nodes"]} == {"record", "resource", "topic", "wiki"}
    assert any(edge["kind"] == "citation" for edge in data["edges"])
    topic = next(edge for edge in data["edges"] if edge["kind"] == "topic")
    assert client.patch("/api/graph/topics/" + topic["reference_id"], json={"feedback": "accepted"}).status_code == 200
    assert client.get("/api/graph", params={"hops": 4}).status_code == 400
    assert not client.get("/api/graph", params={"since": "2099-01-01T00:00:00Z"}).json()["nodes"]
    resource = next(item for item in detail["inputs"] if item["origin"] == "body")
    timeline = client.get("/api/graph/nodes/resource/" + resource["resource_id"]).json()
    assert timeline["timeline"][0]["id"] == record["id"]
    client.post("/api/records/" + record["id"] + "/archive")
    assert not client.get("/api/graph").json()["nodes"]
    assert client.get("/api/graph", params={"include_archived": True}).json()["nodes"]


def test_mixed_topics_do_not_spread_to_unsupported_resources(client, monkeypatch):
    def complete(run_id, stage, instruction, payload, schema):
        draft = fake_complete(run_id, stage, instruction, payload, schema)
        if stage.startswith("analyze:"):
            draft.topic_evidence = []
        return draft

    monkeypatch.setattr(graph.llm, "complete", complete)
    record = submit(client, "Rerank 用户原文。")
    execute(client, record["id"])
    result = client.get("/api/graph", params={"hops": 3}).json()
    nodes = {node["id"]: node for node in result["nodes"]}
    assert not any(edge["kind"] == "topic" and nodes[edge["source"]]["kind"] == "resource"
                   for edge in result["edges"])


def test_identical_web_evidence_not_counted_multiple_times(client, monkeypatch):
    monkeypatch.setattr(web, "read_webpage", lambda url: {"text": "Rerank shared source", "url": url, "coverage": ""})
    for _ in range(3):
        record = submit(client, "https://example.com/same")
        execute(client, record["id"])
    hits = retrieve("Rerank")
    assert len(hits) == 1
    with db.SessionLocal() as session:
        sources = list(session.scalars(select(Source)))
    payload = graph.source_payload(sources)
    assert len(payload) == 1 and len(payload[0]["equivalent_source_ids"]) == 2


def test_identical_pasted_text_is_one_evidence_with_separate_events(client, monkeypatch):
    monkeypatch.setattr(graph.llm, "complete", fake_complete)
    for _ in range(3):
        saved = submit(client, "Rerank 完全相同的粘贴内容。")
        execute(client, saved["id"])
    assert client.get("/api/records").json()["total"] == 3
    assert len(retrieve("Rerank")) == 1
    with db.SessionLocal() as session:
        assert len(graph.source_payload(list(session.scalars(select(Source))))) == 1


def seed_old_content(clock, *, repeated=False):
    with db.transaction() as session:
        first = create_capture(session, text="https://example.com/review")
        first.created_at = clock - timedelta(days=30)
        record_id = first.id
        resource_id = session.scalar(select(CaptureInput.resource_id).where(CaptureInput.record_id == first.id))
        if repeated:
            second = create_capture(session, text="https://example.com/review")
            second.created_at = clock - timedelta(days=1)
        return record_id, resource_id


def test_daily_clock_idempotence_feedback_and_cooldown(environment):
    clock = datetime(2026, 11, 19, 1, 5, tzinfo=timezone.utc)
    _, resource_id = seed_old_content(clock, repeated=True)
    assert generate_daily(clock.replace(hour=0)) is None
    batch = generate_daily(clock)
    assert generate_daily(clock + timedelta(hours=2)) == batch
    with db.transaction() as session:
        cards = daily_cards(session, clock)["items"]
        assert len(cards) == 1 and cards[0]["kind"] == "revisit"
        feedback(session, cards[0]["id"], "seen", clock)
        assert session.get(ResourceReviewState, resource_id).last_shown_at is not None
    generate_daily(clock + timedelta(days=1))
    with db.SessionLocal() as session:
        assert not daily_cards(session, clock + timedelta(days=1))["items"]
        assert session.scalar(select(func.count()).select_from(ReviewBatch)) == 2


def test_review_pause_archive_and_permanent_opt_out(client, monkeypatch):
    clock = datetime.now(timezone.utc).replace(hour=10)
    record_id, resource_id = seed_old_content(clock)
    assert client.patch("/api/reviews/preferences", json={"pause": True}).status_code == 200
    assert generate_daily(clock) is None
    client.patch("/api/reviews/preferences", json={"resume": True})
    generate_daily(clock)
    with db.transaction() as session:
        card = session.scalar(select(ReviewCard))
        feedback(session, card.id, "never", clock)
        assert session.get(ResourceReviewState, resource_id).blocked
    assert client.get("/api/reviews/blocked").json()["items"]
    client.post("/api/reviews/resources/" + resource_id + "/restore")
    client.post("/api/records/" + record_id + "/archive")
    with db.SessionLocal() as session:
        assert not daily_cards(session, clock)["items"]


def test_new_content_does_not_create_fake_daily_cards(environment):
    clock = datetime.now(timezone.utc).replace(hour=10)
    with db.transaction() as session:
        create_capture(session, text="刚保存的新想法。")
    generate_daily(clock)
    with db.SessionLocal() as session:
        assert not daily_cards(session, clock)["items"]


def test_review_and_graph_authentication(client, monkeypatch):
    from wiki_app.config import get_settings

    monkeypatch.setenv("API_TOKEN", "capture-access-only")
    get_settings.cache_clear()
    for endpoint in ("/api/graph", "/api/reviews"):
        assert client.get(endpoint).status_code == 401


def test_insight_needs_two_expressions_and_budget_failure_is_independent(client, monkeypatch):
    monkeypatch.setattr(web, "read_webpage", lambda url: {"text": "Rerank 视频参考原文。", "url": url, "coverage": ""})
    monkeypatch.setattr(graph.llm, "complete", fake_complete)
    first = submit(client, "去年为准备面试学习。 https://example.com/shared")
    execute(client, first["id"])
    second = submit(client, "今年想把方法用于项目。 https://example.com/shared")
    detail = execute(client, second["id"])
    with db.SessionLocal() as session:
        insight = session.scalar(select(CaptureInsight).where(CaptureInsight.record_id == second["id"]))
        assert insight.status == "pending"
        identifier = insight.id
    monkeypatch.setattr(insights.llm, "complete", lambda *a: (_ for _ in ()).throw(BudgetExceeded()))
    insights.generate_insight(identifier)
    detail = client.get("/api/records/" + second["id"]).json()
    assert detail["status"] == "completed" and detail["insights"][0]["status"] == "budget_exceeded"


def test_topic_support_and_feedback_survive_reread(client, monkeypatch):
    def complete(run_id, stage, instruction, payload, schema):
        result = fake_complete(run_id, stage, instruction, payload, schema)
        if stage.startswith("analyze"):
            from wiki_app.schemas import TopicEvidence

            result.topic_evidence = [TopicEvidence(topic="Rerank", source_ids=[payload["sources"][0]["id"]])]
        return result

    monkeypatch.setattr(graph.llm, "complete", complete)
    saved = submit(client)
    execute(client, saved["id"])
    data = client.get("/api/graph", params={"hops": 3}).json()
    nodes = {node["id"]: node for node in data["nodes"]}
    supported = [edge for edge in data["edges"] if edge["kind"] == "topic"
                 and nodes[edge["source"]]["kind"] == "resource"]
    assert supported
    client.patch("/api/graph/topics/" + supported[0]["reference_id"], json={"feedback": "accepted"})
    assert client.post("/api/records/" + saved["id"] + "/reread").status_code == 200
    execute(client, saved["id"])
    data = client.get("/api/graph", params={"hops": 3}).json()
    assert any(edge["kind"] == "topic" and edge["feedback"] == "accepted" for edge in data["edges"])
    from wiki_app.modules.capture.topics import validate_topic_evidence
    from wiki_app.schemas import AnalysisDraft

    bad = AnalysisDraft(kind="note", summary="主题", topics=["Rerank"],
                        points=[{"text": "内容", "source_ids": ["provided"]}],
                        topic_evidence=[{"topic": "Rerank", "source_ids": ["invented"]}])
    with pytest.raises(AppError, match="未提供"):
        validate_topic_evidence(bad, {"provided"})


@pytest.mark.parametrize("fabricate", [False, True])
def test_insight_requires_two_verifiable_personal_quotes(client, monkeypatch, fabricate):
    monkeypatch.setattr(web, "read_webpage", lambda url: {"text": "Rerank 视频观点", "url": url, "coverage": ""})
    monkeypatch.setattr(graph.llm, "complete", fake_complete)
    first = submit(client, "https://example.com/insight", notes="过去我准备面试。")
    execute(client, first["id"])
    second = submit(client, "https://example.com/insight", notes="现在我准备实践。")
    execute(client, second["id"])

    def explain(run_id, stage, instruction, payload, schema):
        assert stage == "revisit"
        return schema(explanation="关注点可能从面试转向实践。", evidence=[
            {"source_id": source["id"], "quote": "编造的不存在原文" if fabricate else source["text"]}
            for source in payload["sources"]])

    monkeypatch.setattr(insights.llm, "complete", explain)
    with db.SessionLocal() as session:
        identifier = session.scalar(select(CaptureInsight.id).where(CaptureInsight.record_id == second["id"]))
    insights.generate_insight(identifier)
    with db.SessionLocal() as session:
        result = session.get(CaptureInsight, identifier)
        assert result.status == ("failed" if fabricate else "ready")
        assert bool(result.evidence) is not fabricate
    if not fabricate:
        with db.transaction() as session:
            session.get(Record, first["id"]).version += 1
        detail = client.get("/api/records/" + second["id"]).json()
        assert detail["insights"][0]["stale"]


def test_repeated_video_without_personal_expression_does_not_explain(client, monkeypatch):
    from wiki_app.integrations import platform_video

    monkeypatch.setattr(platform_video, "read_platform_video", lambda *a: (
        [{"text": "Rerank 视频作者的看法", "start": 0, "end": 10}], None))
    monkeypatch.setattr(graph.llm, "complete", fake_complete)
    for _ in range(2):
        saved = submit(client, "https://youtu.be/no-notes")
        execute(client, saved["id"])
    with db.SessionLocal() as session:
        result = session.scalar(select(CaptureInsight).where(CaptureInsight.record_id == saved["id"]))
        assert result.status == "insufficient_evidence"


def test_daily_only_today_timezone_and_cooldown_expiry(environment):
    clock = datetime(2026, 12, 31, 15, 30, tzinfo=timezone.utc)
    seed_old_content(clock)
    batch = generate_daily(clock)
    with db.transaction() as session:
        assert session.get(ReviewBatch, batch).local_date == "2026-12-31"
        card = daily_cards(session, clock)["items"][0]
        feedback(session, card["id"], "seen", clock)
    assert generate_daily(clock + timedelta(hours=1)) is None  # 上海次日 00:30，尚未到 09:00
    later = clock + timedelta(days=8)
    generate_daily(later)
    with db.SessionLocal() as session:
        assert len(daily_cards(session, later)["items"]) == 1
        assert session.scalar(select(func.count()).select_from(ReviewBatch)) == 2


def test_daily_prioritizes_related_then_revisit_and_preserves_inaccurate_feedback(environment, configure, monkeypatch):
    clock = datetime(2026, 11, 19, 2, tzinfo=timezone.utc)
    configure(review={"review_daily_limit": 3, "review_recent_days": 30})
    monkeypatch.setattr(graph.llm, "complete", lambda *a: pytest.fail("每日卡片不能调用模型"))
    with db.transaction() as session:
        old = create_capture(session, text="很久以前的相关资料。")
        old.created_at = clock - timedelta(days=40)
        repeated = create_capture(session, text="https://example.com/repeated-review")
        repeated.created_at = clock - timedelta(days=50)
        again = create_capture(session, text="https://example.com/repeated-review")
        again.created_at = clock - timedelta(days=1)
        other = create_capture(session, text="长期没有回顾的其他资料。")
        other.created_at = clock - timedelta(days=60)
        new = create_capture(session, text="最近新资料与过去的内容有关。")
        new.created_at = clock - timedelta(days=1)
        evidence = []
        for record in (new, old):
            item = session.scalar(select(CaptureInput).where(CaptureInput.record_id == record.id))
            source = Source(record_id=record.id, input_id=item.id, version=1, origin="body", ordinal=0,
                            text=record.content, method="user_text", locator={}, search_text=record.content)
            session.add(source)
            session.flush()
            evidence.append({"source_id": source.id, "quote": source.text})
        relation = Relation(record_id=new.id, target_id=old.id, kind="same_topic", explanation="已有主题联系。",
                            evidence=evidence, created_at=clock)
        session.add(relation)
        session.flush()
        relation_id = relation.id
    generate_daily(clock)
    with db.transaction() as session:
        cards = daily_cards(session, clock)["items"]
        assert {card["kind"] for card in cards} == {"related", "revisit", "rediscover"}
        related = next(card for card in cards if card["kind"] == "related")
        session.get(Relation, relation_id).feedback = "ignored"
        session.flush()
        assert all(card["kind"] != "related" for card in daily_cards(session, clock)["items"])
        session.get(Relation, relation_id).feedback = "suggested"
        feedback(session, related["id"], "inaccurate", clock)
    later = clock + timedelta(days=8)
    generate_daily(later)
    with db.SessionLocal() as session:
        assert all(card["kind"] != "related" for card in daily_cards(session, later)["items"])
        assert session.scalar(select(ReviewCard.id).where(
            ReviewCard.feedback == "inaccurate", ReviewCard.evidence["relation_id"].as_string() == relation_id))


def test_graph_truncation_filters_and_stale_evidence(client, configure, monkeypatch):
    configure(graph={"graph_max_nodes": 10, "graph_max_edges": 10})
    monkeypatch.setattr(graph.llm, "complete", fake_complete)
    for number in range(12):
        saved = submit(client, f"Rerank 内容 {number}")
        execute(client, saved["id"])
    data = client.get("/api/graph", params={"hops": 3}).json()
    assert data["truncated"] and len(data["nodes"]) <= 10 and len(data["edges"]) <= 10
    assert all(node["kind"] == "record" for node in client.get("/api/graph", params={"kinds": "record"}).json()["nodes"])
    from wiki_app.modules.graph.service import valid_evidence

    with db.transaction() as session:
        source = session.scalar(select(Source).where(Source.record_id == saved["id"]))
        evidence = [{"source_id": source.id, "quote": source.text}]
        assert valid_evidence(session, evidence)
        session.get(Record, saved["id"]).version += 1
        assert not valid_evidence(session, evidence)


def test_short_alias_reads_once_and_cancel_does_not_commit_sources(client, monkeypatch):
    from wiki_app.integrations import platform_video

    calls = []
    monkeypatch.setattr(web, "fetch_html", lambda url: ("", "https://www.bilibili.com/video/BVcapture"))
    monkeypatch.setattr(platform_video, "read_platform_video", lambda url, *a: (
        calls.append(url) or [{"text": "Rerank 字幕原文", "start": 0, "end": 10}], None))
    monkeypatch.setattr(graph.llm, "complete", fake_complete)
    saved = submit(client, "https://b23.tv/capture https://www.bilibili.com/video/BVcapture")
    detail = execute(client, saved["id"])
    assert len(calls) == 1 and len(detail["sources"]) == 1

    def cancel(url):
        with db.transaction() as session:
            session.get(Record, other["id"]).status = "cancelled"
        return {"text": "读取期间取消", "url": url, "coverage": ""}

    monkeypatch.setattr(web, "read_webpage", cancel)
    other = submit(client, "https://example.com/cancel")
    detail = execute(client, other["id"])
    assert not detail["sources"]
