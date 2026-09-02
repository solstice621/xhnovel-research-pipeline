from __future__ import annotations

import pytest

from xhnovel_pipeline.catalog import Catalog, ID_FIELDS
from xhnovel_pipeline.errors import SchemaError, ValidationError
from xhnovel_pipeline.ids import PREFIXES, derived_id
from xhnovel_pipeline.schema import validate_schema, validate_schema_resources

HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64
NOW = "2026-09-01T00:00:00Z"


def _brief():
    return {
        "schema_version": "0.2-draft",
        "brief_id": "XBR-BRIEF1",
        "research_question": "寻找玄幻作品中的对象控制桥段。",
        "evidence_discovery_brief": "寻找对象控制变化并改变后续行动空间的场景。",
        "scope": {"genres": ["玄幻"], "target_leads": 4, "max_leads_per_work": 2},
        "brief_hash": HASH_A,
        "frozen_at": NOW,
    }


def _lead_source():
    return {
        "lead_source_id": "LDS-SOURCE1",
        "source_kind": "ENCYCLOPEDIA",
        "locator": "https://example.invalid/lead",
        "title": None,
        "publisher": None,
        "role": "LEAD_ONLY",
        "supports": ["WORK_IDENTITY", "SCENE_EXISTENCE_HINT", "LOCATION_HINT"],
    }


def _lead():
    return {
        "schema_version": "0.2-draft",
        "lead_id": "RLD-LEAD1",
        "brief_id": "XBR-BRIEF1",
        "assurance": "UNVERIFIED_LEAD",
        "work_claim": {"title": "测试仙途", "author": "测试作者", "language": "zh", "aliases": []},
        "scene_hint": {
            "summary": "可能存在法器控制变化桥段。",
            "why_relevant": "可能压力测试物理持有与使用权限。",
            "interaction_tags": ["object_control"],
            "location_hints": [
                {
                    "kind": "CHARACTER",
                    "value": "林舟",
                    "basis": "SOURCE_STATED",
                    "lead_source_ids": ["LDS-SOURCE1"],
                }
            ],
        },
        "lead_sources": [_lead_source()],
        "lead_hash": HASH_A,
        "frozen_at": NOW,
    }


def _source_declaration():
    return {
        "schema_version": "0.2-draft",
        "source_declaration_id": "SDL-DECL1",
        "work": {
            "identity": {
                "basis": "TITLE_AUTHOR",
                "normalized_title": "测试仙途",
                "normalized_author": "测试作者",
                "language": "zh",
            },
            "canonical_title": "测试仙途",
            "author": "测试作者",
            "language": "zh",
            "aliases": [],
            "external_ids": [],
        },
        "source": {"kind": "txt", "path": "/tmp/book.txt"},
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
        "edition_label": "用户授权副本",
        "declaration_hash": HASH_A,
        "declared_at": NOW,
    }


def _build_request():
    return {
        "schema_version": "0.2-draft",
        "build_request_id": "HBR-REQUEST1",
        "exploration_brief_artifact_id": HASH_A,
        "research_lead_artifact_ids": [HASH_B],
        "source_declaration_artifact_id": HASH_C,
        "execution_profile": "DIRECT_FULL_WORK_V1",
        "requested_at": NOW,
        "build_request_hash": HASH_A,
    }


def _work_ref():
    return {
        "work_ref_id": "WREF-WORK1",
        "identity": {
            "basis": "TITLE_AUTHOR",
            "normalized_title": "测试仙途",
            "normalized_author": "测试作者",
            "language": "zh",
        },
        "canonical_title": "测试仙途",
        "normalized_title": "测试仙途",
        "author": "测试作者",
        "normalized_author": "测试作者",
        "language": "zh",
        "aliases": [],
        "external_ids": [],
        "resolution_basis": "TITLE_AUTHOR",
    }


