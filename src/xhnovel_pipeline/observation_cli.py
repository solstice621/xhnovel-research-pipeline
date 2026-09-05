"""Thin command adapters for the observation research contracts.

Semantic planning and open-web search remain host responsibilities. These commands
seal supplied drafts, invoke the native compiler, and rebuild audit views.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .file_io import atomic_write
from .errors import ValidationError
from .generic_handoff import prepare_generic_handoff_from_input, validate_generic_handoff
from .generic_handoff_execution import execute_generic_handoff, validate_generic_execution
from .observation_common import SealedRecord, research_store
from .observation_planning import (
    seal_observation_definition_from_draft,
    seal_observation_work_lead_from_draft,
    seal_profile_resolution_from_draft,
)


def _execution_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--research-root", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--executor", choices=["api", "agent-files"], default="agent-files")
    parser.add_argument("--model", default=None)
    parser.add_argument("--agent-model-label", default="host-code-agent")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry", action="store_true")


def add_observation_parsers(sub: argparse._SubParsersAction) -> None:
    for name in ("seal-observation-definition", "seal-profile-resolution", "seal-observation-work-lead"):
        parser = sub.add_parser(name)
        parser.add_argument("input", type=Path)
        parser.add_argument("--work-dir", type=Path, required=True)
    prepare = sub.add_parser("prepare-generic-handoff")
    prepare.add_argument("input", type=Path)
    prepare.add_argument("--work-dir", type=Path, required=True)
    validate = sub.add_parser("validate-generic-handoff")
    validate.add_argument("handoff", type=Path)
    validate.add_argument("--research-root", type=Path, required=True)
    execute = sub.add_parser("execute-generic-handoff")
    execute.add_argument("handoff", type=Path)
    _execution_arguments(execute)
    receipt = sub.add_parser("validate-generic-execution")
    receipt.add_argument("receipt", type=Path)
    receipt.add_argument("--research-root", type=Path, required=True)
    receipt.add_argument("--work-dir", type=Path)

    campaign = sub.add_parser("observation-research")
    actions = campaign.add_subparsers(dest="observation_action", required=True)
    attach = actions.add_parser("attach", help="freeze host search or source input bytes as audit data")
    attach.add_argument("input", type=Path)
    attach.add_argument("--research-root", type=Path, required=True)
    initialize = actions.add_parser("init")
    initialize.add_argument("input", type=Path)
    initialize.add_argument("--work-dir", type=Path, required=True)
    for name in ("record", "report", "validate"):
        action = actions.add_parser(name)
        action.add_argument("run", type=Path)
        action.add_argument("--research-root", type=Path, required=True)
        if name == "record":
            action.add_argument("input", type=Path)
        elif name == "report":
            action.add_argument("--output", type=Path)
        else:
            action.add_argument("--report", type=Path)
    execute_campaign = actions.add_parser("execute")
    execute_campaign.add_argument("run", type=Path)
    execute_campaign.add_argument("handoff", type=Path)
    _execution_arguments(execute_campaign)


def _emit(value: object) -> None:
    if isinstance(value, SealedRecord):
        value = {"record": value.record, "artifact_id": value.artifact_id, "path": str(value.path)}
    print(json.dumps(value, ensure_ascii=True, indent=2))


def _execution_options(args: argparse.Namespace, root: Path) -> dict:
    return {
        "executor_kind": args.executor, "model": args.model,
        "agent_model_label": args.agent_model_label,
        "resume": args.resume, "retry": args.retry, "root": root,
    }


def _execution_exit(result: dict) -> int:
    statuses = {"SUCCEEDED": 0, "WAITING_FOR_AGENT": 3, "PARTIAL_RETRYABLE": 2,
                "FAILED": 1, "FAILED_PRESTART": 1, "INTERRUPTED": 1}
    if result.get("status") not in statuses:
        raise ValidationError("E-OBSERVATION-CLI-STATUS", "unrecognized execution status")
    return statuses[result["status"]]


def handle_observation_command(args: argparse.Namespace, *, root: Path) -> int | None:
    sealers = {
        "seal-observation-definition": seal_observation_definition_from_draft,
        "seal-profile-resolution": seal_profile_resolution_from_draft,
        "seal-observation-work-lead": seal_observation_work_lead_from_draft,
    }
    if args.cmd in sealers:
        _emit(sealers[args.cmd](args.input, args.work_dir, root=root))
    elif args.cmd == "prepare-generic-handoff":
        _emit(prepare_generic_handoff_from_input(args.input, args.work_dir, root=root))
    elif args.cmd == "validate-generic-handoff":
        record = validate_generic_handoff(args.handoff, args.research_root, root=root)
        _emit({"validation": "PASS", "handoff_id": record["handoff_id"]})
    elif args.cmd == "execute-generic-handoff":
        result = execute_generic_handoff(args.handoff, args.research_root, args.work_dir, **_execution_options(args, root))
        _emit(result)
        return _execution_exit(result)
    elif args.cmd == "validate-generic-execution":
        record = validate_generic_execution(args.receipt, args.research_root, root=root, work_dir=args.work_dir)
        _emit({"validation": "PASS", "receipt_id": record["receipt_id"], "status": record["status"]})
    elif args.cmd == "observation-research":
        if args.observation_action == "attach":
            artifact_id = research_store(args.research_root).put(args.input.read_bytes())
            _emit({"artifact_id": artifact_id, "input_path": str(args.input), "evidence_status": "LEAD_ONLY"})
            return 0
        from .observation_campaign import (
            execute_campaign_handoff, init_observation_research,
            record_observation_research_event, report_observation_research,
            validate_observation_research,
        )
        action = args.observation_action
        if action == "init":
            _emit(init_observation_research(args.input, args.work_dir, root=root))
        elif action == "record":
            _emit(record_observation_research_event(args.run, args.input, args.research_root, root=root))
        elif action == "report":
            report = report_observation_research(args.run, args.research_root, root=root)
            if args.output:
                atomic_write(args.output, (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
            _emit(report)
        elif action == "validate":
            _emit(validate_observation_research(args.run, args.research_root, root=root, report_or_path=args.report))
        else:
            result = execute_campaign_handoff(args.run, args.handoff, args.research_root, args.work_dir, **_execution_options(args, root))
            _emit(result)
            return _execution_exit(result)
    else:
        return None
    return 0
