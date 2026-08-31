from __future__ import annotations

import pytest

from xhnovel_pipeline.runtime import TEST_NOW as NOW
from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.ids import derived_id
from xhnovel_pipeline.novel_ingest import run_novel_ingestion
from xhnovel_pipeline.novel_workflow import prepare_novel_evidence_bundle
from xhnovel_pipeline.paths import repo_root
from xhnovel_pipeline.validate import bundle_hash, validate_evidence


def _direct_spec(source_path) -> dict:
    return {
        "source": {
            "kind": "directory",
            "path": str(source_path),
            "title": "测试小说",
        },
        "evidence": {"tier": "A", "access_legitimacy": "AUTHORIZED"},
        "strict_order": False,
    }


def _write_chapter(source_path) -> None:
    source_path.mkdir()
    (source_path / "001.txt").write_text(
        "第一章 入山\n\n林舟进入山门。",
        encoding="utf-8",
    )


def test_ingestion_does_not_materialize_self_declared_evidence(tmp_path):
    source_path = tmp_path / "chapters"
    _write_chapter(source_path)

    result = run_novel_ingestion(
        _direct_spec(source_path),
        tmp_path / "run",
        repo_root=repo_root(),
        now=NOW,
    )

    triage_assessments = result["catalog"].all("TriageAssessment")
    retrieval_triage_links = [
        retrieval.get("triage_assessment_id")
        for retrieval in result["catalog"].all("Retrieval")
        if retrieval.get("triage_assessment_id") is not None
    ]
    assert (triage_assessments, retrieval_triage_links) == ([], [])


class _ReviewAssessor:
    def __init__(self, build_id: str) -> None:
        self.build_id = build_id
        self.tasks: list[str] = []

    def assess(self, *, task, subject_ids, artifacts):
        self.tasks.append(task)
        if task == "TRIAGE":
            return {
                "outcome": {
                    "disposition": "SELECTED",
                    "tier": "B",
                    "access_legitimacy": "AUTHORIZED",
                },
                "confidence": "HIGH",
                "basis": ["the frozen chapter and source metadata were reviewed"],
            }
        if task == "CHAPTER_IDENTITY":
            return {
                "outcome": {"identity_status": "MATCH"},
                "confidence": "HIGH",
                "basis": ["the frozen chapter identity was independently reviewed"],
            }
        raise AssertionError(f"unexpected collection task {task}")


def _prepare_source_assessment(tmp_path):
    source_path = tmp_path / "chapters"
    _write_chapter(source_path)
    spec = _direct_spec(source_path)
    ingestion = run_novel_ingestion(
        spec,
        tmp_path / "run",
        repo_root=repo_root(),
        now=NOW,
    )
    collector = _ReviewAssessor("collector-source-assessment-v1")
    reviewer = _ReviewAssessor("reviewer-source-assessment-v1")

    snapshot, bundle = prepare_novel_evidence_bundle(
        ingestion["catalog"],
        ingestion["store"],
        ingestion["ingestion"],
        spec,
        collector=collector,
        reviewer=reviewer,
        repo_root=repo_root(),
        now=NOW,
    )
    return ingestion, snapshot, bundle, collector, reviewer


def test_workflow_triage_review_not_spec_determines_tier(tmp_path):
    ingestion, snapshot, bundle, collector, reviewer = _prepare_source_assessment(tmp_path)

    assert collector.tasks.count("TRIAGE") == 1
    assert reviewer.tasks.count("TRIAGE") == 1
    retrieval = ingestion["catalog"].all("Retrieval")[0]
    triage = ingestion["catalog"].get("TriageAssessment", retrieval["triage_assessment_id"])
    assert triage["tier"] == "B"
    assert triage["access_legitimacy"] == "UNKNOWN"
    assert triage["assessment_id"] in bundle["triage_assessment_ids"]
    triage_review_ids = [
        review["review_id"]
        for review in ingestion["catalog"].all("CollectionReview")
        if ingestion["catalog"].get(
            "CollectionDecision", review["collector_decision_id"]
        )["task"]
        == "TRIAGE"
    ]
    assert len(triage_review_ids) == 1
    assert triage_review_ids[0] in snapshot["collection_review_ids"]
    assert triage_review_ids[0] in bundle["selection_manifest"]["collection_review_ids"]


def test_workflow_successful_site_fetch_caps_reviewed_access_at_public(tmp_path):
    spec = {
        "source": {
            "kind": "site",
            "index_url": "https://novel.example/index",
            "chapter_url_pattern": r"/chapter/\d+$",
            "title": "测试小说",
        },
        "evidence": {"tier": "A", "access_legitimacy": "AUTHORIZED"},
        "strict_order": False,
    }

    class _Fetcher:
        def fetch(self, url):
            if url.endswith("/index"):
                return b'<a href="/chapter/1">First</a>', "text/html", 200, url
            if url.endswith("/chapter/1"):
                return "<p>第一章正文。</p>".encode(), "text/html", 200, url
            raise AssertionError(f"unexpected URL {url}")

    ingestion = run_novel_ingestion(
        spec,
        tmp_path / "run",
        repo_root=repo_root(),
        fetcher=_Fetcher(),
        now=NOW,
    )
    prepare_novel_evidence_bundle(
        ingestion["catalog"],
        ingestion["store"],
        ingestion["ingestion"],
        spec,
        collector=_ReviewAssessor("collector-site-assessment-v1"),
        reviewer=_ReviewAssessor("reviewer-site-assessment-v1"),
        repo_root=repo_root(),
        now=NOW,
    )

    triage = ingestion["catalog"].all("TriageAssessment")[0]
    assert triage["tier"] == "B"
    assert triage["access_legitimacy"] == "PUBLIC"


@pytest.mark.parametrize(
    ("field", "tampered_value"),
    [("tier", "A"), ("access_legitimacy", "AUTHORIZED")],
)
def test_validator_rejects_triage_that_diverges_from_bound_review(
    tmp_path, field, tampered_value
):
    ingestion, _, bundle, _, _ = _prepare_source_assessment(tmp_path)
    catalog = ingestion["catalog"]
    retrieval = catalog.all("Retrieval")[0]
    triage = catalog.get("TriageAssessment", retrieval["triage_assessment_id"])
    triage[field] = tampered_value
    bundle["bundle_hash"] = bundle_hash(catalog, bundle)
    bundle["bundle_id"] = derived_id(
        "EvidenceBundle", {"bundle_hash": bundle["bundle_hash"]}
    )

    with pytest.raises(ValidationError) as exc:
        validate_evidence(catalog, ingestion["store"])
    assert exc.value.code == "E-NOVEL-TRIAGE-BIND"


def test_validator_rejects_triage_review_omitted_from_snapshot_and_bundle(tmp_path):
    ingestion, snapshot, bundle, _, _ = _prepare_source_assessment(tmp_path)
    catalog = ingestion["catalog"]
    triage_review_id = next(
        review["review_id"]
        for review in catalog.all("CollectionReview")
        if catalog.get("CollectionDecision", review["collector_decision_id"])["task"]
        == "TRIAGE"
    )
    snapshot["collection_review_ids"].remove(triage_review_id)
    bundle["selection_manifest"]["collection_review_ids"].remove(triage_review_id)
    bundle["bundle_hash"] = bundle_hash(catalog, bundle)
    bundle["bundle_id"] = derived_id(
        "EvidenceBundle", {"bundle_hash": bundle["bundle_hash"]}
    )

    with pytest.raises(ValidationError) as exc:
        validate_evidence(catalog, ingestion["store"])
    assert exc.value.code == "E-NOVEL-TRIAGE-BIND"
