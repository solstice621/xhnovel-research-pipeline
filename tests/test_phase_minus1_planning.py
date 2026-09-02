from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from xhnovel_pipeline.canonical import canonical_dumps
from xhnovel_pipeline.cli import main
from xhnovel_pipeline.errors import SchemaError, ValidationError
from xhnovel_pipeline.hashing import object_hash
from xhnovel_pipeline.ids import derived_id
from xhnovel_pipeline.paths import repo_root
from xhnovel_pipeline.phase0_builder import prepare_handoff_from_input
from xhnovel_pipeline.phase0_handoff import make_exploration_brief
from xhnovel_pipeline.phase0_planning import (
    PLANNING_CONTRACT_DEPENDENCIES,
    compile_exploration_brief,
    compile_exploration_plan_from_request,
    make_exploration_plan,
    make_neutral_execution,
    make_neutral_frame,
    make_planning_receipt,
    make_research_intake,
    normalize_seed_value,
    planning_contract_hash,
    project_neutral_input,
    put_planning_record,
    seal_intake_from_draft,
    seal_neutral_frame_from_drafts,
    validate_exploration_plan,
    validate_neutral_execution,
    validate_neutral_frame,
    validate_neutral_input,
    validate_planning_handoff,
    validate_planning_receipt,
    validate_research_intake,
)
from xhnovel_pipeline.schema import validate_schema
from xhnovel_pipeline.store import ArtifactStore


ROOT = repo_root()
FIXTURE = ROOT / "fixtures" / "positive" / "phase-minus1-planning"
NOW = "2026-09-02T00:00:00Z"
LATER = "2026-09-03T00:00:00Z"


def _scope():
    return {
        "genres": {"include": ["玄幻", "仙侠"], "exclude": ["西方奇幻"]},
        "scope_origin": "USER_EXPLICIT",
    }


def _seed(value="《斗破苍穹》", *, origin="USER_SUPPLIED"):
    return {"value": value, "bucket": "works", "provenance": [{"origin": origin}]}


def _intake(*, frozen_at=NOW, seeds=None, verbatim="先看看《斗破苍穹》里的物品争夺"):
    return make_research_intake(
        user_goal_verbatim=verbatim,
        neutral_goal_text="研究玄幻作品中的物品争夺机制",
        neutral_goal_origin="USER_CONFIRMED_SUMMARY",
        explicit_scope=_scope(),
        seeds=seeds if seeds is not None else [_seed()],
        frozen_at=frozen_at,
    )


def _stack(tmp_path: Path, *, assurance="HOST_ISOLATED_ATTESTED"):
    store = ArtifactStore(tmp_path / "planning" / "objects")
    intake = _intake()
    neutral_input = project_neutral_input(intake)
    frame = make_neutral_frame(
        intake=intake,
        neutral_input=neutral_input,
        research_question="物品争夺如何改变控制关系？",
        evidence_discovery_brief="寻找物品控制权变化及其对行动空间的影响。",
        selection_budget={"target_leads": 12, "max_leads_per_work": 3},
        frozen_at=NOW,
    )
    isolation_claim = (
        "FRESH_SUBAGENT_NO_SEED_PAYLOAD"
        if assurance == "HOST_ISOLATED_ATTESTED"
        else "HOST_ISOLATION_UNAVAILABLE"
    )
    execution = make_neutral_execution(
        neutral_input=neutral_input,
        neutral_frame=frame,
        host="codex-host",
        isolation_claim=isolation_claim,
        assurance=assurance,
        recorded_at=NOW,
        store=store,
    )
    user_seed = intake["seeds"][0]
    planner_seed = {
        "value": "拍卖争夺",
        "bucket": "interaction_families",
        "provenance": [
            {
                "origin": "PLANNER_DERIVED",
                "derived_from": [
                    {"kind": "INTAKE_SEED", "seed_id": user_seed["seed_id"]},
                    {"kind": "NEUTRAL_FRAME", "frame_id": frame["frame_id"]},
                ],
            }
        ],
    }
    plan = make_exploration_plan(
        intake=intake,
        neutral_frame=frame,
        neutral_execution=execution,
        exploration_seeds=[user_seed, planner_seed],
        diversity={
            "min_works": 4,
            "min_interaction_families": 3,
            "max_initial_leads_per_work": 2,
        },
        frozen_at=NOW,
    )
    brief = compile_exploration_brief(plan)
    receipt = make_planning_receipt(
        intake=intake,
        neutral_input=neutral_input,
        neutral_frame=frame,
        neutral_execution=execution,
        plan=plan,
        brief=brief,
        compiled_at=NOW,
        store=store,
        repo_root=ROOT,
    )
    return {
        "store": store,
        "intake": intake,
        "neutral_input": neutral_input,
        "frame": frame,
        "execution": execution,
        "plan": plan,
        "brief": brief,
        "receipt": receipt,
    }


