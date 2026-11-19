"""验证用户输入、文件路径、网络地址和模型费用边界。"""

import socket
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from wiki_app import db
from wiki_app.config import get_settings
from wiki_app.errors import AppError, BudgetExceeded
from wiki_app.integrations import llm
from wiki_app.integrations.storage import storage_path
from wiki_app.integrations.web import public_address
from wiki_app.models import ModelCall
from wiki_app.modules.library.service import normalize_url
from wiki_app.schemas import AnalysisDraft


def test_idempotent_submission_and_normalized_link(client):
    data = {"content": "同一个网络请求只保存一次。"}
    first = client.post("/api/records", json=data, headers={"Idempotency-Key": "request-1"})
    second = client.post("/api/records", json=data, headers={"Idempotency-Key": "request-1"})
    assert first.json()["id"] == second.json()["id"]
    assert client.get("/api/records").json()["total"] == 1
    assert (
        client.post("/api/records", json={"url": "https://example.com/a?utm_source=x"}).status_code
        == 201
    )
    assert client.post("/api/records", json={"url": "https://example.com/a"}).status_code == 409
    assert (
        normalize_url("https://EXAMPLE.com:443/a?x=1&utm_source=y#section")
        == "https://example.com/a?x=1"
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://u:password@example.com",
        "http://example.com:5432/",
    ],
)
def test_unsafe_link_forms_rejected(url):
    with pytest.raises(AppError):
        normalize_url(url)


@pytest.mark.parametrize(
    "ip", ["127.0.0.1", "10.1.1.1", "169.254.169.254", "::1", "::ffff:127.0.0.1"]
)
def test_private_network_and_metadata_rejected(monkeypatch, ip):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))],
    )
    with pytest.raises(AppError, match="公网"):
        public_address("example.com", 80)


def test_storage_traversal_and_failed_upload_cleanup(client, environment):
    with pytest.raises(AppError):
        storage_path("../outside.txt")
    result = client.post(
        "/api/records/upload",
        files=[
            ("files", ("notes.txt", b"hello", "text/plain")),
            ("files", ("bad.exe", b"bad", "application/octet-stream")),
        ],
    )
    assert result.status_code == 400
    assert not list((environment / "uploads").glob("*"))
    assert client.get("/api/records").json()["total"] == 0


def test_access_token_and_media_cookie(client, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "test-local-access")
    get_settings.cache_clear()
    assert client.get("/api/records").status_code == 401
    assert client.post("/api/session", headers={"X-API-Key": "wrong"}).status_code == 401
    assert (
        client.post("/api/session", headers={"X-API-Key": "test-local-access"}).status_code == 200
    )
    assert client.get("/api/records").status_code == 200
    saved = client.post(
        "/api/records/upload", files={"files": ("note.txt", b"original text", "text/plain")}
    ).json()
    file_id = client.get("/api/records/" + saved["id"]).json()["attachments"][0]["id"]
    assert client.get("/api/files/" + file_id).content == b"original text"
    assert client.post("/api/session/logout").status_code == 200
    assert client.get("/api/files/" + file_id).status_code == 401


def test_model_cache_and_conservative_budget(client, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "offline-test-only")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("MONTHLY_TOKEN_LIMIT", "100000")
    get_settings.cache_clear()
    saved = client.post("/api/records", json={"content": "预算验证"}).json()
    run_id = client.get("/api/records/" + saved["id"]).json()["runs"][0]["id"]
    output = AnalysisDraft(
        kind="summary",
        summary="测试摘要",
        topics=[],
        points=[{"text": "测试要点", "source_ids": ["s1"]}],
    )
    called = []

    class OfflineClient:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def create(self, **kwargs):
            called.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content=output.model_dump_json()))
                ],
                usage=SimpleNamespace(prompt_tokens=20, completion_tokens=30, total_tokens=50),
            )

    monkeypatch.setattr(llm, "OpenAI", OfflineClient)
    first = llm.complete(run_id, "test", "测试", {"text": "原文"}, AnalysisDraft)
    second = llm.complete(run_id, "test", "测试", {"text": "原文"}, AnalysisDraft)
    assert first == second and len(called) == 1
    with db.SessionLocal() as session:
        assert session.scalar(select(ModelCall)).charged_tokens == 50
    llm.invalidate_result(run_id, "test")
    llm.complete(run_id, "test", "测试", {"text": "原文"}, AnalysisDraft)
    assert len(called) == 2
    monkeypatch.setenv("MONTHLY_TOKEN_LIMIT", "101")
    get_settings.cache_clear()
    with pytest.raises(BudgetExceeded):
        llm.complete(run_id, "test2", "另一次请求", {}, AnalysisDraft)
    assert len(called) == 2
