"""Installed CLI acceptance for observation research, using a fixed fixture oracle.

The oracle fills only native agent-file answers. It is an authored test input,
never a semantic evaluator, live search implementation or production extractor.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


CHECKOUT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = CHECKOUT / "fixtures/positive/observation-research"
NOW = "2026-09-05T00:00:00Z"
TAINT = "TAINT_SEARCH_ONLY_7391"


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON object: {path}")
    return value


def _write(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def _run(output: Path, label: str, args: list[str], *, expected: int = 0, as_json: bool = True) -> Any:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("OPENAI_API_KEY", None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    command = [sys.executable, *args]
    completed = subprocess.run(command, cwd=output, env=env, check=False, capture_output=True, text=True, encoding="utf-8")
    logs = output / "logs"
    _write(logs / f"{label}.command.json", {
        "argv": command, "cwd": str(output), "returncode": completed.returncode,
        "removed_environment": ["PYTHONPATH", "OPENAI_API_KEY"], "encoding": "utf-8",
    })
    (logs / f"{label}.stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (logs / f"{label}.stderr.txt").write_text(completed.stderr, encoding="utf-8")
    if completed.returncode != expected:
        raise AssertionError(f"{label}: exit {completed.returncode}, expected {expected}\n{completed.stdout}\n{completed.stderr}")
    return json.loads(completed.stdout) if as_json else completed.stdout


def _cli(output: Path, label: str, *args: str, expected: int = 0, as_json: bool = True) -> Any:
    return _run(output, label, ["-m", "xhnovel_pipeline.cli", *map(str, args)], expected=expected, as_json=as_json)


def _probe(output: Path, *, require_wheel: bool) -> dict[str, Any]:
    probe = _run(output, "00-installation", ["-c", """
import json, os, pathlib, xhnovel_pipeline
from xhnovel_pipeline.paths import repo_root
root = repo_root()
assert 'PYTHONPATH' not in os.environ
assert 'OPENAI_API_KEY' not in os.environ
for name in ('generic-novel-spec.schema.json', 'observation-definition.schema.json',
             'generic-extraction-handoff.schema.json', 'observation-research-run.schema.json'):
    assert (root / 'contracts' / name).is_file(), name
print(json.dumps({'package_file': xhnovel_pipeline.__file__, 'data_root': str(root),
                  'cwd': str(pathlib.Path.cwd()), 'environment_isolated': True}))
