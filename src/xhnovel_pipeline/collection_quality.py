from __future__ import annotations

import pathlib
from typing import Any, Protocol

from .catalog import Catalog
from .canonical import canonical_dumps
from .constants import SCHEMA_VERSION
from .errors import ValidationError
from .hashing import object_hash, sorted_ids
from .ids import derived_id
from .schema import validate_schema
from .store import ArtifactStore

TASK_REQUIRED_FIELDS = {
    "RELEVANCE": {"disposition"},
    "TRIAGE": {"disposition", "tier", "access_legitimacy"},
    "ORIGIN": {"origin_relation"},
    "CHAPTER_IDENTITY": {"chapter_key"},
    "STOP": {"disposition"},
}

TASK_DISPOSITIONS = {
    "RELEVANCE": {"SELECTED", "REJECTED", "LEAD_ONLY"},
    "TRIAGE": {"SELECTED", "REJECTED", "LEAD_ONLY", "QUARANTINED"},
    "CHAPTER_IDENTITY": {"SELECTED", "REJECTED", "QUARANTINED"},
    "STOP": {"CONTINUE", "STOP"},
}

TIER_QUALITY = {"D": 0, "C": 1, "B": 2, "A": 3}


class CollectionAssessor(Protocol):
    build_id: str

    def assess(
        self,
        *,
        task: str,
        subject_ids: list[str],
        artifacts: dict[str, bytes],
    ) -> dict[str, Any]: ...


def decision_input_hash(task: str, subject_ids: list[str], input_artifact_ids: list[str]) -> str:
    return object_hash(
        {
            "task": task,
            "subject_ids": sorted_ids(subject_ids),
            "input_artifact_ids": sorted_ids(input_artifact_ids),
        },
        omit=(),
    )


def make_collection_decision(
    *,
    task: str,
    subject_ids: list[str],
    input_artifact_ids: list[str],
    assessor_role: str,
    assessor_build_id: str,
    output_artifact_id: str,
    outcome: dict[str, Any],
    confidence: str,
    basis: list[str],
    created_at: str,
) -> dict[str, Any]:
    decision = {
        "schema_version": SCHEMA_VERSION,
        "task": task,
        "subject_ids": sorted_ids(subject_ids),
        "input_artifact_ids": sorted_ids(input_artifact_ids),
        "input_manifest_hash": decision_input_hash(task, subject_ids, input_artifact_ids),
        "assessor_role": assessor_role,
        "assessor_build_id": assessor_build_id,
        "output_artifact_id": output_artifact_id,
        "outcome": outcome,
        "confidence": confidence,
        "basis": basis,
        "created_at": created_at,
    }
    decision["decision_id"] = derived_id("CollectionDecision", decision)
    validate_collection_decision_record(decision)
    return decision


def decision_output_bytes(
    *,
    assessor_role: str,
    assessor_build_id: str,
    outcome: dict[str, Any],
    confidence: str,
    basis: list[str],
) -> bytes:
    return canonical_dumps(
        {
            "assessor_role": assessor_role,
            "assessor_build_id": assessor_build_id,
            "outcome": outcome,
            "confidence": confidence,
            "basis": basis,
        }
    )


def _put_artifact(
    catalog: Catalog,
    store: ArtifactStore,
    data: bytes,
    *,
    media_type: str,
    created_at: str,
) -> str:
    artifact_id = store.put(data)
    if not any(item["artifact_id"] == artifact_id for item in catalog.all("Artifact")):
        catalog.add(
            "Artifact",
            {
                "schema_version": SCHEMA_VERSION,
                "artifact_id": artifact_id,
                "media_type": media_type,
                "byte_length": len(data),
                "retention_policy": "retention-v1",
                "durability_status": "LOCAL",
                "created_at": created_at,
            },
        )
    return artifact_id


def _run_assessor(
    assessor: CollectionAssessor,
    *,
    role: str,
    task: str,
    subject_ids: list[str],
    input_artifact_ids: list[str],
    frozen_inputs: dict[str, bytes],
    catalog: Catalog,
    store: ArtifactStore,
    created_at: str,
) -> dict[str, Any]:
    result = assessor.assess(task=task, subject_ids=list(subject_ids), artifacts=dict(frozen_inputs))
    if set(result) != {"outcome", "confidence", "basis"}:
        raise ValidationError(
            "E-ASSESSOR-OUTPUT",
            "assessor output must contain exactly outcome, confidence and basis",
        )
    output_bytes = decision_output_bytes(
        assessor_role=role,
        assessor_build_id=assessor.build_id,
        outcome=result["outcome"],
        confidence=result["confidence"],
        basis=result["basis"],
    )
    output_artifact_id = _put_artifact(
        catalog,
        store,
        output_bytes,
        media_type="application/json",
        created_at=created_at,
    )
    decision = make_collection_decision(
        task=task,
        subject_ids=subject_ids,
        input_artifact_ids=input_artifact_ids,
        assessor_role=role,
        assessor_build_id=assessor.build_id,
        output_artifact_id=output_artifact_id,
        outcome=result["outcome"],
        confidence=result["confidence"],
        basis=result["basis"],
        created_at=created_at,
    )
    catalog.add("CollectionDecision", decision)
    return decision


