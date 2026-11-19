"""覆盖统一配置的真实边界：错误脱敏、旧文件迁移和配置在消费者中的效果。"""

import asyncio
import importlib.util
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import quote

import httpx
import pytest
from dotenv import dotenv_values

from wiki_app.config import ROOT, get_settings, read_public
from wiki_app.configuration import compose_environment, migrate_env
from wiki_app.inference_task import create_asr_model, create_ocr_engine, transcribe
from wiki_app.integrations.extraction import split_text


def test_ports_paths_secrets_and_container_context(configure, monkeypatch, tmp_path):
    configure(services={"api_port": 18000, "frontend_port": 15173, "ocr_port": 18101,
                        "asr_port": 18102, "embedding_port": 18103, "model_http_port": 18100},
              database={"db_port": 15432, "db_container_port": 15433},
              paths={"data_dir": "relative/files"})
    monkeypatch.setenv("POSTGRES_PASSWORD", "a@b:/?#%")
    monkeypatch.chdir(tmp_path.parent)
    settings = get_settings()
    assert settings.api_url == "http://127.0.0.1:18000"
    assert "15173" in settings.cors_origins
    assert settings.ocr_url.endswith(":18101")
    assert settings.asr_url.endswith(":18102")
    assert settings.embedding_url.endswith(":18103")
    assert settings.data_dir == tmp_path / "relative/files"
    assert quote("a@b:/?#%", safe="") in settings.database_url
    assert "connect_timeout=5" in settings.database_url
    environment = compose_environment(settings)
    assert environment["WIKI_DB_PORT"] == "15432"
    assert environment["WIKI_DB_INTERNAL_PORT"] == "15433"
    assert environment["WIKI_MODEL_PORT"] == "18100"
    assert environment["WIKI_OCR_PORT"] == "18101"
    assert "LLM_API_KEY" not in environment and "API_TOKEN" not in environment
    assert "a@b" not in json.dumps(settings.model_dump())
    monkeypatch.setenv("WIKI_RUNTIME", "ocr")
    get_settings.cache_clear()
    assert "@postgres:15433/" in get_settings().database_url
    assert get_settings().data_dir == Path(settings.container_data_dir)


def test_public_script_output_supports_windows_unicode_paths(configure, monkeypatch, capsys):
    from wiki_app.configuration import main

    configure(paths={"ffmpeg_dir": "中文工具/ffmpeg"})
    monkeypatch.setattr(sys, "argv", ["configuration", "public"])
    assert main() == 0
    output = capsys.readouterr().out
    assert output.isascii()
    assert "中文工具" in json.loads(output)["ffmpeg_dir"]


@pytest.mark.parametrize("section,values", [
    ("services", {"api_port": "do-not-log-secret"}),
    ("services", {"api_port": 65536}),
    ("models", {"unknown_key": "do-not-log-secret"}),
    ("worker", {"worker_stalled_seconds": 1}),
    ("launcher", {"stop_timeout_seconds": 1}),
    ("models", {"inference_client_timeout_seconds": 1}),
    ("search", {"index_batch_size": 65}),
    ("paths", {"hf_cache_dir": "/tmp/models"}),
    ("paths", {"container_data_dir": "/app"}),
    ("graph", {"graph_max_nodes": 201}),
    ("graph", {"graph_max_edges": 501}),
    ("graph", {"graph_default_hops": 3, "graph_max_hops": 2}),
    ("review", {"review_timezone": "do-not-log-secret"}),
    ("review", {"review_daily_time": "25:30"}),
])
def test_invalid_config_is_rejected_without_values(configure, section, values):
    configure(**{section: values})
    with pytest.raises(ValueError) as error:
        get_settings()
    assert "do-not-log-secret" not in str(error.value)


