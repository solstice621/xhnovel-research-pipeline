from __future__ import annotations

import copy

import pytest

from test_generic_handoff import NOW, prepared_handoff
from test_generic_handoff_execution import _answers
from test_observation_planning import definition_draft, resolution_draft
from xhnovel_pipeline.errors import SchemaError, ValidationError
from xhnovel_pipeline.observation_common import get_record, research_store
from xhnovel_pipeline.observation_planning import seal_observation_definition_from_draft, seal_profile_resolution_from_draft
from xhnovel_pipeline.observation_campaign import (
    _campaign_lock, execute_campaign_handoff, init_observation_research,
    record_observation_research_event, report_observation_research, validate_observation_research,
)


def campaign_draft(definition_aid, resolution_aid, research_root, **budget):
    definition = get_record(research_root, definition_aid)
    return {"definition_artifact_id": definition_aid, "resolution_artifact_id": resolution_aid,
        "search_strategy": {"queries": ["fiction with explicit races"], "selection_rationale": "fixture-only search plan"},
        "budget": {"target_works": 1, "max_search_rounds": 1, "max_source_attempts": 2, "max_full_work_attempts": 2, "max_resume_invocations": 3, **budget},
        "budget_authoring": {"host": "fixture", "input_artifact_id": definition["neutral_input_artifact_id"], "assurance": "NOT_PROVEN", "isolation_claim": "CONTEXT_NOT_ISOLATED"}, "frozen_at": NOW}


def record(run, research_root, op, kind, detail):
    return record_observation_research_event(run.record, {"operation_id": op, "event_type": kind, "detail": detail, "recorded_at": NOW}, research_root)


def setup_campaign(tmp_path, *, profile="race-mention-v1", kind="RACE_MENTION", **budget):
    research_root, source, prepared = prepared_handoff(tmp_path, profile=profile, kind=kind)
    refs = prepared["handoff"]["builder"]
    run = init_observation_research(campaign_draft(refs["definition_artifact_id"], refs["resolution_artifact_id"], research_root, **budget), research_root)
    record(run, research_root, "lead", "LEAD_RECORDED", {"lead_artifact_id": refs["work_lead_artifact_ids"][0], "search_event_artifact_id": None})
    started = record(run, research_root, "source:start", "SOURCE_STARTED", {"lead_artifact_ids": refs["work_lead_artifact_ids"], "source_input_artifact_id": refs["source_declaration_artifact_id"]})
    record(run, research_root, "source:finish", "SOURCE_FINISHED", {"start_event_artifact_id": started.artifact_id, "status": "ELIGIBLE", "handoff_artifact_id": prepared["handoff_artifact_id"], "reason": "fixture source admitted"})
    return research_root, source, prepared, run


@pytest.mark.parametrize("nonempty", [False, True])
def test_full_campaign_two_pass_stop_report_and_offline_validation(tmp_path, nonempty):
    research_root, source, prepared, run = setup_campaign(tmp_path)
    waiting = execute_campaign_handoff(run.record, prepared["handoff_path"], research_root, tmp_path / "native", now=NOW)
    assert waiting["status"] == "WAITING_FOR_AGENT", waiting
    _answers(waiting, nonempty=nonempty)
    completed = execute_campaign_handoff(run.record, prepared["handoff_path"], research_root, tmp_path / "native", now=NOW)
    assert completed["status"] == "SUCCEEDED", completed
    record(run, research_root, "stop", "STOP", {"reason": "TARGET_WORKS_REACHED", "rationale": "one successful fixture work"})
    report = report_observation_research(run.record, research_root)
    assert report["budget_used"] == {"search_rounds": 0, "source_attempts": 1, "full_work_attempts": 1, "resume_invocations": 1}
    assert report["counts"]["successful_works"] == 1
    assert report["counts"]["zero_result_receipts"] == int(not nonempty)
    assert report["counts"]["corpus_record_count_sum"] == int(nonempty)
    assert report["leads"][0]["execution"] == "SUCCEEDED"
    source.unlink()
    assert validate_observation_research(run.path, research_root, report_or_path=report) == report
    if nonempty:
        span = report["results"][0]["evidence_index"][0]["source_spans"][0]
        assert span["chapter_id"] and span["segment_id"]
        assert "text" not in span and "quote" not in span


