from __future__ import annotations

from typing import Any

from .access import is_snippet_kind, looks_like_snippet_label, normalize_access_kind
from .catalog import Catalog
from .constants import FORBIDDEN_EXPORT_TOKENS, SCHEMA_VERSION
from .errors import ValidationError
from .hashing import is_real_sha256, object_hash, sorted_ids
from .origin import independent_pair, platform_classes
from .schema import SCHEMA_BY_TYPE, validate_schema
from .store import ArtifactStore


def _require_hash(value: object, label: str) -> None:
    if not is_real_sha256(value):
        raise ValidationError("E-PLACEHOLDER-HASH", f"{label} is not a real SHA-256: {value!r}")


def _no_forbidden(text: str, label: str) -> None:
    for token in FORBIDDEN_EXPORT_TOKENS:
        if token in text:
            raise ValidationError("E-PROJECT-LEAK", f"{label} contains forbidden token {token}")


def validate_typed(kind: str, obj: dict[str, Any]) -> None:
    if obj.get("schema_version") != SCHEMA_VERSION:
        raise ValidationError("E-SCHEMA-VERSION", f"{kind} schema_version {obj.get('schema_version')!r}")
    if kind in SCHEMA_BY_TYPE:
        validate_schema(kind, obj)


def validate_collection(catalog: Catalog, store: ArtifactStore | None = None) -> None:
    for kind in (
        "ResearchRequest",
        "SearchCampaign",
        "QuerySpec",
        "SearchRun",
        "DiscoveryHit",
        "Source",
        "Retrieval",
        "TriageAssessment",
        "OriginAssessment",
        "CollectionSnapshot",
        "Artifact",
    ):
        for obj in catalog.all(kind):
            validate_typed(kind, obj)

    for campaign in catalog.all("SearchCampaign"):
        catalog.get("ResearchRequest", campaign["request_id"])
        if campaign["status"] not in {"DRAFT", "RUNNING"} and not campaign.get("stop_reason"):
            raise ValidationError("E-STOP-REASON", f"{campaign['campaign_id']} terminal without stop_reason")

    for query in catalog.all("QuerySpec"):
        catalog.get("SearchCampaign", query["campaign_id"])
        for hid in query.get("derived_from_hit_ids") or []:
            catalog.get("DiscoveryHit", hid)

    for run in catalog.all("SearchRun"):
        catalog.get("QuerySpec", run["query_id"])
        if run.get("raw_response_artifact_id"):
            _require_hash(run["raw_response_artifact_id"], run["search_run_id"])
            if store and not store.exists(run["raw_response_artifact_id"]):
                raise ValidationError("E-ARTIFACT-MISSING", f"{run['search_run_id']} raw response missing")
        if run.get("result_set_hash"):
            hits = [h for h in catalog.all("DiscoveryHit") if h["search_run_id"] == run["search_run_id"]]
            payload = [
                {"rank": h["rank"], "url": h["url"], "snippet": h["snippet"], "hit_id": h["hit_id"]}
                for h in sorted(hits, key=lambda h: h["rank"])
            ]
            expected = object_hash({"hits": payload}, omit=())
            if run["result_set_hash"] != expected:
                raise ValidationError("E-HASH-MISMATCH", f"{run['search_run_id']} result_set_hash mismatch")

    ranks: dict[str, set[int]] = {}
    for hit in catalog.all("DiscoveryHit"):
        catalog.get("SearchRun", hit["search_run_id"])
        ranks.setdefault(hit["search_run_id"], set())
        if hit["rank"] in ranks[hit["search_run_id"]]:
            raise ValidationError("E-HIT-RANK", f"duplicate rank {hit['rank']} in {hit['search_run_id']}")
        ranks[hit["search_run_id"]].add(hit["rank"])

    sources = catalog.all("Source")
    platform_classes(sources)

    for retrieval in catalog.all("Retrieval"):
        catalog.get("Source", retrieval["source_id"])
        if retrieval.get("discovery_hit_id"):
            catalog.get("DiscoveryHit", retrieval["discovery_hit_id"])
        kind = normalize_access_kind(retrieval["access_kind"])
        if not kind:
            raise ValidationError("E-ACCESS-KIND", f"{retrieval['retrieval_id']} missing access_kind")
        if looks_like_snippet_label(retrieval["access_kind"]) and kind != "search_snippet":
            raise ValidationError("E-SNIPPET-KIND", f"{retrieval['retrieval_id']} snippet alias not normalized")

    for art in catalog.all("Artifact"):
        _require_hash(art["artifact_id"], "artifact_id")
        if store and art["durability_status"] != "EPHEMERAL":
            if not store.exists(art["artifact_id"]):
                raise ValidationError("E-ARTIFACT-MISSING", art["artifact_id"])
            data = store.get(art["artifact_id"])
            if len(data) != art["byte_length"]:
                raise ValidationError("E-HASH-MISMATCH", f"{art['artifact_id']} length mismatch")

    for link in catalog.all("RetrievalArtifact"):
        catalog.get("Retrieval", link["retrieval_id"])
        _require_hash(link["artifact_id"], "retrieval artifact")

    for triage in catalog.all("TriageAssessment"):
        ret = catalog.get("Retrieval", triage["retrieval_id"])
        if is_snippet_kind(ret["access_kind"]) and triage["tier"] != "D":
            raise ValidationError("E-SNIPPET-TIER", f"{triage['assessment_id']} snippet must be Tier D")

    for origin in catalog.all("OriginAssessment"):
        catalog.get("Source", origin["source_a"])
        catalog.get("Source", origin["source_b"])

    for snapshot in catalog.all("CollectionSnapshot"):
        catalog.get("SearchCampaign", snapshot["campaign_id"])
        for rid in snapshot["retrieval_ids"]:
            catalog.get("Retrieval", rid)
        for aid in snapshot["artifact_ids"]:
            catalog.get("Artifact", aid) if any(
                a["artifact_id"] == aid for a in catalog.all("Artifact")
            ) else (_ for _ in ()).throw(ValidationError("E-DANGLING-REF", aid))
        expected = object_hash(
            {
                "campaign_id": snapshot["campaign_id"],
                "search_run_ids": sorted_ids(snapshot["search_run_ids"]),
                "hit_ids": sorted_ids(snapshot["hit_ids"]),
                "retrieval_ids": sorted_ids(snapshot["retrieval_ids"]),
                "artifact_ids": sorted_ids(snapshot["artifact_ids"]),
                "triage_assessment_ids": sorted_ids(snapshot["triage_assessment_ids"]),
                "origin_assessment_ids": sorted_ids(snapshot["origin_assessment_ids"]),
            }
        )
        if snapshot["snapshot_hash"] != expected:
            raise ValidationError("E-HASH-MISMATCH", f"{snapshot['snapshot_id']} snapshot_hash mismatch")
        # collection layer must not contain claims
        if catalog.all("Claim") and snapshot.get("contains_claims"):
            raise ValidationError("E-COLLECTION-CLAIM", "collection snapshot must not contain claims")

    if any("claim_id" in obj for obj in catalog.all("SearchCampaign")):
        raise ValidationError("E-COLLECTION-CLAIM", "campaign must not produce claims")