def _reseal_receipt(receipt):
    receipt["receipt_id"] = derived_id(
        "PlanningCompilationReceipt",
        {
            key: copy.deepcopy(value)
            for key, value in receipt.items()
            if key not in {"receipt_id", "receipt_hash"}
        },
    )
    receipt["receipt_hash"] = object_hash(receipt, omit=("receipt_hash",))
    return receipt


def _reseal_plan(plan):
    plan["plan_id"] = derived_id(
        "ExplorationPlan",
        {
            key: copy.deepcopy(value)
            for key, value in plan.items()
            if key not in {"plan_id", "plan_hash", "frozen_at"}
        },
    )
    plan["plan_hash"] = object_hash(plan, omit=("plan_hash", "frozen_at"))
    return plan


def test_intake_frame_plan_and_brief_round_trip_and_ignore_record_times(tmp_path):
    first = _stack(tmp_path / "first")
    assert validate_research_intake(first["intake"]) == first["intake"]
    assert validate_neutral_input(first["neutral_input"]) == first["neutral_input"]
    assert validate_neutral_frame(first["frame"]) == first["frame"]
    assert validate_neutral_execution(first["execution"]) == first["execution"]
    assert validate_exploration_plan(first["plan"]) == first["plan"]
    assert validate_planning_receipt(first["receipt"]) == first["receipt"]

    later_intake = _intake(frozen_at=LATER)
    assert later_intake["intake_id"] == first["intake"]["intake_id"]
    assert later_intake["intake_hash"] == first["intake"]["intake_hash"]
    later_frame = make_neutral_frame(
        intake=later_intake,
        neutral_input=project_neutral_input(later_intake),
        research_question=first["frame"]["research_question"],
        evidence_discovery_brief=first["frame"]["evidence_discovery_brief"],
        selection_budget=first["frame"]["selection_budget"],
        frozen_at=LATER,
    )
    assert later_frame["frame_id"] == first["frame"]["frame_id"]
    assert later_frame["frame_hash"] == first["frame"]["frame_hash"]


def test_neutral_projection_drops_every_seed_dependent_or_verbatim_field():
    first = _intake(seeds=[_seed()], verbatim="先看《斗破苍穹》")
    second = _intake(
        seeds=[
            {"value": "凡人修仙传", "bucket": "works", "provenance": [{"origin": "USER_CONFIRMED"}]},
            {"value": "争夺", "bucket": "concepts", "provenance": [{"origin": "USER_SUPPLIED"}]},
        ],
        verbatim="完全不同的原始问题，包含《凡人修仙传》",
    )
    first_projection = project_neutral_input(first)
    second_projection = project_neutral_input(second)
    assert canonical_dumps(first_projection) == canonical_dumps(second_projection)
    assert set(first_projection) == {
        "schema_version",
        "neutral_input_id",
        "neutral_goal_text",
        "explicit_scope",
    }
    assert "intake_id" not in first_projection
    assert "seeds" not in first_projection
    assert "user_goal_verbatim" not in first_projection