def test_no_profile_report_preserves_unresolved_requirements(tmp_path):
    definition = seal_observation_definition_from_draft(definition_draft(tmp_path), tmp_path)
    draft = resolution_draft(definition)
    draft["decision"] = "CREATE_REQUIRED"
    for key in ("selected_profile_ref", "fit", "admission"): del draft[key]
    draft["coverage"][0].update(disposition="UNSUPPORTED", payload_kinds=[], payload_paths=[])
    resolution = seal_profile_resolution_from_draft(draft, tmp_path)
    run = init_observation_research(campaign_draft(definition.artifact_id, resolution.artifact_id, tmp_path), tmp_path)
    record(run, tmp_path, "stop", "STOP", {"reason": "NO_USABLE_PROFILE", "rationale": "no existing extraction contract fits"})
    report = report_observation_research(run.record, tmp_path)
    assert report["status"] == "STOPPED" and report["profile_decision"] == "CREATE_REQUIRED"
    assert report["unmet_requirement_ids"] and report["counts"]["execution_invocations"] == 0


def test_search_budget_idempotency_and_result_artifact_binding(tmp_path):
    root, _, _, run = setup_campaign(tmp_path)
    start = record(run, root, "search:start", "SEARCH_STARTED", {"query": "fixture query"})
    assert record(run, root, "search:start", "SEARCH_STARTED", {"query": "fixture query"}).artifact_id == start.artifact_id
    with pytest.raises(ValidationError, match="operation ID|operation ID|operation"):
        record(run, root, "search:start", "SEARCH_STARTED", {"query": "changed query"})
    with pytest.raises(ValidationError, match="budget"):
        record(run, root, "search:second", "SEARCH_STARTED", {"query": "another query"})
    aid = research_store(root).put(b'fixture search result, lead-only')
    record(run, root, "search:finish", "SEARCH_FINISHED", {"start_event_artifact_id": start.artifact_id, "outcome": "COMPLETED", "result_artifact_ids": [aid], "error": None})
    report = report_observation_research(run.record, root)
    assert report["counts"]["search_rounds"] == 1
    research_store(root).delete_for_test(aid)
    with pytest.raises(ValidationError, match="missing"):
        validate_observation_research(run.record, root)


def test_failed_sources_remain_in_denominator(tmp_path):
    root, _, prepared, run = setup_campaign(tmp_path)
    refs = prepared["handoff"]["builder"]
    start = record(run, root, "source2:start", "SOURCE_STARTED", {"lead_artifact_ids": refs["work_lead_artifact_ids"], "source_input_artifact_id": research_store(root).put(b'host source attempt input')})
    record(run, root, "source2:finish", "SOURCE_FINISHED", {"start_event_artifact_id": start.artifact_id, "status": "BLOCKED_BY_RIGHTS", "handoff_artifact_id": None, "reason": "fixture reports unknown rights"})
    report = report_observation_research(run.record, root)
    assert report["counts"]["source_attempts"] == 2 and report["counts"]["failed_source_attempts"] == 1
    assert len(report["source_attempts"]) == 2


def test_execution_requires_reservation_and_resume_budget(tmp_path):
    root, _, prepared, run = setup_campaign(tmp_path, max_resume_invocations=0)
    with pytest.raises(ValidationError, match="owned"):
        record(run, root, "forged", "EXECUTION_FINISHED", {})
    first = execute_campaign_handoff(run.record, prepared["handoff_path"], root, tmp_path / "native", operation_id="initial", now=NOW)
    duplicate = execute_campaign_handoff(run.record, prepared["handoff_path"], root, tmp_path / "native", operation_id="initial", now=NOW)
    assert duplicate == first
    _answers(first)
    with pytest.raises(ValidationError, match="budget"):
        execute_campaign_handoff(run.record, prepared["handoff_path"], root, tmp_path / "native", now=NOW)
    assert report_observation_research(run.record, root)["counts"]["execution_invocations"] == 1


