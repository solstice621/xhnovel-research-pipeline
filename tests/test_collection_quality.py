from __future__ import annotations

import pytest

from xhnovel_pipeline.catalog import Catalog
from xhnovel_pipeline.collection import run_collection
from xhnovel_pipeline.collection_quality import (
    collection_review_gate,
    compare_collection_decisions,
    decision_output_bytes,
    make_collection_decision,
    run_independent_collection_review,
)
from xhnovel_pipeline.constants import SCHEMA_VERSION
from xhnovel_pipeline.engine import NOW
from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.ids import derived_id
from xhnovel_pipeline.paths import repo_root
from xhnovel_pipeline.store import ArtifactStore
from xhnovel_pipeline.validate import validate_collection


def _add_artifact(catalog: Catalog, store: ArtifactStore, data: bytes) -> str:
    artifact_id = store.put(data)
    catalog.add(
        "Artifact",
        {
            "schema_version": SCHEMA_VERSION,
            "artifact_id": artifact_id,
            "media_type": "application/json",
            "byte_length": len(data),
            "retention_policy": "retention-v1",
            "durability_status": "LOCAL",
            "created_at": NOW,
        },
    )
    return artifact_id


def _decision_pair(tmp_path, collector_outcome, reviewer_outcome):
    catalog = Catalog()
    store = ArtifactStore(tmp_path / "objects")
    input_id = _add_artifact(catalog, store, b'{"title":"candidate"}')
    confidence = "HIGH"
    basis = ["classified from the frozen page artifact"]
    collector_output_id = _add_artifact(
        catalog,
        store,
        decision_output_bytes(
            assessor_role="COLLECTOR",
            assessor_build_id="small-model-v1",
            outcome=collector_outcome,
            confidence=confidence,
            basis=basis,
        ),
    )
    reviewer_output_id = _add_artifact(
        catalog,
        store,
        decision_output_bytes(
            assessor_role="REVIEWER",
            assessor_build_id="large-model-v1",
            outcome=reviewer_outcome,
            confidence=confidence,
            basis=basis,
        ),
    )
    common = {
        "task": "TRIAGE",
        "subject_ids": ["RET-CANDIDATE"],
        "input_artifact_ids": [input_id],
        "confidence": confidence,
        "basis": basis,
        "created_at": NOW,
    }
    collector = make_collection_decision(
        **common,
        assessor_role="COLLECTOR",
        assessor_build_id="small-model-v1",
        output_artifact_id=collector_output_id,
        outcome=collector_outcome,
    )
    reviewer = make_collection_decision(
        **common,
        assessor_role="REVIEWER",
        assessor_build_id="large-model-v1",
        output_artifact_id=reviewer_output_id,
        outcome=reviewer_outcome,
    )
    catalog.add("CollectionDecision", collector)
    catalog.add("CollectionDecision", reviewer)
    rubric = repo_root() / "policies/collection-quality-v1.yaml"
    rubric_artifact_id = _add_artifact(catalog, store, rubric.read_bytes())
    review = compare_collection_decisions(
        collector,
        reviewer,
        rubric_id="collection-quality-v1",
        rubric_artifact_id=rubric_artifact_id,
        reviewed_at=NOW,
    )
    catalog.add("CollectionReview", review)
    return catalog, store, collector, reviewer, review


def test_collection_command_stops_at_frozen_snapshot(tmp_path):
    root = repo_root()
    result = run_collection(root / "fixtures/positive/minimal-local", tmp_path, repo_root=root)

    assert result["snapshot"]["status"] == "FROZEN"
    assert result["collection_report"]["contains_claims"] is False
    for kind in ("EvidenceBundle", "ExtractionRun", "Claim", "EvidenceExport"):
        assert result["catalog"].all(kind) == []
    assert (result["work_dir"] / "snapshot.json").is_file()
    assert (result["work_dir"] / "catalog.json").is_file()
    assert not (tmp_path / "export.json").exists()


def test_independent_review_agreement_is_bound_and_valid(tmp_path):
    outcome = {"disposition": "SELECTED", "tier": "B", "access_legitimacy": "PUBLIC"}
    catalog, store, _, _, review = _decision_pair(tmp_path, outcome, outcome)

    assert review["verdict"] == "AGREE"
    assert review["requires_adjudication"] is False
    assert review["conservative_outcome"] == outcome
    validate_collection(catalog, store)