def _origin_relation(catalog: Catalog, src_a: str, src_b: str) -> str:
    if src_a == src_b:
        return "SAME_ORIGIN"
    for orig in catalog.all("OriginAssessment"):
        pair = {orig["source_a"], orig["source_b"]}
        if pair == {src_a, src_b}:
            return orig["relation"]
    classes = platform_classes(catalog.all("Source"))
    if classes.get(src_a) and classes.get(src_a) == classes.get(src_b):
        return "SAME_ORIGIN"
    return "UNKNOWN"


def _support_ok(catalog: Catalog, claim: dict[str, Any]) -> None:
    metas = []
    for sup in claim["support"]:
        ret = catalog.get("Retrieval", sup["retrieval_id"])
        seg = catalog.get("Segment", sup["segment_id"])
        art = catalog.get("Artifact", sup["artifact_id"])
        if seg["normalized_text_hash"] != sup["normalized_text_hash"]:
            raise ValidationError("E-TEXT-HASH", f"{claim['claim_id']} normalized_text_hash mismatch")
        if not is_real_sha256(sup["artifact_id"]):
            raise ValidationError("E-PLACEHOLDER-HASH", f"{claim['claim_id']} artifact hash")
        _no_forbidden(claim["statement"], claim["claim_id"])
        _no_forbidden(str(claim.get("profile_payload") or {}), claim["claim_id"])
        triage = next(
            (t for t in catalog.all("TriageAssessment") if t["retrieval_id"] == ret["retrieval_id"]),
            None,
        )
        if triage is None:
            raise ValidationError("E-DANGLING-REF", f"{claim['claim_id']} missing triage")
        metas.append({"retrieval": ret, "triage": triage, "source_id": ret["source_id"], "artifact": art, "segment": seg})
    if claim["status"] != "ACTIVE":
        if claim["grade"] == "CONFIRMED":
            raise ValidationError("E-DEAD-CONFIRMED", f"{claim['claim_id']} non-ACTIVE still CONFIRMED")
        return
    usable = [
        m
        for m in metas
        if not is_snippet_kind(m["retrieval"]["access_kind"]) and m["triage"]["tier"] != "D"
    ]
    if claim["kind"] == "ORIGINAL_FACT":
        if claim["grade"] == "SUPPORTED":
            if not any(m["triage"]["tier"] in {"A", "B"} for m in usable):
                raise ValidationError("E-TIER-D-SUPPORT", f"{claim['claim_id']} SUPPORTED ORIGINAL_FACT needs A/B")
        if claim["grade"] == "CONFIRMED":
            if any(m["triage"]["tier"] == "A" for m in usable):
                return
            bs = [m for m in usable if m["triage"]["tier"] == "B"]
            ok = False
            for i, a in enumerate(bs):
                for b in bs[i + 1 :]:
                    if a["source_id"] == b["source_id"]:
                        continue
                    rel = _origin_relation(catalog, a["source_id"], b["source_id"])
                    if rel == "UNKNOWN":
                        raise ValidationError("E-UNKNOWN-ORIGIN", f"{claim['claim_id']} UNKNOWN cannot confirm")
                    if independent_pair(rel):
                        ok = True
            if not ok:
                raise ValidationError("E-NOT-INDEPENDENT", f"{claim['claim_id']} CONFIRMED needs independent dual B or A")
    if claim["kind"] == "RECEPTION" and claim["grade"] == "CONFIRMED":
        cs = [m for m in usable if m["triage"]["tier"] == "C"]
        ok = False
        for i, a in enumerate(cs):
            for b in cs[i + 1 :]:
                rel = _origin_relation(catalog, a["source_id"], b["source_id"])
                if independent_pair(rel):
                    ok = True
        if not ok:
            raise ValidationError("E-NOT-INDEPENDENT", f"{claim['claim_id']} RECEPTION CONFIRMED needs independent C")


