from __future__ import annotations

import re
from typing import Any

from .access import is_snippet_kind, looks_like_snippet_label, normalize_access_kind
from .catalog import Catalog
from .collection_quality import validate_collection_quality_records
from .constants import FORBIDDEN_EXPORT_TOKENS, PARSER_BUILD_ID, SCHEMA_VERSION
from .errors import ValidationError
from .extraction import mock_extract
from .hashing import is_real_sha256, object_hash, sorted_ids
from .ids import derived_id
from .origin import independent_pair, platform_classes
from .parse import parse_artifact, text_hash
from .paths import repo_root
from .qualification import (
    BUILD_IDENTITY_FIELDS,
    SUITE_FILES,
    build_source_hash,
    extractor_build_hash,
    file_sha,
    fixture_suite_hash,
    replay_mock_qualification,
    replay_mock_source_injection,
)
from .schema import SCHEMA_BY_TYPE, validate_profile_payload, validate_schema
from .stop import has_two_independent_secondary_sources
from .store import ArtifactStore


def _require_hash(value: object, label: str) -> None:
    if not is_real_sha256(value):
        raise ValidationError("E-PLACEHOLDER-HASH", f"{label} is not a real SHA-256: {value!r}")


def _no_forbidden(value: Any, label: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _no_forbidden(key, label)
            _no_forbidden(item, label)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _no_forbidden(item, label)
        return
    if not isinstance(value, str):
        return
    for token in FORBIDDEN_EXPORT_TOKENS:
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])"
        if re.search(pattern, value):
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
        "CollectionDecision",
        "CollectionReview",
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
        seen_run_ids = {run["search_run_id"]}
        retry_of = run.get("retry_of")
        while retry_of:
            if retry_of in seen_run_ids:
                raise ValidationError("E-RETRY-LINEAGE", f"{run['search_run_id']} has a retry cycle")
            seen_run_ids.add(retry_of)
            previous = catalog.get("SearchRun", retry_of)
            if previous["query_id"] != run["query_id"] or previous["provider_id"] != run["provider_id"]:
                raise ValidationError("E-RETRY-LINEAGE", f"{run['search_run_id']} retries another search")
            if previous["status"] != "FAILED":
                raise ValidationError("E-RETRY-LINEAGE", f"{run['search_run_id']} predecessor was not FAILED")
            retry_of = previous.get("retry_of")
        if run.get("raw_response_artifact_id"):
            _require_hash(run["raw_response_artifact_id"], run["search_run_id"])
            catalog.get("Artifact", run["raw_response_artifact_id"])
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
    source_classes = platform_classes(sources)

    for retrieval in catalog.all("Retrieval"):
        catalog.get("Source", retrieval["source_id"])
        seen_retrieval_ids = {retrieval["retrieval_id"]}
        retry_of = retrieval.get("retry_of")
        while retry_of:
            if retry_of in seen_retrieval_ids:
                raise ValidationError("E-RETRY-LINEAGE", f"{retrieval['retrieval_id']} has a retry cycle")
            seen_retrieval_ids.add(retry_of)
            previous = catalog.get("Retrieval", retry_of)
            if previous["source_id"] != retrieval["source_id"] or previous.get("discovery_hit_id") != retrieval.get(
                "discovery_hit_id"
            ):
                raise ValidationError("E-RETRY-LINEAGE", f"{retrieval['retrieval_id']} retries another retrieval")
            if previous["status"] != "FAILED":
                raise ValidationError("E-RETRY-LINEAGE", f"{retrieval['retrieval_id']} predecessor was not FAILED")
            retry_of = previous.get("retry_of")
        if retrieval.get("discovery_hit_id"):
            catalog.get("DiscoveryHit", retrieval["discovery_hit_id"])
        if retrieval.get("triage_assessment_id"):
            triage = catalog.get("TriageAssessment", retrieval["triage_assessment_id"])
            if triage["retrieval_id"] != retrieval["retrieval_id"]:
                raise ValidationError("E-LINEAGE", f"{retrieval['retrieval_id']} triage points elsewhere")
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
        validate_typed("RetrievalArtifact", link)
        catalog.get("Retrieval", link["retrieval_id"])
        _require_hash(link["artifact_id"], "retrieval artifact")
        catalog.get("Artifact", link["artifact_id"])

    linked_retrieval_ids = {link["retrieval_id"] for link in catalog.all("RetrievalArtifact")}
    for retrieval in catalog.all("Retrieval"):
        if retrieval["status"] == "FETCHED" and retrieval["retrieval_id"] not in linked_retrieval_ids:
            raise ValidationError("E-LINEAGE", f"{retrieval['retrieval_id']} FETCHED without artifact")

    for triage in catalog.all("TriageAssessment"):
        ret = catalog.get("Retrieval", triage["retrieval_id"])
        if is_snippet_kind(ret["access_kind"]) and triage["tier"] != "D":
            raise ValidationError("E-SNIPPET-TIER", f"{triage['assessment_id']} snippet must be Tier D")

    for origin in catalog.all("OriginAssessment"):
        catalog.get("Source", origin["source_a"])
        catalog.get("Source", origin["source_b"])
        if (
            origin["relation"] == "INDEPENDENT"
            and source_classes.get(origin["source_a"])
            == source_classes.get(origin["source_b"])
        ):
            raise ValidationError("E-NOT-INDEPENDENT", f"{origin['assessment_id']} contradicts platform identity")

    for snapshot in catalog.all("CollectionSnapshot"):
        campaign = catalog.get("SearchCampaign", snapshot["campaign_id"])
        snapshot_run_ids = set(snapshot["search_run_ids"])
        snapshot_hit_ids = set(snapshot["hit_ids"])
        snapshot_retrieval_ids = set(snapshot["retrieval_ids"])
        snapshot_artifact_ids = set(snapshot["artifact_ids"])
        for run_id in snapshot["search_run_ids"]:
            run = catalog.get("SearchRun", run_id)
            query = catalog.get("QuerySpec", run["query_id"])
            if query["campaign_id"] != campaign["campaign_id"]:
                raise ValidationError("E-LINEAGE", f"{run_id} is outside snapshot campaign")
            raw_artifact_id = run.get("raw_response_artifact_id")
            if raw_artifact_id and raw_artifact_id not in snapshot_artifact_ids:
                raise ValidationError("E-OUT-OF-SNAPSHOT", f"{run_id} raw response missing from snapshot")
        for hit_id in snapshot["hit_ids"]:
            hit = catalog.get("DiscoveryHit", hit_id)
            if hit["search_run_id"] not in snapshot_run_ids:
                raise ValidationError("E-OUT-OF-SNAPSHOT", f"{hit_id} search run missing from snapshot")
        for rid in snapshot["retrieval_ids"]:
            retrieval = catalog.get("Retrieval", rid)
            hit_id = retrieval.get("discovery_hit_id")
            if hit_id and hit_id not in snapshot_hit_ids:
                raise ValidationError("E-OUT-OF-SNAPSHOT", f"{rid} discovery hit missing from snapshot")
            linked_artifacts = {
                link["artifact_id"]
                for link in catalog.all("RetrievalArtifact")
                if link["retrieval_id"] == rid
            }
            if linked_artifacts - snapshot_artifact_ids:
                raise ValidationError("E-OUT-OF-SNAPSHOT", f"{rid} artifacts missing from snapshot")
        for aid in snapshot["artifact_ids"]:
            catalog.get("Artifact", aid) if any(
                a["artifact_id"] == aid for a in catalog.all("Artifact")
            ) else (_ for _ in ()).throw(ValidationError("E-DANGLING-REF", aid))
        for assessment_id in snapshot["triage_assessment_ids"]:
            assessment = catalog.get("TriageAssessment", assessment_id)
            if assessment["retrieval_id"] not in snapshot_retrieval_ids:
                raise ValidationError("E-OUT-OF-SNAPSHOT", f"{assessment_id} retrieval missing from snapshot")
        for assessment_id in snapshot["origin_assessment_ids"]:
            assessment = catalog.get("OriginAssessment", assessment_id)
            snapshot_source_ids = {
                catalog.get("Retrieval", retrieval_id)["source_id"]
                for retrieval_id in snapshot_retrieval_ids
            }
            if {assessment["source_a"], assessment["source_b"]} - snapshot_source_ids:
                raise ValidationError("E-OUT-OF-SNAPSHOT", f"{assessment_id} sources missing from snapshot")
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

    terminal_reasons = {
        "COMPLETED": {"coverage_reached"},
        "EXHAUSTED": {"provider_exhausted", "no_new_source"},
        "BUDGET_STOPPED": {"budget_exhausted"},
        "FAILED": {"failed"},
        "CANCELLED": {"manual_stop"},
    }
    for campaign in catalog.all("SearchCampaign"):
        if campaign["status"] in terminal_reasons and campaign.get("stop_reason") not in terminal_reasons[campaign["status"]]:
            raise ValidationError("E-STOP-REASON", f"{campaign['campaign_id']} status/reason mismatch")
        if campaign.get("stop_reason") != "coverage_reached":
            continue
        snapshots = [
            snapshot
            for snapshot in catalog.all("CollectionSnapshot")
            if snapshot["campaign_id"] == campaign["campaign_id"]
        ]
        coverage = any(
            has_two_independent_secondary_sources(
                retrievals=[catalog.get("Retrieval", rid) for rid in snapshot["retrieval_ids"]],
                triage_assessments=[
                    catalog.get("TriageAssessment", aid) for aid in snapshot["triage_assessment_ids"]
                ],
                origin_assessments=[
                    catalog.get("OriginAssessment", aid) for aid in snapshot["origin_assessment_ids"]
                ],
                sources=catalog.all("Source"),
            )
            for snapshot in snapshots
        )
        if not coverage:
            raise ValidationError("E-STOP-REASON", f"{campaign['campaign_id']} has no evidence for coverage_reached")

    if any("claim_id" in obj for obj in catalog.all("SearchCampaign")):
        raise ValidationError("E-COLLECTION-CLAIM", "campaign must not produce claims")

    validate_collection_quality_records(catalog, store)