def test_triage_disagreement_is_conservative_and_escalated(tmp_path):
    collector = {"disposition": "SELECTED", "tier": "A", "access_legitimacy": "AUTHORIZED"}
    reviewer = {"disposition": "REJECTED", "tier": "C", "access_legitimacy": "RESTRICTED"}
    catalog, store, _, _, review = _decision_pair(tmp_path, collector, reviewer)

    assert review["verdict"] == "ESCALATED"
    assert review["requires_adjudication"] is True
    assert review["conservative_outcome"] == {
        "disposition": "LEAD_ONLY",
        "tier": "C",
        "access_legitimacy": "UNKNOWN",
    }
    validate_collection(catalog, store)


def test_same_build_cannot_pose_as_independent_reviewer(tmp_path):
    outcome = {"disposition": "SELECTED", "tier": "B", "access_legitimacy": "PUBLIC"}
    catalog, store, collector, reviewer, _ = _decision_pair(tmp_path, outcome, outcome)
    del catalog, store
    reviewer["assessor_build_id"] = collector["assessor_build_id"]
    reviewer["decision_id"] = derived_id(
        "CollectionDecision", {key: value for key, value in reviewer.items() if key != "decision_id"}
    )

    with pytest.raises(ValidationError) as exc:
        compare_collection_decisions(
            collector,
            reviewer,
            rubric_id="collection-quality-v1",
            rubric_artifact_id="sha256:" + "a" * 64,
            reviewed_at=NOW,
        )
    assert exc.value.code == "E-REVIEW-INDEPENDENCE"


def test_review_cannot_be_changed_after_comparison(tmp_path):
    outcome = {"disposition": "SELECTED", "tier": "B", "access_legitimacy": "PUBLIC"}
    catalog, store, _, _, review = _decision_pair(tmp_path, outcome, outcome)
    review["verdict"] = "ESCALATED"

    with pytest.raises(ValidationError) as exc:
        validate_collection(catalog, store)
    assert exc.value.code == "E-REVIEW-BIND"


def test_decision_output_artifact_must_match_normalized_decision(tmp_path):
    outcome = {"disposition": "SELECTED", "tier": "B", "access_legitimacy": "PUBLIC"}
    catalog, store, collector, _, _ = _decision_pair(tmp_path, outcome, outcome)
    collector["basis"] = ["different unsupported basis"]
    collector["decision_id"] = derived_id(
        "CollectionDecision", {key: value for key, value in collector.items() if key != "decision_id"}
    )

    with pytest.raises(ValidationError) as exc:
        validate_collection(catalog, store)
    assert exc.value.code == "E-DECISION-OUTPUT"


def test_escalated_review_does_not_pass_quality_gate(tmp_path):
    collector = {"disposition": "SELECTED", "tier": "A", "access_legitimacy": "PUBLIC"}
    reviewer = {"disposition": "LEAD_ONLY", "tier": "B", "access_legitimacy": "PUBLIC"}
    catalog, store, collector_decision, _, _ = _decision_pair(tmp_path, collector, reviewer)

    report = collection_review_gate(catalog, store, [collector_decision["decision_id"]])
    assert report["result"] == "INCONCLUSIVE"
    assert report["unresolved_review_ids"]


def test_review_runner_keeps_reviewer_blind_and_passes_complete_gate(tmp_path):
    catalog = Catalog()
    store = ArtifactStore(tmp_path / "objects")
    input_id = _add_artifact(catalog, store, b'{"title":"candidate"}')
    seen_inputs = []

    class StaticAssessor:
        def __init__(self, build_id):
            self.build_id = build_id

        def assess(self, *, task, subject_ids, artifacts):
            seen_inputs.append((self.build_id, task, subject_ids, set(artifacts)))
            return {
                "outcome": {
                    "disposition": "SELECTED",
                    "tier": "B",
                    "access_legitimacy": "PUBLIC",
                },
                "confidence": "HIGH",
                "basis": ["page identifies the requested work"],
            }

    review = run_independent_collection_review(
        catalog,
        store,
        task="TRIAGE",
        subject_ids=["RET-CANDIDATE"],
        input_artifact_ids=[input_id],
        collector=StaticAssessor("small-model-v1"),
        reviewer=StaticAssessor("large-model-v1"),
        rubric_id="collection-quality-v1",
        rubric_path=repo_root() / "policies/collection-quality-v1.yaml",
        created_at=NOW,
    )

    assert seen_inputs == [
        ("small-model-v1", "TRIAGE", ["RET-CANDIDATE"], {input_id}),
        ("large-model-v1", "TRIAGE", ["RET-CANDIDATE"], {input_id}),
    ]
    collector_id = catalog.all("CollectionDecision")[0]["decision_id"]
    assert collection_review_gate(catalog, store, [collector_id])["result"] == "PASS"
    assert review["verdict"] == "AGREE"
    validate_collection(catalog, store)