def test_cached_native_receipt_reused_by_new_campaign_without_budget(tmp_path):
    root, _, prepared, first_run = setup_campaign(tmp_path)
    work = tmp_path / "native"
    waiting = execute_campaign_handoff(first_run.record, prepared["handoff_path"], root, work, now=NOW)
    _answers(waiting)
    execute_campaign_handoff(first_run.record, prepared["handoff_path"], root, work, now=NOW)
    refs = prepared["handoff"]["builder"]
    draft = campaign_draft(refs["definition_artifact_id"], refs["resolution_artifact_id"], root, max_full_work_attempts=0)
    run = init_observation_research(draft, root)
    record(run, root, "lead", "LEAD_RECORDED", {"lead_artifact_id": refs["work_lead_artifact_ids"][0], "search_event_artifact_id": None})
    source = record(run, root, "source:start", "SOURCE_STARTED", {"lead_artifact_ids": refs["work_lead_artifact_ids"], "source_input_artifact_id": refs["source_declaration_artifact_id"]})
    record(run, root, "source:finish", "SOURCE_FINISHED", {"start_event_artifact_id": source.artifact_id, "status": "ELIGIBLE", "handoff_artifact_id": prepared["handoff_artifact_id"], "reason": "fixture source admitted"})
    reused = execute_campaign_handoff(run.record, prepared["handoff_path"], root, work, now=NOW)
    assert reused["status"] == "SUCCEEDED"
    report = report_observation_research(run.record, root)
    assert report["budget_used"]["full_work_attempts"] == 0 and report["counts"]["reused_executions"] == 1


def test_report_forged_count_and_missing_event_rejected(tmp_path):
    root, _, _, run = setup_campaign(tmp_path)
    report = report_observation_research(run.record, root)
    changed = copy.deepcopy(report)
    changed["counts"]["successful_works"] = 99
    with pytest.raises(ValidationError, match="saved report"):
        validate_observation_research(run.record, root, report_or_path=changed)
    events = root / "campaigns" / run.record["run_id"] / "events"
    (events / "000002.json").unlink()
    with pytest.raises(ValidationError, match="consecutive"):
        validate_observation_research(run.record, root)


def test_campaign_lock_prevents_parallel_budget_reservation(tmp_path):
    root, _, _, run = setup_campaign(tmp_path)
    with _campaign_lock(run.record, root):
        with pytest.raises(ValidationError, match="already"):
            record(run, root, "search", "SEARCH_STARTED", {"query": "fixture"})


def test_invalid_budget_authoring_and_premature_stop_rejected(tmp_path):
    root, _, prepared, run = setup_campaign(tmp_path)
    refs = prepared["handoff"]["builder"]
    draft = campaign_draft(refs["definition_artifact_id"], refs["resolution_artifact_id"], root)
    draft["budget_authoring"]["input_artifact_id"] = refs["definition_artifact_id"]
    with pytest.raises(ValidationError, match="independently"):
        init_observation_research(draft, root)
    with pytest.raises(ValidationError, match="target"):
        record(run, root, "stop", "STOP", {"reason": "TARGET_WORKS_REACHED", "rationale": "false success"})


def test_interrupted_native_call_requires_explicit_resume_and_consumes_resume_budget(tmp_path, monkeypatch):
    import xhnovel_pipeline.generic_handoff_execution as native
    root, _, prepared, run = setup_campaign(tmp_path)
    real = native.run_generic_corpus_workflow
    def crash(*args, **kwargs): raise KeyboardInterrupt("fixture native interruption")
    monkeypatch.setattr(native, "run_generic_corpus_workflow", crash)
    with pytest.raises(KeyboardInterrupt):
        execute_campaign_handoff(run.record, prepared["handoff_path"], root, tmp_path / "native", now=NOW)
    monkeypatch.setattr(native, "run_generic_corpus_workflow", real)
    with pytest.raises(ValidationError, match="explicit resume"):
        execute_campaign_handoff(run.record, prepared["handoff_path"], root, tmp_path / "native", now=NOW)
    waiting = execute_campaign_handoff(run.record, prepared["handoff_path"], root, tmp_path / "native", resume=True, now=NOW)
    assert waiting["status"] == "WAITING_FOR_AGENT", waiting
    _answers(waiting)
    assert execute_campaign_handoff(run.record, prepared["handoff_path"], root, tmp_path / "native", now=NOW)["status"] == "SUCCEEDED"
    report = report_observation_research(run.record, root)
    assert report["budget_used"]["full_work_attempts"] == 1 and report["budget_used"]["resume_invocations"] == 2
    assert [row["finish"]["detail"]["status"] for row in report["execution_invocations"]] == ["INTERRUPTED", "WAITING_FOR_AGENT", "SUCCEEDED"]