def _origin_relation(
    catalog: Catalog,
    src_a: str,
    src_b: str,
    *,
    allowed_assessment_ids: set[str] | None = None,
) -> str:
    if src_a == src_b:
        return "SAME_ORIGIN"
    classes = platform_classes(catalog.all("Source"))
    if classes.get(src_a) and classes.get(src_a) == classes.get(src_b):
        return "SAME_ORIGIN"
    relations = set()
    for orig in catalog.all("OriginAssessment"):
        if allowed_assessment_ids is not None and orig["assessment_id"] not in allowed_assessment_ids:
            continue
        pair = {orig["source_a"], orig["source_b"]}
        if pair == {src_a, src_b}:
            relations.add(orig["relation"])
    if len(relations) == 1:
        return next(iter(relations))
    if len(relations) > 1:
        return "CONFLICTING"
    return "UNKNOWN"


def _support_ok(
    catalog: Catalog,
    claim: dict[str, Any],
    run: dict[str, Any],
    bundle: dict[str, Any],
) -> None:
    metas = []
    bundle_triage_ids = set(bundle.get("triage_assessment_ids") or [])
    bundle_origin_ids = set(bundle.get("origin_assessment_ids") or [])
    for sup in claim["support"]:
        ret = catalog.get("Retrieval", sup["retrieval_id"])
        seg = catalog.get("Segment", sup["segment_id"])
        art = catalog.get("Artifact", sup["artifact_id"])
        doc = catalog.get("ParsedDocument", seg["document_id"])
        if not any(
            link["retrieval_id"] == ret["retrieval_id"] and link["artifact_id"] == art["artifact_id"]
            for link in catalog.all("RetrievalArtifact")
        ):
            raise ValidationError("E-LINEAGE", f"{claim['claim_id']} missing RetrievalArtifact edge")
        parses = [
            parse
            for parse in catalog.all("ParseRun")
            if parse.get("status") == "SUCCEEDED"
            and parse.get("input_artifact_id") == art["artifact_id"]
            and parse.get("output_document_id") == doc["document_id"]
        ]
        if not parses or doc["input_artifact_id"] != art["artifact_id"]:
            raise ValidationError("E-LINEAGE", f"{claim['claim_id']} artifact does not produce cited segment")
        if sup["retrieval_id"] not in bundle.get("retrieval_ids", []):
            raise ValidationError("E-OUT-OF-BUNDLE", f"{claim['claim_id']} retrieval not in bound bundle")
        if sup["artifact_id"] not in bundle.get("artifact_ids", []):
            raise ValidationError("E-OUT-OF-BUNDLE", f"{claim['claim_id']} artifact not in bound bundle")
        if sup["segment_id"] not in bundle["segment_ids"]:
            raise ValidationError("E-OUT-OF-BUNDLE", f"{claim['claim_id']} segment not in bound bundle")
        if sup["segment_id"] not in run["input_manifest"]["segment_ids"]:
            raise ValidationError("E-OUT-OF-RUN", f"{claim['claim_id']} segment not in extraction input")
        if sup["artifact_id"] not in run["input_manifest"]["allowed_context_artifact_ids"]:
            raise ValidationError("E-OUT-OF-RUN", f"{claim['claim_id']} artifact not in extraction allowlist")
        if doc["document_id"] not in bundle.get("document_ids", []):
            raise ValidationError("E-OUT-OF-BUNDLE", f"{claim['claim_id']} document not in bound bundle")
        actual_text_hash = text_hash(seg["normalized_text"])
        if seg["normalized_text_hash"] != actual_text_hash:
            raise ValidationError("E-TEXT-HASH", f"{seg['segment_id']} normalized text changed")
        if seg["normalized_text_hash"] != sup["normalized_text_hash"]:
            raise ValidationError("E-TEXT-HASH", f"{claim['claim_id']} normalized_text_hash mismatch")
        if not is_real_sha256(sup["artifact_id"]):
            raise ValidationError("E-PLACEHOLDER-HASH", f"{claim['claim_id']} artifact hash")
        _no_forbidden(claim["statement"], claim["claim_id"])
        _no_forbidden(claim.get("profile_payload") or {}, claim["claim_id"])
        triage_id = ret.get("triage_assessment_id")
        if not triage_id or triage_id not in bundle_triage_ids:
            raise ValidationError("E-OUT-OF-BUNDLE", f"{claim['claim_id']} triage not in bound bundle")
        triage = catalog.get("TriageAssessment", triage_id)
        if triage["retrieval_id"] != ret["retrieval_id"]:
            raise ValidationError("E-LINEAGE", f"{claim['claim_id']} triage does not assess retrieval")
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
                    rel = _origin_relation(
                        catalog,
                        a["source_id"],
                        b["source_id"],
                        allowed_assessment_ids=bundle_origin_ids,
                    )
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
                rel = _origin_relation(
                    catalog,
                    a["source_id"],
                    b["source_id"],
                    allowed_assessment_ids=bundle_origin_ids,
                )
                if independent_pair(rel):
                    ok = True
        if not ok:
            raise ValidationError("E-NOT-INDEPENDENT", f"{claim['claim_id']} RECEPTION CONFIRMED needs independent C")
    if claim["kind"] == "RECEPTION" and claim["grade"] == "SUPPORTED":
        if not any(m["triage"]["tier"] == "C" for m in usable):
            raise ValidationError("E-TIER-C-SUPPORT", f"{claim['claim_id']} SUPPORTED RECEPTION needs Tier C")


