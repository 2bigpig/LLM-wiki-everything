"""验证本地后端与模型服务的协议边界，不依赖真实大模型。"""

import json

import httpx
import pytest

from wiki_app.config import get_settings
from wiki_app.errors import AppError
from wiki_app.integrations.media import inference
from wiki_app.modules.search.service import embed


def mock_http(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs
    ))


def test_remote_embeddings_protocol(environment, monkeypatch):
    def respond(request):
        assert request.url.path == "/embed"
        assert json.loads(request.content) == {"texts": ["检索"], "model": "BAAI/bge-m3"}
        return httpx.Response(200, json={"model": "BAAI/bge-m3", "embeddings": [[0.0] * 1024]})

    mock_http(monkeypatch, respond)
    assert len(embed(["检索"])[0]) == 1024
    assert embed([]) == []


@pytest.mark.parametrize("payload", [
    {"model": "wrong-model", "embeddings": [[0.0] * 1024]},
    {"model": "BAAI/bge-m3", "embeddings": [[0.0] * 3]},
    {"model": "BAAI/bge-m3", "embeddings": []},
    {"model": "BAAI/bge-m3", "embeddings": [[True] * 1024]},
])
def test_reject_incompatible_embeddings(environment, monkeypatch, payload):
    mock_http(monkeypatch, lambda _: httpx.Response(200, json=payload))
    with pytest.raises(AppError) as error:
        embed(["检索"])
    assert error.value.code == "embedding_failed"


def test_inference_request_id_and_shared_key(environment, monkeypatch):
    monkeypatch.setenv("OCR_URL", "http://127.0.0.1:8101")
    get_settings.cache_clear()

    def respond(request):
        assert request.url.path == "/infer"
        assert len(request.headers["X-Request-ID"]) == 32
        assert json.loads(request.content) == {"storage_key": "derived/page.png"}
        return httpx.Response(200, json={"segments": [{"text": "文字"}]})

    mock_http(monkeypatch, respond)
    assert inference("ocr", "derived/page.png")["segments"][0]["text"] == "文字"


def test_api_request_log_does_not_contain_body(client, environment):
    response = client.get("/api/overview?secret=do-not-log-this")
    request_id = response.headers["X-Request-ID"]
    content = (environment / "logs" / "api.log").read_text(encoding="utf-8")
    assert request_id in content
    assert "do-not-log-this" not in content
