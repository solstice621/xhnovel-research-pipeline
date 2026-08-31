from __future__ import annotations

import re
from typing import Any

from .access import is_snippet_kind, looks_like_snippet_label, normalize_access_kind
from .catalog import Catalog
from .collection_quality import collection_review_gate, validate_collection_quality_records
from .constants import FORBIDDEN_EXPORT_TOKENS, PARSER_BUILD_ID, SCHEMA_VERSION
from .errors import ValidationError
from .hashing import collection_snapshot_hash, is_real_sha256, object_hash, sorted_ids
from .ids import derived_id
from .novel_assessment import (
    find_bound_chapter_identity_review,
    find_bound_triage_review,
    reviewed_triage_assessment,
    validate_bound_chapter_identity_review,
)
from .parse import parse_artifact, parser_build_id_for, text_hash
from .build_identity import BUILD_IDENTITY_FIELDS, build_source_hash
from .paths import repo_root
from .schema import SCHEMA_BY_TYPE, validate_profile_payload, validate_schema
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
        if "quality_gate" in snapshot:
            decision_ids = set(snapshot["collection_decision_ids"])
            review_ids = set(snapshot["collection_review_ids"])
            decisions = [catalog.get("CollectionDecision", ident) for ident in decision_ids]
            reviews = [catalog.get("CollectionReview", ident) for ident in review_ids]
            required_collectors = [
                decision["decision_id"]
                for decision in decisions
                if decision["assessor_role"] == "COLLECTOR"
            ]
            expected_review_ids = {
                review["review_id"]
                for review in catalog.all("CollectionReview")
                if review["collector_decision_id"] in required_collectors
            }
            if review_ids != expected_review_ids:
                raise ValidationError("E-REVIEW-BIND", f"{snapshot['snapshot_id']} review set mismatch")
            referenced_decision_ids = {
                ident
                for review in reviews
                for ident in (review["collector_decision_id"], review["reviewer_decision_id"])
            }
            if decision_ids != referenced_decision_ids:
                raise ValidationError("E-REVIEW-BIND", f"{snapshot['snapshot_id']} decision set mismatch")
            quality_artifacts = {snapshot["quality_policy_artifact_id"]}
            for decision in decisions:
                quality_artifacts.update(decision["input_artifact_ids"])
                quality_artifacts.add(decision["output_artifact_id"])
                for field in ("model_request_artifact_id", "provider_response_artifact_id"):
                    if decision.get(field):
                        quality_artifacts.add(decision[field])
            quality_artifacts.update(review["rubric_artifact_id"] for review in reviews)
            if quality_artifacts - snapshot_artifact_ids:
                raise ValidationError("E-OUT-OF-SNAPSHOT", f"{snapshot['snapshot_id']} quality artifacts missing")
            expected_gate = collection_review_gate(catalog, store, required_collectors)
            if snapshot["quality_gate"] != expected_gate or expected_gate["result"] != "PASS":
                raise ValidationError("E-REVIEW-GATE", f"{snapshot['snapshot_id']} quality gate is not PASS")
        expected = collection_snapshot_hash(snapshot)
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
        if campaign.get("stop_reason") == "coverage_reached":
            raise ValidationError(
                "E-STOP-REASON",
                "standalone novel ingestion does not claim open-web coverage",
            )

    if any("claim_id" in obj for obj in catalog.all("SearchCampaign")):
        raise ValidationError("E-COLLECTION-CLAIM", "campaign must not produce claims")

    validate_collection_quality_records(catalog, store)


