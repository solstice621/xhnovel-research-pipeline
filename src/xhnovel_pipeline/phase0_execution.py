"""Receipt-managed execution of a validated Phase 0 Evidence Handoff.

This module is a thin authority boundary around the existing ``run_novel_research``
path.  It owns only immutable attempt bookkeeping, cross-process exclusion, and the
post-run lineage receipt.  Ingestion, Scene Scout, merge, replay, and export remain
native Evidence Compiler responsibilities.
"""

from __future__ import annotations

import copy
import json
import os
import pathlib
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable

from .agent_files import AGENT_FILES_EXECUTOR_KIND, AgentResponsesPending
from .catalog import Catalog
from .constants import SCHEMA_VERSION
from .errors import PipelineError, SchemaError, ValidationError
from .hashing import object_hash
from .ids import derived_id
from .model_api import API_EXECUTOR_KIND
from .novel_ingest import _lock_file_handle, _unlock_file_handle
from .novel_workflow import run_novel_research
from .phase0_builder import resolve_validated_handoff_input, validate_evidence_handoff
from .schema import validate_schema
from .store import ArtifactStore
from .validate import validate_all

EXECUTION_LOCK_NAME = ".execute-handoff.lock"
EXECUTORS = frozenset({"api", "agent-files"})


@dataclass(frozen=True)
class HandoffAttemptHistory:
    attempt_id: str
    attempt_ordinal: int
    executor: str
    work_dir: pathlib.Path
    state: str
    events: tuple[dict[str, Any], ...]
    receipt: dict[str, Any] | None
    receipt_path: pathlib.Path


