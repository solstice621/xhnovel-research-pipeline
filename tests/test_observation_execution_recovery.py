"""Regression cases from the PR #17 concurrency/crash/budget review."""
from __future__ import annotations

import copy
import json
import pathlib
import subprocess
import sys
import threading

import pytest

from test_generic_extraction import CountingApiExecutor
from test_generic_handoff import NOW, handoff_input
from test_observation_campaign import record, setup_campaign
import xhnovel_pipeline.generic_handoff_execution as native
import xhnovel_pipeline.observation_campaign as campaign
from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.generic_extraction import generic_work_dir_lock
from xhnovel_pipeline.generic_handoff import prepare_generic_handoff_from_input
from xhnovel_pipeline.observation_common import get_record


@pytest.mark.parametrize("boundary", ["before_native", "after_native"])
def test_campaign_holds_native_ownership_through_return(tmp_path, monkeypatch, boundary):
    root, _, prepared, run = setup_campaign(tmp_path)
    work = tmp_path / "native"
    reached, release = threading.Event(), threading.Event()
    outcome = {}

    def wait_at_boundary():
        reached.set()
        assert release.wait(20), "direct caller did not finish"

    if boundary == "before_native":
        real = campaign.execute_generic_handoff
        def pause(*args, **kwargs):
            wait_at_boundary()
            return real(*args, **kwargs)
        monkeypatch.setattr(campaign, "execute_generic_handoff", pause)
    else:
        real = campaign._append
        def pause(plan, draft, *args, **kwargs):
            if draft["event_type"] == "EXECUTION_FINISHED":
                wait_at_boundary()
            return real(plan, draft, *args, **kwargs)
        monkeypatch.setattr(campaign, "_append", pause)

    def caller():
        try:
            outcome["result"] = campaign.execute_campaign_handoff(run.record, prepared["handoff_path"], root, work, now=NOW)
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=caller)
    thread.start()
    try:
        assert reached.wait(20), outcome
        with pytest.raises(ValidationError, match="handoff is already executing"):
            native.execute_generic_handoff(prepared["handoff_path"], root, work, now=NOW)
    finally:
        release.set()
        thread.join(20)
    assert not thread.is_alive() and "error" not in outcome, outcome
    assert outcome["result"]["status"] == "WAITING_FOR_AGENT"
    history = native.validate_generic_execution_history(prepared["handoff"], root)
    assert [e["state"] for _, e in history] == ["STARTED", "WAITING_FOR_AGENT"]
    report = campaign.validate_observation_research(run.record, root)
    reservation = report["execution_invocations"][0]["start"]
    assert get_record(root, history[0][1]["detail"]["campaign_start_artifact_id"]) == reservation


def test_prestart_crash_cannot_adopt_same_executor_external_call(tmp_path, monkeypatch):
    root, _, prepared, run = setup_campaign(tmp_path)
    work = tmp_path / "native"
    def crash(*args, **kwargs):
        raise KeyboardInterrupt("after reservation, before native start")
    with monkeypatch.context() as patch:
        patch.setattr(campaign, "execute_generic_handoff", crash)
        with pytest.raises(KeyboardInterrupt):
            campaign.execute_campaign_handoff(run.record, prepared["handoff_path"], root, work, now=NOW)
    assert not native.validate_generic_execution_history(prepared["handoff"], root)
    outside = native.execute_generic_handoff(prepared["handoff_path"], root, work, now=NOW)
    assert outside["status"] == "WAITING_FOR_AGENT"
    rejected = campaign.execute_campaign_handoff(run.record, prepared["handoff_path"], root, work, now=NOW)
    assert rejected["status"] == "FAILED_PRESTART"
    assert "receipt_artifact_id" not in rejected and "event_artifact_id" not in rejected
    report = campaign.validate_observation_research(run.record, root)
    assert report["counts"]["failed_execution_invocations"] == 1
    with pytest.raises(ValidationError, match="unrecorded native continuation"):
        campaign.execute_campaign_handoff(run.record, prepared["handoff_path"], root, work, now=NOW)
    assert campaign.validate_observation_research(run.record, root)["budget_used"] == report["budget_used"]


