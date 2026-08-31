from __future__ import annotations

import copy
import json
import pathlib
from typing import Any

from .bundle_ops import bundle_from_snapshot
from .catalog import Catalog
from .constants import PROFILE_ID, SCHEMA_VERSION
from .runtime import repository_commit
from .errors import ValidationError
from .hashing import collection_snapshot_hash, object_hash, sorted_ids
from .ids import derived_id
from .model_api import OpenAIResponsesClient
from .novel_assessment import (
    declared_rights,
    declared_source_quality,
    deterministic_triage_assessment,
    resolve_validated_bundle_ingestion,
)
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
from .policies import policy_bundle_hash
from .ranking import run_fame_ranking, validate_fame_ranking, write_ranking_result
from .scene_scout import (
    run_scene_scout,
    scene_scout_artifact_ids,
    scene_scout_distributable_artifact_ids,
    validate_scene_scouts,
)
from .schema import validate_schema
from .store import ArtifactStore
from .validate import validate_collection, validate_evidence, validate_export


def validated_famous_novel_spec(
    spec: dict[str, Any],
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Validate selection inputs without creating stores, clients, or network work."""
    if not isinstance(spec, dict):
        raise ValidationError("E-RANKING-INPUT", "famous novel specification must be an object")
    genre = spec.get("genre")
    raw_ranking_spec = spec.get("ranking")
    ranking_spec = {} if raw_ranking_spec is None else raw_ranking_spec
    if not isinstance(genre, str) or not genre.strip():
        raise ValidationError("E-RANKING-INPUT", "famous novel workflow requires genre")
    if not isinstance(ranking_spec, dict):
        raise ValidationError("E-RANKING-INPUT", "ranking must be an object")
    if set(ranking_spec) - {"queries", "pages_per_query", "limit"}:
        raise ValidationError("E-RANKING-INPUT", "ranking contains unsupported fields")
    queries = ranking_spec.get("queries")
    pages_per_query = ranking_spec.get("pages_per_query", 1)
    limit = ranking_spec.get("limit", 10)
    if (
        queries is not None
        and (
            not isinstance(queries, list)
            or not 1 <= len(queries) <= 100
            or any(not isinstance(query, str) or not query.strip() for query in queries)
            or len({query.strip() for query in queries}) != len(queries)
        )
    ):
        raise ValidationError(
            "E-RANKING-INPUT", "ranking queries must be 1-100 unique non-empty strings"
        )
    if (
        not isinstance(pages_per_query, int)
        or isinstance(pages_per_query, bool)
        or not 1 <= pages_per_query <= 100
        or not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= 100
    ):
        raise ValidationError("E-RANKING-INPUT", "invalid ranking search window")
    catalog_input = validated_source_catalog_input(spec.get("source_catalog"), spec.get("defaults"))
    return genre.strip(), copy.deepcopy(ranking_spec), catalog_input


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
    identity = {key: value for key, value in base.items() if key != "created_at"}
    return {**base, "request_id": derived_id("ResearchRequest", identity)}


def prepare_novel_evidence_bundle(
    catalog: Catalog,
    store: ArtifactStore,
    ingestion: dict[str, Any],
    spec: dict[str, Any],
    *,
    repo_root: pathlib.Path,
    now: str,
    selection_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if ingestion["status"] == "FAILED":
        raise ValidationError("E-CHAPTER-ORDER", "failed chapter ordering cannot enter evidence bundle")
    rights = declared_rights(spec, require_storage=True, require_external_model=True)
    lineage_time = ingestion["started_at"]
    request = _request_from_spec(
        spec,
        input_spec_hash=ingestion["input_spec_hash"],
        now=lineage_time,
        selection_context=selection_context,
    )
    validate_schema("ResearchRequest", request)
    catalog.add("ResearchRequest", request)
    policy_hash = policy_bundle_hash(repo_root)
    source_quality = declared_source_quality(spec)
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

    ready_chapters = [
        catalog.get("NovelChapter", chapter_id) for chapter_id in ingestion["ready_chapter_ids"]
    ]
    if not ready_chapters:
        raise ValidationError("E-NOVEL-EMPTY", "no narrative chapters available for scene discovery")
    ready_retrieval_ids = [chapter["retrieval_id"] for chapter in ready_chapters]
    for chapter in ready_chapters:
        retrieval = catalog.get("Retrieval", chapter["retrieval_id"])
        triage = deterministic_triage_assessment(
            catalog,
            retrieval,
            rights=rights,
            source_quality=source_quality,
            policy_hash=policy_hash,
            assessed_at=lineage_time,
        )
        validate_schema("TriageAssessment", triage)
        catalog.add("TriageAssessment", triage)
        retrieval["triage_assessment_id"] = triage["assessment_id"]
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
        "frozen_at": lineage_time,
        "supersedes": None,
        "status": "FROZEN",
    }
    base_snapshot["snapshot_hash"] = collection_snapshot_hash(base_snapshot)
    base_snapshot["snapshot_id"] = derived_id(
        "CollectionSnapshot", {"snapshot_hash": base_snapshot["snapshot_hash"]}
    )
    catalog.add("CollectionSnapshot", base_snapshot)
    reviewed_snapshot = base_snapshot
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
        "ignored_chapter_ids": ingestion["ignored_chapter_ids"],
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
        frozen_at=lineage_time,
        schema_version=SCHEMA_VERSION,
    )
    catalog.add("EvidenceBundle", bundle)
    validate_collection(catalog, store)
    validate_evidence(catalog, store)
    return reviewed_snapshot, bundle


def _make_unqualified_export(
    catalog: Catalog,
    store: ArtifactStore,
    bundle: dict[str, Any],
    scout: dict[str, Any],
    *,
    repo_root: pathlib.Path,
    now: str,
) -> dict[str, Any]:
    lineage = resolve_validated_bundle_ingestion(catalog, store, bundle)
    rights = lineage["rights"]
    distributable_ids = (
        set(scene_scout_distributable_artifact_ids(catalog, scout))
        if rights["may_export_excerpts"]
        else set()
    )
    request = catalog.get("ResearchRequest", bundle["request_id"])
    run = catalog.get("SceneScoutRun", scout["run"]["scene_scout_run_id"])
    merge_run = catalog.get("SceneMergeRun", scout["merge_run"]["merge_run_id"])
    if (
        run != scout["run"]
        or merge_run != scout["merge_run"]
        or run["bundle_id"] != bundle["bundle_id"]
        or merge_run["scene_scout_run_id"] != run["scene_scout_run_id"]
    ):
        raise ValidationError("E-SCENE-BIND", "export requires the stored scene scout lineage")
    build = catalog.get("ExtractorBuild", run["extractor_build_id"])
    if scout["build"]["extractor_build_id"] != build["extractor_build_id"]:
        raise ValidationError("E-SCENE-BIND", "export scene scout build differs from its run")
    candidates = [
        catalog.get("SceneCandidate", candidate_id)
        for candidate_id in merge_run["output_candidate_ids"]
    ]
    if any(
        candidate["status"] != "DRAFT"
        or candidate["verification"] != "UNVERIFIED"
        or candidate["scene_scout_run_id"] != run["scene_scout_run_id"]
        or candidate["scene_merge_run_id"] != merge_run["merge_run_id"]
        for candidate in candidates
    ):
        raise ValidationError("E-SCENE-BIND", "export candidates differ from scene scout merge")
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
    source_classifier_build_id = "source-classifier-set-" + object_hash(
        {"build_ids": source_classifier_build_ids}, omit=()
    ).removeprefix("sha256:")[:20]
    parser_build_id = "parser-set-" + object_hash(
        {"build_ids": parser_build_ids}, omit=()
    ).removeprefix("sha256:")[:20]
    export = {
        "schema_version": SCHEMA_VERSION,
        "producer": {
            "repository_commit": repository_commit(repo_root),
            "source_classifier_build_id": source_classifier_build_id,
            "parser_build_id": parser_build_id,
            "scene_scout_build_id": build["extractor_build_id"],
        },
        "origin_request": copy.deepcopy(request),
        "bundle": {"bundle_id": bundle["bundle_id"], "bundle_hash": bundle["bundle_hash"]},
        "scene_candidates": copy.deepcopy(candidates),
        "scene_discovery": {
            "scene_scout_run_id": run["scene_scout_run_id"],
            "merge_run_id": merge_run["merge_run_id"],
            "window_count": len(run["window_ids"]),
            "candidate_count": len(candidates),
        },
        "policies": {"policy_bundle_hash": bundle["policy_bundle_hash"]},
        "assurance": {"level": "UNQUALIFIED", "auditability": "DEGRADED"},
        "artifact_manifest": [
            {
                "artifact_id": artifact["artifact_id"],
                "byte_length": artifact["byte_length"],
                "durability_status": artifact["durability_status"],
                "availability": (
                    "AVAILABLE"
                    if artifact["artifact_id"] in distributable_ids
                    else "WITHHELD_BY_RIGHTS"
                ),
            }
            for artifact in (
                catalog.get("Artifact", artifact_id)
                for artifact_id in scene_scout_artifact_ids(catalog, bundle, scout)
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
    extractor_client: OpenAIResponsesClient,
    repo_root: pathlib.Path,
    now: str,
    fetcher: Any | None = None,
    catalog: Catalog | None = None,
    store: ArtifactStore | None = None,
    selection_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    declared_rights(spec, require_storage=True, require_external_model=True)
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
        repo_root=repo_root,
        now=now,
        selection_context=selection_context,
    )
    scene_options = spec.get("scene_scout") or {}
    if not isinstance(scene_options, dict) or set(scene_options) - {
        "window_chars",
        "overlap_chars",
        "max_input_chars",
        "max_request_bytes",
        "max_workers",
    }:
        raise ValidationError("E-SCENE-CONFIG", "scene_scout options are invalid")
    scout = run_scene_scout(
        catalog,
        store,
        bundle,
        client=extractor_client,
        repo_root=repo_root,
        created_at=now,
        work_dir=work_dir / "scene-scout",
        **scene_options,
    )
    export = _make_unqualified_export(
        catalog,
        store,
        bundle,
        scout,
        repo_root=repo_root,
        now=scout["run"]["created_at"],
    )
    validate_novel_ingestion(catalog, store)
    validate_collection(catalog, store)
    validate_evidence(catalog, store)
    validate_scene_scouts(catalog, store, repo_root=repo_root)
    if catalog.all("NovelRankingRun"):
        validate_fame_ranking(catalog, store)
    if catalog.all("NovelSourceResolution"):
        validate_source_resolutions(catalog, store)
    validate_export(catalog, store)
    output_dir = work_dir / "research" / scout["run"]["scene_scout_run_id"]
    outputs = {
        "catalog.json": _json_bytes(
            {
                kind: sorted(rows, key=lambda row: object_hash(row, omit=()))
                for kind, rows in catalog.by_type.items()
                if rows
            }
        ),
        "scene-scout-run.json": _json_bytes(scout["run"]),
        "scene-merge-run.json": _json_bytes(scout["merge_run"]),
        "scene-candidates.json": _json_bytes(scout["candidates"]),
        "evidence-export.json": _json_bytes(export),
        "run-summary.json": _json_bytes(
            {
                **(selection_context or {}),
                "work_id": ingestion_result["work"]["work_id"],
                "ingestion_run_id": ingestion_result["ingestion"]["ingestion_run_id"],
                "snapshot_id": snapshot["snapshot_id"],
                "bundle_id": bundle["bundle_id"],
                "scene_scout_run_id": scout["run"]["scene_scout_run_id"],
                "merge_run_id": scout["merge_run"]["merge_run_id"],
                "candidate_count": len(scout["candidates"]),
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
        "scout": scout,
        "export": export,
        "work_dir": output_dir,
    }


def run_famous_novel_research(
    spec: dict[str, Any],
    work_dir: pathlib.Path,
    *,
    providers: list[Any],
    extractor_client: OpenAIResponsesClient,
    repo_root: pathlib.Path,
    now: str,
    fetcher: Any | None = None,
) -> dict[str, Any]:
    genre, ranking_spec, catalog_input = validated_famous_novel_spec(spec)
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
        extractor_client=extractor_client,
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
