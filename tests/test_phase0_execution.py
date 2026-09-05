from __future__ import annotations

import copy
import json
import subprocess
import sys

import pytest

from xhnovel_pipeline import cli, phase0_execution
from xhnovel_pipeline.agent_files import (
    AGENT_FILES_EXECUTOR_KIND,
    AgentFileExecutor,
    AgentResponsesPending,
)
from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.phase0_builder import prepare_handoff_from_input
from xhnovel_pipeline.phase0_execution import (
    execute_evidence_handoff,
    validate_handoff_execution_history,
    verify_handoff_execution,
)
from xhnovel_pipeline.paths import repo_root

from test_agent_files_integration import _candidate
from test_novel_workflow import _client, _marker_transport
from test_phase0_builder import _input, _reseal_handoff, _write_input

NOW = "2026-09-02T00:00:00Z"
LATER = "2026-09-02T00:01:00Z"
LATEST = "2026-09-02T00:02:00Z"


class _StubAgentClient:
    executor_kind = AGENT_FILES_EXECUTOR_KIND


def _prepare(tmp_path, name="phase0", *, requested_at=NOW):
    source = tmp_path / f"{name}.txt"
    source.write_text(
        "第一章 天门\n林舟触发天门机关，山路随之开启。",
        encoding="utf-8",
    )
    value = _input(source, requested_at=requested_at)
    return prepare_handoff_from_input(
        _write_input(tmp_path, value, f"{name}-prepare.json"),
        tmp_path / name,
    )


def _agent_factory(work_dir):
    return lambda: AgentFileExecutor(work_dir / "scene-scout" / "agent-files")