def test_recovery_cannot_absorb_unrecorded_native_continuation(tmp_path, monkeypatch):
    import xhnovel_pipeline.observation_campaign as campaign
    from xhnovel_pipeline.generic_handoff_execution import execute_generic_handoff
    root, _, prepared, run = setup_campaign(tmp_path)
    real = campaign._append
    def lose_return(plan, draft, *args, **kwargs):
        if draft["event_type"] == "EXECUTION_FINISHED": raise KeyboardInterrupt("fixture lost campaign return")
        return real(plan, draft, *args, **kwargs)
    monkeypatch.setattr(campaign, "_append", lose_return)
    with pytest.raises(KeyboardInterrupt):
        execute_campaign_handoff(run.record, prepared["handoff_path"], root, tmp_path / "native", now=NOW)
    monkeypatch.setattr(campaign, "_append", real)
    events = campaign.native_history(prepared["handoff"], root)
    _answers(events[-1][1]["detail"])
    outside = execute_generic_handoff(prepared["handoff_path"], root, tmp_path / "native", now=NOW)
    assert outside["status"] == "SUCCEEDED"
    repaired = execute_campaign_handoff(run.record, prepared["handoff_path"], root, tmp_path / "native", now=NOW)
    assert repaired["status"] == "WAITING_FOR_AGENT"
    before_rejection = report_observation_research(run.record, root)
    assert before_rejection["counts"]["successful_works"] == 0
    assert before_rejection["budget_used"]["full_work_attempts"] == 1
    assert before_rejection["budget_used"]["resume_invocations"] == 0
    with pytest.raises(ValidationError, match="unrecorded native continuation"):
        execute_campaign_handoff(run.record, prepared["handoff_path"], root, tmp_path / "native", now=NOW)
    after_rejection = report_observation_research(run.record, root)
    assert after_rejection["budget_used"] == before_rejection["budget_used"]
    assert after_rejection["counts"]["execution_invocations"] == 1


def test_reservation_cannot_adopt_different_native_executor(tmp_path, monkeypatch):
    import xhnovel_pipeline.observation_campaign as campaign
    from xhnovel_pipeline.generic_handoff_execution import execute_generic_handoff
    root, _, prepared, run = setup_campaign(tmp_path)
    real = campaign.execute_generic_handoff
    def before_native(*args, **kwargs): raise KeyboardInterrupt("fixture interruption before native start")
    monkeypatch.setattr(campaign, "execute_generic_handoff", before_native)
    with pytest.raises(KeyboardInterrupt):
        execute_campaign_handoff(run.record, prepared["handoff_path"], root, tmp_path / "native", now=NOW)
    monkeypatch.setattr(campaign, "execute_generic_handoff", real)
    outside = execute_generic_handoff(prepared["handoff_path"], root, tmp_path / "native", agent_model_label="other executor", now=NOW)
    assert outside["status"] == "WAITING_FOR_AGENT"
    with pytest.raises(ValidationError, match="executor differs"):
        execute_campaign_handoff(run.record, prepared["handoff_path"], root, tmp_path / "native", now=NOW)


