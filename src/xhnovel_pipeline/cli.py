from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .catalog import Catalog
from .errors import PipelineError
from .model_api import OpenAIResponsesClient
from .novel_ingest import load_novel_spec, run_novel_ingestion, validate_novel_ingestion
from .novel_selection import validate_source_resolutions
from .novel_workflow import (
    run_famous_novel_research,
    run_novel_research,
    validated_famous_novel_spec,
)
from .paths import repo_root
from .ranking import run_fame_ranking, validate_fame_ranking, write_ranking_result
from .ranking_provider import WikipediaRankingProvider
from .runtime import utc_now
from .scene_scout import validate_scene_scouts
from .store import ArtifactStore
from .validate import validate_all, validate_evidence, validate_export


def _catalog_from_json(path: pathlib.Path) -> Catalog:
    data = json.loads(path.read_text(encoding="utf-8"))
    catalog = Catalog()
    for kind, records in data.items():
        for record in records:
            catalog.add(kind, record)
    return catalog


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xhnovel-pipeline")
    sub = parser.add_subparsers(dest="cmd", required=True)

    validate = sub.add_parser("validate")
    validate.add_argument(
        "target",
        choices=["novel", "ranking", "selection", "scene", "evidence", "export", "all"],
    )
    validate.add_argument("catalog", type=pathlib.Path)
    validate.add_argument("--store", type=pathlib.Path, required=True)

    ingest = sub.add_parser("ingest-novel")
    ingest.add_argument("spec", type=pathlib.Path)
    ingest.add_argument("--work-dir", type=pathlib.Path, default=None)

    rank = sub.add_parser("rank-novels")
    rank.add_argument("genre")
    rank.add_argument("--pages", type=int, default=1)
    rank.add_argument("--limit", type=int, default=10)
    rank.add_argument("--query", action="append", default=[])
    rank.add_argument("--work-dir", type=pathlib.Path, default=None)

    research = sub.add_parser("research-novel")
    research.add_argument("spec", type=pathlib.Path)
    research.add_argument("--scout-model", default=None)
    research.add_argument("--work-dir", type=pathlib.Path, default=None)

    famous = sub.add_parser("research-famous-novel")
    famous.add_argument("spec", type=pathlib.Path)
    famous.add_argument("--scout-model", default=None)
    famous.add_argument("--work-dir", type=pathlib.Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = repo_root()
    try:
        if args.cmd == "validate":
            catalog = _catalog_from_json(args.catalog)
            store = ArtifactStore(args.store)
            if args.target == "novel":
                validate_novel_ingestion(catalog, store)
            elif args.target == "ranking":
                validate_fame_ranking(catalog, store)
            elif args.target == "selection":
                validate_fame_ranking(catalog, store)
                validate_source_resolutions(catalog, store)
            elif args.target == "scene":
                validate_scene_scouts(catalog, store, repo_root=root)
            elif args.target == "evidence":
                validate_evidence(catalog, store)
            elif args.target == "export":
                validate_export(catalog, store)
            else:
                validate_all(catalog, store)
            print(f"OK: validate {args.target}")
            return 0

        if args.cmd == "ingest-novel":
            spec = load_novel_spec(args.spec)
            work_dir = args.work_dir or (root / ".runtime" / "novels" / args.spec.stem)
            result = run_novel_ingestion(spec, work_dir, repo_root=root, now=utc_now())
            ingestion = result["ingestion"]
            output = result["work_dir"] / "novel-ingestion.json"
            if ingestion["status"] == "FAILED":
                print(
                    f"FAIL: novel ingestion {ingestion['ingestion_run_id']} status=FAILED",
                    file=sys.stderr,
                )
                print(output, file=sys.stderr)
                return 2
            if ingestion["status"] == "PARTIAL":
                print(
                    f"WARNING: novel ingestion {ingestion['ingestion_run_id']} status=PARTIAL",
                    file=sys.stderr,
                )
            else:
                print(f"OK: novel ingestion {ingestion['ingestion_run_id']}")
            print(output)
            return 0

        if args.cmd == "rank-novels":
            work_dir = args.work_dir or (root / ".runtime" / "rankings" / args.genre)
            catalog = Catalog()
            store = ArtifactStore(work_dir / "objects")
            ranking = run_fame_ranking(
                genre=args.genre,
                providers=[WikipediaRankingProvider()],
                store=store,
                catalog=catalog,
                repo_root=root,
                created_at=utc_now(),
                queries=args.query or None,
                pages_per_query=args.pages,
                limit=args.limit,
            )
            validate_fame_ranking(catalog, store)
            output_dir = write_ranking_result(ranking, catalog, work_dir)
            print(
                f"OK: ranked {len(ranking['candidates'])} candidates in declared search window"
            )
            print(output_dir / "ranking.json")
            return 0

        spec = load_novel_spec(args.spec)
        if args.cmd == "research-famous-novel":
            # Fail on local selection/ranking input before credential lookup or network setup.
            validated_famous_novel_spec(spec)
        scout_model = args.scout_model
        if not scout_model:
            raise PipelineError("E-MODEL-CONFIG", "--scout-model is required")
        extractor = OpenAIResponsesClient(model=scout_model)
        if args.cmd == "research-novel":
            work_dir = args.work_dir or (root / ".runtime" / "novel-research" / args.spec.stem)
            result = run_novel_research(
                spec,
                work_dir,
                extractor_client=extractor,
                repo_root=root,
                now=utc_now(),
            )
        else:
            work_dir = args.work_dir or (
                root / ".runtime" / "famous-novel-research" / args.spec.stem
            )
            result = run_famous_novel_research(
                spec,
                work_dir,
                providers=[WikipediaRankingProvider()],
                extractor_client=extractor,
                repo_root=root,
                now=utc_now(),
            )
        print(
            f"OK: discovered {len(result['scout']['candidates'])} draft scene candidates "
            f"({result['export']['assurance']['level']})"
        )
        print(result["work_dir"] / "scene-candidates.json")
        return 0
    except (PipelineError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
