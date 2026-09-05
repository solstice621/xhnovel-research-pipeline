"""An immutable, budgeted journal around host research and native execution.

The journal records host activity, not hidden tool use. It is neither a search
runtime nor an agent scheduler. Only the execution wrapper records executions;
all reported results are reconstructed from native, precisely validated receipts.
"""
from __future__ import annotations

import copy
import json
import pathlib
from contextlib import contextmanager
from typing import Any

from .canonical import canonical_dumps
from .catalog import Catalog
from .errors import PipelineError, ValidationError
from .file_io import write_immutable
from .generic_extraction import executor_descriptor
from .generic_cli import make_generic_executor
from .generic_handoff import resolve_generic_handoff
from .generic_handoff_execution import (validate_generic_execution_history as native_history, execute_generic_handoff,
                                        validate_generic_execution, validate_generic_execution_event)
from .hashing import artifact_id_for, object_hash
from .novel_ingest import _lock_file_handle, _unlock_file_handle
from .observation_common import (SealedRecord, get_record, publish_record, read_json, record_path,
                                 research_store, seal_record, validate_record_identity)
from .observation_planning import validate_observation_definition, validate_observation_work_lead, validate_profile_resolution
from .phase0_common import require_fields
from .phase0_planning import _require_attestation_pair, validate_research_intake
from .runtime import utc_now
from .schema import schema_validation_session
from .store import ArtifactStore

RUN_KIND = "ObservationResearchRun"
EVENT_KIND = "ObservationResearchEvent"


def _input(value: Any, research_root: pathlib.Path) -> dict[str, Any]:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    if isinstance(value, str) and value.startswith("sha256:"):
        return get_record(research_root, value)
    return read_json(pathlib.Path(value))


