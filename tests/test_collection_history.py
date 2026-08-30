from __future__ import annotations

import json

import pytest

from xhnovel_pipeline.engine import FakeSearchProvider, FixtureFetcher, run_local_slice
from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.paths import repo_root
from xhnovel_pipeline.stop import has_two_independent_secondary_sources
from xhnovel_pipeline.store import ArtifactStore


def _fixture_fetcher():
    fixture = repo_root() / "fixtures/positive/minimal-local"
    mapping = json.loads((fixture / "fetch-map.json").read_text(encoding="utf-8"))
    return FixtureFetcher({url: fixture / rel for url, rel in mapping.items()})


def test_provider_retry_exhaustion_persists_failed_attempt_chain(tmp_path):
    root = repo_root()
    fixture = root / "fixtures/positive/minimal-local"

    class AlwaysRetryable:
        provider_id = "failed-provider"
        provider_build_id = "failed-provider-v1"

        def search(self, query_text, parameters):
            exc = ValidationError("E-RETRYABLE", "HTTP 429")
            exc.raw_response_bytes = b"rate limited"
            raise exc

    with pytest.raises(ValidationError, match="429"):
        run_local_slice(fixture, tmp_path, repo_root=root, provider=AlwaysRetryable())

    failure_catalogs = list((tmp_path / "failures").glob("*/catalog.json"))
    assert len(failure_catalogs) == 1
    catalog = json.loads(failure_catalogs[0].read_text(encoding="utf-8"))
    runs = catalog["SearchRun"]
    assert [run["status"] for run in runs] == ["FAILED", "FAILED", "FAILED"]
    assert [run["parameters"]["attempt"] for run in runs] == [1, 2, 3]
    assert runs[0]["retry_of"] is None
    assert runs[1]["retry_of"] == runs[0]["search_run_id"]
    assert runs[2]["retry_of"] == runs[1]["search_run_id"]
    assert all(run["raw_response_artifact_id"] for run in runs)
    assert catalog["SearchCampaign"][0]["status"] == "FAILED"
    assert catalog["SearchCampaign"][0]["stop_reason"] == "failed"


def test_provider_schema_failure_preserves_exact_raw_response(tmp_path):
    root = repo_root()
    fixture = root / "fixtures/positive/minimal-local"
    raw = b'{"unexpected":"provider schema"}\n'

    class MalformedProvider:
        provider_id = "malformed-provider"
        provider_build_id = "malformed-provider-v1"

        def search(self, query_text, parameters):
            exc = ValidationError("E-PROVIDER-SCHEMA", "shape changed")
            exc.raw_response_bytes = raw
            raise exc

    with pytest.raises(ValidationError) as exc:
        run_local_slice(fixture, tmp_path, repo_root=root, provider=MalformedProvider())
    assert exc.value.code == "E-PROVIDER-SCHEMA"

    catalog_path = next((tmp_path / "failures").glob("*/catalog.json"))
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    failed_run = catalog["SearchRun"][0]
    assert failed_run["status"] == "FAILED"
    assert failed_run["raw_response_artifact_id"] in {
        artifact["artifact_id"] for artifact in catalog["Artifact"]
    }
    assert ArtifactStore(tmp_path / "objects").get(failed_run["raw_response_artifact_id"]) == raw


def test_page_retry_records_failed_attempt_and_terminal_success(tmp_path):
    root = repo_root()
    fixture = root / "fixtures/positive/minimal-local"
    inner = _fixture_fetcher()

    class FlakyFetcher:
        def __init__(self):
            self.calls: dict[str, int] = {}

        def fetch(self, url):
            self.calls[url] = self.calls.get(url, 0) + 1
            if "wiki-alpha" in url and self.calls[url] == 1:
                raise ValidationError("E-RETRYABLE", "HTTP 503")
            return inner.fetch(url)

    result = run_local_slice(fixture, tmp_path, repo_root=root, fetcher=FlakyFetcher())
    attempts = [
        ret
        for ret in result["catalog"].all("Retrieval")
        if ret.get("discovery_hit_id") == "HIT-001" and ret["access_kind"] != "search_snippet"
    ]
    assert [ret["status"] for ret in attempts] == ["FAILED", "FETCHED"]
    assert attempts[0]["retrieval_id"] != attempts[1]["retrieval_id"]
    assert attempts[1]["retry_of"] == attempts[0]["retrieval_id"]
    assert len(
        [
            triage
            for triage in result["catalog"].all("TriageAssessment")
            if triage["retrieval_id"] in {ret["retrieval_id"] for ret in attempts}
        ]
    ) == 2


def test_one_secondary_source_does_not_claim_coverage(tmp_path):
    root = repo_root()
    fixture = root / "fixtures/positive/minimal-local"
    inner = _fixture_fetcher()

    class OnePageFetcher:
        def fetch(self, url):
            if "wiki-beta" in url:
                raise ValidationError("E-HTTP", "HTTP 404")
            return inner.fetch(url)

    result = run_local_slice(fixture, tmp_path, repo_root=root, fetcher=OnePageFetcher())
    campaign = result["catalog"].all("SearchCampaign")[0]
    assert campaign["stop_reason"] != "coverage_reached"
    assert campaign["status"] != "COMPLETED"