@dataclass(frozen=True)
class HandoffExecutionResult:
    status: str
    attempt_id: str
    attempt_ordinal: int
    receipt: dict[str, Any]
    receipt_path: pathlib.Path
    work_dir: pathlib.Path
    reused_terminal_receipt: bool
    native_result: dict[str, Any] | None


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def _atomic_write_immutable(path: pathlib.Path, data: bytes) -> None:
    """Publish complete bytes once without an overwrite window on POSIX or Windows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temp_path = pathlib.Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp_path, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValidationError(
                    "E-HANDOFF-ATTEMPT-IMMUTABLE",
                    f"refusing to overwrite immutable execution record {path}",
                )
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


@contextmanager
def _exclusive_execution(execution_dir: pathlib.Path):
    """Reuse the native ingestion lock primitive for the outer execution lock."""
    execution_dir.mkdir(parents=True, exist_ok=True)
    lock_path = execution_dir / EXECUTION_LOCK_NAME
    try:
        handle = lock_path.open("a+b")
    except OSError as exc:
        raise ValidationError(
            "E-HANDOFF-EXECUTION-LOCK",
            f"cannot open Handoff execution lock {lock_path}",
        ) from exc
    locked = False
    try:
        try:
            _lock_file_handle(handle)
            locked = True
        except OSError as exc:
            raise ValidationError(
                "E-HANDOFF-EXECUTION-LOCKED",
                f"another process is executing the Handoff under {execution_dir}",
            ) from exc
        yield
    finally:
        try:
            if locked:
                _unlock_file_handle(handle)
        finally:
            handle.close()


def _phase0_root_for_handoff(
    handoff_path: pathlib.Path,
    phase0_root: pathlib.Path | None,
) -> pathlib.Path:
    path = pathlib.Path(handoff_path).resolve()
    return pathlib.Path(phase0_root).resolve() if phase0_root is not None else path.parents[2]


def _execution_dir(phase0_root: pathlib.Path, handoff_id: str) -> pathlib.Path:
    return phase0_root / "executions" / handoff_id


def _attempt_key(attempt_ordinal: int, attempt_id: str) -> str:
    return f"{attempt_ordinal:06d}-{attempt_id}"


def _marker_path(
    execution_dir: pathlib.Path,
    attempt_ordinal: int,
    attempt_id: str,
) -> pathlib.Path:
    return execution_dir / "started-markers" / f"{_attempt_key(attempt_ordinal, attempt_id)}.json"


def _waiting_dir(
    execution_dir: pathlib.Path,
    attempt_ordinal: int,
    attempt_id: str,
) -> pathlib.Path:
    return execution_dir / "waiting-events" / _attempt_key(attempt_ordinal, attempt_id)


def _receipt_path(
    execution_dir: pathlib.Path,
    attempt_ordinal: int,
    attempt_id: str,
) -> pathlib.Path:
    return execution_dir / "receipts" / f"{_attempt_key(attempt_ordinal, attempt_id)}.json"


def _attempt_id_for(
    handoff: dict[str, Any],
    *,
    executor: str,
    work_dir: str,
    attempt_ordinal: int,
) -> str:
    return derived_id(
        "HandoffAttempt",
        {
            "handoff_id": handoff["handoff_id"],
            "handoff_hash": handoff["handoff_hash"],
            "expected_input_spec_hash": handoff["novel_spec"][
                "expected_input_spec_hash"
            ],
            "executor": executor,
            "work_dir": work_dir,
            "attempt_ordinal": attempt_ordinal,
        },
    )


def _make_attempt_event(
    handoff: dict[str, Any],
    *,
    attempt_id: str,
    attempt_ordinal: int,
    event_ordinal: int,
    state: str,
    executor: str,
    work_dir: str,
    recorded_at: str,
    pending_count: int | None = None,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "attempt_id": attempt_id,
        "handoff_id": handoff["handoff_id"],
        "attempt_ordinal": attempt_ordinal,
        "event_ordinal": event_ordinal,
        "state": state,
        "executor": executor,
        "work_dir": work_dir,
        "recorded_at": recorded_at,
    }
    if pending_count is not None:
        base["pending_count"] = pending_count
    event_id = derived_id("HandoffAttemptEvent", base)
    event = {**base, "event_id": event_id, "event_hash": "sha256:" + "0" * 64}
    event["event_hash"] = object_hash(event, omit=("event_hash",))
    validate_schema("HandoffAttemptEvent", event)
    return event


def _validate_attempt_event(event: dict[str, Any]) -> dict[str, Any]:
    validate_schema("HandoffAttemptEvent", event)
    identity = {
        key: copy.deepcopy(value)
        for key, value in event.items()
        if key not in {"event_id", "event_hash"}
    }
    expected_id = derived_id("HandoffAttemptEvent", identity)
    expected_hash = object_hash(event, omit=("event_hash",))
    if event["event_id"] != expected_id or event["event_hash"] != expected_hash:
        raise ValidationError(
            "E-HANDOFF-ATTEMPT-INTEGRITY",
            "Handoff attempt event identity changed",
        )
    return copy.deepcopy(event)


def _make_execution_receipt(base: dict[str, Any]) -> dict[str, Any]:
    receipt_id = derived_id("EvidenceHandoffExecutionReceipt", base)
    receipt = {
        **copy.deepcopy(base),
        "receipt_id": receipt_id,
        "receipt_hash": "sha256:" + "0" * 64,
    }
    receipt["receipt_hash"] = object_hash(receipt, omit=("receipt_hash",))
    validate_schema("EvidenceHandoffExecutionReceipt", receipt)
    return receipt


def _validate_execution_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    validate_schema("EvidenceHandoffExecutionReceipt", receipt)
    identity = {
        key: copy.deepcopy(value)
        for key, value in receipt.items()
        if key not in {"receipt_id", "receipt_hash"}
    }
    expected_id = derived_id("EvidenceHandoffExecutionReceipt", identity)
    expected_hash = object_hash(receipt, omit=("receipt_hash",))
    if receipt["receipt_id"] != expected_id or receipt["receipt_hash"] != expected_hash:
        raise ValidationError(
            "E-HANDOFF-RECEIPT-INTEGRITY",
            "Handoff execution receipt identity changed",
        )
    return copy.deepcopy(receipt)


def _read_exact_record(
    path: pathlib.Path,
    *,
    label: str,
    validator: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(
            "E-HANDOFF-ATTEMPT-INTEGRITY",
            f"invalid {label}: {path}",
        ) from exc
    if not isinstance(value, dict) or raw != _json_bytes(value):
        raise ValidationError(
            "E-HANDOFF-ATTEMPT-INTEGRITY",
            f"{label} is not canonical execution output: {path}",
        )
    try:
        return validator(value)
    except SchemaError as exc:
        raise ValidationError(
            "E-HANDOFF-ATTEMPT-INTEGRITY",
            f"{label} violates its execution contract: {path}",
        ) from exc


def _json_files_only(path: pathlib.Path, *, label: str) -> list[pathlib.Path]:
    if not path.exists():
        return []
    if not path.is_dir():
        raise ValidationError(
            "E-HANDOFF-ATTEMPT-INTEGRITY",
            f"{label} is not a directory: {path}",
        )
    entries = sorted(path.iterdir(), key=lambda item: item.name)
    if any(not item.is_file() or item.suffix != ".json" for item in entries):
        raise ValidationError(
            "E-HANDOFF-ATTEMPT-INTEGRITY",
            f"{label} contains an unsupported entry",
        )
    return entries


def _bind_event_to_handoff(event: dict[str, Any], handoff: dict[str, Any]) -> None:
    recorded_work_dir = pathlib.Path(event["work_dir"])
    if (
        not recorded_work_dir.is_absolute()
        or str(recorded_work_dir.resolve()) != event["work_dir"]
    ):
        raise ValidationError(
            "E-HANDOFF-ATTEMPT-BIND",
            "Handoff attempt work-dir must be an absolute resolved path",
        )
    expected_attempt_id = _attempt_id_for(
        handoff,
        executor=event["executor"],
        work_dir=event["work_dir"],
        attempt_ordinal=event["attempt_ordinal"],
    )
    if event["handoff_id"] != handoff["handoff_id"] or event["attempt_id"] != expected_attempt_id:
        raise ValidationError(
            "E-HANDOFF-ATTEMPT-BIND",
            "Handoff attempt marker differs from the validated Handoff identity",
        )


def _read_attempt_history(
    handoff: dict[str, Any],
    execution_dir: pathlib.Path,
) -> tuple[HandoffAttemptHistory, ...]:
    marker_paths = _json_files_only(execution_dir / "started-markers", label="started marker set")
    markers: dict[str, dict[str, Any]] = {}
    for marker_path in marker_paths:
        marker = _read_exact_record(
            marker_path,
            label="STARTED marker",
            validator=_validate_attempt_event,
        )
        if marker["state"] != "STARTED" or marker["event_ordinal"] != 1:
            raise ValidationError(
                "E-HANDOFF-ATTEMPT-INTEGRITY",
                "attempt must begin with event ordinal 1 in STARTED state",
            )
        _bind_event_to_handoff(marker, handoff)
        key = _attempt_key(marker["attempt_ordinal"], marker["attempt_id"])
        if marker_path.name != f"{key}.json" or key in markers:
            raise ValidationError(
                "E-HANDOFF-ATTEMPT-INTEGRITY",
                "STARTED marker filename or identity is ambiguous",
            )
        markers[key] = marker

    receipt_paths = _json_files_only(execution_dir / "receipts", label="receipt set")
    receipts: dict[str, tuple[dict[str, Any], pathlib.Path]] = {}
    for receipt_path in receipt_paths:
        receipt = _read_exact_record(
            receipt_path,
            label="terminal execution receipt",
            validator=_validate_execution_receipt,
        )
        key = _attempt_key(receipt["attempt_ordinal"], receipt["attempt_id"])
        if receipt_path.name != f"{key}.json" or key in receipts or key not in markers:
            raise ValidationError(
                "E-HANDOFF-RECEIPT-INTEGRITY",
                "terminal receipt does not bind exactly one STARTED marker",
            )
        receipts[key] = (receipt, receipt_path)

    waiting_root = execution_dir / "waiting-events"
    waiting_by_key: dict[str, list[dict[str, Any]]] = {}
    if waiting_root.exists():
        if not waiting_root.is_dir():
            raise ValidationError(
                "E-HANDOFF-ATTEMPT-INTEGRITY",
                "waiting event set is not a directory",
            )
        for attempt_dir in sorted(waiting_root.iterdir(), key=lambda item: item.name):
            if not attempt_dir.is_dir() or attempt_dir.name not in markers:
                raise ValidationError(
                    "E-HANDOFF-ATTEMPT-INTEGRITY",
                    "waiting event directory has no matching STARTED marker",
                )
            waiting = []
            for event_path in _json_files_only(attempt_dir, label="WAITING event set"):
                event = _read_exact_record(
                    event_path,
                    label="WAITING event",
                    validator=_validate_attempt_event,
                )
                key = _attempt_key(event["attempt_ordinal"], event["attempt_id"])
                expected_name = f"{event['event_ordinal']:06d}-{event['event_id']}.json"
                if event["state"] != "WAITING_FOR_AGENT" or key != attempt_dir.name:
                    raise ValidationError(
                        "E-HANDOFF-ATTEMPT-INTEGRITY",
                        "WAITING event differs from its attempt",
                    )
                if event_path.name != expected_name:
                    raise ValidationError(
                        "E-HANDOFF-ATTEMPT-INTEGRITY",
                        "WAITING event filename differs from its identity",
                    )
                _bind_event_to_handoff(event, handoff)
                waiting.append(event)
            waiting_by_key[attempt_dir.name] = waiting

    histories = []
    for key, marker in markers.items():
        waiting = sorted(
            waiting_by_key.get(key, []),
            key=lambda event: event["event_ordinal"],
        )
        events = [marker, *waiting]
        if [event["event_ordinal"] for event in events] != list(range(1, len(events) + 1)):
            raise ValidationError(
                "E-HANDOFF-ATTEMPT-INTEGRITY",
                "attempt event ordinals are not contiguous",
            )
        if any(
            event[field] != marker[field]
            for event in waiting
            for field in (
                "attempt_id",
                "handoff_id",
                "attempt_ordinal",
                "executor",
                "work_dir",
            )
        ):
            raise ValidationError(
                "E-HANDOFF-ATTEMPT-BIND",
                "WAITING event identity differs from STARTED",
            )
        receipt_pair = receipts.get(key)
        receipt = receipt_pair[0] if receipt_pair else None
        receipt_path = (
            receipt_pair[1]
            if receipt_pair
            else _receipt_path(
                execution_dir,
                marker["attempt_ordinal"],
                marker["attempt_id"],
            )
        )
        if receipt is not None and any(
            receipt[field] != marker[field]
            for field in ("attempt_id", "handoff_id", "attempt_ordinal", "executor")
        ):
            raise ValidationError(
                "E-HANDOFF-RECEIPT-BIND",
                "terminal receipt identity differs from STARTED",
            )
        if (
            receipt is not None
            and receipt["expected_input_spec_hash"]
            != handoff["novel_spec"]["expected_input_spec_hash"]
        ):
            raise ValidationError(
                "E-HANDOFF-RECEIPT-BIND",
                "terminal receipt expected spec hash differs from the Handoff",
            )
        state = (
            receipt["status"]
            if receipt is not None
            else "WAITING_FOR_AGENT"
            if waiting
            else "INTERRUPTED"
        )
        histories.append(
            HandoffAttemptHistory(
                attempt_id=marker["attempt_id"],
                attempt_ordinal=marker["attempt_ordinal"],
                executor=marker["executor"],
                work_dir=pathlib.Path(marker["work_dir"]),
                state=state,
                events=tuple(copy.deepcopy(events)),
                receipt=copy.deepcopy(receipt),
                receipt_path=receipt_path,
            )
        )

    histories.sort(key=lambda item: item.attempt_ordinal)
    if [item.attempt_ordinal for item in histories] != list(range(1, len(histories) + 1)):
        raise ValidationError(
            "E-HANDOFF-ATTEMPT-INTEGRITY",
            "attempt ordinals are not contiguous",
        )
    if any(item.state not in {"FAILED", "INTERRUPTED"} for item in histories[:-1]):
        raise ValidationError(
            "E-HANDOFF-ATTEMPT-STATE",
            "a later retry follows a non-retryable attempt state",
        )
    return tuple(histories)


def _catalog_from_json(path: pathlib.Path) -> Catalog:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(
            "E-HANDOFF-EXECUTION-CATALOG",
            f"invalid execution catalog {path}",
        ) from exc
    if not isinstance(value, dict):
        raise ValidationError("E-HANDOFF-EXECUTION-CATALOG", "catalog must be an object")
    catalog = Catalog()
    for kind, records in value.items():
        if not isinstance(records, list):
            raise ValidationError(
                "E-HANDOFF-EXECUTION-CATALOG",
                f"catalog {kind} must be an array",
            )
        for record in records:
            catalog.add(kind, record)
    return catalog


def _one(records: list[dict[str, Any]], *, label: str) -> dict[str, Any]:
    if len(records) != 1:
        raise ValidationError(
            "E-HANDOFF-LINEAGE",
            f"successful Handoff must resolve exactly one {label}; found {len(records)}",
        )
    return records[0]


def verify_handoff_execution(
    handoff: dict[str, Any],
    output_catalog: Catalog,
    store: ArtifactStore,
    *,
    attempt_id: str,
    attempt_ordinal: int,
    executor: str,
    recorded_at: str,
) -> dict[str, Any]:
    """Validate the full core closure and return a terminal SUCCEEDED receipt."""
    validate_schema("EvidenceHandoff", handoff)
    validate_all(output_catalog, store)
    expected_hash = handoff["novel_spec"]["expected_input_spec_hash"]
    ingestion = _one(output_catalog.all("NovelIngestionRun"), label="NovelIngestionRun")
    if ingestion["input_spec_hash"] != expected_hash:
        raise ValidationError(
            "E-HANDOFF-SPEC-HASH",
            "NovelIngestionRun input_spec_hash differs from EvidenceHandoff",
        )

    snapshots = [
        snapshot
        for snapshot in output_catalog.all("CollectionSnapshot")
        if snapshot["ingestion_run_id"] == ingestion["ingestion_run_id"]
    ]
    snapshot = _one(snapshots, label="CollectionSnapshot")
    bundles = [
        bundle
        for bundle in output_catalog.all("EvidenceBundle")
        if bundle["request_id"] == snapshot["request_id"]
        and bundle["collection_snapshot_ids"] == [snapshot["snapshot_id"]]
    ]
    bundle = _one(bundles, label="EvidenceBundle")
    request = output_catalog.get("ResearchRequest", bundle["request_id"])
    scout = _one(
        [
            run
            for run in output_catalog.all("SceneScoutRun")
            if run["bundle_id"] == bundle["bundle_id"]
        ],
        label="SceneScoutRun",
    )
    merge = _one(
        [
            run
            for run in output_catalog.all("SceneMergeRun")
            if run["scene_scout_run_id"] == scout["scene_scout_run_id"]
        ],
        label="SceneMergeRun",
    )
    export = _one(
        [
            item
            for item in output_catalog.all("EvidenceExport")
            if item["bundle"]["bundle_id"] == bundle["bundle_id"]
            and item["scene_discovery"]["scene_scout_run_id"]
            == scout["scene_scout_run_id"]
            and item["scene_discovery"]["merge_run_id"] == merge["merge_run_id"]
        ],
        label="EvidenceExport",
    )
    return _make_execution_receipt(
        {
            "schema_version": SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "handoff_id": handoff["handoff_id"],
            "attempt_ordinal": attempt_ordinal,
            "status": "SUCCEEDED",
            "executor": executor,
            "expected_input_spec_hash": expected_hash,
            "actual_input_spec_hash": ingestion["input_spec_hash"],
            "ingestion_run_id": ingestion["ingestion_run_id"],
            "request_id": request["request_id"],
            "bundle_id": bundle["bundle_id"],
            "scene_scout_run_id": scout["scene_scout_run_id"],
            "merge_run_id": merge["merge_run_id"],
            "export_id": export["export_id"],
            "validate_all": "PASS",
            "recorded_at": recorded_at,
        }
    )


def _verify_success_receipt(
    handoff: dict[str, Any],
    attempt: HandoffAttemptHistory,
) -> None:
    receipt = attempt.receipt
    if receipt is None or receipt["status"] != "SUCCEEDED":
        return
    catalog_path = attempt.work_dir / "research" / receipt["scene_scout_run_id"] / "catalog.json"
    catalog = _catalog_from_json(catalog_path)
    store = ArtifactStore(attempt.work_dir / "ingestion" / "objects")
    rebuilt = verify_handoff_execution(
        handoff,
        catalog,
        store,
        attempt_id=attempt.attempt_id,
        attempt_ordinal=attempt.attempt_ordinal,
        executor=attempt.executor,
        recorded_at=receipt["recorded_at"],
    )
    if rebuilt != receipt:
        raise ValidationError(
            "E-HANDOFF-RECEIPT-REPLAY",
            "SUCCEEDED receipt differs from fresh catalog lineage validation",
        )


def validate_handoff_execution_history(
    handoff_path: pathlib.Path,
    *,
    phase0_root: pathlib.Path | None = None,
) -> tuple[HandoffAttemptHistory, ...]:
    """Validate all immutable markers and replay every successful terminal receipt."""
    path = pathlib.Path(handoff_path).resolve()
    root = _phase0_root_for_handoff(path, phase0_root)
    handoff = validate_evidence_handoff(path, phase0_root=root)
    execution_dir = _execution_dir(root, handoff["handoff_id"])
    if not execution_dir.exists():
        return ()
    with _exclusive_execution(execution_dir):
        histories = _read_attempt_history(handoff, execution_dir)
        for attempt in histories:
            _verify_success_receipt(handoff, attempt)
        return histories


def _failure_stage(exc: Exception, *, validating: bool) -> str:
    if validating:
        return "VALIDATION"
    code = exc.code if isinstance(exc, PipelineError) else ""
    if code.startswith(("E-NOVEL", "E-CHAPTER", "E-PARSE")):
        return "INGESTION"
    if code.startswith(("E-BUNDLE", "E-COLLECTION", "E-TRIAGE", "E-RIGHTS")):
        return "EVIDENCE_BUNDLE"
    if code.startswith(("E-SCENE", "E-AGENT", "E-MODEL", "E-CITATION")):
        return "SCENE_SCOUT"
    return "UNKNOWN"


def _failure_receipt(
    handoff: dict[str, Any],
    *,
    attempt_id: str,
    attempt_ordinal: int,
    executor: str,
    recorded_at: str,
    stage: str,
    exc: Exception,
) -> dict[str, Any]:
    error_code = (
        exc.code
        if isinstance(exc, PipelineError)
        else f"E-NATIVE-{type(exc).__name__.upper()}"
    )
    return _make_execution_receipt(
        {
            "schema_version": SCHEMA_VERSION,
            "attempt_id": attempt_id,
            "handoff_id": handoff["handoff_id"],
            "attempt_ordinal": attempt_ordinal,
            "status": "FAILED",
            "executor": executor,
            "expected_input_spec_hash": handoff["novel_spec"][
                "expected_input_spec_hash"
            ],
            "stage": stage,
            "error_code": error_code,
            "error_message": str(exc) or type(exc).__name__,
            "recorded_at": recorded_at,
        }
    )


def _check_executor_client(executor: str, client: Any) -> None:
    expected_kind = API_EXECUTOR_KIND if executor == "api" else AGENT_FILES_EXECUTOR_KIND
    if getattr(client, "executor_kind", None) != expected_kind:
        raise ValidationError(
            "E-HANDOFF-EXECUTOR",
            f"executor {executor} does not match the native Scene Scout client",
        )


def execute_evidence_handoff(
    handoff_path: pathlib.Path,
    work_dir: pathlib.Path,
    *,
    executor: str,
    extractor_factory: Callable[[], Any],
    repo_root: pathlib.Path,
    now: str,
    retry: bool = False,
    phase0_root: pathlib.Path | None = None,
    fetcher: Any | None = None,
) -> HandoffExecutionResult:
    """Execute or resume one Handoff through the unchanged native research path."""
    if executor not in EXECUTORS:
        raise ValidationError("E-HANDOFF-EXECUTOR", f"unsupported executor {executor!r}")
    if not isinstance(retry, bool):
        raise ValidationError("E-HANDOFF-RETRY", "retry must be a boolean")
    path = pathlib.Path(handoff_path).resolve()
    root = _phase0_root_for_handoff(path, phase0_root)
    validated_input = resolve_validated_handoff_input(path, phase0_root=root)
    handoff = validated_input.handoff
    execution_spec = validated_input.execution_spec
    resolved_work_dir = pathlib.Path(work_dir).expanduser().resolve()
    work_dir_text = str(resolved_work_dir)
    execution_dir = _execution_dir(root, handoff["handoff_id"])

    with _exclusive_execution(execution_dir):
        histories = _read_attempt_history(handoff, execution_dir)
        for prior in histories:
            _verify_success_receipt(handoff, prior)
        latest = histories[-1] if histories else None

        if latest is None:
            if retry:
                raise ValidationError(
                    "E-HANDOFF-RETRY",
                    "--retry requires a prior FAILED or INTERRUPTED attempt",
                )
            attempt_ordinal = 1
            attempt_id = _attempt_id_for(
                handoff,
                executor=executor,
                work_dir=work_dir_text,
                attempt_ordinal=attempt_ordinal,
            )
            started = _make_attempt_event(
                handoff,
                attempt_id=attempt_id,
                attempt_ordinal=attempt_ordinal,
                event_ordinal=1,
                state="STARTED",
                executor=executor,
                work_dir=work_dir_text,
                recorded_at=now,
            )
            _atomic_write_immutable(
                _marker_path(execution_dir, attempt_ordinal, attempt_id),
                _json_bytes(started),
            )
            events = (started,)
        elif latest.state == "SUCCEEDED":
            if retry:
                raise ValidationError("E-HANDOFF-RETRY", "SUCCEEDED attempt cannot be retried")
            if latest.executor != executor or latest.work_dir != resolved_work_dir:
                raise ValidationError(
                    "E-HANDOFF-ATTEMPT-IDENTITY",
                    "executor or work-dir differs from the completed attempt",
                )
            assert latest.receipt is not None
            return HandoffExecutionResult(
                status="SUCCEEDED",
                attempt_id=latest.attempt_id,
                attempt_ordinal=latest.attempt_ordinal,
                receipt=latest.receipt,
                receipt_path=latest.receipt_path,
                work_dir=resolved_work_dir,
                reused_terminal_receipt=True,
                native_result=None,
            )
        elif latest.state == "WAITING_FOR_AGENT":
            if retry:
                raise ValidationError(
                    "E-HANDOFF-RETRY",
                    "WAITING_FOR_AGENT must resume the same attempt",
                )
            if latest.executor != executor or latest.work_dir != resolved_work_dir:
                raise ValidationError(
                    "E-HANDOFF-ATTEMPT-IDENTITY",
                    "executor or work-dir differs from the pending attempt",
                )
            attempt_id = latest.attempt_id
            attempt_ordinal = latest.attempt_ordinal
            events = latest.events
        else:
            if not retry:
                code = (
                    "E-HANDOFF-RETRY-REQUIRED"
                    if latest.state == "FAILED"
                    else "E-HANDOFF-ATTEMPT-INTERRUPTED"
                )
                raise ValidationError(
                    code,
                    f"attempt {latest.attempt_id} is {latest.state}; use --retry for a new attempt",
                )
            attempt_ordinal = latest.attempt_ordinal + 1
            attempt_id = _attempt_id_for(
                handoff,
                executor=executor,
                work_dir=work_dir_text,
                attempt_ordinal=attempt_ordinal,
            )
            started = _make_attempt_event(
                handoff,
                attempt_id=attempt_id,
                attempt_ordinal=attempt_ordinal,
                event_ordinal=1,
                state="STARTED",
                executor=executor,
                work_dir=work_dir_text,
                recorded_at=now,
            )
            _atomic_write_immutable(
                _marker_path(execution_dir, attempt_ordinal, attempt_id),
                _json_bytes(started),
            )
            events = (started,)

        validating = False
        try:
            extractor = extractor_factory()
            _check_executor_client(executor, extractor)
            native_result = run_novel_research(
                copy.deepcopy(execution_spec),
                resolved_work_dir,
                extractor_client=extractor,
                repo_root=pathlib.Path(repo_root),
                now=now,
                fetcher=fetcher,
            )
            validating = True
            receipt = verify_handoff_execution(
                handoff,
                native_result["catalog"],
                native_result["store"],
                attempt_id=attempt_id,
                attempt_ordinal=attempt_ordinal,
                executor=executor,
                recorded_at=now,
            )
        except AgentResponsesPending as exc:
            waiting = _make_attempt_event(
                handoff,
                attempt_id=attempt_id,
                attempt_ordinal=attempt_ordinal,
                event_ordinal=len(events) + 1,
                state="WAITING_FOR_AGENT",
                executor=executor,
                work_dir=work_dir_text,
                recorded_at=now,
                pending_count=exc.pending_count,
            )
            waiting_path = _waiting_dir(execution_dir, attempt_ordinal, attempt_id) / (
                f"{waiting['event_ordinal']:06d}-{waiting['event_id']}.json"
            )
            _atomic_write_immutable(waiting_path, _json_bytes(waiting))
            raise
        except Exception as exc:
            receipt = _failure_receipt(
                handoff,
                attempt_id=attempt_id,
                attempt_ordinal=attempt_ordinal,
                executor=executor,
                recorded_at=now,
                stage=_failure_stage(exc, validating=validating),
                exc=exc,
            )
            _atomic_write_immutable(
                _receipt_path(execution_dir, attempt_ordinal, attempt_id),
                _json_bytes(receipt),
            )
            raise

        receipt_path = _receipt_path(execution_dir, attempt_ordinal, attempt_id)
        _atomic_write_immutable(receipt_path, _json_bytes(receipt))
        return HandoffExecutionResult(
            status="SUCCEEDED",
            attempt_id=attempt_id,
            attempt_ordinal=attempt_ordinal,
            receipt=receipt,
            receipt_path=receipt_path,
            work_dir=resolved_work_dir,
            reused_terminal_receipt=False,
            native_result=native_result,
        )
