from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest

from xhnovel_pipeline.catalog import ID_FIELDS
from xhnovel_pipeline.errors import SchemaError, ValidationError
from xhnovel_pipeline.observation_common import get_record, put_record, research_store, seal_record
from xhnovel_pipeline.observation_planning import (
    seal_observation_definition_from_draft, seal_profile_resolution_from_draft,
    seal_observation_work_lead_from_draft, validate_observation_definition,
    validate_profile_resolution, validate_observation_work_lead,
)
from xhnovel_pipeline.paths import repo_root
from xhnovel_pipeline.phase0_planning import make_research_intake, project_neutral_input

NOW = "2026-09-05T00:00:00Z"


def definition_draft(research_root: Path, *, goal="Observe explicitly named races", requirements=None):
    intake = make_research_intake(
        user_goal_verbatim=goal, neutral_goal_text=goal, neutral_goal_origin="USER_VERBATIM_NO_SEEDS",
        explicit_scope={"genres": {"include": ["fantasy"], "exclude": []}, "scope_origin": "USER_EXPLICIT"}, seeds=[], frozen_at=NOW,
    )
    intake_id = put_record(research_root, "ResearchIntake", intake)
    neutral_id = put_record(research_root, "NeutralPlanningInput", project_neutral_input(intake))
    return {
        "intake_artifact_id": intake_id, "neutral_input_artifact_id": neutral_id,
        "research_question": goal, "inclusion_rules": ["Explicit names in the current window"],
        "exclusion_rules": ["Do not infer absent traits"], "required_distinctions": [],
        "requirements": requirements or [{"statement": "Collect names", "applies_to": ["race mentions"], "necessity": "REQUIRED", "locality": "UNIT_LOCAL", "origin": "HOST_INTERPRETATION", "origin_pointer": None, "origin_quote": None}],
        "locality": "UNIT_LOCAL", "locality_rationale": "Names can be directly supported locally", "decomposition_status": "NOT_REQUIRED",
        "authoring": {"host": "test fixture", "input_artifact_id": neutral_id, "assurance": "NOT_PROVEN", "isolation_claim": "CONTEXT_NOT_ISOLATED"},
        "frozen_at": NOW,
    }


def resolution_draft(definition, *, profile="race-mention-v1", kind="RACE_MENTION"):
    return {
        "definition_artifact_id": definition.artifact_id, "decision": "REUSE_EXISTING", "selected_profile_ref": profile, "fit": "EXACT",
        "admission": {"status": "HOST_REVIEWED_EXECUTABLE", "reviewer": "test fixture", "review_reference": "fixture-only built-in contract review"},
        "coverage": [{"requirement_id": item["requirement_id"], "disposition": "COVERED" if item["locality"] == "UNIT_LOCAL" else "UNSUPPORTED", "payload_kinds": [kind] if item["locality"] == "UNIT_LOCAL" else [], "payload_paths": ["/name"] if item["locality"] == "UNIT_LOCAL" else [], "prompt_rules": [], "rationale": "Names are explicit payload fields"} for item in definition.record["requirements"]],
        "rationale": "This built-in Profile covers the local target", "assessor": "test fixture", "frozen_at": NOW,
    }


def planning_stack(research_root: Path, *, profile="race-mention-v1", kind="RACE_MENTION", goal="Observe explicitly named races"):
    definition = seal_observation_definition_from_draft(definition_draft(research_root, goal=goal), research_root)
    resolution = seal_profile_resolution_from_draft(resolution_draft(definition, profile=profile, kind=kind), research_root)
    lead = seal_observation_work_lead_from_draft({
        "definition_artifact_id": definition.artifact_id,
        "work_claim": {"title": "Fixture Novel", "author": "Fixture Author", "language": "zh", "aliases": []},
        "relevance_hypothesis": "This work may contain explicit observations",
        "lead_sources": [{"source_kind": "OTHER", "locator": "user:fixture", "supports": ["WORK_IDENTITY"]}],
        "location_hints": [], "frozen_at": NOW,
    }, research_root)
    return definition, resolution, lead


def test_sealed_closure_and_visible_file_is_not_authority(tmp_path):
    definition, resolution, lead = planning_stack(tmp_path)
    assert get_record(tmp_path, definition.artifact_id) == validate_observation_definition(definition.record, tmp_path)
    assert validate_profile_resolution(resolution.record, tmp_path)["semantic_assurance"] == "UNQUALIFIED"
    assert validate_observation_work_lead(lead.record, tmp_path)["evidence_status"] == "LEAD_ONLY"
    definition.path.write_text("{}")
    assert validate_profile_resolution(resolution.record, tmp_path) == resolution.record
    assert "ObservationWorkLead" not in ID_FIELDS