def _support_ok(
    catalog: Catalog,
    claim: dict[str, Any],
    run: dict[str, Any],
    bundle: dict[str, Any],
) -> None:
    metas = []
    bundle_triage_ids = set(bundle.get("triage_assessment_ids") or [])
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
            raise ValidationError(
                "E-UNQUALIFIED-CONFIRMATION",
                "standalone model extraction cannot emit CONFIRMED claims",
            )
    if claim["kind"] == "RECEPTION" and claim["grade"] == "CONFIRMED":
        raise ValidationError(
            "E-UNQUALIFIED-CONFIRMATION",
            "standalone model extraction cannot emit CONFIRMED claims",
        )
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
                    artifact_bytes = store.get(artifact["artifact_id"])
                    expected_parser_build = parser_build_id_for(
                        parse["parameters"].get("media_type", artifact["media_type"]),
                        artifact_bytes,
                    )
                    if parse["parser_build_id"] != expected_parser_build:
                        raise ValidationError("E-PARSE-REPLAY", "no replay runner for parser build")
                    replayed = parse_artifact(
                        artifact["artifact_id"],
                        artifact_bytes,
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
        request = catalog.get("ResearchRequest", bundle["request_id"])
        selection = request.get("search_constraints", {}).get("novel_selection")
        if selection is not None:
            if not isinstance(selection, dict):
                raise ValidationError("E-NOVEL-SOURCE-BIND", "novel_selection must be an object")
            resolution = catalog.get("NovelSourceResolution", selection.get("resolution_id", ""))
            expected_selection = {
                "ranking_run_id": resolution["ranking_run_id"],
                "resolution_id": resolution["resolution_id"],
                "candidate_id": resolution["candidate_id"],
                "candidate_rank": resolution["candidate_rank"],
                "candidate_title": resolution["candidate_title"],
                "source_spec_hash": resolution["source_spec_hash"],
            }
            if selection != expected_selection:
                raise ValidationError("E-NOVEL-SOURCE-BIND", "request selection differs from resolution")
            ingestion_matches = [
                run
                for run in catalog.all("NovelIngestionRun")
                if run["input_spec_hash"] == resolution["source_spec_hash"]
            ]
            if len(ingestion_matches) != 1:
                raise ValidationError(
                    "E-NOVEL-SOURCE-BIND", "selected source must bind exactly one ingestion run"
                )
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
        quality_snapshots = [snapshot for snapshot in snapshots if "quality_gate" in snapshot]
        bound_review_ids = sorted_ids(
            review_id
            for snapshot in quality_snapshots
            for review_id in snapshot["collection_review_ids"]
        )
        if quality_snapshots:
            if bundle["selection_manifest"].get("collection_review_ids") != bound_review_ids:
                raise ValidationError(
                    "E-REVIEW-BIND", f"{bundle['bundle_id']} does not bind snapshot reviews"
                )
            if bundle["selection_manifest"].get("quality_gate_result") != "PASS":
                raise ValidationError("E-REVIEW-GATE", f"{bundle['bundle_id']} quality gate is not PASS")
        selected_chapter_ids = bundle["selection_manifest"].get("selected_chapter_ids", [])
        if not isinstance(selected_chapter_ids, list) or len(selected_chapter_ids) != len(
            set(selected_chapter_ids)
        ):
            raise ValidationError(
                "E-CHAPTER-IDENTITY-BIND",
                f"{bundle['bundle_id']} has an invalid selected chapter partition",
            )
        if selected_chapter_ids:
            if store is None:
                raise ValidationError(
                    "E-STORE",
                    "novel chapter identity validation requires an ArtifactStore",
                )
            for chapter_id in selected_chapter_ids:
                chapter = catalog.get("NovelChapter", chapter_id)
                segments = [
                    catalog.get("Segment", segment_id) for segment_id in chapter["segment_ids"]
                ]
                identity_review = find_bound_chapter_identity_review(
                    catalog,
                    bound_review_ids,
                    chapter_id,
                )
                validate_bound_chapter_identity_review(
                    catalog,
                    store,
                    chapter,
                    segments,
                    identity_review,
                )
        for retrieval_id in bundle.get("retrieval_ids") or []:
            retrieval = catalog.get("Retrieval", retrieval_id)
            hit_id = retrieval.get("discovery_hit_id")
            if hit_id and hit_id not in expected_selected:
                raise ValidationError("E-SELECTION-MANIFEST", f"{retrieval_id} comes from an unselected hit")
            if retrieval.get("access_kind") == "full_text_chapter":
                triage_id = retrieval.get("triage_assessment_id")
                if not triage_id or triage_id not in set(bundle.get("triage_assessment_ids") or []):
                    raise ValidationError(
                        "E-NOVEL-TRIAGE-BIND",
                        f"{retrieval_id} lacks its reviewed TriageAssessment in the bundle",
                    )
                assessment = catalog.get("TriageAssessment", triage_id)
                review = find_bound_triage_review(catalog, bound_review_ids, retrieval_id)
                expected_assessment = reviewed_triage_assessment(
                    catalog,
                    retrieval,
                    review,
                    policy_hash=bundle["policy_bundle_hash"],
                    assessed_at=assessment["assessed_at"],
                )
                if assessment != expected_assessment:
                    raise ValidationError(
                        "E-NOVEL-TRIAGE-BIND",
                        f"{triage_id} does not match its bound TRIAGE review and access cap",
                    )
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

    if any(
        run["execution_environment"]["executor_build_id"] == "openai-responses-v1"
        for run in catalog.all("ExtractionRun")
    ):
        from .plot_extraction import validate_model_plot_extractions

        if store is None:
            raise ValidationError("E-STORE", "model extraction replay requires an ArtifactStore")
        validate_model_plot_extractions(catalog, store)

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
    snapshot_decision_ids = {
        ident for snapshot in snapshots for ident in snapshot.get("collection_decision_ids", [])
    }
    snapshot_review_ids = {
        ident for snapshot in snapshots for ident in snapshot.get("collection_review_ids", [])
    }
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
        "collection_decisions": records("CollectionDecision", list(snapshot_decision_ids)),
        "collection_reviews": records("CollectionReview", list(snapshot_review_ids)),
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


def validate_model_builds(catalog: Catalog) -> None:
    for build in catalog.all("ExtractorBuild"):
        validate_typed("ExtractorBuild", build)
        identity = {key: build[key] for key in BUILD_IDENTITY_FIELDS}
        if build["extractor_build_id"] != derived_id("ExtractorBuild", identity):
            raise ValidationError(
                "E-ID-BIND",
                f"{build['extractor_build_id']} does not match build content",
            )
        _require_hash(build.get("source_tree_hash"), "source_tree_hash")
        if build["source_tree_hash"] != build_source_hash(repo_root()):
            raise ValidationError(
                "E-BUILD-BIND",
                f"{build['extractor_build_id']} executable source tree changed",
            )
        if build.get("status") != "UNQUALIFIED":
            raise ValidationError(
                "E-BUILD-STATUS",
                "standalone model builds remain UNQUALIFIED until a separate qualification gate exists",
            )



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
        plot_analysis_id = (export.get("scene_facts") or {}).get("plot_analysis_id")
        analysis = None
        if plot_analysis_id:
            analysis = catalog.get("PlotAnalysis", plot_analysis_id)
            expected_scene_facts = {
                "plot_analysis_id": analysis["analysis_id"],
                "timeline": analysis["timeline"],
                "event_groups": analysis["event_groups"],
                "key_events": analysis["key_events"],
                "alias_groups": analysis["alias_groups"],
            }
            if export["scene_facts"] != expected_scene_facts:
                raise ValidationError("E-PLOT-BIND", "export scene facts differ from plot analysis")
            analysis_run = catalog.get("ExtractionRun", analysis["extraction_run_id"])
            if analysis_run["bundle_id"] != bundle["bundle_id"]:
                raise ValidationError("E-PLOT-BIND", "plot analysis belongs to another bundle")
            snapshot_ids = set(bundle["collection_snapshot_ids"])
            decision_ids = {
                decision_id
                for snapshot in catalog.all("CollectionSnapshot")
                if snapshot["snapshot_id"] in snapshot_ids
                for decision_id in snapshot.get("collection_decision_ids", [])
            }
            collection_build_ids = sorted(
                {
                    catalog.get("CollectionDecision", decision_id)["assessor_build_id"]
                    for decision_id in decision_ids
                }
            )
            parser_build_ids = sorted(
                {
                    parse_run["parser_build_id"]
                    for parse_run in catalog.all("ParseRun")
                    if parse_run.get("output_document_id") in set(bundle["document_ids"])
                }
            )
            expected_collector = "collection-set-" + object_hash(
                {"build_ids": collection_build_ids}, omit=()
            ).removeprefix("sha256:")[:20]
            expected_parser = "parser-set-" + object_hash(
                {"build_ids": parser_build_ids}, omit=()
            ).removeprefix("sha256:")[:20]
            if (
                producer["collector_build_id"] != expected_collector
                or producer["parser_build_id"] != expected_parser
            ):
                raise ValidationError("E-PRODUCER", "producer build sets differ from bundle lineage")
        if analysis is not None:
            catalog_claims = {
                claim_id: catalog.get("Claim", claim_id)
                for claim_id in analysis["claim_ids"]
            }
            if any(
                claim["status"] != "ACTIVE"
                or claim["extraction_run_id"] != analysis["extraction_run_id"]
                for claim in catalog_claims.values()
            ):
                raise ValidationError("E-CLAIM-BIND", "plot export claims differ from analysis")
        else:
            catalog_claims = {
                claim["claim_id"]: claim
                for claim in catalog.all("Claim")
                if claim["status"] == "ACTIVE"
            }
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
        if analysis is not None and audit != "DEGRADED":
            raise ValidationError(
                "E-AUDITABILITY",
                "model-backed novel exports must remain DEGRADED until failed calls and "
                "automatic retry attempts have immutable run records",
            )
        manifest_ids = [item["artifact_id"] for item in export["artifact_manifest"]]
        if analysis is not None:
            from .plot_analysis import plot_analysis_artifact_ids

            expected_artifact_ids = set(
                plot_analysis_artifact_ids(catalog, bundle, analysis)
            )
        else:
            expected_artifact_ids = {
                artifact["artifact_id"] for artifact in catalog.all("Artifact")
            }
        if len(manifest_ids) != len(set(manifest_ids)) or set(manifest_ids) != expected_artifact_ids:
            raise ValidationError("E-ARTIFACT-BIND", "artifact manifest does not exactly match catalog artifacts")
        for item in export["artifact_manifest"]:
            _require_hash(item["artifact_id"], "manifest")
            art = catalog.get("Artifact", item["artifact_id"])
            if item["byte_length"] != art["byte_length"] or item["durability_status"] != art["durability_status"]:
                raise ValidationError("E-ARTIFACT-BIND", "artifact manifest differs from catalog")
            if item["durability_status"] == "EPHEMERAL" and audit == "FULL":
                raise ValidationError("E-EPHEMERAL", "FULL auditability cannot depend on EPHEMERAL artifacts")
            if store:
                if store.exists(item["artifact_id"]):
                    data = store.get(item["artifact_id"])
                    if len(data) != item["byte_length"]:
                        raise ValidationError(
                            "E-HASH-MISMATCH",
                            f"export artifact length differs from CAS: {item['artifact_id']}",
                        )
                elif (
                    item["durability_status"] != "EPHEMERAL"
                    or item.get("availability") == "AVAILABLE"
                ):
                    raise ValidationError(
                        "E-ARTIFACT-MISSING",
                        f"export missing {item['artifact_id']}",
                    )
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
    validate_model_builds(catalog)
    if catalog.all("EvidenceExport"):
        validate_export(catalog, store)
    if any(
        catalog.all(kind)
        for kind in ("NovelWork", "NovelRankingRun", "NovelSourceResolution", "PlotAnalysis")
    ):
        if store is None:
            raise ValidationError("E-STORE", "novel workflow validation requires an ArtifactStore")
        from .novel_ingest import validate_novel_ingestion
        from .novel_selection import validate_source_resolutions
        from .plot_analysis import validate_plot_analysis
        from .ranking import validate_fame_ranking

        validate_novel_ingestion(catalog, store)
        validate_fame_ranking(catalog, store)
        validate_source_resolutions(catalog, store)
        validate_plot_analysis(catalog, store)
