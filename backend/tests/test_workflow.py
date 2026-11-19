"""验证业务结果而非图的连接方式：恢复、引用、编辑保护和幂等。"""

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select

from wiki_app import db
from wiki_app.models import Analysis, ProcessingRun, Relation, WikiRevision
from wiki_app.modules.processing import graph


def fake_complete(run_id, stage, instruction, payload, schema):
    """离线测试替身仅在测试注入，运行中的产品不会伪造分析结果。"""
    if stage.startswith("analyze"):
        sources = payload["sources"]
        return schema.model_validate(
            {
                "kind": "learning",
                "summary": "Rerank 用于候选重排序。",
                "topics": ["Rerank"],
                "points": [{"text": sources[0]["text"], "source_ids": [sources[0]["id"]]}],
                "observations": "建议结合面试记录复习。",
            }
        )
    if stage == "relate":
        source, target = payload["current"][0], payload["candidates"][0]
        return schema.model_validate(
            {
                "relations": [
                    {
                        "target_id": target["record_id"],
                        "kind": "same_topic",
                        "explanation": "两份资料讨论候选重排序。",
                        "source_id": source["id"],
                        "target_source_id": target["id"],
                        "source_quote": source["text"][:8],
                        "target_quote": target["text"][:8],
                    }
                ]
            }
        )
    existing = payload["existing_pages"]
    return schema.model_validate(
        {
            "pages": [
                {
                    "page_id": existing[0]["id"] if existing else None,
                    "title": "Rerank",
                    "sections": [
                        {
                            "heading": "关键知识",
                            "content": "综合已有候选重排序资料。",
                            "source_ids": [s["id"] for s in payload["sources"]],
                        }
                    ],
                }
            ],
            "reason": "",
        }
    )


def save_and_run(client, text="Rerank 在检索后对候选结果重新排序。", saver=None):
    result = client.post("/api/records", json={"content": text})
    assert result.status_code == 201, result.text
    record_id = result.json()["id"]
    run_id = client.get("/api/records/" + record_id).json()["runs"][0]["id"]
    graph.execute_run(run_id, saver or InMemorySaver())
    return record_id, run_id


def test_automatic_pipeline_provenance_and_repeat_safety(client, monkeypatch):
    monkeypatch.setattr(graph.llm, "complete", fake_complete)
    saver = InMemorySaver()
    first, run_id = save_and_run(client, saver=saver)
    detail = client.get("/api/records/" + first).json()
    assert detail["status"] == "completed"
    assert detail["sources"][0]["text"].startswith("Rerank")
    assert detail["analyses"][0]["points"][0]["source_ids"] == [detail["sources"][0]["id"]]
    graph.execute_run(run_id, saver)
    second, _ = save_and_run(client, "Rerank 能补充向量检索候选的相关性判断。")
    page = client.get("/api/wiki").json()["items"][0]
    assert len(page["source_ids"]) == 2
    assert page["version"] == 2
    assert len(client.get("/api/records/" + second).json()["relations"]) == 1
    with db.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(Analysis)) == 2
        assert session.scalar(select(func.count()).select_from(WikiRevision)) == 2


def test_no_key_preserves_extraction_then_resumes(client, monkeypatch):
    saver = InMemorySaver()
    record_id, run_id = save_and_run(client, saver=saver)
    detail = client.get("/api/records/" + record_id).json()
    assert detail["status"] == "waiting_configuration"
    assert detail["sources"] and not detail["analyses"]
    original = detail["sources"][0]["id"]
    monkeypatch.setattr(graph.llm, "complete", fake_complete)
    monkeypatch.setattr(
        graph, "extract_record", lambda *args: pytest.fail("恢复不应再次读取已成功的原件")
    )
    assert client.post(f"/api/records/{record_id}/retry").status_code == 200
    graph.execute_run(run_id, saver)
    detail = client.get("/api/records/" + record_id).json()
    assert detail["status"] == "completed" and detail["sources"][0]["id"] == original


def test_wiki_personal_edit_survives_ai_update_and_restore(client, monkeypatch):
    monkeypatch.setattr(graph.llm, "complete", fake_complete)
    save_and_run(client)
    page = client.get("/api/wiki").json()["items"][0]
    response = client.put(
        f"/api/wiki/{page['id']}",
        json={"version": page["version"], "personal": "我在面试里没解释清楚。"},
    )
    assert response.status_code == 200
    assert (
        client.put(
            f"/api/wiki/{page['id']}", json={"version": 1, "personal": "过期修改"}
        ).status_code
        == 409
    )
    save_and_run(client, "Rerank 的计算成本需要结合候选数量评估。")
    updated = client.get(f"/api/wiki/{page['id']}").json()
    assert updated["personal"] == "我在面试里没解释清楚。" and updated["version"] == 3
    restored = client.post(
        f"/api/wiki/{page['id']}/restore", json={"version": 3, "revision": 2}
    ).json()
    assert restored["version"] == 4 and restored["personal"] == updated["personal"]
    assert len(client.get(f"/api/wiki/{page['id']}").json()["revisions"]) == 4
    exported = client.get(f"/api/wiki/{page['id']}/export")
    assert "我在面试里没解释清楚" in exported.text and "来源索引" in exported.text


