from __future__ import annotations

import json
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
    declared_source_quality,
    deterministic_triage_assessment,
    find_bound_chapter_identity_review,
    find_bound_triage_review,
    resolve_validated_bundle_ingestion,
    rights_for_bundle,
    reviewed_triage_assessment,
    validate_bound_chapter_identity_review,
)
from .novel_ingest import novel_ingestion_artifact_ids
from .parse import parse_artifact, parser_build_id_for, text_hash
from .build_identity import BUILD_IDENTITY_FIELDS, build_source_hash
from .paths import repo_root
from .schema import SCHEMA_BY_TYPE, validate_schema
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
        "Source",
        "Retrieval",
        "TriageAssessment",
        "CollectionDecision",
        "CollectionReview",
        "CollectionSnapshot",
        "Artifact",
    ):
        for obj in catalog.all(kind):
            validate_typed(kind, obj)

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
            raise ValidationError(
                "E-UNSUPPORTED-RECORD",
                "standalone novel retrievals cannot bind retired discovery-hit records",
            )
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
        catalog.get("ResearchRequest", snapshot["request_id"])
        ingestion = catalog.get("NovelIngestionRun", snapshot["ingestion_run_id"])
        snapshot_retrieval_ids = set(snapshot["retrieval_ids"])
        snapshot_artifact_ids = set(snapshot["artifact_ids"])
        ready_chapters = [
            catalog.get("NovelChapter", chapter_id)
            for chapter_id in ingestion["ready_chapter_ids"]
        ]
        expected_retrieval_ids = {chapter["retrieval_id"] for chapter in ready_chapters}
        if (
            any(chapter["work_id"] != ingestion["work_id"] for chapter in ready_chapters)
            or snapshot_retrieval_ids != expected_retrieval_ids
        ):
            raise ValidationError(
                "E-SNAPSHOT-INGESTION-LINEAGE",
                f"{snapshot['snapshot_id']} retrievals do not exactly match its ingestion run",
            )
        expected_triage_ids = {
            catalog.get("Retrieval", retrieval_id).get("triage_assessment_id")
            for retrieval_id in expected_retrieval_ids
        }
        if None in expected_triage_ids or set(snapshot["triage_assessment_ids"]) != expected_triage_ids:
            raise ValidationError(
                "E-SNAPSHOT-INGESTION-LINEAGE",
                f"{snapshot['snapshot_id']} triage set does not exactly close its ingestion run",
            )
        if store is not None:
            ingestion_artifacts = set(novel_ingestion_artifact_ids(catalog, store, ingestion))
            if not ingestion_artifacts <= snapshot_artifact_ids:
                raise ValidationError(
                    "E-SNAPSHOT-INGESTION-LINEAGE",
                    f"{snapshot['snapshot_id']} omits ingestion closure artifacts",
                )
        for rid in snapshot["retrieval_ids"]:
            retrieval = catalog.get("Retrieval", rid)
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
        if "review_completion_gate" in snapshot:
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
            review_artifacts = {snapshot["review_policy_artifact_id"]}
            for decision in decisions:
                review_artifacts.update(decision["input_artifact_ids"])
                review_artifacts.add(decision["output_artifact_id"])
                for field in ("model_request_artifact_id", "provider_response_artifact_id"):
                    if decision.get(field):
                        review_artifacts.add(decision[field])
            review_artifacts.update(review["rubric_artifact_id"] for review in reviews)
            if review_artifacts - snapshot_artifact_ids:
                raise ValidationError("E-OUT-OF-SNAPSHOT", f"{snapshot['snapshot_id']} review artifacts missing")
            expected_gate = collection_review_gate(catalog, store, required_collectors)
            if snapshot["review_completion_gate"] != expected_gate or expected_gate["result"] != "PASS":
                raise ValidationError("E-REVIEW-GATE", f"{snapshot['snapshot_id']} review gate is not PASS")
        expected = collection_snapshot_hash(snapshot)
        if snapshot["snapshot_hash"] != expected:
            raise ValidationError("E-HASH-MISMATCH", f"{snapshot['snapshot_id']} snapshot_hash mismatch")
    validate_collection_quality_records(catalog, store)