def validate_evidence(catalog: Catalog, store: ArtifactStore | None = None) -> None:
    for kind in ("ParseRun", "ParsedDocument", "Segment", "EvidenceBundle", "ExtractionRun", "Claim"):
        for obj in catalog.all(kind):
            validate_typed(kind, obj)

    for parse in catalog.all("ParseRun"):
        artifact = catalog.get("Artifact", parse["input_artifact_id"])
        if parse.get("output_document_id"):
            doc = catalog.get("ParsedDocument", parse["output_document_id"])
            if doc["input_artifact_id"] != parse["input_artifact_id"]:
                raise ValidationError("E-LINEAGE", f"{parse['parse_run_id']} document input artifact mismatch")
            if parse["status"] == "SUCCEEDED":
                segments = [s for s in catalog.all("Segment") if s["document_id"] == doc["document_id"]]
                expected = object_hash({"document": doc, "segments": segments})
                if parse.get("output_hash") != expected:
                    raise ValidationError("E-HASH-MISMATCH", f"{parse['parse_run_id']} output_hash mismatch")
                if store:
                    if parse["parser_build_id"] != PARSER_BUILD_ID:
                        raise ValidationError("E-PARSE-REPLAY", "no replay runner for parser build")
                    replayed = parse_artifact(
                        artifact["artifact_id"],
                        store.get(artifact["artifact_id"]),
                        parse["parameters"].get("media_type", artifact["media_type"]),
                        doc["document_id"],
                    )
                    if replayed["document"] != doc or replayed["segments"] != sorted(
                        segments, key=lambda segment: segment["ordinal"]
                    ):
                        raise ValidationError("E-PARSE-REPLAY", f"{parse['parse_run_id']} differs from artifact replay")
        parse_identity = {
            "input_artifact_id": parse["input_artifact_id"],
            "parser_build_id": parse["parser_build_id"],
            "parameters": parse["parameters"],
            "retry_of": parse.get("retry_of"),
            "supersedes": parse.get("supersedes"),
            "status": parse["status"],
        }
        if parse["status"] == "SUCCEEDED":
            parse_identity["output_hash"] = parse["output_hash"]
        if parse["parse_run_id"] != derived_id("ParseRun", parse_identity):
            raise ValidationError("E-ID-BIND", f"{parse['parse_run_id']} does not match parse content")

    for seg in catalog.all("Segment"):
        catalog.get("ParsedDocument", seg["document_id"])
        _require_hash(seg["normalized_text_hash"], seg["segment_id"])
        if seg["normalized_text_hash"] != text_hash(seg["normalized_text"]):
            raise ValidationError("E-TEXT-HASH", f"{seg['segment_id']} normalized text changed")
        if not seg.get("source_locator"):
            raise ValidationError("E-LOCATOR", f"{seg['segment_id']} missing source_locator")

    for bundle in catalog.all("EvidenceBundle"):
        catalog.get("ResearchRequest", bundle["request_id"])
        snapshots = []
        for snapshot_id in bundle["collection_snapshot_ids"]:
            snapshot = catalog.get("CollectionSnapshot", snapshot_id)
            campaign = catalog.get("SearchCampaign", snapshot["campaign_id"])
            if campaign["request_id"] != bundle["request_id"]:
                raise ValidationError("E-REQUEST-BIND", f"{snapshot_id} belongs to another request")
            snapshots.append(snapshot)
        snapshot_retrieval_ids = {ident for snapshot in snapshots for ident in snapshot["retrieval_ids"]}
        snapshot_artifact_ids = {ident for snapshot in snapshots for ident in snapshot["artifact_ids"]}
        snapshot_triage_ids = {ident for snapshot in snapshots for ident in snapshot["triage_assessment_ids"]}
        snapshot_origin_ids = {ident for snapshot in snapshots for ident in snapshot["origin_assessment_ids"]}
        snapshot_hit_ids = {ident for snapshot in snapshots for ident in snapshot["hit_ids"]}
        selected_ids = bundle["selection_manifest"].get("selected_hit_ids")
        rejected_ids = bundle["selection_manifest"].get("rejected_hit_ids")
        if not isinstance(selected_ids, list) or not isinstance(rejected_ids, list):
            raise ValidationError("E-SELECTION-MANIFEST", f"{bundle['bundle_id']} missing hit decisions")
        if len(selected_ids) != len(set(selected_ids)) or len(rejected_ids) != len(set(rejected_ids)):
            raise ValidationError("E-SELECTION-MANIFEST", f"{bundle['bundle_id']} has duplicate hit decisions")
        expected_selected = {
            hit_id
            for hit_id in snapshot_hit_ids
            if catalog.get("DiscoveryHit", hit_id)["selection_status"] == "SELECTED"
        }
        expected_rejected = snapshot_hit_ids - expected_selected
        if set(selected_ids) != expected_selected or set(rejected_ids) != expected_rejected:
            raise ValidationError("E-SELECTION-MANIFEST", f"{bundle['bundle_id']} hit decisions do not match snapshots")
        for retrieval_id in bundle.get("retrieval_ids") or []:
            hit_id = catalog.get("Retrieval", retrieval_id).get("discovery_hit_id")
            if hit_id and hit_id not in expected_selected:
                raise ValidationError("E-SELECTION-MANIFEST", f"{retrieval_id} comes from an unselected hit")
        if set(bundle.get("retrieval_ids") or []) - snapshot_retrieval_ids:
            raise ValidationError("E-OUT-OF-SNAPSHOT", f"{bundle['bundle_id']} cites retrieval outside snapshots")
        if set(bundle.get("artifact_ids") or []) - snapshot_artifact_ids:
            raise ValidationError("E-OUT-OF-SNAPSHOT", f"{bundle['bundle_id']} cites artifact outside snapshots")
        if set(bundle.get("triage_assessment_ids") or []) - snapshot_triage_ids:
            raise ValidationError("E-OUT-OF-SNAPSHOT", f"{bundle['bundle_id']} cites triage outside snapshots")
        if set(bundle.get("origin_assessment_ids") or []) - snapshot_origin_ids:
            raise ValidationError("E-OUT-OF-SNAPSHOT", f"{bundle['bundle_id']} cites origin outside snapshots")
        for document_id in bundle["document_ids"]:
            document = catalog.get("ParsedDocument", document_id)
            if document["input_artifact_id"] not in bundle.get("artifact_ids", []):
                raise ValidationError("E-OUT-OF-BUNDLE", f"{document_id} input artifact not in bundle")
        for sid in bundle["segment_ids"]:
            segment = catalog.get("Segment", sid)
            if segment["document_id"] not in bundle["document_ids"]:
                raise ValidationError("E-OUT-OF-BUNDLE", f"{sid} document not in bundle")
        for rid in bundle.get("retrieval_ids") or []:
            catalog.get("Retrieval", rid)
        for assessment_id in bundle.get("triage_assessment_ids") or []:
            assessment = catalog.get("TriageAssessment", assessment_id)
            if assessment["policy_hash"] != bundle["policy_bundle_hash"]:
                raise ValidationError("E-POLICY-HASH", f"{assessment_id} policy differs from bundle")
        for assessment_id in bundle.get("origin_assessment_ids") or []:
            assessment = catalog.get("OriginAssessment", assessment_id)
            if assessment["policy_hash"] != bundle["policy_bundle_hash"]:
                raise ValidationError("E-POLICY-HASH", f"{assessment_id} policy differs from bundle")
        for aid in bundle.get("artifact_ids") or []:
            art = catalog.get("Artifact", aid)
            if bundle["status"] in {"FROZEN", "EXTRACTED", "EXPORTED"} and art["durability_status"] == "EPHEMERAL":
                raise ValidationError("E-EPHEMERAL", f"{bundle['bundle_id']} cites EPHEMERAL {aid}")
        if bundle["status"] in {"FROZEN", "EXTRACTED", "EXPORTED"}:
            expected = bundle_hash(catalog, bundle)
            if bundle["bundle_hash"] != expected:
                raise ValidationError("E-FROZEN", f"{bundle['bundle_id']} frozen members changed in place")
        expected = bundle_hash(catalog, bundle)
        if bundle["bundle_hash"] != expected:
            raise ValidationError("E-HASH-MISMATCH", f"{bundle['bundle_id']} bundle_hash mismatch")
        if bundle["status"] in {"FROZEN", "EXTRACTED", "EXPORTED"}:
            catalog.frozen_bundle_ids.add(bundle["bundle_id"])

    for run in catalog.all("ExtractionRun"):
        bundle = catalog.get("EvidenceBundle", run["bundle_id"])
        build = catalog.get("ExtractorBuild", run["extractor_build_id"])
        if bundle["status"] == "DRAFT":
            raise ValidationError("E-FROZEN", f"{run['extraction_run_id']} bound to DRAFT bundle")
        if run["bundle_hash"] != bundle["bundle_hash"]:
            raise ValidationError("E-BUNDLE-BIND", f"{run['extraction_run_id']} bundle_hash does not match frozen bundle")
        if bundle["profile_id"] != build["profile_version"]:
            raise ValidationError("E-BUILD-BIND", f"{run['extraction_run_id']} profile differs from build")
        manifest = run["input_manifest"]
        environment = run["execution_environment"]
        if manifest["system_prompt_hash"] != build["prompt_template_hash"]:
            raise ValidationError("E-BUILD-BIND", f"{run['extraction_run_id']} system prompt differs from build")
        if manifest["user_prompt_hash"] != build["prompt_template_hash"]:
            raise ValidationError("E-BUILD-BIND", f"{run['extraction_run_id']} user prompt differs from build")
        if manifest["forbidden_context_policy_hash"] != bundle["policy_bundle_hash"]:
            raise ValidationError("E-POLICY-HASH", f"{run['extraction_run_id']} isolation policy differs from bundle")
        if environment["model_snapshot"] != build["model"]:
            raise ValidationError("E-BUILD-BIND", f"{run['extraction_run_id']} model differs from build")
        if environment["executor_build_id"] != build["executor_build_id"]:
            raise ValidationError("E-BUILD-BIND", f"{run['extraction_run_id']} executor differs from build")
        if environment["parameters"] != build["parameters"]:
            raise ValidationError("E-BUILD-BIND", f"{run['extraction_run_id']} parameters differ from build")
        if environment["tool_policy_hash"] != build["tool_policy_hash"]:
            raise ValidationError("E-BUILD-BIND", f"{run['extraction_run_id']} tool policy differs from build")
        allowed = set(run["input_manifest"]["segment_ids"])
        if allowed - set(bundle["segment_ids"]):
            raise ValidationError("E-OUT-OF-BUNDLE", f"{run['extraction_run_id']} manifest cites extra segments")
        allowed_artifacts = set(run["input_manifest"]["allowed_context_artifact_ids"])
        if allowed_artifacts - set(bundle.get("artifact_ids") or []):
            raise ValidationError("E-OUT-OF-BUNDLE", f"{run['extraction_run_id']} allowlist cites extra artifacts")
        run_identity = {
            key: value
            for key, value in run.items()
            if key not in {"schema_version", "extraction_run_id", "status"}
        }
        if run["extraction_run_id"] != derived_id("ExtractionRun", run_identity):
            raise ValidationError("E-ID-BIND", f"{run['extraction_run_id']} does not match extraction input")

    for claim in catalog.all("Claim"):
        if claim["status"] == "ACTIVE" and not claim.get("support"):
            raise ValidationError("E-NO-SEGMENT", f"{claim['claim_id']} live claim needs support")
        run = catalog.get("ExtractionRun", claim["extraction_run_id"])
        if run["status"] != "SUCCEEDED":
            raise ValidationError("E-EXTRACTION-RUN", f"{claim['claim_id']} bound to non-successful extraction")
        bundle = catalog.get("EvidenceBundle", run["bundle_id"])
        if run["bundle_hash"] != bundle["bundle_hash"]:
            raise ValidationError("E-BUNDLE-BIND", f"{claim['claim_id']} extraction bundle hash mismatch")
        if claim["profile_schema"] != bundle["profile_id"]:
            raise ValidationError("E-PROFILE-SCHEMA", f"{claim['claim_id']} profile differs from bundle")
        validate_profile_payload(claim["profile_schema"], claim["profile_payload"])
        _support_ok(catalog, claim, run, bundle)
        claim_identity = {key: value for key, value in claim.items() if key != "claim_id"}
        if claim["claim_id"] != derived_id("Claim", claim_identity):
            raise ValidationError("E-ID-BIND", f"{claim['claim_id']} does not match claim content")

    for run in catalog.all("ExtractionRun"):
        if run["status"] != "SUCCEEDED":
            continue
        build = catalog.get("ExtractorBuild", run["extractor_build_id"])
        if build["model"] != "mock-deterministic-v1":
            continue
        bundle = catalog.get("EvidenceBundle", run["bundle_id"])
        retrievals_by_doc: dict[str, dict[str, str]] = {}
        for document_id in bundle["document_ids"]:
            document = catalog.get("ParsedDocument", document_id)
            candidates = sorted(
                (
                    {"retrieval_id": retrieval_id, "artifact_id": document["input_artifact_id"]}
                    for retrieval_id in bundle.get("retrieval_ids") or []
                    if any(
                        edge["retrieval_id"] == retrieval_id
                        and edge["artifact_id"] == document["input_artifact_id"]
                        for edge in catalog.all("RetrievalArtifact")
                    )
                ),
                key=lambda candidate: candidate["retrieval_id"],
            )
            if len(candidates) != 1:
                raise ValidationError(
                    "E-EXTRACTION-REPLAY",
                    f"{run['extraction_run_id']} cannot resolve one retrieval for {document_id}",
                )
            retrievals_by_doc[document_id] = candidates[0]
        input_segment_ids = set(run["input_manifest"]["segment_ids"])
        input_segments = [
            segment
            for segment in catalog.all("Segment")
            if segment["segment_id"] in input_segment_ids
        ]
        expected_claims = mock_extract(
            input_segments,
            retrievals_by_doc,
            extraction_run_id=run["extraction_run_id"],
            project_context={},
        )
        actual_claims = [
            claim for claim in catalog.all("Claim") if claim["extraction_run_id"] == run["extraction_run_id"]
        ]
        if actual_claims != expected_claims:
            raise ValidationError("E-EXTRACTION-REPLAY", f"{run['extraction_run_id']} claims differ from replay")

    for bundle in catalog.all("EvidenceBundle"):
        if bundle["bundle_id"] != derived_id("EvidenceBundle", {"bundle_hash": bundle["bundle_hash"]}):
            raise ValidationError("E-ID-BIND", f"{bundle['bundle_id']} does not match bundle content")