def _source_ref():
    return {
        "source_ref_id": "SREF-SOURCE1",
        "work_ref_id": "WREF-WORK1",
        "kind": "txt",
        "locator": "file:///tmp/book.txt",
        "source_config_hash": HASH_A,
        "edition_label": "用户授权副本",
        "content_binding": "DEFERRED_TO_INGESTION",
    }


def _handoff():
    return {
        "schema_version": "0.2-draft",
        "handoff_id": "EHO-HANDOFF1",
        "brief_id": "XBR-BRIEF1",
        "motivating_lead_ids": ["RLD-LEAD1"],
        "work_ref": _work_ref(),
        "source_ref": _source_ref(),
        "localization": {
            "policy": "LEAD_ONLY_NOT_EXECUTOR_INPUT",
            "execution_scope": "FULL_WORK",
            "hint_refs": [{"lead_id": "RLD-LEAD1", "hint_indexes": [0]}],
        },
        "novel_spec": {
            "path": "novel-spec.json",
            "raw_artifact_id": HASH_A,
            "expected_input_spec_hash": HASH_B,
        },
        "builder": {
            "build_id": "phase0-handoff-builder-v1",
            "build_request_artifact_id": HASH_A,
            "exploration_brief_artifact_id": HASH_B,
            "research_lead_artifact_ids": [HASH_C],
            "source_declaration_artifact_id": "sha256:" + "d" * 64,
        },
        "readiness": {
            "status": "READY_FOR_XHNOVEL",
            "rights_basis": "USER_AUTHORIZED_LOCAL_COPY",
            "may_store_full_text": True,
            "may_send_to_external_model": True,
            "source_quality_tier": "B",
            "discovery_brief_hash": HASH_A,
        },
        "contains_evidence": False,
        "requested_at": NOW,
        "handoff_hash": HASH_C,
    }


def _started_event():
    return {
        "schema_version": "0.2-draft",
        "event_id": "HEV-EVENT1",
        "attempt_id": "HAT-ATTEMPT1",
        "handoff_id": "EHO-HANDOFF1",
        "attempt_ordinal": 1,
        "event_ordinal": 1,
        "state": "STARTED",
        "executor": "agent-files",
        "work_dir": "/tmp/run",
        "recorded_at": NOW,
        "event_hash": HASH_A,
    }


def _success_receipt():
    return {
        "schema_version": "0.2-draft",
        "receipt_id": "HER-RECEIPT1",
        "attempt_id": "HAT-ATTEMPT1",
        "handoff_id": "EHO-HANDOFF1",
        "attempt_ordinal": 1,
        "status": "SUCCEEDED",
        "executor": "agent-files",
        "expected_input_spec_hash": HASH_A,
        "actual_input_spec_hash": HASH_A,
        "ingestion_run_id": "NING-ABC",
        "request_id": "REQ-ABC",
        "bundle_id": "BND-ABC",
        "scene_scout_run_id": "SSRUN-ABC",
        "merge_run_id": "SMRUN-ABC",
        "export_id": "EXP-ABC",
        "validate_all": "PASS",
        "recorded_at": NOW,
        "receipt_hash": HASH_B,
    }


def _planning_seed():
    return {
        "seed_id": "SD-AAAAAAAAAAAAAAAAAAAA",
        "value": "斗破苍穹",
        "bucket": "works",
        "surface_forms": ["《斗破苍穹》"],
        "provenance": [{"origin": "USER_SUPPLIED"}],
    }


def _research_intake():
    return {
        "schema_version": "0.2-draft",
        "intake_id": "RIN-INTAKE1",
        "user_goal_verbatim": "先看《斗破苍穹》",
        "neutral_goal_text": "研究玄幻作品中的物品争夺",
        "neutral_goal_origin": "USER_CONFIRMED_SUMMARY",
        "explicit_scope": {
            "genres": {"include": ["玄幻"], "exclude": []},
            "scope_origin": "USER_EXPLICIT",
        },
        "seeds": [_planning_seed()],
        "intake_hash": HASH_A,
        "frozen_at": NOW,
    }


