"""Deterministic, source-only handoffs for observation research.

Research inputs remain outside Catalog and never enter a native generic task.
Prepare requires accessible local source paths; replay needs only verified CAS
inputs and the bound runtime/Profile, so completed research survives source loss.
"""
from __future__ import annotations

import copy
import json
import pathlib
from dataclasses import dataclass
from typing import Any

from .build_identity import build_source_hash
from .canonical import canonical_dumps
from .constants import SCHEMA_VERSION
from .errors import ValidationError
from .file_io import write_immutable
from .hashing import artifact_id_for, object_hash
from .novel_spec import ValidatedGenericResearchSpec, validate_generic_research_spec
from .observation_common import (
    get_record,
    put_record,
    read_json,
    research_store,
    seal_record,
    validate_record_identity,
)
from .observation_planning import (
    validate_observation_definition,
    validate_observation_work_lead,
    validate_profile_resolution,
)
from .paths import repo_root
from .phase0_builder import _seal_declaration
from .phase0_handoff import (
    _lead_matches_work,
    attestation_rights,
    load_standalone_attestation,
    normalize_author,
    source_ref_from_validated,
    validate_operator_attestation,
    validate_source_declaration,
    work_ref_from_declaration,
)
from .runtime import utc_now


GENERIC_HANDOFF_BUILDER_ID = "generic-handoff-builder-v1"
GENERIC_INGESTION_LIMITS = {"max_chapters": 100_000, "max_bytes": 500_000_000}
_CONTRACT_FILES = (
    "defs.schema.json",
    "phase0-defs.schema.json",
    "source-declaration.schema.json",
    "operator-attestation.schema.json",
    "observation-definition.schema.json",
    "profile-resolution.schema.json",
    "observation-work-lead.schema.json",
    "generic-novel-spec.schema.json",
    "generic-handoff-build-request.schema.json",
    "generic-extraction-handoff.schema.json",
    "generic/extraction-profile-manifest.schema.json",
)


@dataclass(frozen=True)
class ResolvedGenericHandoff:
    handoff: dict[str, Any]
    spec: dict[str, Any]
    profile_ref: str

    @property
    def execution_spec(self) -> dict[str, Any]:
        return copy.deepcopy(self.spec)