def test_two_tier_b_sources_require_explicit_independence():
    retrievals = [
        {
            "retrieval_id": "RET-A",
            "source_id": "SRC-A",
            "status": "FETCHED",
            "triage_assessment_id": "TRI-A",
        },
        {
            "retrieval_id": "RET-B",
            "source_id": "SRC-B",
            "status": "FETCHED",
            "triage_assessment_id": "TRI-B",
        },
    ]
    triage = [
        {"assessment_id": "TRI-A", "retrieval_id": "RET-A", "tier": "B"},
        {"assessment_id": "TRI-B", "retrieval_id": "RET-B", "tier": "B"},
    ]
    origins = [{"source_a": "SRC-A", "source_b": "SRC-B", "relation": "UNKNOWN"}]
    assert not has_two_independent_secondary_sources(
        retrievals=retrievals,
        triage_assessments=triage,
        origin_assessments=origins,
        sources=[
            {"source_id": "SRC-A", "platform_id": "platform-a"},
            {"source_id": "SRC-B", "platform_id": "platform-b"},
        ],
    )
    origins[0]["relation"] = "INDEPENDENT"
    assert has_two_independent_secondary_sources(
        retrievals=retrievals,
        triage_assessments=triage,
        origin_assessments=origins,
        sources=[
            {"source_id": "SRC-A", "platform_id": "platform-a"},
            {"source_id": "SRC-B", "platform_id": "platform-b"},
        ],
    )


def test_unselected_triage_and_conflicting_origin_do_not_satisfy_coverage():
    retrievals = [
        {
            "retrieval_id": "RET-A",
            "source_id": "SRC-A",
            "status": "FETCHED",
            "triage_assessment_id": "TRI-A",
        },
        {
            "retrieval_id": "RET-B",
            "source_id": "SRC-B",
            "status": "FETCHED",
            "triage_assessment_id": "TRI-B",
        },
    ]
    triage = [
        {"assessment_id": "TRI-A", "retrieval_id": "RET-A", "tier": "D"},
        {"assessment_id": "TRI-A-SIDECAR", "retrieval_id": "RET-A", "tier": "B"},
        {"assessment_id": "TRI-B", "retrieval_id": "RET-B", "tier": "B"},
    ]
    origins = [
        {"source_a": "SRC-A", "source_b": "SRC-B", "relation": "INDEPENDENT"},
        {"source_a": "SRC-A", "source_b": "SRC-B", "relation": "SAME_ORIGIN"},
    ]
    assert not has_two_independent_secondary_sources(
        retrievals=retrievals,
        triage_assessments=triage,
        origin_assessments=origins,
        sources=[
            {"source_id": "SRC-A", "platform_id": "platform-a"},
            {"source_id": "SRC-B", "platform_id": "platform-b"},
        ],
    )


def test_retry_lineage_rejects_self_reference(slice_result):
    catalog = slice_result["catalog"]
    run = catalog.all("SearchRun")[0]
    run["retry_of"] = run["search_run_id"]

    with pytest.raises(ValidationError) as exc:
        from xhnovel_pipeline.validate import validate_collection

        validate_collection(catalog, slice_result["store"])

    assert exc.value.code == "E-RETRY-LINEAGE"


def test_terminal_ids_are_stable_and_outputs_are_append_only(tmp_path):
    root = repo_root()
    fixture = root / "fixtures/positive/minimal-local"
    first = run_local_slice(fixture, tmp_path / "history", repo_root=root)
    same = run_local_slice(fixture, tmp_path / "same-input", repo_root=root)
    same_location = run_local_slice(fixture, tmp_path / "history", repo_root=root)

    for kind, field in (
        ("SearchCampaign", "campaign_id"),
        ("SearchRun", "search_run_id"),
        ("CollectionSnapshot", "snapshot_id"),
        ("EvidenceBundle", "bundle_id"),
        ("ExtractionRun", "extraction_run_id"),
        ("QualificationRun", "qualification_run_id"),
        ("EvidenceExport", "export_id"),
    ):
        assert first["catalog"].ids(kind) == same["catalog"].ids(kind)
        assert all("LOCAL-001" not in item[field] for item in first["catalog"].all(kind))
    assert same_location["work_dir"] == first["work_dir"]

    changed_response = json.loads((fixture / "provider.json").read_text(encoding="utf-8"))
    changed_response["pages"][0]["hits"][0]["snippet"] += "（修订）"
    changed = run_local_slice(
        fixture,
        tmp_path / "history",
        repo_root=root,
        provider=FakeSearchProvider(changed_response),
    )

    assert changed["export"]["export_id"] != first["export"]["export_id"]
    assert changed["work_dir"] != first["work_dir"]
    assert (first["work_dir"] / "export.json").is_file()
    assert (changed["work_dir"] / "export.json").is_file()
    assert len(list((tmp_path / "history" / "runs").glob("*/export.json"))) == 2
    assert json.loads((tmp_path / "history" / "export.json").read_text(encoding="utf-8"))[
        "export_id"
    ] == first["export"]["export_id"]
