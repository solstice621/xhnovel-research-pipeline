"""Phase 0 identity, sealing, and deterministic N→1 grouping.

This module remains outside the core Catalog. It gives the open-ended exploration
layer content-bound standalone records and derives bibliographic/source identities
without allowing a lead or location hint to become evidence.
"""

from __future__ import annotations

import copy
import pathlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

from .constants import SCHEMA_VERSION
from .errors import ValidationError
from .hashing import object_hash
from .ids import derived_id
from .novel_spec import ValidatedDirectResearchSpec
from .ranking import normalize_work_title
from .schema import validate_schema

_SUPPORTED_SOURCE_KIND = {
    "txt": "txt",
    "epub": "epub",
    "directory": "directory",
    "chapter-directory": "directory",
    "site": "site",
    "static-site": "site",
}


@dataclass(frozen=True)
class HandoffGroup:
    """One brief + one resolved work/source + N motivating leads."""

    brief_id: str
    work_ref: dict[str, Any]
    source_ref: dict[str, Any]
    discovery_brief_hash: str
    motivating_lead_ids: tuple[str, ...]
    hint_refs: tuple[dict[str, Any], ...]
    group_key: str


def normalize_author(value: str | None) -> str | None:
    """Minimal frozen author normalization: strip and collapse whitespace only."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("E-PHASE0-WORK", "author must be a string or null")
    normalized = re.sub(r"\s+", " ", value).strip()
    return normalized or None


def _nonempty(value: Any, *, code: str, message: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(code, message)
    return value.strip()


def _require_fields(
    value: dict[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
    code: str,
    label: str,
) -> None:
    fields = set(value)
    allowed = required | (optional or set())
    if not required <= fields or not fields <= allowed:
        raise ValidationError(code, f"{label} has an invalid field set")


def _phase0_object_hash(
    value: dict[str, Any],
    *,
    omit: tuple[str, ...],
    code: str,
    label: str,
) -> str:
    try:
        return object_hash(value, omit=omit)
    except (TypeError, ValueError) as exc:
        raise ValidationError(code, f"{label} is not canonical JSON") from exc


def _phase0_derived_id(
    kind: str,
    value: dict[str, Any],
    *,
    code: str,
    label: str,
) -> str:
    try:
        return derived_id(kind, value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(code, f"{label} is not canonical JSON") from exc


def _sorted_strings(values: Any, *, code: str, field: str) -> list[str]:
    if not isinstance(values, list) or any(
        not isinstance(item, str) or not item.strip() for item in values
    ):
        raise ValidationError(code, f"{field} must be an array of non-empty strings")
    normalized = sorted({item.strip() for item in values})
    if len(normalized) != len(values):
        raise ValidationError(code, f"{field} must contain unique values")
    return normalized


def _canonical_external_ids(values: Any) -> list[dict[str, str]]:
    if not isinstance(values, list):
        raise ValidationError("E-PHASE0-WORK", "external_ids must be an array")
    result: list[dict[str, str]] = []
    for item in values:
        if not isinstance(item, dict) or set(item) != {"namespace", "value"}:
            raise ValidationError(
                "E-PHASE0-WORK",
                "external_ids entries must contain namespace and value",
            )
        namespace = _nonempty(
            item["namespace"],
            code="E-PHASE0-WORK",
            message="external id namespace must be non-empty",
        ).casefold()
        value = _nonempty(
            item["value"],
            code="E-PHASE0-WORK",
            message="external id value must be non-empty",
        )
        result.append({"namespace": namespace, "value": value})
    result.sort(key=lambda item: (item["namespace"], item["value"]))
    if len({(item["namespace"], item["value"]) for item in result}) != len(result):
        raise ValidationError("E-PHASE0-WORK", "external_ids must be unique")
    return result


def _canonical_work_identity(
    work: dict[str, Any],
    *,
    require_canonical: bool = True,
) -> dict[str, Any]:
    identity = work.get("identity")
    if not isinstance(identity, dict):
        raise ValidationError("E-PHASE0-WORK", "work identity must be an object")
    basis = identity.get("basis")
    canonical_title = _nonempty(
        work.get("canonical_title"),
        code="E-PHASE0-WORK",
        message="canonical_title must be non-empty",
    )
    language = _nonempty(
        work.get("language"),
        code="E-PHASE0-WORK",
        message="work language must be non-empty",
    )
    author = normalize_author(work.get("author"))
    external_ids = _canonical_external_ids(work.get("external_ids", []))

    if basis == "TITLE_AUTHOR":
        if author is None:
            raise ValidationError("E-PHASE0-WORK", "TITLE_AUTHOR identity requires an author")
        expected = {
            "basis": "TITLE_AUTHOR",
            "normalized_title": normalize_work_title(canonical_title),
            "normalized_author": author,
            "language": language,
        }
        supplied = {
            "basis": "TITLE_AUTHOR",
            "normalized_title": normalize_work_title(
                _nonempty(
                    identity.get("normalized_title"),
                    code="E-PHASE0-WORK",
                    message="TITLE_AUTHOR requires normalized_title",
                )
            ),
            "normalized_author": normalize_author(identity.get("normalized_author")),
            "language": _nonempty(
                identity.get("language"),
                code="E-PHASE0-WORK",
                message="TITLE_AUTHOR requires language",
            ),
        }
        if supplied["normalized_author"] is None:
            raise ValidationError(
                "E-PHASE0-WORK",
                "TITLE_AUTHOR requires normalized_author",
            )
    elif basis == "STABLE_EXTERNAL_ID":
        namespace = _nonempty(
            identity.get("namespace"),
            code="E-PHASE0-WORK",
            message="STABLE_EXTERNAL_ID requires a namespace",
        ).casefold()
        external_id = _nonempty(
            identity.get("external_id"),
            code="E-PHASE0-WORK",
            message="STABLE_EXTERNAL_ID requires an external_id",
        )
        expected = {
            "basis": "STABLE_EXTERNAL_ID",
            "namespace": namespace,
            "external_id": external_id,
        }
        supplied = copy.deepcopy(expected)
        if {"namespace": namespace, "value": external_id} not in external_ids:
            raise ValidationError(
                "E-PHASE0-WORK",
                "stable work identity must appear in external_ids",
            )
    elif basis == "USER_CONFIRMED":
        confirmation = _nonempty(
            identity.get("confirmation_artifact_id"),
            code="E-PHASE0-WORK",
            message="USER_CONFIRMED identity requires confirmation_artifact_id",
        )
        expected = {
            "basis": "USER_CONFIRMED",
            "confirmation_artifact_id": confirmation,
        }
        supplied = copy.deepcopy(expected)
    else:
        raise ValidationError("E-PHASE0-WORK", "work identity basis is not recognized")

    if set(identity) != set(expected) or supplied != expected:
        raise ValidationError(
            "E-PHASE0-WORK",
            "work identity conflicts with its normalized basis payload",
        )
    if require_canonical and identity != expected:
        raise ValidationError(
            "E-PHASE0-WORK",
            "work identity differs from its canonical basis payload",
        )
    return expected


def work_ref_from_declaration(declaration: dict[str, Any]) -> dict[str, Any]:
    """Derive a WorkRef and validate basis-specific identity consistency."""
    work = declaration.get("work")
    if not isinstance(work, dict):
        raise ValidationError("E-PHASE0-WORK", "source declaration work must be an object")
    identity = _canonical_work_identity(work)
    canonical_title = str(work["canonical_title"]).strip()
    author = normalize_author(work.get("author"))
    language = str(work["language"]).strip()
    aliases = _sorted_strings(
        work.get("aliases", []),
        code="E-PHASE0-WORK",
        field="work.aliases",
    )
    external_ids = _canonical_external_ids(work.get("external_ids", []))
    work_ref_id = derived_id("WorkRef", identity)
    return {
        "work_ref_id": work_ref_id,
        "identity": copy.deepcopy(identity),
        "canonical_title": canonical_title,
        "normalized_title": normalize_work_title(canonical_title),
        "author": author,
        "normalized_author": author,
        "language": language,
        "aliases": aliases,
        "external_ids": external_ids,
        "resolution_basis": identity["basis"],
    }


def _bind_source_metadata(
    source: dict[str, Any],
    work: dict[str, Any],
    *,
    require_canonical: bool,
) -> dict[str, Any]:
    if not isinstance(source, dict):
        raise ValidationError("E-PHASE0-SOURCE", "source must be an object")
    result = copy.deepcopy(source)
    expected = {
        "title": work["canonical_title"],
        "author": work["author"],
        "language": work["language"],
    }
    for field, expected_value in expected.items():
        if field not in result:
            continue
        raw_value = result[field]
        if field == "author":
            if raw_value is not None and (
                not isinstance(raw_value, str) or not raw_value.strip()
            ):
                raise ValidationError(
                    "E-PHASE0-SOURCE-BIND",
                    "source author must be null or non-empty",
                )
            normalized = normalize_author(raw_value)
        else:
            normalized = _nonempty(
                raw_value,
                code="E-PHASE0-SOURCE-BIND",
                message=f"source {field} must be non-empty",
            )
        if normalized != expected_value:
            raise ValidationError(
                "E-PHASE0-SOURCE-BIND",
                f"source {field} conflicts with resolved work metadata",
            )
        if require_canonical and raw_value != expected_value:
            raise ValidationError(
                "E-PHASE0-SOURCE-BIND",
                f"source {field} is not canonical",
            )
        result[field] = expected_value
    return result


def source_ref_from_validated(
    declaration: dict[str, Any],
    validated: ValidatedDirectResearchSpec,
    work_ref: dict[str, Any],
) -> dict[str, Any]:
    """Derive source identity from the path-resolved, adapter-validated source config."""
    if work_ref != work_ref_from_declaration(declaration):
        raise ValidationError(
            "E-PHASE0-SOURCE-BIND",
            "work reference differs from the source declaration",
        )
    source = copy.deepcopy(validated.normalized_source_spec)
    expected_source = _bind_source_metadata(
        declaration["source"],
        work_ref,
        require_canonical=True,
    )
    raw_declared_kind = str(expected_source.get("kind", "")).casefold()
    try:
        expected_source["kind"] = _SUPPORTED_SOURCE_KIND[raw_declared_kind]
    except KeyError as exc:
        raise ValidationError("E-PHASE0-SOURCE", "source kind is not supported") from exc
    if expected_source["kind"] in {"txt", "epub", "directory"}:
        expected_source["path"] = str(pathlib.Path(str(expected_source["path"])).resolve())
    expected_source["title"] = work_ref["canonical_title"]
    expected_source["author"] = work_ref["author"]
    expected_source["language"] = work_ref["language"]
    if source != expected_source:
        raise ValidationError(
            "E-PHASE0-SOURCE-BIND",
            "validated source differs from the source declaration projection",
        )

    raw_kind = str(source.get("kind", "")).casefold()
    try:
        source_kind = _SUPPORTED_SOURCE_KIND[raw_kind]
    except KeyError as exc:
        raise ValidationError("E-PHASE0-SOURCE", "source kind is not supported") from exc
    source["kind"] = source_kind
    if source_kind in {"txt", "epub", "directory"}:
        path = pathlib.Path(str(source["path"])).resolve()
        source["path"] = str(path)
        locator = path.as_uri()
    else:
        locator = _nonempty(
            source.get("index_url"),
            code="E-PHASE0-SOURCE",
            message="site source requires index_url",
        )
    source_config_hash = object_hash(source, omit=())
    source_ref_id = derived_id(
        "SourceRef",
        {
            "work_ref_id": work_ref["work_ref_id"],
            "source_config_hash": source_config_hash,
        },
    )
    return {
        "source_ref_id": source_ref_id,
        "work_ref_id": work_ref["work_ref_id"],
        "kind": source_kind,
        "locator": locator,
        "source_config_hash": source_config_hash,
        "edition_label": _nonempty(
            declaration.get("edition_label"),
            code="E-PHASE0-SOURCE",
            message="edition_label must be non-empty",
        ),
        "content_binding": "DEFERRED_TO_INGESTION",
    }


def _brief_identity_payload(brief: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in brief.items()
        if key not in {"brief_id", "brief_hash", "frozen_at"}
    }


def validate_exploration_brief(brief: dict[str, Any]) -> dict[str, Any]:
    validate_schema("ExplorationBrief", brief)
    expected_id = derived_id("ExplorationBrief", _brief_identity_payload(brief))
    expected_hash = object_hash(brief, omit=("brief_hash", "frozen_at"))
    if brief["brief_id"] != expected_id or brief["brief_hash"] != expected_hash:
        raise ValidationError("E-PHASE0-BRIEF-BIND", "exploration brief identity changed")
    return copy.deepcopy(brief)


def make_exploration_brief(
    *,
    research_question: str,
    evidence_discovery_brief: str,
    scope: dict[str, Any],
    frozen_at: str,
) -> dict[str, Any]:
    canonical_scope = copy.deepcopy(scope)
    if not isinstance(canonical_scope, dict):
        raise ValidationError("E-PHASE0-BRIEF", "scope must be an object")
    canonical_scope["genres"] = _sorted_strings(
        canonical_scope.get("genres"),
        code="E-PHASE0-BRIEF",
        field="scope.genres",
    )
    for field in ("prefer", "avoid"):
        if field in canonical_scope:
            canonical_scope[field] = _sorted_strings(
                canonical_scope[field],
                code="E-PHASE0-BRIEF",
                field=f"scope.{field}",
            )
    base = {
        "schema_version": SCHEMA_VERSION,
        "research_question": _nonempty(
            research_question,
            code="E-PHASE0-BRIEF",
            message="research_question must be non-empty",
        ),
        "evidence_discovery_brief": evidence_discovery_brief,
        "scope": canonical_scope,
    }
    if not isinstance(evidence_discovery_brief, str) or not evidence_discovery_brief.strip():
        raise ValidationError(
            "E-PHASE0-BRIEF",
            "evidence_discovery_brief must be non-empty",
        )
    brief_id = _phase0_derived_id(
        "ExplorationBrief",
        base,
        code="E-PHASE0-BRIEF",
        label="exploration brief",
    )
    record = {
        **base,
        "brief_id": brief_id,
        "brief_hash": "sha256:" + "0" * 64,
        "frozen_at": frozen_at,
    }
    record["brief_hash"] = _phase0_object_hash(
        record,
        omit=("brief_hash", "frozen_at"),
        code="E-PHASE0-BRIEF",
        label="exploration brief",
    )
    validate_schema("ExplorationBrief", record)
    return record


def _canonical_lead_source(source: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(source, dict):
        raise ValidationError("E-PHASE0-LEAD", "lead source must be an object")
    _require_fields(
        source,
        required={"source_kind", "locator", "supports"},
        optional={"title", "publisher"},
        code="E-PHASE0-LEAD",
        label="lead source",
    )
    payload: dict[str, Any] = {
        "source_kind": _nonempty(
            source.get("source_kind"),
            code="E-PHASE0-LEAD",
            message="lead source kind must be non-empty",
        ).upper(),
        "locator": _nonempty(
            source.get("locator"),
            code="E-PHASE0-LEAD",
            message="lead source locator must be non-empty",
        ),
        "role": "LEAD_ONLY",
        "supports": _sorted_strings(
            source.get("supports"),
            code="E-PHASE0-LEAD",
            field="lead_source.supports",
        ),
    }
    for field in ("title", "publisher"):
        value = source.get(field)
        if value is None:
            payload[field] = None
        elif not isinstance(value, str) or not value.strip():
            raise ValidationError(
                "E-PHASE0-LEAD",
                f"lead source {field} must be null or a non-empty string",
            )
        else:
            payload[field] = value.strip()
    return {
        "lead_source_id": _phase0_derived_id(
            "LeadSource",
            payload,
            code="E-PHASE0-LEAD",
            label="lead source",
        ),
        **payload,
    }


def _canonical_work_claim(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("E-PHASE0-LEAD", "work_claim must be an object")
    _require_fields(
        value,
        required={"title", "author", "language", "aliases"},
        code="E-PHASE0-LEAD",
        label="work_claim",
    )
    title = _nonempty(
        value.get("title"),
        code="E-PHASE0-LEAD",
        message="work_claim.title must be non-empty",
    )
    language = _nonempty(
        value.get("language"),
        code="E-PHASE0-LEAD",
        message="work_claim.language must be non-empty",
    )
    return {
        "title": title,
        "author": normalize_author(value.get("author")),
        "language": language,
        "aliases": _sorted_strings(
            value.get("aliases", []),
            code="E-PHASE0-LEAD",
            field="work_claim.aliases",
        ),
    }


def make_research_lead(
    *,
    brief_id: str,
    work_claim: dict[str, Any],
    scene_hint: dict[str, Any],
    lead_sources: list[dict[str, Any]],
    frozen_at: str,
) -> dict[str, Any]:
    if not isinstance(lead_sources, list):
        raise ValidationError("E-PHASE0-LEAD", "lead_sources must be an array")
    sources = sorted(
        (_canonical_lead_source(item) for item in lead_sources),
        key=lambda item: item["lead_source_id"],
    )
    source_ids = {item["lead_source_id"] for item in sources}
    location_source_ids = {
        item["lead_source_id"]
        for item in sources
        if "LOCATION_HINT" in item["supports"]
    }
    if not isinstance(scene_hint, dict):
        raise ValidationError("E-PHASE0-LEAD", "scene_hint must be an object")
    _require_fields(
        scene_hint,
        required={"summary", "why_relevant", "interaction_tags", "location_hints"},
        code="E-PHASE0-LEAD",
        label="scene_hint",
    )
    raw_hints = scene_hint["location_hints"]
    if not isinstance(raw_hints, list):
        raise ValidationError("E-PHASE0-LEAD", "location_hints must be an array")
    hints = []
    for raw in raw_hints:
        if not isinstance(raw, dict):
            raise ValidationError("E-PHASE0-LEAD", "location hint must be an object")
        _require_fields(
            raw,
            required={"kind", "value", "basis", "lead_source_ids"},
            code="E-PHASE0-LEAD",
            label="location hint",
        )
        refs = _sorted_strings(
            raw.get("lead_source_ids"),
            code="E-PHASE0-LEAD",
            field="location_hint.lead_source_ids",
        )
        if not refs or not set(refs) <= source_ids:
            raise ValidationError(
                "E-PHASE0-LEAD",
                "location hint must reference lead sources in the same lead",
            )
        if not set(refs) <= location_source_ids:
            raise ValidationError(
                "E-PHASE0-LEAD",
                "location hint must reference sources declaring LOCATION_HINT support",
            )
        hints.append(
            {
                "kind": _nonempty(
                    raw.get("kind"),
                    code="E-PHASE0-LEAD",
                    message="location hint kind must be non-empty",
                ).upper(),
                "value": _nonempty(
                    raw.get("value"),
                    code="E-PHASE0-LEAD",
                    message="location hint value must be non-empty",
                ),
                "basis": _nonempty(
                    raw.get("basis"),
                    code="E-PHASE0-LEAD",
                    message="location hint basis must be non-empty",
                ).upper(),
                "lead_source_ids": refs,
            }
        )
    hints.sort(key=lambda item: object_hash(item, omit=()))
    canonical_hint = {
        "summary": _nonempty(
            scene_hint.get("summary"),
            code="E-PHASE0-LEAD",
            message="scene_hint.summary must be non-empty",
        ),
        "why_relevant": _nonempty(
            scene_hint.get("why_relevant"),
            code="E-PHASE0-LEAD",
            message="scene_hint.why_relevant must be non-empty",
        ),
        "interaction_tags": _sorted_strings(
            scene_hint.get("interaction_tags"),
            code="E-PHASE0-LEAD",
            field="scene_hint.interaction_tags",
        ),
        "location_hints": hints,
    }
    identity = {
        "brief_id": brief_id,
        "work_claim": _canonical_work_claim(work_claim),
        "scene_hint": canonical_hint,
        "lead_sources": sources,
    }
    lead_id = _phase0_derived_id(
        "ResearchLead",
        identity,
        code="E-PHASE0-LEAD",
        label="research lead",
    )
    record = {
        "schema_version": SCHEMA_VERSION,
        "lead_id": lead_id,
        **identity,
        "assurance": "UNVERIFIED_LEAD",
        "lead_hash": "sha256:" + "0" * 64,
        "frozen_at": frozen_at,
    }
    record["lead_hash"] = _phase0_object_hash(
        record,
        omit=("lead_hash", "frozen_at"),
        code="E-PHASE0-LEAD",
        label="research lead",
    )
    validate_schema("ResearchLead", record)
    return record


def validate_research_lead(lead: dict[str, Any]) -> dict[str, Any]:
    validate_schema("ResearchLead", lead)
    for source in lead["lead_sources"]:
        payload = {key: value for key, value in source.items() if key != "lead_source_id"}
        if source["lead_source_id"] != derived_id("LeadSource", payload):
            raise ValidationError("E-PHASE0-LEAD-BIND", "lead source identity changed")
    source_ids = {source["lead_source_id"] for source in lead["lead_sources"]}
    location_source_ids = {
        source["lead_source_id"]
        for source in lead["lead_sources"]
        if "LOCATION_HINT" in source["supports"]
    }
    for hint in lead["scene_hint"]["location_hints"]:
        if not set(hint["lead_source_ids"]) <= source_ids:
            raise ValidationError("E-PHASE0-LEAD-BIND", "location hint references another lead")
        if not set(hint["lead_source_ids"]) <= location_source_ids:
            raise ValidationError(
                "E-PHASE0-LEAD-BIND",
                "location hint source does not declare LOCATION_HINT support",
            )
    identity = {
        "brief_id": lead["brief_id"],
        "work_claim": lead["work_claim"],
        "scene_hint": lead["scene_hint"],
        "lead_sources": lead["lead_sources"],
    }
    if (
        lead["lead_id"] != derived_id("ResearchLead", identity)
        or lead["lead_hash"] != object_hash(lead, omit=("lead_hash", "frozen_at"))
    ):
        raise ValidationError("E-PHASE0-LEAD-BIND", "research lead identity changed")
    return copy.deepcopy(lead)


def make_source_declaration(
    *,
    work: dict[str, Any],
    source: dict[str, Any],
    rights: dict[str, Any],
    source_quality: dict[str, Any],
    edition_label: str,
    declared_at: str,
) -> dict[str, Any]:
    work_copy = copy.deepcopy(work)
    if not isinstance(work_copy, dict):
        raise ValidationError("E-PHASE0-WORK", "work must be an object")
    _require_fields(
        work_copy,
        required={
            "identity",
            "canonical_title",
            "author",
            "language",
            "aliases",
            "external_ids",
        },
        code="E-PHASE0-WORK",
        label="work",
    )
    identity = _canonical_work_identity(work_copy, require_canonical=False)
    canonical_work = {
        "identity": identity,
        "canonical_title": str(work_copy["canonical_title"]).strip(),
        "author": normalize_author(work_copy.get("author")),
        "language": str(work_copy["language"]).strip(),
        "aliases": _sorted_strings(
            work_copy.get("aliases", []),
            code="E-PHASE0-WORK",
            field="work.aliases",
        ),
        "external_ids": _canonical_external_ids(work_copy.get("external_ids", [])),
    }
    source_copy = copy.deepcopy(source)
    if not isinstance(source_copy, dict):
        raise ValidationError("E-PHASE0-SOURCE", "source must be an object")
    raw_kind = _nonempty(
        source_copy.get("kind"),
        code="E-PHASE0-SOURCE",
        message="source kind must be non-empty",
    ).casefold()
    try:
        source_copy["kind"] = _SUPPORTED_SOURCE_KIND[raw_kind]
    except KeyError as exc:
        raise ValidationError("E-PHASE0-SOURCE", "source kind is not supported") from exc
    if source_copy["kind"] in {"txt", "epub", "directory"}:
        path_value = source_copy.get("path")
        if not isinstance(path_value, str) or not path_value.strip():
            raise ValidationError(
                "E-PHASE0-SOURCE",
                "local source declaration requires a non-empty path",
            )
        path = pathlib.Path(path_value).expanduser()
        if not path.is_absolute():
            raise ValidationError(
                "E-PHASE0-SOURCE",
                "local source declaration path must be absolute",
            )
        source_copy["path"] = str(path.resolve())
    source_copy = _bind_source_metadata(
        source_copy,
        canonical_work,
        require_canonical=False,
    )
    base = {
        "schema_version": SCHEMA_VERSION,
        "work": canonical_work,
        "source": source_copy,
        "rights": copy.deepcopy(rights),
        "source_quality": copy.deepcopy(source_quality),
        "edition_label": _nonempty(
            edition_label,
            code="E-PHASE0-SOURCE",
            message="edition_label must be non-empty",
        ),
    }
    declaration_hash = _phase0_object_hash(
        base,
        omit=(),
        code="E-PHASE0-SOURCE",
        label="source declaration",
    )
    declaration_id = _phase0_derived_id(
        "SourceDeclaration",
        {"declaration_hash": declaration_hash},
        code="E-PHASE0-SOURCE",
        label="source declaration",
    )
    record = {
        **base,
        "source_declaration_id": declaration_id,
        "declaration_hash": declaration_hash,
        "declared_at": declared_at,
    }
    validate_schema("SourceDeclaration", record)
    return record


def validate_source_declaration(declaration: dict[str, Any]) -> dict[str, Any]:
    validate_schema("SourceDeclaration", declaration)
    work_ref = work_ref_from_declaration(declaration)
    _bind_source_metadata(
        declaration["source"],
        work_ref,
        require_canonical=True,
    )
    payload = {
        key: copy.deepcopy(value)
        for key, value in declaration.items()
        if key not in {"source_declaration_id", "declaration_hash", "declared_at"}
    }
    expected_hash = object_hash(payload, omit=())
    expected_id = derived_id("SourceDeclaration", {"declaration_hash": expected_hash})
    if (
        declaration["declaration_hash"] != expected_hash
        or declaration["source_declaration_id"] != expected_id
    ):
        raise ValidationError(
            "E-PHASE0-SOURCE-BIND",
            "source declaration identity changed",
        )
    return copy.deepcopy(declaration)


def _lead_matches_work(lead: dict[str, Any], work_ref: dict[str, Any]) -> bool:
    claim = lead["work_claim"]
    titles = {
        normalize_work_title(work_ref["canonical_title"]),
        *(normalize_work_title(alias) for alias in work_ref["aliases"]),
    }
    titles.discard("")
    if normalize_work_title(claim["title"]) not in titles:
        return False
    if claim["language"] != work_ref["language"]:
        return False
    claim_author = normalize_author(claim.get("author"))
    work_author = normalize_author(work_ref.get("author"))
    if work_ref["resolution_basis"] == "TITLE_AUTHOR":
        return claim_author is not None and claim_author == work_author
    return claim_author is None or work_author is None or claim_author == work_author


def group_leads_for_source(
    *,
    brief: dict[str, Any],
    leads: Iterable[dict[str, Any]],
    declaration: dict[str, Any],
    validated_spec: ValidatedDirectResearchSpec,
) -> HandoffGroup:
    """Validate and group N Leads into one work/source/brief execution unit."""
    brief = validate_exploration_brief(brief)
    declaration = validate_source_declaration(declaration)
    work_ref = work_ref_from_declaration(declaration)
    if validated_spec.rights != declaration["rights"]:
        raise ValidationError(
            "E-PHASE0-GROUP",
            "validated spec rights differ from the source declaration",
        )
    if validated_spec.source_quality != declaration["source_quality"]:
        raise ValidationError(
            "E-PHASE0-GROUP",
            "validated spec source quality differs from the source declaration",
        )
    source_ref = source_ref_from_validated(declaration, validated_spec, work_ref)
    checked = [validate_research_lead(lead) for lead in leads]
    if not checked:
        raise ValidationError("E-PHASE0-GROUP", "handoff group requires at least one lead")
    for lead in checked:
        if lead["brief_id"] != brief["brief_id"]:
            raise ValidationError("E-PHASE0-GROUP", "lead belongs to another exploration brief")
        if not _lead_matches_work(lead, work_ref):
            raise ValidationError("E-PHASE0-GROUP", "lead work claim differs from resolved work")
    claimed_authors = {
        author
        for lead in checked
        if (author := normalize_author(lead["work_claim"].get("author"))) is not None
    }
    if len(claimed_authors) > 1:
        raise ValidationError(
            "E-PHASE0-GROUP",
            "lead work claims contain conflicting authors",
        )
    if validated_spec.discovery_brief != brief["evidence_discovery_brief"]:
        raise ValidationError(
            "E-PHASE0-GROUP",
            "validated spec discovery brief differs from exploration brief",
        )
    motivating = tuple(sorted(lead["lead_id"] for lead in checked))
    if len(set(motivating)) != len(motivating):
        raise ValidationError("E-PHASE0-GROUP", "handoff group contains duplicate leads")
    hint_refs = tuple(
        {
            "lead_id": lead["lead_id"],
            "hint_indexes": list(range(len(lead["scene_hint"]["location_hints"]))),
        }
        for lead in sorted(checked, key=lambda item: item["lead_id"])
        if lead["scene_hint"]["location_hints"]
    )
    discovery_brief_hash = object_hash(
        {"discovery_brief": brief["evidence_discovery_brief"]},
        omit=(),
    )
    key_payload = {
        "brief_id": brief["brief_id"],
        "work_ref_id": work_ref["work_ref_id"],
        "source_ref_id": source_ref["source_ref_id"],
        "discovery_brief_hash": discovery_brief_hash,
    }
    return HandoffGroup(
        brief_id=brief["brief_id"],
        work_ref=work_ref,
        source_ref=source_ref,
        discovery_brief_hash=discovery_brief_hash,
        motivating_lead_ids=motivating,
        hint_refs=hint_refs,
        group_key=object_hash(key_payload, omit=()),
    )
