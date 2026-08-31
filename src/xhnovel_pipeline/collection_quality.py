from __future__ import annotations

import json
import copy
import pathlib
from typing import Any, Protocol

from .catalog import Catalog
from .canonical import canonical_dumps
from .constants import SCHEMA_VERSION
from .errors import ValidationError
from .hashing import collection_snapshot_hash, object_hash, sorted_ids
from .ids import derived_id
from .schema import validate_schema
from .store import ArtifactStore

TASK_REQUIRED_FIELDS = {
    "RELEVANCE": {"disposition"},
    "TRIAGE": {"disposition", "tier", "access_legitimacy"},
    "ORIGIN": {"origin_relation"},
    "CHAPTER_IDENTITY": {"identity_status"},
    "STOP": {"disposition"},
}

TASK_DISPOSITIONS = {
    "RELEVANCE": {"SELECTED", "REJECTED", "LEAD_ONLY"},
    "TRIAGE": {"SELECTED", "REJECTED", "LEAD_ONLY", "QUARANTINED"},
    "STOP": {"CONTINUE", "STOP"},
}

TIER_QUALITY = {"D": 0, "C": 1, "B": 2, "A": 3}
CHAPTER_IDENTITY_STATUSES = {"MATCH", "MISMATCH", "UNKNOWN", "QUARANTINED"}


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
    model_request_artifact_id: str | None = None,
    provider_response_artifact_id: str | None = None,
    assessor_model: str | None = None,
    assessor_parameters: dict[str, Any] | None = None,
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
    if model_request_artifact_id is not None:
        decision["model_request_artifact_id"] = model_request_artifact_id
    if provider_response_artifact_id is not None:
        decision["provider_response_artifact_id"] = provider_response_artifact_id
    if assessor_model is not None:
        decision["assessor_model"] = assessor_model
    if assessor_parameters is not None:
        decision["assessor_parameters"] = assessor_parameters
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
    request_bytes = getattr(assessor, "last_request_bytes", None)
    response_bytes = getattr(assessor, "last_response_bytes", None)
    request_artifact_id = (
        _put_artifact(
            catalog,
            store,
            request_bytes,
            media_type="application/json",
            created_at=created_at,
        )
        if isinstance(request_bytes, bytes)
        else None
    )
    response_artifact_id = (
        _put_artifact(
            catalog,
            store,
            response_bytes,
            media_type="application/json",
            created_at=created_at,
        )
        if isinstance(response_bytes, bytes)
        else None
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
        model_request_artifact_id=request_artifact_id,
        provider_response_artifact_id=response_artifact_id,
        assessor_model=getattr(assessor, "model", None),
        assessor_parameters=copy.deepcopy(getattr(assessor, "build_parameters", None)),
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
    collector_model = getattr(collector, "assessor_model", None) or getattr(collector, "model", None)
    reviewer_model = getattr(reviewer, "assessor_model", None) or getattr(reviewer, "model", None)
    if collector_model is not None and reviewer_model is not None and collector_model == reviewer_model:
        raise ValidationError("E-REVIEW-INDEPENDENCE", "collector and reviewer models must differ")
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


def bind_collection_quality_snapshot(
    catalog: Catalog,
    store: ArtifactStore,
    snapshot: dict[str, Any],
    *,
    required_collector_decision_ids: list[str],
    quality_policy_artifact_id: str,
    frozen_at: str,
) -> dict[str, Any]:
    if snapshot.get("status") != "FROZEN":
        raise ValidationError("E-FROZEN", "collection quality can bind only a frozen snapshot")
    catalog.get("Artifact", quality_policy_artifact_id)
    store.verify(quality_policy_artifact_id)
    gate = collection_review_gate(catalog, store, required_collector_decision_ids)
    if gate["result"] != "PASS":
        raise ValidationError("E-REVIEW-GATE", "collection quality review is incomplete or unresolved")
    review_ids = []
    artifact_ids = set(snapshot["artifact_ids"])
    decision_ids = set()
    for collector_id in sorted_ids(required_collector_decision_ids):
        collector = catalog.get("CollectionDecision", collector_id)
        matches = [
            review
            for review in catalog.all("CollectionReview")
            if review["collector_decision_id"] == collector_id
        ]
        review = matches[0]
        reviewer = catalog.get("CollectionDecision", review["reviewer_decision_id"])
        decision_ids.update((collector_id, reviewer["decision_id"]))
        review_ids.append(review["review_id"])
        artifact_ids.update(collector["input_artifact_ids"])
        for decision in (collector, reviewer):
            artifact_ids.add(decision["output_artifact_id"])
            for field in ("model_request_artifact_id", "provider_response_artifact_id"):
                if decision.get(field):
                    artifact_ids.add(decision[field])
        artifact_ids.add(review["rubric_artifact_id"])
    artifact_ids.add(quality_policy_artifact_id)
    bound = copy.deepcopy(snapshot)
    bound.update(
        {
            "snapshot_id": "SNP-PENDING",
            "artifact_ids": sorted_ids(artifact_ids),
            "collection_decision_ids": sorted_ids(decision_ids),
            "collection_review_ids": sorted_ids(review_ids),
            "quality_policy_artifact_id": quality_policy_artifact_id,
            "quality_gate": gate,
            "snapshot_hash": "sha256:" + "0" * 64,
            "frozen_at": frozen_at,
            "supersedes": snapshot["snapshot_id"],
            "status": "FROZEN",
        }
    )
    bound["snapshot_hash"] = collection_snapshot_hash(bound)
    bound["snapshot_id"] = derived_id("CollectionSnapshot", {"snapshot_hash": bound["snapshot_hash"]})
    validate_schema("CollectionSnapshot", bound)
    catalog.add("CollectionSnapshot", bound)
    return bound


def _conservative_outcome(task: str, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    if task == "RELEVANCE":
        return {"disposition": "LEAD_ONLY"}
    if task == "ORIGIN":
        return {"origin_relation": "UNKNOWN"}
    if task == "CHAPTER_IDENTITY":
        return {"identity_status": "QUARANTINED"}
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
    collector_model = collector.get("assessor_model")
    reviewer_model = reviewer.get("assessor_model")
    if collector_model is not None and reviewer_model is not None and collector_model == reviewer_model:
        raise ValidationError("E-REVIEW-INDEPENDENCE", "collector and reviewer models must differ")
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
    expected_fields = TASK_REQUIRED_FIELDS[task]
    if set(outcome) != expected_fields:
        raise ValidationError(
            "E-DECISION-OUTCOME",
            f"{decision['decision_id']} {task} outcome fields must be exactly "
            f"{sorted(expected_fields)}",
        )
    allowed_dispositions = TASK_DISPOSITIONS.get(task)
    if (
        allowed_dispositions
        and "disposition" in outcome
        and outcome.get("disposition") not in allowed_dispositions
    ):
        raise ValidationError(
            "E-DECISION-OUTCOME",
            f"{decision['decision_id']} invalid {task} disposition {outcome.get('disposition')!r}",
        )
    if (
        task == "CHAPTER_IDENTITY"
        and outcome.get("identity_status") not in CHAPTER_IDENTITY_STATUSES
    ):
        raise ValidationError(
            "E-DECISION-OUTCOME",
            f"{decision['decision_id']} invalid identity status {outcome.get('identity_status')!r}",
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
        for field in ("model_request_artifact_id", "provider_response_artifact_id"):
            artifact_id = decision.get(field)
            if artifact_id:
                catalog.get("Artifact", artifact_id)
                if store:
                    store.verify(artifact_id)
        has_model_request = bool(decision.get("model_request_artifact_id"))
        has_model_response = bool(decision.get("provider_response_artifact_id"))
        if has_model_request != has_model_response:
            raise ValidationError(
                "E-DECISION-MODEL-REQUEST",
                f"{decision['decision_id']} model request/response pair is incomplete",
            )
        if store and has_model_request:
            from .model_api import _response_output_text, model_build_id
            from .model_collection import COLLECTION_SYSTEM_PROMPT, TASK_SCHEMAS

            try:
                request = json.loads(store.get(decision["model_request_artifact_id"]).decode("utf-8"))
                input_value = json.loads(request["input"])
                response = json.loads(
                    store.get(decision["provider_response_artifact_id"]).decode("utf-8")
                )
                response_value = json.loads(_response_output_text(response))
            except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
                raise ValidationError(
                    "E-DECISION-MODEL-REQUEST",
                    f"{decision['decision_id']} model request artifact is invalid",
                ) from exc
            expected_artifacts = []
            for artifact_id in sorted(decision["input_artifact_ids"]):
                try:
                    text = store.get(artifact_id).decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ValidationError(
                        "E-DECISION-MODEL-REQUEST",
                        f"{decision['decision_id']} input artifact is not UTF-8",
                    ) from exc
                expected_artifacts.append(
                    {"artifact_id": artifact_id, "encoding": "utf-8", "untrusted_text": text}
                )
            expected_input = {
                "assessor_role": decision["assessor_role"],
                "task": decision["task"],
                "subject_ids": decision["subject_ids"],
                "input_artifact_ids": sorted(decision["input_artifact_ids"]),
                "artifacts": expected_artifacts,
            }
            parameters = decision.get("assessor_parameters")
            if (
                input_value != expected_input
                or response_value
                != {
                    "outcome": decision["outcome"],
                    "confidence": decision["confidence"],
                    "basis": decision["basis"],
                }
                or not isinstance(parameters, dict)
                or set(parameters) != {"endpoint", "max_input_chars", "structured_output"}
                or parameters.get("structured_output") is not True
                or not isinstance(parameters.get("endpoint"), str)
                or not parameters["endpoint"].startswith("https://")
                or not isinstance(parameters.get("max_input_chars"), int)
                or parameters["max_input_chars"] < sum(len(item["untrusted_text"]) for item in expected_artifacts)
                or request.get("model") != decision.get("assessor_model")
                or request.get("instructions") != COLLECTION_SYSTEM_PROMPT
                or request.get("store") is not False
                or request.get("text", {}).get("format")
                != {
                    "type": "json_schema",
                    "name": f"collection_{decision['task'].casefold()}",
                    "strict": True,
                    "schema": TASK_SCHEMAS[decision["task"]],
                }
            ):
                raise ValidationError(
                    "E-DECISION-MODEL-REQUEST",
                    f"{decision['decision_id']} stored model exchange does not match decision",
                )
            expected_build_id = model_build_id(
                purpose=decision["assessor_role"].casefold(),
                model=decision["assessor_model"],
                instructions=COLLECTION_SYSTEM_PROMPT,
                parameters=parameters,
            )
            if decision["assessor_build_id"] != expected_build_id:
                raise ValidationError(
                    "E-DECISION-MODEL-REQUEST",
                    f"{decision['decision_id']} assessor build identity does not match",
                )
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
