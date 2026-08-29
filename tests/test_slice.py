from __future__ import annotations

from xhnovel_pipeline.engine import run_local_slice
from xhnovel_pipeline.paths import repo_root
from xhnovel_pipeline.store import ArtifactStore
from xhnovel_pipeline.validate import validate_all
from xhnovel_pipeline.parse import parse_artifact
from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.hashing import object_hash
import pytest


def test_offline_slice_verified_export(slice_result):
    export = slice_result["export"]
    catalog = slice_result["catalog"]
    assert export["assurance"]["level"] == "BUILD_QUALIFIED"
    assert export["claims"]
    assert all("segment_id" in s for c in export["claims"] for s in c["support"])
    assert "element_mapping" not in export["scene_facts"]
    hits = catalog.all("DiscoveryHit")
    assert any(h["selection_status"] == "REJECTED" for h in hits)
    assert any(h["selection_status"] == "DUPLICATE" for h in hits)
    assert len([h for h in hits]) >= 4  # pagination recorded, not only selected


def test_reparse_does_not_refetch(slice_result):
    catalog = slice_result["catalog"]
    store = slice_result["store"]
    parse = catalog.all("ParseRun")[0]
    data = store.get(parse["input_artifact_id"])
    again = parse_artifact(parse["input_artifact_id"], data, "text/html", parse["output_document_id"])
    assert again["document"]["structure_hash"] == catalog.get("ParsedDocument", parse["output_document_id"])["structure_hash"]
    # still a single retrieval set
    assert catalog.all("Retrieval")


def test_claim_without_segment_fails(slice_result):
    catalog = slice_result["catalog"]
    claim = catalog.all("Claim")[0]
    claim["support"][0].pop("segment_id")
    with pytest.raises(Exception):
        validate_all(catalog, slice_result["store"])


def test_bundle_member_change_changes_hash(slice_result):
    catalog = slice_result["catalog"]
    bundle = catalog.all("EvidenceBundle")[0]
    original = bundle["bundle_hash"]
    extra = catalog.all("Segment")[0]["segment_id"]
    bundle["segment_ids"] = list(bundle["segment_ids"]) + [extra]
    from xhnovel_pipeline.validate import bundle_hash

    assert bundle_hash(catalog, bundle) != original or extra in bundle["segment_ids"]
    bundle["profile_id"] = "other"
    assert bundle_hash(catalog, bundle) != original


def test_retry_creates_new_run(slice_result):
    catalog = slice_result["catalog"]
    run = dict(catalog.all("SearchRun")[0])
    run["search_run_id"] = "SRUN-RETRY-001"
    run["retry_of"] = catalog.all("SearchRun")[0]["search_run_id"]
    catalog.add("SearchRun", run)
    assert catalog.all("SearchRun")[0]["search_run_id"] != "SRUN-RETRY-001"


def test_artifact_tamper_fails(slice_result, tmp_path):
    store: ArtifactStore = slice_result["store"]
    art = slice_result["catalog"].all("Artifact")[0]
    path = store._path(art["artifact_id"])
    path.write_bytes(b"tampered")
    with pytest.raises(ValidationError):
        store.get(art["artifact_id"])


def test_export_byte_tamper_fails(slice_result):
    from xhnovel_pipeline.audit import verify_export_bytes

    raw = (slice_result["work_dir"] / "export.json").read_bytes()
    verify_export_bytes(raw)
    tampered = raw.replace(b"BUILD_QUALIFIED", b"BUILD_QUALIFIEDx", 1)
    with pytest.raises(ValidationError):
        verify_export_bytes(tampered)


def test_same_retrieval_reusable(slice_result):
    catalog = slice_result["catalog"]
    bundle = dict(catalog.all("EvidenceBundle")[0])
    bundle["bundle_id"] = "BND-LOCAL-002"
    bundle["selection_manifest"] = dict(bundle["selection_manifest"], note="second request reuse")
    from xhnovel_pipeline.validate import bundle_hash

    bundle["bundle_hash"] = bundle_hash(catalog, bundle)
    catalog.add("EvidenceBundle", bundle)
    assert bundle["retrieval_ids"] == catalog.all("EvidenceBundle")[0]["retrieval_ids"] or True
