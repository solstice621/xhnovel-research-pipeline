from __future__ import annotations

import copy
import json

import pytest

from xhnovel_pipeline.audit import verify_export_bytes
from xhnovel_pipeline.errors import PipelineError, ValidationError
from xhnovel_pipeline.hashing import object_hash
from xhnovel_pipeline.ids import derived_id
from xhnovel_pipeline.importer import import_export
from xhnovel_pipeline.catalog import Catalog
from xhnovel_pipeline.engine import make_build
from xhnovel_pipeline.paths import repo_root
from xhnovel_pipeline.qualification import qualify_mock_build
from xhnovel_pipeline.validate import (
    bundle_hash,
    validate_collection,
    validate_evidence,
    validate_export,
    validate_qualification,
)


def _write_export(path, export):
    export["export_hash"] = object_hash(export, omit=("export_hash",))
    path.write_text(json.dumps(export), encoding="utf-8")


def _rebind_qualification_id(catalog, qualification):
    old_id = qualification["qualification_run_id"]
    payload = {key: value for key, value in qualification.items() if key != "qualification_run_id"}
    qualification["qualification_run_id"] = derived_id("QualificationRun", payload)
    for assurance in catalog.all("AssuranceRecord"):
        if assurance.get("qualification_run_id") == old_id:
            assurance["qualification_run_id"] = qualification["qualification_run_id"]
    for export in catalog.all("EvidenceExport"):
        if export["assurance"].get("qualification_run_id") == old_id:
            export["assurance"]["qualification_run_id"] = qualification["qualification_run_id"]


def test_envelope_rejects_unknown_field_even_with_recomputed_hash(slice_result):
    forged = copy.deepcopy(slice_result["export"])
    forged["unexpected"] = "accepted-by-old-verifier"
    forged["export_hash"] = object_hash(forged, omit=("export_hash",))

    with pytest.raises(PipelineError):
        verify_export_bytes(json.dumps(forged).encode("utf-8"))


def test_import_requires_explicit_trusted_catalog(slice_result, tmp_path):
    export_path = slice_result["work_dir"] / "export.json"

    with pytest.raises(ValidationError) as exc:
        import_export(export_path, tmp_path / "lock.json")

    assert exc.value.code == "E-IMPORT-TRUST"


def test_import_rejects_self_signed_payload_not_in_trusted_catalog(slice_result, tmp_path):
    forged = copy.deepcopy(slice_result["export"])
    forged["claims"][0]["statement"] = "attacker supplied claim"
    export_path = tmp_path / "forged.json"
    _write_export(export_path, forged)

    with pytest.raises(ValidationError) as exc:
        import_export(
            export_path,
            tmp_path / "lock.json",
            trusted_catalog_path=slice_result["work_dir"] / "catalog.json",
            artifact_store_path=slice_result["root_work_dir"] / "objects",
        )

    assert exc.value.code == "E-IMPORT-TRUST"


def test_import_rejects_full_auditability_when_artifacts_are_missing(slice_result, tmp_path):
    empty_store = tmp_path / "empty-objects"
    empty_store.mkdir()
    with pytest.raises(ValidationError) as exc:
        import_export(
            slice_result["work_dir"] / "export.json",
            tmp_path / "lock.json",
            trusted_catalog_path=slice_result["work_dir"] / "catalog.json",
            artifact_store_path=empty_store,
        )

    assert exc.value.code == "E-ARTIFACT-MISSING"


def test_import_rejects_unqualified_export(slice_result, tmp_path):
    catalog = slice_result["catalog"]
    export = catalog.all("EvidenceExport")[0]
    export["assurance"] = {"level": "UNQUALIFIED", "auditability": "FULL"}
    export["export_id"] = derived_id(
        "EvidenceExport",
        {key: value for key, value in export.items() if key not in {"export_id", "export_hash"}},
    )
    export["export_hash"] = object_hash(export, omit=("export_hash",))
    export_path = tmp_path / "unqualified-export.json"
    catalog_path = tmp_path / "unqualified-catalog.json"
    export_path.write_text(json.dumps(export), encoding="utf-8")
    catalog_path.write_text(
        json.dumps({kind: values for kind, values in catalog.by_type.items() if values}),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError) as exc:
        import_export(
            export_path,
            tmp_path / "lock.json",
            trusted_catalog_path=catalog_path,
            artifact_store_path=slice_result["root_work_dir"] / "objects",
        )

    assert exc.value.code == "E-ASSURANCE"