def _neutral_input():
    return {
        "schema_version": "0.2-draft",
        "neutral_input_id": "NPI-INPUT1",
        "neutral_goal_text": "研究玄幻作品中的物品争夺",
        "explicit_scope": {
            "genres": {"include": ["玄幻"], "exclude": []},
            "scope_origin": "USER_EXPLICIT",
        },
    }


def _neutral_frame():
    return {
        "schema_version": "0.2-draft",
        "frame_id": "NRF-FRAME1",
        "intake_id": "RIN-INTAKE1",
        "neutral_input_id": "NPI-INPUT1",
        "research_question": "物品争夺如何改变控制关系？",
        "evidence_discovery_brief": "寻找控制权变化。",
        "selection_budget": {"target_leads": 12, "max_leads_per_work": 3},
        "frame_hash": HASH_A,
        "frozen_at": NOW,
    }


def _neutral_execution():
    return {
        "schema_version": "0.2-draft",
        "execution_id": "NPE-EXECUTION1",
        "record_kind": "NEUTRAL_PLANNING_EXECUTION",
        "neutral_input_artifact_id": HASH_A,
        "neutral_frame_artifact_id": HASH_B,
        "host": "codex-host",
        "isolation_claim": "FRESH_SUBAGENT_NO_SEED_PAYLOAD",
        "assurance": "HOST_ISOLATED_ATTESTED",
        "execution_hash": HASH_C,
        "recorded_at": NOW,
    }


def _exploration_plan():
    return {
        "schema_version": "0.2-draft",
        "plan_id": "XPL-PLAN1",
        "intake_id": "RIN-INTAKE1",
        "neutral_frame": _neutral_frame(),
        "neutral_execution_id": "NPE-EXECUTION1",
        "explicit_scope": {
            "genres": {"include": ["玄幻"], "exclude": []},
            "scope_origin": "USER_EXPLICIT",
        },
        "exploration_seeds": [_planning_seed()],
        "diversity": {
            "min_works": 4,
            "min_interaction_families": 3,
            "max_initial_leads_per_work": 2,
        },
        "seed_blindness_assurance": "HOST_ISOLATED_ATTESTED",
        "plan_hash": HASH_A,
        "frozen_at": NOW,
    }


def _compile_request():
    return {
        "schema_version": "0.2-draft",
        "intake_artifact_id": HASH_A,
        "neutral_frame_artifact_id": HASH_B,
        "neutral_execution_artifact_id": HASH_C,
        "strategy": {
            "exploration_seeds": [_planning_seed()],
            "diversity": {
                "min_works": 4,
                "min_interaction_families": 3,
                "max_initial_leads_per_work": 2,
            },
        },
        "compiled_at": NOW,
    }


def _planning_receipt():
    return {
        "schema_version": "0.2-draft",
        "receipt_id": "PCR-RECEIPT1",
        "receipt_kind": "PLANNING_COMPILATION",
        "compiler_contract_version": "phase-minus1-compiler-v1",
        "compiler_build_id": "PCB-BUILD1",
        "repository_commit": "a" * 40,
        "source_tree_hash": HASH_A,
        "planning_contract_hash": HASH_B,
        "package_version": "0.2.0.dev0",
        "rebuild_hint": {
            "build_command": "python -m build --wheel",
            "wheel_sha256": None,
        },
        "intake_artifact_id": HASH_A,
        "neutral_input_artifact_id": HASH_B,
        "neutral_frame_artifact_id": HASH_C,
        "neutral_execution_artifact_id": "sha256:" + "d" * 64,
        "plan_artifact_id": "sha256:" + "e" * 64,
        "compiled_brief_artifact_id": "sha256:" + "f" * 64,
        "intake_id": "RIN-INTAKE1",
        "intake_hash": HASH_A,
        "neutral_input_id": "NPI-INPUT1",
        "neutral_frame_id": "NRF-FRAME1",
        "neutral_frame_hash": HASH_B,
        "neutral_execution_id": "NPE-EXECUTION1",
        "neutral_execution_hash": HASH_C,
        "plan_id": "XPL-PLAN1",
        "plan_hash": HASH_A,
        "compiled_brief_id": "XBR-BRIEF1",
        "compiled_brief_hash": HASH_B,
        "compiled_at": NOW,
        "receipt_hash": HASH_C,
    }