def validate_evidence(catalog: Catalog, store: ArtifactStore | None = None) -> None:
    for kind in ("ParseRun", "ParsedDocument", "Segment", "EvidenceBundle", "ExtractionRun", "Claim"):
        for obj in catalog.all(kind):
            validate_typed(kind, obj)

    for parse in catalog.all("ParseRun"):
        catalog.get("Artifact", parse["input_artifact_id"])
        if parse.get("output_document_id"):
            catalog.get("ParsedDocument", parse["output_document_id"])

    for seg in catalog.all("Segment"):
        catalog.get("ParsedDocument", seg["document_id"])
        _require_hash(seg["normalized_text_hash"], seg["segment_id"])
        if not seg.get("source_locator"):
            raise ValidationError("E-LOCATOR", f"{seg['segment_id']} missing source_locator")

    for bundle in catalog.all("EvidenceBundle"):
        catalog.get("ResearchRequest", bundle["request_id"])
        for sid in bundle["segment_ids"]:
            catalog.get("Segment", sid)
        for rid in bundle.get("retrieval_ids") or []:
            catalog.get("Retrieval", rid)
        for aid in bundle.get("artifact_ids") or []:
            art = catalog.get("Artifact", aid)
            if bundle["status"] in {"FROZEN", "EXTRACTED", "EXPORTED"} and art["durability_status"] == "EPHEMERAL":
                raise ValidationError("E-EPHEMERAL", f"{bundle['bundle_id']} cites EPHEMERAL {aid}")
        expected = bundle_hash(catalog, bundle)
        if bundle["bundle_hash"] != expected:
            raise ValidationError("E-HASH-MISMATCH", f"{bundle['bundle_id']} bundle_hash mismatch")
        if bundle["status"] in {"FROZEN", "EXTRACTED", "EXPORTED"}:
            catalog.frozen_bundle_ids.add(bundle["bundle_id"])

    for run in catalog.all("ExtractionRun"):
        bundle = catalog.get("EvidenceBundle", run["bundle_id"])
        if bundle["status"] == "DRAFT":
            raise ValidationError("E-FROZEN", f"{run['extraction_run_id']} bound to DRAFT bundle")
        if run["bundle_hash"] != bundle["bundle_hash"]:
            raise ValidationError("E-BUNDLE-BIND", f"{run['extraction_run_id']} bundle_hash does not match frozen bundle")
        allowed = set(run["input_manifest"]["segment_ids"])
        if allowed - set(bundle["segment_ids"]):
            raise ValidationError("E-OUT-OF-BUNDLE", f"{run['extraction_run_id']} manifest cites extra segments")

    for claim in catalog.all("Claim"):
        if claim["status"] == "ACTIVE" and not claim.get("support"):
            raise ValidationError("E-NO-SEGMENT", f"{claim['claim_id']} live claim needs support")
        _support_ok(catalog, claim)
        for sup in claim["support"]:
            if catalog.all("EvidenceBundle"):
                bundle = catalog.all("EvidenceBundle")[0]
                if sup["segment_id"] not in bundle["segment_ids"]:
                    raise ValidationError("E-OUT-OF-BUNDLE", f"{claim['claim_id']} segment not in bundle")