def test_goal_origin_binding_is_byte_exact_and_confirmed_summary_may_differ():
    with pytest.raises(ValidationError, match="E-PLANNING-GOAL-BIND"):
        make_research_intake(
            user_goal_verbatim="研究物品争夺 ",
            neutral_goal_text="研究物品争夺",
            neutral_goal_origin="USER_VERBATIM_NO_SEEDS",
            explicit_scope=_scope(),
            seeds=[],
            frozen_at=NOW,
        )
    exact = make_research_intake(
        user_goal_verbatim="研究物品争夺 ",
        neutral_goal_text="研究物品争夺 ",
        neutral_goal_origin="USER_VERBATIM_NO_SEEDS",
        explicit_scope=_scope(),
        seeds=[],
        frozen_at=NOW,
    )
    validate_research_intake(exact)
    validate_research_intake(_intake())


def test_scope_is_typed_disjoint_and_empty_include_is_schema_illegal():
    overlap = _scope()
    overlap["genres"]["exclude"].append("玄幻")
    with pytest.raises(ValidationError, match="E-PLANNING-SCOPE"):
        make_research_intake(
            user_goal_verbatim="研究物品争夺",
            neutral_goal_text="研究物品争夺",
            neutral_goal_origin="USER_VERBATIM_NO_SEEDS",
            explicit_scope=overlap,
            seeds=[],
            frozen_at=NOW,
        )
    empty = _scope()
    empty["genres"]["include"] = []
    with pytest.raises(SchemaError, match="E-SCHEMA"):
        make_research_intake(
            user_goal_verbatim="研究物品争夺",
            neutral_goal_text="研究物品争夺",
            neutral_goal_origin="USER_VERBATIM_NO_SEEDS",
            explicit_scope=empty,
            seeds=[],
            frozen_at=NOW,
        )


def test_seed_identity_normalization_provenance_union_and_shuffle_are_deterministic():
    first = _intake(
        seeds=[
            _seed("《斗破苍穹》", origin="USER_SUPPLIED"),
            _seed("斗破苍穹", origin="USER_CONFIRMED"),
        ]
    )
    second = _intake(
        seeds=[
            _seed("斗破苍穹", origin="USER_CONFIRMED"),
            _seed("《斗破苍穹》", origin="USER_SUPPLIED"),
        ]
    )
    assert first["intake_id"] == second["intake_id"]
    assert first["intake_hash"] == second["intake_hash"]
    assert len(first["seeds"]) == 1
    merged = first["seeds"][0]
    assert merged["value"] == "斗破苍穹"
    assert merged["surface_forms"] == ["《斗破苍穹》", "斗破苍穹"]
    assert merged["provenance"] == [
        {"origin": "USER_SUPPLIED"},
        {"origin": "USER_CONFIRMED"},
    ]
    assert normalize_seed_value("concepts", "  灵\u0069\u0301   物  ") == "灵í 物"


def test_duplicate_seed_ids_in_hand_built_record_are_rejected():
    intake = _intake()
    intake["seeds"].append(copy.deepcopy(intake["seeds"][0]))
    with pytest.raises((SchemaError, ValidationError), match="E-(?:SCHEMA|PLANNING-SEED-BIND)"):
        validate_research_intake(intake)


def test_planner_same_seed_merges_derived_from_and_surface_forms(tmp_path):
    stack = _stack(tmp_path)
    user_seed = stack["intake"]["seeds"][0]
    frame_id = stack["frame"]["frame_id"]
    duplicate_a = {
        "value": " 拍卖   争夺 ",
        "bucket": "interaction_families",
        "provenance": [
            {
                "origin": "PLANNER_DERIVED",
                "derived_from": [{"kind": "INTAKE_SEED", "seed_id": user_seed["seed_id"]}],
            }
        ],
    }
    duplicate_b = {
        "value": "拍卖 争夺",
        "bucket": "interaction_families",
        "provenance": [
            {
                "origin": "PLANNER_DERIVED",
                "derived_from": [{"kind": "NEUTRAL_FRAME", "frame_id": frame_id}],
            }
        ],
    }
    plan = make_exploration_plan(
        intake=stack["intake"],
        neutral_frame=stack["frame"],
        neutral_execution=stack["execution"],
        exploration_seeds=[user_seed, duplicate_b, duplicate_a],
        diversity={
            "min_works": 1,
            "min_interaction_families": 1,
            "max_initial_leads_per_work": 1,
        },
        frozen_at=NOW,
    )
    assert len(plan["exploration_seeds"]) == 2
    planner = next(item for item in plan["exploration_seeds"] if item["bucket"] != "works")
    assert planner["value"] == "拍卖 争夺"
    assert len(planner["provenance"]) == 2