@pytest.mark.parametrize("full_budget", [1, 2])
def test_interrupted_retry_consumes_full_work_not_resume_budget(tmp_path, monkeypatch, full_budget):
    root, _, prepared, run = setup_campaign(tmp_path, max_full_work_attempts=full_budget, max_resume_invocations=0)
    work = tmp_path / "native"
    def crash(*args, **kwargs):
        raise KeyboardInterrupt("native interruption")
    with monkeypatch.context() as patch:
        patch.setattr(native, "run_generic_corpus_workflow", crash)
        with pytest.raises(KeyboardInterrupt):
            campaign.execute_campaign_handoff(run.record, prepared["handoff_path"], root, work, now=NOW)
    if full_budget == 1:
        with pytest.raises(ValidationError, match="full_work_attempts budget exhausted"):
            campaign.execute_campaign_handoff(run.record, prepared["handoff_path"], root, work, retry=True, now=NOW)
    else:
        result = campaign.execute_campaign_handoff(run.record, prepared["handoff_path"], root, work, retry=True, now=NOW)
        assert result["status"] == "WAITING_FOR_AGENT"
    report = campaign.validate_observation_research(run.record, root)
    assert report["budget_used"]["full_work_attempts"] == full_budget
    assert report["budget_used"]["resume_invocations"] == 0
    history = native.validate_generic_execution_history(prepared["handoff"], root)
    assert sorted({event["attempt_ordinal"] for _, event in history}) == list(range(1, full_budget + 1))
    assert [item["start"]["detail"]["invocation_kind"] for item in report["execution_invocations"]] == ["FULL_WORK", "RETRY"][:full_budget]


def test_prestart_crash_without_external_invocation_can_retry(tmp_path, monkeypatch):
    root, _, prepared, run = setup_campaign(tmp_path, max_resume_invocations=0)
    work = tmp_path / "native"
    def crash(*args, **kwargs): raise KeyboardInterrupt("prestart interruption")
    with monkeypatch.context() as patch:
        patch.setattr(campaign, "execute_generic_handoff", crash)
        with pytest.raises(KeyboardInterrupt):
            campaign.execute_campaign_handoff(run.record, prepared["handoff_path"], root, work, now=NOW)
    result = campaign.execute_campaign_handoff(run.record, prepared["handoff_path"], root, work, resume=True, now=NOW)
    assert result["status"] == "WAITING_FOR_AGENT"
    report = campaign.validate_observation_research(run.record, root)
    assert report["budget_used"]["full_work_attempts"] == 2
    assert report["budget_used"]["resume_invocations"] == 0


def test_owned_start_cannot_be_relabelled_prestart_failure(tmp_path, monkeypatch):
    root, _, prepared, run = setup_campaign(tmp_path)
    def crash(*args, **kwargs): raise KeyboardInterrupt("native interruption")
    monkeypatch.setattr(native, "run_generic_corpus_workflow", crash)
    with pytest.raises(KeyboardInterrupt):
        campaign.execute_campaign_handoff(run.record, prepared["handoff_path"], root, tmp_path / "native", now=NOW)
    events = campaign._history(run.record, root)
    aid, start = events[-1]
    forged = campaign._finish_draft(aid, start, None, now=NOW, error=OSError("pretend prestart error"))
    with pytest.raises(ValidationError, match="cannot hide an owned native invocation"):
        campaign._append(run.record, forged, events, root)


def test_conflicting_flags_and_exhausted_budget_have_no_work_dir_effects(tmp_path):
    root, _, prepared, run = setup_campaign(tmp_path, max_full_work_attempts=0)
    work = tmp_path / "native"
    for flags, message in (({"resume": True, "retry": True}, "choose resume or retry"), ({}, "budget")):
        with pytest.raises(ValidationError, match=message):
            campaign.execute_campaign_handoff(run.record, prepared["handoff_path"], root, work, now=NOW, **flags)
        assert not work.exists()
        report = campaign.validate_observation_research(run.record, root)
        assert report["counts"]["execution_invocations"] == 0


