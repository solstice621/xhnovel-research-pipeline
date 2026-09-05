from __future__ import annotations

import copy
import json
import pathlib
import subprocess
import sys

import pytest

import xhnovel_pipeline.generic_handoff_execution as execution
from xhnovel_pipeline.canonical import canonical_dumps
from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.generic_cli import make_generic_executor
from xhnovel_pipeline.generic_extraction import generic_work_dir_lock, run_generic_corpus_workflow
from xhnovel_pipeline.generic_handoff import prepare_generic_handoff_from_input
from xhnovel_pipeline.generic_handoff_execution import (
    execute_generic_handoff, validate_generic_execution,
    validate_generic_execution_event, validate_generic_execution_history,
)
from xhnovel_pipeline.observation_common import get_record, put_record, research_store, seal_record

from test_generic_extraction import CountingApiExecutor, ROOT
from test_generic_handoff import NOW, handoff_input, prepared_handoff


def _run(research_root, prepared, work_dir, **kwargs):
    return execute_generic_handoff(
        prepared["handoff_path"], research_root, work_dir, root=ROOT, now=NOW, **kwargs,
    )


def _answers(waiting, *, nonempty=False):
    for pending in waiting["pending"]:
        packet = json.loads(pathlib.Path(pending["task"]).read_bytes())
        answer = {"records": []}
        if "completion" in packet["output"]["schema"]["required"]:
            answer["completion"] = {"status": "COMPLETE"}
        if nonempty:
            kind, phrase = ("RACE_MENTION", "人族") if packet["profile_id"] == "xhnovel.race-mention" else ("PLACE_MENTION", "青云山")
            for span in packet["input"]["unit"]["source_spans"]:
                index = span["untrusted_text"].find(phrase)
                if index >= 0:
                    answer["records"].append({
                        "payload": {"kind": kind, "name": phrase},
                        "evidence_bindings": [{"paths": ["/name"], "source_spans": [{
                            "segment_id": span["segment_id"], "start": span["start"] + index,
                            "end": span["start"] + index + len(phrase),
                        }]}],
                    })
                    break
        pathlib.Path(pending["answer"]).write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")


def _event_files(research_root, prepared):
    return sorted((research_root / "executions" / prepared["handoff"]["handoff_id"] / "events").glob("*.json"))


@pytest.mark.parametrize("profile,kind,nonempty", [
    ("race-mention-v1", "RACE_MENTION", False),
    ("race-mention-v1", "RACE_MENTION", True),
    ("geography-unique-v1", "PLACE_MENTION", True),
])
def test_two_pass_selected_receipt_cached_success_and_offline_replay(tmp_path, profile, kind, nonempty):
    research_root, source, prepared = prepared_handoff(tmp_path, profile=profile, kind=kind)
    work_dir = tmp_path / "work"
    waiting = _run(research_root, prepared, work_dir)
    assert waiting["status"] == "WAITING_FOR_AGENT"
    assert waiting["failed_unit_count"] == 0
    assert not list((research_root / "records" / execution.RECEIPT_KIND).glob("*.json"))
    _answers(waiting, nonempty=nonempty)
    completed = _run(research_root, prepared, work_dir)
    assert completed["status"] == "SUCCEEDED", completed
    assert completed["receipt"]["result"]["corpus_record_count"] == int(nonempty)
    assert completed["receipt"]["attempt_id"] == waiting["attempt_id"]
    assert completed["receipt"]["invocation_ordinal"] == 2
    assert completed["receipt"]["result"]["semantic_assurance"] == "UNQUALIFIED"
    assert get_record(research_root, completed["event_artifact_id"])["detail"]["receipt_artifact_id"] == completed["receipt_artifact_id"]
    before = _event_files(research_root, prepared)
    source.unlink()
    assert validate_generic_execution(completed["receipt_path"], research_root) == completed["receipt"]
    cached = _run(research_root, prepared, work_dir)
    assert cached["reused_terminal_receipt"]
    assert cached["receipt_artifact_id"] == completed["receipt_artifact_id"]
    assert before == _event_files(research_root, prepared)
    if nonempty and profile == "race-mention-v1":
        script = "from pathlib import Path; import sys; from xhnovel_pipeline.generic_handoff_execution import validate_generic_execution; r=validate_generic_execution(Path(sys.argv[1]),Path(sys.argv[2])); assert r['status']=='SUCCEEDED'"
        process = subprocess.run([sys.executable, "-c", script, completed["receipt_path"], str(research_root)],
                                 capture_output=True, text=True, timeout=30)
        assert process.returncode == 0, process.stderr