def bundle_hash(catalog: Catalog, bundle: dict[str, Any]) -> str:
    segs = []
    for sid in sorted_ids(bundle["segment_ids"]):
        seg = catalog.get("Segment", sid)
        segs.append({"segment_id": sid, "normalized_text_hash": seg["normalized_text_hash"]})
    payload = {
        "segment_ids": segs,
        "retrieval_ids": sorted_ids(bundle.get("retrieval_ids") or []),
        "artifact_ids": sorted_ids(bundle.get("artifact_ids") or []),
        "triage_assessment_ids": sorted_ids(bundle.get("triage_assessment_ids") or []),
        "origin_assessment_ids": sorted_ids(bundle.get("origin_assessment_ids") or []),
        "profile_id": bundle["profile_id"],
        "policy_bundle_hash": bundle["policy_bundle_hash"],
        "selection_manifest": bundle["selection_manifest"],
    }
    return object_hash(payload)


def validate_qualification(catalog: Catalog) -> None:
    for kind in ("ExtractorBuild", "QualificationRun", "AssuranceRecord"):
        for obj in catalog.all(kind):
            if kind in SCHEMA_BY_TYPE:
                validate_typed(kind, obj)
    registry = {b["extractor_build_id"]: b for b in catalog.all("ExtractorBuild")}
    for q in catalog.all("QualificationRun"):
        build = registry.get(q["extractor_build_id"])
        if not build:
            raise ValidationError("E-BUILD-REGISTRY", f"{q['qualification_run_id']} build not in registry")
        if q.get("run_a") and q.get("run_b") and q["run_a"] == q["run_b"]:
            raise ValidationError("E-RUN-PAIR", "run_a and run_b must be distinct")
        if q.get("run_a_hash"):
            _require_hash(q["run_a_hash"], "run_a_hash")
        if q.get("run_b_hash"):
            _require_hash(q["run_b_hash"], "run_b_hash")
        if q["result"] == "PASS":
            if q["adversarial_project_expectation"] != "PASS" or q["source_content_injection"] != "PASS":
                raise ValidationError("E-ADV-FAIL", "PASS qualification requires both adversarial fixtures PASS")
            if q["reproducibility"] != "PASS":
                raise ValidationError("E-REPRO", "PASS qualification requires reproducibility PASS")
            if build["status"] == "INVALIDATED":
                raise ValidationError("E-BUILD-INVALID", "invalidated build cannot newly qualify")
    for export in catalog.all("EvidenceExport"):
        level = export.get("assurance", {}).get("level")
        if level not in {None, "UNQUALIFIED"}:
            build_id = export["producer"]["extractor_build_id"]
            build = registry.get(build_id)
            if not build or build["status"] != "QUALIFIED":
                raise ValidationError("E-BUILD-REGISTRY", "export requires QUALIFIED extractor build")