def test_failed_source_then_distinct_source_success_preserves_both_attempts(tmp_path):
    from test_generic_handoff import handoff_input
    from xhnovel_pipeline.canonical import canonical_dumps
    from xhnovel_pipeline.generic_handoff import prepare_generic_handoff_from_input

    research_root = tmp_path / "research"
    first_path, second_path = tmp_path / "unresolved-rights.txt", tmp_path / "authorized.txt"
    first_path.write_text("第一章 源一\n人族居住在青云山。\n", encoding="utf-8")
    second_path.write_text("第一章 源二\n人族居住在青云山。\n", encoding="utf-8")
    accepted_input = handoff_input(research_root, second_path)
    run = init_observation_research(campaign_draft(accepted_input["definition_artifact_id"], accepted_input["resolution_artifact_id"], research_root), research_root)
    record(run, research_root, "lead", "LEAD_RECORDED", {"lead_artifact_id": accepted_input["work_lead_artifact_ids"][0], "search_event_artifact_id": None})

    blocked_input = copy.deepcopy(accepted_input)
    blocked_input["source_declaration"]["source"]["path"] = str(first_path)
    blocked_input["source_declaration"]["rights"]["basis"] = "UNKNOWN"
    blocked_input_aid = research_store(research_root).put(canonical_dumps(blocked_input))
    first = record(run, research_root, "blocked:start", "SOURCE_STARTED", {"lead_artifact_ids": blocked_input["work_lead_artifact_ids"], "source_input_artifact_id": blocked_input_aid})
    with pytest.raises(ValidationError) as denied:
        prepare_generic_handoff_from_input(blocked_input, research_root)
    record(run, research_root, "blocked:finish", "SOURCE_FINISHED", {"start_event_artifact_id": first.artifact_id, "status": "BLOCKED_BY_RIGHTS", "handoff_artifact_id": None, "reason": str(denied.value)})

    accepted_input_aid = research_store(research_root).put(canonical_dumps(accepted_input))
    second = record(run, research_root, "accepted:start", "SOURCE_STARTED", {"lead_artifact_ids": accepted_input["work_lead_artifact_ids"], "source_input_artifact_id": accepted_input_aid})
    prepared = prepare_generic_handoff_from_input(accepted_input, research_root)
    record(run, research_root, "accepted:finish", "SOURCE_FINISHED", {"start_event_artifact_id": second.artifact_id, "status": "ELIGIBLE", "handoff_artifact_id": prepared["handoff_artifact_id"], "reason": "Distinct authorized source passed native preflight"})
    waiting = execute_campaign_handoff(run.record, prepared["handoff_path"], research_root, tmp_path / "native", now=NOW)
    _answers(waiting, nonempty=True)
    result = execute_campaign_handoff(run.record, prepared["handoff_path"], research_root, tmp_path / "native", now=NOW)
    assert result["status"] == "SUCCEEDED", result
    record(run, research_root, "stop", "STOP", {"reason": "TARGET_WORKS_REACHED", "rationale": "Successful extraction from the second source"})
    report = report_observation_research(run.record, research_root)
    assert [attempt["finish"]["detail"]["status"] for attempt in report["source_attempts"]] == ["BLOCKED_BY_RIGHTS", "ELIGIBLE"]
    assert [attempt["start"]["detail"]["source_input_artifact_id"] for attempt in report["source_attempts"]] == [blocked_input_aid, accepted_input_aid]
    assert blocked_input_aid != accepted_input_aid
    assert report["counts"]["source_attempts"] == report["budget_used"]["source_attempts"] == 2
    assert report["counts"]["failed_source_attempts"] == 1
    assert report["counts"]["successful_works"] == report["counts"]["successful_receipts"] == 1
    assert report["leads"][0]["source"] == "ELIGIBLE"
    assert report["leads"][0]["execution"] == "SUCCEEDED"
    assert len(report["leads"][0]["source_attempt_ids"]) == 2
    assert report["results"][0]["receipt_artifact_id"] == result["receipt_artifact_id"]
    assert validate_observation_research(run.record, research_root, report_or_path=report) == report


