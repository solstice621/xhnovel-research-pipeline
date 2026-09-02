from __future__ import annotations

import copy
import json
from typing import Any, Iterable

from .canonical import canonical_dumps
from .catalog import Catalog
from .constants import SCHEMA_VERSION
from .errors import ValidationError
from .hashing import artifact_id_for, object_hash
from .ids import derived_id
from .novel_adapters import chapter_number
from .store import ArtifactStore


NOVEL_TRIAGE_MATERIALIZER_BUILD_ID = "novel-triage-review-materializer-v1"
NOVEL_SOURCE_CLASSIFIER_BUILD_ID = "novel-source-classifier-v1"
CHAPTER_IDENTITY_SCOPE = "DISCOVERY_ORDER_VS_BODY_HEADING_V1"
_LOCAL_NOVEL_PLATFORMS = {"novel:txt", "novel:epub", "novel:directory"}
RIGHTS_BASES = {
    "USER_AUTHORIZED_LOCAL_COPY",
    "PUBLIC_DOMAIN",
    "LICENSED",
    "FAIR_USE_RESEARCH",
    "UNKNOWN",
}
RIGHTS_FIELDS = {
    "basis",
    "may_store_full_text",
    "may_send_to_external_model",
    "may_export_excerpts",
}
SOURCE_QUALITY_FIELDS = {"edition_status", "textual_completeness"}
EDITION_STATUSES = {
    "OFFICIAL",
    "PUBLISHED_EDITION",
    "USER_VERIFIED_COPY",
    "UNOFFICIAL_COPY",
    "UNKNOWN",
}
TEXTUAL_COMPLETENESS = {"COMPLETE", "PARTIAL", "UNKNOWN"}


def declared_rights(
    spec: dict[str, Any],
    *,
    require_storage: bool = False,
    require_external_model: bool = False,
) -> dict[str, Any]:
    rights = spec.get("rights")
    if not isinstance(rights, dict) or set(rights) != RIGHTS_FIELDS:
        raise ValidationError(
            "E-RIGHTS",
            "rights must explicitly contain basis, may_store_full_text, "
            "may_send_to_external_model and may_export_excerpts",
        )
    if rights.get("basis") not in RIGHTS_BASES:
        raise ValidationError("E-RIGHTS", "rights.basis is not recognized")
    for field in RIGHTS_FIELDS - {"basis"}:
        if not isinstance(rights.get(field), bool):
            raise ValidationError("E-RIGHTS", f"rights.{field} must be boolean")
    if require_storage and not rights["may_store_full_text"]:
        raise ValidationError("E-RIGHTS-STORAGE", "rights do not permit storing full text")
    if require_external_model and (
        rights["basis"] == "UNKNOWN" or not rights["may_send_to_external_model"]
    ):
        raise ValidationError(
            "E-RIGHTS-EXTERNAL-MODEL",
            "an explicit non-UNKNOWN rights basis and external-model permission are required",
        )
    return copy.deepcopy(rights)