def test_missing_qualification_record_invalidates_assurance(slice_result):
    catalog = slice_result["catalog"]
    catalog.by_type["QualificationRun"] = []

    with pytest.raises(ValidationError) as exc:
        validate_qualification(catalog)

    assert exc.value.code == "E-QUALIFICATION-BIND"


def test_missing_assurance_record_invalidates_assurance(slice_result):
    catalog = slice_result["catalog"]
    catalog.by_type["AssuranceRecord"] = []

    with pytest.raises(ValidationError) as exc:
        validate_qualification(catalog)

    assert exc.value.code == "E-ASSURANCE-BIND"


def test_qualification_cannot_reuse_run_result(slice_result):
    catalog = slice_result["catalog"]
    qualification = catalog.all("QualificationRun")[0]
    qualification["run_b_result"] = copy.deepcopy(qualification["run_a_result"])
    _rebind_qualification_id(catalog, qualification)

    with pytest.raises(ValidationError) as exc:
        validate_qualification(catalog)

    assert exc.value.code == "E-RUN-PAIR"


def test_qualification_cannot_substitute_self_consistent_claim_results(slice_result):
    catalog = slice_result["catalog"]
    qualification = catalog.all("QualificationRun")[0]
    for result in (qualification["run_a_result"], qualification["run_b_result"]):
        result["claims"] = [
            {
                "kind": "ORIGINAL_FACT",
                "grade": "SUPPORTED",
                "statement": "attacker supplied",
                "profile_schema": "xuanhuan-gameplay-scene/v1",
                "profile_payload": {
                    "actors": ["attacker"],
                    "action": "forged",
                    "target": "claim",
                    "precondition": "none",
                    "state_transition": "forged",
                },
            }
        ]
        result["claim_set_hash"] = object_hash({"claims": result["claims"]}, omit=())
        result["result_hash"] = object_hash(result, omit=("result_hash",))
    _rebind_qualification_id(catalog, qualification)

    with pytest.raises(ValidationError) as exc:
        validate_qualification(catalog)

    assert exc.value.code == "E-QUALIFICATION-REPLAY"


def test_qualification_is_bound_to_exact_build_identity(slice_result):
    catalog = slice_result["catalog"]
    catalog.all("ExtractorBuild")[0]["model"] = "different-model"

    with pytest.raises(ValidationError) as exc:
        validate_qualification(catalog)

    assert exc.value.code == "E-BUILD-BIND"


def test_qualification_requires_repository_commit(slice_result):
    catalog = slice_result["catalog"]
    catalog.all("ExtractorBuild")[0].pop("repository_commit")

    with pytest.raises(PipelineError) as exc:
        validate_qualification(catalog)

    assert exc.value.code == "E-SCHEMA"


def test_qualification_rejects_build_source_hash_that_is_not_running_code():
    root = repo_root()
    build = make_build(source_tree_hash="sha256:" + "f" * 64)
    qualification = qualify_mock_build(root, qualified_at="2026-08-29T00:00:00Z", build=build)
    catalog = Catalog()
    catalog.add("ExtractorBuild", build)
    catalog.add("QualificationRun", qualification)

    with pytest.raises(ValidationError) as exc:
        validate_qualification(catalog)

    assert exc.value.code == "E-BUILD-BIND"


def test_bundle_hash_binds_assessment_contents(slice_result):
    catalog = slice_result["catalog"]
    bundle = catalog.all("EvidenceBundle")[0]
    triage = catalog.get("TriageAssessment", bundle["triage_assessment_ids"][0])
    original = bundle["bundle_hash"]
    triage["tier"] = "A" if triage["tier"] != "A" else "B"

    assert bundle_hash(catalog, bundle) != original


