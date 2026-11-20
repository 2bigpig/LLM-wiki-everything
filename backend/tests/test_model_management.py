"""真实适配器连接本机契约服务器；管理/预算/空间测试不访问用户厂商或资料。"""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from wiki_app import db
from wiki_app.config import get_settings
from wiki_app.errors import AppError, ConfigurationRequired
from wiki_app.integrations import llm
from wiki_app.models import (
    EmbeddingSpace,
    ModelCall,
    ModelConnection,
    ProcessingRun,
    Record,
    Source,
)
from wiki_app.modules.model_management import calls, runtime, service, vectors
from wiki_app.modules.model_management.schemas import ModelInput, ProbeDraft


@pytest.fixture
def management(client, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "test-admin")
    monkeypatch.setenv("MODEL_CREDENTIAL_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    client.headers["X-API-Key"] = "test-admin"
    return client


def add_model(client, kind="llm", model="test-model"):
    connection = client.post("/api/model-settings/connections", json={"name": "测试连接",
        "provider": "custom", "protocol": "openai", "base_url": "http://127.0.0.1:9991/v1",
        "api_key": "secret-do-not-return"})
    assert connection.status_code == 201, connection.text
    data = {"connection_id": connection.json()["id"], "name": model, "model": model, "kind": kind}
    response = client.post("/api/model-settings/models", json=data)
    assert response.status_code == 201, response.text
    return connection.json(), response.json(), data


def test_management_requires_explicit_token_even_when_instance_open(client):
    assert client.get("/api/model-settings").status_code == 403


def test_credentials_redacted_revision_snapshot_and_rotation(management):
    client = management
    connection, model, data = add_model(client)
    assert "secret-do-not-return" not in client.get("/api/model-settings").text
    assert client.put("/api/model-settings/bindings", json={"bindings": {"default": model["id"]}}).status_code == 200
    saved = client.post("/api/records", json={"content": "原始收藏"}).json()
    run_id = client.get("/api/records/" + saved["id"]).json()["runs"][0]["id"]
    with db.SessionLocal() as session:
        assert session.get(ProcessingRun, run_id).model_snapshot["analyze"] == model["revision_id"]
        assert session.get(ModelConnection, connection["id"]).key_cipher != "secret-do-not-return"
    revised = client.patch("/api/model-settings/models/" + model["id"], json={**data, "model": "new-model"}).json()
    assert revised["revision_id"] != model["revision_id"]
    with db.transaction() as session:
        assert service.resolve_run(session, run_id, "analyze")["model"] == "test-model"
    target = {k: connection[k] for k in ("name", "provider", "protocol", "base_url", "enabled")}
    assert client.patch("/api/model-settings/connections/" + connection["id"], json={**target,
        "api_key": "rotated-key"}).status_code == 200
    with db.transaction() as session:
        config = service.resolve_run(session, run_id, "analyze")
    calls.execute(config, "test", 0, lambda key: {"result": key == "rotated-key", "usage": {}})
    assert client.patch("/api/model-settings/connections/" + connection["id"], json={**target,
        "base_url": "http://127.0.0.1:9992/v1"}).status_code == 400
    assert client.patch("/api/model-settings/connections/" + connection["id"], json={**target,
        "enabled": False}).status_code == 200
    with pytest.raises(ConfigurationRequired), db.transaction() as session:
        service.resolve_run(session, run_id, "analyze")


def test_validation_never_echoes_input_secret(management):
    response = management.post("/api/model-settings/connections", json={"api_key": "private-bad-form"})
    assert response.status_code == 422 and "private-bad-form" not in response.text


def test_no_cross_target_secret_redirect(management):
    with fixture_server(redirect=True) as (url, requests):
        config = {**ModelInput(connection_id="x", name="test", model="test").model_dump(),
                  "protocol": "openai", "base_url": url + "/v1"}
        with pytest.raises(Exception):
            runtime.chat(config, "secret", [{"role": "user", "content": "test"}])
        assert len(requests) == 1


@contextmanager
def fixture_server(redirect=False, fail=False):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            requests.append((self.path, data, dict(self.headers)))
            if redirect:
                self.send_response(307)
                self.send_header("Location", "/another-target")
                self.end_headers()
                return
            if fail:
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"rate limited","type":"rate_limit_error"}}')
                return
            if "generateContent" in self.path:
                result = {"candidates": [{"content": {"role": "model", "parts": [{"text": '{"ok":true}'}]},
                    "finishReason": "STOP", "index": 0}], "usageMetadata": {"promptTokenCount": 10,
                    "candidatesTokenCount": 2, "totalTokenCount": 12}, "modelVersion": "gemini-test"}
            elif self.path.endswith("/messages"):
                content = [{"type": "tool_use", "id": "tool_1", "name": data["tools"][0]["name"], "input": {"ok": True}}] if data.get("tools") else [{"type": "text", "text": '{"ok":true}'}]
                result = {"id": "msg_test", "type": "message", "role": "assistant", "model": "claude-test",
                    "content": content, "stop_reason": "tool_use" if data.get("tools") else "end_turn",
                    "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 2}}
            else:
                result = {"id": "chatcmpl_test", "object": "chat.completion", "created": 1,
                    "model": "test-model", "choices": [{"index": 0, "message": {"role": "assistant",
                    "content": '{"ok":true}'}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}}
            payload = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("protocol", ["openai", "deepseek", "azure", "anthropic", "google"])
def test_real_langchain_protocols_json_and_usage(protocol):
    with fixture_server() as (url, requests):
        config = {**ModelInput(connection_id="x", name="test", model="test-model").model_dump(),
                  "protocol": protocol, "base_url": url + ("/v1" if protocol in ("openai", "deepseek") else "")}
        result = runtime.chat(config, "test-key", [{"role": "user", "content": '输出 {"ok":true}'}], ProbeDraft)
        assert ProbeDraft.model_validate_json(result["text"]).ok
        assert result["usage"]["total_tokens"] == 12
        assert len(requests) == 1


@pytest.mark.parametrize("protocol", ["openai", "deepseek", "azure", "anthropic", "google"])
def test_no_implicit_sdk_retry(protocol):
    with fixture_server(fail=True) as (url, requests):
        config = {**ModelInput(connection_id="x", name="test", model="test-model").model_dump(),
                  "protocol": protocol, "base_url": url + ("/v1" if protocol in ("openai", "deepseek") else "")}
        with pytest.raises(Exception):
            runtime.chat(config, "test-key", [{"role": "user", "content": "test"}])
        assert len(requests) == 1


def test_space_rebuild_resume_switch_and_wrong_dimensions(management, monkeypatch):
    client = management
    _, model, _ = add_model(client, kind="embedding")
    with db.transaction() as session:
        record = Record(kind="text", title="检索测试", content="原文", version=1)
        session.add(record)
        session.flush()
        session.add(Source(record_id=record.id, version=1, origin="text", ordinal=0, text="原文", locator={}, method="text"))
    space = client.post("/api/model-settings/spaces", json={"model_id": model["id"]}).json()["id"]
    old = client.get("/api/model-settings").json()["active_space_id"]
    assert client.post(f"/api/model-settings/spaces/{space}/activate").json()["status"] == "building"
    monkeypatch.setattr(calls, "embed", lambda *_: (_ for _ in ()).throw(AppError("模拟暂时失败")))
    vectors.rebuild(space)
    assert client.get("/api/model-settings").json()["active_space_id"] == old
    client.post(f"/api/model-settings/spaces/{space}/activate")
    monkeypatch.setattr(calls, "embed", lambda config, texts: [[1.0] + [0.0] * 1023 for _ in texts])
    vectors.rebuild(space)
    assert client.get("/api/model-settings").json()["active_space_id"] == space
    vectors.rebuild(space)
    assert client.get("/api/model-settings").json()["spaces"][-1]["indexed"] == 1
    with pytest.raises(AppError, match="1024"):
        runtime.validate_vectors([[1.0] * 1536], 1)


def test_invalid_json_preserves_usage_and_cannot_cache(management, monkeypatch):
    client = management
    _, model, _ = add_model(client)
    client.put("/api/model-settings/bindings", json={"bindings": {"default": model["id"]}})
    record = client.post("/api/records", json={"content": "测试"}).json()
    run = client.get('/api/records/' + record['id']).json()['runs'][0]['id']
    monkeypatch.setattr(runtime, "chat", lambda *args: {"text": "not json", "usage": {
        "input_tokens": 10, "output_tokens": 2, "total_tokens": 12}})
    with pytest.raises(AppError):
        llm.complete(run, "analyze", "test", {}, ProbeDraft)
    with db.SessionLocal() as session:
        call = session.scalar(select(ModelCall).where(ModelCall.run_id == run))
        assert call.status == "invalid_output" and call.charged_tokens == 12


def test_prepare_import_idempotent_and_preserves_master_key(environment, configure, monkeypatch):
    from wiki_app.config import read_private
    from wiki_app.modules.model_management.prepare import prepare

    path = configure(llm={"llm_model": "legacy-model"})
    for name in ("API_TOKEN", "MODEL_CREDENTIAL_KEY", "LLM_API_KEY", "POSTGRES_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    private = path.parent / ".env"
    private.write_text('POSTGRES_PASSWORD="unchanged-password"\nLLM_API_KEY="legacy-secret"\nAPI_TOKEN="existing-token"\n', encoding="utf-8")
    get_settings.cache_clear()
    prepare()
    values = read_private(private)
    assert "LLM_API_KEY" not in values and values["API_TOKEN"] == "existing-token"
    assert values["POSTGRES_PASSWORD"] == "unchanged-password"
    prepare()
    assert read_private(private)["MODEL_CREDENTIAL_KEY"] == values["MODEL_CREDENTIAL_KEY"]
    with db.SessionLocal() as session:
        assert len(list(session.scalars(select(ModelConnection)))) == 2
        prefs = service.preferences(session)
        assert prefs.bindings["default"]
    assert list(private.parent.glob(".env.backup.models.*"))


def test_prepare_failure_restores_original_file(environment, configure, monkeypatch):
    from wiki_app.modules.model_management.prepare import prepare

    path = configure()
    for name in ("API_TOKEN", "MODEL_CREDENTIAL_KEY", "LLM_API_KEY", "POSTGRES_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    original = b'POSTGRES_PASSWORD="unchanged"\nLLM_API_KEY="unimported-secret"\n'
    private = path.parent / ".env"
    private.write_bytes(original)
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="旧 LLM"):
        prepare()
    assert private.read_bytes() == original


def test_empty_stage_backfill_keeps_existing_revision(management):
    client = management
    _, first, _ = add_model(client, model="first")
    client.put("/api/model-settings/bindings", json={"bindings": {"analyze": first["id"]}})
    record = client.post("/api/records", json={"content": "补配模型"}).json()
    run = client.get('/api/records/' + record['id']).json()['runs'][0]['id']
    _, second, _ = add_model(client, model="second")
    client.put("/api/model-settings/bindings", json={"bindings": {"default": second["id"]}})
    with db.transaction() as session:
        assert service.resolve_run(session, run, "wiki")["model"] == "second"
        assert service.resolve_run(session, run, "analyze")["model"] == "first"


@pytest.mark.parametrize("case", ["truncated", "refused", "missing_usage"])
def test_truncation_refusal_and_unknown_usage_accounting(management, monkeypatch, case):
    client = management
    _, model, _ = add_model(client)
    client.put("/api/model-settings/bindings", json={"bindings": {"default": model["id"]}})
    record = client.post("/api/records", json={"content": "用量测试"}).json()
    run = client.get('/api/records/' + record['id']).json()['runs'][0]['id']
    result = {"text": '' if case == "refused" else '{"ok":true}', "incomplete": case == "truncated",
              "usage": {} if case == "missing_usage" else {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}}
    monkeypatch.setattr(runtime, "chat", lambda *args: result)
    if case == "missing_usage":
        assert llm.complete(run, "analyze", "test", {}, ProbeDraft).ok
    else:
        with pytest.raises(AppError):
            llm.complete(run, "analyze", "test", {}, ProbeDraft)
    with db.SessionLocal() as session:
        call = session.scalar(select(ModelCall).where(ModelCall.run_id == run))
        assert call.charged_tokens > 2400 if case == "missing_usage" else call.charged_tokens == 12
        assert (call.input_tokens is None) == (case == "missing_usage")


def test_public_preset_rejects_secret_fields(configure):
    configure(model_management={"provider_presets": [{"id": "bad", "name": "bad",
        "protocol": "openai", "base_url": "http://localhost", "api_key": "never-echo-me"}]})
    with pytest.raises(ValueError) as exc:
        get_settings()
    assert "api_key" in str(exc.value) and "never-echo-me" not in str(exc.value)


def test_local_bge_remains_available_when_cloud_budget_exhausted(environment, monkeypatch, configure):
    configure(llm={"monthly_token_limit": 1})
    with db.transaction() as session:
        service.preferences(session)
        space = session.scalar(select(EmbeddingSpace))
        config = service.revision_config(session, space.revision_id)
        session.add(ModelCall(stage="old", model="cloud", request_hash="old", operation="test", charged_tokens=100))
    monkeypatch.setattr(runtime, "embedding", lambda *args: ([[1.0] * 1024], None))
    assert len(calls.embed(config, ["本地资料"])[0]) == 1024
