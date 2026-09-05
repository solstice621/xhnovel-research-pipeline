"""Invocation-aware Generic Handoff execution over the native compiler."""
from __future__ import annotations

import copy
import pathlib
from contextlib import contextmanager
from typing import Any

from .canonical import canonical_dumps
from .errors import ValidationError
from .file_io import write_immutable
from .generic_agent_files import GENERIC_AGENT_FILES_EXECUTOR_KIND, GenericAgentResponsesPending
from .generic_cli import make_generic_executor
from .generic_extraction import (
    GenericExtractionPartial, build_extraction_build, executor_descriptor,
    generic_work_dir_lock, run_generic_corpus_workflow, validate_selected_generic_corpus,
    validate_generic_checkpoint_bytes,
)
from .generic_handoff import resolve_generic_handoff
from .generic_profile import load_extraction_profile
from .hashing import artifact_id_for, object_hash
from .ids import derived_id
from .model_api import API_EXECUTOR_KIND
from .novel_ingest import _lock_file_handle, _unlock_file_handle
from .observation_common import (
    get_record, put_record, read_json, record_path, research_store,
    seal_record, validate_record_identity,
)
from .paths import repo_root
from .runtime import utc_now

EVENT_KIND = "GenericHandoffAttemptEvent"
RECEIPT_KIND = "GenericExtractionExecutionReceipt"
RETURN_STATES = {"WAITING_FOR_AGENT", "PARTIAL_RETRYABLE", "SUCCEEDED", "FAILED"}


@contextmanager
def _handoff_lock(directory: pathlib.Path):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a+b") as handle:
        try:
            _lock_file_handle(handle)
        except OSError as exc:
            raise ValidationError("E-GENERIC-HANDOFF-LOCKED", "handoff is already executing") from exc
        try:
            yield
        finally:
            _unlock_file_handle(handle)


def _binding(resolved: Any, executor: Any, research_root: pathlib.Path, root: pathlib.Path) -> dict[str, Any]:
    profile = load_extraction_profile(resolved.profile_ref, root=root)
    build = build_extraction_build(profile, executor, research_store(research_root), root=root, created_at=utc_now())
    return {
        "input_spec_hash": object_hash(resolved.spec, omit=()),
        "profile_package_hash": profile.package_hash,
        "extraction_build_id": build["extraction_build_id"],
        "extraction_build_hash": build["extraction_build_hash"],
        "executor": executor_descriptor(executor),
    }


def _event_id_check(value: dict[str, Any]) -> dict[str, Any]:
    return validate_record_identity(value, EVENT_KIND, id_field="event_id", hash_field="event_hash")


def _checkpoint_path(work_dir: pathlib.Path, profile_ref: str, binding: dict[str, Any]) -> pathlib.Path:
    # Native paths use the admitted Profile directory name, even for a path ref.
    return (work_dir / "generic-extraction" / "profiles" / pathlib.Path(profile_ref).name / "extractions"
            / binding["extraction_build_id"] / "checkpoint.json")


def _check_checkpoint_binding(data: bytes, binding: dict[str, Any]) -> dict[str, Any]:
    checkpoint = validate_generic_checkpoint_bytes(data)
    if (checkpoint.get("extraction_build_id") != binding["extraction_build_id"]
            or checkpoint.get("extraction_build_hash") != binding["extraction_build_hash"]
            or not isinstance(checkpoint.get("failed"), dict)
            or not isinstance(checkpoint.get("completed"), dict)):
        raise ValidationError("E-GENERIC-RESUME-CHECKPOINT", "checkpoint does not match invocation build")
    return checkpoint


