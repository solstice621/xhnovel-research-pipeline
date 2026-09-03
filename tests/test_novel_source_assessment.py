from __future__ import annotations

import copy

import pytest

from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.hashing import collection_snapshot_hash
from xhnovel_pipeline.novel_assessment import NOVEL_SOURCE_CLASSIFIER_BUILD_ID
from xhnovel_pipeline.novel_ingest import run_novel_ingestion
from xhnovel_pipeline.novel_workflow import prepare_novel_evidence_bundle
from xhnovel_pipeline.paths import repo_root
from xhnovel_pipeline.runtime import TEST_NOW as NOW
from xhnovel_pipeline.validate import validate_evidence

RIGHTS = {
    "basis": "USER_AUTHORIZED_LOCAL_COPY",
    "may_store_full_text": True,
    "may_send_to_external_model": True,
    "may_export_excerpts": False,
}
SOURCE_QUALITY = {
    "edition_status": "USER_VERIFIED_COPY",
    "textual_completeness": "COMPLETE",
}


def _direct_spec(source_path, *, source_quality=SOURCE_QUALITY) -> dict:
    return {
        "source": {"kind": "directory", "path": str(source_path), "title": "测试小说"},
        "evidence": {"tier": "A", "access_legitimacy": "AUTHORIZED"},
        "rights": copy.deepcopy(RIGHTS),
        "source_quality": copy.deepcopy(source_quality),
        "strict_order": False,
    }


def _write_chapter(source_path) -> None:
    source_path.mkdir()
    (source_path / "001.txt").write_text("第一章 入山\n林舟进入山门。", encoding="utf-8")


def _prepare(tmp_path, *, source_quality=SOURCE_QUALITY):
    source_path = tmp_path / "chapters"
    _write_chapter(source_path)
    spec = _direct_spec(source_path, source_quality=source_quality)
    ingestion = run_novel_ingestion(
        spec, tmp_path / "run", repo_root=repo_root(), now=NOW
    )
    snapshot, bundle = prepare_novel_evidence_bundle(
        ingestion["catalog"],
        ingestion["store"],
        ingestion["ingestion"],
        spec,
        repo_root=repo_root(),
        now=NOW,
    )
    return ingestion, snapshot, bundle


def test_ingestion_alone_does_not_materialize_source_assessment(tmp_path):
    source_path = tmp_path / "chapters"
    _write_chapter(source_path)
    result = run_novel_ingestion(
        _direct_spec(source_path), tmp_path / "run", repo_root=repo_root(), now=NOW
    )
    assert result["catalog"].all("TriageAssessment") == []
    assert all(
        retrieval.get("triage_assessment_id") is None
        for retrieval in result["catalog"].all("Retrieval")
    )


def test_workflow_uses_deterministic_source_classification_without_model_reviews(tmp_path):
    ingestion, snapshot, bundle = _prepare(tmp_path)
    catalog = ingestion["catalog"]
    retrieval = catalog.all("Retrieval")[0]
    triage = catalog.get("TriageAssessment", retrieval["triage_assessment_id"])

    assert triage["tier"] == "B"
    assert triage["allowed_uses"] == ["event-facts"]
    assert triage["technical_access"] == {"method": "LOCAL_FILE", "succeeded": True}
    assert triage["rights"] == RIGHTS
    assert triage["source_quality"] == SOURCE_QUALITY
    assert NOVEL_SOURCE_CLASSIFIER_BUILD_ID == "novel-source-classifier-v2"
    assert triage["assessor_build_id"] == NOVEL_SOURCE_CLASSIFIER_BUILD_ID
    assert triage["assessment_id"] in bundle["triage_assessment_ids"]
    assert catalog.all("CollectionDecision") == []
    assert catalog.all("CollectionReview") == []
    assert "review_completion_gate" not in snapshot


def test_self_declared_legacy_evidence_does_not_override_unknown_source_quality(tmp_path):
    ingestion, _, bundle = _prepare(
        tmp_path,
        source_quality={"edition_status": "UNKNOWN", "textual_completeness": "UNKNOWN"},
    )
    triage = ingestion["catalog"].get(
        "TriageAssessment", bundle["triage_assessment_ids"][0]
    )
    assert triage["tier"] == "D"
    assert triage["allowed_uses"] == ["lead-only"]


def test_http_200_records_technical_access_without_inferring_public_rights(tmp_path):
    spec = {
        "source": {
            "kind": "site",
            "index_url": "https://novel.example/index",
            "chapter_url_pattern": r"/chapter/\d+$",
            "title": "测试小说",
        },
        "rights": copy.deepcopy(RIGHTS),
        "source_quality": copy.deepcopy(SOURCE_QUALITY),
        "strict_order": False,
    }

    class Fetcher:
        def fetch(self, url):
            if url.endswith("/index"):
                return b'<a href="/chapter/1">First</a>', "text/html", 200, url
            if url.endswith("/chapter/1"):
                return "<p>第一章正文。</p>".encode(), "text/html", 200, url
            raise AssertionError(url)

    ingestion = run_novel_ingestion(
        spec,
        tmp_path / "run",
        repo_root=repo_root(),
        fetcher=Fetcher(),
        now=NOW,
    )
    prepare_novel_evidence_bundle(
        ingestion["catalog"],
        ingestion["store"],
        ingestion["ingestion"],
        spec,
        repo_root=repo_root(),
        now=NOW,
    )
    triage = ingestion["catalog"].all("TriageAssessment")[0]
    assert triage["technical_access"] == {"method": "ANONYMOUS_HTTP", "succeeded": True}
    assert triage["rights"] == RIGHTS
    assert "PUBLIC" not in repr(triage)


@pytest.mark.parametrize(
    ("field", "tampered"),
    [
        ("tier", "A"),
        ("technical_access", {"method": "LOCAL_FILE", "succeeded": False}),
        ("source_quality", {"edition_status": "OFFICIAL", "textual_completeness": "COMPLETE"}),
    ],
)
def test_validator_rejects_tampered_deterministic_assessment(tmp_path, field, tampered):
    ingestion, snapshot, _ = _prepare(tmp_path)
    triage = ingestion["catalog"].all("TriageAssessment")[0]
    triage[field] = tampered
    snapshot["snapshot_hash"] = collection_snapshot_hash(snapshot)

    with pytest.raises(ValidationError) as exc:
        validate_evidence(ingestion["catalog"], ingestion["store"])
    assert exc.value.code == "E-NOVEL-TRIAGE-BIND"


def test_invalid_source_quality_is_rejected(tmp_path):
    source_path = tmp_path / "chapters"
    _write_chapter(source_path)
    spec = _direct_spec(source_path)
    spec["source_quality"]["edition_status"] = "TRUST_ME"
    ingestion = run_novel_ingestion(
        spec, tmp_path / "run", repo_root=repo_root(), now=NOW
    )
    with pytest.raises(ValidationError) as exc:
        prepare_novel_evidence_bundle(
            ingestion["catalog"],
            ingestion["store"],
            ingestion["ingestion"],
            spec,
            repo_root=repo_root(),
            now=NOW,
        )
    assert exc.value.code == "E-SOURCE-QUALITY"