def test_api_executor_uses_native_descriptor(tmp_path):
    research_root, _, prepared = prepared_handoff(tmp_path)
    executor = CountingApiExecutor(lambda _: {"records": []})
    result = _run(research_root, prepared, tmp_path / "work", executor_kind="api", executor=executor)
    assert result["status"] == "SUCCEEDED", result
    assert executor.calls > 0
    assert result["receipt"]["binding"]["executor"]["kind"] == "API"


def test_new_research_definition_reuses_same_profile_native_extraction(tmp_path):
    research_root, source, first = prepared_handoff(tmp_path)
    work_dir = tmp_path / "work"
    one = _run(research_root, first, work_dir, executor_kind="api", executor=CountingApiExecutor(lambda _: {"records": []}))
    assert one["status"] == "SUCCEEDED", one
    second = prepare_generic_handoff_from_input(
        handoff_input(research_root, source, goal="Observe local explicit race names for another comparison"), research_root,
    )
    assert second["handoff"]["handoff_id"] != first["handoff"]["handoff_id"]
    assert second["novel_spec"] == first["novel_spec"]
    executor = CountingApiExecutor(lambda _: {"records": []})
    two = _run(research_root, second, work_dir, executor_kind="api", executor=executor)
    assert two["status"] == "SUCCEEDED", two
    assert executor.calls == 0
    assert two["receipt"]["result"] == one["receipt"]["result"]
    assert two["receipt"]["handoff_id"] != one["receipt"]["handoff_id"]


def test_rejected_answers_remain_audited_after_partial_correction(tmp_path):
    research_root, _, prepared = prepared_handoff(tmp_path)
    work_dir = tmp_path / "work"
    waiting = _run(research_root, prepared, work_dir)
    for item in waiting["pending"]:
        pathlib.Path(item["answer"]).write_text('{"wrong": []}', encoding="utf-8")
    partial = _run(research_root, prepared, work_dir)
    assert partial["status"] == "PARTIAL_RETRYABLE", partial
    assert partial["failed_unit_count"] == waiting["pending_count"]
    frozen = research_store(research_root).get(partial["checkpoint_artifact_id"])
    _answers(waiting, nonempty=True)
    result = _run(research_root, prepared, work_dir)
    assert result["status"] == "SUCCEEDED", result
    assert result["attempt_id"] == partial["attempt_id"] == waiting["attempt_id"]
    assert research_store(research_root).get(partial["checkpoint_artifact_id"]) == frozen
    attempts = next(work_dir.glob("generic-extraction/profiles/*/extractions/*/attempts.jsonl"))
    states = [json.loads(line)["status"] for line in attempts.read_text().splitlines()]
    assert "REJECTED" in states and "SUCCEEDED" in states


def test_waiting_keeps_failed_units_and_only_current_checkpoint(tmp_path):
    research_root = tmp_path / "research"
    source = tmp_path / "novel.txt"
    source.write_text("第一章 山门\n人族在青云山。" + "甲" * 25000 + "\n", encoding="utf-8")
    prepared = prepare_generic_handoff_from_input(handoff_input(research_root, source), research_root)
    work_dir = tmp_path / "work"
    waiting = _run(research_root, prepared, work_dir)
    assert waiting["pending_count"] > 2
    pathlib.Path(waiting["pending"][0]["answer"]).write_text('{"wrong": []}', encoding="utf-8")
    completed_answer = pathlib.Path(waiting["pending"][1]["answer"])
    completed_answer.write_text('{"records": []}', encoding="utf-8")
    mixed = _run(research_root, prepared, work_dir)
    assert mixed["status"] == "WAITING_FOR_AGENT"
    assert mixed["failed_unit_count"] == 1
    assert mixed["pending_count"] == waiting["pending_count"] - 2
    assert len(mixed["failures"]) == 1
    _answers(waiting)
    completed_answer.unlink()  # Resume must replay its checkpoint, not request this unit again.
    result = _run(research_root, prepared, work_dir)
    assert result["status"] == "SUCCEEDED", result