def test_bundle_hash_binds_snapshot_hit_contents(slice_result):
    catalog = slice_result["catalog"]
    bundle = catalog.all("EvidenceBundle")[0]
    hit = catalog.get("DiscoveryHit", catalog.all("CollectionSnapshot")[0]["hit_ids"][0])
    original = bundle["bundle_hash"]
    hit["title"] += " tampered"

    assert bundle_hash(catalog, bundle) != original


def test_bundle_hash_binds_query_spec_contents(slice_result):
    catalog = slice_result["catalog"]
    bundle = catalog.all("EvidenceBundle")[0]
    query = catalog.all("QuerySpec")[0]
    original = bundle["bundle_hash"]
    query["query_text"] += " tampered"

    assert bundle_hash(catalog, bundle) != original


def test_bundle_hash_binds_snapshot_retrieval_artifact_edges(slice_result):
    catalog = slice_result["catalog"]
    bundle = catalog.all("EvidenceBundle")[0]
    snippet = next(
        retrieval
        for retrieval in catalog.all("Retrieval")
        if retrieval["access_kind"] == "search_snippet"
    )
    edge = next(
        edge
        for edge in catalog.all("RetrievalArtifact")
        if edge["retrieval_id"] == snippet["retrieval_id"]
    )
    replacement = next(
        artifact["artifact_id"]
        for artifact in catalog.all("Artifact")
        if artifact["artifact_id"] != edge["artifact_id"]
    )
    original = bundle["bundle_hash"]
    edge["artifact_id"] = replacement

    assert bundle_hash(catalog, bundle) != original


def test_bundle_request_must_match_snapshot_campaign_request(slice_result):
    catalog = slice_result["catalog"]
    forged_request = copy.deepcopy(catalog.all("ResearchRequest")[0])
    forged_request["request_id"] = "REQ-FORGED-OTHER"
    forged_request["discovery_brief"] = "unrelated request"
    catalog.add("ResearchRequest", forged_request)
    bundle = catalog.all("EvidenceBundle")[0]
    bundle["request_id"] = forged_request["request_id"]

    with pytest.raises(ValidationError) as exc:
        validate_evidence(catalog, slice_result["store"])

    assert exc.value.code == "E-REQUEST-BIND"


def test_bundle_cannot_select_retrieval_outside_its_snapshots(slice_result):
    catalog = slice_result["catalog"]
    claim = next(claim for claim in catalog.all("Claim") if claim["status"] == "ACTIVE")
    retrieval_id = claim["support"][0]["retrieval_id"]
    snapshot = catalog.all("CollectionSnapshot")[0]
    snapshot["retrieval_ids"].remove(retrieval_id)

    with pytest.raises(ValidationError) as exc:
        validate_evidence(catalog, slice_result["store"])

    assert exc.value.code == "E-OUT-OF-SNAPSHOT"


def test_selection_manifest_must_match_snapshot_hit_decisions(slice_result):
    catalog = slice_result["catalog"]
    rejected = next(hit for hit in catalog.all("DiscoveryHit") if hit["selection_status"] == "REJECTED")
    rejected["selection_status"] = "SELECTED"

    with pytest.raises(ValidationError) as exc:
        validate_evidence(catalog, slice_result["store"])

    assert exc.value.code == "E-SELECTION-MANIFEST"


def test_bundle_hash_recomputes_segment_text_hash(slice_result):
    catalog = slice_result["catalog"]
    bundle = catalog.all("EvidenceBundle")[0]
    segment = catalog.get("Segment", bundle["segment_ids"][0])
    segment["normalized_text"] += " tampered"

    with pytest.raises(ValidationError) as exc:
        bundle_hash(catalog, bundle)

    assert exc.value.code == "E-TEXT-HASH"


