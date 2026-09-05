"""Recomputable host view over library records and native validators.

No state transitions, registration, task generation, report parsing or model calls.
Native validators may acquire their usual locks. This view is not an evidence record.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import research_library as lib
from xhnovel_pipeline.phase0_handoff import load_standalone_attestation
from xhnovel_pipeline.phase0_planning import (
    read_planning_record, validate_planning_compilation, validate_planning_handoff,
)


def _error(exc):
    return {"code": getattr(exc, "code", "E-HOST-STATUS-IO"), "message": str(exc)}


def _inspect(kind, path, operation):
    """Keep a failed item visible without hiding independent successful items."""
    item = {"kind": kind, "path": str(path)}
    try:
        item.update(operation())
    except (lib.PipelineError, OSError, ValueError, KeyError, TypeError) as exc:
        item.update(validation="UNAVAILABLE", next_action="REVIEW_ERROR", error=_error(exc))
    return item


def _planning(root):
    receipt_path = root / "planning-compilation-receipt.json"
    if not receipt_path.exists():
        return {"validation": "INCOMPLETE", "next_action": "CONTINUE_PLANNING"}
    # Do not create a missing CAS merely to inspect a damaged planning directory.
    if not (root / "objects/sha256").is_dir():
        lib.fail("planning CAS is missing", "E-ARTIFACT-MISSING")
    receipt = validate_planning_compilation(receipt_path, planning_root=root, repo_root=lib.ROOT)
    store = lib.ArtifactStore(root / "objects")
    brief = read_planning_record(store, receipt["compiled_brief_artifact_id"], "ExplorationBrief")
    plan = read_planning_record(store, receipt["plan_artifact_id"], "ExplorationPlan")
    # Formal JSON files are human-facing copies; CAS is authoritative. Flag a
    # changed copy so a host will not accidentally reuse it in a new preparation.
    for name, record in (("exploration-brief.json", brief), ("exploration-plan.json", plan)):
        if lib.read_json(root / name) != record:
            lib.fail(f"{name} differs from the replayed planning CAS", "E-HOST-PLANNING-COPY")
    return {"validation": "VALIDATED", "receipt_id": receipt["receipt_id"],
            "brief_id": brief["brief_id"], "brief_artifact_id": receipt["compiled_brief_artifact_id"],
            "scope": brief["scope"], "diversity": plan["diversity"],
            "next_action": "SELECT_AND_RESOLVE_SOURCES"}


def _rights(root):
    attestation = load_standalone_attestation(root)
    if attestation is None:
        return {"validation": "MISSING", "next_action": "LOCATE_STANDING_ATTESTATION"}
    return {"validation": "VALIDATED", "attestation_id": attestation["attestation_id"],
            "rights": lib.attestation_rights(attestation),
            "next_action": "REUSE_STANDING_ATTESTATION"}


def _tasks(work, protocol):
    # pending.json is an operational locator, not a trusted count or task hash.
    # Only the native wrapper can regenerate/check tasks against the frozen input.
    if protocol != "SCENE":
        return {"validation": "NOT_CHECKED", "next_action": "RESUME_NATIVE_FOR_PENDING"}
    manifest = work / "scene-scout/agent-files/pending.json"
    if not manifest.exists():
        return {"validation": "NOT_CHECKED", "path": str(manifest),
                "next_action": "RESUME_NATIVE_FOR_PENDING"}
    data = lib.acq.read_json(manifest)
    pending = data.get("pending")
    if not isinstance(pending, list) or data.get("pending_count") != len(pending):
        lib.fail("pending locator count differs from its entries", "E-HOST-PENDING")
    missing_tasks = answers_present = 0
    seen = set()
    for entry in pending:
        window = entry["window_id"]
        if window in seen:
            lib.fail("duplicate pending window locator", "E-HOST-PENDING")
        seen.add(window)
        task = lib.acq.child(work, entry["task"])
        answer = lib.acq.child(work, entry["answer"])
        if (task.parent != work / "scene-scout/agent-files/tasks" or
                answer.parent != work / "scene-scout/agent-files/answers"):
            lib.fail("pending paths do not belong to native task directories", "E-HOST-PENDING")
        missing_tasks += not task.is_file()
        answers_present += answer.is_file()
    return {"validation": "LOCATORS_ONLY", "path": str(manifest),
            "listed_count": len(pending), "missing_task_files": missing_tasks,
            "answer_files_present": answers_present,
            "current_pending_count": None, "task_integrity": "NOT_REPLAYED",
            "next_action": "RESUME_NATIVE_FOR_PENDING"}


def _execution(library, record, planning, planning_root):
    state = library.execution_status(record)
    result = {"validation": "VALIDATED", "native_state": state,
              "protocol": record["protocol"], "work_ref_id": record["work_ref_id"],
              "input_spec_hash": record["input_spec_hash"], "work_dir": record["work_dir"],
              "handoff": record["handoff"], "native_root": record["native_root"]}
    if record["protocol"] == "SCENE":
        if planning.get("validation") != "VALIDATED":
            return {**result, "planning_match": "NOT_CHECKED", "next_action": "CHECK_PLANNING"}
        try:
            validate_planning_handoff(
                planning_root / "planning-compilation-receipt.json",
                Path(record["handoff"]["path"]), planning_root=planning_root,
                phase0_root=Path(record["native_root"]), repo_root=lib.ROOT,
            )
        except lib.PipelineError as exc:
            mismatch = exc.code == "E-PLANNING-HANDOFF-CLOSURE"
            return {**result, "planning_match": "MISMATCH" if mismatch else "UNAVAILABLE",
                    "error": _error(exc), "next_action":
                    "PREPARE_CURRENT_RESEARCH_HANDOFF" if mismatch else "REVIEW_ERROR"}
        result["planning_match"] = "VALIDATED"
    else:
        # Generic requirements/profile/budget remain owned by its campaign.
        result["planning_match"] = "CHECK_NATIVE_CAMPAIGN"
    result["next_action"] = {
        "HANDOFF_READY": "FREEZE_AND_EXECUTE_NATIVE",
        "WAITING_FOR_AGENT": "CONTINUE_NATIVE_TASKS",
        "STARTED": "CHECK_INTERRUPTED_NATIVE",
        "INTERRUPTED": "CHECK_INTERRUPTED_NATIVE",
        "FAILED": "REVIEW_NATIVE_FAILURE",
        "SUCCEEDED": "VALIDATE_PRODUCTS_AND_REPORT",
    }.get(state, "CHECK_NATIVE_STATE")
    if state == "WAITING_FOR_AGENT":
        result["tasks"] = _inspect("tasks", record["work_dir"],
                                  lambda: _tasks(Path(record["work_dir"]), record["protocol"]))
        if result["tasks"]["validation"] == "UNAVAILABLE":
            result["next_action"] = "REVIEW_NATIVE_TASK_LOCATORS"
    return result


def _acquisition(root):
    run = lib.acq.Run(root)
    status = run.status()
    verification = lib.acq.verify(run, persist=False)
    # A source-run being acquired is not sealed or quality-approved.
    return {"validation": "VALIDATED", "native_status": {
                k: v for k, v in status.items() if k != "missing_entries"},
            "missing_entry_count": len(status["missing_entries"]),
            "missing_entries_sample": status["missing_entries"][:20],
            "coverage": verification["result"], "quality_checks": verification["checks"],
            "limits": run.limits, "next_action": "REVIEW_SOURCE_ATTEMPT"}


def _legacy(root):
    """Bounded discovery in explicit roots; never adopt legacy research implicitly."""
    items = []
    for path in sorted((root / "handoffs").glob("*/handoff.json")):
        def inspect(path=path):
            resolved = lib.resolve_binding("SCENE", lib.file_ref(path), root)
            histories = lib.validate_handoff_execution_history(path, phase0_root=root)
            return {"validation": "VALIDATED", "research_binding": "UNREGISTERED",
                    "brief_artifact_id": resolved.handoff["builder"]["exploration_brief_artifact_id"],
                    "input_spec_hash": resolved.handoff["novel_spec"]["expected_input_spec_hash"],
                    "attempts": [{"state": h.state, "work_dir": str(h.work_dir),
                                  "attempt_id": h.attempt_id,
                                  "historical_pending_count": next((
                                      e.get("pending_count") for e in reversed(h.events)
                                      if e["state"] == "WAITING_FOR_AGENT"), None)}
                                 for h in histories],
                    "next_action": "CHECK_LEGACY_RESEARCH_BINDING"}
        items.append(_inspect("legacy-handoff", path, inspect))
    # Report unsealed files only as material to investigate, never as sources.
    for path in sorted((root / "sources").glob("*/book.txt")):
        items.append(_inspect("legacy-material", path, lambda path=path: {
            "validation": "NOT_CHECKED", "bytes": lib.checked_path(path).stat().st_size,
            "next_action": "IMPORT_AND_VERIFY_MATERIAL"}))
    return items


def research_status(library, research_id, *, planning_root=None, legacy_root=(),
                    acquisition_root=(), work_ref_id=(), attestation_root=None):
    library.load(research_id, "research")
    library.validate(research_id)
    started = datetime.now(timezone.utc).isoformat()
    source_hash = lib.build_source_hash(lib.ROOT)
    planning_root = lib.checked_path(planning_root) if planning_root else None
    planning = (_inspect("planning", planning_root, lambda: _planning(planning_root))
                if planning_root else {"validation": "NOT_SUPPLIED", "next_action": "CHECK_REQUIREMENTS"})
    attestation_root = lib.checked_path(attestation_root or lib.ROOT / "attestations")
    rights = _inspect("rights", attestation_root, lambda: _rights(attestation_root))
    # Avoid reindex/list_records: inventory reads immutable registrations directly.
    records, issues = library._inventory()
    selected = {rid: r for rid, r in records.items()
                if r.get("research_record_id") == research_id}
    source_ids = {r["source_record_id"] for r in selected.values() if r["kind"] == "execution"}
    requested_works = set(work_ref_id)
    # Candidate lookup happens only after neutral planning has been validated.
    if requested_works and planning.get("validation") != "VALIDATED":
        issues.append({"code": "E-HOST-PLANNING-REQUIRED",
                       "message": "freeze and validate neutral planning before candidate source lookup"})
    elif requested_works:
        source_ids.update(rid for rid, r in records.items()
                          if r["kind"] == "source" and r["work_ref_id"] in requested_works)
    items = []
    for rid in sorted(source_ids):
        record = records.get(rid)
        def source(record=record, rid=rid):
            if record is None:
                lib.fail("referenced source registration is unavailable", "E-LIBRARY-INTEGRITY")
            library.validate(rid)
            return {"validation": "VALIDATED", "work_ref_id": record["work_ref_id"],
                    "sealed_path": record["sealed_path"], "source_revision": record["source_revision"],
                    "research_binding": "CANDIDATE_ONLY", "next_action": "MATCH_AND_PREPARE_SOURCE"}
        items.append({"record_id": rid, **_inspect("source", rid, source)})
    for rid, record in sorted(selected.items()):
        kind = record["kind"]
        if kind in {"execution", "external-execution"}:
            operation = lambda r=record: _execution(library, r, planning, planning_root)
        elif kind in {"product", "report"}:
            operation = lambda rid=rid: {"validation": library.validate(rid)["status"],
                                         "next_action": "REVIEW_RESEARCH_COVERAGE"}
        else:
            continue
        items.append({"record_id": rid, **_inspect(kind, rid, operation)})
    # Products bind through executions, not directly through a research record.
    execution_ids = {rid for rid, r in selected.items() if r["kind"] in {"execution", "external-execution"}}
    for rid, record in sorted(records.items()):
        if record["kind"] == "product" and record["execution_record_id"] in execution_ids:
            items.append({"record_id": rid, **_inspect("product", rid, lambda rid=rid: {
                "validation": library.validate(rid)["status"], "next_action": "REVIEW_RESEARCH_COVERAGE"})})
    for root in acquisition_root:
        root = lib.checked_path(root)
        items.append(_inspect("acquisition", root, lambda root=root: _acquisition(root)))
    for root in legacy_root:
        root = lib.checked_path(root)
        if not root.is_dir():
            items.append({"kind": "legacy-root", "path": str(root), "validation": "MISSING",
                          "next_action": "LOCATE_LEGACY_ROOT"})
        else:
            items.extend(_legacy(root))
    # An inventory changing while observed requires reconciliation, not a cached PASS.
    after, after_issues = library._inventory()
    if records != after or issues != after_issues or source_hash != lib.build_source_hash(lib.ROOT):
        issues.append({"code": "E-HOST-STATUS-CHANGED",
                       "message": "inventory or code changed during inspection; reconcile again"})
    for item in [planning, rights, *items]:
        if item.get("error"):
            issues.append({"kind": item.get("kind"), "path": item.get("path"), **item["error"]})
        if item.get("tasks", {}).get("error"):
            tasks = item["tasks"]
            issues.append({"kind": "tasks", "path": tasks["path"], **tasks["error"]})
    return {"view_kind": "HOST_RESEARCH_STATUS", "research_record_id": research_id,
            "checked_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
            "source_tree_hash": source_hash,
            "host_tools_sha256": {p.name: lib.digest(lib.read_bytes(p)) for p in
                                  (Path(__file__), lib.ROOT / "scripts/research_library.py",
                                   lib.ROOT / "scripts/source_acquisition.py")},
            "planning": planning, "rights": rights, "items": items, "issues": issues,
            "assurance": "RECOMPUTABLE_HOST_VIEW",
            "notes": ["Native validators own eligibility and execution; actions are advisory.",
                      "Listed legacy files and pending counts are not verified evidence.",
                      "Recheck affected inputs before writing; this view is not an atomic snapshot.",
                      "Markdown reports and list metadata never override native replay."]}
