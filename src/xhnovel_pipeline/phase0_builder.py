"""Deterministic Phase 0 Evidence-Handoff construction and replay.

Open-world exploration remains agentic and lead-only. This module starts only once
an ExplorationBrief, one or more ResearchLeads, and a SourceDeclaration are ready to
be content-bound. It projects the ordinary Novel Spec consumed by ``research-novel``
and proves that no lead text or location hint entered that execution input.
"""

from __future__ import annotations

import copy
import json
import pathlib
from dataclasses import dataclass
from typing import Any, Callable

from .canonical import canonical_dumps
from .constants import SCHEMA_VERSION
from .errors import ValidationError
from .hashing import artifact_id_for, is_real_sha256, object_hash
from .ids import derived_id
from .novel_spec import (
    SpecValidationPurpose,
    ValidatedDirectResearchSpec,
    load_validated_direct_research_spec,
    validate_direct_research_spec,
)
from .phase0_handoff import (
    HandoffGroup,
    group_leads_for_source,
    make_exploration_brief,
    make_research_lead,
    make_source_declaration,
    normalize_author,
    validate_exploration_brief,
    validate_research_lead,
    validate_source_declaration,
    work_ref_from_declaration,
)
from .ranking import normalize_work_title
from .schema import validate_schema
from .store import ArtifactStore

PHASE0_HANDOFF_BUILDER_ID = "phase0-handoff-builder-v1"
PHASE0_EXECUTION_PROFILE = "DIRECT_FULL_WORK_V1"
PHASE0_LIMITS = {"max_chapters": 100_000, "max_bytes": 500_000_000}
PHASE0_SCENE_SCOUT = {
    "window_chars": 10_000,
    "overlap_chars": 1_800,
    "max_input_chars": 20_000,
    "max_request_bytes": 2_000_000,
    "max_workers": 8,
}


@dataclass(frozen=True)
class PreparedHandoff:
    phase0_root: pathlib.Path
    handoff_path: pathlib.Path
    novel_spec_path: pathlib.Path
    validation_receipt_path: pathlib.Path
    handoff_artifact_id: str
    build_request_artifact_id: str
    handoff: dict[str, Any]
    novel_spec: dict[str, Any]


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _build_request_hash(value: dict[str, Any]) -> str:
    try:
        return object_hash(value, omit=())
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "E-PHASE0-BUILD-REQUEST",
            "handoff build request is not canonical JSON",
        ) from exc