def test_yaml_errors_missing_files_and_legacy_env(configure, monkeypatch):
    path = configure()
    original = path.read_text(encoding="utf-8")
    path.write_text("services: [do-not-log-secret", encoding="utf-8")
    with pytest.raises(ValueError) as error:
        read_public(path)
    assert "do-not-log-secret" not in str(error.value)
    path.write_text(original + "\nservices: {}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        read_public(path)
    path.unlink()
    with pytest.raises(ValueError):
        read_public(path)
    configure()
    monkeypatch.setenv("LLM_MODEL", "do-not-log-secret")
    with pytest.raises(ValueError, match="LLM_MODEL") as error:
        get_settings()
    assert "do-not-log-secret" not in str(error.value)


def test_migration_preserves_secrets_comments_and_effective_settings(configure, monkeypatch):
    path = configure()
    # 使用带注释的公开模板验证逐值更新不丢失说明。
    path.write_bytes((ROOT / "config.yaml").read_bytes())
    private = path.parent / ".env"
    password = "p@ss:/?#"
    original = ("# old configuration\nPOSTGRES_PASSWORD='p@ss:/?#'\nLLM_API_KEY='secret$123'\n"
                "API_TOKEN='token'\nDATA_DIR=custom-files\nLLM_MODEL=test-model\n"
                "MAX_VIDEO_FRAMES=5\nDATABASE_URL=postgresql+psycopg://wiki:"
                + quote(password, safe="") + "@127.0.0.1:15432/wiki\n")
    private.write_text(original, encoding="utf-8")
    migrate_env(path)
    assert set(dotenv_values(private)) == {"POSTGRES_PASSWORD", "LLM_API_KEY", "API_TOKEN"}
    assert dotenv_values(private)["POSTGRES_PASSWORD"] == password
    assert "#" in path.read_text(encoding="utf-8")
    assert next(path.parent.glob(".env.backup.*")).exists()
    for key in ("POSTGRES_PASSWORD", "LLM_API_KEY", "API_TOKEN"):
        monkeypatch.delenv(key)
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.llm_model == "test-model" and settings.max_video_frames == 5
    assert settings.db_port == 15432 and settings.data_dir.name == "custom-files"
    assert settings.llm_api_key.get_secret_value() == "secret$123"
    assert "secret$123" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("old", [
    "UNKNOWN_KEY=secret\n", "MAX_VIDEO_FRAMES=bad\n",
    "POSTGRES_PASSWORD=a\nDATABASE_URL=postgresql+psycopg://wiki:b@localhost/wiki\n",
    "OCR_URL=http://localhost:8101\nASR_URL=http://elsewhere:8102\n",
    "LLM_API_KEY='unterminated\n",
    "LLM_API_KEY=first\nLLM_API_KEY=second\n",
])
def test_migration_conflicts_leave_both_files_unchanged(configure, old):
    path = configure()
    private = path.parent / ".env"
    private.write_text(old, encoding="utf-8")
    original = path.read_bytes(), private.read_bytes()
    with pytest.raises(ValueError):
        migrate_env(path)
    assert original == (path.read_bytes(), private.read_bytes())


def test_migration_write_failure_restores_files(configure, monkeypatch):
    path = configure()
    private = path.parent / ".env"
    private.write_text("POSTGRES_PASSWORD=test\nLLM_MODEL=test-model\n", encoding="utf-8")
    before = path.read_bytes(), private.read_bytes()
    original = Path.write_text

    def write(target, *args, **kwargs):
        if target == private:
            raise OSError("simulated disk error")
        return original(target, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", write)
    with pytest.raises(ValueError, match="恢复"):
        migrate_env(path)
    assert before == (path.read_bytes(), private.read_bytes())


def test_chunking_and_model_constructors_use_config(configure, monkeypatch):
    configure(processing={"chunk_chars": 7}, models={"asr_model": "small", "asr_beam_size": 2,
              "asr_vad_filter": False, "ocr_detection_model": "det-test", "ocr_recognition_model": "rec-test"})
    assert [len(part) for part in split_text("x" * 20)] == [7, 7, 6]
    whisper = MagicMock()
    whisper.return_value.transcribe.return_value = ([], SimpleNamespace(language="zh"))
    paddle = MagicMock()
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=whisper))
    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=paddle))
    create_asr_model()
    assert whisper.call_args.args == ("small",)
    transcribe("input.wav")
    assert whisper.return_value.transcribe.call_args.kwargs["beam_size"] == 2
    assert whisper.return_value.transcribe.call_args.kwargs["vad_filter"] is False
    create_ocr_engine()
    assert paddle.call_args.kwargs["text_detection_model_name"] == "det-test"
    assert paddle.call_args.kwargs["text_recognition_model_name"] == "rec-test"


def test_worker_and_embedding_consumers_use_config(configure, monkeypatch):
    configure(worker={"worker_concurrency": 3, "worker_heartbeat_seconds": 4},
              models={"embedding_batch_size": 1, "embedding_max_seq_length": 128})
    from wiki_app import worker

    @asynccontextmanager
    async def scope():
        yield

    class Consumer:
        async def run(self):
            pass

        def stop(self):
            pass

    constructor = MagicMock(return_value=Consumer())
    monkeypatch.setattr(worker.app, "open_async", scope)
    monkeypatch.setattr(worker.app, "perform_import_paths", lambda: None)
    monkeypatch.setattr(worker, "Worker", constructor)
    asyncio.run(worker.serve())
    assert constructor.call_args.kwargs["concurrency"] == 3
    assert constructor.call_args.kwargs["update_heartbeat_interval"] == 4
    fake_model = MagicMock()
    fake_model.get_sentence_embedding_dimension.return_value = 1024
    fake_model.encode.return_value.tolist.return_value = [[0.0] * 1024]
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(
        SentenceTransformer=MagicMock(return_value=fake_model)))
    spec = importlib.util.spec_from_file_location("test_embedding_server", ROOT / "deploy/embedding_server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.embed(module.EmbeddingInput(texts=["sample"]))
    assert fake_model.max_seq_length == 128
    assert fake_model.encode.call_args.kwargs["batch_size"] == 1


def test_search_and_http_timeout_consumers(environment, configure, monkeypatch):
    from wiki_app.modules.search.service import embed, retrieve

    configure(search={"search_candidates": 1, "search_rrf_k": 10},
              models={"embedding_timeout_seconds": 9})
    # 插入来源后验证实际召回 SQL 的数量和融合分数。
    from wiki_app import db
    from wiki_app.models import Record, Source

    with db.transaction() as session:
        for title in ("first", "second"):
            record = Record(kind="text", title=title, content="shared term")
            session.add(record)
            session.flush()
            session.add(Source(record_id=record.id, version=record.version, text="shared term",
                               search_text="shared term", method="text", locator={},
                               origin="text", ordinal=0))
    hits = retrieve("shared")
    assert len(hits) == 1 and hits[0]["score"] == pytest.approx(1 / 11)
    original = httpx.Client
    timeouts = []

    def client(**kwargs):
        timeouts.append(kwargs["timeout"].read)
        return original(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={
            "model": get_settings().embedding_model, "embeddings": [[0.0] * 1024]})), **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    embed(["shared"])
    assert timeouts == [9]