@pytest.mark.parametrize("change", ["unknown", "duplicate", "proven", "wrong_input", "invented_confirmation"])
def test_definition_rejects_invalid_contract_and_provenance(tmp_path, change):
    draft = definition_draft(tmp_path)
    if change == "unknown": draft["profile_ref"] = "race-mention-v1"
    if change == "duplicate": draft["requirements"] *= 2
    if change == "proven": draft["authoring"]["assurance"] = "PROVEN"
    if change == "wrong_input": draft["authoring"]["input_artifact_id"] = draft["intake_artifact_id"]
    if change == "invented_confirmation":
        draft["requirements"][0].update(origin="NEUTRAL_GOAL", origin_pointer="/neutral_goal_text", origin_quote="invented user scope")
    with pytest.raises((ValidationError, SchemaError)):
        seal_observation_definition_from_draft(draft, tmp_path)


@pytest.mark.parametrize("change", ["missing_profile", "partial", "dangling", "duplicate", "path", "kind", "rule", "false_hash"])
def test_resolution_rejects_bad_coverage_and_bindings(tmp_path, change):
    definition = seal_observation_definition_from_draft(definition_draft(tmp_path), tmp_path)
    draft = resolution_draft(definition)
    if change == "missing_profile": del draft["selected_profile_ref"]
    if change == "partial": draft["coverage"][0]["disposition"] = "PARTIAL"
    if change == "dangling": draft["coverage"][0]["requirement_id"] = "OREQ-" + "A" * 20
    if change == "duplicate": draft["coverage"] *= 2
    if change == "path": draft["coverage"][0]["payload_paths"] = ["/invented"]
    if change == "kind": draft["coverage"][0]["payload_kinds"] = ["INVENTED"]
    if change == "rule": draft["coverage"][0]["prompt_rules"] = ["a rule absent from the prompt"]
    if change == "false_hash": draft["selected_profile"] = {}
    with pytest.raises((ValidationError, SchemaError)):
        seal_profile_resolution_from_draft(draft, tmp_path)


@pytest.mark.parametrize("decision", ["CREATE_REQUIRED", "UNSUPPORTED_BY_LOCAL_EXTRACTION"])
def test_no_profile_branches_are_deliverable(tmp_path, decision):
    definition = seal_observation_definition_from_draft(definition_draft(tmp_path), tmp_path)
    draft = resolution_draft(definition)
    for key in ("selected_profile_ref", "fit", "admission"): del draft[key]
    draft["decision"] = decision
    draft["coverage"][0].update(disposition="UNSUPPORTED", payload_kinds=[], payload_paths=[])
    sealed = seal_profile_resolution_from_draft(draft, tmp_path)
    assert "selected_profile" not in sealed.record
    assert sealed.record["unmet_requirement_ids"]
    draft["selected_profile_ref"] = "race-mention-v1"
    with pytest.raises(SchemaError): seal_profile_resolution_from_draft(draft, tmp_path)


def test_mixed_requires_new_decomposed_version_preserving_unresolved_scope(tmp_path):
    draft = definition_draft(tmp_path)
    cross = {**draft["requirements"][0], "statement": "Associate names across chapters", "locality": "CROSS_UNIT"}
    draft["requirements"].append(cross)
    draft.update(locality="MIXED_REQUIRES_DECOMPOSITION", decomposition_status="REQUIRED")
    original = seal_observation_definition_from_draft(draft, tmp_path)
    with pytest.raises(ValidationError, match="decomposed"):
        seal_profile_resolution_from_draft(resolution_draft(original), tmp_path)
    draft.update(decomposition_status="DECOMPOSED", previous_definition_artifact_id=original.artifact_id)
    revised = seal_observation_definition_from_draft(draft, tmp_path)
    resolution = seal_profile_resolution_from_draft(resolution_draft(revised), tmp_path)
    assert revised.record["unresolved_requirement_ids"] == resolution.record["unmet_requirement_ids"]
    assert original.record["requirements"] == revised.record["requirements"]


def test_cross_unit_cannot_be_claimed_covered(tmp_path):
    draft = definition_draft(tmp_path)
    draft["requirements"][0]["locality"] = "CROSS_UNIT"
    draft["locality"] = "REQUIRES_CROSS_UNIT_ANALYSIS"
    definition = seal_observation_definition_from_draft(draft, tmp_path)
    resolution = resolution_draft(definition)
    resolution["coverage"][0]["disposition"] = "COVERED"
    with pytest.raises(ValidationError, match="cross-unit"):
        seal_profile_resolution_from_draft(resolution, tmp_path)


def test_missing_or_corrupt_cas_fails_closed(tmp_path):
    definition, resolution, _ = planning_stack(tmp_path)
    research_store(tmp_path).delete_for_test(definition.artifact_id)
    with pytest.raises(ValidationError, match="missing"):
        validate_profile_resolution(resolution.record, tmp_path)