def _json_bytes(record: dict[str, Any]) -> bytes:
    return (json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _builder_binding(root: pathlib.Path) -> dict[str, str]:
    assets = []
    for name in _CONTRACT_FILES:
        path = root / "contracts" / name
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ValidationError("E-GENERIC-HANDOFF-BUILD", f"missing builder contract: {name}") from exc
        assets.append({"path": name, "artifact_id": artifact_id_for(data)})
    return {
        "build_id": GENERIC_HANDOFF_BUILDER_ID,
        "source_tree_hash": build_source_hash(root),
        "contract_hash": object_hash({"contracts": assets}, omit=()),
    }


def make_generic_handoff_build_request(
    *,
    definition_artifact_id: str,
    resolution_artifact_id: str,
    work_lead_artifact_ids: list[str],
    source_declaration_artifact_id: str,
    requested_at: str,
    limits: dict[str, int] | None = None,
    strict_order: bool = False,
    operator_attestation_artifact_id: str | None = None,
) -> dict[str, Any]:
    if (
        not isinstance(work_lead_artifact_ids, list)
        or not work_lead_artifact_ids
        or any(not isinstance(value, str) for value in work_lead_artifact_ids)
        or len(set(work_lead_artifact_ids)) != len(work_lead_artifact_ids)
    ):
        raise ValidationError("E-GENERIC-HANDOFF-REQUEST", "work lead artifacts must be non-empty and unique")
    body = {
        "schema_version": SCHEMA_VERSION,
        "definition_artifact_id": definition_artifact_id,
        "resolution_artifact_id": resolution_artifact_id,
        "work_lead_artifact_ids": sorted(work_lead_artifact_ids),
        "source_declaration_artifact_id": source_declaration_artifact_id,
        "requested_at": requested_at,
        "execution_scope": "FULL_WORK",
        "limits": copy.deepcopy(GENERIC_INGESTION_LIMITS if limits is None else limits),
        "strict_order": strict_order,
    }
    if operator_attestation_artifact_id is not None:
        body["operator_attestation_artifact_id"] = operator_attestation_artifact_id
    return seal_record(
        "GenericHandoffBuildRequest", body,
        id_field="build_request_id", hash_field="build_request_hash",
    )


def validate_generic_handoff_build_request(record: dict[str, Any]) -> dict[str, Any]:
    result = validate_record_identity(
        record, "GenericHandoffBuildRequest",
        id_field="build_request_id", hash_field="build_request_hash",
    )
    if result["work_lead_artifact_ids"] != sorted(set(result["work_lead_artifact_ids"])):
        raise ValidationError("E-GENERIC-HANDOFF-REQUEST", "work lead artifacts are not canonically ordered")
    return result


def project_generic_novel_spec(
    declaration: dict[str, Any],
    *,
    limits: dict[str, int] | None = None,
    strict_order: bool = False,
    require_source_access: bool = True,
) -> tuple[dict[str, Any], ValidatedGenericResearchSpec]:
    """Project only source governance. This function accepts no research inputs."""
    declaration = validate_source_declaration(declaration)
    work = work_ref_from_declaration(declaration)
    source = copy.deepcopy(declaration["source"])
    source.update(title=work["canonical_title"], author=work["author"], language=work["language"])
    validated = validate_generic_research_spec(
        {
            "source": source,
            "rights": copy.deepcopy(declaration["rights"]),
            "source_quality": copy.deepcopy(declaration["source_quality"]),
            "limits": copy.deepcopy(GENERIC_INGESTION_LIMITS if limits is None else limits),
            "strict_order": strict_order,
        },
        require_source_access=require_source_access,
    )
    return copy.deepcopy(validated.effective_spec), validated


def _verify_attestation(
    request: dict[str, Any], declaration: dict[str, Any], research_root: pathlib.Path,
) -> None:
    reference = request.get("operator_attestation_artifact_id")
    attestation_id = declaration.get("operator_attestation_id")
    if reference is None and attestation_id is None:
        return
    if reference is None or attestation_id is None:
        raise ValidationError("E-PHASE0-ATTEST-BIND", "attestation declaration and CAS reference must both be present")
    attestation = validate_operator_attestation(get_record(research_root, reference))
    if attestation["attestation_id"] != attestation_id or declaration["rights"] != attestation_rights(attestation):
        raise ValidationError("E-PHASE0-ATTEST-BIND", "source declaration conflicts with its frozen operator attestation")


def rebuild_generic_handoff(
    build_request_artifact_id: str,
    research_root: pathlib.Path,
    *,
    root: pathlib.Path | None = None,
    require_source_access: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Replay every dependency from CAS; no visible input copy has authority."""
    runtime_root = (root or repo_root()).resolve()
    request = validate_generic_handoff_build_request(get_record(research_root, build_request_artifact_id))
    definition = validate_observation_definition(
        get_record(research_root, request["definition_artifact_id"]), research_root, root=runtime_root,
    )
    resolution = validate_profile_resolution(
        get_record(research_root, request["resolution_artifact_id"]), research_root, root=runtime_root,
    )
    if (
        resolution["definition_artifact_id"] != request["definition_artifact_id"]
        or resolution["definition_id"] != definition["definition_id"]
    ):
        raise ValidationError("E-GENERIC-HANDOFF-BIND", "Profile resolution belongs to another observation definition")
    if resolution["decision"] != "REUSE_EXISTING":
        raise ValidationError("E-GENERIC-HANDOFF-PROFILE", "resolution is reportable but has no executable existing Profile")
    declaration = validate_source_declaration(get_record(research_root, request["source_declaration_artifact_id"]))
    _verify_attestation(request, declaration, research_root)
    work_ref = work_ref_from_declaration(declaration)
    if work_ref["identity"]["basis"] == "USER_CONFIRMED":
        research_store(research_root).get(work_ref["identity"]["confirmation_artifact_id"])
    spec, validated = project_generic_novel_spec(
        declaration, limits=request["limits"], strict_order=request["strict_order"],
        require_source_access=require_source_access,
    )
    source_ref = source_ref_from_validated(declaration, validated, work_ref)
    leads = [
        validate_observation_work_lead(get_record(research_root, artifact), research_root, root=runtime_root)
        for artifact in request["work_lead_artifact_ids"]
    ]
    for lead in leads:
        if (
            lead["definition_artifact_id"] != request["definition_artifact_id"]
            or lead["definition_id"] != definition["definition_id"]
        ):
            raise ValidationError("E-GENERIC-HANDOFF-GROUP", "work lead belongs to another observation definition")
        if not _lead_matches_work(lead, work_ref):
            raise ValidationError("E-GENERIC-HANDOFF-GROUP", "work lead claim differs from resolved work")
    authors = {normalize_author(lead["work_claim"].get("author")) for lead in leads}
    if len(authors - {None}) > 1:
        raise ValidationError("E-GENERIC-HANDOFF-GROUP", "work leads contain conflicting authors")
    lead_ids = sorted(lead["lead_id"] for lead in leads)
    if len(set(lead_ids)) != len(lead_ids):
        raise ValidationError("E-GENERIC-HANDOFF-GROUP", "handoff group contains duplicate leads")
    profile = copy.deepcopy(resolution["selected_profile"])
    body = {
        "schema_version": SCHEMA_VERSION,
        "definition_id": definition["definition_id"],
        "resolution_id": resolution["resolution_id"],
        "motivating_lead_ids": lead_ids,
        "work_ref": work_ref,
        "source_ref": source_ref,
        "selected_profile": profile,
        "localization": {
            "policy": "LEAD_ONLY_NOT_EXECUTOR_INPUT",
            "execution_scope": "FULL_WORK",
            "hint_refs": [
                {"lead_id": lead["lead_id"], "hint_indexes": list(range(len(lead["location_hints"])))}
                for lead in sorted(leads, key=lambda item: item["lead_id"])
                if lead["location_hints"]
            ],
        },
        "novel_spec": {
            "path": "novel-spec.json",
            "raw_artifact_id": artifact_id_for(canonical_dumps(spec)),
            "expected_input_spec_hash": validated.resolved_spec_hash,
        },
        "builder": {
            **_builder_binding(runtime_root),
            "build_request_artifact_id": build_request_artifact_id,
            **{key: copy.deepcopy(request[key]) for key in (
                "definition_artifact_id", "resolution_artifact_id", "work_lead_artifact_ids",
                "source_declaration_artifact_id",
            )},
        },
        "readiness": {
            "status": "READY_FOR_XHNOVEL",
            "rights_basis": validated.rights["basis"],
            "may_store_full_text": validated.rights["may_store_full_text"],
            "may_send_to_external_model": validated.rights["may_send_to_external_model"],
            "source_quality_tier": validated.source_quality_tier,
        },
        "contains_evidence": False,
        "requested_at": request["requested_at"],
    }
    return seal_record(
        "GenericExtractionHandoff", body, id_field="handoff_id", hash_field="handoff_hash",
    ), spec


def _prepare_request(
    value: dict[str, Any], research_root: pathlib.Path, input_dir: pathlib.Path, now: str | None,
) -> dict[str, Any]:
    if "build_request_id" in value:
        return validate_generic_handoff_build_request(value)
    required = {"definition_artifact_id", "resolution_artifact_id", "work_lead_artifact_ids"}
    optional = {
        "source_declaration", "source_declaration_artifact_id", "operator_attestation_artifact_id",
        "requested_at", "limits", "strict_order",
    }
    if not required <= set(value) or set(value) - required - optional:
        raise ValidationError("E-GENERIC-HANDOFF-PREPARE", "generic preparation input has an invalid field set")
    if ("source_declaration" in value) == ("source_declaration_artifact_id" in value):
        raise ValidationError("E-GENERIC-HANDOFF-PREPARE", "supply exactly one source declaration draft or artifact")
    attestation = load_standalone_attestation(research_root)
    attestation_ref = value.get("operator_attestation_artifact_id")
    if "source_declaration" in value:
        if not isinstance(value["source_declaration"], dict):
            raise ValidationError("E-GENERIC-HANDOFF-PREPARE", "source declaration must be an object")
        declaration = _seal_declaration(value["source_declaration"], input_dir=input_dir, attestation=attestation)
        declaration_ref = put_record(research_root, "SourceDeclaration", declaration)
    else:
        declaration_ref = value["source_declaration_artifact_id"]
        declaration = validate_source_declaration(get_record(research_root, declaration_ref))
    if attestation is not None:
        if declaration["rights"] != attestation_rights(attestation):
            raise ValidationError("E-PHASE0-ATTEST-MISMATCH", "declared rights conflict with standing operator attestation")
        if declaration.get("operator_attestation_id") is not None:
            standing_ref = put_record(research_root, "OperatorAttestation", attestation)
            if attestation_ref is not None and attestation_ref != standing_ref:
                raise ValidationError("E-PHASE0-ATTEST-BIND", "supplied attestation differs from standing attestation")
            attestation_ref = standing_ref
    return make_generic_handoff_build_request(
        definition_artifact_id=value["definition_artifact_id"],
        resolution_artifact_id=value["resolution_artifact_id"],
        work_lead_artifact_ids=value["work_lead_artifact_ids"],
        source_declaration_artifact_id=declaration_ref,
        requested_at=value.get("requested_at", now or utc_now()),
        limits=value.get("limits"), strict_order=value.get("strict_order", False),
        operator_attestation_artifact_id=attestation_ref,
    )


def prepare_generic_handoff_from_input(
    value_or_path: dict[str, Any] | pathlib.Path | str,
    research_root: pathlib.Path,
    *,
    root: pathlib.Path | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    research_root = pathlib.Path(research_root).resolve()
    if isinstance(value_or_path, dict):
        value, input_dir = copy.deepcopy(value_or_path), research_root
    else:
        path = pathlib.Path(value_or_path).resolve()
        value, input_dir = read_json(path), path.parent
    request = _prepare_request(value, research_root, input_dir, now)
    request_ref = put_record(research_root, "GenericHandoffBuildRequest", request)
    handoff, spec = rebuild_generic_handoff(request_ref, research_root, root=root, require_source_access=True)
    spec_ref = research_store(research_root).put(canonical_dumps(spec))
    if spec_ref != handoff["novel_spec"]["raw_artifact_id"]:
        raise ValidationError("E-GENERIC-HANDOFF-SPEC", "projected spec artifact identity changed")
    handoff_ref = put_record(research_root, "GenericExtractionHandoff", handoff)
    output_dir = research_root / "handoffs" / handoff["handoff_id"]
    handoff_path, spec_path = output_dir / "handoff.json", output_dir / "novel-spec.json"
    write_immutable(handoff_path, _json_bytes(handoff))
    write_immutable(spec_path, _json_bytes(spec))
    # The freshly published object must reproduce from its complete CAS closure.
    validate_generic_handoff(handoff_path, research_root, root=root)
    return {
        "handoff": handoff, "novel_spec": spec,
        "handoff_path": str(handoff_path), "novel_spec_path": str(spec_path),
        "handoff_artifact_id": handoff_ref, "build_request_artifact_id": request_ref,
    }


def resolve_generic_handoff(
    handoff_path: dict[str, Any] | pathlib.Path | str,
    research_root: pathlib.Path,
    *,
    root: pathlib.Path | None = None,
    require_source_access: bool = False,
) -> ResolvedGenericHandoff:
    path = None if isinstance(handoff_path, dict) else pathlib.Path(handoff_path)
    visible = copy.deepcopy(handoff_path) if path is None else read_json(path)
    handoff = validate_record_identity(
        visible, "GenericExtractionHandoff", id_field="handoff_id", hash_field="handoff_hash",
    )
    # CAS must exist even if someone recomputed all the hashes in a visible file.
    authoritative = get_record(research_root, artifact_id_for(canonical_dumps(handoff)))
    if authoritative != handoff or (path is not None and path.read_bytes() != _json_bytes(handoff)):
        raise ValidationError("E-GENERIC-HANDOFF-REPLAY", "visible handoff differs from canonical CAS output")
    rebuilt, spec = rebuild_generic_handoff(
        handoff["builder"]["build_request_artifact_id"], research_root,
        root=root, require_source_access=require_source_access,
    )
    if rebuilt != handoff:
        raise ValidationError("E-GENERIC-HANDOFF-REPLAY", "handoff differs from deterministic builder replay")
    artifact_spec = get_record(research_root, handoff["novel_spec"]["raw_artifact_id"])
    if artifact_spec != spec or object_hash(artifact_spec, omit=()) != handoff["novel_spec"]["expected_input_spec_hash"]:
        raise ValidationError("E-GENERIC-HANDOFF-SPEC", "frozen spec differs from handoff projection")
    if path is not None:
        spec_path = path.parent / handoff["novel_spec"]["path"]
        try:
            same_visible = spec_path.read_bytes() == _json_bytes(artifact_spec)
        except OSError:
            same_visible = False
        if not same_visible:
            raise ValidationError("E-GENERIC-HANDOFF-SPEC", "visible spec differs from CAS")
    return ResolvedGenericHandoff(copy.deepcopy(handoff), copy.deepcopy(artifact_spec), handoff["selected_profile"]["profile_ref"])


def validate_generic_handoff(
    handoff_path: dict[str, Any] | pathlib.Path | str,
    research_root: pathlib.Path,
    *,
    root: pathlib.Path | None = None,
    require_source_access: bool = False,
) -> dict[str, Any]:
    return resolve_generic_handoff(
        handoff_path, research_root, root=root, require_source_access=require_source_access,
    ).handoff