def run_independent_collection_review(
    catalog: Catalog,
    store: ArtifactStore,
    *,
    task: str,
    subject_ids: list[str],
    input_artifact_ids: list[str],
    collector: CollectionAssessor,
    reviewer: CollectionAssessor,
    rubric_id: str,
    rubric_path: pathlib.Path,
    created_at: str,
) -> dict[str, Any]:
    if collector.build_id == reviewer.build_id:
        raise ValidationError("E-REVIEW-INDEPENDENCE", "collector and reviewer builds must differ")
    rubric_bytes = rubric_path.read_bytes()
    frozen_inputs: dict[str, bytes] = {}
    for artifact_id in sorted_ids(input_artifact_ids):
        catalog.get("Artifact", artifact_id)
        frozen_inputs[artifact_id] = store.get(artifact_id)

    collector_decision = _run_assessor(
        collector,
        role="COLLECTOR",
        task=task,
        subject_ids=subject_ids,
        input_artifact_ids=input_artifact_ids,
        frozen_inputs=frozen_inputs,
        catalog=catalog,
        store=store,
        created_at=created_at,
    )
    reviewer_decision = _run_assessor(
        reviewer,
        role="REVIEWER",
        task=task,
        subject_ids=subject_ids,
        input_artifact_ids=input_artifact_ids,
        frozen_inputs=frozen_inputs,
        catalog=catalog,
        store=store,
        created_at=created_at,
    )
    rubric_artifact_id = _put_artifact(
        catalog,
        store,
        rubric_bytes,
        media_type="application/yaml",
        created_at=created_at,
    )
    review = compare_collection_decisions(
        collector_decision,
        reviewer_decision,
        rubric_id=rubric_id,
        rubric_artifact_id=rubric_artifact_id,
        reviewed_at=created_at,
    )
    catalog.add("CollectionReview", review)
    return review


def collection_review_gate(
    catalog: Catalog,
    store: ArtifactStore,
    required_collector_decision_ids: list[str],
) -> dict[str, Any]:
    validate_collection_quality_records(catalog, store)
    required = sorted_ids(required_collector_decision_ids)
    missing: list[str] = []
    duplicate_reviews: list[str] = []
    unresolved: list[str] = []
    reviews = catalog.all("CollectionReview")
    for decision_id in required:
        decision = catalog.get("CollectionDecision", decision_id)
        if decision["assessor_role"] != "COLLECTOR":
            raise ValidationError("E-REVIEW-ROLE", f"{decision_id} is not a collector decision")
        matches = [review for review in reviews if review["collector_decision_id"] == decision_id]
        if not matches:
            missing.append(decision_id)
        elif len(matches) > 1:
            duplicate_reviews.append(decision_id)
        elif matches[0]["requires_adjudication"]:
            unresolved.append(matches[0]["review_id"])
    result = (
        "PASS"
        if required and not missing and not duplicate_reviews and not unresolved
        else "INCONCLUSIVE"
    )
    return {
        "result": result,
        "required_count": len(required),
        "reviewed_count": len(required) - len(missing) - len(duplicate_reviews),
        "missing_decision_ids": missing,
        "duplicate_review_decision_ids": duplicate_reviews,
        "unresolved_review_ids": unresolved,
    }