def test_executor_materialization_is_reserved_locked_and_audited(tmp_path, monkeypatch):
    import xhnovel_pipeline.generic_agent_files as agent
    root, _, prepared, run = setup_campaign(tmp_path)
    work = tmp_path / "native"
    def fail(*args, **kwargs):
        assert campaign._history(run.record, root)[-1][1]["event_type"] == "EXECUTION_STARTED"
        with pytest.raises(ValidationError, match="already using"):
            with generic_work_dir_lock(work): pass
        with pytest.raises(ValidationError, match="already executing"):
            with native.generic_handoff_lock(root / "executions" / prepared["handoff"]["handoff_id"]): pass
        raise OSError("README publication failed")
    monkeypatch.setattr(agent, "_write_immutable", fail)
    result = campaign.execute_campaign_handoff(run.record, prepared["handoff_path"], root, work, now=NOW)
    assert result["status"] == "FAILED_PRESTART"
    assert result["error"]["message"] == "README publication failed"
    assert not native.validate_generic_execution_history(prepared["handoff"], root)
    report = campaign.validate_observation_research(run.record, root)
    assert report["counts"]["failed_execution_invocations"] == 1


def test_handoff_lock_token_rejects_cross_thread_directory_copy_and_expiry(tmp_path):
    directory = tmp_path / "handoff"
    failures = []
    with native.generic_handoff_lock(directory) as token:
        with native.generic_handoff_lock(directory, lock_token=token) as nested:
            assert nested is token
        for target, supplied in ((tmp_path / "other", token), (directory, copy.copy(token))):
            with pytest.raises(ValidationError, match="invalid generic handoff lock token"):
                with native.generic_handoff_lock(target, lock_token=supplied): pass
        def reuse():
            try:
                with native.generic_handoff_lock(directory, lock_token=token): pass
            except ValidationError as exc:
                failures.append(exc.code)
        thread = threading.Thread(target=reuse)
        thread.start()
        thread.join(10)
        assert not thread.is_alive() and failures == ["E-GENERIC-HANDOFF-LOCK-TOKEN"]
    with pytest.raises(ValidationError, match="invalid generic handoff lock token"):
        with native.generic_handoff_lock(directory, lock_token=token): pass


