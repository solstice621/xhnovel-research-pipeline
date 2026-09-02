"""Deterministic Phase -1 planning records, compilation, and replay.

Semantic planning remains a host-agent responsibility.  This module only seals
typed drafts, projects a seed-free neutral worker input, compiles an existing
ExplorationBrief, and proves the content lineage under one fixed compiler build.
"""

from __future__ import annotations

import copy
import json
import os
import pathlib
import re
import unicodedata
from dataclasses import dataclass
from importlib import metadata
from typing import Any, Callable

from .build_identity import build_source_hash
from .canonical import canonical_dumps
from .constants import SCHEMA_VERSION
from .errors import ValidationError
from .hashing import artifact_id_for, digest_prefix, is_real_sha256, object_hash, sha256_bytes
from .ids import derived_id
from .phase0_builder import resolve_validated_handoff_input
from .phase0_common import (
    nonempty,
    phase0_derived_id,
    phase0_object_hash,
    require_fields,
    sorted_strings,
    write_immutable,
)
from .phase0_handoff import make_exploration_brief, validate_exploration_brief
from .ranking import normalize_work_title
from .runtime import repository_commit
from .schema import validate_schema
from .store import ArtifactStore


PLANNING_COMPILER_CONTRACT_VERSION = "phase-minus1-compiler-v1"
PLANNING_CONTRACT_DEPENDENCIES = (
    "contracts/defs.schema.json",
    "contracts/exploration-brief.schema.json",
    "contracts/exploration-plan-compile-request.schema.json",
    "contracts/exploration-plan.schema.json",
    "contracts/id-prefixes.json",
    "contracts/neutral-planning-execution.schema.json",
    "contracts/neutral-planning-input.schema.json",
    "contracts/neutral-research-frame.schema.json",
    "contracts/phase0-defs.schema.json",
    "contracts/planning-compilation-receipt.schema.json",
    "contracts/research-intake.schema.json",
)

_SEED_BUCKETS = {"works", "concepts", "interaction_families"}
_USER_ORIGINS = {"USER_SUPPLIED", "USER_CONFIRMED"}
_ORIGIN_RANK = {
    "USER_SUPPLIED": 0,
    "USER_CONFIRMED": 1,
    "PLANNER_DERIVED": 2,
}
_ATTESTATION_PAIRS = {
    ("HOST_ISOLATED_ATTESTED", "FRESH_SUBAGENT_NO_SEED_PAYLOAD"),
    ("NOT_PROVEN", "HOST_ISOLATION_UNAVAILABLE"),
    ("NOT_PROVEN", "CONTEXT_NOT_ISOLATED"),
    ("NOT_PROVEN", "OPERATOR_DID_NOT_ATTEST"),
}


@dataclass(frozen=True)
class SealedIntake:
    planning_root: pathlib.Path
    intake_path: pathlib.Path
    neutral_input_path: pathlib.Path
    intake: dict[str, Any]
    neutral_input: dict[str, Any]
    intake_artifact_id: str
    neutral_input_artifact_id: str


@dataclass(frozen=True)
class SealedNeutralFrame:
    planning_root: pathlib.Path
    neutral_frame_path: pathlib.Path
    neutral_execution_path: pathlib.Path
    neutral_frame: dict[str, Any]
    neutral_execution: dict[str, Any]
    neutral_frame_artifact_id: str
    neutral_execution_artifact_id: str


@dataclass(frozen=True)
class CompiledExplorationPlan:
    planning_root: pathlib.Path
    plan_path: pathlib.Path
    brief_path: pathlib.Path
    receipt_path: pathlib.Path
    plan: dict[str, Any]
    brief: dict[str, Any]
    receipt: dict[str, Any]