def rights_for_bundle(
    catalog: Catalog,
    store: ArtifactStore,
    bundle: dict[str, Any],
    *,
    require_storage: bool = False,
    require_external_model: bool = False,
) -> dict[str, Any]:
    """Resolve immutable declared rights through Bundle -> Snapshot -> Ingestion -> spec."""
    snapshot_ids = bundle.get("collection_snapshot_ids")
    if not isinstance(snapshot_ids, list) or not snapshot_ids:
        raise ValidationError("E-RIGHTS", "bundle has no collection snapshot rights lineage")
    snapshots = [catalog.get("CollectionSnapshot", snapshot_id) for snapshot_id in snapshot_ids]
    request_id = bundle.get("request_id")
    if any(snapshot.get("request_id") != request_id for snapshot in snapshots):
        raise ValidationError("E-RIGHTS", "bundle and snapshot request lineage differs")
    ingestion_ids = {snapshot.get("ingestion_run_id") for snapshot in snapshots}
    if len(ingestion_ids) != 1 or None in ingestion_ids:
        raise ValidationError("E-RIGHTS", "bundle must resolve to exactly one ingestion rights record")
    ingestion = catalog.get("NovelIngestionRun", next(iter(ingestion_ids)))
    try:
        raw = store.get(ingestion["input_spec_artifact_id"])
        spec = json.loads(raw.decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("E-RIGHTS", "stored ingestion specification is invalid") from exc
    if (
        not isinstance(spec, dict)
        or artifact_id_for(raw) != ingestion["input_spec_artifact_id"]
        or object_hash(spec, omit=()) != ingestion["input_spec_hash"]
    ):
        raise ValidationError("E-RIGHTS", "stored ingestion rights specification changed")
    return declared_rights(
        spec,
        require_storage=require_storage,
        require_external_model=require_external_model,
    )


def resolve_validated_bundle_ingestion(
    catalog: Catalog,
    store: ArtifactStore,
    bundle: dict[str, Any],
    *,
    require_external_model: bool = False,
) -> dict[str, Any]:
    """Resolve rights only after the complete immutable novel lineage validates."""
    if catalog.get("EvidenceBundle", bundle.get("bundle_id", "")) != bundle:
        raise ValidationError("E-BUNDLE-BIND", "bundle is not the stored immutable record")

    # Local imports keep the validation dependency acyclic. These validators are
    # the single source of truth for ingestion, snapshot, bundle-member, and
    # deterministic triage closure; egress/export must not maintain weaker copies.
    from .novel_ingest import validate_novel_ingestion
    from .validate import validate_collection, validate_evidence

    frozen_ids_before = set(catalog.frozen_bundle_ids)
    try:
        validate_novel_ingestion(catalog, store)
        validate_collection(catalog, store)
        validate_evidence(catalog, store)
    finally:
        catalog.frozen_bundle_ids.clear()
        catalog.frozen_bundle_ids.update(frozen_ids_before)

    snapshots = [
        catalog.get("CollectionSnapshot", snapshot_id)
        for snapshot_id in bundle["collection_snapshot_ids"]
    ]
    ingestion_ids = {snapshot["ingestion_run_id"] for snapshot in snapshots}
    if len(ingestion_ids) != 1:
        raise ValidationError("E-BUNDLE-BIND", "validated bundle has ambiguous ingestion lineage")
    ingestion = catalog.get("NovelIngestionRun", next(iter(ingestion_ids)))
    raw = store.get(ingestion["input_spec_artifact_id"])
    try:
        input_spec = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("E-RIGHTS", "stored ingestion specification is invalid") from exc
    rights = rights_for_bundle(
        catalog,
        store,
        bundle,
        require_storage=True,
        require_external_model=require_external_model,
    )
    return {
        "bundle": bundle,
        "snapshots": snapshots,
        "ingestion": ingestion,
        "input_spec": input_spec,
        "rights": rights,
    }


def declared_source_quality(spec: dict[str, Any]) -> dict[str, str]:
    value = spec.get("source_quality")
    if value is None:
        return {"edition_status": "UNKNOWN", "textual_completeness": "UNKNOWN"}
    if not isinstance(value, dict) or set(value) != SOURCE_QUALITY_FIELDS:
        raise ValidationError(
            "E-SOURCE-QUALITY",
            "source_quality must contain exactly edition_status and textual_completeness",
        )
    if value.get("edition_status") not in EDITION_STATUSES:
        raise ValidationError("E-SOURCE-QUALITY", "source_quality.edition_status is not recognized")
    if value.get("textual_completeness") not in TEXTUAL_COMPLETENESS:
        raise ValidationError(
            "E-SOURCE-QUALITY", "source_quality.textual_completeness is not recognized"
        )
    return copy.deepcopy(value)


def source_quality_tier(source_quality: dict[str, str]) -> str:
    """Classify a validated source-quality declaration for all runtime callers."""

    edition = source_quality["edition_status"]
    completeness = source_quality["textual_completeness"]
    if completeness == "COMPLETE" and edition == "OFFICIAL":
        return "A"
    if completeness == "COMPLETE" and edition in {
        "PUBLISHED_EDITION",
        "USER_VERIFIED_COPY",
    }:
        return "B"
    return "D"


def deterministic_triage_assessment(
    catalog: Catalog,
    retrieval: dict[str, Any],
    *,
    rights: dict[str, Any],
    source_quality: dict[str, str],
    policy_hash: str,
    assessed_at: str,
) -> dict[str, Any]:
    tier = source_quality_tier(source_quality)
    allowed_uses = ["event-facts"] if tier in {"A", "B"} else ["lead-only"]
    record = {
        "schema_version": SCHEMA_VERSION,
        "retrieval_id": retrieval["retrieval_id"],
        "tier": tier,
        "technical_access": novel_technical_access(catalog, retrieval),
        "rights": copy.deepcopy(rights),
        "source_quality": copy.deepcopy(source_quality),
        "allowed_uses": allowed_uses,
        "selection_decision": "selected",
        "decision_reason": (
            "deterministic source classification from declared edition status and "
            "textual completeness"
        ),
        "assessor_build_id": NOVEL_SOURCE_CLASSIFIER_BUILD_ID,
        "policy_hash": policy_hash,
        "assessed_at": assessed_at,
    }
    return {**record, "assessment_id": derived_id("TriageAssessment", record)}


def novel_technical_access(catalog: Catalog, retrieval: dict[str, Any]) -> dict[str, Any]:
    source = catalog.get("Source", retrieval["source_id"])
    platform_id = str(source.get("platform_id") or "").casefold()
    if platform_id == "novel:site":
        if retrieval.get("http_status") != 200 or retrieval.get("status") != "FETCHED":
            raise ValidationError(
                "E-NOVEL-TRIAGE-BIND",
                f"{retrieval['retrieval_id']} lacks a successful anonymous fetch observation",
            )
        return {"method": "ANONYMOUS_HTTP", "succeeded": True}
    if platform_id in _LOCAL_NOVEL_PLATFORMS:
        if retrieval.get("status") != "FETCHED":
            raise ValidationError(
                "E-NOVEL-TRIAGE-BIND",
                f"{retrieval['retrieval_id']} lacks a successful local-read observation",
            )
        return {"method": "LOCAL_FILE", "succeeded": True}
    raise ValidationError(
        "E-NOVEL-TRIAGE-BIND",
        f"{retrieval['retrieval_id']} has unsupported full-text platform {platform_id!r}",
    )


def find_bound_triage_review(
    catalog: Catalog,
    review_ids: Iterable[str],
    retrieval_id: str,
) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for review_id in review_ids:
        review = catalog.get("CollectionReview", review_id)
        collector = catalog.get("CollectionDecision", review["collector_decision_id"])
        if collector.get("task") == "TRIAGE" and collector.get("subject_ids") == [retrieval_id]:
            matches.append(review)
    if len(matches) != 1:
        raise ValidationError(
            "E-NOVEL-TRIAGE-BIND",
            f"{retrieval_id} requires exactly one bound TRIAGE review, found {len(matches)}",
        )
    return matches[0]


def chapter_identity_review_input(
    chapter: dict[str, Any],
    segments: list[dict[str, Any]],
) -> dict[str, Any]:
    if not segments:
        raise ValidationError("E-CHAPTER-IDENTITY", "chapter has no body segment to identify")
    heading = segments[0]["normalized_text"]
    observed_number = chapter_number(heading)
    expected_number = chapter.get("declared_number")
    if expected_number is None:
        expected_number = chapter["ordinal"]
    if observed_number is None:
        raise ValidationError(
            "E-CHAPTER-IDENTITY",
            f"chapter body has no independently observable heading number: {chapter['chapter_id']}",
        )
    if observed_number != expected_number:
        raise ValidationError(
            "E-CHAPTER-IDENTITY",
            f"chapter body heading {observed_number} does not match discovered number "
            f"{expected_number}: {chapter['chapter_id']}",
        )
    return {
        "identity_scope": CHAPTER_IDENTITY_SCOPE,
        "body_heading_observation": {
            "segment_id": segments[0]["segment_id"],
            "text": heading,
            "declared_number": observed_number,
        },
        "segments": [
            {"segment_id": segment["segment_id"], "text": segment["normalized_text"]}
            for segment in segments
        ],
    }


def find_bound_chapter_identity_review(
    catalog: Catalog,
    review_ids: Iterable[str],
    chapter_id: str,
) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for review_id in review_ids:
        review = catalog.get("CollectionReview", review_id)
        collector = catalog.get("CollectionDecision", review["collector_decision_id"])
        if collector.get("task") == "CHAPTER_IDENTITY" and collector.get("subject_ids") == [chapter_id]:
            matches.append(review)
    if len(matches) != 1:
        raise ValidationError(
            "E-CHAPTER-IDENTITY-BIND",
            f"{chapter_id} requires exactly one bound CHAPTER_IDENTITY review, found {len(matches)}",
        )
    return matches[0]


def validate_bound_chapter_identity_review(
    catalog: Catalog,
    store: ArtifactStore,
    chapter: dict[str, Any],
    segments: list[dict[str, Any]],
    review: dict[str, Any],
) -> None:
    collector = catalog.get("CollectionDecision", review["collector_decision_id"])
    reviewer = catalog.get("CollectionDecision", review["reviewer_decision_id"])
    chapter_id = chapter["chapter_id"]
    if (
        collector.get("task") != "CHAPTER_IDENTITY"
        or reviewer.get("task") != "CHAPTER_IDENTITY"
        or collector.get("subject_ids") != [chapter_id]
        or reviewer.get("subject_ids") != [chapter_id]
        or review.get("requires_adjudication") is not False
        or review.get("conservative_outcome") != {"identity_status": "MATCH"}
    ):
        raise ValidationError(
            "E-CHAPTER-IDENTITY-BIND",
            f"{review['review_id']} is not a resolved identity review for {chapter_id}",
        )
    input_ids = collector.get("input_artifact_ids")
    content_input_ids = (
        [artifact_id for artifact_id in input_ids if artifact_id != collector.get("rubric_artifact_id")]
        if isinstance(input_ids, list)
        else []
    )
    if (
        input_ids != reviewer.get("input_artifact_ids")
        or collector.get("rubric_artifact_id") != reviewer.get("rubric_artifact_id")
        or len(content_input_ids) != 1
    ):
        raise ValidationError(
            "E-CHAPTER-IDENTITY-BIND",
            f"{review['review_id']} does not bind one shared identity input",
        )
    raw = store.get(content_input_ids[0])
    try:
        stored_input = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(
            "E-CHAPTER-IDENTITY-BIND",
            f"{review['review_id']} identity input is not JSON",
        ) from exc
    expected_input = chapter_identity_review_input(chapter, segments)
    if raw != canonical_dumps(stored_input) or stored_input != expected_input:
        raise ValidationError(
            "E-CHAPTER-IDENTITY-BIND",
            f"{review['review_id']} identity input differs from the frozen chapter body",
        )


def reviewed_triage_assessment(
    catalog: Catalog,
    retrieval: dict[str, Any],
    review: dict[str, Any],
    *,
    rights: dict[str, Any],
    source_quality: dict[str, str] | None = None,
    policy_hash: str,
    assessed_at: str,
) -> dict[str, Any]:
    collector = catalog.get("CollectionDecision", review["collector_decision_id"])
    reviewer = catalog.get("CollectionDecision", review["reviewer_decision_id"])
    retrieval_id = retrieval["retrieval_id"]
    if (
        collector.get("task") != "TRIAGE"
        or reviewer.get("task") != "TRIAGE"
        or collector.get("subject_ids") != [retrieval_id]
        or reviewer.get("subject_ids") != [retrieval_id]
        or review.get("requires_adjudication") is not False
    ):
        raise ValidationError(
            "E-NOVEL-TRIAGE-BIND",
            f"{review['review_id']} is not a resolved TRIAGE review for {retrieval_id}",
        )
    outcome = review.get("conservative_outcome") or {}
    if outcome.get("disposition") != "SELECTED" or outcome.get("tier") not in {"A", "B", "C", "D"}:
        raise ValidationError(
            "E-NOVEL-TRIAGE",
            f"{review['review_id']} did not select {retrieval_id}",
        )
    tier = outcome["tier"]
    allowed_uses = (
        ["event-facts"]
        if tier in {"A", "B"}
        else ["reception"]
        if tier == "C"
        else ["lead-only"]
    )
    record = {
        "schema_version": SCHEMA_VERSION,
        "retrieval_id": retrieval_id,
        "tier": tier,
        "technical_access": novel_technical_access(catalog, retrieval),
        "rights": copy.deepcopy(rights),
        "source_quality": copy.deepcopy(
            source_quality
            or {"edition_status": "UNKNOWN", "textual_completeness": "UNKNOWN"}
        ),
        "allowed_uses": allowed_uses,
        "selection_decision": "selected",
        "decision_reason": f"materialized from independent TRIAGE review {review['review_id']}",
        "assessor_build_id": NOVEL_TRIAGE_MATERIALIZER_BUILD_ID,
        "policy_hash": policy_hash,
        "assessed_at": assessed_at,
    }
    return {**record, "assessment_id": derived_id("TriageAssessment", record)}