def test_current_pending_and_source_failure_never_use_older_native_success(tmp_path):
    research_root, source, prepared = prepared_handoff(tmp_path)
    work_dir = tmp_path / "work"
    old = run_generic_corpus_workflow(
        prepared["novel_spec"], work_dir, profile_ref="race-mention-v1",
        executor=CountingApiExecutor(lambda _: {"records": []}), root=ROOT, now=NOW,
    )
    current = _run(research_root, prepared, work_dir)
    assert current["status"] == "WAITING_FOR_AGENT"
    assert old.corpus_snapshot_path.exists()
    source.unlink()
    failed = _run(research_root, prepared, work_dir)
    assert failed["status"] == "FAILED"
    assert failed["receipt"]["error"]["stage"] == "SOURCE_PREFLIGHT"
    assert "result" not in failed["receipt"]
    assert validate_generic_execution(failed["receipt"], research_root) == failed["receipt"]


def test_interruption_on_continuation_requires_explicit_recovery(tmp_path, monkeypatch):
    research_root, _, prepared = prepared_handoff(tmp_path)
    work_dir = tmp_path / "work"
    waiting = _run(research_root, prepared, work_dir)
    _answers(waiting)
    native = execution.run_generic_corpus_workflow
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt("simulated host interruption")
    monkeypatch.setattr(execution, "run_generic_corpus_workflow", interrupted)
    with pytest.raises(KeyboardInterrupt):
        _run(research_root, prepared, work_dir)
    monkeypatch.setattr(execution, "run_generic_corpus_workflow", native)
    with pytest.raises(ValidationError, match="E-GENERIC-INTERRUPTED"):
        _run(research_root, prepared, work_dir)
    result = _run(research_root, prepared, work_dir, resume=True)
    assert result["status"] == "SUCCEEDED", result
    assert result["attempt_id"] == waiting["attempt_id"]
    assert result["receipt"]["invocation_ordinal"] == 3
    assert validate_generic_execution(result["receipt"], research_root) == result["receipt"]


@pytest.mark.parametrize("failure", [ValidationError("E-TEST", "external prerequisite failed"), TypeError("unexpected executor failure")])
def test_failed_new_attempt_requires_retry_and_native_source_identity_still_applies(tmp_path, monkeypatch, failure):
    research_root, _, prepared = prepared_handoff(tmp_path)
    work_dir = tmp_path / "work"
    native = execution.run_generic_corpus_workflow
    def fail_once(*args, **kwargs):
        raise failure
    monkeypatch.setattr(execution, "run_generic_corpus_workflow", fail_once)
    failed = _run(research_root, prepared, work_dir)
    assert failed["status"] == "FAILED"
    with pytest.raises(ValidationError, match="E-GENERIC-RETRY-REQUIRED"):
        _run(research_root, prepared, work_dir)
    monkeypatch.setattr(execution, "run_generic_corpus_workflow", native)
    waiting = _run(research_root, prepared, work_dir, retry=True)
    assert waiting["status"] == "WAITING_FOR_AGENT"
    assert waiting["attempt_id"] != failed["attempt_id"]
    _answers(waiting)
    result = _run(research_root, prepared, work_dir)
    assert result["status"] == "SUCCEEDED", result
    assert result["receipt"]["attempt_ordinal"] == 2


@pytest.mark.parametrize("drift", ["label", "timeout", "directory"])
def test_resume_rejects_actual_executor_or_directory_drift(tmp_path, drift):
    research_root, _, prepared = prepared_handoff(tmp_path)
    work_dir = tmp_path / "work"
    _run(research_root, prepared, work_dir)
    executor = make_generic_executor("agent-files", work_dir, "race-mention-v1", root=ROOT)
    if drift == "label": executor.model = "different-host"
    if drift == "timeout": executor.timeout = 2.0
    if drift == "directory": work_dir = tmp_path / "different-work"
    with pytest.raises(ValidationError, match="E-GENERIC-ATTEMPT-IDENTITY"):
        _run(research_root, prepared, work_dir, executor=executor)


@pytest.mark.parametrize("binding_change", ["repository_commit", "engine_source_hash"])
@pytest.mark.parametrize("completed", [False, True])
def test_runtime_change_rejects_old_attempt_and_completed_replay(tmp_path, monkeypatch, binding_change, completed):
    import xhnovel_pipeline.generic_extraction as native
    research_root, _, prepared = prepared_handoff(tmp_path)
    work_dir = tmp_path / "work"
    waiting = _run(research_root, prepared, work_dir)
    if completed:
        _answers(waiting)
        result = _run(research_root, prepared, work_dir)
        assert result["status"] == "SUCCEEDED", result
    if binding_change == "repository_commit":
        monkeypatch.setattr(native, "repository_commit", lambda _: "f" * 40)
    else:
        monkeypatch.setattr(native, "_generic_engine_source_hash", lambda _: "sha256:" + "b" * 64)
    with pytest.raises(ValidationError, match="E-GENERIC-ATTEMPT-IDENTITY"):
        _run(research_root, prepared, work_dir)
    if completed:
        with pytest.raises(ValidationError, match="E-GENERIC-BUILD-BIND: exact engine runtime differs"):
            validate_generic_execution(result["receipt"], research_root)