@pytest.mark.parametrize(
    ("kind", "factory"),
    [
        ("ExplorationBrief", _brief),
        ("ResearchLead", _lead),
        ("HandoffBuildRequest", _build_request),
        ("SourceDeclaration", _source_declaration),
        ("EvidenceHandoff", _handoff),
        ("HandoffAttemptEvent", _started_event),
        ("EvidenceHandoffExecutionReceipt", _success_receipt),
        ("ResearchIntake", _research_intake),
        ("NeutralPlanningInput", _neutral_input),
        ("NeutralPlanningExecution", _neutral_execution),
        ("NeutralResearchFrame", _neutral_frame),
        ("ExplorationPlan", _exploration_plan),
        ("ExplorationPlanCompileRequest", _compile_request),
        ("PlanningCompilationReceipt", _planning_receipt),
    ],
)
def test_phase0_valid_contracts(kind, factory):
    validate_schema(kind, factory())


def test_all_distributed_schema_references_resolve():
    validate_schema_resources()


@pytest.mark.parametrize(
    "field",
    [
        "source_spans",
        "segment_id",
        "KNOWN",
        "CONFLICTING",
        "SceneCandidate",
        "MechanismCandidate",
    ],
)
def test_research_lead_rejects_evidence_fields(field):
    lead = _lead()
    lead[field] = []
    with pytest.raises(SchemaError, match="E-SCHEMA"):
        validate_schema("ResearchLead", lead)


def test_research_lead_rejects_non_lead_sources():
    lead = _lead()
    lead["lead_sources"][0]["role"] = "EVIDENCE"
    with pytest.raises(SchemaError, match="E-SCHEMA"):
        validate_schema("ResearchLead", lead)


@pytest.mark.parametrize(
    ("kind", "factory"),
    [
        ("ExplorationBrief", _brief),
        ("ResearchLead", _lead),
        ("HandoffBuildRequest", _build_request),
        ("SourceDeclaration", _source_declaration),
        ("EvidenceHandoff", _handoff),
        ("HandoffAttemptEvent", _started_event),
        ("EvidenceHandoffExecutionReceipt", _success_receipt),
        ("ResearchIntake", _research_intake),
        ("NeutralPlanningInput", _neutral_input),
        ("NeutralPlanningExecution", _neutral_execution),
        ("NeutralResearchFrame", _neutral_frame),
        ("ExplorationPlan", _exploration_plan),
        ("ExplorationPlanCompileRequest", _compile_request),
        ("PlanningCompilationReceipt", _planning_receipt),
    ],
)
def test_phase0_contracts_reject_unknown_top_level_fields(kind, factory):
    record = factory()
    record["unexpected"] = True
    with pytest.raises(SchemaError, match="E-SCHEMA"):
        validate_schema(kind, record)


def test_source_declaration_rejects_unknown_or_cross_adapter_options():
    declaration = _source_declaration()
    declaration["source"]["unknown_adapter_option"] = True
    with pytest.raises(SchemaError, match="E-SCHEMA"):
        validate_schema("SourceDeclaration", declaration)

    declaration = _source_declaration()
    declaration["source"]["recursive"] = True
    with pytest.raises(SchemaError, match="E-SCHEMA"):
        validate_schema("SourceDeclaration", declaration)