def bundle_hash(catalog: Catalog, bundle: dict[str, Any]) -> str:
    def records(kind: str, ids: list[str]) -> list[dict[str, Any]]:
        return [catalog.get(kind, ident) for ident in sorted_ids(ids)]

    snapshots = records("CollectionSnapshot", bundle["collection_snapshot_ids"])
    campaign_ids = {snapshot["campaign_id"] for snapshot in snapshots}
    search_run_ids = {run_id for snapshot in snapshots for run_id in snapshot["search_run_ids"]}
    hit_ids = {hit_id for snapshot in snapshots for hit_id in snapshot["hit_ids"]}
    snapshot_retrieval_ids = {rid for snapshot in snapshots for rid in snapshot["retrieval_ids"]}
    snapshot_artifact_ids = {aid for snapshot in snapshots for aid in snapshot["artifact_ids"]}
    snapshot_triage_ids = {aid for snapshot in snapshots for aid in snapshot["triage_assessment_ids"]}
    snapshot_origin_ids = {aid for snapshot in snapshots for aid in snapshot["origin_assessment_ids"]}
    campaigns = records("SearchCampaign", list(campaign_ids))
    search_runs = records("SearchRun", list(search_run_ids))
    query_ids = {run["query_id"] for run in search_runs}
    request_ids = {bundle["request_id"], *(campaign["request_id"] for campaign in campaigns)}
    segments = records("Segment", bundle["segment_ids"])
    for seg in segments:
        if seg["normalized_text_hash"] != text_hash(seg["normalized_text"]):
            raise ValidationError("E-TEXT-HASH", f"{seg['segment_id']} normalized text changed")
    retrievals = records("Retrieval", bundle.get("retrieval_ids") or [])
    artifacts = records("Artifact", bundle.get("artifact_ids") or [])
    documents = records("ParsedDocument", bundle.get("document_ids") or [])
    retrieval_ids = {r["retrieval_id"] for r in retrievals} | snapshot_retrieval_ids
    artifact_ids = {a["artifact_id"] for a in artifacts} | snapshot_artifact_ids
    retrieval_artifacts = sorted(
        (
            link
            for link in catalog.all("RetrievalArtifact")
            if link["retrieval_id"] in retrieval_ids and link["artifact_id"] in artifact_ids
        ),
        key=lambda link: (link["retrieval_id"], link["artifact_id"], link["role"]),
    )
    document_ids = {d["document_id"] for d in documents}
    parse_runs = sorted(
        (
            parse
            for parse in catalog.all("ParseRun")
            if parse.get("output_document_id") in document_ids
        ),
        key=lambda parse: parse["parse_run_id"],
    )
    triage = records("TriageAssessment", bundle.get("triage_assessment_ids") or [])
    origins = records("OriginAssessment", bundle.get("origin_assessment_ids") or [])
    snapshot_retrievals = records("Retrieval", list(snapshot_retrieval_ids))
    snapshot_origins = records("OriginAssessment", list(snapshot_origin_ids))
    source_ids = {r["source_id"] for r in [*retrievals, *snapshot_retrievals]}
    for origin in [*origins, *snapshot_origins]:
        source_ids.update((origin["source_a"], origin["source_b"]))
    payload = {
        "requests": records("ResearchRequest", list(request_ids)),
        "campaigns": campaigns,
        "query_specs": records("QuerySpec", list(query_ids)),
        "search_runs": search_runs,
        "discovery_hits": records("DiscoveryHit", list(hit_ids)),
        "snapshot_retrievals": snapshot_retrievals,
        "snapshot_artifacts": records("Artifact", list(snapshot_artifact_ids)),
        "snapshot_triage_assessments": records("TriageAssessment", list(snapshot_triage_ids)),
        "snapshot_origin_assessments": snapshot_origins,
        "collection_snapshots": snapshots,
        "sources": records("Source", list(source_ids)),
        "retrievals": retrievals,
        "retrieval_artifacts": retrieval_artifacts,
        "artifacts": artifacts,
        "parse_runs": parse_runs,
        "documents": documents,
        "segments": segments,
        "triage_assessments": triage,
        "origin_assessments": origins,
        "profile_id": bundle["profile_id"],
        "policy_bundle_hash": bundle["policy_bundle_hash"],
        "selection_manifest": bundle["selection_manifest"],
    }
    return object_hash(payload, omit=())