def validate_export(catalog: Catalog, store: ArtifactStore | None = None) -> None:
    for export in catalog.all("EvidenceExport"):
        validate_typed("EvidenceExport", export)
        _require_hash(export["export_hash"], "export_hash")
        expected = object_hash(export, omit=("export_hash",))
        if export["export_hash"] != expected:
            raise ValidationError("E-EXPORT-TAMPER", f"{export['export_id']} export_hash mismatch")
        producer = export["producer"]
        for key in ("repository_commit", "collector_build_id", "parser_build_id", "extractor_build_id"):
            if not producer.get(key):
                raise ValidationError("E-PRODUCER", f"missing producer.{key}")
        if not export.get("policies"):
            raise ValidationError("E-POLICY-HASH", "export missing policies")
        bundle = catalog.get("EvidenceBundle", export["bundle"]["bundle_id"])
        if export["bundle"]["bundle_hash"] != bundle["bundle_hash"]:
            raise ValidationError("E-BUNDLE-BIND", "export bundle_hash does not match bundle")
        _no_forbidden(str(export.get("scene_facts") or {}), "scene_facts")
        if "element_mapping" in (export.get("scene_facts") or {}):
            raise ValidationError("E-PROJECT-LEAK", "export scene_facts must not include element_mapping")
        for claim in export["claims"]:
            _no_forbidden(str(claim), "export claim")
        audit = export.get("assurance", {}).get("auditability", "FULL")
        for item in export["artifact_manifest"]:
            _require_hash(item["artifact_id"], "manifest")
            if item["durability_status"] == "EPHEMERAL" and audit == "FULL":
                raise ValidationError("E-EPHEMERAL", "FULL auditability cannot depend on EPHEMERAL artifacts")
            if store and not store.exists(item["artifact_id"]):
                if audit == "FULL":
                    raise ValidationError("E-ARTIFACT-MISSING", f"export missing {item['artifact_id']}")
        if export.get("assurance", {}).get("level") == "UNQUALIFIED" and export.get("formal"):
            raise ValidationError("E-ASSURANCE", "formal export cannot be UNQUALIFIED")


def validate_all(catalog: Catalog, store: ArtifactStore | None = None) -> None:
    validate_collection(catalog, store)
    validate_evidence(catalog, store)
    validate_qualification(catalog)
    if catalog.all("EvidenceExport"):
        validate_export(catalog, store)