""".strip()])
    assert Path(probe["cwd"]).resolve() == output.resolve()
    if require_wheel:
        for key in ("package_file", "data_root"):
            path = Path(probe[key]).resolve()
            if path == CHECKOUT or CHECKOUT in path.parents:
                raise AssertionError(f"{key} borrowed checkout content: {path}")
        package = Path(probe["package_file"])
        if "site-packages" not in {part.casefold() for part in package.parts}:
            raise AssertionError(f"package is not installed in site-packages: {package}")
        if output == CHECKOUT or CHECKOUT in output.parents:
            raise AssertionError("wheel smoke child cwd must be outside checkout")
    return {**probe, "require_wheel": require_wheel}


def _put_artifact(output: Path, label: str, research_root: Path, path: Path) -> str:
    attached = _cli(output, label, "observation-research", "attach", path, "--research-root", research_root)
    assert attached["evidence_status"] == "LEAD_ONLY"
    return attached["artifact_id"]


def _answer_pending(pending: dict[str, Any], oracle: list[dict[str, Any]], *, zero: bool) -> tuple[int, int]:
    assert pending["status"] == "WAITING_FOR_AGENT"
    assert pending["pending_count"] == len(pending["pending"]) > 0
    found: set[str] = set()
    record_count = 0
    for item in pending["pending"]:
        task = _read(Path(item["task"]))
        assert TAINT not in json.dumps(task, ensure_ascii=False)
        assert task["protocol"] == "xhnovel-generic-agent-files-v1"
        records = []
        for observation in ([] if zero else oracle):
            for span in task["input"]["unit"]["source_spans"]:
                offset = span["untrusted_text"].find(observation["phrase"])
                if offset < 0:
                    continue
                start = span["start"] + offset
                records.append({
                    "payload": observation["payload"],
                    "evidence_bindings": [{
                        "paths": observation["paths"],
                        "source_spans": [{"segment_id": span["segment_id"], "start": start, "end": start + len(observation["phrase"])}],
                    }],
                })
                found.add(observation["phrase"])
                break
        answer: dict[str, Any] = {"records": records}
        if "completion" in task["output"]["schema"]["required"]:
            answer["completion"] = {"status": "COMPLETE"}
        declared_answer = Path(item["task"]).parent.parent / task["answer_file"]
        assert declared_answer.resolve() == Path(item["answer"]).resolve()
        _write(declared_answer, answer)
        record_count += len(records)
    if not zero:
        assert found == {item["phrase"] for item in oracle}, "fixture phrases missing from native windows"
    return len(pending["pending"]), record_count


def _case(output: Path, inputs: Path, case: str, profile: str, kind: str, *, zero: bool = False) -> dict[str, Any]:
    research_root = output / case / "research"
    work_dir = output / ("work-zero" if zero else "work-shared")
    drafts = output / case / "drafts"
    goal = "Observe explicitly named places" if profile.startswith("geography") else "Observe explicitly named races"
    title = "Fixture Quiet Novel" if zero else "Fixture Novel"
    source = inputs / ("zero-novel.txt" if zero else "novel.txt")
    def command(step: str, *args: str, **kwargs: Any) -> Any:
        return _cli(output, f"{case}-{step}", *args, **kwargs)
    intake = {
        "user_goal_verbatim": goal, "neutral_goal_text": goal, "neutral_goal_origin": "USER_VERBATIM_NO_SEEDS",
        "explicit_scope": {"genres": {"include": ["fantasy"], "exclude": []}, "scope_origin": "USER_EXPLICIT"},
        "seeds": [], "frozen_at": NOW,
    }
    intake_path = _write(drafts / "intake.json", intake)
    command("01-intake", "seal-intake", intake_path, "--work-dir", research_root, as_json=False)
    planning = _read(research_root / "planning-manifest.json")
    definition_draft = {
        "intake_artifact_id": planning["intake_artifact_id"], "neutral_input_artifact_id": planning["neutral_input_artifact_id"],
        "research_question": goal, "inclusion_rules": ["Names explicitly stated in the current window"],
        "exclusion_rules": ["Do not infer absent entities"], "required_distinctions": [],
        "requirements": [{"statement": "Collect explicit names", "applies_to": [kind], "necessity": "REQUIRED",
                          "locality": "UNIT_LOCAL", "origin": "HOST_INTERPRETATION", "origin_pointer": None, "origin_quote": None}],
        "locality": "UNIT_LOCAL", "locality_rationale": "An explicit name is supported within the window",
        "decomposition_status": "NOT_REQUIRED",
        "authoring": {"host": "acceptance fixture", "input_artifact_id": planning["neutral_input_artifact_id"],
                      "assurance": "NOT_PROVEN", "isolation_claim": "CONTEXT_NOT_ISOLATED"}, "frozen_at": NOW,
    }
    definition = command("02-definition", "seal-observation-definition", _write(drafts / "definition.json", definition_draft), "--work-dir", research_root)
    resolution_draft = {
        "definition_artifact_id": definition["artifact_id"], "decision": "REUSE_EXISTING", "selected_profile_ref": profile, "fit": "EXACT",
        "admission": {"status": "HOST_REVIEWED_EXECUTABLE", "reviewer": "acceptance fixture", "review_reference": "fixed fixture contract review; semantic quality not evaluated"},
        "coverage": [{"requirement_id": definition["record"]["requirements"][0]["requirement_id"], "disposition": "COVERED",
                      "payload_kinds": [kind], "payload_paths": ["/name"], "prompt_rules": [], "rationale": "Profile defines the name payload field"}],
        "rationale": "Fixed built-in Profile used for acceptance", "assessor": "acceptance fixture", "frozen_at": NOW,
    }
    resolution = command("03-resolution", "seal-profile-resolution", _write(drafts / "resolution.json", resolution_draft), "--work-dir", research_root)
    run_draft = {
        "definition_artifact_id": definition["artifact_id"], "resolution_artifact_id": resolution["artifact_id"],
        "search_strategy": {"queries": ["fixture search"], "selection_rationale": "Synthetic search input for CLI acceptance only"},
        "budget": {"target_works": 1, "max_search_rounds": 1, "max_source_attempts": 1, "max_full_work_attempts": 1, "max_resume_invocations": 1},
        "budget_authoring": {"host": "acceptance fixture", "input_artifact_id": planning["neutral_input_artifact_id"],
                             "assurance": "NOT_PROVEN", "isolation_claim": "CONTEXT_NOT_ISOLATED"}, "frozen_at": NOW,
    }
    run = command("04-run-init", "observation-research", "init", _write(drafts / "run.json", run_draft), "--work-dir", research_root)
    def event(step: str, event_type: str, detail: dict[str, Any]) -> dict[str, Any]:
        draft = {"operation_id": f"{case}-{step}", "event_type": event_type, "detail": detail, "recorded_at": NOW}
        return command(step, "observation-research", "record", run["path"], _write(drafts / f"{step}.json", draft), "--research-root", research_root)
    search_started = event("05-search-start", "SEARCH_STARTED", {"query": "fixture search"})
    search_artifact = _put_artifact(output, f"{case}-06-search-cas", research_root, inputs / "search-results.json")
    search = event("07-search-finish", "SEARCH_FINISHED", {"start_event_artifact_id": search_started["artifact_id"], "outcome": "COMPLETED", "result_artifact_ids": [search_artifact], "error": None})
    lead_draft = {
        "definition_artifact_id": definition["artifact_id"],
        "work_claim": {"title": title, "author": "Fixture Author", "language": "zh", "aliases": []},
        "relevance_hypothesis": f"{TAINT}: a simulated search selected this fixture work",
        "lead_sources": [{"source_kind": "OTHER", "locator": f"fixture:{case}", "supports": ["WORK_IDENTITY"]}],
        "location_hints": [], "frozen_at": NOW,
    }
    lead = command("08-lead", "seal-observation-work-lead", _write(drafts / "lead.json", lead_draft), "--work-dir", research_root)
    event("09-lead-record", "LEAD_RECORDED", {"lead_artifact_id": lead["artifact_id"], "search_event_artifact_id": search["artifact_id"]})
    declaration = {
        "work": {"canonical_title": title, "author": "Fixture Author", "language": "zh", "aliases": [], "external_ids": []},
        "source": {"kind": "txt", "path": str(source)},
        "rights": {"basis": "LICENSED", "may_store_full_text": True, "may_send_to_external_model": True, "may_export_excerpts": True},
        "source_quality": {"edition_status": "USER_VERIFIED_COPY", "textual_completeness": "COMPLETE"},
        "edition_label": "CC0 complete acceptance miniature", "declared_at": NOW,
    }
    preparation = {"definition_artifact_id": definition["artifact_id"], "resolution_artifact_id": resolution["artifact_id"],
                   "work_lead_artifact_ids": [lead["artifact_id"]], "source_declaration": declaration, "requested_at": NOW}
    preparation_path = _write(drafts / "preparation.json", preparation)
    source_artifact = _put_artifact(output, f"{case}-10-source-cas", research_root, preparation_path)
    source_started = event("11-source-start", "SOURCE_STARTED", {"lead_artifact_ids": [lead["artifact_id"]], "source_input_artifact_id": source_artifact})
    prepared = command("12-prepare", "prepare-generic-handoff", preparation_path, "--work-dir", research_root)
    assert TAINT not in json.dumps(prepared["novel_spec"])
    assert TAINT not in json.dumps(prepared["handoff"])
    assert set(prepared["novel_spec"]) == {"source", "rights", "source_quality", "limits", "strict_order"}
    command("13-handoff-validate", "validate-generic-handoff", prepared["handoff_path"], "--research-root", research_root)
    event("14-source-finish", "SOURCE_FINISHED", {"start_event_artifact_id": source_started["artifact_id"], "status": "ELIGIBLE", "handoff_artifact_id": prepared["handoff_artifact_id"], "reason": "Explicitly licensed complete acceptance source"})
    execute = ("observation-research", "execute", run["path"], prepared["handoff_path"], "--research-root", research_root,
               "--work-dir", work_dir, "--executor", "agent-files", "--agent-model-label", "fixture-oracle-not-semantic-evaluation")
    pending = command("15-waiting", *execute, expected=3)
    oracle = _read(inputs / "answer-oracle.json")[profile]
    tasks, answers = _answer_pending(pending, oracle, zero=zero)
    # WAITING resumes by rerunning the identical command. Explicit --resume is
    # reserved by the native protocol for an interrupted invocation.
    completed = command("16-resume", *execute)
    assert completed["status"] == "SUCCEEDED"
    receipt = completed["receipt"]
    target = receipt["result"]
    assert target["corpus_record_count"] == (0 if zero else 1)
    assert target["semantic_assurance"] == "UNQUALIFIED"
    assert target["semantic_coverage"] == "UNMEASURED"
    assert target["input_spec_hash"] == prepared["handoff"]["novel_spec"]["expected_input_spec_hash"]
    command("17-receipt-validate", "validate-generic-execution", completed["receipt_path"], "--research-root", research_root, "--work-dir", work_dir)
    event("18-stop", "STOP", {"reason": "TARGET_WORKS_REACHED", "rationale": "The one complete fixture work succeeded"})
    report_path = output / case / "report.json"
    report = command("19-report", "observation-research", "report", run["path"], "--research-root", research_root, "--output", report_path)
    assert report["counts"]["leads_considered"] == report["counts"]["successful_works"] == 1
    assert report["counts"]["corpus_record_count_sum"] == (0 if zero else 1)
    assert report["counts"]["zero_result_receipts"] == (1 if zero else 0)
    assert report["budget_used"] == {"search_rounds": 1, "source_attempts": 1, "full_work_attempts": 1, "resume_invocations": 1}
    assert len(report["results"][0]["evidence_index"]) == (0 if zero else 1)
    command("20-report-validate", "observation-research", "validate", run["path"], "--research-root", research_root, "--report", report_path)
    return {
        "case": case, "profile_ref": profile, "source_path": str(source), "research_root": str(research_root),
        "work_dir": str(work_dir), "run_path": run["path"], "report_path": str(report_path),
        "handoff_path": prepared["handoff_path"], "receipt_path": completed["receipt_path"],
        "status": completed["status"], "pending_task_count": tasks, "oracle_record_count": answers,
        "corpus_record_count": target["corpus_record_count"], "semantic_assurance": target["semantic_assurance"],
        "text_snapshot_id": target["text_snapshot_id"], "result": target,
    }


def run_acceptance(fixture_root: Path, output_root: Path, *, require_wheel: bool) -> Path:
    output_root = output_root.resolve()
    if output_root.exists():
        raise AssertionError(f"refusing to overwrite acceptance output: {output_root}")
    output_root.mkdir(parents=True)
    inputs = output_root / "inputs"
    shutil.copytree(fixture_root.resolve(), inputs)
    assert "CC0" in (inputs / "RIGHTS.md").read_text(encoding="utf-8")
    assert _read(inputs / "answer-oracle.json")["oracle_kind"] == "FIXTURE_ONLY_NOT_SEMANTIC_EVALUATION"
    probe = _probe(output_root, require_wheel=require_wheel)
    cases = [
        _case(output_root, inputs, "geography", "geography-unique-v1", "PLACE_MENTION"),
        _case(output_root, inputs, "race", "race-mention-v1", "RACE_MENTION"),
        _case(output_root, inputs, "race-zero", "race-mention-v1", "RACE_MENTION", zero=True),
    ]
    assert cases[0]["text_snapshot_id"] == cases[1]["text_snapshot_id"], "same source/spec must reuse ingestion in the explicit shared directory"
    # Verify completed closures without original source bytes or visible spec copies.
    for source in {Path(case["source_path"]) for case in cases}:
        source.unlink()
    for case in cases:
        (Path(case["handoff_path"]).parent / "novel-spec.json").unlink()
        _cli(output_root, f"{case['case']}-21-offline-receipt", "validate-generic-execution", case["receipt_path"], "--research-root", case["research_root"], "--work-dir", case["work_dir"])
        _cli(output_root, f"{case['case']}-22-offline-report", "observation-research", "validate", case["run_path"], "--research-root", case["research_root"], "--report", case["report_path"])
        case["offline_fresh_process_validation"] = "PASS"
    report_path = output_root / "acceptance-report.json"
    _write(report_path, {
        "report_kind": "NON_AUTHORITATIVE_ACCEPTANCE_REPORT", "status": "PASS",
        "acceptance_gate": "OBSERVATION_RESEARCH_INSTALLED_CLI_SLICE", "installation": probe,
        "search_was_simulated": True, "oracle_is_semantic_evaluation": False,
        "fixture_completeness": "COMPLETE_MINIATURE_WORKS", "cases": cases,
    })
    return report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-root", type=Path, default=FIXTURE_ROOT)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--require-wheel", action="store_true")
    args = parser.parse_args(argv)
    if args.output_root is not None:
        print(run_acceptance(args.fixture_root, args.output_root, require_wheel=args.require_wheel))
    else:
        with tempfile.TemporaryDirectory(prefix="xhnovel-observation-smoke-") as raw:
            print(run_acceptance(args.fixture_root, Path(raw) / "acceptance", require_wheel=args.require_wheel))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