def validate_qualification(catalog: Catalog) -> None:
    for kind in ("ExtractorBuild", "QualificationRun", "AssuranceRecord"):
        for obj in catalog.all(kind):
            if kind in SCHEMA_BY_TYPE:
                validate_typed(kind, obj)
    registry = {b["extractor_build_id"]: b for b in catalog.all("ExtractorBuild")}
    qualification_by_id = {q["qualification_run_id"]: q for q in catalog.all("QualificationRun")}
    for q in catalog.all("QualificationRun"):
        build = registry.get(q["extractor_build_id"])
        if not build:
            raise ValidationError("E-BUILD-REGISTRY", f"{q['qualification_run_id']} build not in registry")
        if q["extractor_build_hash"] != extractor_build_hash(build):
            raise ValidationError("E-BUILD-BIND", f"{q['qualification_run_id']} extractor build identity changed")
        build_identity = {key: build[key] for key in BUILD_IDENTITY_FIELDS}
        if build["extractor_build_id"] != derived_id("ExtractorBuild", build_identity):
            raise ValidationError("E-ID-BIND", f"{build['extractor_build_id']} does not match build content")
        _require_hash(build.get("source_tree_hash"), "source_tree_hash")
        if build["source_tree_hash"] != build_source_hash(repo_root()):
            raise ValidationError("E-BUILD-BIND", f"{q['qualification_run_id']} executable source tree changed")
        if q.get("run_a") and q.get("run_b") and q["run_a"] == q["run_b"]:
            raise ValidationError("E-RUN-PAIR", "run_a and run_b must be distinct")
        if (q["run_a"], q["run_b"]) != SUITE_FILES[:2]:
            raise ValidationError("E-RUN-PAIR", "qualification must use the frozen RUN-A/RUN-B fixtures")
        if q.get("run_a_hash"):
            _require_hash(q["run_a_hash"], "run_a_hash")
        if q.get("run_b_hash"):
            _require_hash(q["run_b_hash"], "run_b_hash")
        root = repo_root()
        for label in ("run_a", "run_b"):
            rel = q[label]
            path = (root / rel).resolve()
            try:
                path.relative_to(root.resolve())
            except ValueError as exc:
                raise ValidationError("E-RUN-PAIR", f"{label} escapes repository") from exc
            if not path.is_file() or file_sha(path) != q[f"{label}_hash"]:
                raise ValidationError("E-HASH-MISMATCH", f"{label} fixture hash mismatch")
        if q["fixture_suite_hash"] != fixture_suite_hash(root):
            raise ValidationError("E-HASH-MISMATCH", "qualification fixture suite changed")
        run_a_result = q["run_a_result"]
        run_b_result = q["run_b_result"]
        if run_a_result["execution_id"] == run_b_result["execution_id"]:
            raise ValidationError("E-RUN-PAIR", "RUN-A and RUN-B must be independent executions")
        if run_a_result["input_hash"] != q["run_a_hash"] or run_b_result["input_hash"] != q["run_b_hash"]:
            raise ValidationError("E-HASH-MISMATCH", "qualification result input hash mismatch")
        for label, result in (("run_a", run_a_result), ("run_b", run_b_result)):
            claim_hash = object_hash({"claims": result["claims"]}, omit=())
            if result["claim_set_hash"] != claim_hash:
                raise ValidationError("E-HASH-MISMATCH", f"{label} claim set hash mismatch")
            expected = object_hash(result, omit=("result_hash",))
            if result["result_hash"] != expected:
                raise ValidationError("E-HASH-MISMATCH", f"{label} result hash mismatch")
        if run_a_result["result_hash"] == run_b_result["result_hash"]:
            raise ValidationError("E-RUN-PAIR", "RUN-A and RUN-B cannot reuse one result record")
        expected_a, expected_b = replay_mock_qualification(root, build)
        if run_a_result != expected_a or run_b_result != expected_b:
            raise ValidationError("E-QUALIFICATION-REPLAY", "qualification results do not match runner replay")
        source_clean, source_injected = replay_mock_source_injection(root, build)
        expected_source_result = (
            "PASS" if source_clean["claim_set_hash"] == source_injected["claim_set_hash"] else "FAIL"
        )
        if q["source_content_injection"] != expected_source_result:
            raise ValidationError("E-QUALIFICATION-REPLAY", "source-injection result does not match runner replay")
        q_payload = {key: value for key, value in q.items() if key != "qualification_run_id"}
        if q["qualification_run_id"] != derived_id("QualificationRun", q_payload):
            raise ValidationError("E-ID-BIND", f"{q['qualification_run_id']} does not match qualification content")
        if q["result"] == "PASS":
            if q["adversarial_project_expectation"] != "PASS" or q["source_content_injection"] != "PASS":
                raise ValidationError("E-ADV-FAIL", "PASS qualification requires both adversarial fixtures PASS")
            if q["reproducibility"] != "PASS":
                raise ValidationError("E-REPRO", "PASS qualification requires reproducibility PASS")
            if run_a_result["claim_set_hash"] != run_b_result["claim_set_hash"]:
                raise ValidationError("E-ADV-FAIL", "project expectation changed the claim set")
            if not run_a_result["claims"]:
                raise ValidationError("E-REPRO", "PASS qualification requires non-empty claims")
            if any("忽略" in claim["statement"] for claim in run_a_result["claims"]):
                raise ValidationError("E-ADV-FAIL", "source instruction leaked into qualification output")
            if build["status"] == "INVALIDATED":
                raise ValidationError("E-BUILD-INVALID", "invalidated build cannot newly qualify")

    assurances = catalog.all("AssuranceRecord")
    for build in registry.values():
        if build["status"] != "QUALIFIED":
            continue
        passing = [
            q
            for q in catalog.all("QualificationRun")
            if q["extractor_build_id"] == build["extractor_build_id"] and q["result"] == "PASS"
        ]
        if not passing:
            raise ValidationError("E-QUALIFICATION-BIND", f"{build['extractor_build_id']} has no PASS qualification")
        if not any(
            rec["subject_type"] == "BUILD"
            and rec["subject_id"] == build["extractor_build_id"]
            and rec["level"] == "BUILD_QUALIFIED"
            and rec.get("qualification_run_id") in {q["qualification_run_id"] for q in passing}
            for rec in assurances
        ):
            raise ValidationError("E-ASSURANCE-BIND", f"{build['extractor_build_id']} lacks qualification assurance")

    for rec in assurances:
        if rec["level"] == "UNQUALIFIED":
            continue
        qid = rec.get("qualification_run_id")
        q = qualification_by_id.get(qid)
        if not q or q["result"] != "PASS":
            raise ValidationError("E-QUALIFICATION-BIND", f"assurance is not backed by PASS qualification {qid!r}")
        if rec["subject_type"] == "BUILD" and rec["subject_id"] != q["extractor_build_id"]:
            raise ValidationError("E-ASSURANCE-BIND", "build assurance subject differs from qualified build")
        if rec["subject_type"] == "BUNDLE":
            if rec["level"] == "BUNDLE_VERIFIED":
                raise ValidationError(
                    "E-ASSURANCE-BIND",
                    "BUNDLE_VERIFIED requires bundle-level dual-run evidence not implemented in this contract",
                )
            run = catalog.get("ExtractionRun", rec.get("extraction_run_id", ""))
            if run["status"] != "SUCCEEDED" or run["bundle_id"] != rec["subject_id"]:
                raise ValidationError("E-ASSURANCE-BIND", "bundle assurance is not backed by its extraction run")
            if run["extractor_build_id"] != q["extractor_build_id"]:
                raise ValidationError("E-ASSURANCE-BIND", "bundle assurance build differs from qualification")
    for export in catalog.all("EvidenceExport"):
        level = export.get("assurance", {}).get("level")
        if level not in {None, "UNQUALIFIED"}:
            build_id = export["producer"]["extractor_build_id"]
            build = registry.get(build_id)
            if not build or build["status"] != "QUALIFIED":
                raise ValidationError("E-BUILD-REGISTRY", "export requires QUALIFIED extractor build")
            qid = export["assurance"].get("qualification_run_id")
            q = qualification_by_id.get(qid)
            if not q or q["result"] != "PASS" or q["extractor_build_id"] != build_id:
                raise ValidationError("E-QUALIFICATION-BIND", "export assurance does not bind a PASS qualification")
            if level == "HUMAN_AUDITED":
                matching_assurance = any(
                    rec["level"] == "HUMAN_AUDITED"
                    and rec.get("qualification_run_id") == qid
                    and (
                        (rec["subject_type"] == "BUNDLE" and rec["subject_id"] == export["bundle"]["bundle_id"])
                        or (rec["subject_type"] == "EXPORT" and rec["subject_id"] == export["export_id"])
                    )
                    and rec["policy_hash"] == export["policies"]["policy_bundle_hash"]
                    for rec in assurances
                )
            else:
                subject_type = "BUNDLE" if level == "BUNDLE_VERIFIED" else "BUILD"
                subject_id = export["bundle"]["bundle_id"] if subject_type == "BUNDLE" else build_id
                matching_assurance = any(
                    rec["subject_type"] == subject_type
                    and rec["subject_id"] == subject_id
                    and rec["level"] == level
                    and rec.get("qualification_run_id") == qid
                    and rec["policy_hash"] == export["policies"]["policy_bundle_hash"]
                    for rec in assurances
                )
            if not matching_assurance:
                raise ValidationError("E-ASSURANCE-BIND", "export assurance record is missing")


