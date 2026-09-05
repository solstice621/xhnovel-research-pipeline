"""Seal local observation requirements, host profile assessments and search leads.

Semantic locality, coverage and isolation remain explicit host attestations. This
module proves structure, immutable provenance and bindings, never semantic truth.
"""
from __future__ import annotations

import copy
import pathlib
from typing import Any

from .constants import SCHEMA_VERSION
from .errors import ValidationError
from .generic_extraction import _resolve_pointer
from .generic_profile import ExtractionProfile, load_extraction_profile
from .ids import derived_id
from .observation_common import (
    SealedRecord, get_record, publish_record, read_json, seal_record,
    validate_record_identity,
)
from .phase0_common import require_fields
from .phase0_handoff import _canonical_lead_source, _canonical_work_claim
from .phase0_planning import (
    _require_attestation_pair, project_neutral_input, validate_neutral_input,
    validate_research_intake,
)
from .schema import schema_validation_session


def _draft(value: dict[str, Any] | pathlib.Path) -> dict[str, Any]:
    return copy.deepcopy(value) if isinstance(value, dict) else read_json(pathlib.Path(value))


def _fields(value: dict[str, Any], required: set[str], optional: set[str] | None = None) -> None:
    require_fields(value, required=required, optional=optional, code="E-OBSERVATION-DRAFT", label="observation draft")


def _definition(research_root: pathlib.Path, artifact_id: str, *, root: pathlib.Path | None = None) -> dict[str, Any]:
    return validate_observation_definition(get_record(research_root, artifact_id), research_root, root=root)


def _requirements(requirements: list[dict[str, Any]], *, sealed: bool) -> list[dict[str, Any]]:
    if not isinstance(requirements, list) or not requirements:
        raise ValidationError("E-OBSERVATION-REQUIREMENT", "requirements must be a non-empty array")
    fields = {"statement", "applies_to", "necessity", "locality", "origin", "origin_pointer", "origin_quote"}
    result = []
    for requirement in requirements:
        if not isinstance(requirement, dict):
            raise ValidationError("E-OBSERVATION-REQUIREMENT", "requirement must be an object")
        _fields(requirement, fields | ({"requirement_id"} if sealed else set()))
        body = {key: value for key, value in requirement.items() if key != "requirement_id"}
        identifier = derived_id("ObservationRequirement", body)
        if sealed and requirement["requirement_id"] != identifier:
            raise ValidationError("E-OBSERVATION-REQUIREMENT", "requirement identity changed")
        result.append({**body, "requirement_id": identifier})
    if len({item["requirement_id"] for item in result}) != len(result):
        raise ValidationError("E-OBSERVATION-REQUIREMENT", "duplicate requirements")
    return sorted(result, key=lambda item: item["requirement_id"])


@schema_validation_session()
def seal_observation_definition_from_draft(
    draft_or_path: dict[str, Any] | pathlib.Path, research_root: pathlib.Path, *, root: pathlib.Path | None = None,
) -> SealedRecord:
    draft = _draft(draft_or_path)
    _fields(draft, {"intake_artifact_id", "neutral_input_artifact_id", "research_question", "inclusion_rules", "exclusion_rules", "required_distinctions", "requirements", "locality", "locality_rationale", "decomposition_status", "authoring", "frozen_at"}, {"previous_definition_artifact_id"})
    intake = validate_research_intake(get_record(research_root, draft["intake_artifact_id"]))
    neutral = validate_neutral_input(get_record(research_root, draft["neutral_input_artifact_id"]))
    requirements = _requirements(draft["requirements"], sealed=False)
    body = {**draft, "schema_version": SCHEMA_VERSION, "intake_id": intake["intake_id"], "neutral_input_id": neutral["neutral_input_id"], "requirements": requirements,
            "executable_requirement_ids": [item["requirement_id"] for item in requirements if item["locality"] == "UNIT_LOCAL"],
            "unresolved_requirement_ids": [item["requirement_id"] for item in requirements if item["locality"] == "CROSS_UNIT"]}
    record = seal_record("ObservationDefinition", body, id_field="definition_id", hash_field="definition_hash")
    validate_observation_definition(record, research_root, root=root)
    return publish_record(research_root, "ObservationDefinition", record)