def test_seed_drop_bad_provenance_and_dangling_refs_fail_closed(tmp_path):
    stack = _stack(tmp_path)
    with pytest.raises(ValidationError, match="E-PLANNING-SEED-DROP"):
        make_exploration_plan(
            intake=stack["intake"],
            neutral_frame=stack["frame"],
            neutral_execution=stack["execution"],
            exploration_seeds=[],
            diversity={
                "min_works": 1,
                "min_interaction_families": 1,
                "max_initial_leads_per_work": 1,
            },
            frozen_at=NOW,
        )
    dangling = {
        "value": "夺宝",
        "bucket": "interaction_families",
        "provenance": [
            {
                "origin": "PLANNER_DERIVED",
                "derived_from": [{"kind": "INTAKE_SEED", "seed_id": "SD-AAAAAAAAAAAAAAAAAAAA"}],
            }
        ],
    }
    with pytest.raises(ValidationError, match="E-PLANNING-SEED-REF"):
        make_exploration_plan(
            intake=stack["intake"],
            neutral_frame=stack["frame"],
            neutral_execution=stack["execution"],
            exploration_seeds=[stack["intake"]["seeds"][0], dangling],
            diversity={
                "min_works": 1,
                "min_interaction_families": 1,
                "max_initial_leads_per_work": 1,
            },
            frozen_at=NOW,
        )
    with pytest.raises(ValidationError, match="E-PLANNING-SEED-ORIGIN"):
        _intake(
            seeds=[
                {
                    "value": "夺宝",
                    "bucket": "concepts",
                    "provenance": [
                        {
                            "origin": "PLANNER_DERIVED",
                            "derived_from": [
                                {"kind": "NEUTRAL_FRAME", "frame_id": stack["frame"]["frame_id"]}
                            ],
                        }
                    ],
                }
            ]
        )


@pytest.mark.parametrize(
    ("assurance", "claim"),
    [
        ("HOST_ISOLATED_ATTESTED", "HOST_ISOLATION_UNAVAILABLE"),
        ("NOT_PROVEN", "FRESH_SUBAGENT_NO_SEED_PAYLOAD"),
        ("NOT_PROVEN", "UNKNOWN_CLAIM"),
    ],
)
def test_attestation_state_machine_rejects_invalid_pairs(tmp_path, assurance, claim):
    intake = _intake()
    neutral = project_neutral_input(intake)
    frame = make_neutral_frame(
        intake=intake,
        neutral_input=neutral,
        research_question="问题",
        evidence_discovery_brief="发现目标互动",
        selection_budget={"target_leads": 4, "max_leads_per_work": 2},
        frozen_at=NOW,
    )
    with pytest.raises(ValidationError, match="E-PLANNING-ATTEST"):
        make_neutral_execution(
            neutral_input=neutral,
            neutral_frame=frame,
            host="codex",
            isolation_claim=claim,
            assurance=assurance,
            recorded_at=NOW,
            store=ArtifactStore(tmp_path / "objects"),
        )


