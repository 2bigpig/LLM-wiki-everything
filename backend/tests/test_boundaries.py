"""验证用户输入、文件路径、网络地址和模型费用边界。"""

import socket

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
    # 不同主动提交是不同收藏事件，规范化后的相同内容由资源表关联。
    assert client.post("/api/records", json={"url": "https://example.com/a"}).status_code == 201
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


def test_model_cache_and_conservative_budget(client, monkeypatch, configure):
    monkeypatch.setenv("LLM_API_KEY", "offline-test-only")
    configure(llm={"llm_model": "test-model", "monthly_token_limit": 100000})
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

    from cryptography.fernet import Fernet

    from wiki_app.modules.model_management import runtime, service
    from wiki_app.modules.model_management.schemas import ConnectionInput, ModelInput

    monkeypatch.setenv("MODEL_CREDENTIAL_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    with db.transaction() as session:
        connection = service.save_connection(session, ConnectionInput(name="测试", provider="custom",
            protocol="openai", base_url="http://127.0.0.1:9991/v1", api_key="offline-test-only"))
        model = service.save_model(session, ModelInput(connection_id=connection.id, name="测试", model="test-model"))
        service.preferences(session).bindings = {"default": model.id}

    def offline(*args):
        called.append(args)
        return {"text": output.model_dump_json(), "usage": {"input_tokens": 20,
            "output_tokens": 30, "total_tokens": 50}}

    monkeypatch.setattr(runtime, "chat", offline)
    first = llm.complete(run_id, "test", "测试", {"text": "原文"}, AnalysisDraft)
    second = llm.complete(run_id, "test", "测试", {"text": "原文"}, AnalysisDraft)
    assert first == second and len(called) == 1
    with db.SessionLocal() as session:
        assert session.scalar(select(ModelCall)).charged_tokens == 50
    llm.invalidate_result(run_id, "test")
    llm.complete(run_id, "test", "测试", {"text": "原文"}, AnalysisDraft)
    assert len(called) == 2
    configure(llm={"monthly_token_limit": 101})
    get_settings.cache_clear()
    with pytest.raises(BudgetExceeded):
        llm.complete(run_id, "test2", "另一次请求", {}, AnalysisDraft)
    assert len(called) == 2