def _json_bytes(value: Any) -> bytes:
    """Human-readable visible JSON; deliberately distinct from CAS canonical bytes."""

    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _read_json(path: pathlib.Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("E-PLANNING-JSON", f"invalid {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ValidationError("E-PLANNING-JSON", f"{label} must be an object")
    return value


def _preserved_nonempty(value: Any, *, code: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(code, f"{field} must be a non-empty string")
    return value


def _canonical_explicit_scope(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("E-PLANNING-SCOPE", "explicit_scope must be an object")
    require_fields(
        value,
        required={"genres", "scope_origin"},
        code="E-PLANNING-SCOPE",
        label="explicit_scope",
    )
    genres = value.get("genres")
    if not isinstance(genres, dict):
        raise ValidationError("E-PLANNING-SCOPE", "explicit_scope.genres must be an object")
    require_fields(
        genres,
        required={"include", "exclude"},
        code="E-PLANNING-SCOPE",
        label="explicit_scope.genres",
    )
    include = sorted_strings(
        genres.get("include"),
        code="E-PLANNING-SCOPE",
        field="explicit_scope.genres.include",
    )
    exclude = sorted_strings(
        genres.get("exclude"),
        code="E-PLANNING-SCOPE",
        field="explicit_scope.genres.exclude",
    )
    if set(include) & set(exclude):
        raise ValidationError(
            "E-PLANNING-SCOPE",
            "explicit_scope genre include and exclude sets must be disjoint",
        )
    origin = value.get("scope_origin")
    if origin not in {"USER_EXPLICIT", "USER_CONFIRMED"}:
        raise ValidationError("E-PLANNING-SCOPE", "scope_origin is not recognized")
    return {
        "genres": {"include": include, "exclude": exclude},
        "scope_origin": origin,
    }


def _canonical_selection_budget(value: Any) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != {
        "target_leads",
        "max_leads_per_work",
    }:
        raise ValidationError("E-PLANNING-BUDGET", "selection_budget has an invalid field set")
    target = value.get("target_leads")
    per_work = value.get("max_leads_per_work")
    if (
        not isinstance(target, int)
        or isinstance(target, bool)
        or not 1 <= target <= 1000
        or not isinstance(per_work, int)
        or isinstance(per_work, bool)
        or not 1 <= per_work <= 100
    ):
        raise ValidationError("E-PLANNING-BUDGET", "selection_budget is outside frozen bounds")
    return {"target_leads": target, "max_leads_per_work": per_work}


def _canonical_diversity(value: Any) -> dict[str, int]:
    fields = {
        "min_works",
        "min_interaction_families",
        "max_initial_leads_per_work",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValidationError("E-PLANNING-BUDGET", "diversity has an invalid field set")
    result: dict[str, int] = {}
    for field in sorted(fields):
        item = value.get(field)
        if not isinstance(item, int) or isinstance(item, bool) or item < 1:
            raise ValidationError("E-PLANNING-BUDGET", f"diversity.{field} must be at least 1")
        result[field] = item
    return result


def normalize_seed_value(bucket: str, value: str) -> str:
    """Return the single frozen semantic normal form for a planning seed."""

    if bucket not in _SEED_BUCKETS:
        raise ValidationError("E-PLANNING-SEED", f"unsupported seed bucket {bucket!r}")
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("E-PLANNING-SEED", "seed value must be a non-empty string")
    normalized = unicodedata.normalize("NFC", value)
    if bucket == "works":
        normalized = normalize_work_title(normalized)
    else:
        normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        raise ValidationError("E-PLANNING-SEED", "seed value normalizes to an empty string")
    return normalized


def _surface_form(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("E-PLANNING-SEED", "seed surface form must be non-empty")
    return unicodedata.normalize("NFC", value.strip())


def _canonical_seed_reference(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValidationError("E-PLANNING-SEED-PROV", "derived_from reference must be an object")
    kind = value.get("kind")
    if kind == "INTAKE_SEED" and set(value) == {"kind", "seed_id"}:
        seed_id = value.get("seed_id")
        if not isinstance(seed_id, str):
            raise ValidationError("E-PLANNING-SEED-PROV", "intake seed reference lacks seed_id")
        return {"kind": kind, "seed_id": seed_id}
    if kind == "NEUTRAL_FRAME" and set(value) == {"kind", "frame_id"}:
        frame_id = value.get("frame_id")
        if not isinstance(frame_id, str):
            raise ValidationError("E-PLANNING-SEED-PROV", "frame reference lacks frame_id")
        return {"kind": kind, "frame_id": frame_id}
    raise ValidationError("E-PLANNING-SEED-PROV", "derived_from reference has an invalid shape")


def _reference_key(value: dict[str, str]) -> tuple[str, str]:
    return (
        value["kind"],
        value["seed_id"] if value["kind"] == "INTAKE_SEED" else value["frame_id"],
    )


def _provenance_key(value: dict[str, Any]) -> tuple[int, tuple[tuple[str, str], ...]]:
    refs = tuple(_reference_key(item) for item in value.get("derived_from", []))
    return (_ORIGIN_RANK[value["origin"]], refs)


def _canonical_provenance(value: Any, *, allow_planner: bool) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValidationError("E-PLANNING-SEED-PROV", "seed provenance must be non-empty")
    canonical: dict[tuple[int, tuple[tuple[str, str], ...]], dict[str, Any]] = {}
    for raw in value:
        if not isinstance(raw, dict):
            raise ValidationError("E-PLANNING-SEED-PROV", "seed provenance entry must be an object")
        origin = raw.get("origin")
        if origin in _USER_ORIGINS:
            if set(raw) != {"origin"}:
                raise ValidationError(
                    "E-PLANNING-SEED-PROV",
                    "user seed provenance must not carry derived_from",
                )
            item: dict[str, Any] = {"origin": origin}
        elif origin == "PLANNER_DERIVED":
            if not allow_planner:
                raise ValidationError(
                    "E-PLANNING-SEED-ORIGIN",
                    "research intake seeds cannot have planner-derived provenance",
                )
            if set(raw) != {"origin", "derived_from"}:
                raise ValidationError(
                    "E-PLANNING-SEED-PROV",
                    "planner provenance must contain only origin and derived_from",
                )
            refs_raw = raw.get("derived_from")
            if not isinstance(refs_raw, list) or not refs_raw:
                raise ValidationError(
                    "E-PLANNING-SEED-PROV",
                    "planner provenance requires at least one derived_from reference",
                )
            refs = sorted(
                {_reference_key(ref): ref for ref in map(_canonical_seed_reference, refs_raw)}.values(),
                key=_reference_key,
            )
            item = {"origin": origin, "derived_from": refs}
        else:
            code = "E-PLANNING-SEED-ORIGIN" if not allow_planner else "E-PLANNING-SEED-PROV"
            raise ValidationError(code, f"unknown seed provenance origin {origin!r}")
        canonical[_provenance_key(item)] = item
    return [canonical[key] for key in sorted(canonical)]


def _seed(
    bucket: str,
    value: str,
    provenance: list[dict[str, Any]],
    *,
    surface_forms: list[str] | None = None,
    seed_id: str | None = None,
    allow_planner: bool = True,
) -> dict[str, Any]:
    """Seal one seed draft; callers merge same-identity records separately."""

    normalized_value = normalize_seed_value(bucket, value)
    if surface_forms is None:
        canonical_surfaces = [_surface_form(value)]
    else:
        if not isinstance(surface_forms, list) or not surface_forms:
            raise ValidationError("E-PLANNING-SEED", "surface_forms must be a non-empty array")
        canonical_surfaces = sorted({_surface_form(item) for item in surface_forms})
    expected_id = phase0_derived_id(
        "Seed",
        {"bucket": bucket, "normalized_value": normalized_value},
        code="E-PLANNING-SEED-BIND",
        label="seed",
    )
    if seed_id is not None and seed_id != expected_id:
        raise ValidationError("E-PLANNING-SEED-BIND", "seed_id differs from normalized identity")
    return {
        "seed_id": expected_id,
        "value": normalized_value,
        "bucket": bucket,
        "surface_forms": canonical_surfaces,
        "provenance": _canonical_provenance(provenance, allow_planner=allow_planner),
    }


def _seed_from_mapping(value: Any, *, allow_planner: bool) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("E-PLANNING-SEED", "seed must be an object")
    allowed = {"seed_id", "value", "bucket", "surface_forms", "provenance"}
    if not {"value", "bucket", "provenance"} <= set(value) or not set(value) <= allowed:
        raise ValidationError("E-PLANNING-SEED", "seed has an invalid field set")
    return _seed(
        value.get("bucket"),
        value.get("value"),
        value.get("provenance"),
        surface_forms=value.get("surface_forms"),
        seed_id=value.get("seed_id"),
        allow_planner=allow_planner,
    )


def _merge_seed_items(values: Any, *, allow_planner: bool) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        raise ValidationError("E-PLANNING-SEED", "seeds must be an array")
    merged: dict[str, dict[str, Any]] = {}
    for raw in values:
        item = _seed_from_mapping(raw, allow_planner=allow_planner)
        existing = merged.get(item["seed_id"])
        if existing is None:
            merged[item["seed_id"]] = item
            continue
        provenance = _canonical_provenance(
            existing["provenance"] + item["provenance"],
            allow_planner=allow_planner,
        )
        merged[item["seed_id"]] = {
            "seed_id": item["seed_id"],
            "value": item["value"],
            "bucket": item["bucket"],
            "surface_forms": sorted(
                set(existing["surface_forms"]) | set(item["surface_forms"])
            ),
            "provenance": provenance,
        }
    return [merged[seed_id] for seed_id in sorted(merged)]


def _validate_seed_array(values: list[dict[str, Any]], *, allow_planner: bool) -> None:
    canonical: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        item = _seed_from_mapping(value, allow_planner=allow_planner)
        if item["seed_id"] in seen:
            raise ValidationError("E-PLANNING-SEED-BIND", "duplicate seed_id in sealed record")
        seen.add(item["seed_id"])
        canonical.append(item)
    canonical.sort(key=lambda item: item["seed_id"])
    if canonical != values:
        raise ValidationError("E-PLANNING-SEED-BIND", "sealed seeds are not canonical")


def _validate_goal_origin(
    *,
    user_goal_verbatim: str,
    neutral_goal_text: str,
    neutral_goal_origin: str,
) -> None:
    if neutral_goal_origin == "USER_VERBATIM_NO_SEEDS":
        if neutral_goal_text != user_goal_verbatim:
            raise ValidationError(
                "E-PLANNING-GOAL-BIND",
                "USER_VERBATIM_NO_SEEDS requires a byte-exact verbatim neutral goal",
            )
    elif neutral_goal_origin != "USER_CONFIRMED_SUMMARY":
        raise ValidationError("E-PLANNING-GOAL-BIND", "neutral_goal_origin is not recognized")


def _intake_identity_payload(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key not in {"intake_id", "intake_hash", "frozen_at"}
    }


def make_research_intake(
    *,
    user_goal_verbatim: str,
    neutral_goal_text: str,
    neutral_goal_origin: str,
    explicit_scope: dict[str, Any],
    seeds: list[dict[str, Any]],
    frozen_at: str,
) -> dict[str, Any]:
    user_goal = _preserved_nonempty(
        user_goal_verbatim,
        code="E-PLANNING-GOAL-BIND",
        field="user_goal_verbatim",
    )
    neutral_goal = _preserved_nonempty(
        neutral_goal_text,
        code="E-PLANNING-GOAL-BIND",
        field="neutral_goal_text",
    )
    _validate_goal_origin(
        user_goal_verbatim=user_goal,
        neutral_goal_text=neutral_goal,
        neutral_goal_origin=neutral_goal_origin,
    )
    base = {
        "schema_version": SCHEMA_VERSION,
        "user_goal_verbatim": user_goal,
        "neutral_goal_text": neutral_goal,
        "neutral_goal_origin": neutral_goal_origin,
        "explicit_scope": _canonical_explicit_scope(explicit_scope),
        "seeds": _merge_seed_items(seeds, allow_planner=False),
    }
    intake_id = phase0_derived_id(
        "ResearchIntake",
        base,
        code="E-PLANNING-INTAKE-BIND",
        label="research intake",
    )
    record = {
        **base,
        "intake_id": intake_id,
        "intake_hash": "sha256:" + "0" * 64,
        "frozen_at": frozen_at,
    }
    record["intake_hash"] = phase0_object_hash(
        record,
        omit=("intake_hash", "frozen_at"),
        code="E-PLANNING-INTAKE-BIND",
        label="research intake",
    )
    validate_schema("ResearchIntake", record)
    return record


def validate_research_intake(value: dict[str, Any]) -> dict[str, Any]:
    validate_schema("ResearchIntake", value)
    if _canonical_explicit_scope(value["explicit_scope"]) != value["explicit_scope"]:
        raise ValidationError("E-PLANNING-INTAKE-BIND", "intake scope is not canonical")
    _validate_seed_array(value["seeds"], allow_planner=False)
    _validate_goal_origin(
        user_goal_verbatim=value["user_goal_verbatim"],
        neutral_goal_text=value["neutral_goal_text"],
        neutral_goal_origin=value["neutral_goal_origin"],
    )
    expected_id = derived_id("ResearchIntake", _intake_identity_payload(value))
    expected_hash = object_hash(value, omit=("intake_hash", "frozen_at"))
    if value["intake_id"] != expected_id or value["intake_hash"] != expected_hash:
        raise ValidationError("E-PLANNING-INTAKE-BIND", "research intake identity changed")
    return copy.deepcopy(value)


def project_neutral_input(intake: dict[str, Any]) -> dict[str, Any]:
    intake = validate_research_intake(intake)
    payload = {
        "neutral_goal_text": intake["neutral_goal_text"],
        "explicit_scope": copy.deepcopy(intake["explicit_scope"]),
    }
    record = {
        "schema_version": SCHEMA_VERSION,
        "neutral_input_id": phase0_derived_id(
            "NeutralPlanningInput",
            payload,
            code="E-PLANNING-NEUTRAL-BIND",
            label="neutral planning input",
        ),
        **payload,
    }
    validate_schema("NeutralPlanningInput", record)
    return record


def validate_neutral_input(value: dict[str, Any]) -> dict[str, Any]:
    validate_schema("NeutralPlanningInput", value)
    if _canonical_explicit_scope(value["explicit_scope"]) != value["explicit_scope"]:
        raise ValidationError("E-PLANNING-NEUTRAL-BIND", "neutral input scope is not canonical")
    expected_id = derived_id(
        "NeutralPlanningInput",
        {
            "neutral_goal_text": value["neutral_goal_text"],
            "explicit_scope": value["explicit_scope"],
        },
    )
    if value["neutral_input_id"] != expected_id:
        raise ValidationError("E-PLANNING-NEUTRAL-BIND", "neutral input identity changed")
    return copy.deepcopy(value)


def _frame_identity_payload(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key not in {"frame_id", "frame_hash", "frozen_at"}
    }


def make_neutral_frame(
    *,
    intake: dict[str, Any],
    neutral_input: dict[str, Any],
    research_question: str,
    evidence_discovery_brief: str,
    selection_budget: dict[str, Any],
    frozen_at: str,
) -> dict[str, Any]:
    intake = validate_research_intake(intake)
    neutral_input = validate_neutral_input(neutral_input)
    expected_input = project_neutral_input(intake)
    if neutral_input != expected_input:
        raise ValidationError(
            "E-PLANNING-NEUTRAL-BIND",
            "neutral input differs from deterministic intake projection",
        )
    base = {
        "schema_version": SCHEMA_VERSION,
        "intake_id": intake["intake_id"],
        "neutral_input_id": neutral_input["neutral_input_id"],
        "research_question": nonempty(
            research_question,
            code="E-PLANNING-FRAME-BIND",
            message="research_question must be non-empty",
        ),
        "evidence_discovery_brief": _preserved_nonempty(
            evidence_discovery_brief,
            code="E-PLANNING-FRAME-BIND",
            field="evidence_discovery_brief",
        ),
        "selection_budget": _canonical_selection_budget(selection_budget),
    }
    frame_id = phase0_derived_id(
        "NeutralResearchFrame",
        base,
        code="E-PLANNING-FRAME-BIND",
        label="neutral research frame",
    )
    record = {
        **base,
        "frame_id": frame_id,
        "frame_hash": "sha256:" + "0" * 64,
        "frozen_at": frozen_at,
    }
    record["frame_hash"] = phase0_object_hash(
        record,
        omit=("frame_hash", "frozen_at"),
        code="E-PLANNING-FRAME-BIND",
        label="neutral research frame",
    )
    validate_schema("NeutralResearchFrame", record)
    return record


def validate_neutral_frame(value: dict[str, Any]) -> dict[str, Any]:
    validate_schema("NeutralResearchFrame", value)
    if value["research_question"] != value["research_question"].strip():
        raise ValidationError("E-PLANNING-FRAME-BIND", "research_question is not canonical")
    if _canonical_selection_budget(value["selection_budget"]) != value["selection_budget"]:
        raise ValidationError("E-PLANNING-FRAME-BIND", "selection_budget is not canonical")
    expected_id = derived_id("NeutralResearchFrame", _frame_identity_payload(value))
    expected_hash = object_hash(value, omit=("frame_hash", "frozen_at"))
    if value["frame_id"] != expected_id or value["frame_hash"] != expected_hash:
        raise ValidationError("E-PLANNING-FRAME-BIND", "neutral research frame identity changed")
    return copy.deepcopy(value)


def _execution_identity_payload(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key not in {"execution_id", "execution_hash", "recorded_at"}
    }


def _require_attestation_pair(assurance: Any, isolation_claim: Any) -> None:
    if (assurance, isolation_claim) not in _ATTESTATION_PAIRS:
        raise ValidationError(
            "E-PLANNING-ATTEST",
            "assurance and isolation_claim do not form a valid attestation state",
        )


def make_neutral_execution(
    *,
    neutral_input: dict[str, Any],
    neutral_frame: dict[str, Any],
    host: str,
    isolation_claim: str,
    assurance: str,
    recorded_at: str,
    store: ArtifactStore,
) -> dict[str, Any]:
    neutral_input = validate_neutral_input(neutral_input)
    neutral_frame = validate_neutral_frame(neutral_frame)
    if neutral_frame["neutral_input_id"] != neutral_input["neutral_input_id"]:
        raise ValidationError("E-PLANNING-NEUTRAL-BIND", "neutral frame binds another input")
    _require_attestation_pair(assurance, isolation_claim)
    input_artifact_id = put_planning_record(store, "NeutralPlanningInput", neutral_input)
    frame_artifact_id = put_planning_record(store, "NeutralResearchFrame", neutral_frame)
    base = {
        "schema_version": SCHEMA_VERSION,
        "record_kind": "NEUTRAL_PLANNING_EXECUTION",
        "neutral_input_artifact_id": input_artifact_id,
        "neutral_frame_artifact_id": frame_artifact_id,
        "host": nonempty(host, code="E-PLANNING-ATTEST", message="host must be non-empty"),
        "isolation_claim": isolation_claim,
        "assurance": assurance,
    }
    execution_id = phase0_derived_id(
        "NeutralPlanningExecution",
        base,
        code="E-PLANNING-EXECUTION-BIND",
        label="neutral planning execution",
    )
    record = {
        **base,
        "execution_id": execution_id,
        "execution_hash": "sha256:" + "0" * 64,
        "recorded_at": recorded_at,
    }
    record["execution_hash"] = phase0_object_hash(
        record,
        omit=("execution_hash", "recorded_at"),
        code="E-PLANNING-EXECUTION-BIND",
        label="neutral planning execution",
    )
    validate_schema("NeutralPlanningExecution", record)
    return record


def validate_neutral_execution(value: dict[str, Any]) -> dict[str, Any]:
    validate_schema("NeutralPlanningExecution", value)
    if value["host"] != value["host"].strip():
        raise ValidationError("E-PLANNING-EXECUTION-BIND", "execution host is not canonical")
    expected_id = derived_id("NeutralPlanningExecution", _execution_identity_payload(value))
    expected_hash = object_hash(value, omit=("execution_hash", "recorded_at"))
    if value["execution_id"] != expected_id or value["execution_hash"] != expected_hash:
        raise ValidationError(
            "E-PLANNING-EXECUTION-BIND",
            "neutral planning execution identity changed",
        )
    return copy.deepcopy(value)


def _validate_seed_references(
    seeds: list[dict[str, Any]],
    *,
    intake_seed_ids: set[str],
    frame_id: str,
) -> None:
    for seed in seeds:
        for provenance in seed["provenance"]:
            if provenance["origin"] != "PLANNER_DERIVED":
                continue
            for ref in provenance["derived_from"]:
                if ref["kind"] == "INTAKE_SEED":
                    if ref["seed_id"] not in intake_seed_ids:
                        raise ValidationError(
                            "E-PLANNING-SEED-REF",
                            f"planner seed references unknown intake seed {ref['seed_id']}",
                        )
                elif ref["frame_id"] != frame_id:
                    raise ValidationError(
                        "E-PLANNING-SEED-REF",
                        "planner seed references another neutral frame",
                    )


def _validate_seed_superset(
    intake_seeds: list[dict[str, Any]],
    plan_seeds: list[dict[str, Any]],
) -> None:
    intake_by_id = {item["seed_id"]: item for item in intake_seeds}
    plan_by_id = {item["seed_id"]: item for item in plan_seeds}
    for seed_id, intake_seed in intake_by_id.items():
        planned = plan_by_id.get(seed_id)
        if planned is None:
            raise ValidationError("E-PLANNING-SEED-DROP", f"plan dropped intake seed {seed_id}")
        intake_provenance = {_provenance_key(item) for item in intake_seed["provenance"]}
        planned_user_provenance = {
            _provenance_key(item)
            for item in planned["provenance"]
            if item["origin"] in _USER_ORIGINS
        }
        if planned_user_provenance != intake_provenance or not set(
            intake_seed["surface_forms"]
        ) <= set(planned["surface_forms"]):
            raise ValidationError(
                "E-PLANNING-SEED-DROP",
                f"plan did not preserve intake seed provenance for {seed_id}",
            )
    for seed_id, planned in plan_by_id.items():
        user_provenance = [
            item for item in planned["provenance"] if item["origin"] in _USER_ORIGINS
        ]
        planner_provenance = [
            item for item in planned["provenance"] if item["origin"] == "PLANNER_DERIVED"
        ]
        if seed_id not in intake_by_id and (user_provenance or not planner_provenance):
            raise ValidationError(
                "E-PLANNING-SEED-PROV",
                "planner-added seeds require planner provenance and cannot invent user provenance",
            )


def _validate_execution_bindings(
    *,
    intake: dict[str, Any],
    neutral_input: dict[str, Any],
    neutral_frame: dict[str, Any],
    neutral_execution: dict[str, Any],
) -> None:
    if neutral_execution["neutral_input_artifact_id"] != artifact_id_for(
        canonical_dumps(neutral_input)
    ) or neutral_execution["neutral_frame_artifact_id"] != artifact_id_for(
        canonical_dumps(neutral_frame)
    ):
        raise ValidationError("E-PLANNING-PAIR-BIND", "execution binds another input or frame")
    if (
        neutral_frame["intake_id"] != intake["intake_id"]
        or neutral_frame["neutral_input_id"] != neutral_input["neutral_input_id"]
    ):
        raise ValidationError("E-PLANNING-PAIR-BIND", "intake, neutral input, and frame differ")
    expected_input = project_neutral_input(intake)
    if neutral_input != expected_input:
        raise ValidationError(
            "E-PLANNING-NEUTRAL-BIND",
            "stored neutral input differs from deterministic intake projection",
        )


def _plan_identity_payload(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key not in {"plan_id", "plan_hash", "frozen_at"}
    }


def make_exploration_plan(
    *,
    intake: dict[str, Any],
    neutral_frame: dict[str, Any],
    neutral_execution: dict[str, Any],
    exploration_seeds: list[dict[str, Any]],
    diversity: dict[str, Any],
    frozen_at: str,
) -> dict[str, Any]:
    intake = validate_research_intake(intake)
    neutral_frame = validate_neutral_frame(neutral_frame)
    neutral_execution = validate_neutral_execution(neutral_execution)
    neutral_input = project_neutral_input(intake)
    _validate_execution_bindings(
        intake=intake,
        neutral_input=neutral_input,
        neutral_frame=neutral_frame,
        neutral_execution=neutral_execution,
    )
    seeds = _merge_seed_items(exploration_seeds, allow_planner=True)
    _validate_seed_superset(intake["seeds"], seeds)
    intake_seed_ids = {item["seed_id"] for item in intake["seeds"]}
    _validate_seed_references(
        seeds,
        intake_seed_ids=intake_seed_ids,
        frame_id=neutral_frame["frame_id"],
    )
    canonical_diversity = _canonical_diversity(diversity)
    target_leads = neutral_frame["selection_budget"]["target_leads"]
    if (
        canonical_diversity["min_works"] > target_leads
        or canonical_diversity["min_interaction_families"] > target_leads
    ):
        raise ValidationError(
            "E-PLANNING-BUDGET",
            "diversity minima cannot exceed the neutral frame target_leads budget",
        )
    base = {
        "schema_version": SCHEMA_VERSION,
        "intake_id": intake["intake_id"],
        "neutral_frame": copy.deepcopy(neutral_frame),
        "neutral_execution_id": neutral_execution["execution_id"],
        "explicit_scope": copy.deepcopy(intake["explicit_scope"]),
        "exploration_seeds": seeds,
        "diversity": canonical_diversity,
        "seed_blindness_assurance": neutral_execution["assurance"],
    }
    plan_id = phase0_derived_id(
        "ExplorationPlan",
        base,
        code="E-PLANNING-PLAN-BIND",
        label="exploration plan",
    )
    record = {
        **base,
        "plan_id": plan_id,
        "plan_hash": "sha256:" + "0" * 64,
        "frozen_at": frozen_at,
    }
    record["plan_hash"] = phase0_object_hash(
        record,
        omit=("plan_hash", "frozen_at"),
        code="E-PLANNING-PLAN-BIND",
        label="exploration plan",
    )
    validate_schema("ExplorationPlan", record)
    return record


def validate_exploration_plan(value: dict[str, Any]) -> dict[str, Any]:
    validate_schema("ExplorationPlan", value)
    frame = validate_neutral_frame(value["neutral_frame"])
    if frame["intake_id"] != value["intake_id"]:
        raise ValidationError("E-PLANNING-PLAN-BIND", "plan embeds a frame for another intake")
    if _canonical_explicit_scope(value["explicit_scope"]) != value["explicit_scope"]:
        raise ValidationError("E-PLANNING-PLAN-BIND", "plan scope is not canonical")
    _validate_seed_array(value["exploration_seeds"], allow_planner=True)
    _canonical_diversity(value["diversity"])
    known_seed_ids = {item["seed_id"] for item in value["exploration_seeds"]}
    _validate_seed_references(
        value["exploration_seeds"],
        intake_seed_ids=known_seed_ids,
        frame_id=frame["frame_id"],
    )
    target = frame["selection_budget"]["target_leads"]
    if value["diversity"]["min_works"] > target or value["diversity"][
        "min_interaction_families"
    ] > target:
        raise ValidationError("E-PLANNING-BUDGET", "plan diversity exceeds target_leads")
    expected_id = derived_id("ExplorationPlan", _plan_identity_payload(value))
    expected_hash = object_hash(value, omit=("plan_hash", "frozen_at"))
    if value["plan_id"] != expected_id or value["plan_hash"] != expected_hash:
        raise ValidationError("E-PLANNING-PLAN-BIND", "exploration plan identity changed")
    return copy.deepcopy(value)


def _validate_plan_bindings(
    *,
    intake: dict[str, Any],
    neutral_input: dict[str, Any],
    neutral_frame: dict[str, Any],
    neutral_execution: dict[str, Any],
    plan: dict[str, Any],
) -> None:
    _validate_execution_bindings(
        intake=intake,
        neutral_input=neutral_input,
        neutral_frame=neutral_frame,
        neutral_execution=neutral_execution,
    )
    if (
        plan["intake_id"] != intake["intake_id"]
        or plan["neutral_frame"] != neutral_frame
        or plan["explicit_scope"] != intake["explicit_scope"]
        or plan["neutral_execution_id"] != neutral_execution["execution_id"]
        or plan["seed_blindness_assurance"] != neutral_execution["assurance"]
    ):
        raise ValidationError("E-PLANNING-PAIR-BIND", "plan binds another planning record set")
    _validate_seed_superset(intake["seeds"], plan["exploration_seeds"])
    _validate_seed_references(
        plan["exploration_seeds"],
        intake_seed_ids={item["seed_id"] for item in intake["seeds"]},
        frame_id=neutral_frame["frame_id"],
    )


def compile_exploration_brief(plan: dict[str, Any]) -> dict[str, Any]:
    """Compile only neutral-frame semantics and typed hard scope into a Brief."""

    plan = validate_exploration_plan(plan)
    frame = plan["neutral_frame"]
    scope = {
        "genres": copy.deepcopy(plan["explicit_scope"]["genres"]["include"]),
        "target_leads": frame["selection_budget"]["target_leads"],
        "max_leads_per_work": frame["selection_budget"]["max_leads_per_work"],
    }
    excluded = plan["explicit_scope"]["genres"]["exclude"]
    if excluded:
        scope["avoid"] = copy.deepcopy(excluded)
    return make_exploration_brief(
        research_question=frame["research_question"],
        evidence_discovery_brief=frame["evidence_discovery_brief"],
        scope=scope,
        frozen_at=plan["frozen_at"],
    )


_PLANNING_VALIDATORS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "ResearchIntake": validate_research_intake,
    "NeutralPlanningInput": validate_neutral_input,
    "NeutralPlanningExecution": validate_neutral_execution,
    "NeutralResearchFrame": validate_neutral_frame,
    "ExplorationPlan": validate_exploration_plan,
    "ExplorationBrief": validate_exploration_brief,
}


def put_planning_record(store: ArtifactStore, kind: str, record: dict[str, Any]) -> str:
    try:
        validator = _PLANNING_VALIDATORS[kind]
    except KeyError as exc:
        raise ValidationError("E-PLANNING-KIND", f"unsupported planning record kind {kind}") from exc
    return store.put(canonical_dumps(validator(record)))


def read_planning_record(
    store: ArtifactStore,
    artifact_id: str,
    kind: str,
) -> dict[str, Any]:
    if (
        not isinstance(artifact_id, str)
        or not artifact_id.startswith("sha256:")
        or not is_real_sha256(artifact_id)
    ):
        raise ValidationError("E-PLANNING-CAS", f"{kind} artifact id is invalid")
    try:
        validator = _PLANNING_VALIDATORS[kind]
    except KeyError as exc:
        raise ValidationError("E-PLANNING-KIND", f"unsupported planning record kind {kind}") from exc
    raw = store.get(artifact_id)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("E-PLANNING-CAS", f"{kind} artifact is not JSON") from exc
    try:
        canonical = canonical_dumps(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("E-PLANNING-CAS", f"{kind} artifact is not canonical JSON") from exc
    if not isinstance(value, dict) or raw != canonical:
        raise ValidationError("E-PLANNING-CAS", f"{kind} artifact is not canonical JSON")
    return validator(value)


def planning_contract_hash(repo_root: pathlib.Path) -> str:
    payload = []
    for relative in PLANNING_CONTRACT_DEPENDENCIES:
        path = pathlib.Path(repo_root) / relative
        if not path.is_file():
            raise ValidationError(
                "E-PLANNING-BUILD-BIND",
                f"missing planning compiler contract dependency {relative}",
            )
        payload.append(
            {
                "path": relative,
                "sha256": digest_prefix(sha256_bytes(path.read_bytes())),
            }
        )
    return object_hash({"planning_contract_dependencies": payload}, omit=())


def _package_version() -> str:
    try:
        return metadata.version("xhnovel-pipeline")
    except metadata.PackageNotFoundError:
        return "0.2.0.dev0"


def planning_compiler_build(repo_root: pathlib.Path) -> dict[str, str]:
    root = pathlib.Path(repo_root)
    commit = repository_commit(root)
    source_hash = build_source_hash(root)
    contract_hash = planning_contract_hash(root)
    compiler_build_id = derived_id(
        "PlanningCompilerBuild",
        {
            "compiler_contract_version": PLANNING_COMPILER_CONTRACT_VERSION,
            "source_tree_hash": source_hash,
            "planning_contract_hash": contract_hash,
            "repository_commit": commit,
        },
    )
    return {
        "compiler_build_id": compiler_build_id,
        "repository_commit": commit,
        "source_tree_hash": source_hash,
        "planning_contract_hash": contract_hash,
    }


def _receipt_identity_payload(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(item)
        for key, item in value.items()
        if key not in {"receipt_id", "receipt_hash"}
    }


def make_planning_receipt(
    *,
    intake: dict[str, Any],
    neutral_input: dict[str, Any],
    neutral_frame: dict[str, Any],
    neutral_execution: dict[str, Any],
    plan: dict[str, Any],
    brief: dict[str, Any],
    compiled_at: str,
    store: ArtifactStore,
    repo_root: pathlib.Path,
) -> dict[str, Any]:
    intake = validate_research_intake(intake)
    neutral_input = validate_neutral_input(neutral_input)
    neutral_frame = validate_neutral_frame(neutral_frame)
    neutral_execution = validate_neutral_execution(neutral_execution)
    plan = validate_exploration_plan(plan)
    brief = validate_exploration_brief(brief)
    _validate_plan_bindings(
        intake=intake,
        neutral_input=neutral_input,
        neutral_frame=neutral_frame,
        neutral_execution=neutral_execution,
        plan=plan,
    )
    if compile_exploration_brief(plan) != brief:
        raise ValidationError("E-PLANNING-BRIEF-BIND", "brief differs from deterministic plan compile")
    artifact_ids = {
        "intake_artifact_id": put_planning_record(store, "ResearchIntake", intake),
        "neutral_input_artifact_id": put_planning_record(
            store, "NeutralPlanningInput", neutral_input
        ),
        "neutral_frame_artifact_id": put_planning_record(
            store, "NeutralResearchFrame", neutral_frame
        ),
        "neutral_execution_artifact_id": put_planning_record(
            store, "NeutralPlanningExecution", neutral_execution
        ),
        "plan_artifact_id": put_planning_record(store, "ExplorationPlan", plan),
        "compiled_brief_artifact_id": put_planning_record(
            store, "ExplorationBrief", brief
        ),
    }
    build = planning_compiler_build(repo_root)
    base = {
        "schema_version": SCHEMA_VERSION,
        "receipt_kind": "PLANNING_COMPILATION",
        "compiler_contract_version": PLANNING_COMPILER_CONTRACT_VERSION,
        **build,
        "package_version": _package_version(),
        "rebuild_hint": {
            "build_command": "python -m build --wheel",
            "wheel_sha256": None,
        },
        **artifact_ids,
        "intake_id": intake["intake_id"],
        "intake_hash": intake["intake_hash"],
        "neutral_input_id": neutral_input["neutral_input_id"],
        "neutral_frame_id": neutral_frame["frame_id"],
        "neutral_frame_hash": neutral_frame["frame_hash"],
        "neutral_execution_id": neutral_execution["execution_id"],
        "neutral_execution_hash": neutral_execution["execution_hash"],
        "plan_id": plan["plan_id"],
        "plan_hash": plan["plan_hash"],
        "compiled_brief_id": brief["brief_id"],
        "compiled_brief_hash": brief["brief_hash"],
        "compiled_at": compiled_at,
    }
    receipt_id = phase0_derived_id(
        "PlanningCompilationReceipt",
        base,
        code="E-PLANNING-RECEIPT-BIND",
        label="planning compilation receipt",
    )
    record = {
        **base,
        "receipt_id": receipt_id,
        "receipt_hash": "sha256:" + "0" * 64,
    }
    record["receipt_hash"] = phase0_object_hash(
        record,
        omit=("receipt_hash",),
        code="E-PLANNING-RECEIPT-BIND",
        label="planning compilation receipt",
    )
    validate_schema("PlanningCompilationReceipt", record)
    return record


def validate_planning_receipt(value: dict[str, Any]) -> dict[str, Any]:
    validate_schema("PlanningCompilationReceipt", value)
    expected_id = derived_id("PlanningCompilationReceipt", _receipt_identity_payload(value))
    expected_hash = object_hash(value, omit=("receipt_hash",))
    if value["receipt_id"] != expected_id or value["receipt_hash"] != expected_hash:
        raise ValidationError("E-PLANNING-RECEIPT-BIND", "planning receipt identity changed")
    return copy.deepcopy(value)


_MANIFEST_FIELDS = {
    "schema_version",
    "record_kind",
    "intake_artifact_id",
    "neutral_input_artifact_id",
    "neutral_frame_artifact_id",
    "neutral_execution_artifact_id",
    "plan_artifact_id",
    "compiled_brief_artifact_id",
    "receipt_id",
}


def _read_manifest(root: pathlib.Path) -> dict[str, Any]:
    path = root / "planning-manifest.json"
    if not path.is_file():
        raise ValidationError("E-PLANNING-MANIFEST", "planning manifest is missing")
    manifest = _read_json(path, label="planning manifest")
    if (
        not set(manifest) <= _MANIFEST_FIELDS
        or manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("record_kind") != "PHASE_MINUS1_PLANNING_MANIFEST"
    ):
        raise ValidationError("E-PLANNING-MANIFEST", "planning manifest is not recognized")
    return manifest


def _assert_manifest_compatible(
    manifest: dict[str, Any],
    expected: dict[str, Any],
) -> None:
    for key, item in expected.items():
        if key in manifest and manifest[key] != item:
            raise ValidationError(
                "E-PLANNING-MANIFEST",
                f"planning manifest already binds a different {key}",
            )


def _update_manifest(root: pathlib.Path, additions: dict[str, Any]) -> dict[str, Any]:
    path = root / "planning-manifest.json"
    if path.is_file():
        manifest = _read_manifest(root)
    else:
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "record_kind": "PHASE_MINUS1_PLANNING_MANIFEST",
        }
    for key, item in additions.items():
        if key not in _MANIFEST_FIELDS:
            raise ValidationError("E-PLANNING-MANIFEST", f"unknown manifest field {key}")
        if key in manifest and manifest[key] != item:
            raise ValidationError(
                "E-PLANNING-MANIFEST",
                f"planning manifest already binds a different {key}",
            )
        manifest[key] = item
    root.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(_json_bytes(manifest))
    os.replace(temp, path)
    return manifest


def seal_intake_from_draft(
    input_path: pathlib.Path,
    planning_root: pathlib.Path,
) -> SealedIntake:
    value = _read_json(pathlib.Path(input_path), label="research intake draft")
    required = {
        "user_goal_verbatim",
        "neutral_goal_text",
        "neutral_goal_origin",
        "explicit_scope",
        "seeds",
        "frozen_at",
    }
    if not required <= set(value) or not set(value) <= required | {"schema_version"}:
        raise ValidationError("E-PLANNING-INTAKE", "intake draft has an invalid field set")
    if "schema_version" in value and value["schema_version"] != SCHEMA_VERSION:
        raise ValidationError("E-PLANNING-INTAKE", "intake draft schema_version is unsupported")
    intake = make_research_intake(
        user_goal_verbatim=value["user_goal_verbatim"],
        neutral_goal_text=value["neutral_goal_text"],
        neutral_goal_origin=value["neutral_goal_origin"],
        explicit_scope=value["explicit_scope"],
        seeds=value["seeds"],
        frozen_at=value["frozen_at"],
    )
    neutral_input = project_neutral_input(intake)
    root = pathlib.Path(planning_root)
    prospective = {
        "intake_artifact_id": artifact_id_for(canonical_dumps(intake)),
        "neutral_input_artifact_id": artifact_id_for(canonical_dumps(neutral_input)),
    }
    if (root / "planning-manifest.json").is_file():
        _assert_manifest_compatible(_read_manifest(root), prospective)
    store = ArtifactStore(root / "objects")
    intake_artifact_id = put_planning_record(store, "ResearchIntake", intake)
    neutral_input_artifact_id = put_planning_record(
        store, "NeutralPlanningInput", neutral_input
    )
    intake_path = root / "intake.json"
    neutral_input_path = root / "neutral-planning-input.json"
    write_immutable(intake_path, _json_bytes(intake))
    write_immutable(neutral_input_path, _json_bytes(neutral_input))
    _update_manifest(
        root,
        {
            "intake_artifact_id": intake_artifact_id,
            "neutral_input_artifact_id": neutral_input_artifact_id,
        },
    )
    return SealedIntake(
        planning_root=root,
        intake_path=intake_path,
        neutral_input_path=neutral_input_path,
        intake=intake,
        neutral_input=neutral_input,
        intake_artifact_id=intake_artifact_id,
        neutral_input_artifact_id=neutral_input_artifact_id,
    )


def seal_neutral_frame_from_drafts(
    frame_draft_path: pathlib.Path,
    attestation_path: pathlib.Path,
    planning_root: pathlib.Path,
) -> SealedNeutralFrame:
    root = pathlib.Path(planning_root)
    manifest = _read_manifest(root)
    for field in ("intake_artifact_id", "neutral_input_artifact_id"):
        if field not in manifest:
            raise ValidationError("E-PLANNING-MANIFEST", f"planning manifest lacks {field}")
    store = ArtifactStore(root / "objects")
    intake = read_planning_record(store, manifest["intake_artifact_id"], "ResearchIntake")
    neutral_input = read_planning_record(
        store,
        manifest["neutral_input_artifact_id"],
        "NeutralPlanningInput",
    )
    draft = _read_json(pathlib.Path(frame_draft_path), label="neutral frame draft")
    required = {
        "neutral_input_id",
        "research_question",
        "evidence_discovery_brief",
        "selection_budget",
        "frozen_at",
    }
    if not required <= set(draft) or not set(draft) <= required | {"schema_version"}:
        raise ValidationError("E-PLANNING-FRAME", "neutral frame draft has an invalid field set")
    if "schema_version" in draft and draft["schema_version"] != SCHEMA_VERSION:
        raise ValidationError("E-PLANNING-FRAME", "neutral frame draft schema_version is unsupported")
    if draft["neutral_input_id"] != neutral_input["neutral_input_id"]:
        raise ValidationError("E-PLANNING-NEUTRAL-BIND", "frame draft echoed another neutral input")
    attestation = _read_json(pathlib.Path(attestation_path), label="neutral planning attestation")
    if set(attestation) != {"host", "isolation_claim", "assurance"}:
        raise ValidationError("E-PLANNING-ATTEST", "attestation has an invalid field set")
    _require_attestation_pair(attestation["assurance"], attestation["isolation_claim"])
    frame = make_neutral_frame(
        intake=intake,
        neutral_input=neutral_input,
        research_question=draft["research_question"],
        evidence_discovery_brief=draft["evidence_discovery_brief"],
        selection_budget=draft["selection_budget"],
        frozen_at=draft["frozen_at"],
    )
    execution = make_neutral_execution(
        neutral_input=neutral_input,
        neutral_frame=frame,
        host=attestation["host"],
        isolation_claim=attestation["isolation_claim"],
        assurance=attestation["assurance"],
        recorded_at=draft["frozen_at"],
        store=store,
    )
    frame_artifact_id = put_planning_record(store, "NeutralResearchFrame", frame)
    execution_artifact_id = put_planning_record(
        store, "NeutralPlanningExecution", execution
    )
    _assert_manifest_compatible(
        manifest,
        {
            "neutral_frame_artifact_id": frame_artifact_id,
            "neutral_execution_artifact_id": execution_artifact_id,
        },
    )
    frame_path = root / "neutral-research-frame.json"
    execution_path = root / "neutral-planning-execution.json"
    write_immutable(frame_path, _json_bytes(frame))
    write_immutable(execution_path, _json_bytes(execution))
    _update_manifest(
        root,
        {
            "neutral_frame_artifact_id": frame_artifact_id,
            "neutral_execution_artifact_id": execution_artifact_id,
        },
    )
    return SealedNeutralFrame(
        planning_root=root,
        neutral_frame_path=frame_path,
        neutral_execution_path=execution_path,
        neutral_frame=frame,
        neutral_execution=execution,
        neutral_frame_artifact_id=frame_artifact_id,
        neutral_execution_artifact_id=execution_artifact_id,
    )


def compile_exploration_plan_from_request(
    request_path: pathlib.Path,
    planning_root: pathlib.Path,
    *,
    repo_root: pathlib.Path,
) -> CompiledExplorationPlan:
    request = _read_json(pathlib.Path(request_path), label="exploration plan compile request")
    validate_schema("ExplorationPlanCompileRequest", request)
    root = pathlib.Path(planning_root)
    existing_manifest: dict[str, Any] | None = None
    if (root / "planning-manifest.json").is_file():
        existing_manifest = _read_manifest(root)
        _assert_manifest_compatible(
            existing_manifest,
            {
                "intake_artifact_id": request["intake_artifact_id"],
                "neutral_frame_artifact_id": request["neutral_frame_artifact_id"],
                "neutral_execution_artifact_id": request[
                    "neutral_execution_artifact_id"
                ],
            },
        )
    store = ArtifactStore(root / "objects")
    intake = read_planning_record(store, request["intake_artifact_id"], "ResearchIntake")
    frame = read_planning_record(
        store,
        request["neutral_frame_artifact_id"],
        "NeutralResearchFrame",
    )
    execution = read_planning_record(
        store,
        request["neutral_execution_artifact_id"],
        "NeutralPlanningExecution",
    )
    neutral_input = read_planning_record(
        store,
        execution["neutral_input_artifact_id"],
        "NeutralPlanningInput",
    )
    _validate_execution_bindings(
        intake=intake,
        neutral_input=neutral_input,
        neutral_frame=frame,
        neutral_execution=execution,
    )
    plan = make_exploration_plan(
        intake=intake,
        neutral_frame=frame,
        neutral_execution=execution,
        exploration_seeds=request["strategy"]["exploration_seeds"],
        diversity=request["strategy"]["diversity"],
        frozen_at=request["compiled_at"],
    )
    brief = compile_exploration_brief(plan)
    receipt = make_planning_receipt(
        intake=intake,
        neutral_input=neutral_input,
        neutral_frame=frame,
        neutral_execution=execution,
        plan=plan,
        brief=brief,
        compiled_at=request["compiled_at"],
        store=store,
        repo_root=repo_root,
    )
    manifest_additions = {
        "intake_artifact_id": receipt["intake_artifact_id"],
        "neutral_input_artifact_id": receipt["neutral_input_artifact_id"],
        "neutral_frame_artifact_id": receipt["neutral_frame_artifact_id"],
        "neutral_execution_artifact_id": receipt["neutral_execution_artifact_id"],
        "plan_artifact_id": receipt["plan_artifact_id"],
        "compiled_brief_artifact_id": receipt["compiled_brief_artifact_id"],
        "receipt_id": receipt["receipt_id"],
    }
    if existing_manifest is not None:
        _assert_manifest_compatible(existing_manifest, manifest_additions)
    plan_path = root / "exploration-plan.json"
    brief_path = root / "exploration-brief.json"
    receipt_path = root / "planning-compilation-receipt.json"
    write_immutable(plan_path, _json_bytes(plan))
    write_immutable(brief_path, _json_bytes(brief))
    write_immutable(receipt_path, _json_bytes(receipt))
    _update_manifest(root, manifest_additions)
    return CompiledExplorationPlan(
        planning_root=root,
        plan_path=plan_path,
        brief_path=brief_path,
        receipt_path=receipt_path,
        plan=plan,
        brief=brief,
        receipt=receipt,
    )


def _receipt_record_fields(
    *,
    intake: dict[str, Any],
    neutral_input: dict[str, Any],
    frame: dict[str, Any],
    execution: dict[str, Any],
    plan: dict[str, Any],
    brief: dict[str, Any],
) -> dict[str, str]:
    return {
        "intake_id": intake["intake_id"],
        "intake_hash": intake["intake_hash"],
        "neutral_input_id": neutral_input["neutral_input_id"],
        "neutral_frame_id": frame["frame_id"],
        "neutral_frame_hash": frame["frame_hash"],
        "neutral_execution_id": execution["execution_id"],
        "neutral_execution_hash": execution["execution_hash"],
        "plan_id": plan["plan_id"],
        "plan_hash": plan["plan_hash"],
        "compiled_brief_id": brief["brief_id"],
        "compiled_brief_hash": brief["brief_hash"],
    }


def validate_planning_handoff(
    receipt_path: pathlib.Path,
    handoff_path: pathlib.Path,
    *,
    planning_root: pathlib.Path,
    phase0_root: pathlib.Path,
    repo_root: pathlib.Path,
) -> dict[str, Any]:
    receipt = validate_planning_receipt(
        _read_json(pathlib.Path(receipt_path), label="planning compilation receipt")
    )
    current_build = planning_compiler_build(repo_root)
    if receipt["compiler_build_id"] != current_build["compiler_build_id"]:
        raise ValidationError(
            "E-PLANNING-BUILD-BIND",
            "planning receipt requires a different fixed compiler build",
        )
    store = ArtifactStore(pathlib.Path(planning_root) / "objects")
    intake = read_planning_record(store, receipt["intake_artifact_id"], "ResearchIntake")
    neutral_input = read_planning_record(
        store,
        receipt["neutral_input_artifact_id"],
        "NeutralPlanningInput",
    )
    frame = read_planning_record(
        store,
        receipt["neutral_frame_artifact_id"],
        "NeutralResearchFrame",
    )
    execution = read_planning_record(
        store,
        receipt["neutral_execution_artifact_id"],
        "NeutralPlanningExecution",
    )
    plan = read_planning_record(store, receipt["plan_artifact_id"], "ExplorationPlan")
    brief = read_planning_record(
        store,
        receipt["compiled_brief_artifact_id"],
        "ExplorationBrief",
    )
    expected_fields = _receipt_record_fields(
        intake=intake,
        neutral_input=neutral_input,
        frame=frame,
        execution=execution,
        plan=plan,
        brief=brief,
    )
    if any(receipt[field] != expected for field, expected in expected_fields.items()):
        raise ValidationError(
            "E-PLANNING-RECEIPT-REPLAY",
            "receipt id/hash pairs differ from referenced planning artifacts",
        )
    try:
        _validate_plan_bindings(
            intake=intake,
            neutral_input=neutral_input,
            neutral_frame=frame,
            neutral_execution=execution,
            plan=plan,
        )
        if compile_exploration_brief(plan) != brief:
            raise ValidationError(
                "E-PLANNING-BRIEF-BIND",
                "stored brief differs from deterministic plan compile",
            )
        rebuilt = make_planning_receipt(
            intake=intake,
            neutral_input=neutral_input,
            neutral_frame=frame,
            neutral_execution=execution,
            plan=plan,
            brief=brief,
            compiled_at=receipt["compiled_at"],
            store=store,
            repo_root=repo_root,
        )
    except ValidationError as exc:
        if exc.code == "E-PLANNING-BUILD-BIND":
            raise
        raise ValidationError(
            "E-PLANNING-RECEIPT-REPLAY",
            "planning artifacts do not reproduce the compilation receipt",
        ) from exc
    if rebuilt != receipt:
        raise ValidationError(
            "E-PLANNING-RECEIPT-REPLAY",
            "planning compilation receipt differs from exact replay",
        )
    validated_handoff = resolve_validated_handoff_input(
        pathlib.Path(handoff_path),
        phase0_root=pathlib.Path(phase0_root),
    )
    if validated_handoff.handoff["builder"][
        "exploration_brief_artifact_id"
    ] != receipt["compiled_brief_artifact_id"]:
        raise ValidationError(
            "E-PLANNING-HANDOFF-CLOSURE",
            "evidence handoff does not reference the compiled planning brief",
        )
    return receipt