def test_profile_drift_is_rejected(tmp_path):
    definition, resolution, _ = planning_stack(tmp_path / "research")
    root = tmp_path / "repo"
    shutil.copytree(repo_root() / "profiles", root / "profiles")
    shutil.copytree(repo_root() / "contracts", root / "contracts")
    prompt = root / "profiles/generic/race-mention-v1/prompt.md"
    prompt.write_text(prompt.read_text() + "\nChanged rule.\n")
    with pytest.raises(ValidationError, match="bytes or identity"):
        validate_profile_resolution(resolution.record, tmp_path / "research", root=root)


def test_geography_uses_same_contract_without_domain_branch(tmp_path):
    _, resolution, _ = planning_stack(tmp_path, profile="geography-unique-v1", kind="PLACE_MENTION", goal="Observe named places")
    assert resolution.record["selected_profile"]["profile_ref"] == "geography-unique-v1"


def test_resealed_forgery_does_not_bypass_partition_or_source_closure(tmp_path):
    definition, resolution, lead = planning_stack(tmp_path)
    body = {key: value for key, value in definition.record.items() if key not in {"definition_id", "definition_hash"}}
    body["executable_requirement_ids"] = []
    forged = seal_record("ObservationDefinition", body, id_field="definition_id", hash_field="definition_hash")
    with pytest.raises(ValidationError, match="partitions"):
        validate_observation_definition(forged, tmp_path)
    body = {key: copy.deepcopy(value) for key, value in lead.record.items() if key not in {"lead_id", "lead_hash"}}
    body["lead_sources"][0]["locator"] = "changed:source"
    forged = seal_record("ObservationWorkLead", body, id_field="lead_id", hash_field="lead_hash")
    with pytest.raises(ValidationError, match="source identity"):
        validate_observation_work_lead(forged, tmp_path)


def test_hint_requires_matching_location_source(tmp_path):
    _, _, lead = planning_stack(tmp_path)
    body = {key: copy.deepcopy(value) for key, value in lead.record.items() if key not in {"lead_id", "lead_hash"}}
    body["location_hints"] = [{"kind": "CHAPTER_TITLE", "value": "chapter 3", "basis": "SEARCH_SNIPPET", "lead_source_ids": [lead.record["lead_sources"][0]["lead_source_id"]]}]
    # Shape itself is checked by schema; use native enum values.
    from xhnovel_pipeline.schema import validate_schema
    import json
    defs = json.loads((repo_root() / "contracts/phase0-defs.schema.json").read_text())["$defs"]["location_hint"]["properties"]
    body["location_hints"][0]["kind"] = defs["kind"]["enum"][0]
    body["location_hints"][0]["basis"] = defs["basis"]["enum"][0]
    forged = seal_record("ObservationWorkLead", body, id_field="lead_id", hash_field="lead_hash")
    with pytest.raises(ValidationError, match="location source"):
        validate_observation_work_lead(forged, tmp_path)


def test_cas_rejects_noncanonical_and_tampered_bytes(tmp_path):
    bad_id = research_store(tmp_path).put(b'{ "a": 1 }')
    with pytest.raises(ValidationError, match="canonical"):
        get_record(tmp_path, bad_id)
    definition, resolution, _ = planning_stack(tmp_path)
    research_store(tmp_path)._path(definition.artifact_id).write_bytes(b'{}')
    with pytest.raises(ValidationError, match="corrupt"):
        validate_profile_resolution(resolution.record, tmp_path)


def test_truthful_input_attestation_and_exact_user_origin(tmp_path):
    draft = definition_draft(tmp_path)
    draft["authoring"].update(assurance="HOST_ISOLATED_ATTESTED", isolation_claim="FRESH_SUBAGENT_NO_SEED_PAYLOAD")
    draft["requirements"][0].update(origin="NEUTRAL_GOAL", origin_pointer="/neutral_goal_text", origin_quote="explicitly named races")
    definition = seal_observation_definition_from_draft(draft, tmp_path)
    assert definition.record["authoring"]["assurance"] == "HOST_ISOLATED_ATTESTED"
    assert definition.record["authoring"]["input_artifact_id"] == definition.record["neutral_input_artifact_id"]


def test_definition_ids_stay_stable_when_requirement_order_changes(tmp_path):
    draft = definition_draft(tmp_path)
    draft["requirements"].append({**draft["requirements"][0], "statement": "Collect explicit type", "necessity": "OPTIONAL"})
    first = seal_observation_definition_from_draft(draft, tmp_path)
    draft["requirements"].reverse()
    second = seal_observation_definition_from_draft(draft, tmp_path)
    assert first.artifact_id == second.artifact_id
