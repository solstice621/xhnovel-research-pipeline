from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .catalog import Catalog
from .errors import PipelineError, ValidationError
from .agent_files import (
    AGENT_FILES_PROTOCOL,
    AgentFileExecutor,
    AgentResponsesPending,
    _safe_file_stem,
    locate_quote_in_task,
)
from .model_api import OpenAIResponsesClient
from .novel_ingest import load_novel_spec, run_novel_ingestion, validate_novel_ingestion
from .novel_selection import validate_source_resolutions
from .novel_workflow import (
    run_famous_novel_research,
    run_novel_research,
    validated_famous_novel_spec,
)
from .paths import repo_root
from .phase0_builder import prepare_handoff_from_input, validate_evidence_handoff
from .phase0_execution import execute_evidence_handoff
from .ranking import run_fame_ranking, validate_fame_ranking, write_ranking_result
from .ranking_provider import WikipediaRankingProvider
from .runtime import utc_now
from .scene_scout import _atomic_write, validate_scene_scouts
from .store import ArtifactStore
from .validate import validate_all, validate_evidence, validate_export


def _catalog_from_json(path: pathlib.Path) -> Catalog:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("E-CATALOG-JSON", f"invalid catalog JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ValidationError("E-CATALOG-JSON", "catalog root must be an object")
    return Catalog.from_mapping(data)


def _rel(path: pathlib.Path, base: pathlib.Path) -> str:
    # POSIX separators keep the machine-readable manifest identical on Windows.
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def _json_stdout(value: object) -> str:
    # ensure_ascii=True escapes non-ASCII so stdout never depends on the terminal
    # code page (Windows cp1252 would otherwise fail to encode Chinese text).
    body = json.dumps(value, ensure_ascii=True, indent=2)
    print(body)
    return body


def _agent_files_dir(work_dir: pathlib.Path) -> pathlib.Path:
    return work_dir / "scene-scout" / "agent-files"


def _emit_pending_manifest(exc: AgentResponsesPending, work_dir: pathlib.Path | None) -> int:
    """Report WAITING_FOR_AGENT: human line to stderr, stable JSON to stdout, exit 3.

    The pending manifest is a regenerable operational view (also written to
    ``pending.json``), never an audit source of truth. It carries window ids and
    task/answer paths only — no source text and no task packet body.
    """
    # tasks_dir is <work_dir>/scene-scout/agent-files/tasks; recover work_dir from it
    # so the manifest never depends on a caller local that may be unbound on error.
    base = work_dir if work_dir is not None else exc.tasks_dir.parents[2]
    manifest = {
        "status": "WAITING_FOR_AGENT",
        "exit_code": 3,
        "executor": "agent-files",
        "pending_count": exc.pending_count,
        "tasks_dir": _rel(exc.tasks_dir, base),
        "answers_dir": _rel(exc.answers_dir, base),
        "pending": [
            {
                "window_id": item.window_id,
                "task": _rel(item.task_path, base),
                "answer": _rel(item.answer_path, base),
            }
            for item in exc.pending
        ],
    }
    body = json.dumps(manifest, ensure_ascii=True, indent=2)
    print(
        f"WAITING_FOR_AGENT: {exc.pending_count} SceneWindow answer(s) are pending",
        file=sys.stderr,
    )
    print(body)
    manifest_path = _agent_files_dir(base) / "pending.json"
    _atomic_write(manifest_path, (body + "\n").encode("utf-8"))
    return 3


def _write_agent_pending_manifest_complete(work_dir: pathlib.Path) -> None:
    manifest_path = _agent_files_dir(work_dir) / "pending.json"
    body = json.dumps(
        {"status": "COMPLETE", "pending_count": 0, "pending": []},
        ensure_ascii=False,
        indent=2,
    )
    _atomic_write(manifest_path, (body + "\n").encode("utf-8"))


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
    research.add_argument("--executor", choices=["api", "agent-files"], default="api")
    research.add_argument("--scout-model", default=None)
    research.add_argument("--agent-model-label", default="host-code-agent")
    research.add_argument("--work-dir", type=pathlib.Path, default=None)

    famous = sub.add_parser("research-famous-novel")
    famous.add_argument("spec", type=pathlib.Path)
    famous.add_argument("--executor", choices=["api", "agent-files"], default="api")
    famous.add_argument("--scout-model", default=None)
    famous.add_argument("--agent-model-label", default="host-code-agent")
    famous.add_argument("--work-dir", type=pathlib.Path, default=None)

    prepare = sub.add_parser("prepare-handoff")
    prepare.add_argument("input", type=pathlib.Path)
    prepare.add_argument("--work-dir", type=pathlib.Path, default=None)

    handoff_validate = sub.add_parser("validate-handoff")
    handoff_validate.add_argument("handoff", type=pathlib.Path)
    handoff_validate.add_argument("--phase0-root", type=pathlib.Path, default=None)

    execute_handoff = sub.add_parser("execute-handoff")
    execute_handoff.add_argument("handoff", type=pathlib.Path)
    execute_handoff.add_argument(
        "--executor", choices=["api", "agent-files"], default="api"
    )
    execute_handoff.add_argument("--scout-model", default=None)
    execute_handoff.add_argument("--agent-model-label", default="host-code-agent")
    execute_handoff.add_argument("--work-dir", type=pathlib.Path, required=True)
    execute_handoff.add_argument("--retry", action="store_true")

    locate = sub.add_parser("agent-locate")
    locate.add_argument("--work-dir", type=pathlib.Path, required=True)
    locate.add_argument("--window", required=True)
    locate.add_argument("--quote", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = repo_root()
    work_dir: pathlib.Path | None = None
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

        if args.cmd == "prepare-handoff":
            work_dir = args.work_dir or (root / ".runtime" / "exploration" / args.input.stem)
            prepared = prepare_handoff_from_input(args.input, work_dir)
            print(f"OK: prepared evidence handoff {prepared.handoff['handoff_id']}")
            print(prepared.handoff_path)
            return 0

        if args.cmd == "validate-handoff":
            handoff = validate_evidence_handoff(
                args.handoff,
                phase0_root=args.phase0_root,
            )
            print(f"OK: validate handoff {handoff['handoff_id']}")
            return 0

        if args.cmd == "execute-handoff":
            work_dir = args.work_dir
            if args.executor == "api":
                if not args.scout_model:
                    raise PipelineError(
                        "E-MODEL-CONFIG",
                        "--scout-model is required for --executor api",
                    )
                extractor_factory = lambda: OpenAIResponsesClient(model=args.scout_model)
            else:
                if args.scout_model:
                    raise PipelineError(
                        "E-MODEL-CONFIG",
                        "--scout-model is not allowed for --executor agent-files",
                    )
                extractor_factory = lambda: AgentFileExecutor(
                    work_dir / "scene-scout" / "agent-files",
                    model_label=args.agent_model_label,
                )
            executed = execute_evidence_handoff(
                args.handoff,
                work_dir,
                executor=args.executor,
                extractor_factory=extractor_factory,
                repo_root=root,
                now=utc_now(),
                retry=args.retry,
            )
            print(
                f"OK: executed evidence handoff {executed.receipt['handoff_id']} "
                f"attempt={executed.attempt_id} status={executed.status}"
            )
            print(executed.receipt_path)
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

        if args.cmd == "agent-locate":
            if not args.quote:
                raise PipelineError("E-AGENT-LOCATE", "locate requires a non-empty quote")
            tasks_dir = args.work_dir / "scene-scout" / "agent-files" / "tasks"
            task_path = tasks_dir / f"{_safe_file_stem(args.window)}.json"
            if not task_path.is_file():
                raise PipelineError("E-AGENT-LOCATE", f"unknown window under {tasks_dir}")
            try:
                task = json.loads(task_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PipelineError("E-AGENT-LOCATE", f"task packet is invalid: {task_path}") from exc
            if not isinstance(task, dict) or task.get("protocol") != AGENT_FILES_PROTOCOL:
                raise PipelineError("E-AGENT-LOCATE", "task packet protocol is not recognized")
            if task.get("window_id") != args.window:
                raise PipelineError("E-AGENT-LOCATE", "task packet window_id differs from --window")
            matches = locate_quote_in_task(task, args.quote)
            _json_stdout(
                {
                    "window_id": args.window,
                    "quote": args.quote,
                    "match_count": len(matches),
                    "matches": matches,
                }
            )
            return 0

        spec = load_novel_spec(args.spec)
        if args.cmd == "research-famous-novel":
            # Fail on local selection/ranking input before credential lookup or network setup.
            validated_famous_novel_spec(spec)
            if args.executor == "agent-files":
                # The famous workflow re-runs ranking + source resolution on every
                # call, so a second identical command would derive fresh
                # ranking/resolution/request/window identities and never consume the
                # first pass's answers. Refuse before any provider/network work until
                # a persisted selection snapshot exists (out of Stage 3 scope).
                raise PipelineError(
                    "E-AGENT-EXECUTOR-UNSUPPORTED",
                    "research-famous-novel does not support --executor agent-files; "
                    "use research-novel with a resolved local spec",
                )
        if args.cmd == "research-novel":
            work_dir = args.work_dir or (root / ".runtime" / "novel-research" / args.spec.stem)
        else:
            work_dir = args.work_dir or (
                root / ".runtime" / "famous-novel-research" / args.spec.stem
            )
        if args.executor == "api":
            if not args.scout_model:
                raise PipelineError("E-MODEL-CONFIG", "--scout-model is required for --executor api")
            extractor = OpenAIResponsesClient(model=args.scout_model)
        else:
            if args.scout_model:
                raise PipelineError(
                    "E-MODEL-CONFIG", "--scout-model is not allowed for --executor agent-files"
                )
            extractor = AgentFileExecutor(
                work_dir / "scene-scout" / "agent-files",
                model_label=args.agent_model_label,
            )
        if args.cmd == "research-novel":
            result = run_novel_research(
                spec,
                work_dir,
                extractor_client=extractor,
                repo_root=root,
                now=utc_now(),
            )
        else:
            result = run_famous_novel_research(
                spec,
                work_dir,
                providers=[WikipediaRankingProvider()],
                extractor_client=extractor,
                repo_root=root,
                now=utc_now(),
            )
        if args.executor == "agent-files":
            _write_agent_pending_manifest_complete(work_dir)
        print(
            f"OK: discovered {len(result['scout']['candidates'])} draft scene candidates "
            f"({result['export']['assurance']['level']})"
        )
        if args.executor == "agent-files":
            # Agent attempts have no token accounting; surface that honestly. The API
            # success output stays byte-identical to the pre-Stage-3 two-line form.
            usage = result["scout"]["run"]["usage_ledger"]
            unknown = usage.get("attempts_with_unknown_usage", 0)
            print(f"Token usage: unknown for {unknown} host-agent attempt(s)")
        print(result["work_dir"] / "scene-candidates.json")
        return 0
    except AgentResponsesPending as exc:
        return _emit_pending_manifest(exc, work_dir)
    except (PipelineError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
