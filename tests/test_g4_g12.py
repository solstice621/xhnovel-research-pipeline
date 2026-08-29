from __future__ import annotations

import json

import pytest

from xhnovel_pipeline.bundle_ops import clone_bundle_with_selection, refuse_inplace_member_edit
from xhnovel_pipeline.engine import FakeSearchProvider, make_build, run_local_slice
from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.hardening import apply_gc, backup_export, restore_from_backup, write_revocation
from xhnovel_pipeline.origin import near_duplicate_assessments, token_jaccard
from xhnovel_pipeline.page_kind import looks_like_js_shell, looks_like_login_wall
from xhnovel_pipeline.parse import diff_segments, parse_artifact, parse_html
from xhnovel_pipeline.paths import repo_root
from xhnovel_pipeline.qualification import compare_claim_sets, fixture_suite_hash, qualify_mock_build
from xhnovel_pipeline.stop import decide_campaign_stop
from xhnovel_pipeline.store import ArtifactStore
from xhnovel_pipeline.validate import validate_evidence


def test_stop_reasons():
    assert decide_campaign_stop(
        selected_page_count=1, fetch_budget_hit=False, first_page_empty=False, query_budget_hit=False
    ) == ("COMPLETED", "coverage_reached")
    assert decide_campaign_stop(
        selected_page_count=0, fetch_budget_hit=True, first_page_empty=False, query_budget_hit=False
    ) == ("BUDGET_STOPPED", "budget_exhausted")
    assert decide_campaign_stop(
        selected_page_count=0, fetch_budget_hit=False, first_page_empty=True, query_budget_hit=False
    ) == ("EXHAUSTED", "provider_exhausted")
    assert decide_campaign_stop(
        selected_page_count=0, fetch_budget_hit=False, first_page_empty=False, query_budget_hit=False
    ) == ("COMPLETED", "no_new_source")


def test_search_retry_records_failed_run(tmp_path):
    root = repo_root()
    fixture = root / "fixtures/positive/minimal-local"
    inner = FakeSearchProvider(json.loads((fixture / "provider.json").read_text(encoding="utf-8")))

    class Flaky:
        provider_id = inner.provider_id
        provider_build_id = inner.provider_build_id

        def __init__(self):
            self.fails = 1

        def search(self, query_text, parameters):
            if self.fails:
                self.fails -= 1
                raise ValidationError("E-RETRYABLE", "HTTP 429")
            return inner.search(query_text, parameters)

    result = run_local_slice(fixture, tmp_path, repo_root=root, provider=Flaky())
    runs = result["catalog"].all("SearchRun")
    failed = [r for r in runs if r["status"] == "FAILED"]
    ok = [r for r in runs if r["status"] == "SUCCEEDED"]
    assert failed
    assert failed[0]["search_run_id"] != ok[0]["search_run_id"]
    assert any(r.get("retry_of") == failed[0]["search_run_id"] for r in ok)


def test_golden_html_drops_ads():
    html = (repo_root() / "fixtures/positive/golden-html/mixed.html").read_bytes()
    parsed = parse_html("sha256:" + "a" * 64, html, document_id="DOC-GOLD")
    blob = " ".join(s["normalized_text"] for s in parsed["segments"])
    assert "限时折扣" not in blob
    assert "握着" in blob
    assert "灯落地" in blob


def test_parse_diff_detects_change():
    html = (repo_root() / "fixtures/positive/golden-html/mixed.html").read_bytes()
    a = parse_html("sha256:" + "a" * 64, html, document_id="DOC-A")["segments"]
    b = parse_html(
        "sha256:" + "a" * 64,
        html.replace("灯落地".encode("utf-8"), "灯仍被持有".encode("utf-8")),
        document_id="DOC-B",
    )["segments"]
    diff = diff_segments(a, b)
    assert diff["changed"] is True


def test_login_wall_and_js_shell():
    root = repo_root()
    login = (root / "fixtures/negative/login-wall/page.html").read_bytes()
    shell = (root / "fixtures/negative/needs-renderer/page.html").read_bytes()
    assert looks_like_login_wall(login, 200)
    assert looks_like_js_shell(shell)
    assert not looks_like_js_shell((root / "fixtures/positive/minimal-local/pages/wiki-alpha.html").read_bytes())


