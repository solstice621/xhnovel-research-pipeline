from __future__ import annotations

import json
from typing import Any, Iterable

from .canonical import canonical_dumps
from .catalog import Catalog
from .constants import SCHEMA_VERSION
from .errors import ValidationError
from .ids import derived_id
from .novel_adapters import chapter_number
from .store import ArtifactStore


NOVEL_TRIAGE_MATERIALIZER_BUILD_ID = "novel-triage-review-materializer-v1"
CHAPTER_IDENTITY_SCOPE = "DISCOVERY_ORDER_VS_BODY_HEADING_V1"
_LOCAL_NOVEL_PLATFORMS = {"novel:txt", "novel:epub", "novel:directory"}


def novel_access_legitimacy(catalog: Catalog, retrieval: dict[str, Any]) -> str:
    source = catalog.get("Source", retrieval["source_id"])
    platform_id = str(source.get("platform_id") or "").casefold()
    if platform_id == "novel:site":
        if retrieval.get("http_status") != 200 or retrieval.get("status") != "FETCHED":
            raise ValidationError(
                "E-NOVEL-TRIAGE-BIND",
                f"{retrieval['retrieval_id']} cannot claim PUBLIC access without a successful anonymous fetch",
            )
        return "PUBLIC"
    if platform_id in _LOCAL_NOVEL_PLATFORMS:
        return "UNKNOWN"
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
    if input_ids != reviewer.get("input_artifact_ids") or not isinstance(input_ids, list) or len(input_ids) != 1:
        raise ValidationError(
            "E-CHAPTER-IDENTITY-BIND",
            f"{review['review_id']} does not bind one shared identity input",
        )
    raw = store.get(input_ids[0])
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
        "access_legitimacy": novel_access_legitimacy(catalog, retrieval),
        "allowed_uses": allowed_uses,
        "selection_decision": "selected",
        "decision_reason": f"materialized from independent TRIAGE review {review['review_id']}",
        "assessor_build_id": NOVEL_TRIAGE_MATERIALIZER_BUILD_ID,
        "policy_hash": policy_hash,
        "assessed_at": assessed_at,
    }
    return {**record, "assessment_id": derived_id("TriageAssessment", record)}