def test_killed_native_writer_orphan_does_not_block_validation_or_continuation(tmp_path):
    root, _, prepared, _ = setup_campaign(tmp_path)
    work = tmp_path / "native"
    assert native.execute_generic_handoff(prepared["handoff_path"], root, work, now=NOW)["status"] == "WAITING_FOR_AGENT"
    script = """
import json, pathlib, sys
from xhnovel_pipeline import file_io
from xhnovel_pipeline.generic_handoff_execution import execute_generic_handoff
real = file_io.os.link
def pause(temporary, destination, *args, **kwargs):
    if pathlib.Path(destination).parent.name == 'events':
        print(json.dumps({'temporary':str(temporary), 'destination':str(destination)}), flush=True)
        sys.stdin.readline()
    return real(temporary, destination, *args, **kwargs)
file_io.os.link = pause
execute_generic_handoff(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]), now=sys.argv[4])
"""
    proc = subprocess.Popen([sys.executable, "-c", script, str(prepared["handoff_path"]), str(root), str(work), NOW],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    reached, lines = threading.Event(), []
    def read_line():
        lines.append(proc.stdout.readline())
        reached.set()
    reader = threading.Thread(target=read_line)
    reader.start()
    try:
        assert reached.wait(20) and lines[0], "child did not reach the publication boundary"
        paths = json.loads(lines[0])
        assert len(native.validate_generic_execution_history(prepared["handoff"], root)) == 2
        proc.kill()  # SIGKILL on POSIX; forced process termination on Windows.
        proc.wait(timeout=10)
        assert proc.returncode != 0
        assert pathlib.Path(paths["temporary"]).is_file()
        assert not pathlib.Path(paths["destination"]).exists()
        assert len(native.validate_generic_execution_history(prepared["handoff"], root)) == 2
        assert native.execute_generic_handoff(prepared["handoff_path"], root, work, now=NOW)["status"] == "WAITING_FOR_AGENT"
        assert pathlib.Path(paths["temporary"]).is_file()  # No hidden repair/cleanup needed.
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
        reader.join(10)
        for stream in (proc.stdin, proc.stdout, proc.stderr): stream.close()


@pytest.mark.parametrize("junk", ["unexpected.json", "000003.json"])
def test_published_json_still_fails_closed(tmp_path, junk):
    root, _, prepared, _ = setup_campaign(tmp_path)
    native.execute_generic_handoff(prepared["handoff_path"], root, tmp_path / "native", now=NOW)
    directory = root / "executions" / prepared["handoff"]["handoff_id"] / "events"
    (directory / ".DS_Store").write_bytes(b"harmless OS metadata")
    assert len(native.validate_generic_execution_history(prepared["handoff"], root)) == 2
    (directory / junk).write_bytes(b"invalid JSON")
    with pytest.raises(ValidationError):
        native.validate_generic_execution_history(prepared["handoff"], root)


@pytest.mark.parametrize("statuses", [("SUCCEEDED", "INTERRUPTED"), ("FAILED", "SUCCEEDED"), ("SUCCEEDED", "HANDOFF_READY")])
def test_report_aggregates_distinct_handoffs_without_losing_outcomes(tmp_path, monkeypatch, statuses):
    root, _, first, run = setup_campaign(tmp_path)
    source = tmp_path / "second.txt"
    source.write_text("第一章 山门\n人族居住在青云山。\n", encoding="utf-8")
    second = prepare_generic_handoff_from_input(handoff_input(root, source), root)
    refs = second["handoff"]["builder"]
    start = record(run, root, "second:start", "SOURCE_STARTED", {"lead_artifact_ids":refs["work_lead_artifact_ids"], "source_input_artifact_id":refs["source_declaration_artifact_id"]})
    record(run, root, "second:finish", "SOURCE_FINISHED", {"start_event_artifact_id":start.artifact_id, "status":"ELIGIBLE", "handoff_artifact_id":second["handoff_artifact_id"], "reason":"second authorized source"})
    for index, (prepared, status) in enumerate(zip((first, second), statuses)):
        if status == "HANDOFF_READY":
            continue
        with monkeypatch.context() as patch:
            if status != "SUCCEEDED":
                def fail(*args, **kwargs):
                    if status == "INTERRUPTED": raise KeyboardInterrupt("fixture interruption")
                    raise OSError("fixture source/runtime failure")
                patch.setattr(native, "run_generic_corpus_workflow", fail)
            opts = dict(executor_kind="api", executor=CountingApiExecutor(lambda _: {"records": []}), now=NOW)
            if status == "INTERRUPTED":
                with pytest.raises(KeyboardInterrupt):
                    campaign.execute_campaign_handoff(run.record, prepared["handoff_path"], root, tmp_path / f"native-{index}", **opts)
            else:
                assert campaign.execute_campaign_handoff(run.record, prepared["handoff_path"], root, tmp_path / f"native-{index}", **opts)["status"] == status
    report = campaign.validate_observation_research(run.record, root)
    assert report["leads"][0]["execution"] == "MIXED"
    assert report["leads"][0]["execution_statuses"] == sorted(statuses)
    assert report["works"][0]["execution_statuses"] == sorted(statuses)
    assert report["counts"]["successful_works"] == 1
    assert report["counts"]["failed_execution_invocations"] == int("FAILED" in statuses)