@pytest.mark.parametrize("tamper", ["event", "receipt", "frozen_checkpoint", "live_checkpoint_missing"])
def test_audit_or_checkpoint_tampering_fails_closed(tmp_path, tamper):
    research_root, _, prepared = prepared_handoff(tmp_path)
    work_dir = tmp_path / "work"
    waiting = _run(research_root, prepared, work_dir)
    if tamper == "event":
        _event_files(research_root, prepared)[-1].write_text("{}", encoding="utf-8")
        with pytest.raises(ValidationError): _run(research_root, prepared, work_dir)
    elif tamper == "frozen_checkpoint":
        research_store(research_root)._path(waiting["checkpoint_artifact_id"]).write_bytes(b"corrupt")
        with pytest.raises(ValidationError, match="E-ARTIFACT-CORRUPT"):
            _run(research_root, prepared, work_dir)
    elif tamper == "live_checkpoint_missing":
        pathlib.Path(waiting["checkpoint_path"]).unlink()
        result = _run(research_root, prepared, work_dir)
        assert result["status"] == "FAILED"
        assert result["receipt"]["error"]["code"] == "E-GENERIC-RESUME-CHECKPOINT"
    else:
        _answers(waiting)
        result = _run(research_root, prepared, work_dir)
        receipt = copy.deepcopy(result["receipt"])
        receipt["result"]["corpus_record_count"] = 999
        with pytest.raises(ValidationError): validate_generic_execution(receipt, research_root)


def test_rehashed_illegal_recovery_event_is_rejected(tmp_path):
    research_root, _, prepared = prepared_handoff(tmp_path)
    _run(research_root, prepared, tmp_path / "work")
    path = _event_files(research_root, prepared)[0]
    start = json.loads(path.read_bytes())
    body = {key: value for key, value in start.items() if key not in {"event_id", "event_hash"}}
    body["detail"]["recovery"] = "RETRY"
    forged = seal_record(execution.EVENT_KIND, body, id_field="event_id", hash_field="event_hash")
    put_record(research_root, execution.EVENT_KIND, forged)
    path.write_bytes(canonical_dumps(forged))
    with pytest.raises(ValidationError, match="illegal attempt/invocation start"):
        _run(research_root, prepared, tmp_path / "work")


def test_handoff_and_direct_caller_share_native_work_dir_lock(tmp_path):
    research_root, _, prepared = prepared_handoff(tmp_path)
    work_dir = tmp_path / "work"
    with generic_work_dir_lock(work_dir):
        with pytest.raises(ValidationError, match="E-GENERIC-WORKDIR-LOCKED"):
            _run(research_root, prepared, work_dir)
    assert not _event_files(research_root, prepared)
    assert _run(research_root, prepared, work_dir)["status"] == "WAITING_FOR_AGENT"


def test_public_event_validation_replays_returns_and_rejects_start_or_wrong_handoff(tmp_path):
    research_root, source, prepared = prepared_handoff(tmp_path)
    work_dir = tmp_path / "work"
    waiting = _run(research_root, prepared, work_dir)
    assert validate_generic_execution_event(waiting["event_artifact_id"], prepared["handoff"], research_root)["state"] == "WAITING_FOR_AGENT"
    history = validate_generic_execution_history(prepared["handoff"], research_root)
    with pytest.raises(ValidationError, match="authoritative invocation return"):
        validate_generic_execution_event(history[0][0], prepared["handoff"], research_root)
    other = prepare_generic_handoff_from_input(
        handoff_input(research_root, source, goal="A different local research question"), research_root,
    )
    with pytest.raises(ValidationError, match="authoritative invocation return"):
        validate_generic_execution_event(waiting["event_artifact_id"], other["handoff"], research_root)
    _answers(waiting)
    completed = _run(research_root, prepared, work_dir)
    assert validate_generic_execution_event(completed["event_artifact_id"], prepared["handoff"], research_root)["state"] == "SUCCEEDED"
    corpus = next(work_dir.glob("generic-extraction/profiles/*/extractions/*/reductions/*/corpus.jsonl"))
    corpus.write_bytes(b"corrupt")
    with pytest.raises(ValidationError):
        validate_generic_execution_event(completed["event_artifact_id"], prepared["handoff"], research_root)