def test_plan_assurance_and_execution_id_are_derived_not_strategy_authored(tmp_path):
    stack = _stack(tmp_path, assurance="NOT_PROVEN")
    assert stack["plan"]["seed_blindness_assurance"] == "NOT_PROVEN"
    assert stack["plan"]["neutral_execution_id"] == stack["execution"]["execution_id"]
    tampered = copy.deepcopy(stack["plan"])
    tampered["seed_blindness_assurance"] = "HOST_ISOLATED_ATTESTED"
    with pytest.raises(ValidationError, match="E-PLANNING-PLAN-BIND"):
        validate_exploration_plan(tampered)

    resealed = _reseal_plan(tampered)
    validate_exploration_plan(resealed)
    with pytest.raises(ValidationError, match="E-PLANNING-PAIR-BIND"):
        make_planning_receipt(
            intake=stack["intake"],
            neutral_input=stack["neutral_input"],
            neutral_frame=stack["frame"],
            neutral_execution=stack["execution"],
            plan=resealed,
            brief=compile_exploration_brief(resealed),
            compiled_at=NOW,
            store=stack["store"],
            repo_root=ROOT,
        )


def test_budget_inclusive_non_projection_metamorphism(tmp_path):
    stack = _stack(tmp_path)
    intake = stack["intake"]
    user = intake["seeds"][0]
    first = make_exploration_plan(
        intake=intake,
        neutral_frame=stack["frame"],
        neutral_execution=stack["execution"],
        exploration_seeds=[user],
        diversity={
            "min_works": 1,
            "min_interaction_families": 1,
            "max_initial_leads_per_work": 1,
        },
        frozen_at=NOW,
    )
    second = stack["plan"]
    first_brief = compile_exploration_brief(first)
    second_brief = compile_exploration_brief(second)
    assert canonical_dumps(first_brief) == canonical_dumps(second_brief)
    assert "prefer" not in first_brief["scope"]
    assert first_brief["scope"]["avoid"] == ["西方奇幻"]


def test_diversity_cannot_exceed_seed_blind_budget(tmp_path):
    stack = _stack(tmp_path)
    with pytest.raises(ValidationError, match="E-PLANNING-BUDGET"):
        make_exploration_plan(
            intake=stack["intake"],
            neutral_frame=stack["frame"],
            neutral_execution=stack["execution"],
            exploration_seeds=stack["intake"]["seeds"],
            diversity={
                "min_works": 13,
                "min_interaction_families": 1,
                "max_initial_leads_per_work": 1,
            },
            frozen_at=NOW,
        )


def test_planning_contract_dependency_set_is_sorted_complete_and_hash_sensitive(tmp_path):
    assert PLANNING_CONTRACT_DEPENDENCIES == tuple(sorted(PLANNING_CONTRACT_DEPENDENCIES))
    assert "contracts/exploration-brief.schema.json" in PLANNING_CONTRACT_DEPENDENCIES
    assert {
        "contracts/phase0-defs.schema.json",
        "contracts/defs.schema.json",
        "contracts/id-prefixes.json",
    } <= set(PLANNING_CONTRACT_DEPENDENCIES)
    baseline = planning_contract_hash(ROOT)
    copied = tmp_path / "copy"
    for relative in PLANNING_CONTRACT_DEPENDENCIES:
        destination = copied / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    assert planning_contract_hash(copied) == baseline
    for relative in PLANNING_CONTRACT_DEPENDENCIES:
        path = copied / relative
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        assert planning_contract_hash(copied) != baseline
        path.write_bytes(original)


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _stage_planning_run(tmp_path: Path):
    planning_root = tmp_path / "planning"
    intake_draft = {
        "user_goal_verbatim": "先看看《斗破苍穹》里的物品争夺",
        "neutral_goal_text": "研究玄幻作品中的物品争夺机制",
        "neutral_goal_origin": "USER_CONFIRMED_SUMMARY",
        "explicit_scope": _scope(),
        "seeds": [_seed()],
        "frozen_at": NOW,
    }
    sealed_intake = seal_intake_from_draft(
        _write_json(tmp_path / "intake-draft.json", intake_draft),
        planning_root,
    )
    frame_draft = {
        "neutral_input_id": sealed_intake.neutral_input["neutral_input_id"],
        "research_question": "物品争夺如何改变控制关系？",
        "evidence_discovery_brief": "寻找物品控制权变化及其对行动空间的影响。",
        "selection_budget": {"target_leads": 12, "max_leads_per_work": 3},
        "frozen_at": NOW,
    }
    attestation = {
        "host": "pytest-host",
        "isolation_claim": "FRESH_SUBAGENT_NO_SEED_PAYLOAD",
        "assurance": "HOST_ISOLATED_ATTESTED",
    }
    sealed_frame = seal_neutral_frame_from_drafts(
        _write_json(tmp_path / "neutral-frame-draft.json", frame_draft),
        _write_json(tmp_path / "attestation.json", attestation),
        planning_root,
    )
    user_seed = sealed_intake.intake["seeds"][0]
    request = {
        "schema_version": "0.2-draft",
        "intake_artifact_id": sealed_intake.intake_artifact_id,
        "neutral_frame_artifact_id": sealed_frame.neutral_frame_artifact_id,
        "neutral_execution_artifact_id": sealed_frame.neutral_execution_artifact_id,
        "strategy": {
            "exploration_seeds": [
                user_seed,
                {
                    "value": "拍卖争夺",
                    "bucket": "interaction_families",
                    "provenance": [
                        {
                            "origin": "PLANNER_DERIVED",
                            "derived_from": [
                                {"kind": "INTAKE_SEED", "seed_id": user_seed["seed_id"]},
                                {
                                    "kind": "NEUTRAL_FRAME",
                                    "frame_id": sealed_frame.neutral_frame["frame_id"],
                                },
                            ],
                        }
                    ],
                },
            ],
            "diversity": {
                "min_works": 4,
                "min_interaction_families": 3,
                "max_initial_leads_per_work": 2,
            },
        },
        "compiled_at": NOW,
    }
    compiled = compile_exploration_plan_from_request(
        _write_json(planning_root / "compile-request.json", request),
        planning_root,
        repo_root=ROOT,
    )
    return sealed_intake, sealed_frame, compiled


