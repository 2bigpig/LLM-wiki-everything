"""可控时钟验证两种结束策略、缓存、预算、检查点和后台解释，不调用真实云服务。"""

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import func, select
from test_model_management import add_model
from test_model_management import management as management
from test_workflow import fake_complete

from wiki_app import db
from wiki_app.config import LLMScheduleSettings
from wiki_app.integrations import llm
from wiki_app.models import Analysis, CaptureInsight, ModelCall, ProcessingRun, Source
from wiki_app.modules.model_management import runtime, scheduling
from wiki_app.modules.model_management.schemas import ProbeDraft
from wiki_app.modules.processing import graph
from wiki_app.modules.review import insights


def at(hour, minute=0, day=20):
    return datetime(2026, 11, day, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(timezone.utc)


def policy(**changes):
    return {"enabled": True, "start": "00:30", "end": "08:30", "timezone": "Asia/Shanghai",
            "end_policy": "finish_batch", **changes}


def setup(client, monkeypatch, current, **changes):
    _, model, _ = add_model(client)
    client.put('/api/model-settings/bindings', json={"bindings": {"default": model["id"]}})
    clock = [current]
    monkeypatch.setattr(scheduling, "now", lambda: clock[0])
    assert client.put('/api/model-settings/schedule', json=policy(**changes)).status_code == 200
    requests = []

    def chat(config, key, messages, schema=None):
        requests.append(schema.__name__ if schema else "test")
        data = json.loads(messages[-1]["content"])["资料"] if "资料" in messages[-1]["content"] else {}
        if schema is ProbeDraft:
            output = ProbeDraft(ok=True)
        elif schema.__name__ == "InsightDraft":
            output = schema(explanation="", evidence=[])
        else:
            stage = "analyze" if schema.__name__ == "AnalysisDraft" else "relate" if schema.__name__ == "RelationsDraft" else "wiki"
            output = fake_complete("test", stage, "test", data, schema)
        return {"text": output.model_dump_json(), "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}}

    monkeypatch.setattr(runtime, "chat", chat)
    return clock, requests, model


def capture(client, text="Rerank 在检索之后对候选结果重新排序。"):
    record = client.post('/api/records', json={"content": text}).json()
    run_id = client.get('/api/records/' + record["id"]).json()['runs'][0]['id']
    with db.transaction() as session:
        session.get(ProcessingRun, run_id).created_at = scheduling.now()
    return record["id"], run_id


@pytest.mark.parametrize("start,end,hour,minute,opened", [
    ("00:30", "08:30", 0, 29, False), ("00:30", "08:30", 0, 30, True),
    ("00:30", "08:30", 8, 30, False), ("22:00", "06:00", 23, 0, True),
    ("22:00", "06:00", 5, 59, True), ("22:00", "06:00", 6, 0, False),
])
def test_daily_window_boundaries(start, end, hour, minute, opened):
    value = LLMScheduleSettings.model_validate(policy(start=start, end=end))
    current = at(hour, minute)
    assert scheduling.is_open(value, current) is opened
    next_time = scheduling.next_open(value, current)
    assert next_time >= current
    assert scheduling.is_open(value, next_time)


def test_default_and_invalid_policy(management):
    client = management
    result = client.get('/api/model-settings/schedule').json()
    assert result['policy']['enabled'] is False
    assert result['policy']['end_policy'] == 'finish_batch'
    for bad in (policy(end="00:30"), policy(start="26:00"), policy(timezone="not-a-zone"),
                policy(end_policy="unknown")):
        assert client.put('/api/model-settings/schedule', json=bad).status_code == 422


def test_waiting_does_not_reserve_or_retry_and_resumes_checkpoint(management, monkeypatch):
    client = management
    clock, requests, _ = setup(client, monkeypatch, at(12), end_policy="wait_next_window")
    record_id, run_id = capture(client)
    saver = InMemorySaver()
    graph.execute_run(run_id, saver)
    with db.SessionLocal() as session:
        run = session.get(ProcessingRun, run_id)
        assert run.status == 'waiting_schedule' and run.attempts == 0 and run.finished_at is None
        assert run.scheduled_for is not None
        assert session.scalar(select(func.count()).select_from(Source)) > 0
        assert session.scalar(select(func.count()).select_from(ModelCall)) == 0
    assert not requests
    clock[0] = at(1, day=21)
    scheduling.tick()
    graph.execute_run(run_id, saver)
    assert client.get('/api/records/' + record_id).json()['status'] == 'completed'
    with db.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(Analysis)) == 1
    assert requests


@pytest.mark.parametrize("end_policy,expected", [("finish_batch", "completed"), ("wait_next_window", "waiting_schedule")])
def test_inflight_completes_then_next_request_obeys_end_policy(management, monkeypatch, end_policy, expected):
    client = management
    clock, requests, _ = setup(client, monkeypatch, at(8, 29), end_policy=end_policy)
    original = runtime.chat
    def finish_after_end(*args):
        result = original(*args)
        clock[0] = at(8, 31)
        return result
    monkeypatch.setattr(runtime, 'chat', finish_after_end)
    record_id, run_id = capture(client)
    graph.execute_run(run_id, InMemorySaver())
    assert client.get('/api/records/' + record_id).json()['status'] == expected
    assert len(requests) >= 1
    with db.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(Analysis)) == 1