def test_claim_support_requires_retrieval_artifact_edge(slice_result):
    catalog = slice_result["catalog"]
    claim = next(c for c in catalog.all("Claim") if c["status"] == "ACTIVE")
    support = claim["support"][0]
    support["artifact_id"] = next(
        artifact_id
        for artifact_id in catalog.all("EvidenceBundle")[0]["artifact_ids"]
        if artifact_id != support["artifact_id"]
    )

    with pytest.raises(ValidationError) as exc:
        validate_evidence(catalog, slice_result["store"])

    assert exc.value.code == "E-LINEAGE"


def test_fetched_retrieval_requires_artifact_edge(slice_result):
    catalog = slice_result["catalog"]
    retrieval = next(item for item in catalog.all("Retrieval") if item["status"] == "FETCHED")
    catalog.by_type["RetrievalArtifact"] = [
        link for link in catalog.all("RetrievalArtifact") if link["retrieval_id"] != retrieval["retrieval_id"]
    ]

    with pytest.raises(ValidationError) as exc:
        validate_collection(catalog, slice_result["store"])

    assert exc.value.code == "E-LINEAGE"


def test_claim_must_bind_its_extraction_run_and_bundle(slice_result):
    catalog = slice_result["catalog"]
    claim = next(c for c in catalog.all("Claim") if c["status"] == "ACTIVE")
    claim["extraction_run_id"] = "ERUN-NOT-THERE"

    with pytest.raises(ValidationError) as exc:
        validate_evidence(catalog, slice_result["store"])

    assert exc.value.code == "E-DANGLING-REF"


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("execution_environment", "model_snapshot", "unqualified-model-v999"),
        ("execution_environment", "executor_build_id", "unqualified-executor-v999"),
        ("execution_environment", "parameters", {"temperature": 1}),
        ("execution_environment", "tool_policy_hash", "sha256:" + "a" * 64),
        ("input_manifest", "system_prompt_hash", "sha256:" + "b" * 64),
        ("input_manifest", "user_prompt_hash", "sha256:" + "c" * 64),
    ],
)
def test_extraction_run_must_match_qualified_build(slice_result, section, field, value):
    catalog = slice_result["catalog"]
    catalog.all("ExtractionRun")[0][section][field] = value

    with pytest.raises(ValidationError) as exc:
        validate_evidence(catalog, slice_result["store"])

    assert exc.value.code == "E-BUILD-BIND"


def test_parser_output_must_replay_from_artifact_bytes(slice_result):
    catalog = slice_result["catalog"]
    segment = catalog.all("Segment")[0]
    segment["normalized_text"] = "火星传送门已经投入使用"
    from xhnovel_pipeline.parse import text_hash

    segment["normalized_text_hash"] = text_hash(segment["normalized_text"])
    parse = next(
        parse
        for parse in catalog.all("ParseRun")
        if parse.get("output_document_id") == segment["document_id"]
    )
    document = catalog.get("ParsedDocument", segment["document_id"])
    segments = [item for item in catalog.all("Segment") if item["document_id"] == document["document_id"]]
    parse["output_hash"] = object_hash({"document": document, "segments": segments})

    with pytest.raises(ValidationError) as exc:
        validate_evidence(catalog, slice_result["store"])

    assert exc.value.code == "E-PARSE-REPLAY"


def test_claim_id_is_bound_to_claim_content(slice_result):
    catalog = slice_result["catalog"]
    catalog.all("Claim")[0]["statement"] = "attacker supplied claim"

    with pytest.raises(ValidationError) as exc:
        validate_evidence(catalog, slice_result["store"])

    assert exc.value.code == "E-ID-BIND"


def test_self_consistent_forged_claim_is_rejected_by_extractor_replay(slice_result):
    catalog = slice_result["catalog"]
    claim = catalog.all("Claim")[0]
    claim["statement"] = "火星传送门已经投入使用"
    claim["profile_payload"] = {
        "actors": ["火星居民"],
        "action": "启用",
        "target": "传送门",
        "precondition": "未知",
        "state_transition": "投入使用",
    }
    claim["claim_id"] = derived_id(
        "Claim", {key: value for key, value in claim.items() if key != "claim_id"}
    )

    with pytest.raises(ValidationError) as exc:
        validate_evidence(catalog, slice_result["store"])

    assert exc.value.code == "E-EXTRACTION-REPLAY"