def _preparation_input(brief, source_path: Path):
    return {
        "brief": brief,
        "leads": [
            {
                "work_claim": {
                    "title": "测试仙途",
                    "author": "测试作者",
                    "language": "zh",
                    "aliases": [],
                },
                "scene_hint": {
                    "summary": "可能存在物品控制变化。",
                    "why_relevant": "用于压力测试控制关系。",
                    "interaction_tags": ["object_control"],
                    "location_hints": [],
                },
                "lead_sources": [
                    {
                        "source_kind": "REFERENCE",
                        "locator": "https://example.invalid/lead",
                        "supports": ["WORK_IDENTITY", "SCENE_EXISTENCE_HINT"],
                    }
                ],
                "frozen_at": NOW,
            }
        ],
        "source_declaration": {
            "work": {
                "canonical_title": "测试仙途",
                "author": "测试作者",
                "language": "zh",
                "aliases": [],
                "external_ids": [],
            },
            "source": {"kind": "txt", "path": str(source_path)},
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
            "edition_label": "测试副本",
            "declared_at": NOW,
        },
        "requested_at": NOW,
    }


def test_staged_lifecycle_receipt_replay_and_handoff_closure(tmp_path):
    _, _, compiled = _stage_planning_run(tmp_path)
    source = tmp_path / "book.txt"
    source.write_text("第一章\n林舟取得法器，但仍不能使用。", encoding="utf-8")
    phase0_root = tmp_path / "phase0"
    prepared = prepare_handoff_from_input(
        _write_json(tmp_path / "preparation.json", _preparation_input(compiled.brief, source)),
        phase0_root,
    )
    replayed = validate_planning_handoff(
        compiled.receipt_path,
        prepared.handoff_path,
        planning_root=compiled.planning_root,
        phase0_root=phase0_root,
        repo_root=ROOT,
    )
    assert replayed == compiled.receipt
    manifest = json.loads(
        (compiled.planning_root / "planning-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["compiled_brief_artifact_id"] == compiled.receipt[
        "compiled_brief_artifact_id"
    ]


def test_fixed_build_mismatch_precedes_content_replay(tmp_path):
    _, _, compiled = _stage_planning_run(tmp_path)
    forged = copy.deepcopy(compiled.receipt)
    forged["compiler_build_id"] = "PCB-DIFFERENT-BUILD"
    _reseal_receipt(forged)
    forged_path = _write_json(tmp_path / "forged-build-receipt.json", forged)
    with pytest.raises(ValidationError, match="E-PLANNING-BUILD-BIND"):
        validate_planning_handoff(
            forged_path,
            tmp_path / "not-read-handoff.json",
            planning_root=compiled.planning_root,
            phase0_root=tmp_path / "not-read-phase0",
            repo_root=ROOT,
        )


def test_forged_cross_pair_receipt_fails_exact_replay(tmp_path):
    _, _, compiled = _stage_planning_run(tmp_path)
    store = ArtifactStore(compiled.planning_root / "objects")
    unrelated = _intake(seeds=[], verbatim="另一个问题")
    unrelated_artifact_id = put_planning_record(store, "ResearchIntake", unrelated)
    forged = copy.deepcopy(compiled.receipt)
    forged["intake_artifact_id"] = unrelated_artifact_id
    forged["intake_id"] = unrelated["intake_id"]
    forged["intake_hash"] = unrelated["intake_hash"]
    _reseal_receipt(forged)
    with pytest.raises(ValidationError, match="E-PLANNING-RECEIPT-REPLAY"):
        validate_planning_handoff(
            _write_json(tmp_path / "forged-pair-receipt.json", forged),
            tmp_path / "not-read-handoff.json",
            planning_root=compiled.planning_root,
            phase0_root=tmp_path / "not-read-phase0",
            repo_root=ROOT,
        )


def test_valid_handoff_with_another_brief_fails_planning_closure(tmp_path):
    _, _, compiled = _stage_planning_run(tmp_path)
    source = tmp_path / "book.txt"
    source.write_text("正文。", encoding="utf-8")
    other_brief = make_exploration_brief(
        research_question="另一问题",
        evidence_discovery_brief="寻找另一类场景",
        scope={"genres": ["玄幻"], "target_leads": 2, "max_leads_per_work": 1},
        frozen_at=NOW,
    )
    phase0_root = tmp_path / "phase0-other"
    prepared = prepare_handoff_from_input(
        _write_json(tmp_path / "preparation-other.json", _preparation_input(other_brief, source)),
        phase0_root,
    )
    with pytest.raises(ValidationError, match="E-PLANNING-HANDOFF-CLOSURE"):
        validate_planning_handoff(
            compiled.receipt_path,
            prepared.handoff_path,
            planning_root=compiled.planning_root,
            phase0_root=phase0_root,
            repo_root=ROOT,
        )


def test_compile_request_is_strict_and_compiled_at_has_one_source(tmp_path):
    sealed_intake, sealed_frame, _ = _stage_planning_run(tmp_path)
    request = {
        "schema_version": "0.2-draft",
        "intake_artifact_id": sealed_intake.intake_artifact_id,
        "neutral_frame_artifact_id": sealed_frame.neutral_frame_artifact_id,
        "neutral_execution_artifact_id": sealed_frame.neutral_execution_artifact_id,
        "strategy": {
            "exploration_seeds": sealed_intake.intake["seeds"],
            "diversity": {
                "min_works": 1,
                "min_interaction_families": 1,
                "max_initial_leads_per_work": 1,
            },
        },
        "compiled_at": LATER,
        "unexpected": True,
    }
    with pytest.raises(SchemaError, match="E-SCHEMA"):
        validate_schema("ExplorationPlanCompileRequest", request)


def test_compile_request_rejects_individually_valid_cross_pair(tmp_path):
    root = tmp_path / "cross-pair"
    store = ArtifactStore(root / "objects")
    first_intake = _intake()
    first_input = project_neutral_input(first_intake)
    first_frame = make_neutral_frame(
        intake=first_intake,
        neutral_input=first_input,
        research_question="第一个问题",
        evidence_discovery_brief="寻找第一个目标。",
        selection_budget={"target_leads": 4, "max_leads_per_work": 2},
        frozen_at=NOW,
    )
    second_intake = make_research_intake(
        user_goal_verbatim="另一个问题",
        neutral_goal_text="另一个不含种子的研究问题",
        neutral_goal_origin="USER_CONFIRMED_SUMMARY",
        explicit_scope=_scope(),
        seeds=[],
        frozen_at=NOW,
    )
    second_input = project_neutral_input(second_intake)
    second_frame = make_neutral_frame(
        intake=second_intake,
        neutral_input=second_input,
        research_question="第二个问题",
        evidence_discovery_brief="寻找第二个目标。",
        selection_budget={"target_leads": 4, "max_leads_per_work": 2},
        frozen_at=NOW,
    )
    second_execution = make_neutral_execution(
        neutral_input=second_input,
        neutral_frame=second_frame,
        host="other-host",
        isolation_claim="HOST_ISOLATION_UNAVAILABLE",
        assurance="NOT_PROVEN",
        recorded_at=NOW,
        store=store,
    )
    request = {
        "schema_version": "0.2-draft",
        "intake_artifact_id": put_planning_record(store, "ResearchIntake", first_intake),
        "neutral_frame_artifact_id": put_planning_record(
            store, "NeutralResearchFrame", first_frame
        ),
        "neutral_execution_artifact_id": put_planning_record(
            store, "NeutralPlanningExecution", second_execution
        ),
        "strategy": {
            "exploration_seeds": first_intake["seeds"],
            "diversity": {
                "min_works": 1,
                "min_interaction_families": 1,
                "max_initial_leads_per_work": 1,
            },
        },
        "compiled_at": NOW,
    }
    with pytest.raises(ValidationError, match="E-PLANNING-PAIR-BIND"):
        compile_exploration_plan_from_request(
            _write_json(tmp_path / "cross-pair-request.json", request),
            root,
            repo_root=ROOT,
        )


def test_positive_fixture_crosses_all_three_public_planning_commands(tmp_path, capsys):
    planning_root = tmp_path / "planning-fixture"
    assert main(
        [
            "seal-intake",
            str(FIXTURE / "intake-draft.json"),
            "--work-dir",
            str(planning_root),
        ]
    ) == 0
    assert "OK: sealed research intake RIN-9ABCC159E993E3FAF515" in capsys.readouterr().out
    neutral = json.loads(
        (planning_root / "neutral-planning-input.json").read_text(encoding="utf-8")
    )
    assert neutral["neutral_input_id"] == "NPI-16129B534400549375A7"
    assert set(neutral) == {
        "schema_version",
        "neutral_input_id",
        "neutral_goal_text",
        "explicit_scope",
    }

    assert main(
        [
            "seal-neutral-frame",
            str(FIXTURE / "neutral-frame-draft.json"),
            "--attestation",
            str(FIXTURE / "attestation.json"),
            "--work-dir",
            str(planning_root),
        ]
    ) == 0
    assert "OK: sealed neutral research frame NRF-691DD5CE73C82E788B68" in capsys.readouterr().out
    manifest = json.loads(
        (planning_root / "planning-manifest.json").read_text(encoding="utf-8")
    )
    strategy = json.loads((FIXTURE / "strategy-draft.json").read_text(encoding="utf-8"))
    request = {
        "schema_version": "0.2-draft",
        "intake_artifact_id": manifest["intake_artifact_id"],
        "neutral_frame_artifact_id": manifest["neutral_frame_artifact_id"],
        "neutral_execution_artifact_id": manifest["neutral_execution_artifact_id"],
        "strategy": strategy,
        "compiled_at": NOW,
    }
    request_path = _write_json(planning_root / "compile-request.json", request)
    assert main(
        [
            "compile-exploration-plan",
            str(request_path),
            "--work-dir",
            str(planning_root),
        ]
    ) == 0
    output = capsys.readouterr().out
    assert "OK: compiled exploration plan XPL-" in output
    assert (planning_root / "exploration-plan.json").is_file()
    assert (planning_root / "exploration-brief.json").is_file()
    assert (planning_root / "planning-compilation-receipt.json").is_file()