@pytest.mark.parametrize(
    "source",
    [
        {"kind": "txt", "path": "/tmp/book.txt", "encoding": "utf-8"},
        {"kind": "directory", "path": "/tmp/book", "recursive": True},
        {
            "kind": "epub",
            "path": "/tmp/book.epub",
            "max_member_bytes": 10_000_000,
            "max_total_bytes": 200_000_000,
        },
        {
            "kind": "site",
            "index_url": "https://example.invalid/index",
            "chapter_url_pattern": "/chapter/[0-9]+$",
            "next_index_url_pattern": "[?&]page=[0-9]+$",
            "allow_external_chapters": False,
            "max_index_pages": 10,
            "max_index_bytes": 1_000_000,
        },
    ],
)
def test_source_declaration_accepts_each_adapter_shape(source):
    declaration = _source_declaration()
    declaration["source"] = source
    validate_schema("SourceDeclaration", declaration)


@pytest.mark.parametrize(
    "identity",
    [
        {"basis": "TITLE_AUTHOR", "normalized_title": "同名", "normalized_author": "甲", "language": "zh"},
        {"basis": "STABLE_EXTERNAL_ID", "namespace": "qidian", "external_id": "1001"},
        {"basis": "USER_CONFIRMED", "confirmation_artifact_id": HASH_A},
    ],
)
def test_source_declaration_accepts_each_work_identity_basis(identity):
    declaration = _source_declaration()
    declaration["work"]["identity"] = identity
    validate_schema("SourceDeclaration", declaration)


def test_work_identity_rejects_mixed_payload():
    declaration = _source_declaration()
    declaration["work"]["identity"] = {
        "basis": "STABLE_EXTERNAL_ID",
        "namespace": "qidian",
        "external_id": "1001",
        "normalized_title": "不应混入",
    }
    with pytest.raises(SchemaError, match="E-SCHEMA"):
        validate_schema("SourceDeclaration", declaration)


def test_handoff_is_ready_and_non_evidentiary_only():
    handoff = _handoff()
    handoff["contains_evidence"] = True
    with pytest.raises(SchemaError, match="E-SCHEMA"):
        validate_schema("EvidenceHandoff", handoff)


def test_handoff_rejects_copied_hint_text():
    handoff = _handoff()
    handoff["localization"]["hint_refs"][0]["text"] = "第327章"
    with pytest.raises(SchemaError, match="E-SCHEMA"):
        validate_schema("EvidenceHandoff", handoff)


def test_phase0_hashes_reject_placeholders():
    brief = _brief()
    brief["brief_hash"] = "sha256:..."
    with pytest.raises(SchemaError, match="E-SCHEMA"):
        validate_schema("ExplorationBrief", brief)


@pytest.mark.parametrize(
    ("kind", "factory", "field"),
    [
        ("ExplorationBrief", _brief, "brief_id"),
        ("ResearchLead", _lead, "lead_id"),
        ("HandoffBuildRequest", _build_request, "build_request_id"),
        ("SourceDeclaration", _source_declaration, "source_declaration_id"),
        ("EvidenceHandoff", _handoff, "handoff_id"),
        ("HandoffAttemptEvent", _started_event, "event_id"),
        ("EvidenceHandoffExecutionReceipt", _success_receipt, "receipt_id"),
        ("ResearchIntake", _research_intake, "intake_id"),
        ("NeutralPlanningInput", _neutral_input, "neutral_input_id"),
        ("NeutralPlanningExecution", _neutral_execution, "execution_id"),
        ("NeutralResearchFrame", _neutral_frame, "frame_id"),
        ("ExplorationPlan", _exploration_plan, "plan_id"),
        ("ExplorationPlanCompileRequest", _compile_request, "intake_artifact_id"),
        ("PlanningCompilationReceipt", _planning_receipt, "receipt_id"),
    ],
)
def test_phase0_contracts_reject_wrong_id_prefixes(kind, factory, field):
    record = factory()
    record[field] = "BAD-IDENTIFIER"
    with pytest.raises(SchemaError, match="E-SCHEMA"):
        validate_schema(kind, record)


