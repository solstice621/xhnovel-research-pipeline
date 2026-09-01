from __future__ import annotations

import json

import pytest

from xhnovel_pipeline.agent_files import (
    AGENT_FILES_EXECUTOR_KIND,
    AGENT_FILES_RESPONSE_FORMAT,
    AgentFileExecutor,
    AgentResponsesPending,
)
from xhnovel_pipeline.model_api import API_EXECUTOR_KIND, OpenAIResponsesClient
from xhnovel_pipeline.novel_workflow import run_novel_research
from xhnovel_pipeline.paths import repo_root
from xhnovel_pipeline.runtime import TEST_NOW
from xhnovel_pipeline.validate import validate_all


def _spec(source) -> dict:
    return {
        "source": {"kind": "txt", "path": str(source), "title": "Agent 仙途"},
        "rights": {
            "basis": "USER_AUTHORIZED_LOCAL_COPY",
            "may_store_full_text": True,
            "may_send_to_external_model": True,
            "may_export_excerpts": False,
        },
        "source_quality": {
            "edition_status": "USER_VERIFIED_COPY",
            "textual_completeness": "COMPLETE",
        },
        "request": {"discovery_brief": "寻找对象控制变化及其后续行动空间"},
        "limits": {"max_chapters": 10, "max_bytes": 2_000_000},
        "scene_scout": {
            "window_chars": 10_000,
            "overlap_chars": 1_800,
            "max_workers": 4,
        },
        "strict_order": False,
    }


def _unknown() -> dict:
    return {"status": "UNKNOWN", "values": [], "support_spans": []}


def _candidate(span: dict) -> dict:
    support = {
        "segment_id": span["segment_id"],
        "start": span["start"],
        "end": min(span["end"], span["start"] + 4),
    }
    known_actor = {
        "status": "KNOWN",
        "values": ["林舟"],
        "support_spans": [support],
    }
    known_action = {
        "status": "KNOWN",
        "values": ["触发机关"],
        "support_spans": [support],
    }
    known_target = {
        "status": "KNOWN",
        "values": ["天门机关"],
        "support_spans": [support],
    }
    return {
        "summary": "林舟触发天门机关",
        "source_spans": [support],
        "actors": known_actor,
        "action": known_action,
        "target": known_target,
        "precondition": _unknown(),
        "state_transition": _unknown(),
        "external_response": _unknown(),
        "immediate_feedback": _unknown(),
        "new_affordances": _unknown(),
        "persistence": _unknown(),
        "mechanic_pressure_point": _unknown(),
    }


def _write_answer(task_path, value: dict) -> bytes:
    task = json.loads(task_path.read_text(encoding="utf-8"))
    answer_path = task_path.parents[1] / task["answer_file"]
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    answer_path.parent.mkdir(parents=True, exist_ok=True)
    answer_path.write_bytes(raw)
    return raw


def _pending_run(tmp_path, text: str):
    source = tmp_path / "book.txt"
    source.write_text(text, encoding="utf-8")
    work_dir = tmp_path / "run"
    executor = AgentFileExecutor(work_dir / "agent-files")
    with pytest.raises(AgentResponsesPending) as caught:
        run_novel_research(
            _spec(source),
            work_dir,
            extractor_client=executor,
            repo_root=repo_root(),
            now=TEST_NOW,
        )
    return source, work_dir, caught.value