def validate_export(catalog: Catalog, store: ArtifactStore | None = None) -> None:
    for export in catalog.all("EvidenceExport"):
        if not export.get("policies"):
            raise ValidationError("E-POLICY-HASH", "export missing policies")
        validate_typed("EvidenceExport", export)
        _require_hash(export["export_hash"], "export_hash")
        expected = object_hash(export, omit=("export_hash",))
        if export["export_hash"] != expected:
            raise ValidationError("E-EXPORT-TAMPER", f"{export['export_id']} export_hash mismatch")
        producer = export["producer"]
        for key in ("repository_commit", "collector_build_id", "parser_build_id", "extractor_build_id"):
            if not producer.get(key):
                raise ValidationError("E-PRODUCER", f"missing producer.{key}")
        bundle = catalog.get("EvidenceBundle", export["bundle"]["bundle_id"])
        if export["bundle"]["bundle_hash"] != bundle["bundle_hash"]:
            raise ValidationError("E-BUNDLE-BIND", "export bundle_hash does not match bundle")
        if export["policies"].get("policy_bundle_hash") != bundle["policy_bundle_hash"]:
            raise ValidationError("E-POLICY-HASH", "export policy hash does not match bundle")
        request = catalog.get("ResearchRequest", bundle["request_id"])
        if export["origin_request"] != request:
            raise ValidationError("E-REQUEST-BIND", "export origin request does not match bundle request")
        build = catalog.get("ExtractorBuild", producer["extractor_build_id"])
        if build.get("repository_commit") != producer["repository_commit"]:
            raise ValidationError("E-PRODUCER", "producer commit does not match extractor build")
        catalog_claims = {claim["claim_id"]: claim for claim in catalog.all("Claim") if claim["status"] == "ACTIVE"}
        export_claims = {claim["claim_id"]: claim for claim in export["claims"]}
        if len(export_claims) != len(export["claims"]) or export_claims != catalog_claims:
            raise ValidationError("E-CLAIM-BIND", "export claims do not match active catalog claims")
        for claim in export["claims"]:
            run = catalog.get("ExtractionRun", claim["extraction_run_id"])
            if run["bundle_id"] != bundle["bundle_id"] or run["extractor_build_id"] != producer["extractor_build_id"]:
                raise ValidationError("E-CLAIM-BIND", "export claim run does not match producer or bundle")
        _no_forbidden(export.get("scene_facts") or {}, "scene_facts")
        if "element_mapping" in (export.get("scene_facts") or {}):
            raise ValidationError("E-PROJECT-LEAK", "export scene_facts must not include element_mapping")
        for claim in export["claims"]:
            _no_forbidden(claim, "export claim")
        audit = export.get("assurance", {}).get("auditability", "FULL")
        manifest_ids = [item["artifact_id"] for item in export["artifact_manifest"]]
        expected_artifact_ids = {artifact["artifact_id"] for artifact in catalog.all("Artifact")}
        if len(manifest_ids) != len(set(manifest_ids)) or set(manifest_ids) != expected_artifact_ids:
            raise ValidationError("E-ARTIFACT-BIND", "artifact manifest does not exactly match catalog artifacts")
        for item in export["artifact_manifest"]:
            _require_hash(item["artifact_id"], "manifest")
            art = catalog.get("Artifact", item["artifact_id"])
            if item["byte_length"] != art["byte_length"] or item["durability_status"] != art["durability_status"]:
                raise ValidationError("E-ARTIFACT-BIND", "artifact manifest differs from catalog")
            if item["durability_status"] == "EPHEMERAL" and audit == "FULL":
                raise ValidationError("E-EPHEMERAL", "FULL auditability cannot depend on EPHEMERAL artifacts")
            if store and not store.exists(item["artifact_id"]):
                if audit == "FULL":
                    raise ValidationError("E-ARTIFACT-MISSING", f"export missing {item['artifact_id']}")
        if export.get("assurance", {}).get("level") == "UNQUALIFIED" and export.get("formal"):
            raise ValidationError("E-ASSURANCE", "formal export cannot be UNQUALIFIED")
        export_identity = {
            key: value for key, value in export.items() if key not in {"export_id", "export_hash"}
        }
        if export["export_id"] != derived_id("EvidenceExport", export_identity):
            raise ValidationError("E-ID-BIND", f"{export['export_id']} does not match export content")


def validate_all(catalog: Catalog, store: ArtifactStore | None = None) -> None:
    validate_collection(catalog, store)
    validate_evidence(catalog, store)
    validate_qualification(catalog)
    if catalog.all("EvidenceExport"):
        validate_export(catalog, store)
