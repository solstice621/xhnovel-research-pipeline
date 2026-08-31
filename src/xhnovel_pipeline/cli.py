from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .catalog import Catalog
from .errors import PipelineError
from .novel_ingest import load_novel_spec, run_novel_ingestion, validate_novel_ingestion
from .novel_selection import validate_source_resolutions
from .paths import repo_root
from .ranking import run_fame_ranking, validate_fame_ranking, write_ranking_result
from .ranking_provider import WikipediaRankingProvider
from .runtime import utc_now
from .store import ArtifactStore


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
    validate.add_argument("target", choices=["novel", "ranking", "selection"])
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
            else:
                validate_fame_ranking(catalog, store)
                validate_source_resolutions(catalog, store)
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
    except (PipelineError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