def test_attempt_event_state_specific_shape():
    event = _started_event()
    event["state"] = "WAITING_FOR_AGENT"
    with pytest.raises(SchemaError, match="E-SCHEMA"):
        validate_schema("HandoffAttemptEvent", event)
    event["pending_count"] = 2
    validate_schema("HandoffAttemptEvent", event)
    started = _started_event()
    started["pending_count"] = 1
    with pytest.raises(SchemaError, match="E-SCHEMA"):
        validate_schema("HandoffAttemptEvent", started)


def test_failed_receipt_is_a_terminal_contract():
    receipt = {
        "schema_version": "0.2-draft",
        "receipt_id": "HER-RECEIPT2",
        "attempt_id": "HAT-ATTEMPT2",
        "handoff_id": "EHO-HANDOFF1",
        "attempt_ordinal": 2,
        "status": "FAILED",
        "executor": "api",
        "expected_input_spec_hash": HASH_A,
        "stage": "INGESTION",
        "error_code": "E-NOVEL-SOURCE",
        "error_message": "missing source",
        "recorded_at": NOW,
        "receipt_hash": HASH_B,
    }
    validate_schema("EvidenceHandoffExecutionReceipt", receipt)


@pytest.mark.parametrize(
    "field",
    [
        "ingestion_run_id",
        "request_id",
        "bundle_id",
        "scene_scout_run_id",
        "merge_run_id",
        "export_id",
    ],
)
def test_success_receipt_rejects_prefix_only_downstream_ids(field):
    receipt = _success_receipt()
    receipt[field] = receipt[field].split("-", 1)[0] + "-"
    with pytest.raises(SchemaError, match="E-SCHEMA"):
        validate_schema("EvidenceHandoffExecutionReceipt", receipt)


def test_phase0_kinds_stay_out_of_core_catalog():
    records = {
        "ResearchLead": _lead(),
        "ResearchIntake": _research_intake(),
        "NeutralPlanningInput": _neutral_input(),
        "NeutralPlanningExecution": _neutral_execution(),
        "NeutralResearchFrame": _neutral_frame(),
        "ExplorationPlan": _exploration_plan(),
        "ExplorationPlanCompileRequest": _compile_request(),
        "PlanningCompilationReceipt": _planning_receipt(),
    }
    for kind, record in records.items():
        assert kind not in ID_FIELDS
        with pytest.raises(ValidationError, match="E-CATALOG-KIND"):
            Catalog().add(kind, record)


@pytest.mark.parametrize(
    ("kind", "prefix"),
    [
        ("ExplorationBrief", "XBR-"),
        ("ResearchLead", "RLD-"),
        ("LeadSource", "LDS-"),
        ("WorkRef", "WREF-"),
        ("SourceRef", "SREF-"),
        ("HandoffBuildRequest", "HBR-"),
        ("SourceDeclaration", "SDL-"),
        ("EvidenceHandoff", "EHO-"),
        ("HandoffAttempt", "HAT-"),
        ("HandoffAttemptEvent", "HEV-"),
        ("EvidenceHandoffExecutionReceipt", "HER-"),
        ("ResearchIntake", "RIN-"),
        ("NeutralPlanningInput", "NPI-"),
        ("NeutralPlanningExecution", "NPE-"),
        ("NeutralResearchFrame", "NRF-"),
        ("ExplorationPlan", "XPL-"),
        ("PlanningCompilationReceipt", "PCR-"),
        ("Seed", "SD-"),
        ("PlanningCompilerBuild", "PCB-"),
    ],
)
def test_phase0_id_prefixes_are_deterministic_but_not_catalog_kinds(kind, prefix):
    assert PREFIXES[kind] == prefix
    assert derived_id(kind, {"x": 1}).startswith(prefix)
    assert kind not in ID_FIELDS