def _answer_all(work_dir):
    task_dir = work_dir / "scene-scout" / "agent-files" / "tasks"
    task_paths = list(task_dir.glob("*.json"))
    assert task_paths
    for task_path in task_paths:
        task = json.loads(task_path.read_text(encoding="utf-8"))
        span = task["input"]["window"]["source_spans"][0]
        answer_path = task_path.parents[1] / task["answer_file"]
        answer_path.parent.mkdir(parents=True, exist_ok=True)
        answer_path.write_text(
            json.dumps(
                {"candidates": [_candidate(span)]},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def _complete_agent_execution(prepared, work_dir):
    with pytest.raises(AgentResponsesPending):
        execute_evidence_handoff(
            prepared.handoff_path,
            work_dir,
            executor="agent-files",
            extractor_factory=_agent_factory(work_dir),
            repo_root=repo_root(),
            now=NOW,
        )
    _answer_all(work_dir)
    return execute_evidence_handoff(
        prepared.handoff_path,
        work_dir,
        executor="agent-files",
        extractor_factory=_agent_factory(work_dir),
        repo_root=repo_root(),
        now=LATER,
    )


def _replace_visible_spec(prepared, *, source_path, discovery_brief):
    visible = json.loads(prepared.novel_spec_path.read_text(encoding="utf-8"))
    visible["source"]["path"] = str(source_path)
    visible["request"]["discovery_brief"] = discovery_brief
    prepared.novel_spec_path.write_text(
        json.dumps(visible, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_agent_files_execution_uses_cas_spec_after_visible_copy_swap(tmp_path, monkeypatch):
    prepared = _prepare(tmp_path)
    work_dir = tmp_path / "cas-agent-work"
    tainted_source = tmp_path / "tainted-agent.txt"
    tainted_source.write_text(
        "第一章 污染输入\nTHIS_TAINTED_SOURCE_MUST_NOT_REACH_A_TASK。",
        encoding="utf-8",
    )
    tainted_brief = "THIS_TAINTED_BRIEF_MUST_NOT_REACH_A_TASK"
    visible_bytes = prepared.novel_spec_path.read_bytes()
    original_resolver = phase0_execution.resolve_validated_handoff_input

    def resolve_then_swap(*args, **kwargs):
        resolved = original_resolver(*args, **kwargs)
        _replace_visible_spec(
            prepared,
            source_path=tainted_source,
            discovery_brief=tainted_brief,
        )
        return resolved

    monkeypatch.setattr(
        phase0_execution,
        "resolve_validated_handoff_input",
        resolve_then_swap,
    )
    try:
        with pytest.raises(AgentResponsesPending):
            execute_evidence_handoff(
                prepared.handoff_path,
                work_dir,
                executor="agent-files",
                extractor_factory=_agent_factory(work_dir),
                repo_root=repo_root(),
                now=NOW,
            )
    finally:
        prepared.novel_spec_path.write_bytes(visible_bytes)

    task_paths = sorted(
        (work_dir / "scene-scout" / "agent-files" / "tasks").glob("*.json")
    )
    assert task_paths
    task_text = "\n".join(path.read_text(encoding="utf-8") for path in task_paths)
    assert "THIS_TAINTED_SOURCE_MUST_NOT_REACH_A_TASK" not in task_text
    assert tainted_brief not in task_text
    assert "林舟触发天门机关" in task_text
    assert prepared.novel_spec["request"]["discovery_brief"] in task_text
    assert validate_handoff_execution_history(prepared.handoff_path)[0].state == (
        "WAITING_FOR_AGENT"
    )


def test_api_execution_uses_cas_spec_before_transport_after_visible_copy_swap(
    tmp_path,
    monkeypatch,
):
    prepared = _prepare(tmp_path)
    work_dir = tmp_path / "cas-api-work"
    tainted_source = tmp_path / "tainted-api.txt"
    tainted_source.write_text(
        "第一章 污染外发\nTHIS_TAINTED_SOURCE_MUST_NOT_REACH_API_TRANSPORT。",
        encoding="utf-8",
    )
    tainted_brief = "THIS_TAINTED_BRIEF_MUST_NOT_REACH_API_TRANSPORT"
    visible_bytes = prepared.novel_spec_path.read_bytes()
    original_resolver = phase0_execution.resolve_validated_handoff_input

    def resolve_then_swap(*args, **kwargs):
        resolved = original_resolver(*args, **kwargs)
        _replace_visible_spec(
            prepared,
            source_path=tainted_source,
            discovery_brief=tainted_brief,
        )
        return resolved

    monkeypatch.setattr(
        phase0_execution,
        "resolve_validated_handoff_input",
        resolve_then_swap,
    )
    model_inputs = []
    delegate = _marker_transport()

    def capture_transport(url, headers, body, timeout):
        model_inputs.append(json.loads(json.loads(body)["input"]))
        return delegate(url, headers, body, timeout)

    try:
        completed = execute_evidence_handoff(
            prepared.handoff_path,
            work_dir,
            executor="api",
            extractor_factory=lambda: _client(capture_transport),
            repo_root=repo_root(),
            now=NOW,
        )
    finally:
        prepared.novel_spec_path.write_bytes(visible_bytes)

    assert completed.status == "SUCCEEDED"
    assert model_inputs
    assert {item["discovery_brief"] for item in model_inputs} == {
        prepared.novel_spec["request"]["discovery_brief"]
    }
    sent_text = "\n".join(
        span["untrusted_text"]
        for item in model_inputs
        for span in item["window"]["source_spans"]
    )
    assert "THIS_TAINTED_SOURCE_MUST_NOT_REACH_API_TRANSPORT" not in sent_text
    assert "林舟触发天门机关" in sent_text


def test_agent_files_two_pass_resumes_same_attempt_and_success_is_idempotent(
    tmp_path,
    monkeypatch,
):
    prepared = _prepare(tmp_path)
    work_dir = tmp_path / "research"

    with pytest.raises(AgentResponsesPending):
        execute_evidence_handoff(
            prepared.handoff_path,
            work_dir,
            executor="agent-files",
            extractor_factory=_agent_factory(work_dir),
            repo_root=repo_root(),
            now=NOW,
        )

    pending = validate_handoff_execution_history(prepared.handoff_path)
    assert len(pending) == 1
    assert pending[0].state == "WAITING_FOR_AGENT"
    assert [event["state"] for event in pending[0].events] == [
        "STARTED",
        "WAITING_FOR_AGENT",
    ]
    assert pending[0].receipt is None
    attempt_id = pending[0].attempt_id
    assert not list(
        (prepared.phase0_root / "executions" / prepared.handoff["handoff_id"] / "receipts").glob(
            "*.json"
        )
    )

    _answer_all(work_dir)
    completed = execute_evidence_handoff(
        prepared.handoff_path,
        work_dir,
        executor="agent-files",
        extractor_factory=_agent_factory(work_dir),
        repo_root=repo_root(),
        now=LATER,
    )
    assert completed.status == "SUCCEEDED"
    assert completed.attempt_id == attempt_id
    assert completed.attempt_ordinal == 1
    assert completed.receipt["actual_input_spec_hash"] == prepared.handoff["novel_spec"][
        "expected_input_spec_hash"
    ]
    assert completed.receipt["validate_all"] == "PASS"

    def must_not_run(*args, **kwargs):
        raise AssertionError("native research must not rerun after SUCCEEDED")

    monkeypatch.setattr(phase0_execution, "run_novel_research", must_not_run)
    repeated = execute_evidence_handoff(
        prepared.handoff_path,
        work_dir,
        executor="agent-files",
        extractor_factory=lambda: (_ for _ in ()).throw(
            AssertionError("executor must not be constructed after SUCCEEDED")
        ),
        repo_root=repo_root(),
        now=LATEST,
    )
    assert repeated.reused_terminal_receipt is True
    assert repeated.receipt == completed.receipt
    assert len(validate_handoff_execution_history(prepared.handoff_path)) == 1


def test_execute_handoff_cli_maps_pending_to_exit_3_and_success_to_exit_0(
    tmp_path,
    monkeypatch,
    capsys,
):
    prepared = _prepare(tmp_path)
    work_dir = tmp_path / "cli-research"
    monkeypatch.setattr(cli, "utc_now", lambda: NOW)
    args = [
        "execute-handoff",
        str(prepared.handoff_path),
        "--executor",
        "agent-files",
        "--work-dir",
        str(work_dir),
    ]

    assert cli.main(args) == 3
    first = capsys.readouterr()
    assert "WAITING_FOR_AGENT" in first.err
    history = validate_handoff_execution_history(prepared.handoff_path)
    assert history[0].state == "WAITING_FOR_AGENT"
    assert history[0].receipt is None

    _answer_all(work_dir)
    monkeypatch.setattr(cli, "utc_now", lambda: LATER)
    assert cli.main(args) == 0
    second = capsys.readouterr()
    assert "status=SUCCEEDED" in second.out
    receipt_path = second.out.strip().splitlines()[-1]
    assert receipt_path.endswith(".json")
    assert history[0].attempt_id == validate_handoff_execution_history(
        prepared.handoff_path
    )[0].attempt_id


def test_api_executor_completes_as_one_started_to_succeeded_attempt(tmp_path):
    prepared = _prepare(tmp_path)
    work_dir = tmp_path / "api-research"
    completed = execute_evidence_handoff(
        prepared.handoff_path,
        work_dir,
        executor="api",
        extractor_factory=lambda: _client(_marker_transport()),
        repo_root=repo_root(),
        now=NOW,
    )
    assert completed.status == "SUCCEEDED"
    history = validate_handoff_execution_history(prepared.handoff_path)
    assert len(history) == 1
    assert history[0].state == "SUCCEEDED"
    assert [event["state"] for event in history[0].events] == ["STARTED"]
    assert history[0].receipt["executor"] == "api"


def test_failed_attempt_requires_explicit_retry_and_preserves_both_receipts(
    tmp_path,
    monkeypatch,
):
    prepared = _prepare(tmp_path)
    work_dir = tmp_path / "failed-research"
    calls = []

    def fail_native(*args, **kwargs):
        calls.append(1)
        raise ValidationError("E-NOVEL-TEST-FAILURE", "native failure")

    monkeypatch.setattr(phase0_execution, "run_novel_research", fail_native)
    invoke = lambda retry=False: execute_evidence_handoff(
        prepared.handoff_path,
        work_dir,
        executor="agent-files",
        extractor_factory=_StubAgentClient,
        repo_root=repo_root(),
        now=NOW if not retry else LATER,
        retry=retry,
    )

    with pytest.raises(ValidationError, match="E-NOVEL-TEST-FAILURE"):
        invoke()
    first = validate_handoff_execution_history(prepared.handoff_path)
    assert [attempt.state for attempt in first] == ["FAILED"]
    assert first[0].receipt["stage"] == "INGESTION"
    assert len(calls) == 1

    with pytest.raises(ValidationError, match="E-HANDOFF-RETRY-REQUIRED"):
        invoke()
    assert len(calls) == 1

    with pytest.raises(ValidationError, match="E-NOVEL-TEST-FAILURE"):
        invoke(retry=True)
    retried = validate_handoff_execution_history(prepared.handoff_path)
    assert [attempt.attempt_ordinal for attempt in retried] == [1, 2]
    assert [attempt.state for attempt in retried] == ["FAILED", "FAILED"]
    assert retried[0].attempt_id != retried[1].attempt_id
    assert retried[0].receipt_path.read_bytes() != retried[1].receipt_path.read_bytes()


def test_started_only_is_interrupted_and_is_not_silently_resumed(tmp_path, monkeypatch):
    prepared = _prepare(tmp_path)
    work_dir = tmp_path / "interrupted-research"

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(phase0_execution, "run_novel_research", interrupt)
    with pytest.raises(KeyboardInterrupt):
        execute_evidence_handoff(
            prepared.handoff_path,
            work_dir,
            executor="agent-files",
            extractor_factory=_StubAgentClient,
            repo_root=repo_root(),
            now=NOW,
        )
    history = validate_handoff_execution_history(prepared.handoff_path)
    assert len(history) == 1
    assert history[0].state == "INTERRUPTED"
    assert history[0].receipt is None

    with pytest.raises(ValidationError, match="E-HANDOFF-ATTEMPT-INTERRUPTED"):
        execute_evidence_handoff(
            prepared.handoff_path,
            work_dir,
            executor="agent-files",
            extractor_factory=_StubAgentClient,
            repo_root=repo_root(),
            now=LATER,
        )


def test_marker_and_receipt_tamper_are_rejected(tmp_path, monkeypatch):
    marker_prepared = _prepare(tmp_path, "marker-phase0")
    marker_work = tmp_path / "marker-work"
    monkeypatch.setattr(
        phase0_execution,
        "run_novel_research",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        execute_evidence_handoff(
            marker_prepared.handoff_path,
            marker_work,
            executor="agent-files",
            extractor_factory=_StubAgentClient,
            repo_root=repo_root(),
            now=NOW,
        )
    marker_path = next(
        (
            marker_prepared.phase0_root
            / "executions"
            / marker_prepared.handoff["handoff_id"]
            / "started-markers"
        ).glob("*.json")
    )
    marker_path.write_bytes(marker_path.read_bytes().replace(b'"STARTED"', b'"TAMPERED"'))
    with pytest.raises(ValidationError, match="E-HANDOFF-ATTEMPT"):
        validate_handoff_execution_history(marker_prepared.handoff_path)

    receipt_prepared = _prepare(tmp_path, "receipt-phase0", requested_at=LATER)
    receipt_work = tmp_path / "receipt-work"
    monkeypatch.setattr(
        phase0_execution,
        "run_novel_research",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValidationError("E-NOVEL-FAIL", "failure")
        ),
    )
    with pytest.raises(ValidationError, match="E-NOVEL-FAIL"):
        execute_evidence_handoff(
            receipt_prepared.handoff_path,
            receipt_work,
            executor="agent-files",
            extractor_factory=_StubAgentClient,
            repo_root=repo_root(),
            now=NOW,
        )
    receipt_path = next(
        (
            receipt_prepared.phase0_root
            / "executions"
            / receipt_prepared.handoff["handoff_id"]
            / "receipts"
        ).glob("*.json")
    )
    receipt_path.write_bytes(receipt_path.read_bytes().replace(b'"FAILED"', b'"TAMPERED"'))
    with pytest.raises(ValidationError, match="E-HANDOFF"):
        validate_handoff_execution_history(receipt_prepared.handoff_path)


def test_pending_attempt_rejects_handoff_executor_and_work_dir_changes(tmp_path):
    prepared = _prepare(tmp_path)
    work_dir = tmp_path / "bound-work"
    with pytest.raises(AgentResponsesPending):
        execute_evidence_handoff(
            prepared.handoff_path,
            work_dir,
            executor="agent-files",
            extractor_factory=_agent_factory(work_dir),
            repo_root=repo_root(),
            now=NOW,
        )
    original_handoff = prepared.handoff_path.read_bytes()
    tampered = copy.deepcopy(prepared.handoff)
    tampered["source_ref"]["edition_label"] = "tampered"
    _reseal_handoff(tampered)
    prepared.handoff_path.write_text(
        json.dumps(tampered, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="E-PHASE0-HANDOFF-REPLAY"):
        execute_evidence_handoff(
            prepared.handoff_path,
            work_dir,
            executor="agent-files",
            extractor_factory=_agent_factory(work_dir),
            repo_root=repo_root(),
            now=LATER,
        )
    prepared.handoff_path.write_bytes(original_handoff)

    factory_called = False

    def should_not_construct():
        nonlocal factory_called
        factory_called = True
        return _StubAgentClient()

    with pytest.raises(ValidationError, match="E-HANDOFF-ATTEMPT-IDENTITY"):
        execute_evidence_handoff(
            prepared.handoff_path,
            work_dir,
            executor="api",
            extractor_factory=should_not_construct,
            repo_root=repo_root(),
            now=LATER,
        )
    with pytest.raises(ValidationError, match="E-HANDOFF-ATTEMPT-IDENTITY"):
        execute_evidence_handoff(
            prepared.handoff_path,
            tmp_path / "different-work",
            executor="agent-files",
            extractor_factory=should_not_construct,
            repo_root=repo_root(),
            now=LATER,
        )
    assert factory_called is False
    assert len(validate_handoff_execution_history(prepared.handoff_path)) == 1


def test_spec_hash_and_unique_catalog_lineage_are_required_and_fail_terminally(
    tmp_path,
    monkeypatch,
):
    prepared = _prepare(tmp_path, "successful-phase0")
    completed = _complete_agent_execution(prepared, tmp_path / "successful-work")
    assert completed.native_result is not None
    native = completed.native_result

    wrong_hash = copy.deepcopy(prepared.handoff)
    wrong_hash["novel_spec"]["expected_input_spec_hash"] = "sha256:" + "f" * 64
    with pytest.raises(ValidationError, match="E-HANDOFF-SPEC-HASH"):
        verify_handoff_execution(
            wrong_hash,
            native["catalog"],
            native["store"],
            attempt_id=completed.attempt_id,
            attempt_ordinal=completed.attempt_ordinal,
            executor="agent-files",
            recorded_at=LATER,
        )

    mismatched_prepared = _prepare(tmp_path, "mismatched-spec-phase0", requested_at=LATEST)
    monkeypatch.setattr(phase0_execution, "run_novel_research", lambda *args, **kwargs: native)
    with pytest.raises(ValidationError, match="E-HANDOFF-SPEC-HASH"):
        execute_evidence_handoff(
            mismatched_prepared.handoff_path,
            tmp_path / "mismatched-spec-work",
            executor="agent-files",
            extractor_factory=_StubAgentClient,
            repo_root=repo_root(),
            now=LATEST,
        )
    mismatch_history = validate_handoff_execution_history(mismatched_prepared.handoff_path)
    assert mismatch_history[0].state == "FAILED"
    assert mismatch_history[0].receipt["stage"] == "VALIDATION"
    assert mismatch_history[0].receipt["error_code"] == "E-HANDOFF-SPEC-HASH"

    native["catalog"].by_type["EvidenceExport"].clear()
    invalid_prepared = prepare_handoff_from_input(
        _write_input(
            tmp_path,
            _input(tmp_path / "successful-phase0.txt", requested_at=LATEST),
            "invalid-lineage-prepare.json",
        ),
        tmp_path / "invalid-lineage-phase0",
    )
    with pytest.raises(ValidationError, match="E-HANDOFF-LINEAGE"):
        execute_evidence_handoff(
            invalid_prepared.handoff_path,
            tmp_path / "invalid-lineage-work",
            executor="agent-files",
            extractor_factory=_StubAgentClient,
            repo_root=repo_root(),
            now=LATEST,
        )
    failed = validate_handoff_execution_history(invalid_prepared.handoff_path)
    assert failed[0].state == "FAILED"
    assert failed[0].receipt["stage"] == "VALIDATION"
    assert failed[0].receipt["error_code"] == "E-HANDOFF-LINEAGE"


def test_success_receipt_validates_in_a_fresh_process(tmp_path):
    prepared = _prepare(tmp_path)
    completed = _complete_agent_execution(prepared, tmp_path / "fresh-work")
    script = (
        "import pathlib,sys; "
        "from xhnovel_pipeline.phase0_execution import validate_handoff_execution_history; "
        "history=validate_handoff_execution_history(pathlib.Path(sys.argv[1])); "
        "print(history[-1].state)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(prepared.handoff_path)],
        cwd=repo_root(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "SUCCEEDED"
    assert completed.receipt_path.is_file()


def test_success_receipt_replay_rejects_unknown_empty_catalog_kind(tmp_path):
    prepared = _prepare(tmp_path)
    work_dir = tmp_path / "strict-replay-work"
    completed = _complete_agent_execution(prepared, work_dir)
    catalog_path = (
        work_dir
        / "research"
        / completed.receipt["scene_scout_run_id"]
        / "catalog.json"
    )
    catalog_json = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog_json["UnknownInjectedKind"] = []
    catalog_path.write_text(
        json.dumps(catalog_json, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="E-CATALOG-KIND"):
        cli._catalog_from_json(catalog_path)
    public_validation = subprocess.run(
        [
            sys.executable,
            "-m",
            "xhnovel_pipeline.cli",
            "validate",
            "all",
            str(catalog_path),
            "--store",
            str(work_dir / "ingestion" / "objects"),
        ],
        cwd=repo_root(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert public_validation.returncode == 1
    assert "E-CATALOG-KIND" in public_validation.stderr
    with pytest.raises(ValidationError, match="E-CATALOG-KIND"):
        validate_handoff_execution_history(prepared.handoff_path)


def test_execution_lock_rejects_concurrent_entry_on_the_current_platform(tmp_path):
    prepared = _prepare(tmp_path)
    work_dir = tmp_path / "locked-work"
    execution_dir = (
        prepared.phase0_root / "executions" / prepared.handoff["handoff_id"]
    )
    with phase0_execution._exclusive_execution(execution_dir):
        with pytest.raises(ValidationError, match="E-HANDOFF-EXECUTION-LOCKED"):
            execute_evidence_handoff(
                prepared.handoff_path,
                work_dir,
                executor="agent-files",
                extractor_factory=_StubAgentClient,
                repo_root=repo_root(),
                now=NOW,
            )
    assert not list((execution_dir / "started-markers").glob("*.json"))


def test_selected_work_without_leads_runs_and_resumes_native_handoff(tmp_path):
    source = tmp_path / "selected.txt"
    source.write_text("第一章 天门\n林舟触发天门机关，山路随之开启。", encoding="utf-8")
    draft = _input(source)
    del draft["leads"]
    prepared = prepare_handoff_from_input(_write_input(tmp_path, draft), tmp_path / "phase0")
    work = tmp_path / "native"
    completed = _complete_agent_execution(prepared, work)
    assert completed.status == "SUCCEEDED"
    history = validate_handoff_execution_history(prepared.handoff_path, phase0_root=tmp_path / "phase0")
    assert len(history) == 1
    assert history[0].state == "SUCCEEDED"
    assert any(event["state"] == "WAITING_FOR_AGENT" for event in history[0].events)
    assert history[0].receipt["expected_input_spec_hash"] == history[0].receipt["actual_input_spec_hash"]
    assert history[0].receipt["validate_all"] == "PASS"
    assert prepared.handoff["motivating_lead_ids"] == []