@schema_validation_session()
def validate_observation_definition(record: dict[str, Any], research_root: pathlib.Path, *, root: pathlib.Path | None = None) -> dict[str, Any]:
    value = validate_record_identity(record, "ObservationDefinition", id_field="definition_id", hash_field="definition_hash")
    intake = validate_research_intake(get_record(research_root, value["intake_artifact_id"]))
    neutral = validate_neutral_input(get_record(research_root, value["neutral_input_artifact_id"]))
    if value["intake_id"] != intake["intake_id"] or value["neutral_input_id"] != neutral["neutral_input_id"] or neutral != project_neutral_input(intake):
        raise ValidationError("E-OBSERVATION-PROVENANCE", "definition does not bind its intake neutral projection")
    authoring = value["authoring"]
    _require_attestation_pair(authoring["assurance"], authoring["isolation_claim"])
    if authoring["input_artifact_id"] != value["neutral_input_artifact_id"]:
        raise ValidationError("E-OBSERVATION-PROVENANCE", "authoring must bind this definition's actual neutral input")
    requirements = _requirements(value["requirements"], sealed=True)
    if requirements != value["requirements"]:
        raise ValidationError("E-OBSERVATION-REQUIREMENT", "requirements are not canonical")
    for requirement in requirements:
        origin, pointer, quote = requirement["origin"], requirement["origin_pointer"], requirement["origin_quote"]
        if origin == "HOST_INTERPRETATION":
            if pointer is not None or quote is not None:
                raise ValidationError("E-OBSERVATION-PROVENANCE", "host interpretation must not claim user confirmation")
        else:
            if not isinstance(pointer, str) or not isinstance(quote, str) or not quote.strip():
                raise ValidationError("E-OBSERVATION-PROVENANCE", "explicit provenance requires a pointer and exact quote")
            if (origin == "NEUTRAL_GOAL" and pointer != "/neutral_goal_text") or (origin == "EXPLICIT_SCOPE" and not pointer.startswith("/explicit_scope/")):
                raise ValidationError("E-OBSERVATION-PROVENANCE", "requirement origin points outside the declared source")
            source = _resolve_pointer(neutral, pointer)
            if not isinstance(source, str) or quote not in source:
                raise ValidationError("E-OBSERVATION-PROVENANCE", "requirement provenance quote is not present")
    local = [item["requirement_id"] for item in requirements if item["locality"] == "UNIT_LOCAL"]
    cross = [item["requirement_id"] for item in requirements if item["locality"] == "CROSS_UNIT"]
    if local != value["executable_requirement_ids"] or cross != value["unresolved_requirement_ids"]:
        raise ValidationError("E-OBSERVATION-LOCALITY", "requirement partitions changed")
    expected_locality = "MIXED_REQUIRES_DECOMPOSITION" if local and cross else ("UNIT_LOCAL" if local else "REQUIRES_CROSS_UNIT_ANALYSIS")
    if value["locality"] != expected_locality:
        raise ValidationError("E-OBSERVATION-LOCALITY", "locality disagrees with requirement partition")
    if expected_locality == "MIXED_REQUIRES_DECOMPOSITION":
        if value["decomposition_status"] not in {"REQUIRED", "DECOMPOSED"}:
            raise ValidationError("E-OBSERVATION-LOCALITY", "mixed requirements require explicit decomposition")
        if value["decomposition_status"] == "DECOMPOSED" and "previous_definition_artifact_id" not in value:
            raise ValidationError("E-OBSERVATION-LOCALITY", "decomposition must seal a new version")
    elif value["decomposition_status"] != "NOT_REQUIRED":
        raise ValidationError("E-OBSERVATION-LOCALITY", "unmixed definition cannot claim decomposition")
    if "previous_definition_artifact_id" in value:
        prior = _definition(research_root, value["previous_definition_artifact_id"], root=root)
        if prior["intake_artifact_id"] != value["intake_artifact_id"]:
            raise ValidationError("E-OBSERVATION-PROVENANCE", "revision binds another intake")
        if value["decomposition_status"] == "DECOMPOSED":
            if prior["decomposition_status"] != "REQUIRED" or not {item["requirement_id"] for item in prior["requirements"]} <= {item["requirement_id"] for item in requirements}:
                raise ValidationError("E-OBSERVATION-LOCALITY", "decomposition must retain the prior unresolved requirements")
    return value


def profile_binding(profile_ref: str, *, root: pathlib.Path | None = None) -> dict[str, str]:
    profile = load_extraction_profile(profile_ref, root=root)
    return {"profile_ref": profile_ref, "profile_id": profile.profile_id, "profile_version": profile.profile_version,
            "package_hash": profile.package_hash, "extraction_profile_hash": profile.extraction_profile_hash,
            "reduction_profile_hash": profile.reduction_profile_hash}