def _write_immutable(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValidationError("E-IMMUTABLE-OUTPUT", f"refusing to overwrite {path}")


def _read_json(path: pathlib.Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("E-PHASE0-JSON", f"invalid {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ValidationError("E-PHASE0-JSON", f"{label} must be an object")
    return value


def _record_validator(kind: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    validators: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
        "ExplorationBrief": validate_exploration_brief,
        "ResearchLead": validate_research_lead,
        "SourceDeclaration": validate_source_declaration,
        "HandoffBuildRequest": validate_handoff_build_request,
    }
    try:
        return validators[kind]
    except KeyError as exc:
        raise ValidationError("E-PHASE0-KIND", f"unsupported Phase 0 record kind {kind}") from exc


def put_phase0_record(
    store: ArtifactStore,
    kind: str,
    record: dict[str, Any],
) -> str:
    validated = _record_validator(kind)(record)
    return store.put(canonical_dumps(validated))


def read_phase0_record(
    store: ArtifactStore,
    artifact_id: str,
    kind: str,
) -> dict[str, Any]:
    if (
        not isinstance(artifact_id, str)
        or not artifact_id.startswith("sha256:")
        or not is_real_sha256(artifact_id)
    ):
        raise ValidationError("E-PHASE0-CAS", f"{kind} artifact id is invalid")
    raw = store.get(artifact_id)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("E-PHASE0-CAS", f"{kind} artifact is not JSON") from exc
    try:
        canonical = canonical_dumps(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("E-PHASE0-CAS", f"{kind} artifact is not canonical JSON") from exc
    if not isinstance(value, dict) or raw != canonical:
        raise ValidationError("E-PHASE0-CAS", f"{kind} artifact is not canonical JSON")
    return _record_validator(kind)(value)


def make_handoff_build_request(
    *,
    exploration_brief_artifact_id: str,
    research_lead_artifact_ids: list[str],
    source_declaration_artifact_id: str,
    requested_at: str,
) -> dict[str, Any]:
    lead_ids = sorted(set(research_lead_artifact_ids))
    if not lead_ids or len(lead_ids) != len(research_lead_artifact_ids):
        raise ValidationError(
            "E-PHASE0-BUILD-REQUEST",
            "research_lead_artifact_ids must be non-empty and unique",
        )
    base = {
        "schema_version": SCHEMA_VERSION,
        "exploration_brief_artifact_id": exploration_brief_artifact_id,
        "research_lead_artifact_ids": lead_ids,
        "source_declaration_artifact_id": source_declaration_artifact_id,
        "execution_profile": PHASE0_EXECUTION_PROFILE,
        "requested_at": requested_at,
    }
    build_request_hash = _build_request_hash(base)
    try:
        build_request_id = derived_id(
            "HandoffBuildRequest",
            {"build_request_hash": build_request_hash},
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "E-PHASE0-BUILD-REQUEST",
            "handoff build request is not canonical JSON",
        ) from exc
    record = {
        **base,
        "build_request_id": build_request_id,
        "build_request_hash": build_request_hash,
    }
    validate_schema("HandoffBuildRequest", record)
    return record


def validate_handoff_build_request(record: dict[str, Any]) -> dict[str, Any]:
    validate_schema("HandoffBuildRequest", record)
    if record["research_lead_artifact_ids"] != sorted(
        set(record["research_lead_artifact_ids"])
    ):
        raise ValidationError(
            "E-PHASE0-BUILD-REQUEST-BIND",
            "research lead artifact ids are not canonically ordered",
        )
    payload = {
        key: copy.deepcopy(value)
        for key, value in record.items()
        if key not in {"build_request_id", "build_request_hash"}
    }
    expected_hash = object_hash(payload, omit=())
    expected_id = derived_id(
        "HandoffBuildRequest",
        {"build_request_hash": expected_hash},
    )
    if (
        record["build_request_hash"] != expected_hash
        or record["build_request_id"] != expected_id
    ):
        raise ValidationError(
            "E-PHASE0-BUILD-REQUEST-BIND",
            "handoff build request identity changed",
        )
    return copy.deepcopy(record)


def project_novel_spec(
    brief: dict[str, Any],
    declaration: dict[str, Any],
) -> tuple[dict[str, Any], ValidatedDirectResearchSpec]:
    """Project only execution inputs; Lead and hint content are not accepted here."""
    brief = validate_exploration_brief(brief)
    declaration = validate_source_declaration(declaration)
    work_ref = work_ref_from_declaration(declaration)
    source = copy.deepcopy(declaration["source"])
    source.update(
        {
            "title": work_ref["canonical_title"],
            "author": work_ref["author"],
            "language": work_ref["language"],
        }
    )
    spec = {
        "source": source,
        "rights": copy.deepcopy(declaration["rights"]),
        "source_quality": copy.deepcopy(declaration["source_quality"]),
        "request": {
            "mode": "EXPLORE",
            "discovery_brief": brief["evidence_discovery_brief"],
        },
        "limits": copy.deepcopy(PHASE0_LIMITS),
        "scene_scout": copy.deepcopy(PHASE0_SCENE_SCOUT),
        "strict_order": False,
    }
    validated = validate_direct_research_spec(
        spec,
        purpose=SpecValidationPurpose.EVIDENCE_HANDOFF,
    )
    if validated.discovery_brief != brief["evidence_discovery_brief"]:
        raise ValidationError(
            "E-PHASE0-BRIEF-LEAK",
            "projected discovery brief differs from frozen exploration brief",
        )
    return spec, validated


def _handoff_identity_payload(handoff: dict[str, Any]) -> dict[str, Any]:
    return {
        "brief_id": handoff["brief_id"],
        "motivating_lead_ids": handoff["motivating_lead_ids"],
        "work_ref_id": handoff["work_ref"]["work_ref_id"],
        "source_ref_id": handoff["source_ref"]["source_ref_id"],
        "expected_input_spec_hash": handoff["novel_spec"]["expected_input_spec_hash"],
        "build_request_artifact_id": handoff["builder"]["build_request_artifact_id"],
    }


def _validate_handoff_record(handoff: dict[str, Any]) -> dict[str, Any]:
    validate_schema("EvidenceHandoff", handoff)
    expected_id = derived_id("EvidenceHandoff", _handoff_identity_payload(handoff))
    expected_hash = object_hash(handoff, omit=("handoff_hash",))
    if handoff["handoff_id"] != expected_id or handoff["handoff_hash"] != expected_hash:
        raise ValidationError("E-PHASE0-HANDOFF-BIND", "evidence handoff identity changed")
    if handoff["motivating_lead_ids"] != sorted(set(handoff["motivating_lead_ids"])):
        raise ValidationError(
            "E-PHASE0-HANDOFF-BIND",
            "motivating lead ids are not canonically ordered",
        )
    return copy.deepcopy(handoff)


def _validation_receipt(
    handoff: dict[str, Any],
    *,
    handoff_artifact_id: str,
) -> dict[str, Any]:
    """Return a regenerable validation output, never an authority for validity."""
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_kind": "PHASE0_HANDOFF_VALIDATION",
        "validation_method": "DETERMINISTIC_REPLAY",
        "status": "PASS",
        "handoff_id": handoff["handoff_id"],
        "handoff_hash": handoff["handoff_hash"],
        "handoff_artifact_id": handoff_artifact_id,
        "build_request_artifact_id": handoff["builder"][
            "build_request_artifact_id"
        ],
        "novel_spec_raw_artifact_id": handoff["novel_spec"]["raw_artifact_id"],
        "expected_input_spec_hash": handoff["novel_spec"][
            "expected_input_spec_hash"
        ],
    }


def rebuild_evidence_handoff(
    store: ArtifactStore,
    build_request_artifact_id: str,
) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    request = read_phase0_record(
        store,
        build_request_artifact_id,
        "HandoffBuildRequest",
    )
    if artifact_id_for(canonical_dumps(request)) != build_request_artifact_id:
        raise ValidationError(
            "E-PHASE0-BUILD-REQUEST-BIND",
            "build request artifact id differs from canonical bytes",
        )
    brief = read_phase0_record(
        store,
        request["exploration_brief_artifact_id"],
        "ExplorationBrief",
    )
    leads = [
        read_phase0_record(store, artifact_id, "ResearchLead")
        for artifact_id in request["research_lead_artifact_ids"]
    ]
    declaration = read_phase0_record(
        store,
        request["source_declaration_artifact_id"],
        "SourceDeclaration",
    )
    work_identity = declaration["work"]["identity"]
    if work_identity["basis"] == "USER_CONFIRMED":
        store.get(work_identity["confirmation_artifact_id"])

    novel_spec, validated = project_novel_spec(brief, declaration)
    group: HandoffGroup = group_leads_for_source(
        brief=brief,
        leads=leads,
        declaration=declaration,
        validated_spec=validated,
    )
    novel_spec_bytes = _json_bytes(novel_spec)
    novel_spec_artifact_id = artifact_id_for(novel_spec_bytes)
    base = {
        "schema_version": SCHEMA_VERSION,
        "brief_id": group.brief_id,
        "motivating_lead_ids": list(group.motivating_lead_ids),
        "work_ref": copy.deepcopy(group.work_ref),
        "source_ref": copy.deepcopy(group.source_ref),
        "localization": {
            "policy": "LEAD_ONLY_NOT_EXECUTOR_INPUT",
            "execution_scope": "FULL_WORK",
            "hint_refs": [copy.deepcopy(item) for item in group.hint_refs],
        },
        "novel_spec": {
            "path": "novel-spec.json",
            "raw_artifact_id": novel_spec_artifact_id,
            "expected_input_spec_hash": validated.resolved_spec_hash,
        },
        "builder": {
            "build_id": PHASE0_HANDOFF_BUILDER_ID,
            "build_request_artifact_id": build_request_artifact_id,
            "exploration_brief_artifact_id": request["exploration_brief_artifact_id"],
            "research_lead_artifact_ids": request["research_lead_artifact_ids"],
            "source_declaration_artifact_id": request["source_declaration_artifact_id"],
        },
        "readiness": {
            "status": "READY_FOR_XHNOVEL",
            "rights_basis": validated.rights["basis"],
            "may_store_full_text": validated.rights["may_store_full_text"],
            "may_send_to_external_model": validated.rights[
                "may_send_to_external_model"
            ],
            "source_quality_tier": validated.source_quality_tier,
            "discovery_brief_hash": group.discovery_brief_hash,
        },
        "contains_evidence": False,
        "requested_at": request["requested_at"],
    }
    handoff_id = derived_id(
        "EvidenceHandoff",
        {
            "brief_id": base["brief_id"],
            "motivating_lead_ids": base["motivating_lead_ids"],
            "work_ref_id": base["work_ref"]["work_ref_id"],
            "source_ref_id": base["source_ref"]["source_ref_id"],
            "expected_input_spec_hash": base["novel_spec"][
                "expected_input_spec_hash"
            ],
            "build_request_artifact_id": build_request_artifact_id,
        },
    )
    handoff = {
        **base,
        "handoff_id": handoff_id,
        "handoff_hash": "sha256:" + "0" * 64,
    }
    handoff["handoff_hash"] = object_hash(handoff, omit=("handoff_hash",))
    _validate_handoff_record(handoff)
    return handoff, novel_spec, novel_spec_bytes


def prepare_evidence_handoff(
    phase0_root: pathlib.Path,
    build_request: dict[str, Any],
) -> PreparedHandoff:
    root = pathlib.Path(phase0_root)
    store = ArtifactStore(root / "objects")
    request = validate_handoff_build_request(build_request)
    request_artifact_id = put_phase0_record(store, "HandoffBuildRequest", request)
    handoff, novel_spec, novel_spec_bytes = rebuild_evidence_handoff(
        store,
        request_artifact_id,
    )
    if store.put(novel_spec_bytes) != handoff["novel_spec"]["raw_artifact_id"]:
        raise ValidationError("E-PHASE0-SPEC-BIND", "novel spec artifact id changed")
    handoff_artifact_id = store.put(canonical_dumps(handoff))
    output_dir = root / "handoffs" / handoff["handoff_id"]
    handoff_path = output_dir / "handoff.json"
    novel_spec_path = output_dir / "novel-spec.json"
    validation_receipt_path = output_dir / "validation-receipt.json"
    _write_immutable(handoff_path, _json_bytes(handoff))
    _write_immutable(novel_spec_path, novel_spec_bytes)
    validated = validate_evidence_handoff(handoff_path, phase0_root=root)
    if validated != handoff:
        raise ValidationError("E-PHASE0-HANDOFF-REPLAY", "stored handoff replay changed")
    _write_immutable(
        validation_receipt_path,
        _json_bytes(
            _validation_receipt(
                handoff,
                handoff_artifact_id=handoff_artifact_id,
            )
        ),
    )
    return PreparedHandoff(
        phase0_root=root,
        handoff_path=handoff_path,
        novel_spec_path=novel_spec_path,
        validation_receipt_path=validation_receipt_path,
        handoff_artifact_id=handoff_artifact_id,
        build_request_artifact_id=request_artifact_id,
        handoff=handoff,
        novel_spec=novel_spec,
    )


def validate_evidence_handoff(
    handoff_path: pathlib.Path,
    *,
    phase0_root: pathlib.Path | None = None,
) -> dict[str, Any]:
    path = pathlib.Path(handoff_path)
    root = pathlib.Path(phase0_root) if phase0_root is not None else path.parents[2]
    raw = path.read_bytes()
    handoff = _read_json(path, label="EvidenceHandoff")
    if raw != _json_bytes(handoff):
        raise ValidationError("E-PHASE0-HANDOFF-REPLAY", "handoff JSON is not canonical output")
    handoff = _validate_handoff_record(handoff)
    if handoff["novel_spec"]["path"] != "novel-spec.json":
        raise ValidationError(
            "E-PHASE0-SPEC-BIND",
            "handoff novel spec path must be novel-spec.json",
        )
    store = ArtifactStore(root / "objects")
    request_artifact_id = handoff["builder"]["build_request_artifact_id"]
    request = read_phase0_record(store, request_artifact_id, "HandoffBuildRequest")
    expected_builder = {
        "build_id": PHASE0_HANDOFF_BUILDER_ID,
        "build_request_artifact_id": request_artifact_id,
        "exploration_brief_artifact_id": request["exploration_brief_artifact_id"],
        "research_lead_artifact_ids": request["research_lead_artifact_ids"],
        "source_declaration_artifact_id": request["source_declaration_artifact_id"],
    }
    if handoff["builder"] != expected_builder:
        raise ValidationError(
            "E-PHASE0-HANDOFF-REPLAY",
            "handoff builder inputs differ from the build request",
        )
    rebuilt, novel_spec, novel_spec_bytes = rebuild_evidence_handoff(
        store,
        request_artifact_id,
    )
    if handoff != rebuilt:
        raise ValidationError(
            "E-PHASE0-HANDOFF-REPLAY",
            "evidence handoff differs from deterministic replay",
        )
    stored_spec = store.get(handoff["novel_spec"]["raw_artifact_id"])
    if stored_spec != novel_spec_bytes:
        raise ValidationError("E-PHASE0-SPEC-BIND", "stored novel spec bytes changed")
    novel_spec_path = path.parent / "novel-spec.json"
    if not novel_spec_path.is_file() or novel_spec_path.read_bytes() != novel_spec_bytes:
        raise ValidationError("E-PHASE0-SPEC-BIND", "visible novel spec differs from CAS")
    validated_spec = load_validated_direct_research_spec(
        novel_spec_path,
        purpose=SpecValidationPurpose.EVIDENCE_HANDOFF,
    )
    if (
        validated_spec.effective_spec != novel_spec
        or validated_spec.resolved_spec_hash
        != handoff["novel_spec"]["expected_input_spec_hash"]
    ):
        raise ValidationError(
            "E-PHASE0-SPEC-BIND",
            "novel spec no longer reproduces the expected input hash",
        )
    return handoff


def _seal_brief(value: dict[str, Any]) -> dict[str, Any]:
    if "brief_id" in value:
        return validate_exploration_brief(value)
    required = {"research_question", "evidence_discovery_brief", "scope", "frozen_at"}
    if set(value) != required:
        raise ValidationError(
            "E-PHASE0-PREPARE",
            "brief draft must contain research_question, evidence_discovery_brief, scope, frozen_at",
        )
    return make_exploration_brief(
        research_question=value["research_question"],
        evidence_discovery_brief=value["evidence_discovery_brief"],
        scope=value["scope"],
        frozen_at=value["frozen_at"],
    )


def _seal_lead(value: dict[str, Any], brief_id: str) -> dict[str, Any]:
    if "lead_id" in value:
        lead = validate_research_lead(value)
        if lead["brief_id"] != brief_id:
            raise ValidationError("E-PHASE0-PREPARE", "sealed lead belongs to another brief")
        return lead
    required = {"work_claim", "scene_hint", "lead_sources", "frozen_at"}
    if set(value) != required:
        raise ValidationError(
            "E-PHASE0-PREPARE",
            "lead draft must contain work_claim, scene_hint, lead_sources, frozen_at",
        )
    scene_hint = copy.deepcopy(value["scene_hint"])
    if not isinstance(scene_hint, dict):
        raise ValidationError("E-PHASE0-LEAD", "scene_hint must be an object")
    raw_hints = scene_hint.get("location_hints")
    if not isinstance(raw_hints, list):
        raise ValidationError("E-PHASE0-LEAD", "location_hints must be an array")
    if any(not isinstance(hint, dict) for hint in raw_hints):
        raise ValidationError("E-PHASE0-LEAD", "location hint must be an object")
    preliminary = make_research_lead(
        brief_id=brief_id,
        work_claim=value["work_claim"],
        scene_hint={**scene_hint, "location_hints": []},
        lead_sources=value["lead_sources"],
        frozen_at=value["frozen_at"],
    )
    location_source_ids = sorted(
        source["lead_source_id"]
        for source in preliminary["lead_sources"]
        if "LOCATION_HINT" in source["supports"]
    )
    for hint in raw_hints:
        if "lead_source_ids" not in hint:
            if not location_source_ids:
                raise ValidationError(
                    "E-PHASE0-LEAD",
                    "location hint has no source declaring LOCATION_HINT support",
                )
            hint["lead_source_ids"] = location_source_ids
    return make_research_lead(
        brief_id=brief_id,
        work_claim=value["work_claim"],
        scene_hint=scene_hint,
        lead_sources=value["lead_sources"],
        frozen_at=value["frozen_at"],
    )


def _seal_declaration(
    value: dict[str, Any],
    *,
    input_dir: pathlib.Path,
) -> dict[str, Any]:
    if "source_declaration_id" in value:
        return validate_source_declaration(value)
    required = {
        "work",
        "source",
        "rights",
        "source_quality",
        "edition_label",
        "declared_at",
    }
    if set(value) != required:
        raise ValidationError(
            "E-PHASE0-PREPARE",
            "source declaration draft has an invalid field set",
        )
    work = copy.deepcopy(value["work"])
    if not isinstance(work, dict):
        raise ValidationError("E-PHASE0-PREPARE", "source declaration work must be an object")
    if "identity" not in work:
        title = work.get("canonical_title")
        author = normalize_author(work.get("author"))
        language = work.get("language")
        if not isinstance(title, str) or not title.strip() or author is None or not isinstance(language, str) or not language.strip():
            raise ValidationError(
                "E-PHASE0-PREPARE",
                "work identity must be explicit unless title, author, and language are present",
            )
        work["identity"] = {
            "basis": "TITLE_AUTHOR",
            "normalized_title": normalize_work_title(title),
            "normalized_author": author,
            "language": language.strip(),
        }
    source = copy.deepcopy(value["source"])
    if isinstance(source, dict) and isinstance(source.get("path"), str):
        source_path = pathlib.Path(source["path"]).expanduser()
        if not source_path.is_absolute():
            source["path"] = str((input_dir / source_path).resolve())
    return make_source_declaration(
        work=work,
        source=source,
        rights=value["rights"],
        source_quality=value["source_quality"],
        edition_label=value["edition_label"],
        declared_at=value["declared_at"],
    )


def prepare_handoff_from_input(
    input_path: pathlib.Path,
    phase0_root: pathlib.Path,
) -> PreparedHandoff:
    """Seal one operational exploration input, build, persist, and replay a Handoff."""
    path = pathlib.Path(input_path)
    value = _read_json(path, label="Phase 0 preparation input")
    if set(value) != {"brief", "leads", "source_declaration", "requested_at"}:
        raise ValidationError(
            "E-PHASE0-PREPARE",
            "preparation input must contain brief, leads, source_declaration, requested_at",
        )
    if not isinstance(value["brief"], dict):
        raise ValidationError("E-PHASE0-PREPARE", "brief must be an object")
    if not isinstance(value["leads"], list) or not value["leads"]:
        raise ValidationError("E-PHASE0-PREPARE", "leads must be a non-empty array")
    if not isinstance(value["source_declaration"], dict):
        raise ValidationError("E-PHASE0-PREPARE", "source_declaration must be an object")
    brief = _seal_brief(value["brief"])
    leads = [
        _seal_lead(item, brief["brief_id"])
        if isinstance(item, dict)
        else (_ for _ in ()).throw(
            ValidationError("E-PHASE0-PREPARE", "lead must be an object")
        )
        for item in value["leads"]
    ]
    declaration = _seal_declaration(
        value["source_declaration"],
        input_dir=path.parent,
    )
    root = pathlib.Path(phase0_root)
    store = ArtifactStore(root / "objects")
    brief_artifact_id = put_phase0_record(store, "ExplorationBrief", brief)
    lead_artifact_ids = [
        put_phase0_record(store, "ResearchLead", lead) for lead in leads
    ]
    declaration_artifact_id = put_phase0_record(
        store,
        "SourceDeclaration",
        declaration,
    )
    _write_immutable(root / "brief.json", _json_bytes(brief))
    for lead in leads:
        _write_immutable(
            root / "leads" / f"{lead['lead_id']}.json",
            _json_bytes(lead),
        )
    _write_immutable(
        root
        / "source-declarations"
        / f"{declaration['source_declaration_id']}.json",
        _json_bytes(declaration),
    )
    request = make_handoff_build_request(
        exploration_brief_artifact_id=brief_artifact_id,
        research_lead_artifact_ids=lead_artifact_ids,
        source_declaration_artifact_id=declaration_artifact_id,
        requested_at=value["requested_at"],
    )
    _write_immutable(
        root / "build-requests" / f"{request['build_request_id']}.json",
        _json_bytes(request),
    )
    return prepare_evidence_handoff(root, request)