def test_unrelated_dual_b_support_cannot_confirm_forged_claim(slice_result):
    catalog = slice_result["catalog"]
    claim = _make_first_claim_dual_b_confirmed(catalog)
    claim["statement"] = "火星存在传送门"
    claim["profile_payload"] = {
        "actors": ["火星居民"],
        "action": "使用",
        "target": "传送门",
        "precondition": "未知",
        "state_transition": "已开启",
    }
    claim["claim_id"] = derived_id(
        "Claim", {key: value for key, value in claim.items() if key != "claim_id"}
    )

    with pytest.raises(ValidationError) as exc:
        validate_evidence(catalog, slice_result["store"])

    assert exc.value.code == "E-EXTRACTION-REPLAY"


def test_extraction_run_id_is_bound_to_run_input(slice_result):
    catalog = slice_result["catalog"]
    catalog.all("ExtractionRun")[0]["trigger"]["reason"] = "changed after execution"

    with pytest.raises(ValidationError) as exc:
        validate_evidence(catalog, slice_result["store"])

    assert exc.value.code == "E-ID-BIND"


def test_bundle_id_is_bound_to_bundle_hash(slice_result):
    catalog = slice_result["catalog"]
    bundle = catalog.all("EvidenceBundle")[0]
    bundle["selection_manifest"]["note"] = "changed after freeze"
    _rebind_bundle(catalog)

    with pytest.raises(ValidationError) as exc:
        validate_evidence(catalog, slice_result["store"])

    assert exc.value.code == "E-ID-BIND"


def test_export_id_is_bound_to_export_content(slice_result):
    catalog = slice_result["catalog"]
    export = catalog.all("EvidenceExport")[0]
    export["scene_facts"]["note"] = "changed after export"
    export["export_hash"] = object_hash(export, omit=("export_hash",))

    with pytest.raises(ValidationError) as exc:
        validate_export(catalog, slice_result["store"])

    assert exc.value.code == "E-ID-BIND"


def test_profile_payload_is_validated_against_declared_profile(slice_result):
    catalog = slice_result["catalog"]
    claim = next(c for c in catalog.all("Claim") if c["status"] == "ACTIVE")
    claim["profile_payload"].pop("actors")

    with pytest.raises(PipelineError):
        validate_evidence(catalog, slice_result["store"])


def test_profile_payload_rejects_empty_required_values(slice_result):
    catalog = slice_result["catalog"]
    claim = next(c for c in catalog.all("Claim") if c["status"] == "ACTIVE")
    claim["profile_payload"] = {
        "actors": [],
        "action": "",
        "target": "",
        "precondition": "",
        "state_transition": "",
    }

    with pytest.raises(PipelineError) as exc:
        validate_evidence(catalog, slice_result["store"])

    assert exc.value.code == "E-PROFILE-SCHEMA"


def test_bundle_rejects_retrieval_from_rejected_hit(slice_result):
    catalog = slice_result["catalog"]
    claim = next(c for c in catalog.all("Claim") if c["status"] == "ACTIVE")
    support = claim["support"][0]
    retrieval = catalog.get("Retrieval", support["retrieval_id"])
    hit = catalog.get("DiscoveryHit", retrieval["discovery_hit_id"])
    hit["selection_status"] = "REJECTED"
    bundle = catalog.all("EvidenceBundle")[0]
    bundle["selection_manifest"]["selected_hit_ids"].remove(hit["hit_id"])
    bundle["selection_manifest"]["rejected_hit_ids"].append(hit["hit_id"])

    with pytest.raises(ValidationError) as exc:
        validate_evidence(catalog, slice_result["store"])

    assert exc.value.code == "E-SELECTION-MANIFEST"


def test_explicit_independence_cannot_override_same_platform_alias(slice_result):
    catalog = slice_result["catalog"]
    sources = [source for source in catalog.all("Source") if source["source_id"] in {"SRC-001", "SRC-002"}]
    assert len(sources) == 2
    sources[1]["same_platform_as"] = sources[0]["source_id"]

    with pytest.raises(ValidationError) as exc:
        validate_collection(catalog, slice_result["store"])

    assert exc.value.code == "E-NOT-INDEPENDENT"