def _schema_alternatives(schema: dict[str, Any], document: dict[str, Any], seen: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    if "$ref" in schema:
        ref = schema["$ref"]
        if ref in seen or not ref.startswith("#/"):
            raise ValidationError("E-OBSERVATION-PROFILE-PATH", "unsupported recursive payload schema reference")
        return _schema_alternatives(_resolve_pointer(document, ref[1:]), document, seen | {ref})
    branches = schema.get("oneOf", schema.get("anyOf"))
    if branches is not None:
        return [node for branch in branches for node in _schema_alternatives(branch, document, seen)]
    return [schema]


def _schema_has_path(schema: dict[str, Any], pointer: str, document: dict[str, Any]) -> bool:
    if not pointer.startswith("/") or pointer == "/":
        return False
    nodes = [schema]
    for token in pointer[1:].split("/"):
        # Strict JSON Pointer parsing is reused from the runtime.
        from .generic_extraction import _decode_pointer_token
        token = _decode_pointer_token(token)
        next_nodes = []
        for node in nodes:
            for alternative in _schema_alternatives(node, document):
                if token in alternative.get("properties", {}):
                    next_nodes.append(alternative["properties"][token])
                elif alternative.get("type") == "array" and token == "*" and isinstance(alternative.get("items"), dict):
                    next_nodes.append(alternative["items"])
        nodes = next_nodes
        if not nodes:
            return False
    return bool(nodes)


def _validate_coverage_paths(coverage: dict[str, Any], profile: ExtractionProfile) -> None:
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for alternative in _schema_alternatives(profile.payload_schema, profile.payload_schema):
        kinds = alternative.get("properties", {}).get("kind", {})
        values = [kinds["const"]] if "const" in kinds else kinds.get("enum", [])
        for kind in values:
            by_kind.setdefault(kind, []).append(alternative)
    if not set(coverage["payload_kinds"]) <= set(by_kind):
        raise ValidationError("E-OBSERVATION-PROFILE-PATH", "coverage cites an unknown payload kind")
    if coverage["payload_paths"] and not coverage["payload_kinds"]:
        raise ValidationError("E-OBSERVATION-PROFILE-PATH", "payload paths require an explicit kind")
    for kind in coverage["payload_kinds"]:
        for pointer in coverage["payload_paths"]:
            if not any(_schema_has_path(node, pointer, profile.payload_schema) for node in by_kind[kind]):
                raise ValidationError("E-OBSERVATION-PROFILE-PATH", f"payload path {pointer!r} is absent for {kind}")
    prompt = profile.prompt_bytes.decode("utf-8")
    if any(rule not in prompt for rule in coverage["prompt_rules"]):
        raise ValidationError("E-OBSERVATION-PROFILE-PATH", "prompt rule quote is absent from the selected Profile")


@schema_validation_session()
def seal_profile_resolution_from_draft(draft_or_path: dict[str, Any] | pathlib.Path, research_root: pathlib.Path, *, root: pathlib.Path | None = None) -> SealedRecord:
    draft = _draft(draft_or_path)
    _fields(draft, {"definition_artifact_id", "decision", "coverage", "rationale", "assessor", "frozen_at"}, {"selected_profile_ref", "fit", "admission"})
    definition = _definition(research_root, draft["definition_artifact_id"], root=root)
    body = {key: value for key, value in draft.items() if key != "selected_profile_ref"}
    body.update(schema_version=SCHEMA_VERSION, definition_id=definition["definition_id"], assessment_assurance="HOST_ATTESTED", semantic_assurance="UNQUALIFIED")
    if "selected_profile_ref" in draft:
        body["selected_profile"] = profile_binding(draft["selected_profile_ref"], root=root)
    if not isinstance(body["coverage"], list) or any(not isinstance(item, dict) or "requirement_id" not in item or "disposition" not in item for item in body["coverage"]):
        raise ValidationError("E-OBSERVATION-COVERAGE", "coverage must contain requirement dispositions")
    body["coverage"] = sorted(body["coverage"], key=lambda item: item["requirement_id"])
    body["unmet_requirement_ids"] = sorted(item["requirement_id"] for item in body["coverage"] if item["disposition"] != "COVERED")
    record = seal_record("ProfileResolution", body, id_field="resolution_id", hash_field="resolution_hash")
    validate_profile_resolution(record, research_root, root=root)
    return publish_record(research_root, "ProfileResolution", record)


@schema_validation_session()
def validate_profile_resolution(record: dict[str, Any], research_root: pathlib.Path, *, root: pathlib.Path | None = None) -> dict[str, Any]:
    value = validate_record_identity(record, "ProfileResolution", id_field="resolution_id", hash_field="resolution_hash")
    definition = _definition(research_root, value["definition_artifact_id"], root=root)
    if value["definition_id"] != definition["definition_id"]:
        raise ValidationError("E-OBSERVATION-PROVENANCE", "resolution binds another definition")
    requirements = {item["requirement_id"]: item for item in definition["requirements"]}
    coverage = {item["requirement_id"]: item for item in value["coverage"]}
    if len(coverage) != len(value["coverage"]) or set(coverage) != set(requirements):
        raise ValidationError("E-OBSERVATION-COVERAGE", "coverage must disposition every requirement exactly once")
    if value["coverage"] != sorted(value["coverage"], key=lambda item: item["requirement_id"]):
        raise ValidationError("E-OBSERVATION-COVERAGE", "coverage is not canonical")
    unmet = sorted(identifier for identifier, item in coverage.items() if item["disposition"] != "COVERED")
    if unmet != value["unmet_requirement_ids"]:
        raise ValidationError("E-OBSERVATION-COVERAGE", "unmet requirement list changed")
    for identifier, requirement in requirements.items():
        if requirement["locality"] == "CROSS_UNIT" and coverage[identifier]["disposition"] != "UNSUPPORTED":
            raise ValidationError("E-OBSERVATION-COVERAGE", "local extraction cannot cover cross-unit analysis")
    if value["decision"] == "REUSE_EXISTING":
        if not definition["executable_requirement_ids"] or definition["decomposition_status"] == "REQUIRED":
            raise ValidationError("E-OBSERVATION-LOCALITY", "definition has no executable decomposed scope")
        if any(item["necessity"] == "REQUIRED" and item["locality"] == "UNIT_LOCAL" and coverage[identifier]["disposition"] != "COVERED" for identifier, item in requirements.items()):
            raise ValidationError("E-OBSERVATION-COVERAGE", "required local requirements are not covered")
        selected = value["selected_profile"]
        if selected != profile_binding(selected["profile_ref"], root=root):
            raise ValidationError("E-OBSERVATION-PROFILE-BIND", "selected Profile bytes or identity changed")
        profile = load_extraction_profile(selected["profile_ref"], root=root)
        for item in coverage.values():
            _validate_coverage_paths(item, profile)
            if item["disposition"] == "COVERED" and not (item["payload_kinds"] or item["prompt_rules"]):
                raise ValidationError("E-OBSERVATION-COVERAGE", "covered requirement needs a concrete Profile reference")
    elif not unmet:
        raise ValidationError("E-OBSERVATION-COVERAGE", "non-executable decision must state unmet requirements")
    return value


@schema_validation_session()
def seal_observation_work_lead_from_draft(draft_or_path: dict[str, Any] | pathlib.Path, research_root: pathlib.Path, *, root: pathlib.Path | None = None) -> SealedRecord:
    draft = _draft(draft_or_path)
    _fields(draft, {"definition_artifact_id", "work_claim", "relevance_hypothesis", "lead_sources", "location_hints", "frozen_at"})
    definition = _definition(research_root, draft["definition_artifact_id"], root=root)
    if not isinstance(draft["lead_sources"], list):
        raise ValidationError("E-OBSERVATION-LEAD", "lead_sources must be an array")
    sources = sorted((_canonical_lead_source(item) for item in draft["lead_sources"]), key=lambda item: item["lead_source_id"])
    body = {**draft, "schema_version": SCHEMA_VERSION, "definition_id": definition["definition_id"], "assurance": "UNVERIFIED_LEAD", "evidence_status": "LEAD_ONLY", "work_claim": _canonical_work_claim(draft["work_claim"]), "lead_sources": sources}
    record = seal_record("ObservationWorkLead", body, id_field="lead_id", hash_field="lead_hash")
    validate_observation_work_lead(record, research_root, root=root)
    return publish_record(research_root, "ObservationWorkLead", record)


@schema_validation_session()
def validate_observation_work_lead(record: dict[str, Any], research_root: pathlib.Path, *, root: pathlib.Path | None = None) -> dict[str, Any]:
    value = validate_record_identity(record, "ObservationWorkLead", id_field="lead_id", hash_field="lead_hash")
    definition = _definition(research_root, value["definition_artifact_id"], root=root)
    if value["definition_id"] != definition["definition_id"]:
        raise ValidationError("E-OBSERVATION-PROVENANCE", "lead binds another definition")
    if value["work_claim"] != _canonical_work_claim(value["work_claim"]):
        raise ValidationError("E-OBSERVATION-LEAD", "work claim is not canonical")
    sources = value["lead_sources"]
    if len({source["lead_source_id"] for source in sources}) != len(sources):
        raise ValidationError("E-OBSERVATION-LEAD", "duplicate lead sources")
    for source in sources:
        raw = {key: item for key, item in source.items() if key not in {"lead_source_id", "role"}}
        if _canonical_lead_source(raw) != source:
            raise ValidationError("E-OBSERVATION-LEAD", "lead source identity changed")
    if sources != sorted(sources, key=lambda item: item["lead_source_id"]):
        raise ValidationError("E-OBSERVATION-LEAD", "lead sources are not canonical")
    location_ids = {source["lead_source_id"] for source in sources if "LOCATION_HINT" in source["supports"]}
    for hint in value["location_hints"]:
        if not set(hint["lead_source_ids"]) <= location_ids:
            raise ValidationError("E-OBSERVATION-LEAD", "location hint lacks a bound location source")
    return value
