from __future__ import annotations

import copy
import json
import pathlib
from typing import Any

from .bundle_ops import bundle_from_snapshot
from .canonical import canonical_dumps
from .catalog import Catalog
from .collection_quality import bind_collection_quality_snapshot, run_independent_collection_review
from .constants import PROFILE_ID, SCHEMA_VERSION
from .runtime import repository_commit
from .errors import ValidationError
from .hashing import collection_snapshot_hash, object_hash, sorted_ids
from .ids import derived_id
from .model_api import OpenAIResponsesClient
from .novel_assessment import chapter_identity_review_input, reviewed_triage_assessment
from .novel_ingest import (
    novel_ingestion_artifact_ids,
    run_novel_ingestion,
    validate_novel_ingestion,
)
from .novel_selection import (
    resolve_ranked_source,
    validated_source_catalog_input,
    validate_source_resolutions,
    write_source_resolution,
)
from .plot_analysis import plot_analysis_artifact_ids, run_plot_analysis, validate_plot_analysis
from .plot_extraction import run_model_plot_extraction
from .policies import policy_bundle_hash
from .ranking import run_fame_ranking, validate_fame_ranking, write_ranking_result
from .schema import validate_schema
from .store import ArtifactStore
from .validate import validate_collection, validate_evidence, validate_export