def test_concurrent_personal_edit_blocks_stale_ai_revision(client, monkeypatch):
    monkeypatch.setattr(graph.llm, "complete", fake_complete)
    save_and_run(client)
    page = client.get("/api/wiki").json()["items"][0]

    def concurrent(*args):
        if args[1] == "wiki":
            assert (
                client.put(
                    f"/api/wiki/{page['id']}", json={"version": 1, "personal": "生成期间的人工修改"}
                ).status_code
                == 200
            )
        return fake_complete(*args)

    monkeypatch.setattr(graph.llm, "complete", concurrent)
    record_id, _ = save_and_run(client, "Rerank 也需要进行离线评测。")
    updated = client.get(f"/api/wiki/{page['id']}").json()
    assert updated["personal"] == "生成期间的人工修改" and updated["version"] == 2
    assert client.get("/api/records/" + record_id).json()["status"] == "failed"


def test_invalid_citations_do_not_publish_analysis(client, monkeypatch):
    def invalid(*args):
        result = fake_complete(*args)
        result.points[0].source_ids = ["invented-id"]
        return result

    monkeypatch.setattr(graph.llm, "complete", invalid)
    record_id, _ = save_and_run(client)
    detail = client.get("/api/records/" + record_id).json()
    assert detail["status"] == "failed" and not detail["analyses"] and not detail["pages"]


def test_ignored_relation_is_retained_on_reanalysis(client, monkeypatch):
    monkeypatch.setattr(graph.llm, "complete", fake_complete)
    first_id, _ = save_and_run(client)
    record_id, _ = save_and_run(client, "Rerank 面试回答需要讲清楚输入输出。")
    relation = client.get("/api/records/" + record_id).json()["relations"][0]
    assert (
        client.patch("/api/relations/" + relation["id"], json={"feedback": "ignored"}).status_code
        == 200
    )
    run = client.post(f"/api/records/{record_id}/reanalyze").json()
    graph.execute_run(run["id"], InMemorySaver())
    first_run = client.post(f"/api/records/{first_id}/reanalyze").json()
    graph.execute_run(first_run["id"], InMemorySaver())
    with db.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(Relation)) == 1
        assert session.get(Relation, relation["id"]).feedback == "ignored"


def test_search_includes_terms_late_in_a_source_chunk(client):
    text = " ".join(f"term{index:03}" for index in range(110)) + " pivottail"
    record_id, _ = save_and_run(client, text)
    results = client.get("/api/search?q=pivottail").json()["items"]
    assert any(result["record_id"] == record_id for result in results)


def test_uploaded_text_is_read_and_original_is_downloadable(client, monkeypatch):
    monkeypatch.setattr(graph.llm, "complete", fake_complete)
    text = "Rerank 文本附件中的原始记录。"
    response = client.post(
        "/api/records/upload", files={"files": ("学习笔记.txt", text.encode(), "text/plain")}
    )
    assert response.status_code == 201
    record_id = response.json()["id"]
    detail = client.get("/api/records/" + record_id).json()
    graph.execute_run(detail["runs"][0]["id"], InMemorySaver())
    detail = client.get("/api/records/" + record_id).json()
    assert detail["status"] == "completed"
    assert detail["sources"][0]["locator"]["filename"] == "学习笔记.txt"
    assert detail["sources"][0]["text"] == text
    assert client.get("/api/files/" + detail["attachments"][0]["id"]).content == text.encode()


def test_docx_paragraph_and_table_remain_traceable(client, monkeypatch):
    from io import BytesIO

    from docx import Document

    monkeypatch.setattr(graph.llm, "complete", fake_complete)
    document = Document()
    document.add_paragraph("Rerank 文档中的原始段落。")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "候选数量", "影响计算成本"
    output = BytesIO()
    document.save(output)
    saved = client.post(
        "/api/records/upload",
        files={"files": ("资料.docx", output.getvalue(), "application/octet-stream")},
    ).json()
    detail = client.get("/api/records/" + saved["id"]).json()
    graph.execute_run(detail["runs"][0]["id"], InMemorySaver())
    detail = client.get("/api/records/" + saved["id"]).json()
    assert detail["status"] == "completed"
    assert any(source["locator"].get("paragraph") == 1 for source in detail["sources"])
    assert any("影响计算成本" in source["text"] for source in detail["sources"])


def test_cancel_during_model_call_keeps_sources_without_publishing(client, monkeypatch):
    def stop_before_publish(*args):
        if args[1].startswith("analyze"):
            assert client.post(f"/api/runs/{args[0]}/cancel").status_code == 200
        return fake_complete(*args)

    monkeypatch.setattr(graph.llm, "complete", stop_before_publish)
    record_id, _ = save_and_run(client)
    detail = client.get("/api/records/" + record_id).json()
    assert detail["status"] == "cancelled"
    assert detail["sources"] and not detail["analyses"] and not detail["pages"]


def test_old_source_remains_traceable_after_edit_and_archive(client, monkeypatch):
    monkeypatch.setattr(graph.llm, "complete", fake_complete)
    record_id, _ = save_and_run(client)
    old = client.get("/api/records/" + record_id).json()["sources"][0]
    changed = client.put(
        f"/api/records/{record_id}",
        json={
            "version": 1,
            "title": "新理解",
            "content": "新的 Rerank 记录。",
            "notes": "",
            "purpose": "note",
        },
    )
    assert changed.status_code == 200
    stale = client.get("/api/sources/" + old["id"]).json()
    assert stale["stale"] and stale["text"] == old["text"]
    assert client.post(f"/api/records/{record_id}/archive").status_code == 200
    assert client.get("/api/records").json()["total"] == 0
    assert client.get("/api/sources/" + old["id"]).status_code == 200
    with db.SessionLocal() as session:
        assert all(
            r.status in ("completed", "cancelled") for r in session.scalars(select(ProcessingRun))
        )