def _validate_checkpoint_event(research_root: pathlib.Path, handoff: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    detail = event["detail"]
    checkpoint = _check_checkpoint_binding(
        research_store(research_root).get(detail["checkpoint_artifact_id"]), event["binding"],
    )
    expected_path = _checkpoint_path(pathlib.Path(event["work_dir"]), handoff["selected_profile"]["profile_ref"], event["binding"])
    if (detail["checkpoint_path"] != str(expected_path)
            or detail["failed_unit_count"] != len(checkpoint["failed"])
            or detail["failures"] != checkpoint["failed"]):
        raise ValidationError("E-GENERIC-HISTORY", "checkpoint return detail differs from frozen state")
    if event["state"] == "WAITING_FOR_AGENT":
        unit_ids = [item["unit_id"] for item in detail["pending"]]
        if (detail["pending_count"] != len(unit_ids) or len(set(unit_ids)) != len(unit_ids)
                or set(unit_ids) & set(checkpoint["completed"])):
            raise ValidationError("E-GENERIC-HISTORY", "pending unit summary differs")
    return checkpoint


def _freeze_checkpoint(research_root: pathlib.Path, work_dir: pathlib.Path,
                       profile_ref: str, binding: dict[str, Any]) -> dict[str, Any]:
    path = _checkpoint_path(work_dir, profile_ref, binding)
    data = path.read_bytes()
    checkpoint = _check_checkpoint_binding(data, binding)
    return {
        "checkpoint_path": str(path), "checkpoint_artifact_id": research_store(research_root).put(data),
        "failed_unit_count": len(checkpoint["failed"]), "failures": copy.deepcopy(checkpoint["failed"]),
    }


def _verify_retained_checkpoint(research_root: pathlib.Path, resolved: Any,
                                work_dir: pathlib.Path, binding: dict[str, Any], history: list) -> None:
    prior_returns = [event for _, event in history
                     if event["state"] in {"WAITING_FOR_AGENT", "PARTIAL_RETRYABLE"}
                     and event["work_dir"] == str(work_dir) and event["binding"] == binding]
    path = _checkpoint_path(work_dir, resolved.profile_ref, binding)
    if not path.exists():
        if prior_returns:
            raise ValidationError("E-GENERIC-RESUME-CHECKPOINT", "previous invocation checkpoint is missing")
        return  # An invocation can be interrupted before native checkpoint creation.
    current = _check_checkpoint_binding(path.read_bytes(), binding)
    if not prior_returns:
        return
    previous = _validate_checkpoint_event(research_root, resolved.handoff, prior_returns[-1])
    for field in ("schema_version", "text_snapshot_id", "text_snapshot_hash", "unit_result_hash"):
        if current.get(field) != previous.get(field):
            raise ValidationError("E-GENERIC-RESUME-CHECKPOINT", "resume changed checkpoint lineage")
    for unit_id, completed in previous["completed"].items():
        if current["completed"].get(unit_id) != completed:
            raise ValidationError("E-GENERIC-RESUME-CHECKPOINT", "resume lost completed unit evidence")
    for unit_id, failed in previous["failed"].items():
        successor = current["completed"].get(unit_id, current["failed"].get(unit_id))
        attempts = failed.get("attempts", [])
        if not isinstance(successor, dict) or successor.get("attempts", [])[:len(attempts)] != attempts:
            raise ValidationError("E-GENERIC-RESUME-CHECKPOINT", "resume lost rejected attempt evidence")


def _history(research_root: pathlib.Path, handoff: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    directory = research_root / "executions" / handoff["handoff_id"] / "events"
    history: list[tuple[str, dict[str, Any]]] = []
    previous = None
    paths = sorted(directory.iterdir()) if directory.exists() else []
    for ordinal, path in enumerate(paths, 1):
        if not path.is_file() or path.is_symlink():
            raise ValidationError("E-GENERIC-HISTORY", "journal entries must be regular files")
        if path.name != f"{ordinal:06d}.json":
            raise ValidationError("E-GENERIC-HISTORY", "nonconsecutive invocation journal")
        visible = read_json(path)
        if path.read_bytes() != canonical_dumps(visible):
            raise ValidationError("E-GENERIC-HISTORY", "journal entry bytes are not canonical")
        aid = artifact_id_for(canonical_dumps(visible))
        event = _event_id_check(get_record(research_root, aid))
        if (event != visible or event["handoff_id"] != handoff["handoff_id"]
                or event["handoff_hash"] != handoff["handoff_hash"]
                or event["sequence"] != ordinal or event["previous_event_artifact_id"] != previous):
            raise ValidationError("E-GENERIC-HISTORY", "invocation journal binding differs")
        if (event["binding"]["input_spec_hash"] != handoff["novel_spec"]["expected_input_spec_hash"]
                or event["binding"]["profile_package_hash"] != handoff["selected_profile"]["package_hash"]
                or str(pathlib.Path(event["work_dir"]).resolve()) != event["work_dir"]):
            raise ValidationError("E-GENERIC-HISTORY", "event spec/profile/work-dir binding differs")
        prior = history[-1][1] if history else None
        if event["state"] == "STARTED":
            if event["invocation_start_artifact_id"] is not None:
                raise ValidationError("E-GENERIC-HISTORY", "start event cannot reference itself")
            if prior is None:
                valid = (event["attempt_ordinal"] == 1 and event["invocation_ordinal"] == 1
                         and event["detail"]["recovery"] == "NORMAL")
            elif event["attempt_id"] == prior["attempt_id"]:
                valid = (prior["state"] in {"STARTED", "WAITING_FOR_AGENT", "PARTIAL_RETRYABLE"}
                         and event["attempt_ordinal"] == prior["attempt_ordinal"]
                         and event["invocation_ordinal"] == prior["invocation_ordinal"] + 1
                         and event["binding"] == prior["binding"] and event["work_dir"] == prior["work_dir"]
                         and event["detail"]["recovery"] == ("RESUME" if prior["state"] == "STARTED" else "NORMAL"))
            else:
                valid = (prior["state"] in {"STARTED", "FAILED"}
                         and event["attempt_ordinal"] == prior["attempt_ordinal"] + 1
                         and event["invocation_ordinal"] == 1
                         and event["detail"]["recovery"] == "RETRY")
            expected_attempt = derived_id("GenericHandoffAttempt", {
                "handoff_id": event["handoff_id"], "attempt_ordinal": event["attempt_ordinal"],
                "work_dir": event["work_dir"], "binding": event["binding"],
            })
            if not valid or event["attempt_id"] != expected_attempt:
                raise ValidationError("E-GENERIC-HISTORY", "illegal attempt/invocation start")
        else:
            if prior is None or prior["state"] != "STARTED" or event["invocation_start_artifact_id"] != previous:
                raise ValidationError("E-GENERIC-HISTORY", "return does not close latest invocation")
            for field in ("attempt_id", "attempt_ordinal", "invocation_ordinal", "work_dir", "binding"):
                if event[field] != prior[field]:
                    raise ValidationError("E-GENERIC-HISTORY", f"return changed {field}")
            if event["state"] in {"SUCCEEDED", "FAILED"}:
                receipt = get_record(research_root, event["detail"]["receipt_artifact_id"])
                validate_record_identity(receipt, RECEIPT_KIND, id_field="receipt_id", hash_field="receipt_hash")
                for field in ("handoff_id", "handoff_hash", "attempt_id", "attempt_ordinal", "invocation_ordinal", "work_dir", "binding", "invocation_start_artifact_id"):
                    if receipt[field] != event[field]:
                        raise ValidationError("E-GENERIC-HISTORY", "receipt and return binding differ")
                if receipt["status"] != event["state"]:
                    raise ValidationError("E-GENERIC-HISTORY", "receipt status differs")
                if receipt["handoff_artifact_id"] != artifact_id_for(canonical_dumps(handoff)):
                    raise ValidationError("E-GENERIC-HISTORY", "receipt handoff artifact differs")
            elif event["state"] in {"WAITING_FOR_AGENT", "PARTIAL_RETRYABLE"}:
                _validate_checkpoint_event(research_root, handoff, event)
        history.append((aid, event))
        previous = aid
    return history


def validate_generic_execution_history(
    handoff_or_path: Any, research_root: pathlib.Path, *, root: pathlib.Path | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Validate the invocation journal and referenced frozen state, without source access.

    Terminal receipts are checked for journal identity here. Consumers that claim
    an execution succeeded must also replay its exact corpus with
    validate_generic_execution or validate_generic_execution_event.
    """
    research_root = pathlib.Path(research_root).resolve()
    resolved = resolve_generic_handoff(handoff_or_path, research_root, root=root, require_source_access=False)
    return _history(research_root, resolved.handoff)


def validate_generic_execution_event(
    event_artifact_id: str, handoff_or_path: Any, research_root: pathlib.Path,
    *, root: pathlib.Path | None = None,
) -> dict[str, Any]:
    """Validate one authoritative invocation return; successful results replay exactly."""
    history = validate_generic_execution_history(handoff_or_path, research_root, root=root)
    matches = [event for aid, event in history if aid == event_artifact_id]
    if len(matches) != 1 or matches[0]["state"] not in RETURN_STATES:
        raise ValidationError("E-GENERIC-HISTORY", "event is not an authoritative invocation return")
    event = matches[0]
    if event["state"] in {"SUCCEEDED", "FAILED"}:
        validate_generic_execution(get_record(research_root, event["detail"]["receipt_artifact_id"]),
                                   research_root, root=root)
    return copy.deepcopy(event)


def _append_event(research_root: pathlib.Path, handoff: dict[str, Any], history: list,
                  *, state: str, attempt_id: str, attempt_ordinal: int, invocation_ordinal: int,
                  work_dir: pathlib.Path, binding: dict[str, Any], detail: dict[str, Any],
                  recorded_at: str, start_artifact_id: str | None = None) -> tuple[str, dict[str, Any]]:
    event = seal_record(EVENT_KIND, {
        "schema_version": "generic-handoff-attempt/v1", "sequence": len(history) + 1,
        "handoff_id": handoff["handoff_id"], "handoff_hash": handoff["handoff_hash"],
        "attempt_id": attempt_id, "attempt_ordinal": attempt_ordinal,
        "invocation_ordinal": invocation_ordinal, "state": state,
        "previous_event_artifact_id": history[-1][0] if history else None,
        "invocation_start_artifact_id": start_artifact_id,
        "work_dir": str(work_dir), "binding": binding, "detail": detail, "recorded_at": recorded_at,
    }, id_field="event_id", hash_field="event_hash")
    aid = put_record(research_root, EVENT_KIND, event)
    path = research_root / "executions" / handoff["handoff_id"] / "events" / f"{len(history)+1:06d}.json"
    write_immutable(path, canonical_dumps(event))
    history.append((aid, event))
    return aid, event


def _corpus_target(result: Any) -> dict[str, Any]:
    extraction = result.extraction
    return {
        "input_spec_hash": extraction.ingestion["input_spec_hash"],
        "ingestion_run_id": extraction.ingestion["ingestion_run_id"],
        "text_snapshot_id": extraction.snapshot["text_snapshot_id"],
        "text_snapshot_hash": extraction.snapshot["text_snapshot_hash"],
        "extraction_build_id": extraction.build["extraction_build_id"],
        "extraction_build_hash": extraction.build["extraction_build_hash"],
        "extraction_run_id": extraction.run["extraction_run_id"],
        "extraction_run_hash": extraction.run["extraction_run_hash"],
        "reduction_run_id": result.reduction_run["reduction_run_id"],
        "reduction_run_hash": result.reduction_run["reduction_run_hash"],
        "corpus_snapshot_id": result.corpus_snapshot["corpus_snapshot_id"],
        "corpus_snapshot_hash": result.corpus_snapshot["corpus_snapshot_hash"],
        "corpus_artifact_id": result.corpus_snapshot["corpus_artifact_id"],
        "corpus_record_count": result.corpus_snapshot["corpus_record_count"],
        "semantic_assurance": result.corpus_snapshot["semantic_assurance"],
        "semantic_coverage": result.corpus_snapshot["semantic_coverage"],
    }


def validate_generic_execution(receipt_or_path: Any, research_root: pathlib.Path, *, root: pathlib.Path | None = None,
                               work_dir: pathlib.Path | None = None) -> dict[str, Any]:
    """Reconstruct precisely the receipt's selected frozen corpus, without model/source access."""
    root = root or repo_root()
    research_root = pathlib.Path(research_root).resolve()
    receipt = read_json(receipt_or_path) if isinstance(receipt_or_path, (str, pathlib.Path)) else copy.deepcopy(receipt_or_path)
    validate_record_identity(receipt, RECEIPT_KIND, id_field="receipt_id", hash_field="receipt_hash")
    if get_record(research_root, artifact_id_for(canonical_dumps(receipt))) != receipt:
        raise ValidationError("E-GENERIC-RECEIPT", "receipt CAS differs")
    handoff = get_record(research_root, receipt["handoff_artifact_id"])
    resolved = resolve_generic_handoff(handoff, research_root, root=root, require_source_access=False)
    if (receipt["handoff_id"] != handoff["handoff_id"] or receipt["handoff_hash"] != handoff["handoff_hash"]
            or receipt["binding"]["input_spec_hash"] != object_hash(resolved.spec, omit=())):
        raise ValidationError("E-GENERIC-RECEIPT", "receipt handoff/spec differs")
    start = _event_id_check(get_record(research_root, receipt["invocation_start_artifact_id"]))
    for field in ("handoff_id", "handoff_hash", "attempt_id", "attempt_ordinal", "invocation_ordinal", "binding", "work_dir"):
        if receipt[field] != start[field]:
            raise ValidationError("E-GENERIC-RECEIPT", f"receipt start binding differs: {field}")
    if start["state"] != "STARTED":
        raise ValidationError("E-GENERIC-RECEIPT", "receipt must reference invocation start")
    histories = _history(research_root, handoff)
    aid = artifact_id_for(canonical_dumps(receipt))
    if not any(e["state"] == receipt["status"] and e["detail"].get("receipt_artifact_id") == aid for _, e in histories):
        raise ValidationError("E-GENERIC-RECEIPT", "receipt has no authoritative return event")
    native_dir = pathlib.Path(receipt["work_dir"])
    if work_dir is not None and pathlib.Path(work_dir).resolve() != native_dir:
        raise ValidationError("E-GENERIC-RECEIPT", "work-dir differs from receipt")
    if receipt["status"] == "FAILED":
        return receipt
    target = receipt["result"]
    selected = validate_selected_generic_corpus(
        resolved.spec, native_dir, profile_ref=resolved.profile_ref,
        extraction_run_id=target["extraction_run_id"], reduction_run_id=target["reduction_run_id"],
        corpus_snapshot_id=target["corpus_snapshot_id"], root=root,
    )
    profile = load_extraction_profile(resolved.profile_ref, root=root)
    if (_corpus_target(selected) != target or selected.extraction.build["extraction_build_hash"] != receipt["binding"]["extraction_build_hash"]
            or selected.extraction.build["extraction_build_id"] != receipt["binding"]["extraction_build_id"]
            or selected.extraction.build["executor"] != receipt["binding"]["executor"]
            or selected.corpus_snapshot["profile_package_hash"] != receipt["binding"]["profile_package_hash"]
            or profile.package_hash != receipt["binding"]["profile_package_hash"]):
        raise ValidationError("E-GENERIC-RECEIPT", "selected corpus differs from receipt binding")
    return receipt


def execute_generic_handoff(handoff_path: Any, research_root: pathlib.Path, work_dir: pathlib.Path, *,
                            executor_kind: str = "agent-files", executor: Any = None, model: str | None = None,
                            agent_model_label: str = "host-code-agent", root: pathlib.Path | None = None,
                            resume: bool = False, retry: bool = False, now: str | None = None) -> dict[str, Any]:
    root = root or repo_root()
    research_root, work_dir = pathlib.Path(research_root).resolve(), pathlib.Path(work_dir).resolve()
    resolved = resolve_generic_handoff(handoff_path, research_root, root=root, require_source_access=False)
    handoff = resolved.handoff
    if resume and retry:
        raise ValidationError("E-GENERIC-RESUME", "choose resume or retry")
    directory = research_root / "executions" / handoff["handoff_id"]
    with _handoff_lock(directory), generic_work_dir_lock(work_dir) as lock_token:
        executor = executor if executor is not None else make_generic_executor(
            executor_kind, work_dir, resolved.profile_ref, model=model, agent_model_label=agent_model_label, root=root)
        expected_kind = {"agent-files": GENERIC_AGENT_FILES_EXECUTOR_KIND, "api": API_EXECUTOR_KIND}.get(executor_kind)
        if expected_kind is None or getattr(executor, "executor_kind", None) != expected_kind:
            raise ValidationError("E-GENERIC-EXECUTOR", "executor descriptor does not match chosen native executor")
        if model and (executor_kind != "api" or model != executor.model):
            raise ValidationError("E-GENERIC-EXECUTOR", "model argument differs from native executor")
        binding = _binding(resolved, executor, research_root, root)
        history = _history(research_root, handoff)
        last = history[-1][1] if history else None
        if last and last["state"] == "SUCCEEDED":
            if retry or resume or last["binding"] != binding or last["work_dir"] != str(work_dir):
                raise ValidationError("E-GENERIC-ATTEMPT-IDENTITY", "completed attempt binding/flags differ")
            receipt = validate_generic_execution(get_record(research_root, last["detail"]["receipt_artifact_id"]), research_root, root=root, work_dir=work_dir)
            return {"status": "SUCCEEDED", "receipt": receipt, "receipt_path": str(record_path(research_root, RECEIPT_KIND, last["detail"]["receipt_artifact_id"])),
                    "receipt_artifact_id": last["detail"]["receipt_artifact_id"], "reused_terminal_receipt": True,
                    "event_artifact_id": history[-1][0], "attempt_id": last["attempt_id"]}
        if last and last["state"] in {"STARTED", "FAILED"}:
            if last["state"] == "FAILED" and not retry:
                raise ValidationError("E-GENERIC-RETRY-REQUIRED", "failed attempt requires explicit retry")
            if last["state"] == "STARTED" and not (resume or retry):
                raise ValidationError("E-GENERIC-INTERRUPTED", "interrupted invocation requires explicit resume or retry")
        elif retry or resume:
            raise ValidationError("E-GENERIC-RESUME", "flags require a failed/interrupted invocation")
        same_attempt = last is not None and not retry
        if same_attempt:
            if last["binding"] != binding or last["work_dir"] != str(work_dir):
                raise ValidationError("E-GENERIC-ATTEMPT-IDENTITY", "resume changed spec/profile/runtime/executor/work-dir")
            attempt_ordinal = last["attempt_ordinal"]
            invocation_ordinal = last["invocation_ordinal"] + 1
        else:
            attempt_ordinal = last["attempt_ordinal"] + 1 if last else 1
            invocation_ordinal = 1
        attempt_id = derived_id("GenericHandoffAttempt", {
            "handoff_id": handoff["handoff_id"], "attempt_ordinal": attempt_ordinal,
            "work_dir": str(work_dir), "binding": binding,
        })
        shared = dict(attempt_id=attempt_id, attempt_ordinal=attempt_ordinal, invocation_ordinal=invocation_ordinal,
                      work_dir=work_dir, binding=binding)
        started_aid, _ = _append_event(research_root, handoff, history, state="STARTED", **shared,
                                       detail={"recovery": "RETRY" if retry else "RESUME" if resume else "NORMAL"}, recorded_at=now or utc_now())
        stage = "SOURCE_PREFLIGHT"
        try:
            resolved = resolve_generic_handoff(handoff_path, research_root, root=root, require_source_access=True)
            stage = "RESUME_PREFLIGHT"
            _verify_retained_checkpoint(research_root, resolved, work_dir, binding, history[:-1])
            stage = "NATIVE_EXTRACTION"
            try:
                result = run_generic_corpus_workflow(resolved.spec, work_dir, profile_ref=resolved.profile_ref,
                                                    executor=executor, root=root, now=now or utc_now(), lock_token=lock_token)
            except (GenericAgentResponsesPending, GenericExtractionPartial) as pending:
                if binding != _binding(resolved, executor, research_root, root):
                    raise ValidationError("E-GENERIC-EXECUTION-BIND", "native binding changed during incomplete invocation")
                detail = _freeze_checkpoint(research_root, work_dir, resolved.profile_ref, binding)
                if isinstance(pending, GenericAgentResponsesPending):
                    status = "WAITING_FOR_AGENT"
                    detail.update(pending_count=pending.pending_count, pending=[
                        {"unit_id": item.unit_id, "task": str(item.task_path), "answer": str(item.answer_path)}
                        for item in pending.pending
                    ])
                else:
                    status = "PARTIAL_RETRYABLE"
                    if pending.checkpoint_path != pathlib.Path(detail["checkpoint_path"]) or pending.failed != detail["failures"]:
                        raise ValidationError("E-GENERIC-EXECUTION-BIND", "partial checkpoint differs from native failure")
                aid, _ = _append_event(research_root, handoff, history, state=status, **shared,
                    detail=detail, recorded_at=now or utc_now(), start_artifact_id=started_aid)
                return {"status": status, "attempt_id": attempt_id, "event_artifact_id": aid, **detail}
            stage = "SELECTED_VALIDATION"
            target = _corpus_target(result)
            selected = validate_selected_generic_corpus(resolved.spec, work_dir, profile_ref=resolved.profile_ref,
                extraction_run_id=target["extraction_run_id"], reduction_run_id=target["reduction_run_id"],
                corpus_snapshot_id=target["corpus_snapshot_id"], root=root)
            if (_corpus_target(selected) != target or binding != _binding(resolved, executor, research_root, root)
                    or target["extraction_build_hash"] != binding["extraction_build_hash"]
                    or selected.corpus_snapshot["profile_package_hash"] != binding["profile_package_hash"]):
                raise ValidationError("E-GENERIC-EXECUTION-BIND", "native output/build changed during invocation")
            status, payload = "SUCCEEDED", {"result": target, "validation": "PASS"}
        except Exception as exc:
            # Programming/integrity failures are terminal audit outcomes too.
            # BaseException (host interruption/process exit) deliberately leaves
            # the invocation-start marker open for explicit recovery.
            status = "FAILED"
            payload = {"error": {"stage": stage, "code": getattr(exc, "code", "E-GENERIC-EXECUTION"), "message": str(exc)}}
        receipt = seal_record(RECEIPT_KIND, {
            "schema_version": "generic-execution-receipt/v1", "handoff_id": handoff["handoff_id"],
            "handoff_hash": handoff["handoff_hash"], "handoff_artifact_id": put_record(research_root, "GenericExtractionHandoff", handoff),
            "attempt_id": attempt_id, "attempt_ordinal": attempt_ordinal, "invocation_ordinal": invocation_ordinal,
            "invocation_start_artifact_id": started_aid, "work_dir": str(work_dir), "binding": binding,
            "status": status, "recorded_at": now or utc_now(), **payload,
        }, id_field="receipt_id", hash_field="receipt_hash")
        receipt_aid = put_record(research_root, RECEIPT_KIND, receipt)
        event_aid, _ = _append_event(research_root, handoff, history, state=status, **shared,
            detail={"receipt_artifact_id": receipt_aid}, recorded_at=now or utc_now(), start_artifact_id=started_aid)
        return {"status": status, "receipt": receipt, "receipt_artifact_id": receipt_aid,
                "receipt_path": str(record_path(research_root, RECEIPT_KIND, receipt_aid)), "reused_terminal_receipt": False,
                "event_artifact_id": event_aid, "attempt_id": attempt_id}
