from __future__ import annotations

import pytest

from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.validate import validate_qualification


def test_run_a_b_same_file_fails(slice_result):
    catalog = slice_result["catalog"]
    q = catalog.all("QualificationRun")[0]
    q["run_b"] = q["run_a"]
    with pytest.raises(ValidationError) as exc:
        validate_qualification(catalog)
    assert exc.value.code == "E-RUN-PAIR"


def test_forged_run_hash_fails(slice_result):
    catalog = slice_result["catalog"]
    q = catalog.all("QualificationRun")[0]
    q["run_a_hash"] = "sha256:" + "0" * 63 + "g"
    with pytest.raises(Exception):
        validate_qualification(catalog)


def test_missing_policy_on_export_fails(slice_result):
    from xhnovel_pipeline.validate import validate_export

    catalog = slice_result["catalog"]
    export = catalog.all("EvidenceExport")[0]
    export["policies"] = {}
    with pytest.raises(ValidationError) as exc:
        validate_export(catalog, slice_result["store"])
    assert exc.value.code in {"E-POLICY-HASH", "E-EXPORT-TAMPER", "E-SCHEMA"}


def test_snippet_cannot_confirm(slice_result):
    from xhnovel_pipeline.validate import validate_evidence

    catalog = slice_result["catalog"]
    claim = [c for c in catalog.all("Claim") if c["status"] == "ACTIVE"][0]
    snip = next(r for r in catalog.all("Retrieval") if "SNIP" in r["retrieval_id"])
    claim["grade"] = "CONFIRMED"
    claim["kind"] = "ORIGINAL_FACT"
    claim["support"] = [
        {
            "retrieval_id": snip["retrieval_id"],
            "artifact_id": next(
                l["artifact_id"] for l in catalog.all("RetrievalArtifact") if l["retrieval_id"] == snip["retrieval_id"]
            ),
            "segment_id": catalog.all("Segment")[0]["segment_id"],
            "normalized_text_hash": catalog.all("Segment")[0]["normalized_text_hash"],
        }
    ]
    with pytest.raises(ValidationError):
        validate_evidence(catalog, slice_result["store"])