def test_superset_profile_keeps_full_corpus_and_exports_offsets_without_excerpts(tmp_path):
    import json
    import pathlib
    from test_generic_handoff import source_declaration_draft
    from test_observation_planning import planning_stack
    from xhnovel_pipeline.generic_handoff import prepare_generic_handoff_from_input
    from xhnovel_pipeline.store import ArtifactStore

    research_root = tmp_path / "research"
    definition, _, lead = planning_stack(research_root, profile="geography-unique-v1", kind="PLACE_MENTION", goal="Observe explicitly named places")
    resolution_input = resolution_draft(definition, profile="geography-unique-v1", kind="PLACE_MENTION")
    resolution_input.update(fit="SUPERSET", rationale="The Profile also emits spatial relations beyond the required place names")
    resolution = seal_profile_resolution_from_draft(resolution_input, research_root)
    source = tmp_path / "geography.txt"
    sentence = "青云山位于北境。"
    source.write_text("第一章 山门\n" + sentence + "\n", encoding="utf-8")
    declaration = source_declaration_draft(source)
    assert declaration["rights"]["may_export_excerpts"] is False
    prepared = prepare_generic_handoff_from_input({"definition_artifact_id": definition.artifact_id, "resolution_artifact_id": resolution.artifact_id, "work_lead_artifact_ids": [lead.artifact_id], "source_declaration": declaration, "requested_at": NOW}, research_root)
    run = init_observation_research(campaign_draft(definition.artifact_id, resolution.artifact_id, research_root), research_root)
    record(run, research_root, "lead", "LEAD_RECORDED", {"lead_artifact_id": lead.artifact_id, "search_event_artifact_id": None})
    started = record(run, research_root, "source:start", "SOURCE_STARTED", {"lead_artifact_ids": [lead.artifact_id], "source_input_artifact_id": prepared["handoff"]["builder"]["source_declaration_artifact_id"]})
    record(run, research_root, "source:finish", "SOURCE_FINISHED", {"start_event_artifact_id": started.artifact_id, "status": "ELIGIBLE", "handoff_artifact_id": prepared["handoff_artifact_id"], "reason": "Complete authorized local fixture"})
    work_dir = tmp_path / "native"
    waiting = execute_campaign_handoff(run.record, prepared["handoff_path"], research_root, work_dir, now=NOW)
    assert waiting["status"] == "WAITING_FOR_AGENT"
    for pending in waiting["pending"]:
        packet = json.loads(pathlib.Path(pending["task"]).read_bytes())
        answer = {"records": []}
        if "completion" in packet["output"]["schema"]["required"]:
            answer["completion"] = {"status": "COMPLETE"}
        for span in packet["input"]["unit"]["source_spans"]:
            index = span["untrusted_text"].find(sentence)
            if index < 0:
                continue
            location = {"segment_id": span["segment_id"], "start": span["start"] + index, "end": span["start"] + index + len(sentence)}
            answer["records"] = [
                {"payload": {"kind": "PLACE_MENTION", "name": "青云山"}, "evidence_bindings": [{"paths": ["/name"], "source_spans": [{**location, "end": location["start"] + 3}]}]},
                {"payload": {"kind": "SPATIAL_RELATION", "subject_name": "青云山", "relation": "LOCATED_IN", "object_name": "北境"}, "evidence_bindings": [{"paths": ["/subject_name", "/relation", "/object_name"], "source_spans": [location]}]},
            ]
            break
        pathlib.Path(pending["answer"]).write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    completed = execute_campaign_handoff(run.record, prepared["handoff_path"], research_root, work_dir, now=NOW)
    assert completed["status"] == "SUCCEEDED", completed
    record(run, research_root, "stop", "STOP", {"reason": "TARGET_WORKS_REACHED", "rationale": "Fixture Profile produced a complete native corpus"})
    report = report_observation_research(run.record, research_root)
    assert report["profile_fit"] == "SUPERSET"
    assert report["result_scope"] == "COMPLETE_NATIVE_CORPORA_NO_SEMANTIC_FILTER"
    assert report["counts"]["corpus_record_count_sum"] == 2
    result = report["results"][0]
    records = [json.loads(line) for line in ArtifactStore(work_dir / "ingestion/objects").get(result["corpus_artifact_id"]).splitlines()]
    assert {row["payload"]["kind"] for row in records} == {"PLACE_MENTION", "SPATIAL_RELATION"}
    assert {row["record_id"] for row in result["evidence_index"]} == {row["record_id"] for row in records}
    assert result["index_policy"] == "OFFSETS_ONLY_NO_EXCERPTS"
    for row in result["evidence_index"]:
        for span in row["source_spans"]:
            assert set(span) == {"chapter_id", "source_artifact_id", "segment_id", "start", "end", "normalized_text_hash"}
    encoded = json.dumps(report, ensure_ascii=False)
    assert sentence not in encoded and "青云山" not in encoded and "北境" not in encoded
    assert validate_observation_research(run.record, research_root, report_or_path=report) == report