def test_agent_files_native_two_pass_run_needs_no_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, work_dir, pending = _pending_run(
        tmp_path,
        "第一章 天门\n林舟触发天门机关，山路随之开启。",
    )

    assert pending.pending_count == 1
    task_path = pending.pending[0].task_path
    task = json.loads(task_path.read_text(encoding="utf-8"))
    span = task["input"]["window"]["source_spans"][0]
    raw_answer = _write_answer(task_path, {"candidates": [_candidate(span)]})

    result = run_novel_research(
        _spec(source),
        work_dir,
        extractor_client=AgentFileExecutor(work_dir / "agent-files"),
        repo_root=repo_root(),
        now=TEST_NOW,
    )

    build = result["scout"]["build"]
    assert build["parameters"]["executor_kind"] == AGENT_FILES_EXECUTOR_KIND
    assert build["parameters"]["response_format"] == AGENT_FILES_RESPONSE_FORMAT
    assert result["scout"]["run"]["usage_ledger"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "attempts_with_unknown_usage": 1,
        "estimated_cost_microusd": None,
    }
    assert len(result["scout"]["candidates"]) == 1
    attempt = result["catalog"].all("ModelAttempt")[0]
    assert attempt["http_status"] is None
    assert attempt["provider_response_id"] is None
    assert result["store"].get(attempt["request_artifact_id"]) == task_path.read_bytes()
    assert result["store"].get(attempt["response_artifact_id"]) == raw_answer
    validate_all(result["catalog"], result["store"])


def test_agent_files_rejected_answer_can_be_corrected_and_replayed(tmp_path):
    source, work_dir, pending = _pending_run(
        tmp_path,
        "第一章 天门\n林舟触发天门机关，山路随之开启。",
    )
    task_path = pending.pending[0].task_path
    task = json.loads(task_path.read_text(encoding="utf-8"))
    span = task["input"]["window"]["source_spans"][0]
    invalid = _candidate(span)
    invalid["action"]["support_spans"] = [
        {
            "segment_id": span["segment_id"],
            "start": span["end"],
            "end": span["end"] + 1,
        }
    ]
    _write_answer(task_path, {"candidates": [invalid]})

    with pytest.raises(Exception, match="E-SCENE-PARTIAL"):
        run_novel_research(
            _spec(source),
            work_dir,
            extractor_client=AgentFileExecutor(work_dir / "agent-files"),
            repo_root=repo_root(),
            now=TEST_NOW,
        )

    _write_answer(task_path, {"candidates": [_candidate(span)]})
    result = run_novel_research(
        _spec(source),
        work_dir,
        extractor_client=AgentFileExecutor(work_dir / "agent-files"),
        repo_root=repo_root(),
        now=TEST_NOW,
    )

    attempts = result["catalog"].all("ModelAttempt")
    assert [item["status"] for item in attempts] == ["REJECTED", "SUCCEEDED"]
    assert attempts[1]["retry_of"] == attempts[0]["attempt_id"]
    validate_all(result["catalog"], result["store"])


def test_agent_files_partial_answers_resume_without_repeating_completed_windows(tmp_path):
    source, work_dir, first_pending = _pending_run(
        tmp_path,
        "第一章 长路\n" + ("甲乙丙丁" * 5_000),
    )
    assert first_pending.pending_count >= 2
    first_task = first_pending.pending[0].task_path
    _write_answer(first_task, {"candidates": []})

    with pytest.raises(AgentResponsesPending) as second:
        run_novel_research(
            _spec(source),
            work_dir,
            extractor_client=AgentFileExecutor(work_dir / "agent-files"),
            repo_root=repo_root(),
            now=TEST_NOW,
        )
    assert second.value.pending_count == first_pending.pending_count - 1

    for item in second.value.pending:
        _write_answer(item.task_path, {"candidates": []})
    result = run_novel_research(
        _spec(source),
        work_dir,
        extractor_client=AgentFileExecutor(work_dir / "agent-files"),
        repo_root=repo_root(),
        now=TEST_NOW,
    )

    attempts = result["catalog"].all("ModelAttempt")
    assert len(attempts) == len(result["scout"]["windows"])
    assert all(item["status"] == "SUCCEEDED" for item in attempts)
    validate_all(result["catalog"], result["store"])


def test_api_executor_identity_remains_explicit():
    client = OpenAIResponsesClient(model="test", api_key="test")
    assert client.executor_kind == API_EXECUTOR_KIND
    assert client.response_format == "OPENAI_RESPONSES"