_CHAPTER_IDENTITY_OUTCOMES = {"MATCH", "MISMATCH", "UNKNOWN", "QUARANTINED"}


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_immutable_outputs(output_dir: pathlib.Path, outputs: dict[str, bytes]) -> None:
    paths = {name: output_dir / name for name in outputs}
    for name, path in paths.items():
        if path.exists() and path.read_bytes() != outputs[name]:
            raise ValidationError("E-IMMUTABLE-OUTPUT", f"refusing to overwrite {path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, path in paths.items():
        try:
            with path.open("xb") as handle:
                handle.write(outputs[name])
        except FileExistsError:
            if path.read_bytes() != outputs[name]:
                raise ValidationError("E-IMMUTABLE-OUTPUT", f"refusing to overwrite {path}")


def _put_artifact(
    catalog: Catalog,
    store: ArtifactStore,
    data: bytes,
    *,
    media_type: str,
    now: str,
) -> str:
    artifact_id = store.put(data)
    if not any(item["artifact_id"] == artifact_id for item in catalog.all("Artifact")):
        catalog.add(
            "Artifact",
            {
                "schema_version": SCHEMA_VERSION,
                "artifact_id": artifact_id,
                "media_type": media_type,
                "byte_length": len(data),
                "retention_policy": "retention-v1",
                "durability_status": "LOCAL",
                "created_at": now,
            },
        )
    return artifact_id


def _request_from_spec(
    spec: dict[str, Any],
    *,
    input_spec_hash: str,
    now: str,
    selection_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_spec = spec.get("request") or {}
    origin = request_spec.get("origin") or {
        "repository": "local-novel-input",
        "commit": input_spec_hash.removeprefix("sha256:"),
    }
    search_constraints = {
        "language": str(spec.get("source", {}).get("language", "zh")),
        "input_kind": str(spec.get("source", {}).get("kind", "unknown")),
        "direct_novel_ingestion": selection_context is None,
    }
    if selection_context is not None:
        search_constraints["novel_selection"] = selection_context
    base = {
        "schema_version": SCHEMA_VERSION,
        "origin": origin,
        "mode": str(request_spec.get("mode", "EXPLORE")),
        "discovery_brief": str(
            request_spec.get("discovery_brief", "提取小说中的关键情节、参与者、条件与状态变化。")
        ),
        "search_constraints": search_constraints,
        "extraction_profile": PROFILE_ID,
        "budget": {
            "max_queries": 1,
            "max_fetches": max(1, int(spec.get("limits", {}).get("max_chapters", 100_000))),
            "max_bytes": int(spec.get("limits", {}).get("max_bytes", 500_000_000)),
        },
        "created_at": now,
        "supersedes": None,
    }
    return {**base, "request_id": derived_id("ResearchRequest", base)}


def prepare_novel_evidence_bundle(
    catalog: Catalog,
    store: ArtifactStore,
    ingestion: dict[str, Any],
    spec: dict[str, Any],
    *,
    collector: Any,
    reviewer: Any,
    repo_root: pathlib.Path,
    now: str,
    selection_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if ingestion["status"] == "FAILED":
        raise ValidationError("E-CHAPTER-ORDER", "failed chapter ordering cannot enter evidence bundle")
    request = _request_from_spec(
        spec,
        input_spec_hash=ingestion["input_spec_hash"],
        now=now,
        selection_context=selection_context,
    )
    validate_schema("ResearchRequest", request)
    catalog.add("ResearchRequest", request)
    policy_hash = policy_bundle_hash(repo_root)
    required_decisions = []
    rubric_artifact_id = None
    ingestion_chapters = [
        catalog.get("NovelChapter", chapter_id) for chapter_id in ingestion["chapter_ids"]
    ]
    snapshot_artifact_ids = set(novel_ingestion_artifact_ids(catalog, store, ingestion))
    if selection_context is not None:
        resolution = catalog.get("NovelSourceResolution", selection_context["resolution_id"])
        ranking = catalog.get("NovelRankingRun", resolution["ranking_run_id"])
        snapshot_artifact_ids.update(
            {
                resolution["source_catalog_artifact_id"],
                resolution["source_spec_artifact_id"],
                ranking["policy_artifact_id"],
            }
        )
        snapshot_artifact_ids.update(
            window["raw_response_artifact_id"]
            for window in ranking["provider_windows"]
            if window["raw_response_artifact_id"] is not None
        )

    def bind_review_artifacts(review: dict[str, Any]) -> None:
        snapshot_artifact_ids.add(review["rubric_artifact_id"])
        for decision_id in (
            review["collector_decision_id"],
            review["reviewer_decision_id"],
        ):
            decision = catalog.get("CollectionDecision", decision_id)
            snapshot_artifact_ids.update(decision["input_artifact_ids"])
            snapshot_artifact_ids.add(decision["output_artifact_id"])
            for field in ("model_request_artifact_id", "provider_response_artifact_id"):
                if decision.get(field):
                    snapshot_artifact_ids.add(decision[field])

    ready_chapters = [
        catalog.get("NovelChapter", chapter_id) for chapter_id in ingestion["ready_chapter_ids"]
    ]
    ready_retrieval_ids = [chapter["retrieval_id"] for chapter in ready_chapters]
    for chapter in ready_chapters:
        segments = [catalog.get("Segment", segment_id) for segment_id in chapter["segment_ids"]]
        identity_input = chapter_identity_review_input(chapter, segments)
        retrieval = catalog.get("Retrieval", chapter["retrieval_id"])
        source = catalog.get("Source", retrieval["source_id"])
        triage_input = canonical_dumps(
            {
                "retrieval": {
                    "retrieval_id": retrieval["retrieval_id"],
                    "requested_url": retrieval["requested_url"],
                    "final_url": retrieval.get("final_url"),
                    "http_status": retrieval.get("http_status"),
                    "content_type": retrieval.get("content_type"),
                    "access_kind": retrieval["access_kind"],
                },
                "source": {
                    "source_id": source["source_id"],
                    "platform_id": source["platform_id"],
                    "title": source.get("title", ""),
                    "author": source.get("author", ""),
                    "work": source.get("work", ""),
                },
                "chapter": {
                    "chapter_id": chapter["chapter_id"],
                    "ordinal": chapter["ordinal"],
                    "title": chapter["title"],
                    "artifact_id": chapter["artifact_id"],
                    "segments": [
                        {"segment_id": segment["segment_id"], "text": segment["normalized_text"]}
                        for segment in segments
                    ],
                },
            }
        )
        triage_input_id = _put_artifact(
            catalog, store, triage_input, media_type="application/json", now=now
        )
        triage_review = run_independent_collection_review(
            catalog,
            store,
            task="TRIAGE",
            subject_ids=[retrieval["retrieval_id"]],
            input_artifact_ids=[triage_input_id],
            collector=collector,
            reviewer=reviewer,
            rubric_id="collection-quality-v1",
            rubric_path=repo_root / "policies" / "collection-quality-v1.yaml",
            created_at=now,
        )
        triage = reviewed_triage_assessment(
            catalog,
            retrieval,
            triage_review,
            policy_hash=policy_hash,
            assessed_at=now,
        )
        validate_schema("TriageAssessment", triage)
        catalog.add("TriageAssessment", triage)
        retrieval["triage_assessment_id"] = triage["assessment_id"]
        required_decisions.append(triage_review["collector_decision_id"])
        rubric_artifact_id = triage_review["rubric_artifact_id"]
        bind_review_artifacts(triage_review)

        review_input = canonical_dumps(identity_input)
        review_input_id = _put_artifact(
            catalog, store, review_input, media_type="application/json", now=now
        )
        review = run_independent_collection_review(
            catalog,
            store,
            task="CHAPTER_IDENTITY",
            subject_ids=[chapter["chapter_id"]],
            input_artifact_ids=[review_input_id],
            collector=collector,
            reviewer=reviewer,
            rubric_id="collection-quality-v1",
            rubric_path=repo_root / "policies" / "collection-quality-v1.yaml",
            created_at=now,
        )
        identity_outcome = review["conservative_outcome"].get("identity_status")
        if identity_outcome not in _CHAPTER_IDENTITY_OUTCOMES or identity_outcome != "MATCH":
            raise ValidationError(
                "E-CHAPTER-IDENTITY",
                f"chapter identity review did not match {chapter['chapter_id']}",
            )
        required_decisions.append(review["collector_decision_id"])
        rubric_artifact_id = review["rubric_artifact_id"]
        bind_review_artifacts(review)
    if not required_decisions or rubric_artifact_id is None:
        raise ValidationError("E-NOVEL-EMPTY", "no unique chapters available for review")
    base_snapshot = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": "SNP-PENDING",
        "request_id": request["request_id"],
        "ingestion_run_id": ingestion["ingestion_run_id"],
        "retrieval_ids": sorted_ids(ready_retrieval_ids),
        "artifact_ids": sorted_ids(snapshot_artifact_ids),
        "triage_assessment_ids": sorted_ids(
            catalog.get("Retrieval", chapter["retrieval_id"])["triage_assessment_id"]
            for chapter in ready_chapters
        ),
        "snapshot_hash": "sha256:" + "0" * 64,
        "frozen_at": now,
        "supersedes": None,
        "status": "FROZEN",
    }
    base_snapshot["snapshot_hash"] = collection_snapshot_hash(base_snapshot)
    base_snapshot["snapshot_id"] = derived_id(
        "CollectionSnapshot", {"snapshot_hash": base_snapshot["snapshot_hash"]}
    )
    catalog.add("CollectionSnapshot", base_snapshot)
    reviewed_snapshot = bind_collection_quality_snapshot(
        catalog,
        store,
        base_snapshot,
        required_collector_decision_ids=required_decisions,
        quality_policy_artifact_id=rubric_artifact_id,
        frozen_at=now,
    )
    ready_artifact_ids = [chapter["artifact_id"] for chapter in ready_chapters]
    ready_document_ids = list(dict.fromkeys(chapter["document_id"] for chapter in ready_chapters))
    ready_segment_ids = [segment_id for chapter in ready_chapters for segment_id in chapter["segment_ids"]]
    ready_triage_ids = [
        catalog.get("Retrieval", retrieval_id)["triage_assessment_id"]
        for retrieval_id in ready_retrieval_ids
    ]
    selection_manifest = {
        "selected_chapter_ids": ingestion["ready_chapter_ids"],
        "duplicate_chapter_ids": ingestion["duplicate_chapter_ids"],
        "collection_review_ids": list(reviewed_snapshot["collection_review_ids"]),
        "quality_gate_result": reviewed_snapshot["quality_gate"]["result"],
    }
    bundle = bundle_from_snapshot(
        catalog,
        request_id=request["request_id"],
        snapshot_id=reviewed_snapshot["snapshot_id"],
        document_ids=ready_document_ids,
        segment_ids=ready_segment_ids,
        retrieval_ids=ready_retrieval_ids,
        artifact_ids=sorted_ids(set(ready_artifact_ids)),
        triage_assessment_ids=ready_triage_ids,
        selection_manifest=selection_manifest,
        profile_id=PROFILE_ID,
        policy_bundle_hash=policy_hash,
        frozen_at=now,
        schema_version=SCHEMA_VERSION,
    )
    catalog.add("EvidenceBundle", bundle)
    validate_collection(catalog, store)
    validate_evidence(catalog, store)
    return reviewed_snapshot, bundle


def _make_unqualified_export(
    catalog: Catalog,
    bundle: dict[str, Any],
    extraction: dict[str, Any],
    analysis: dict[str, Any],
    *,
    repo_root: pathlib.Path,
    now: str,
) -> dict[str, Any]:
    request = catalog.get("ResearchRequest", bundle["request_id"])
    stored_analysis = catalog.get("PlotAnalysis", analysis["analysis_id"])
    if stored_analysis != analysis:
        raise ValidationError("E-PLOT-BIND", "export requires the stored plot analysis")
    analysis_run = catalog.get("ExtractionRun", analysis["extraction_run_id"])
    if (
        analysis_run["bundle_id"] != bundle["bundle_id"]
        or extraction["run"]["extraction_run_id"] != analysis_run["extraction_run_id"]
    ):
        raise ValidationError("E-PLOT-BIND", "export analysis, extraction and bundle differ")
    build = catalog.get("ExtractorBuild", analysis_run["extractor_build_id"])
    if extraction["build"]["extractor_build_id"] != build["extractor_build_id"]:
        raise ValidationError("E-PLOT-BIND", "export extraction build differs from its run")
    claims = [catalog.get("Claim", claim_id) for claim_id in analysis["claim_ids"]]
    if any(
        claim["status"] != "ACTIVE"
        or claim["extraction_run_id"] != analysis_run["extraction_run_id"]
        for claim in claims
    ):
        raise ValidationError("E-PLOT-BIND", "export claims differ from plot analysis")
    snapshot_ids = set(bundle["collection_snapshot_ids"])
    decision_ids = {
        decision_id
        for snapshot in catalog.all("CollectionSnapshot")
        if snapshot["snapshot_id"] in snapshot_ids
        for decision_id in snapshot.get("collection_decision_ids", [])
    }
    collection_build_ids = sorted(
        {catalog.get("CollectionDecision", decision_id)["assessor_build_id"] for decision_id in decision_ids}
    )
    parser_build_ids = sorted(
        {
            parse_run["parser_build_id"]
            for parse_run in catalog.all("ParseRun")
            if parse_run.get("output_document_id") in set(bundle["document_ids"])
        }
    )
    collector_build_id = "collection-set-" + object_hash(
        {"build_ids": collection_build_ids}, omit=()
    ).removeprefix("sha256:")[:20]
    parser_build_id = "parser-set-" + object_hash(
        {"build_ids": parser_build_ids}, omit=()
    ).removeprefix("sha256:")[:20]
    export = {
        "schema_version": SCHEMA_VERSION,
        "producer": {
            "repository_commit": repository_commit(repo_root),
            "collector_build_id": collector_build_id,
            "parser_build_id": parser_build_id,
            "extractor_build_id": build["extractor_build_id"],
        },
        "origin_request": copy.deepcopy(request),
        "bundle": {"bundle_id": bundle["bundle_id"], "bundle_hash": bundle["bundle_hash"]},
        "claims": copy.deepcopy(sorted(claims, key=lambda claim: claim["claim_id"])),
        "scene_facts": {
            "plot_analysis_id": analysis["analysis_id"],
            "timeline": copy.deepcopy(analysis["timeline"]),
            "event_groups": copy.deepcopy(analysis["event_groups"]),
            "key_events": copy.deepcopy(analysis["key_events"]),
            "alias_groups": copy.deepcopy(analysis["alias_groups"]),
        },
        "policies": {"policy_bundle_hash": bundle["policy_bundle_hash"]},
        "assurance": {"level": "UNQUALIFIED", "auditability": "DEGRADED"},
        "artifact_manifest": [
            {
                "artifact_id": artifact["artifact_id"],
                "byte_length": artifact["byte_length"],
                "durability_status": artifact["durability_status"],
                "availability": "AVAILABLE",
            }
            for artifact in (
                catalog.get("Artifact", artifact_id)
                for artifact_id in plot_analysis_artifact_ids(catalog, bundle, analysis)
            )
        ],
        "created_at": now,
        "revocation": None,
    }
    export_identity = {key: value for key, value in export.items() if key not in {"export_id", "export_hash"}}
    export["export_id"] = derived_id("EvidenceExport", export_identity)
    export["export_hash"] = object_hash(export, omit=("export_hash",))
    catalog.add("EvidenceExport", export)
    return export


def run_novel_research(
    spec: dict[str, Any],
    work_dir: pathlib.Path,
    *,
    collector: Any,
    reviewer: Any,
    extractor_client: OpenAIResponsesClient,
    analyst_client: OpenAIResponsesClient,
    repo_root: pathlib.Path,
    now: str,
    fetcher: Any | None = None,
    catalog: Catalog | None = None,
    store: ArtifactStore | None = None,
    selection_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ingestion_result = run_novel_ingestion(
        spec,
        work_dir / "ingestion",
        repo_root=repo_root,
        fetcher=fetcher,
        now=now,
        catalog=catalog,
        store=store,
    )
    catalog = ingestion_result["catalog"]
    store = ingestion_result["store"]
    snapshot, bundle = prepare_novel_evidence_bundle(
        catalog,
        store,
        ingestion_result["ingestion"],
        spec,
        collector=collector,
        reviewer=reviewer,
        repo_root=repo_root,
        now=now,
        selection_context=selection_context,
    )
    extraction = run_model_plot_extraction(
        catalog,
        store,
        bundle,
        client=extractor_client,
        repo_root=repo_root,
        now=now,
    )
    analysis = run_plot_analysis(
        catalog,
        store,
        work_id=ingestion_result["work"]["work_id"],
        extraction_run_id=extraction["run"]["extraction_run_id"],
        client=analyst_client,
        repo_root=repo_root,
        created_at=now,
    )
    export = _make_unqualified_export(
        catalog,
        bundle,
        extraction,
        analysis,
        repo_root=repo_root,
        now=now,
    )
    validate_novel_ingestion(catalog, store)
    validate_collection(catalog, store)
    validate_evidence(catalog, store)
    validate_plot_analysis(catalog, store)
    if catalog.all("NovelRankingRun"):
        validate_fame_ranking(catalog, store)
    if catalog.all("NovelSourceResolution"):
        validate_source_resolutions(catalog, store)
    validate_export(catalog, store)
    output_dir = work_dir / "research" / analysis["analysis_id"]
    outputs = {
        "catalog.json": _json_bytes({kind: rows for kind, rows in catalog.by_type.items() if rows}),
        "plot-analysis.json": _json_bytes(analysis),
        "evidence-export.json": _json_bytes(export),
        "run-summary.json": _json_bytes(
            {
                **(selection_context or {}),
                "work_id": ingestion_result["work"]["work_id"],
                "ingestion_run_id": ingestion_result["ingestion"]["ingestion_run_id"],
                "snapshot_id": snapshot["snapshot_id"],
                "bundle_id": bundle["bundle_id"],
                "extraction_run_id": extraction["run"]["extraction_run_id"],
                "analysis_id": analysis["analysis_id"],
                "export_id": export["export_id"],
                "assurance": export["assurance"]["level"],
                "auditability": export["assurance"]["auditability"],
            }
        ),
    }
    _write_immutable_outputs(output_dir, outputs)
    return {
        "catalog": catalog,
        "store": store,
        "ingestion": ingestion_result["ingestion"],
        "snapshot": snapshot,
        "bundle": bundle,
        "extraction": extraction,
        "analysis": analysis,
        "export": export,
        "work_dir": output_dir,
    }


def run_famous_novel_research(
    spec: dict[str, Any],
    work_dir: pathlib.Path,
    *,
    providers: list[Any],
    collector: Any,
    reviewer: Any,
    extractor_client: OpenAIResponsesClient,
    analyst_client: OpenAIResponsesClient,
    repo_root: pathlib.Path,
    now: str,
    fetcher: Any | None = None,
) -> dict[str, Any]:
    genre = spec.get("genre")
    source_catalog = spec.get("source_catalog")
    raw_ranking_spec = spec.get("ranking")
    ranking_spec = {} if raw_ranking_spec is None else raw_ranking_spec
    defaults = spec.get("defaults")
    if not isinstance(genre, str) or not genre.strip():
        raise ValidationError("E-RANKING-INPUT", "famous novel workflow requires genre")
    if not isinstance(ranking_spec, dict):
        raise ValidationError("E-RANKING-INPUT", "ranking must be an object")
    allowed_ranking_keys = {"queries", "pages_per_query", "limit"}
    if set(ranking_spec) - allowed_ranking_keys:
        raise ValidationError("E-RANKING-INPUT", "ranking contains unsupported fields")
    catalog_input = validated_source_catalog_input(source_catalog, defaults)
    work_dir = pathlib.Path(work_dir)
    catalog = Catalog()
    store = ArtifactStore(work_dir / "objects")
    ranking = run_fame_ranking(
        genre=genre,
        providers=providers,
        store=store,
        catalog=catalog,
        repo_root=repo_root,
        created_at=now,
        queries=ranking_spec.get("queries"),
        pages_per_query=ranking_spec.get("pages_per_query", 1),
        limit=ranking_spec.get("limit", 10),
    )
    validate_fame_ranking(catalog, store)
    write_ranking_result(ranking, catalog, work_dir)
    resolution, novel_spec = resolve_ranked_source(
        ranking,
        catalog_input["source_catalog"],
        catalog,
        store,
        defaults=catalog_input["defaults"],
        created_at=now,
    )
    validate_source_resolutions(catalog, store)
    write_source_resolution(resolution, catalog, work_dir)
    selection_context = {
        "ranking_run_id": ranking["ranking_run_id"],
        "resolution_id": resolution["resolution_id"],
        "candidate_id": resolution["candidate_id"],
        "candidate_rank": resolution["candidate_rank"],
        "candidate_title": resolution["candidate_title"],
        "source_spec_hash": resolution["source_spec_hash"],
    }
    result = run_novel_research(
        novel_spec,
        work_dir,
        collector=collector,
        reviewer=reviewer,
        extractor_client=extractor_client,
        analyst_client=analyst_client,
        repo_root=repo_root,
        now=now,
        fetcher=fetcher,
        catalog=catalog,
        store=store,
        selection_context=selection_context,
    )
    result["ranking"] = ranking
    result["source_resolution"] = resolution
    return result