def test_near_dup_does_not_merge_sources():
    extras = near_duplicate_assessments(
        {"SRC-A": "同一段正文重复出现。", "SRC-B": "同一段正文重复出现。"},
        policy_hash="sha256:" + "a" * 64,
        assessor_build_id="collector-v0.1",
        assessed_at="2026-08-29T00:00:00Z",
        schema_version="0.1-draft-frozen",
        existing=[],
    )
    assert extras and extras[0]["relation"] == "SAME_ORIGIN"
    assert extras[0]["source_a"] != extras[0]["source_b"]
    assert token_jaccard("a b c", "a b d") < 0.92


def test_frozen_bundle_refuses_inplace_edit(slice_result):
    catalog = slice_result["catalog"]
    bundle = catalog.all("EvidenceBundle")[0]
    with pytest.raises(ValidationError) as exc:
        refuse_inplace_member_edit(catalog, bundle, profile_id="other")
    assert exc.value.code == "E-FROZEN"


def test_second_bundle_from_same_snapshot(slice_result):
    catalog = slice_result["catalog"]
    src = catalog.all("EvidenceBundle")[0]
    second = clone_bundle_with_selection(
        catalog,
        src,
        bundle_id="BND-LOCAL-002",
        selection_manifest={**src["selection_manifest"], "note": "second selection"},
    )
    catalog.add("EvidenceBundle", second)
    assert second["bundle_hash"] != src["bundle_hash"]
    assert second["collection_snapshot_ids"] == src["collection_snapshot_ids"]


def test_prompt_change_new_build_id():
    a = make_build()
    b = make_build(prompt=a["prompt_template_hash"] + " changed")
    assert a["extractor_build_id"] != b["extractor_build_id"]
    assert a["prompt_template_hash"] != b["prompt_template_hash"]


def test_qualification_suite_hash_stable():
    root = repo_root()
    h1 = fixture_suite_hash(root)
    h2 = fixture_suite_hash(root)
    assert h1 == h2
    assert h1.startswith("sha256:")


def test_backup_restore_and_gc(slice_result, tmp_path):
    result = slice_result
    export_path = result["work_dir"] / "export.json"
    bak = tmp_path / "backup"
    backup_export(export_path, result["store"], bak)
    store2 = ArtifactStore(tmp_path / "restored")
    restored = restore_from_backup(bak, store2)
    assert restored["restored"]
    junk = store2.put(b"unreferenced-bytes")
    live = {item["artifact_id"] for item in result["export"]["artifact_manifest"]}
    removed = apply_gc(store2, live)
    assert junk in removed
    assert store2.exists(restored["restored"][0])


def test_revocation_is_sidecar(slice_result):
    path = slice_result["work_dir"] / "export.json"
    before = path.read_bytes()
    rec = write_revocation(path, reason="integrity", created_at="2026-08-29T00:00:00Z")
    assert rec["status"] == "REVOKED"
    assert path.read_bytes() == before
    assert path.with_suffix(".revocation.json").is_file()


def test_campaign_report_written(slice_result):
    report = json.loads((slice_result["work_dir"] / "campaign-report.json").read_text(encoding="utf-8"))
    assert report["stop_reason"]
    assert report["campaign_report"] == "CLAIMS_PRODUCED"
    assert report["live_claim_count"] >= 1


def test_conflicting_grade_legal(slice_result):
    catalog = slice_result["catalog"]
    claim = dict(catalog.all("Claim")[0])
    claim["claim_id"] = "CLM-CONFLICT-001"
    claim["grade"] = "CONFLICTING"
    catalog.add("Claim", claim)
    validate_evidence(catalog, slice_result["store"])


def test_parse_failure_no_document():
    with pytest.raises(ValidationError) as exc:
        parse_artifact("sha256:" + "a" * 64, b"%PDF-not-a-real-file", "application/pdf", "DOC-BAD")
    assert exc.value.code == "E-PARSE"


def test_qualify_mock_on_positive_claims(slice_result):
    root = repo_root()
    claims = [c for c in slice_result["catalog"].all("Claim") if c["status"] == "ACTIVE"]
    q = qualify_mock_build(
        root,
        extractor_build_id="BLD-MOCK-DETERMINISTIC-V1",
        claims=claims,
        injected=claims,
        qualified_at="2026-08-29T00:00:00Z",
    )
    assert q["result"] == "PASS"
    assert q["run_a"] != q["run_b"]
    assert compare_claim_sets(claims, claims)
