"""Acquired sources continue through the native observation campaign boundary."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_source_acquisition import acq, fixture_config, mutate_config, reviewed, write_json
from test_generic_handoff_execution import _answers
from test_observation_campaign import campaign_draft, record
from test_observation_planning import NOW, planning_stack
from xhnovel_pipeline.generic_handoff import resolve_generic_handoff
from xhnovel_pipeline.hashing import object_hash
from xhnovel_pipeline.observation_campaign import (
    execute_campaign_handoff, init_observation_research,
    report_observation_research, validate_observation_research,
)
from xhnovel_pipeline.observation_common import research_store


def source_attempt(tmp_path):
    config, inputs = fixture_config(tmp_path, count=1)
    mutate_config(config, work={"title": "Fixture Novel", "author": "Fixture Author", "language": "zh"})
    (inputs / "0001.txt").write_text("第1章 合成1\n\n人族居住在青云山。\n", encoding="utf-8")
    research = tmp_path / "research"
    definition, resolution, lead = planning_stack(research)
    planning = write_json(tmp_path / "observation-input.json", {
        "format_version": acq.FORMAT,
        "definition_artifact_id": definition.artifact_id,
        "resolution_artifact_id": resolution.artifact_id,
        "work_lead_artifact_ids": [lead.artifact_id], "requested_at": NOW,
    })
    campaign = init_observation_research(
        campaign_draft(definition.artifact_id, resolution.artifact_id, research), research,
    )
    record(campaign, research, "lead", "LEAD_RECORDED", {
        "lead_artifact_id": lead.artifact_id, "search_event_artifact_id": None,
    })
    start = record(campaign, research, "source:start", "SOURCE_STARTED", {
        "lead_artifact_ids": [lead.artifact_id],
        "source_input_artifact_id": research_store(research).put(config.read_bytes()),
    })
    # Acquisition happens inside the already recorded source attempt.
    run = acq.Run.initialize(config)
    return run, inputs, planning, research, campaign, start


def sealed_attempt(tmp_path):
    run, inputs, planning, research, campaign, start = source_attempt(tmp_path)
    run.import_local(inputs)
    sealed = acq.seal(run, tmp_path / "sealed", reviewed(run, tmp_path))
    return sealed, planning, research, campaign, start


def test_acquisition_flows_through_generic_campaign_and_preserves_ingestion(tmp_path, capsys):
    sealed, planning, research, campaign, start = sealed_attempt(tmp_path)
    assert acq.main(["prepare-generic", str(sealed), str(planning), "--research-root", str(research)]) == 0
    prepared = json.loads(capsys.readouterr().out)
    assert acq.prepare_generic_source(sealed, planning, research) == prepared
    resolved = resolve_generic_handoff(Path(prepared["handoff_path"]), research)
    spec = resolved.execution_spec
    assert set(spec) == {"source", "rights", "source_quality", "limits", "strict_order"}
    assert spec["source"]["path"] == str(sealed / "chapters")
    assert object_hash(spec, omit=()) == prepared["expected_input_spec_hash"]
    assert prepared["native_freeze"] == prepared["research"] == "NOT_RUN"
    assert (research / "operator-attestation.json").read_bytes() == (sealed / "provenance/run/operator-attestation.json").read_bytes()
    work = tmp_path / "native"
    assert acq.main([
        "freeze-generic", str(sealed), prepared["handoff_path"],
        "--research-root", str(research), "--work-dir", str(work),
    ]) == 0
    frozen = json.loads(capsys.readouterr().out)
    assert frozen["research"] == "NOT_RUN"
    record(campaign, research, "source:finish", "SOURCE_FINISHED", {
        "start_event_artifact_id": start.artifact_id, "status": "ELIGIBLE",
        "handoff_artifact_id": prepared["handoff_artifact_id"], "reason": "Acquired synthetic source verified and frozen.",
    })
    waiting = execute_campaign_handoff(campaign.record, prepared["handoff_path"], research, work, now=NOW)
    assert waiting["status"] == "WAITING_FOR_AGENT"
    for pending in waiting["pending"]:
        task = json.loads(Path(pending["task"]).read_bytes())
        assert task["profile_id"] == "xhnovel.race-mention"
        assert "Fixture Novel" not in json.dumps(task["input"], ensure_ascii=False)
    _answers(waiting, nonempty=True)
    completed = execute_campaign_handoff(campaign.record, prepared["handoff_path"], research, work, now=NOW)
    assert completed["status"] == "SUCCEEDED"
    ingestions = list((work / "ingestion/ingestions").glob("*/novel-ingestion.json"))
    assert len(ingestions) == 1
    assert acq.read_json(ingestions[0])["ingestion_run_id"] == frozen["ingestion_run_id"]
    record(campaign, research, "stop", "STOP", {"reason": "TARGET_WORKS_REACHED", "rationale": "Synthetic full source completed."})
    report = report_observation_research(campaign.record, research)
    assert report["counts"]["successful_works"] == report["counts"]["corpus_record_count_sum"] == 1
    assert report["budget_used"]["source_attempts"] == 1
    assert validate_observation_research(campaign.path, research, report_or_path=report) == report


def test_missing_source_is_recorded_without_handoff_or_semantic_execution(tmp_path):
    run, inputs, planning, research, campaign, start = source_attempt(tmp_path)
    (inputs / "0001.txt").unlink()
    assert run.import_local(inputs)["accepted_entries"] == 0
    with pytest.raises(acq.AcquisitionError, match="NOT-READY"):
        acq.seal(run, tmp_path / "sealed", reviewed(run, tmp_path))
    record(campaign, research, "source:finish", "SOURCE_FINISHED", {
        "start_event_artifact_id": start.artifact_id, "status": "UNRESOLVED",
        "handoff_artifact_id": None, "reason": "Fixed source catalog has a missing chapter.",
    })
    report = report_observation_research(campaign.record, research)
    assert report["counts"]["failed_source_attempts"] == 1
    assert report["leads"][0]["source"] == "UNRESOLVED"
    assert not (research / "handoffs").exists()
    assert not (tmp_path / "native").exists()


@pytest.mark.parametrize("field", ["source_declaration", "rights", "request", "profile", "chapter_scope", "location_hints"])
def test_generic_bridge_rejects_source_overrides_and_semantic_fields(tmp_path, field):
    sealed, planning, research, _, _ = sealed_attempt(tmp_path)
    options = acq.read_json(planning)
    options[field] = "search-derived override"
    write_json(planning, options)
    with pytest.raises(acq.ValidationError, match="invalid field set"):
        acq.prepare_generic_source(sealed, planning, research)
    assert not (research / "handoffs").exists()


def test_generic_bridge_preserves_conflicting_standing_attestation(tmp_path):
    sealed, planning, research, _, _ = sealed_attempt(tmp_path)
    original = b'An existing conflicting standing declaration must be preserved.\n'
    (research / "operator-attestation.json").write_bytes(original)
    with pytest.raises(acq.ValidationError):
        acq.prepare_generic_source(sealed, planning, research)
    assert (research / "operator-attestation.json").read_bytes() == original
    assert not (research / "handoffs").exists()


def test_generic_source_change_before_freeze_cannot_reach_native_tasks(tmp_path):
    sealed, planning, research, _, _ = sealed_attempt(tmp_path)
    prepared = acq.prepare_generic_source(sealed, planning, research)
    chapter = next((sealed / "chapters").glob("*.txt"))
    chapter.write_bytes(chapter.read_bytes() + b'changed')
    with pytest.raises(acq.AcquisitionError, match="sealed files differ"):
        acq.freeze_generic_source(sealed, Path(prepared["handoff_path"]), research, tmp_path / "native")
    assert not (tmp_path / "native").exists()


def test_generic_planning_references_must_exist_in_native_cas(tmp_path):
    sealed, planning, research, _, _ = sealed_attempt(tmp_path)
    options = acq.read_json(planning)
    options["definition_artifact_id"] = acq.artifact_id_for(b'missing neutral definition')
    write_json(planning, options)
    with pytest.raises(acq.ValidationError):
        acq.prepare_generic_source(sealed, planning, research)
    assert not (research / "handoffs").exists()
