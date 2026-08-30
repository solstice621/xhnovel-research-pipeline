from __future__ import annotations

import copy

import pytest

from xhnovel_pipeline.errors import SchemaError, ValidationError
from xhnovel_pipeline.validate import (
    _no_forbidden,
    validate_all,
    validate_collection,
    validate_evidence,
    validate_export,
    validate_qualification,
)
from xhnovel_pipeline.access import normalize_access_kind
from xhnovel_pipeline.hashing import object_hash


def _live(catalog):
    return [c for c in catalog.all("Claim") if c["status"] == "ACTIVE"]


def test_snippet_case_variant_is_d():
    assert normalize_access_kind("Search_Snippet") == "search_snippet"
    assert normalize_access_kind("search-snippet") == "search_snippet"
    assert normalize_access_kind("搜索摘要") == "search_snippet"


def test_snippet_as_b_fails(slice_result):
    catalog = slice_result["catalog"]
    snip = next(t for t in catalog.all("TriageAssessment") if t["tier"] == "D")
    snip["tier"] = "B"
    with pytest.raises(ValidationError) as exc:
        validate_collection(catalog, slice_result["store"])
    assert exc.value.code == "E-SNIPPET-TIER"


def test_unknown_origin_cannot_confirm(slice_result):
    catalog = slice_result["catalog"]
    for orig in catalog.all("OriginAssessment"):
        orig["relation"] = "UNKNOWN"
    bundle = catalog.all("EvidenceBundle")[0]
    from xhnovel_pipeline.ids import derived_id
    from xhnovel_pipeline.validate import bundle_hash

    bundle["bundle_hash"] = bundle_hash(catalog, bundle)
    run = catalog.all("ExtractionRun")[0]
    old_run_id = run["extraction_run_id"]
    run["bundle_hash"] = bundle["bundle_hash"]
    run["extraction_run_id"] = derived_id(
        "ExtractionRun",
        {
            key: value
            for key, value in run.items()
            if key not in {"schema_version", "extraction_run_id", "status"}
        },
    )
    for claim in _live(catalog):
        if claim["extraction_run_id"] == old_run_id:
            claim["extraction_run_id"] = run["extraction_run_id"]
    claims = _live(catalog)
    claims[0]["support"] = [support for claim in claims for support in claim["support"]]
    claims[0]["grade"] = "CONFIRMED"
    with pytest.raises(ValidationError) as exc:
        validate_evidence(catalog, slice_result["store"])
    assert exc.value.code in {"E-UNKNOWN-ORIGIN", "E-NOT-INDEPENDENT"}


def test_same_origin_cannot_dual_b(slice_result):
    catalog = slice_result["catalog"]
    for orig in catalog.all("OriginAssessment"):
        orig["relation"] = "SAME_ORIGIN"
    for claim in _live(catalog):
        claim["grade"] = "CONFIRMED"
        # force two B supports from the two sources
    with pytest.raises(ValidationError):
        validate_evidence(catalog, slice_result["store"])


def test_placeholder_hash_fails(slice_result):
    catalog = slice_result["catalog"]
    catalog.all("Artifact")[0]["artifact_id"] = "hash-a"
    with pytest.raises((ValidationError, SchemaError)) as exc:
        validate_collection(catalog, slice_result["store"])
    if isinstance(exc.value, ValidationError):
        assert exc.value.code == "E-PLACEHOLDER-HASH"


def test_post_freeze_source_does_not_bind_old_run(slice_result):
    catalog = slice_result["catalog"]
    catalog.add(
        "Source",
        {
            "schema_version": catalog.all("Source")[0]["schema_version"],
            "source_id": "SRC-POST",
            "canonical_url": "https://example.com/post",
            "platform_id": "later",
        },
    )
    run = catalog.all("ExtractionRun")[0]
    bundle = catalog.get("EvidenceBundle", run["bundle_id"])
    assert "SRC-POST" not in str(bundle["retrieval_ids"])
    # adding a source does not change frozen bundle hash
    from xhnovel_pipeline.validate import bundle_hash

    assert bundle_hash(catalog, bundle) == bundle["bundle_hash"]


def test_ephemeral_cannot_freeze(slice_result):
    catalog = slice_result["catalog"]
    bundle = catalog.all("EvidenceBundle")[0]
    art = catalog.get("Artifact", bundle["artifact_ids"][0])
    art["durability_status"] = "EPHEMERAL"
    with pytest.raises(ValidationError) as exc:
        validate_evidence(catalog, slice_result["store"])
    assert exc.value.code == "E-EPHEMERAL"


def test_zero_claims_not_auto_eligible():
    from xhnovel_pipeline.tools_legacy import check_scene_002_tombstone
    from xhnovel_pipeline.paths import repo_root

    check_scene_002_tombstone(repo_root())


def test_legacy_scene_001_never_qualifies():
    from xhnovel_pipeline.tools_legacy import check_legacy_scene_001
    from xhnovel_pipeline.paths import repo_root

    check_legacy_scene_001(repo_root())


def test_source_injection_does_not_emit_project_tokens(slice_result):
    export = slice_result["export"]
    semantic_output = [
        {
            "statement": claim["statement"],
            "profile_payload": claim["profile_payload"],
        }
        for claim in export["claims"]
    ]
    _no_forbidden(semantic_output, "claim output")
    # injected paragraph was not turned into a claim
    for claim in export["claims"]:
        assert "忽略" not in claim["statement"]


def test_project_token_check_does_not_match_content_derived_ids():
    _no_forbidden({"claim_id": "CLM-16F437676D5B67C38615"}, "claim")
    with pytest.raises(ValidationError) as exc:
        _no_forbidden({"statement": "M-1 当前缺少什么"}, "claim")
    assert exc.value.code == "E-PROJECT-LEAK"


def test_export_tamper_hash(slice_result):
    export = slice_result["export"]
    export["scene_facts"]["extra"] = "x"
    with pytest.raises(ValidationError) as exc:
        validate_export(slice_result["catalog"], slice_result["store"])
    assert exc.value.code == "E-EXPORT-TAMPER"


def test_superseded_claim_cannot_stay_confirmed(slice_result):
    catalog = slice_result["catalog"]
    claim = next(claim for claim in catalog.all("Claim") if claim["status"] == "ACTIVE")
    claim["status"] = "SUPERSEDED"
    claim["grade"] = "CONFIRMED"

    with pytest.raises(ValidationError) as exc:
        validate_evidence(catalog, slice_result["store"])

    assert exc.value.code == "E-DEAD-CONFIRMED"


def test_invalidated_build_cannot_export(slice_result):
    catalog = slice_result["catalog"]
    catalog.all("ExtractorBuild")[0]["status"] = "INVALIDATED"
    with pytest.raises(ValidationError):
        validate_qualification(catalog)


def test_missing_artifact_scan(slice_result):
    from xhnovel_pipeline.audit import scan_artifacts

    store = slice_result["store"]
    aid = slice_result["catalog"].all("Artifact")[0]["artifact_id"]
    store.delete_for_test(aid)
    rows = scan_artifacts(store, [aid])
    assert rows[0]["status"] != "OK"