def _conservative_outcome(task: str, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    if task == "RELEVANCE":
        return {"disposition": "LEAD_ONLY"}
    if task == "ORIGIN":
        return {"origin_relation": "UNKNOWN"}
    if task == "CHAPTER_IDENTITY":
        return {"disposition": "QUARANTINED"}
    if task == "STOP":
        return {"disposition": "CONTINUE"}
    if task == "TRIAGE":
        left_tier = left.get("tier", "D")
        right_tier = right.get("tier", "D")
        tier = min((left_tier, right_tier), key=TIER_QUALITY.__getitem__)
        return {
            "disposition": left.get("disposition")
            if left.get("disposition") == right.get("disposition")
            else "LEAD_ONLY",
            "tier": tier,
            "access_legitimacy": left.get("access_legitimacy")
            if left.get("access_legitimacy") == right.get("access_legitimacy")
            else "UNKNOWN",
        }
    raise ValidationError("E-COLLECTION-REVIEW", f"unsupported collection decision task {task!r}")


def compare_collection_decisions(
    collector: dict[str, Any],
    reviewer: dict[str, Any],
    *,
    rubric_id: str,
    rubric_artifact_id: str,
    reviewed_at: str,
) -> dict[str, Any]:
    validate_collection_decision_record(collector)
    validate_collection_decision_record(reviewer)
    if collector.get("assessor_role") != "COLLECTOR" or reviewer.get("assessor_role") != "REVIEWER":
        raise ValidationError("E-REVIEW-ROLE", "review pair must be COLLECTOR then REVIEWER")
    if collector.get("assessor_build_id") == reviewer.get("assessor_build_id"):
        raise ValidationError("E-REVIEW-INDEPENDENCE", "collector and reviewer builds must differ")
    for field in ("task", "subject_ids", "input_artifact_ids", "input_manifest_hash"):
        if collector.get(field) != reviewer.get(field):
            raise ValidationError("E-REVIEW-INPUT", f"independent review differs on {field}")
    if collector.get("output_artifact_id") == reviewer.get("output_artifact_id"):
        raise ValidationError("E-REVIEW-INDEPENDENCE", "collector and reviewer outputs must differ")
    if collector.get("output_artifact_id") in set(reviewer.get("input_artifact_ids") or []):
        raise ValidationError("E-REVIEW-BLIND", "reviewer input includes collector output")

    left = collector["outcome"]
    right = reviewer["outcome"]
    disagreements = sorted(key for key in set(left) | set(right) if left.get(key) != right.get(key))
    agreed = not disagreements
    review = {
        "schema_version": SCHEMA_VERSION,
        "collector_decision_id": collector["decision_id"],
        "reviewer_decision_id": reviewer["decision_id"],
        "review_mode": "INDEPENDENT_BLIND",
        "rubric_id": rubric_id,
        "rubric_artifact_id": rubric_artifact_id,
        "verdict": "AGREE" if agreed else "ESCALATED",
        "disagreements": disagreements,
        "conservative_outcome": left if agreed else _conservative_outcome(collector["task"], left, right),
        "requires_adjudication": not agreed,
        "reviewed_at": reviewed_at,
    }
    review["review_id"] = derived_id("CollectionReview", review)
    validate_schema("CollectionReview", review)
    return review


def _validate_task_outcome(decision: dict[str, Any]) -> None:
    task = decision["task"]
    outcome = decision["outcome"]
    missing = TASK_REQUIRED_FIELDS[task] - set(outcome)
    if missing:
        raise ValidationError("E-DECISION-OUTCOME", f"{decision['decision_id']} missing {sorted(missing)}")
    allowed_dispositions = TASK_DISPOSITIONS.get(task)
    if allowed_dispositions and outcome.get("disposition") not in allowed_dispositions:
        raise ValidationError(
            "E-DECISION-OUTCOME",
            f"{decision['decision_id']} invalid {task} disposition {outcome.get('disposition')!r}",
        )


def validate_collection_decision_record(decision: dict[str, Any]) -> None:
    validate_schema("CollectionDecision", decision)
    _validate_task_outcome(decision)
    expected_input_hash = decision_input_hash(
        decision["task"], decision["subject_ids"], decision["input_artifact_ids"]
    )
    if decision["input_manifest_hash"] != expected_input_hash:
        raise ValidationError("E-DECISION-INPUT", f"{decision['decision_id']} input hash mismatch")
    identity = {key: value for key, value in decision.items() if key != "decision_id"}
    if decision["decision_id"] != derived_id("CollectionDecision", identity):
        raise ValidationError("E-ID-BIND", f"{decision['decision_id']} does not match decision content")


def validate_collection_quality_records(catalog: Catalog, store: ArtifactStore | None = None) -> None:
    for decision in catalog.all("CollectionDecision"):
        validate_collection_decision_record(decision)
        for artifact_id in [*decision["input_artifact_ids"], decision["output_artifact_id"]]:
            catalog.get("Artifact", artifact_id)
            if store:
                store.verify(artifact_id)
        if store:
            expected_output = decision_output_bytes(
                assessor_role=decision["assessor_role"],
                assessor_build_id=decision["assessor_build_id"],
                outcome=decision["outcome"],
                confidence=decision["confidence"],
                basis=decision["basis"],
            )
            if store.get(decision["output_artifact_id"]) != expected_output:
                raise ValidationError(
                    "E-DECISION-OUTPUT",
                    f"{decision['decision_id']} normalized output artifact does not match decision",
                )

    for review in catalog.all("CollectionReview"):
        collector = catalog.get("CollectionDecision", review["collector_decision_id"])
        reviewer = catalog.get("CollectionDecision", review["reviewer_decision_id"])
        catalog.get("Artifact", review["rubric_artifact_id"])
        if store:
            store.verify(review["rubric_artifact_id"])
        expected = compare_collection_decisions(
            collector,
            reviewer,
            rubric_id=review["rubric_id"],
            rubric_artifact_id=review["rubric_artifact_id"],
            reviewed_at=review["reviewed_at"],
        )
        if review != expected:
            raise ValidationError("E-REVIEW-BIND", f"{review['review_id']} differs from deterministic comparison")