def validate_evidence(catalog: Catalog, store: ArtifactStore | None = None) -> None:
    for kind in (
        "ParseRun",
        "ParsedDocument",
        "Segment",
        "EvidenceBundle",
        "SceneWindow",
        "SceneScoutRun",
        "SceneMergeRun",
        "SceneCandidate",
        "ModelAttempt",
    ):
        for obj in catalog.all(kind):
            validate_typed(kind, obj)

    for parse in catalog.all("ParseRun"):
        artifact = catalog.get("Artifact", parse["input_artifact_id"])
        if parse.get("output_document_id"):
            doc = catalog.get("ParsedDocument", parse["output_document_id"])
            if doc["input_artifact_id"] != parse["input_artifact_id"]:
                raise ValidationError("E-LINEAGE", f"{parse['parse_run_id']} document input artifact mismatch")
            if parse["status"] == "SUCCEEDED":
                segments = sorted(
                    (s for s in catalog.all("Segment") if s["document_id"] == doc["document_id"]),
                    key=lambda segment: segment["ordinal"],
                )
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
                    if replayed["document"] != doc or replayed["segments"] != segments:
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
            if snapshot["request_id"] != bundle["request_id"]:
                raise ValidationError("E-REQUEST-BIND", f"{snapshot_id} belongs to another request")
            snapshots.append(snapshot)
        ingestion_ids = {snapshot["ingestion_run_id"] for snapshot in snapshots}
        if len(ingestion_ids) != 1 or store is None:
            raise ValidationError(
                "E-NOVEL-TRIAGE-BIND", "novel bundle must bind one ingestion and its ArtifactStore"
            )
        ingestion = catalog.get("NovelIngestionRun", next(iter(ingestion_ids)))
        rights = rights_for_bundle(
            catalog,
            store,
            bundle,
            require_storage=True,
            require_external_model=True,
        )
        ingestion_spec = json.loads(store.get(ingestion["input_spec_artifact_id"]).decode("utf-8"))
        source_quality = declared_source_quality(ingestion_spec)
        snapshot_retrieval_ids = {ident for snapshot in snapshots for ident in snapshot["retrieval_ids"]}
        snapshot_artifact_ids = {ident for snapshot in snapshots for ident in snapshot["artifact_ids"]}
        snapshot_triage_ids = {ident for snapshot in snapshots for ident in snapshot["triage_assessment_ids"]}
        review_snapshots = [snapshot for snapshot in snapshots if "review_completion_gate" in snapshot]
        bound_review_ids = sorted_ids(
            review_id
            for snapshot in review_snapshots
            for review_id in snapshot["collection_review_ids"]
        )
        if review_snapshots:
            if bundle["selection_manifest"].get("collection_review_ids") != bound_review_ids:
                raise ValidationError(
                    "E-REVIEW-BIND", f"{bundle['bundle_id']} does not bind snapshot reviews"
                )
            if bundle["selection_manifest"].get("review_completion_result") != "PASS":
                raise ValidationError("E-REVIEW-GATE", f"{bundle['bundle_id']} review gate is not PASS")
        manifest = bundle["selection_manifest"]
        selected_chapter_ids = manifest.get("selected_chapter_ids")
        if (
            selected_chapter_ids != ingestion["ready_chapter_ids"]
            or manifest.get("duplicate_chapter_ids") != ingestion["duplicate_chapter_ids"]
            or manifest.get("ignored_chapter_ids") != ingestion["ignored_chapter_ids"]
        ):
            raise ValidationError(
                "E-BUNDLE-SELECTION-CLOSURE",
                f"{bundle['bundle_id']} selection differs from the ingestion partition",
            )
        selected_chapters = [
            catalog.get("NovelChapter", chapter_id) for chapter_id in selected_chapter_ids
        ]
        expected_retrieval_ids = [chapter["retrieval_id"] for chapter in selected_chapters]
        expected_artifact_ids = sorted_ids(
            {chapter["artifact_id"] for chapter in selected_chapters}
        )
        expected_document_ids = list(
            dict.fromkeys(chapter["document_id"] for chapter in selected_chapters)
        )
        expected_segment_ids = [
            segment_id for chapter in selected_chapters for segment_id in chapter["segment_ids"]
        ]
        expected_triage_ids = [
            catalog.get("Retrieval", retrieval_id)["triage_assessment_id"]
            for retrieval_id in expected_retrieval_ids
        ]
        if (
            bundle["retrieval_ids"] != expected_retrieval_ids
            or bundle["artifact_ids"] != expected_artifact_ids
            or bundle["document_ids"] != expected_document_ids
            or bundle["segment_ids"] != expected_segment_ids
            or bundle["triage_assessment_ids"] != expected_triage_ids
        ):
            raise ValidationError(
                "E-BUNDLE-SELECTION-CLOSURE",
                f"{bundle['bundle_id']} members are not induced by selected chapters",
            )
        if selected_chapter_ids and review_snapshots:
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
            if retrieval.get("access_kind") == "full_text_chapter":
                triage_id = retrieval.get("triage_assessment_id")
                if not triage_id or triage_id not in set(bundle.get("triage_assessment_ids") or []):
                    raise ValidationError(
                        "E-NOVEL-TRIAGE-BIND",
                        f"{retrieval_id} lacks its reviewed TriageAssessment in the bundle",
                    )
                assessment = catalog.get("TriageAssessment", triage_id)
                if review_snapshots:
                    review = find_bound_triage_review(catalog, bound_review_ids, retrieval_id)
                    expected_assessment = reviewed_triage_assessment(
                        catalog,
                        retrieval,
                        review,
                        rights=rights,
                        source_quality=source_quality,
                        policy_hash=bundle["policy_bundle_hash"],
                        assessed_at=assessment["assessed_at"],
                    )
                else:
                    expected_assessment = deterministic_triage_assessment(
                        catalog,
                        retrieval,
                        rights=rights,
                        source_quality=source_quality,
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

    from .scene_scout import bundle_chapter_index

    for window in catalog.all("SceneWindow"):
        bundle = catalog.get("EvidenceBundle", window["bundle_id"])
        if bundle["request_id"] != window["request_id"]:
            raise ValidationError("E-SCENE-LINEAGE", "scene window request differs from bundle")
        chapter_by_segment, _ = bundle_chapter_index(catalog, bundle)
        for span in window["source_spans"]:
            if span["segment_id"] not in set(bundle["segment_ids"]):
                raise ValidationError("E-OUT-OF-BUNDLE", "scene window cites an extra segment")
            segment = catalog.get("Segment", span["segment_id"])
            chapter = chapter_by_segment[span["segment_id"]]
            assessment = catalog.get(
                "TriageAssessment",
                catalog.get("Retrieval", chapter["retrieval_id"])["triage_assessment_id"],
            )
            if "event-facts" not in assessment.get("allowed_uses", []):
                raise ValidationError("E-ALLOWED-USE", "scene window cites a non-event-facts source")
            if (
                span["normalized_text_hash"] != segment["normalized_text_hash"]
                or not 0 <= span["start"] < span["end"] <= len(segment["normalized_text"])
            ):
                raise ValidationError("E-SCENE-SPAN", "scene window source span is invalid")
        identity = {
            "request_id": window["request_id"],
            "bundle_id": window["bundle_id"],
            "ordinal": window["ordinal"],
            "source_spans": window["source_spans"],
            "window_chars": window["window_chars"],
            "overlap_chars": window["overlap_chars"],
        }
        expected_hash = object_hash(identity, omit=())
        if (
            window["window_hash"] != expected_hash
            or window["window_id"] != derived_id("SceneWindow", {"window_hash": expected_hash})
            or window["text_length"]
            != sum(span["end"] - span["start"] for span in window["source_spans"])
            or not 0.15 <= window["overlap_chars"] / window["window_chars"] <= 0.20
        ):
            raise ValidationError("E-SCENE-WINDOW", "scene window identity or dimensions differ")

    for run in catalog.all("SceneScoutRun"):
        bundle = catalog.get("EvidenceBundle", run["bundle_id"])
        build = catalog.get("ExtractorBuild", run["extractor_build_id"])
        if (
            bundle["status"] == "DRAFT"
            or bundle["bundle_hash"] != run["bundle_hash"]
            or bundle["request_id"] != run["request_id"]
            or bundle["profile_id"] != build["profile_version"]
            or len(run["window_ids"]) != len(run["model_request_artifact_ids"])
            or len(run["window_ids"]) != len(run["provider_response_artifact_ids"])
        ):
            raise ValidationError("E-SCENE-LINEAGE", "scene scout run lineage is incomplete")
        windows = [catalog.get("SceneWindow", window_id) for window_id in run["window_ids"]]
        if [window["ordinal"] for window in windows] != list(range(1, len(windows) + 1)) or any(
            window["bundle_id"] != bundle["bundle_id"] for window in windows
        ):
            raise ValidationError("E-SCENE-WINDOW", "scene scout window order differs")
        for artifact_id in (
            run["model_request_artifact_ids"] + run["provider_response_artifact_ids"]
        ):
            catalog.get("Artifact", artifact_id)
            if store is not None:
                store.verify(artifact_id)
        identity = {
            key: run[key]
            for key in (
                "request_id",
                "bundle_id",
                "bundle_hash",
                "extractor_build_id",
                "discovery_brief_hash",
                "window_ids",
                "model_request_artifact_ids",
                "provider_response_artifact_ids",
                "checkpoint_artifact_id",
                "checkpoint_hash",
                "resumed_from_checkpoint",
                "model_attempt_ids",
                "attempt_record_artifact_ids",
                "usage_ledger",
            )
        }
        if run["scene_scout_run_id"] != derived_id("SceneScoutRun", identity):
            raise ValidationError("E-ID-BIND", "scene scout run id differs from its inputs")

    for merge in catalog.all("SceneMergeRun"):
        catalog.get("SceneScoutRun", merge["scene_scout_run_id"])
        if merge["input_candidate_count"] != len(merge["input_candidate_hashes"]):
            raise ValidationError("E-SCENE-MERGE", "scene merge input count differs")
        identity = {
            "scene_scout_run_id": merge["scene_scout_run_id"],
            "algorithm_id": merge["algorithm_id"],
            "input_candidate_hashes": merge["input_candidate_hashes"],
        }
        if merge["merge_run_id"] != derived_id("SceneMergeRun", identity):
            raise ValidationError("E-ID-BIND", "scene merge id differs from its inputs")
        actual_ids = sorted(
            candidate["scene_candidate_id"]
            for candidate in catalog.all("SceneCandidate")
            if candidate["scene_merge_run_id"] == merge["merge_run_id"]
        )
        if sorted(merge["output_candidate_ids"]) != actual_ids:
            raise ValidationError("E-SCENE-MERGE", "scene merge output set differs")

    for candidate in catalog.all("SceneCandidate"):
        run = catalog.get("SceneScoutRun", candidate["scene_scout_run_id"])
        merge = catalog.get("SceneMergeRun", candidate["scene_merge_run_id"])
        bundle = catalog.get("EvidenceBundle", candidate["bundle_id"])
        if (
            candidate["request_id"] != run["request_id"]
            or candidate["bundle_id"] != run["bundle_id"]
            or merge["scene_scout_run_id"] != run["scene_scout_run_id"]
            or set(candidate["window_ids"]) - set(run["window_ids"])
        ):
            raise ValidationError("E-SCENE-LINEAGE", "scene candidate lineage differs")
        chapter_by_segment, _ = bundle_chapter_index(catalog, bundle)
        span_keys = {
            (span["segment_id"], span["start"], span["end"])
            for span in candidate["source_spans"]
        }
        for span in candidate["source_spans"]:
            segment = catalog.get("Segment", span["segment_id"])
            chapter = chapter_by_segment.get(span["segment_id"])
            if chapter is None or not 0 <= span["start"] < span["end"] <= len(
                segment["normalized_text"]
            ):
                raise ValidationError("E-SCENE-SPAN", "scene candidate span is invalid")
            assessment = catalog.get(
                "TriageAssessment",
                catalog.get("Retrieval", chapter["retrieval_id"])["triage_assessment_id"],
            )
            if "event-facts" not in assessment.get("allowed_uses", []):
                raise ValidationError("E-ALLOWED-USE", "scene candidate cites a non-event-facts source")
        for field in (
            "actors",
            "action",
            "target",
            "precondition",
            "state_transition",
            "external_response",
            "immediate_feedback",
            "new_affordances",
            "persistence",
            "mechanic_pressure_point",
        ):
            observation = candidate[field]
            support_keys = {
                (span["segment_id"], span["start"], span["end"])
                for span in observation["support_spans"]
            }
            if not support_keys <= span_keys or (
                observation["status"] == "UNKNOWN"
                and (observation["values"] or observation["support_spans"])
            ):
                raise ValidationError("E-SCENE-SPAN", f"scene candidate {field} support differs")
            if observation["status"] != "UNKNOWN" and (
                not observation["values"] or not observation["support_spans"]
            ):
                raise ValidationError(
                    "E-SCENE-SPAN", f"scene candidate {field} lacks values or support"
                )
            if observation["status"] == "CONFLICTING" and len(observation["values"]) < 2:
                raise ValidationError(
                    "E-SCENE-SPAN", f"scene candidate {field} lacks conflicting values"
                )
        earliest = min(
            candidate["source_spans"],
            key=lambda span: (
                chapter_by_segment[span["segment_id"]]["ordinal"],
                catalog.get("Segment", span["segment_id"])["ordinal"],
                span["start"],
                span["segment_id"],
            ),
        )
        segment = catalog.get("Segment", earliest["segment_id"])
        chapter = chapter_by_segment[earliest["segment_id"]]
        expected_order = {
            "chapter_id": chapter["chapter_id"],
            "chapter_ordinal": chapter["ordinal"],
            "document_id": segment["document_id"],
            "segment_id": segment["segment_id"],
            "segment_ordinal": segment["ordinal"],
            "start": earliest["start"],
        }
        identity = {key: value for key, value in candidate.items() if key != "scene_candidate_id"}
        if (
            candidate["source_order"] != expected_order
            or candidate["scene_candidate_id"] != derived_id("SceneCandidate", identity)
        ):
            raise ValidationError("E-ID-BIND", "scene candidate identity or order differs")

    if catalog.all("SceneScoutRun"):
        if store is None:
            raise ValidationError("E-STORE", "scene scout replay requires an ArtifactStore")
        from .scene_scout import validate_scene_scouts

        validate_scene_scouts(catalog, store, repo_root=repo_root())

    for bundle in catalog.all("EvidenceBundle"):
        if bundle["bundle_id"] != derived_id("EvidenceBundle", {"bundle_hash": bundle["bundle_hash"]}):
            raise ValidationError("E-ID-BIND", f"{bundle['bundle_id']} does not match bundle content")


def bundle_hash(catalog: Catalog, bundle: dict[str, Any]) -> str:
    def records(kind: str, ids: list[str]) -> list[dict[str, Any]]:
        return [catalog.get(kind, ident) for ident in sorted_ids(ids)]

    snapshots = records("CollectionSnapshot", bundle["collection_snapshot_ids"])
    snapshot_retrieval_ids = {rid for snapshot in snapshots for rid in snapshot["retrieval_ids"]}
    snapshot_artifact_ids = {aid for snapshot in snapshots for aid in snapshot["artifact_ids"]}
    snapshot_triage_ids = {aid for snapshot in snapshots for aid in snapshot["triage_assessment_ids"]}
    snapshot_decision_ids = {
        ident for snapshot in snapshots for ident in snapshot.get("collection_decision_ids", [])
    }
    snapshot_review_ids = {
        ident for snapshot in snapshots for ident in snapshot.get("collection_review_ids", [])
    }
    request_ids = {bundle["request_id"], *(snapshot["request_id"] for snapshot in snapshots)}
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
    snapshot_retrievals = records("Retrieval", list(snapshot_retrieval_ids))
    source_ids = {r["source_id"] for r in [*retrievals, *snapshot_retrievals]}
    payload = {
        "requests": records("ResearchRequest", list(request_ids)),
        "snapshot_retrievals": snapshot_retrievals,
        "snapshot_artifacts": records("Artifact", list(snapshot_artifact_ids)),
        "snapshot_triage_assessments": records("TriageAssessment", list(snapshot_triage_ids)),
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
        for key in (
            "repository_commit",
            "source_classifier_build_id",
            "parser_build_id",
            "scene_scout_build_id",
        ):
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
        build = catalog.get("ExtractorBuild", producer["scene_scout_build_id"])
        if build.get("repository_commit") != producer["repository_commit"]:
            raise ValidationError("E-PRODUCER", "producer commit does not match extractor build")
        discovery = export["scene_discovery"]
        run = catalog.get("SceneScoutRun", discovery["scene_scout_run_id"])
        merge = catalog.get("SceneMergeRun", discovery["merge_run_id"])
        if (
            run["bundle_id"] != bundle["bundle_id"]
            or run["extractor_build_id"] != build["extractor_build_id"]
            or merge["scene_scout_run_id"] != run["scene_scout_run_id"]
        ):
            raise ValidationError("E-SCENE-BIND", "export scene discovery differs from bundle/build")
        expected_candidates = [
            catalog.get("SceneCandidate", candidate_id)
            for candidate_id in merge["output_candidate_ids"]
        ]
        expected_discovery = {
            "scene_scout_run_id": run["scene_scout_run_id"],
            "merge_run_id": merge["merge_run_id"],
            "window_count": len(run["window_ids"]),
            "candidate_count": len(expected_candidates),
        }
        if export["scene_discovery"] != expected_discovery:
            raise ValidationError("E-SCENE-BIND", "export scene discovery counts differ")
        if export["scene_candidates"] != expected_candidates:
            raise ValidationError("E-SCENE-BIND", "export candidates differ from merge output")
        if any(
            candidate["status"] != "DRAFT"
            or candidate["verification"] != "UNVERIFIED"
            for candidate in export["scene_candidates"]
        ):
            raise ValidationError("E-SCENE-TRUST", "unreviewed scene output must remain DRAFT/UNVERIFIED")
        source_classifier_build_ids = sorted(
            {
                catalog.get("TriageAssessment", assessment_id)["assessor_build_id"]
                for assessment_id in bundle["triage_assessment_ids"]
            }
        )
        parser_build_ids = sorted(
            {
                parse_run["parser_build_id"]
                for parse_run in catalog.all("ParseRun")
                if parse_run.get("output_document_id") in set(bundle["document_ids"])
            }
        )
        expected_source_classifier = "source-classifier-set-" + object_hash(
            {"build_ids": source_classifier_build_ids}, omit=()
        ).removeprefix("sha256:")[:20]
        expected_parser = "parser-set-" + object_hash(
            {"build_ids": parser_build_ids}, omit=()
        ).removeprefix("sha256:")[:20]
        if (
            producer["source_classifier_build_id"] != expected_source_classifier
            or producer["parser_build_id"] != expected_parser
        ):
            raise ValidationError("E-PRODUCER", "producer build sets differ from bundle lineage")
        _no_forbidden(export["scene_discovery"], "scene_discovery")
        for candidate in export["scene_candidates"]:
            _no_forbidden(candidate, "scene candidate")
        audit = export.get("assurance", {}).get("auditability", "FULL")
        if audit != "DEGRADED":
            raise ValidationError(
                "E-AUDITABILITY",
                "model-backed scene exports remain DEGRADED until every attempt is immutable",
            )
        manifest_ids = [item["artifact_id"] for item in export["artifact_manifest"]]
        from .scene_scout import (
            scene_scout_artifact_ids,
            scene_scout_distributable_artifact_ids,
        )

        expected_artifact_ids = set(
            scene_scout_artifact_ids(catalog, bundle, {"run": run})
        )
        if len(manifest_ids) != len(set(manifest_ids)) or set(manifest_ids) != expected_artifact_ids:
            raise ValidationError("E-ARTIFACT-BIND", "artifact manifest does not exactly match catalog artifacts")
        if store is None:
            raise ValidationError("E-RIGHTS-EXPORT", "export validation requires the immutable rights store")
        lineage = resolve_validated_bundle_ingestion(catalog, store, bundle)
        rights = lineage["rights"]
        distributable_ids = (
            set(scene_scout_distributable_artifact_ids(catalog, {"run": run}))
            if rights["may_export_excerpts"]
            else set()
        )
        for item in export["artifact_manifest"]:
            _require_hash(item["artifact_id"], "manifest")
            art = catalog.get("Artifact", item["artifact_id"])
            if item["byte_length"] != art["byte_length"] or item["durability_status"] != art["durability_status"]:
                raise ValidationError("E-ARTIFACT-BIND", "artifact manifest differs from catalog")
            expected_availability = (
                "AVAILABLE"
                if item["artifact_id"] in distributable_ids
                else "WITHHELD_BY_RIGHTS"
            )
            if item["availability"] != expected_availability:
                raise ValidationError(
                    "E-RIGHTS-EXPORT", "artifact availability differs from declared export rights"
                )
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
        for kind in (
            "NovelWork",
            "NovelRankingRun",
            "NovelSourceResolution",
            "SceneScoutRun",
        )
    ):
        if store is None:
            raise ValidationError("E-STORE", "novel workflow validation requires an ArtifactStore")
        from .novel_ingest import validate_novel_ingestion
        from .novel_selection import validate_source_resolutions
        from .ranking import validate_fame_ranking
        from .scene_scout import validate_scene_scouts

        validate_novel_ingestion(catalog, store)
        validate_fame_ranking(catalog, store)
        validate_source_resolutions(catalog, store)
        validate_scene_scouts(catalog, store, repo_root=repo_root())
