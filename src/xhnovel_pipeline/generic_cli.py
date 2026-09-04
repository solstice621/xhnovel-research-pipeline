from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .errors import PipelineError
from .generic_agent_files import GenericAgentFileExecutor, GenericAgentResponsesPending
from .generic_extraction import (
    GenericExtractionPartial,
    generic_engine_source_hash,
    run_generic_corpus_workflow,
    validate_generic_work_dir,
)
from .generic_profile import load_extraction_profile
from .hashing import object_hash
from .model_api import OpenAIResponsesClient
from .novel_ingest import load_novel_spec
from .paths import repo_root
from .runtime import utc_now


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xhnovel-extract")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", description="extract a source-grounded domain corpus")
    run.add_argument("spec", type=pathlib.Path)
    run.add_argument("--profile", required=True)
    run.add_argument("--executor", choices=["api", "agent-files"], default="api")
    run.add_argument("--model", default=None)
    run.add_argument("--agent-model-label", default="host-code-agent")
    run.add_argument("--work-dir", type=pathlib.Path, required=True)

    validate = sub.add_parser("validate", description="replay completed generic outputs")
    validate.add_argument("spec", type=pathlib.Path)
    validate.add_argument("--profile", required=True)
    validate.add_argument("--work-dir", type=pathlib.Path, required=True)

    list_profiles = sub.add_parser("list-profiles")
    list_profiles.add_argument("--json", action="store_true")
    return parser


def _json_stdout(value: object) -> None:
    print(json.dumps(value, ensure_ascii=True, indent=2))


def _agent_root(work_dir: pathlib.Path, profile_ref: str, model_label: str) -> pathlib.Path:
    root = repo_root()
    profile = load_extraction_profile(profile_ref, root=root)
    identity = object_hash(
        {
            "engine_source_hash": generic_engine_source_hash(root),
            "extraction_profile_hash": profile.extraction_profile_hash,
            "core_prompt_artifact_id": profile.core_prompt_artifact_id,
            "executor_build_id": GenericAgentFileExecutor.executor_build_id,
            "model": model_label,
        },
        omit=(),
    ).removeprefix("sha256:")[:20]
    return work_dir / "generic-extraction" / "agent-files" / profile.slug / identity


def _list_profiles(root: pathlib.Path) -> list[dict[str, str]]:
    profiles_root = root / "profiles" / "generic"
    result: list[dict[str, str]] = []
    for child in sorted(profiles_root.iterdir()):
        if not child.is_dir() or not (child / "profile.json").is_file():
            continue
        profile = load_extraction_profile(child.name, root=root)
        result.append(
            {
                "ref": child.name,
                "profile_id": profile.profile_id,
                "profile_version": profile.profile_version,
                "profile_package_hash": profile.package_hash,
            }
        )
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = repo_root()
    try:
        if args.command == "list-profiles":
            profiles = _list_profiles(root)
            if args.json:
                _json_stdout(profiles)
            else:
                for profile in profiles:
                    print(
                        f"{profile['ref']}\t{profile['profile_id']}\t{profile['profile_version']}"
                    )
            return 0

        spec = load_novel_spec(args.spec)
        if args.command == "validate":
            results = validate_generic_work_dir(
                spec,
                args.work_dir,
                profile_ref=args.profile,
                root=root,
                now=utc_now(),
            )
            _json_stdout(
                {
                    "status": "VALID",
                    "profile": args.profile,
                    "corpus_snapshots": [
                        result.corpus_snapshot["corpus_snapshot_id"] for result in results
                    ],
                }
            )
            return 0

        if args.executor == "api":
            if not args.model:
                raise PipelineError("E-MODEL-CONFIG", "--model is required for --executor api")
            executor = OpenAIResponsesClient(model=args.model)
        else:
            if args.model:
                raise PipelineError(
                    "E-MODEL-CONFIG",
                    "--model is not allowed for --executor agent-files",
                )
            executor = GenericAgentFileExecutor(
                _agent_root(args.work_dir, args.profile, args.agent_model_label),
                model_label=args.agent_model_label,
            )
        result = run_generic_corpus_workflow(
            spec,
            args.work_dir,
            profile_ref=args.profile,
            executor=executor,
            root=root,
            now=utc_now(),
        )
        _json_stdout(
            {
                "status": "SUCCEEDED",
                "profile": args.profile,
                "text_snapshot_id": result.extraction.snapshot["text_snapshot_id"],
                "extraction_run_id": result.extraction.run["extraction_run_id"],
                "reused_extraction": result.extraction.reused_extraction,
                "observation_count": result.extraction.run["observation_count"],
                "corpus_record_count": result.corpus_snapshot["corpus_record_count"],
                "corpus_snapshot_id": result.corpus_snapshot["corpus_snapshot_id"],
                "corpus_snapshot": str(result.corpus_snapshot_path),
                "semantic_assurance": result.corpus_snapshot["semantic_assurance"],
            }
        )
        return 0
    except GenericAgentResponsesPending as exc:
        _json_stdout(
            {
                "status": "WAITING_FOR_AGENT",
                "exit_code": 3,
                "pending_count": exc.pending_count,
                "tasks_dir": str(exc.tasks_dir),
                "answers_dir": str(exc.answers_dir),
                "pending": [
                    {
                        "unit_id": item.unit_id,
                        "task": str(item.task_path),
                        "answer": str(item.answer_path),
                    }
                    for item in exc.pending
                ],
            }
        )
        return 3
    except GenericExtractionPartial as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        _json_stdout(
            {
                "status": "PARTIAL",
                "failed_count": len(exc.failed),
                "checkpoint": str(exc.checkpoint_path),
            }
        )
        return 2
    except (PipelineError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