def _directory(run: dict[str, Any], research_root: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(research_root) / "campaigns" / run["run_id"]


@contextmanager
def _campaign_lock(run: dict[str, Any], research_root: pathlib.Path):
    directory = _directory(run, research_root)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a+b") as handle:
        try:
            _lock_file_handle(handle)
        except OSError as exc:
            raise ValidationError("E-OBSERVATION-CAMPAIGN-LOCKED", "research is already being updated") from exc
        try:
            yield
        finally:
            _unlock_file_handle(handle)


def _validate_plan(value: dict[str, Any], research_root: pathlib.Path, *, root=None) -> dict[str, Any]:
    run = validate_record_identity(value, RUN_KIND, id_field="run_id", hash_field="run_hash")
    definition = validate_observation_definition(get_record(research_root, run["definition_artifact_id"]), research_root, root=root)
    resolution = validate_profile_resolution(get_record(research_root, run["resolution_artifact_id"]), research_root, root=root)
    intake = validate_research_intake(get_record(research_root, run["intake_artifact_id"]))
    if (run["definition_id"] != definition["definition_id"] or run["resolution_id"] != resolution["resolution_id"]
            or resolution["definition_artifact_id"] != run["definition_artifact_id"]
            or run["intake_artifact_id"] != definition["intake_artifact_id"]
            or run["neutral_input_artifact_id"] != definition["neutral_input_artifact_id"]
            or run["seeds"] != intake["seeds"]):
        raise ValidationError("E-OBSERVATION-CAMPAIGN-BIND", "research plan provenance differs")
    authoring = run["budget_authoring"]
    _require_attestation_pair(authoring["assurance"], authoring["isolation_claim"])
    if authoring["input_artifact_id"] != run["neutral_input_artifact_id"]:
        raise ValidationError("E-OBSERVATION-CAMPAIGN-BIND", "budget authoring must independently bind neutral input")
    return run


def _run(value: Any, research_root: pathlib.Path, *, root=None) -> dict[str, Any]:
    run = _validate_plan(_input(value, research_root), research_root, root=root)
    if get_record(research_root, artifact_id_for(canonical_dumps(run))) != run:
        raise ValidationError("E-OBSERVATION-CAMPAIGN-BIND", "research plan CAS differs")
    return run


@schema_validation_session()
def init_observation_research(draft_or_path: Any, research_root: pathlib.Path, *, root=None) -> SealedRecord:
    draft = _input(draft_or_path, research_root)
    require_fields(draft, required={"definition_artifact_id", "resolution_artifact_id", "search_strategy", "budget", "budget_authoring", "frozen_at"}, code="E-OBSERVATION-CAMPAIGN-DRAFT", label="research plan")
    definition = validate_observation_definition(get_record(research_root, draft["definition_artifact_id"]), research_root, root=root)
    resolution = validate_profile_resolution(get_record(research_root, draft["resolution_artifact_id"]), research_root, root=root)
    intake = validate_research_intake(get_record(research_root, definition["intake_artifact_id"]))
    record = seal_record(RUN_KIND, {**draft, "schema_version": "observation-research/v1", "definition_id": definition["definition_id"],
        "resolution_id": resolution["resolution_id"], "intake_artifact_id": definition["intake_artifact_id"], "neutral_input_artifact_id": definition["neutral_input_artifact_id"],
        "seeds": intake["seeds"], "audit_assurance": "HOST_RECORDED_ACTIVITY_ONLY"}, id_field="run_id", hash_field="run_hash")
    _validate_plan(record, research_root, root=root)
    sealed = publish_record(research_root, RUN_KIND, record)
    write_immutable(_directory(record, research_root) / "plan.json", canonical_dumps(record))
    return sealed


def _history(run: dict[str, Any], research_root: pathlib.Path) -> list[tuple[str, dict[str, Any]]]:
    previous = None
    events = []
    operations = set()
    for sequence, path in enumerate(sorted((_directory(run, research_root) / "events").glob("*.json")), 1):
        if path.name != f"{sequence:06d}.json":
            raise ValidationError("E-OBSERVATION-CAMPAIGN-HISTORY", "journal sequence is not consecutive")
        visible = read_json(path)
        aid = artifact_id_for(canonical_dumps(visible))
        event = validate_record_identity(get_record(research_root, aid), EVENT_KIND, id_field="event_id", hash_field="event_hash")
        if (event != visible or event["sequence"] != sequence or event["previous_event_artifact_id"] != previous
                or event["run_id"] != run["run_id"] or event["run_hash"] != run["run_hash"] or event["operation_id"] in operations):
            raise ValidationError("E-OBSERVATION-CAMPAIGN-HISTORY", "journal identity or previous event differs")
        operations.add(event["operation_id"])
        events.append((aid, event))
        previous = aid
    return events


def _owned_native_outcome(context, history):
    before = context["native_before_artifact_id"]
    positions = [index for index, (aid, _) in enumerate(history) if aid == before] if before is not None else [-1]
    if len(positions) != 1:
        raise ValidationError("E-OBSERVATION-CAMPAIGN-EXECUTION", "reserved native predecessor is missing")
    index = positions[0] + 1
    if index >= len(history):
        return None
    start_aid, start = history[index]
    if start["state"] != "STARTED":
        raise ValidationError("E-OBSERVATION-CAMPAIGN-EXECUTION", "reservation must bind the immediate next native start")
    if index + 1 < len(history) and history[index + 1][1]["invocation_start_artifact_id"] == start_aid:
        return history[index + 1]
    return history[index]


def _check_executor_request(request, event):
    expected = request["executor_descriptor"]
    actual = event["binding"]["executor"]
    if expected is not None:
        valid = expected == actual
    else:
        valid = actual["kind"] == ("GENERIC_AGENT_FILES" if request["executor_kind"] == "agent-files" else "API")
        valid = valid and actual["model"] == (request["agent_model_label"] if request["executor_kind"] == "agent-files" else request["model"])
    if not valid:
        raise ValidationError("E-OBSERVATION-CAMPAIGN-EXECUTION", "native executor differs from reservation")


def _native_return(detail: dict[str, Any], started: dict[str, Any], research_root: pathlib.Path, *, root=None) -> dict[str, Any] | None:
    context = started["detail"]
    handoff = get_record(research_root, context["handoff_artifact_id"])
    resolved = resolve_generic_handoff(handoff, research_root, root=root)
    if detail["status"] == "FAILED_PRESTART":
        if detail["native_event_artifact_id"] is not None or detail["receipt_artifact_id"] is not None or detail["error"] is None:
            raise ValidationError("E-OBSERVATION-CAMPAIGN-EXECUTION", "prestart error cannot claim native evidence")
        return None
    if detail["native_event_artifact_id"] is None or detail["error"] is not None:
        raise ValidationError("E-OBSERVATION-CAMPAIGN-EXECUTION", "native return requires its authoritative event")
    history = native_history(resolved.handoff, pathlib.Path(research_root), root=root)
    found = [item for aid, item in history if aid == detail["native_event_artifact_id"]]
    if len(found) != 1:
        raise ValidationError("E-OBSERVATION-CAMPAIGN-EXECUTION", "native event is not in the authoritative journal")
    event = found[0]
    owned = _owned_native_outcome(context, history)
    if owned is None or owned[0] != detail["native_event_artifact_id"]:
        raise ValidationError("E-OBSERVATION-CAMPAIGN-EXECUTION", "return does not belong to the reserved native invocation")
    _check_executor_request(context["executor_request"], event)
    if event["work_dir"] != context["work_dir"]:
        raise ValidationError("E-OBSERVATION-CAMPAIGN-EXECUTION", "native event work directory differs")
    expected_status = "INTERRUPTED" if event["state"] == "STARTED" else event["state"]
    if detail["status"] != expected_status:
        raise ValidationError("E-OBSERVATION-CAMPAIGN-EXECUTION", "native status differs")
    before = context["native_before_artifact_id"]
    current_index = next(index for index, (aid, _) in enumerate(history) if aid == detail["native_event_artifact_id"])
    if before is not None:
        before_indexes = [index for index, (aid, _) in enumerate(history) if aid == before]
        if len(before_indexes) != 1 or before_indexes[0] >= current_index:
            raise ValidationError("E-OBSERVATION-CAMPAIGN-EXECUTION", "native event does not follow reserved invocation")
    if event["state"] != "STARTED":
        validate_generic_execution_event(detail["native_event_artifact_id"], resolved.handoff, research_root, root=root)
    if event["state"] in {"SUCCEEDED", "FAILED"}:
        if detail["receipt_artifact_id"] != event["detail"]["receipt_artifact_id"]:
            raise ValidationError("E-OBSERVATION-CAMPAIGN-EXECUTION", "receipt does not match native return")
        return validate_generic_execution(get_record(research_root, detail["receipt_artifact_id"]), research_root, root=root)
    if detail["receipt_artifact_id"] is not None:
        raise ValidationError("E-OBSERVATION-CAMPAIGN-EXECUTION", "nonterminal invocation cannot claim a receipt")
    # Checkpoint artifacts are audit bytes, not a mutable path assertion.
    for aid in event["detail"].get("checkpoint_artifact_ids", []):
        research_store(research_root).get(aid)
    if "checkpoint_artifact_id" in event["detail"]:
        research_store(research_root).get(event["detail"]["checkpoint_artifact_id"])
    return None


def _reduce(run: dict[str, Any], events: list[tuple[str, dict[str, Any]]], research_root: pathlib.Path, *, root=None) -> dict[str, Any]:
    state: dict[str, Any] = {"searches": {}, "leads": {}, "sources": {}, "executions": {}, "receipts": {}, "stopped": None,
                            "used": {"search_rounds": 0, "source_attempts": 0, "full_work_attempts": 0, "resume_invocations": 0}}
    by_artifact = {}
    resolution = get_record(research_root, run["resolution_artifact_id"])
    for aid, event in events:
        if state["stopped"] is not None:
            raise ValidationError("E-OBSERVATION-CAMPAIGN-STATE", "no events are permitted after STOP")
        kind, detail = event["event_type"], event["detail"]
        if kind == "SEARCH_STARTED":
            state["searches"][aid] = {"start": event, "finish": None}
            state["used"]["search_rounds"] += 1
        elif kind == "SEARCH_FINISHED":
            item = state["searches"].get(detail["start_event_artifact_id"])
            if item is None or item["finish"] is not None:
                raise ValidationError("E-OBSERVATION-CAMPAIGN-STATE", "search return lacks an open search")
            if (detail["outcome"] == "FAILED") != (detail["error"] is not None):
                raise ValidationError("E-OBSERVATION-CAMPAIGN-STATE", "search error and outcome differ")
            for artifact in detail["result_artifact_ids"]:
                research_store(research_root).get(artifact)
            item["finish"] = event
        elif kind == "LEAD_RECORDED":
            lead = validate_observation_work_lead(get_record(research_root, detail["lead_artifact_id"]), research_root, root=root)
            if lead["definition_artifact_id"] != run["definition_artifact_id"] or lead["lead_id"] in state["leads"]:
                raise ValidationError("E-OBSERVATION-CAMPAIGN-LEAD", "lead has another definition or is already recorded")
            search = detail["search_event_artifact_id"]
            if search is not None and (search not in by_artifact or by_artifact[search]["event_type"] != "SEARCH_FINISHED"):
                raise ValidationError("E-OBSERVATION-CAMPAIGN-LEAD", "lead references an unrecorded search return")
            state["leads"][lead["lead_id"]] = {"artifact_id": detail["lead_artifact_id"], "record": lead}
        elif kind == "SOURCE_STARTED":
            known = {item["artifact_id"] for item in state["leads"].values()}
            if not set(detail["lead_artifact_ids"]) <= known:
                raise ValidationError("E-OBSERVATION-CAMPAIGN-SOURCE", "source attempt references unrecorded leads")
            research_store(research_root).get(detail["source_input_artifact_id"])
            state["sources"][aid] = {"start": event, "finish": None, "handoff": None, "finish_artifact_id": None}
            state["used"]["source_attempts"] += 1
        elif kind == "SOURCE_FINISHED":
            item = state["sources"].get(detail["start_event_artifact_id"])
            if item is None or item["finish"] is not None:
                raise ValidationError("E-OBSERVATION-CAMPAIGN-STATE", "source return lacks an open source attempt")
            if detail["status"] == "ELIGIBLE":
                if detail["handoff_artifact_id"] is None:
                    raise ValidationError("E-OBSERVATION-CAMPAIGN-SOURCE", "eligible source requires a real Handoff")
                handoff = resolve_generic_handoff(get_record(research_root, detail["handoff_artifact_id"]), research_root, root=root).handoff
                if (handoff["builder"]["definition_artifact_id"] != run["definition_artifact_id"]
                        or handoff["builder"]["resolution_artifact_id"] != run["resolution_artifact_id"]
                        or set(handoff["builder"]["work_lead_artifact_ids"]) != set(item["start"]["detail"]["lead_artifact_ids"])):
                    raise ValidationError("E-OBSERVATION-CAMPAIGN-SOURCE", "Handoff does not match source attempt and research")
                item["handoff"] = handoff
            elif detail["handoff_artifact_id"] is not None:
                raise ValidationError("E-OBSERVATION-CAMPAIGN-SOURCE", "blocked source cannot carry an executable Handoff")
            item.update(finish=event, finish_artifact_id=aid)
        elif kind == "EXECUTION_STARTED":
            source = next((item for item in state["sources"].values() if item["finish_artifact_id"] == detail["source_event_artifact_id"]), None)
            if (source is None or source["handoff"] is None or source["finish"]["detail"]["handoff_artifact_id"] != detail["handoff_artifact_id"]
                    or any(item["finish"] is None for item in state["executions"].values())):
                raise ValidationError("E-OBSERVATION-CAMPAIGN-EXECUTION", "execution requires an eligible source and no concurrent invocation")
            if resolution["decision"] != "REUSE_EXISTING":
                raise ValidationError("E-OBSERVATION-CAMPAIGN-EXECUTION", "research has no executable Profile")
            prior = [item for item in state["executions"].values() if item["start"]["detail"]["handoff_artifact_id"] == detail["handoff_artifact_id"]]
            expected_kind = "FULL_WORK" if not prior else ("RETRY" if prior[-1]["finish"]["detail"]["status"] in {"FAILED", "FAILED_PRESTART"} else "RESUME")
            if detail["invocation_kind"] != expected_kind or (prior and prior[-1]["finish"]["detail"]["status"] == "SUCCEEDED"):
                raise ValidationError("E-OBSERVATION-CAMPAIGN-EXECUTION", "invocation budget class differs from prior state")
            budget_key = "resume_invocations" if expected_kind == "RESUME" else "full_work_attempts"
            state["used"][budget_key] += 1
            state["executions"][aid] = {"start": event, "finish": None, "finish_artifact_id": None}
        elif kind == "EXECUTION_REUSED":
            source = next((item for item in state["sources"].values() if item["finish_artifact_id"] == detail["source_event_artifact_id"]), None)
            if source is None or source["handoff"] is None or source["finish"]["detail"]["handoff_artifact_id"] != detail["handoff_artifact_id"]:
                raise ValidationError("E-OBSERVATION-CAMPAIGN-EXECUTION", "reuse requires its eligible source")
            native = validate_generic_execution_event(detail["native_event_artifact_id"], source["handoff"], research_root, root=root)
            _check_executor_request(detail["executor_request"], native)
            if native["state"] != "SUCCEEDED" or native["work_dir"] != detail["work_dir"] or native["detail"]["receipt_artifact_id"] != detail["receipt_artifact_id"]:
                raise ValidationError("E-OBSERVATION-CAMPAIGN-EXECUTION", "reuse does not bind a successful native receipt")
            receipt = validate_generic_execution(get_record(research_root, detail["receipt_artifact_id"]), research_root, root=root)
            state["receipts"][detail["receipt_artifact_id"]] = receipt
            state["executions"][aid] = {"start": event, "finish": event, "finish_artifact_id": aid}
        elif kind == "EXECUTION_FINISHED":
            item = state["executions"].get(detail["start_event_artifact_id"])
            if item is None or item["finish"] is not None:
                raise ValidationError("E-OBSERVATION-CAMPAIGN-STATE", "execution return lacks an open invocation")
            receipt = _native_return(detail, item["start"], research_root, root=root)
            if receipt is not None:
                state["receipts"][detail["receipt_artifact_id"]] = receipt
            item.update(finish=event, finish_artifact_id=aid)
        elif kind == "STOP":
            reason = detail["reason"]
            limits = {"SEARCH_BUDGET_EXHAUSTED": "search_rounds", "SOURCE_BUDGET_EXHAUSTED": "source_attempts", "EXECUTION_BUDGET_EXHAUSTED": "full_work_attempts", "RESUME_BUDGET_EXHAUSTED": "resume_invocations"}
            if reason in limits and state["used"][limits[reason]] < run["budget"]["max_" + limits[reason]]:
                raise ValidationError("E-OBSERVATION-CAMPAIGN-STOP", "declared budget is not exhausted")
            if reason == "NO_USABLE_PROFILE" and resolution["decision"] == "REUSE_EXISTING":
                raise ValidationError("E-OBSERVATION-CAMPAIGN-STOP", "research has a usable Profile")
            if reason == "TARGET_WORKS_REACHED":
                works = {get_record(research_root, receipt["handoff_artifact_id"])["work_ref"]["work_ref_id"] for receipt in state["receipts"].values() if receipt["status"] == "SUCCEEDED"}
                if len(works) < run["budget"]["target_works"]:
                    raise ValidationError("E-OBSERVATION-CAMPAIGN-STOP", "target number of successful works has not been reached")
            state["stopped"] = detail
        else:
            raise ValidationError("E-OBSERVATION-CAMPAIGN-STATE", "unknown event type")
        for key, used in state["used"].items():
            if used > run["budget"]["max_" + key]:
                raise ValidationError("E-OBSERVATION-CAMPAIGN-BUDGET", f"{key} budget exhausted")
        by_artifact[aid] = event
    return state


def _append(run, draft, events, research_root, *, root=None) -> SealedRecord:
    require_fields(draft, required={"operation_id", "event_type", "detail", "recorded_at"}, code="E-OBSERVATION-CAMPAIGN-DRAFT", label="research event")
    for aid, prior in events:
        if prior["operation_id"] == draft["operation_id"]:
            if any(prior[key] != draft[key] for key in ("event_type", "detail")):
                raise ValidationError("E-OBSERVATION-CAMPAIGN-OPERATION", "operation ID reused with different content")
            return SealedRecord(prior, aid, record_path(research_root, EVENT_KIND, aid))
    event = seal_record(EVENT_KIND, {**draft, "schema_version": "observation-research-event/v1", "run_id": run["run_id"], "run_hash": run["run_hash"],
        "sequence": len(events) + 1, "previous_event_artifact_id": events[-1][0] if events else None}, id_field="event_id", hash_field="event_hash")
    aid = artifact_id_for(canonical_dumps(event))
    _reduce(run, events + [(aid, event)], research_root, root=root)
    sealed = publish_record(research_root, EVENT_KIND, event)
    write_immutable(_directory(run, research_root) / "events" / f"{len(events)+1:06d}.json", canonical_dumps(event))
    events.append((aid, event))
    return sealed


@schema_validation_session()
def record_observation_research_event(run_or_path, draft_or_path, research_root, *, root=None) -> SealedRecord:
    run = _run(run_or_path, research_root, root=root)
    draft = _input(draft_or_path, research_root)
    if str(draft.get("event_type", "")).startswith("EXECUTION_"):
        raise ValidationError("E-OBSERVATION-CAMPAIGN-EXECUTION", "execution events are owned by execute_campaign_handoff")
    with _campaign_lock(run, research_root):
        events = _history(run, research_root)
        _reduce(run, events, research_root, root=root)
        return _append(run, draft, events, research_root, root=root)


def _finish_draft(start_aid, start, native, *, now, error=None):
    if error is not None:
        detail = {"start_event_artifact_id": start_aid, "status": "FAILED_PRESTART", "native_event_artifact_id": None,
                  "receipt_artifact_id": None, "error": {"code": getattr(error, "code", "E-OBSERVATION-EXECUTION"), "message": str(error)}}
    else:
        aid, event = native
        detail = {"start_event_artifact_id": start_aid, "status": "INTERRUPTED" if event["state"] == "STARTED" else event["state"],
                  "native_event_artifact_id": aid, "receipt_artifact_id": event["detail"].get("receipt_artifact_id"), "error": None}
    return {"operation_id": start["operation_id"] + ":finish", "event_type": "EXECUTION_FINISHED", "detail": detail, "recorded_at": now}


def _execution_result(finish, research_root):
    detail = finish["detail"]
    result = {"status": detail["status"], "campaign_event_id": finish["event_id"], "campaign_event_artifact_id": artifact_id_for(canonical_dumps(finish))}
    if detail["native_event_artifact_id"] is not None:
        native = get_record(research_root, detail["native_event_artifact_id"])
        result.update(event_artifact_id=detail["native_event_artifact_id"], attempt_id=native["attempt_id"])
        if native["state"] in {"WAITING_FOR_AGENT", "PARTIAL_RETRYABLE"}:
            result.update(native["detail"])
    if detail["receipt_artifact_id"] is not None:
        result.update(receipt_artifact_id=detail["receipt_artifact_id"], receipt=get_record(research_root, detail["receipt_artifact_id"]),
                      receipt_path=str(record_path(research_root, "GenericExtractionExecutionReceipt", detail["receipt_artifact_id"])))
    if detail.get("error") is not None:
        result["error"] = detail["error"]
    return result


@schema_validation_session()
def execute_campaign_handoff(run_or_path, handoff_path, research_root, work_dir, *, operation_id=None, root=None,
                             executor_kind="agent-files", executor=None, model=None, agent_model_label="host-code-agent",
                             resume=False, retry=False, now=None) -> dict[str, Any]:
    research_root, work_dir = pathlib.Path(research_root).resolve(), pathlib.Path(work_dir).resolve()
    run = _run(run_or_path, research_root, root=root)
    handoff = resolve_generic_handoff(handoff_path, research_root, root=root).handoff
    handoff_aid = artifact_id_for(canonical_dumps(handoff))
    if executor is None:
        try:
            executor = make_generic_executor(executor_kind, work_dir, handoff["selected_profile"]["profile_ref"], model=model, agent_model_label=agent_model_label, root=root)
        except PipelineError:
            # The native wrapper will record the configuration failure after the
            # campaign has durably reserved this invocation.
            pass
    request = {"executor_kind": executor_kind, "model": model, "agent_model_label": agent_model_label,
               "executor_descriptor": executor_descriptor(executor) if executor is not None else None}
    with _campaign_lock(run, research_root):
        events = _history(run, research_root)
        state = _reduce(run, events, research_root, root=root)
        source = next((item for item in reversed(list(state["sources"].values())) if item["finish"] and item["finish"]["detail"]["handoff_artifact_id"] == handoff_aid), None)
        if source is None:
            raise ValidationError("E-OBSERVATION-CAMPAIGN-SOURCE", "record an eligible source before execution")
        prior = [(aid, item) for aid, item in state["executions"].items() if item["start"]["detail"]["handoff_artifact_id"] == handoff_aid]
        if operation_id is not None:
            matches = [item for _, item in prior if item["start"]["operation_id"] == operation_id + ":start"]
            if matches:
                match = matches[0]
                if match["start"]["detail"]["executor_request"] != request or match["start"]["detail"]["work_dir"] != str(work_dir):
                    raise ValidationError("E-OBSERVATION-CAMPAIGN-OPERATION", "operation ID changed execution parameters")
                if match["finish"] is not None:
                    return _execution_result(match["finish"], research_root)
        if prior and prior[-1][1]["finish"] and prior[-1][1]["finish"]["detail"]["status"] == "SUCCEEDED":
            latest = prior[-1][1]
            if latest["start"]["detail"]["executor_request"] != request or latest["start"]["detail"]["work_dir"] != str(work_dir) or retry or resume:
                raise ValidationError("E-OBSERVATION-CAMPAIGN-OPERATION", "completed research invocation parameters differ")
            return _execution_result(latest["finish"], research_root)
        if state["stopped"] is not None:
            raise ValidationError("E-OBSERVATION-CAMPAIGN-STATE", "research has stopped")
        history = native_history(handoff, research_root, root=root)
        if not prior and history and history[-1][1]["state"] == "SUCCEEDED":
            # A completed native attempt is verified and attached without claiming
            # a new invocation or consuming the full-work execution budget.
            result = execute_generic_handoff(handoff, research_root, work_dir, executor_kind=executor_kind, executor=executor,
                model=model, agent_model_label=agent_model_label, root=root, resume=resume, retry=retry, now=now)
            reused = _append(run, {"operation_id": operation_id or "reuse:" + handoff["handoff_id"], "event_type": "EXECUTION_REUSED", "recorded_at": now or utc_now(), "detail": {
                "source_event_artifact_id": source["finish_artifact_id"], "handoff_artifact_id": handoff_aid,
                "work_dir": str(work_dir), "executor_request": request, "native_event_artifact_id": result["event_artifact_id"],
                "receipt_artifact_id": result["receipt_artifact_id"], "status": "SUCCEEDED"}}, events, research_root, root=root)
            return _execution_result(reused.record, research_root)
        # A crash between native return and campaign publication is repaired from
        # the native journal. A true interrupted invocation requires explicit flags.
        if prior and prior[-1][1]["finish"] is None:
            start_aid, unfinished = prior[-1]
            start = unfinished["start"]
            if start["detail"]["executor_request"] != request or start["detail"]["work_dir"] != str(work_dir):
                raise ValidationError("E-OBSERVATION-CAMPAIGN-OPERATION", "interrupted invocation parameters differ")
            owned = _owned_native_outcome(start["detail"], history)
            changed = owned is not None
            if changed and owned[1]["state"] != "STARTED":
                finished = _append(run, _finish_draft(start_aid, start, owned, now=now or utc_now()), events, research_root, root=root)
                return _execution_result(finished.record, research_root)
            if not (resume or retry):
                raise ValidationError("E-OBSERVATION-CAMPAIGN-INTERRUPTED", "interrupted research invocation requires explicit resume or retry")
            error = None if changed else ValidationError("E-OBSERVATION-INTERRUPTED-PRESTART", "research reservation interrupted before a native start")
            _append(run, _finish_draft(start_aid, start, owned if changed else None, now=now or utc_now(), error=error), events, research_root, root=root)
            state = _reduce(run, events, research_root, root=root)
            prior = [(aid, item) for aid, item in state["executions"].items() if item["start"]["detail"]["handoff_artifact_id"] == handoff_aid]
            if not changed:
                resume = retry = False
        if prior and prior[-1][1]["finish"] is not None:
            last_detail = prior[-1][1]["finish"]["detail"]
            if last_detail["native_event_artifact_id"] is not None and history and last_detail["native_event_artifact_id"] != history[-1][0]:
                raise ValidationError("E-OBSERVATION-CAMPAIGN-EXECUTION", "unrecorded native continuation cannot consume this campaign's budget")
        invocation_kind = "FULL_WORK" if not prior else ("RETRY" if prior[-1][1]["finish"]["detail"]["status"] in {"FAILED", "FAILED_PRESTART"} else "RESUME")
        before = history[-1][0] if history else None
        if operation_id is None:
            operation_id = "execute:" + object_hash({"run_id": run["run_id"], "handoff": handoff_aid, "before": before,
                "prior_reservations": len(prior), "request": request, "work_dir": str(work_dir)}, omit=()).removeprefix("sha256:")[:24]
        # Recovery of an explicit operation needs a distinct immutable reservation.
        if any(event["operation_id"] == operation_id + ":start" for _, event in events):
            operation_id += ":recovery:" + str(len(prior))
        start = _append(run, {"operation_id": operation_id + ":start", "event_type": "EXECUTION_STARTED", "recorded_at": now or utc_now(), "detail": {
            "source_event_artifact_id": source["finish_artifact_id"], "handoff_artifact_id": handoff_aid, "work_dir": str(work_dir),
            "invocation_kind": invocation_kind, "native_before_artifact_id": before, "executor_request": request}}, events, research_root, root=root)
        try:
            result = execute_generic_handoff(handoff, research_root, work_dir, executor_kind=executor_kind, executor=executor,
                model=model, agent_model_label=agent_model_label, root=root, resume=resume, retry=retry, now=now)
        except (PipelineError, OSError, ValueError) as exc:
            # Prestart failures are audit facts. Never convert failed identity
            # validation into a resumable native partial.
            history = native_history(handoff, research_root, root=root)
            owned = _owned_native_outcome(start.record["detail"], history)
            if owned is not None:
                finished = _append(run, _finish_draft(start.artifact_id, start.record, owned, now=now or utc_now()), events, research_root, root=root)
            else:
                finished = _append(run, _finish_draft(start.artifact_id, start.record, None, now=now or utc_now(), error=exc), events, research_root, root=root)
            return _execution_result(finished.record, research_root)
        history = native_history(handoff, research_root, root=root)
        if not history or history[-1][0] != result["event_artifact_id"]:
            raise ValidationError("E-OBSERVATION-CAMPAIGN-EXECUTION", "execution returned a noncurrent native event")
        finished = _append(run, _finish_draft(start.artifact_id, start.record, history[-1], now=now or utc_now()), events, research_root, root=root)
        return _execution_result(finished.record, research_root)


def _result_index(receipt, receipt_aid):
    native_dir = pathlib.Path(receipt["work_dir"])
    target = receipt["result"]
    store = ArtifactStore(native_dir / "ingestion" / "objects")
    corpus = [json.loads(line) for line in store.get(target["corpus_artifact_id"]).decode("utf-8").splitlines() if line]
    catalog = Catalog.from_mapping(read_json(native_dir / "ingestion" / "ingestions" / target["ingestion_run_id"] / "catalog.json"))
    chapters = {segment_id: chapter for chapter in catalog.by_type["NovelChapter"] for segment_id in chapter["segment_ids"]}
    index = []
    for record in corpus:
        spans = []
        for span in record["source_spans"]:
            chapter = chapters[span["segment_id"]]
            spans.append({"chapter_id": chapter["chapter_id"], "source_artifact_id": chapter["artifact_id"],
                **{key: span[key] for key in ("segment_id", "start", "end", "normalized_text_hash")}})
        index.append({"record_id": record["record_id"], "member_observation_ids": record["member_observation_ids"], "source_spans": spans})
    return {"receipt_artifact_id": receipt_aid, "receipt_id": receipt["receipt_id"], "work_dir": receipt["work_dir"],
            **copy.deepcopy(target), "index_policy": "OFFSETS_ONLY_NO_EXCERPTS", "evidence_index": index}


def _build_report(run, events, state, research_root):
    results = []
    successful_works = set()
    work_groups = {}
    for item in state["sources"].values():
        if item["handoff"]:
            handoff = item["handoff"]
            key = handoff["work_ref"]["work_ref_id"]
            group = work_groups.setdefault(key, {"work_ref": handoff["work_ref"], "lead_ids": set(), "source_attempt_ids": [], "execution_statuses": []})
            group["lead_ids"].update(handoff["motivating_lead_ids"])
            group["source_attempt_ids"].append(item["start"]["event_id"])
    for receipt_aid, receipt in state["receipts"].items():
        if receipt["status"] == "SUCCEEDED":
            handoff = get_record(research_root, receipt["handoff_artifact_id"])
            successful_works.add(handoff["work_ref"]["work_ref_id"])
            results.append({"work_ref_id": handoff["work_ref"]["work_ref_id"], **_result_index(receipt, receipt_aid)})
    lead_rows = []
    for lead_id, lead in sorted(state["leads"].items()):
        sources = [item for item in state["sources"].values() if lead["artifact_id"] in item["start"]["detail"]["lead_artifact_ids"]]
        handoff_ids = {item["finish"]["detail"]["handoff_artifact_id"] for item in sources if item["handoff"]}
        executions = [item for item in state["executions"].values() if item["start"]["detail"]["handoff_artifact_id"] in handoff_ids]
        work_ids = sorted({item["handoff"]["work_ref"]["work_ref_id"] for item in sources if item["handoff"]})
        lead_rows.append({"lead_id": lead_id, "lead_artifact_id": lead["artifact_id"], "work_claim": lead["record"]["work_claim"],
            "discovery": "IDENTITY_RESOLVED" if work_ids else "LEAD_ONLY", "work_ref_ids": work_ids,
            "source": sources[-1]["finish"]["detail"]["status"] if sources and sources[-1]["finish"] else "UNRESOLVED",
            "source_attempt_ids": [item["start"]["event_id"] for item in sources],
            "execution": (executions[-1]["finish"]["detail"]["status"] if executions[-1]["finish"] else "INTERRUPTED") if executions else ("HANDOFF_READY" if handoff_ids else "NOT_STARTED"),
            "quality": {"semantic_assurance": "UNQUALIFIED", "evaluation": "UNMEASURED"}})
    for group in work_groups.values():
        group["lead_ids"] = sorted(group["lead_ids"])
        group["source_attempt_ids"].sort()
        group["execution_statuses"] = sorted({row["execution"] for row in lead_rows if row["lead_id"] in group["lead_ids"]})
    unique_corpora = {result["corpus_artifact_id"]: result["corpus_record_count"] for result in results}
    resolution = get_record(research_root, run["resolution_artifact_id"])
    definition = get_record(research_root, run["definition_artifact_id"])
    report = {"schema_version": "observation-research-report/v1", "run_id": run["run_id"], "run_hash": run["run_hash"],
        "last_event_artifact_id": events[-1][0] if events else None, "event_count": len(events),
        "status": "STOPPED" if state["stopped"] else "IN_PROGRESS", "stop": state["stopped"],
        "audit_assurance": run["audit_assurance"], "profile_decision": resolution["decision"], "profile_fit": resolution.get("fit"),
        "selected_profile": resolution.get("selected_profile"), "unmet_requirement_ids": resolution["unmet_requirement_ids"],
        "unresolved_requirement_ids": definition["unresolved_requirement_ids"], "budget": run["budget"], "budget_used": state["used"],
        "counts": {"leads_considered": len(lead_rows), "resolved_works": len(work_groups), "successful_works": len(successful_works),
            "search_rounds": len(state["searches"]), "source_attempts": len(state["sources"]), "execution_invocations": sum(item["start"]["event_type"] == "EXECUTION_STARTED" for item in state["executions"].values()), "reused_executions": sum(item["start"]["event_type"] == "EXECUTION_REUSED" for item in state["executions"].values()),
            "successful_receipts": len(results), "zero_result_receipts": sum(result["corpus_record_count"] == 0 for result in results),
            "corpus_record_count_sum": sum(unique_corpora.values()),
            "failed_source_attempts": sum(item["finish"] is not None and item["finish"]["detail"]["status"] != "ELIGIBLE" for item in state["sources"].values()),
            "failed_execution_invocations": sum(item["finish"] is not None and item["finish"]["detail"]["status"] in {"FAILED", "FAILED_PRESTART"} for item in state["executions"].values())},
        "leads": lead_rows, "works": [work_groups[key] for key in sorted(work_groups)],
        "searches": list(state["searches"].values()),
        "source_attempts": [{key: value for key, value in item.items() if key != "handoff"} for item in state["sources"].values()],
        "execution_invocations": list(state["executions"].values()), "results": results,
        "result_scope": "COMPLETE_NATIVE_CORPORA_NO_SEMANTIC_FILTER", "semantic_assurance": "UNQUALIFIED", "semantic_coverage": "UNMEASURED"}
    report["report_hash"] = object_hash(report, omit=())
    return report


@schema_validation_session()
def report_observation_research(run_or_path, research_root, *, root=None) -> dict[str, Any]:
    run = _run(run_or_path, research_root, root=root)
    with _campaign_lock(run, research_root):
        events = _history(run, research_root)
        state = _reduce(run, events, research_root, root=root)
        report = _build_report(run, events, state, research_root)
        aid = research_store(research_root).put(canonical_dumps(report))
        write_immutable(_directory(run, research_root) / "reports" / (aid.removeprefix("sha256:") + ".json"), canonical_dumps(report))
        if state["stopped"] is not None:
            write_immutable(_directory(run, research_root) / "final-report.json", canonical_dumps(report))
        return report


@schema_validation_session()
def validate_observation_research(run_or_path, research_root, *, report_or_path=None, root=None) -> dict[str, Any]:
    run = _run(run_or_path, research_root, root=root)
    with _campaign_lock(run, research_root):
        events = _history(run, research_root)
        state = _reduce(run, events, research_root, root=root)
        report = _build_report(run, events, state, research_root)
        if report_or_path is not None and _input(report_or_path, research_root) != report:
            raise ValidationError("E-OBSERVATION-REPORT", "saved report differs from authoritative journal and receipts")
        final = _directory(run, research_root) / "final-report.json"
        if final.exists():
            frozen = read_json(final)
            if frozen != report or get_record(research_root, artifact_id_for(canonical_dumps(frozen))) != frozen:
                raise ValidationError("E-OBSERVATION-REPORT", "final report differs from authoritative journal and receipts")
        return report