def test_whole_batch_is_admitted_and_late_submissions_wait(management, monkeypatch):
    client = management
    clock, requests, _ = setup(client, monkeypatch, at(1))
    first, first_run = capture(client)
    second, second_run = capture(client, "Rerank 可以重新排列候选资料。")
    crossing, crossing_run = capture(client, "模拟扫描跨过边界后才提交的任务。")
    with db.transaction() as session:
        session.get(ProcessingRun, crossing_run).created_at = at(12)
    scheduling.tick()
    clock[0] = at(12)
    graph.execute_run(first_run, InMemorySaver())
    graph.execute_run(second_run, InMemorySaver())
    assert client.get('/api/records/' + first).json()['status'] == 'completed'
    assert client.get('/api/records/' + second).json()['status'] == 'completed'
    before = len(requests)
    graph.execute_run(crossing_run, InMemorySaver())
    assert client.get('/api/records/' + crossing).json()['status'] == 'waiting_schedule'
    late, late_run = capture(client, "新任务不能借用已结束时段的旧批次。")
    graph.execute_run(late_run, InMemorySaver())
    assert client.get('/api/records/' + late).json()['status'] == 'waiting_schedule'
    assert len(requests) == before


def test_cache_and_manual_tests_are_not_deferred(management, monkeypatch):
    client = management
    clock, requests, model = setup(client, monkeypatch, at(1), end_policy="wait_next_window")
    _, run_id = capture(client)
    assert llm.complete(run_id, 'analyze', 'same', {}, ProbeDraft).ok
    clock[0] = at(12)
    assert llm.complete(run_id, 'analyze', 'same', {}, ProbeDraft).ok
    assert len(requests) == 1
    with pytest.raises(scheduling.ScheduleDeferred):
        llm.complete(run_id, 'analyze', 'different', {}, ProbeDraft)
    assert client.post('/api/model-settings/models/' + model['id'] + '/test', json={'kind': 'json'}).json()['succeeded']
    assert len(requests) == 2


def test_changing_to_wait_policy_stops_an_admitted_batch(management, monkeypatch):
    client = management
    clock, requests, _ = setup(client, monkeypatch, at(1))
    _, run_id = capture(client)
    scheduling.tick()
    clock[0] = at(12)
    client.put('/api/model-settings/schedule', json=policy(end_policy='wait_next_window'))
    with pytest.raises(scheduling.ScheduleDeferred):
        llm.complete(run_id, 'analyze', 'test', {}, ProbeDraft)
    assert not requests


def test_stop_scheduled_task_prevents_future_dispatch(management, monkeypatch):
    client = management
    clock, _, _ = setup(client, monkeypatch, at(12))
    _, run_id = capture(client)
    graph.execute_run(run_id, InMemorySaver())
    assert client.post('/api/runs/' + run_id + '/cancel').json()['status'] == 'cancelled'
    clock[0] = at(1, day=21)
    scheduling.tick()
    with db.SessionLocal() as session:
        run = session.get(ProcessingRun, run_id)
        assert run.status == 'cancelled' and run.scheduled_for is None


def test_policy_changes_release_waiting_and_archive_prevents_resume(management, monkeypatch):
    client = management
    clock, _, _ = setup(client, monkeypatch, at(12))
    record_id, run_id = capture(client)
    graph.execute_run(run_id, InMemorySaver())
    assert client.post('/api/records/' + record_id + '/retry').status_code == 409
    assert client.put('/api/model-settings/schedule', json=policy(enabled=False)).status_code == 200
    with db.SessionLocal() as session:
        assert session.get(ProcessingRun, run_id).status == 'queued'
    client.put('/api/model-settings/schedule', json=policy())
    graph.execute_run(run_id, InMemorySaver())
    client.post('/api/records/' + record_id + '/archive')
    clock[0] = at(1, day=21)
    scheduling.tick()
    with db.SessionLocal() as session:
        assert session.get(ProcessingRun, run_id).status == 'cancelled'


def test_deep_insight_waits_without_changing_completed_record(management, monkeypatch):
    client = management
    clock, requests, _ = setup(client, monkeypatch, at(12), end_policy='wait_next_window')
    _, run_id = capture(client)
    with db.transaction() as session:
        run = session.get(ProcessingRun, run_id)
        run.status = 'completed'
        row = CaptureInsight(run_id=run.id, record_id=run.record_id, version=run.version, status='pending')
        session.add(row)
        session.flush()
        identifier = row.id
    monkeypatch.setattr(insights, 'insight_context', lambda *args: ({'events': []}, {}))
    insights.generate_insight(identifier)
    with db.SessionLocal() as session:
        assert session.get(CaptureInsight, identifier).status == 'waiting_schedule'
        assert session.get(ProcessingRun, run_id).status == 'completed'
    assert not requests
    clock[0] = at(1, day=21)
    scheduling.tick()
    insights.generate_insight(identifier)
    with db.SessionLocal() as session:
        assert session.get(CaptureInsight, identifier).status == 'insufficient_evidence'
    assert len(requests) == 1
