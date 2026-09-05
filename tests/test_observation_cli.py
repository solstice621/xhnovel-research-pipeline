"""Public CLI slice with fixed answers, deliberately not a semantic benchmark."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/positive/observation-research"
SCRIPT = ROOT / "scripts/observation_research_wheel_smoke.py"


def test_campaign_invalid_api_configuration_exits_nonzero_and_preserves_prestart_failure(tmp_path):
    from test_generic_handoff import prepared_handoff
    from xhnovel_pipeline.observation_campaign import (
        init_observation_research, record_observation_research_event,
    )
    from xhnovel_pipeline.observation_common import get_record
    from xhnovel_pipeline.runtime import TEST_NOW

    research_root, _, prepared = prepared_handoff(tmp_path)
    builder = prepared["handoff"]["builder"]
    definition = get_record(research_root, builder["definition_artifact_id"])
    run = init_observation_research({
        "definition_artifact_id": builder["definition_artifact_id"],
        "resolution_artifact_id": builder["resolution_artifact_id"],
        "search_strategy": {"queries": ["fixture-only"], "selection_rationale": "Authored configuration failure test"},
        "budget": {"target_works": 1, "max_search_rounds": 0, "max_source_attempts": 1, "max_full_work_attempts": 1, "max_resume_invocations": 0},
        "budget_authoring": {"host": "acceptance fixture", "input_artifact_id": definition["neutral_input_artifact_id"],
                             "assurance": "NOT_PROVEN", "isolation_claim": "CONTEXT_NOT_ISOLATED"},
        "frozen_at": TEST_NOW,
    }, research_root)
    def record(operation, kind, detail):
        return record_observation_research_event(run.record, {
            "operation_id": operation, "event_type": kind, "detail": detail, "recorded_at": TEST_NOW,
        }, research_root)
    lead = builder["work_lead_artifact_ids"][0]
    record("lead", "LEAD_RECORDED", {"lead_artifact_id": lead, "search_event_artifact_id": None})
    source = record("source-start", "SOURCE_STARTED", {
        "lead_artifact_ids": [lead], "source_input_artifact_id": builder["source_declaration_artifact_id"],
    })
    record("source-finish", "SOURCE_FINISHED", {
        "start_event_artifact_id": source.artifact_id, "status": "ELIGIBLE",
        "handoff_artifact_id": prepared["handoff_artifact_id"], "reason": "Authorized complete test fixture",
    })
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    result = subprocess.run([
        sys.executable, "-m", "xhnovel_pipeline.cli", "observation-research", "execute", str(run.path),
        prepared["handoff_path"], "--research-root", str(research_root), "--work-dir", str(tmp_path / "work"),
        "--executor", "api",
    ], env=env, capture_output=True, text=True, encoding="utf-8", check=False)
    assert result.returncode == 1, result.stdout + result.stderr
    failure = json.loads(result.stdout)
    assert failure["status"] == "FAILED_PRESTART"
    report = subprocess.run([
        sys.executable, "-m", "xhnovel_pipeline.cli", "observation-research", "report", str(run.path),
        "--research-root", str(research_root),
    ], env=env, capture_output=True, text=True, encoding="utf-8", check=False)
    assert report.returncode == 0, report.stdout + report.stderr
    frozen = json.loads(report.stdout)
    assert frozen["counts"]["failed_execution_invocations"] == 1
    assert frozen["counts"]["successful_receipts"] == 0
    assert frozen["execution_invocations"][0]["finish"]["detail"]["status"] == "FAILED_PRESTART"
    assert frozen["budget_used"]["full_work_attempts"] == 1


def test_observation_cli_full_slice_keeps_nonempty_zero_and_offline_results(tmp_path):
    env = dict(os.environ)
    env["OPENAI_API_KEY"] = "fixture-must-never-call-a-model"
    env["PYTHONPATH"] = "fixture-invalid-inherited-path"
    output = tmp_path / "acceptance"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--fixture-root", str(FIXTURE), "--output-root", str(output)],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / "acceptance-report.json").read_text(encoding="utf-8"))
    assert report["status"] == "PASS"
    assert report["installation"]["environment_isolated"] is True
    assert report["search_was_simulated"] is True
    assert report["oracle_is_semantic_evaluation"] is False
    assert [case["corpus_record_count"] for case in report["cases"]] == [1, 1, 0]
    assert {case["profile_ref"] for case in report["cases"]} == {"geography-unique-v1", "race-mention-v1"}
    assert all(case["status"] == "SUCCEEDED" and case["semantic_assurance"] == "UNQUALIFIED" for case in report["cases"])
    assert all(case["offline_fresh_process_validation"] == "PASS" for case in report["cases"])
    assert report["cases"][0]["text_snapshot_id"] == report["cases"][1]["text_snapshot_id"]
    assert all(Path(case["receipt_path"]).is_file() and Path(case["report_path"]).is_file() for case in report["cases"])
    commands = [json.loads(path.read_text(encoding="utf-8")) for path in (output / "logs").glob("*.command.json")]
    assert commands and all(Path(item["cwd"]).resolve() == output.resolve() for item in commands)
    assert all(item["removed_environment"] == ["PYTHONPATH", "OPENAI_API_KEY"] for item in commands)