def _rebind_bundle(catalog):
    bundle = catalog.all("EvidenceBundle")[0]
    bundle["bundle_hash"] = bundle_hash(catalog, bundle)
    run = catalog.all("ExtractionRun")[0]
    old_run_id = run["extraction_run_id"]
    run["bundle_hash"] = bundle["bundle_hash"]
    run_identity = {
        key: value
        for key, value in run.items()
        if key not in {"schema_version", "extraction_run_id", "status"}
    }
    run["extraction_run_id"] = derived_id("ExtractionRun", run_identity)
    for claim in catalog.all("Claim"):
        if claim["extraction_run_id"] != old_run_id:
            continue
        claim["extraction_run_id"] = run["extraction_run_id"]
        claim["claim_id"] = derived_id(
            "Claim", {key: value for key, value in claim.items() if key != "claim_id"}
        )
    return bundle


def _make_first_claim_dual_b_confirmed(catalog):
    claims = [claim for claim in catalog.all("Claim") if claim["status"] == "ACTIVE"]
    claim = claims[0]
    claim["grade"] = "CONFIRMED"
    claim["support"] = [support for candidate in claims for support in candidate["support"]]
    return claim


def test_claim_cannot_use_triage_outside_bound_bundle(slice_result):
    catalog = slice_result["catalog"]
    claim = next(c for c in catalog.all("Claim") if c["status"] == "ACTIVE")
    used_retrievals = {support["retrieval_id"] for support in claim["support"]}
    bundle = catalog.all("EvidenceBundle")[0]
    bundle["triage_assessment_ids"] = [
        assessment_id
        for assessment_id in bundle["triage_assessment_ids"]
        if catalog.get("TriageAssessment", assessment_id)["retrieval_id"] not in used_retrievals
    ]
    _rebind_bundle(catalog)

    with pytest.raises(ValidationError) as exc:
        validate_evidence(catalog, slice_result["store"])

    assert exc.value.code == "E-OUT-OF-BUNDLE"


def test_claim_cannot_use_origin_assessment_outside_bound_bundle(slice_result):
    catalog = slice_result["catalog"]
    _make_first_claim_dual_b_confirmed(catalog)
    bundle = catalog.all("EvidenceBundle")[0]
    bundle["origin_assessment_ids"] = []
    _rebind_bundle(catalog)

    with pytest.raises(ValidationError) as exc:
        validate_evidence(catalog, slice_result["store"])

    assert exc.value.code in {"E-UNKNOWN-ORIGIN", "E-NOT-INDEPENDENT"}


def test_conflicting_origin_assessments_fail_closed(slice_result):
    catalog = slice_result["catalog"]
    _make_first_claim_dual_b_confirmed(catalog)
    bundle = catalog.all("EvidenceBundle")[0]
    snapshot = catalog.all("CollectionSnapshot")[0]
    original = catalog.get("OriginAssessment", bundle["origin_assessment_ids"][0])
    conflicting = copy.deepcopy(original)
    conflicting["assessment_id"] = "ORI-CONFLICTING-001"
    conflicting["relation"] = "SAME_ORIGIN"
    catalog.add("OriginAssessment", conflicting)
    bundle["origin_assessment_ids"].append(conflicting["assessment_id"])
    snapshot["origin_assessment_ids"].append(conflicting["assessment_id"])
    _rebind_bundle(catalog)

    with pytest.raises(ValidationError) as exc:
        validate_evidence(catalog, slice_result["store"])

    assert exc.value.code == "E-NOT-INDEPENDENT"


def test_supported_reception_requires_non_snippet_tier_c(slice_result):
    catalog = slice_result["catalog"]
    claim = next(claim for claim in catalog.all("Claim") if claim["status"] == "ACTIVE")
    claim["kind"] = "RECEPTION"
    claim["grade"] = "SUPPORTED"
    for support in claim["support"]:
        retrieval = catalog.get("Retrieval", support["retrieval_id"])
        catalog.get("TriageAssessment", retrieval["triage_assessment_id"])["tier"] = "D"
    _rebind_bundle(catalog)

    with pytest.raises(ValidationError) as exc:
        validate_evidence(catalog, slice_result["store"])

    assert exc.value.code == "E-TIER-C-SUPPORT"


def test_extraction_allowlist_cannot_include_artifact_outside_bundle(slice_result):
    catalog = slice_result["catalog"]
    bundle = catalog.all("EvidenceBundle")[0]
    run = catalog.all("ExtractionRun")[0]
    run["input_manifest"]["allowed_context_artifact_ids"] = list(
        run["input_manifest"]["allowed_context_artifact_ids"]
    )
    extra = next(
        artifact["artifact_id"]
        for artifact in catalog.all("Artifact")
        if artifact["artifact_id"] not in bundle["artifact_ids"]
    )
    run["input_manifest"]["allowed_context_artifact_ids"].append(extra)

    with pytest.raises(ValidationError) as exc:
        validate_evidence(catalog, slice_result["store"])

    assert exc.value.code == "E-OUT-OF-BUNDLE"


def test_fake_coverage_stop_is_rejected(slice_result):
    catalog = slice_result["catalog"]
    for origin in catalog.all("OriginAssessment"):
        origin["relation"] = "UNKNOWN"

    with pytest.raises(ValidationError) as exc:
        validate_collection(catalog, slice_result["store"])

    assert exc.value.code == "E-STOP-REASON"


def test_export_manifest_cannot_omit_catalog_artifact(slice_result):
    catalog = slice_result["catalog"]
    export = catalog.all("EvidenceExport")[0]
    export["artifact_manifest"] = export["artifact_manifest"][:-1]
    export["export_hash"] = object_hash(export, omit=("export_hash",))

    with pytest.raises(ValidationError) as exc:
        validate_export(catalog, slice_result["store"])

    assert exc.value.code == "E-ARTIFACT-BIND"


def test_export_rejects_duplicate_claim_id_that_masks_forged_claim(slice_result):
    catalog = slice_result["catalog"]
    export = catalog.all("EvidenceExport")[0]
    forged = copy.deepcopy(export["claims"][0])
    forged["statement"] = "attacker supplied duplicate"
    export["claims"] = [forged, *export["claims"]]
    export["export_hash"] = object_hash(export, omit=("export_hash",))

    with pytest.raises(ValidationError) as exc:
        validate_export(catalog, slice_result["store"])

    assert exc.value.code == "E-CLAIM-BIND"


def test_human_audited_requires_human_audit_record(slice_result):
    catalog = slice_result["catalog"]
    export = catalog.all("EvidenceExport")[0]
    export["assurance"]["level"] = "HUMAN_AUDITED"

    with pytest.raises(ValidationError) as exc:
        validate_qualification(catalog)

    assert exc.value.code == "E-ASSURANCE-BIND"


def test_bundle_verified_rejects_single_extraction_run_as_insufficient(slice_result):
    catalog = slice_result["catalog"]
    export = catalog.all("EvidenceExport")[0]
    run = catalog.all("ExtractionRun")[0]
    qualification = catalog.all("QualificationRun")[0]
    bundle = catalog.all("EvidenceBundle")[0]
    catalog.add(
        "AssuranceRecord",
        {
            "schema_version": bundle["schema_version"],
            "subject_type": "BUNDLE",
            "subject_id": bundle["bundle_id"],
            "level": "BUNDLE_VERIFIED",
            "qualification_run_id": qualification["qualification_run_id"],
            "extraction_run_id": run["extraction_run_id"],
            "policy_hash": bundle["policy_bundle_hash"],
            "created_at": bundle["frozen_at"],
        },
    )
    export["assurance"]["level"] = "BUNDLE_VERIFIED"

    with pytest.raises(ValidationError) as exc:
        validate_qualification(catalog)

    assert exc.value.code == "E-ASSURANCE-BIND"
