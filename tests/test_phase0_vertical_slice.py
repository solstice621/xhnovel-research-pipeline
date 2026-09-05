"""Acceptance regression for the deterministic Phase 0 product slice."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from xhnovel_pipeline.paths import repo_root


ROOT = repo_root()
FIXTURE = ROOT / "fixtures" / "positive" / "phase0-vertical-slice"
SCRIPT = ROOT / "scripts" / "phase0_vertical_slice_wheel_smoke.py"


def test_vertical_slice_fixture_is_complete_and_explicitly_licensed():
    preparation = json.loads(
        (FIXTURE / "preparation-input.json").read_text(encoding="utf-8")
    )
    declaration = preparation["source_declaration"]
    assert len(preparation["leads"]) == 3
    assert declaration["rights"] == {
        "basis": "LICENSED",
        "may_store_full_text": True,
        "may_send_to_external_model": True,
        "may_export_excerpts": True,
    }
    assert declaration["source_quality"] == {
        "edition_status": "USER_VERIFIED_COPY",
        "textual_completeness": "COMPLETE",
    }
    attestation = json.loads(
        (FIXTURE / "operator-attestation.json").read_text(encoding="utf-8")
    )
    assert attestation["basis"] == "LICENSED"
    assert attestation["attested_by"] == "xhnovel-fixture-operator"
    assert "CC0" in (FIXTURE / "RIGHTS.md").read_text(encoding="utf-8")
    novel = (FIXTURE / "novel.txt").read_text(encoding="utf-8")
    assert all(chapter in novel for chapter in ("第一章", "第二章", "第三章"))


@pytest.mark.parametrize("without_leads", [False, True])
def test_vertical_slice_crosses_public_cli_and_preserves_audit_bundle(tmp_path, without_leads):
    output_root = tmp_path / "acceptance"
    env = dict(os.environ)
    env["OPENAI_API_KEY"] = "must-not-be-used"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--fixture-root",
            str(FIXTURE),
            "--output-root",
            str(output_root),
            *(["--without-leads"] if without_leads else []),
        ],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr

    report = json.loads(
        (output_root / "acceptance-report.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "PASS"
    assert report["phase0"]["lead_count"] == (0 if without_leads else 3)
    assert report["phase0"]["motivating_lead_count"] == (0 if without_leads else 3)
    assert report["phase0"]["handoff_count"] == 1
    assert report["phase0"]["lead_source_roles"] == ([] if without_leads else ["LEAD_ONLY"])
    assert report["phase0"]["location_hint_leak_count"] == 0
    assert str(report["phase0"]["standing_attestation_id"]).startswith("OPA-")
    assert report["phase0"]["replay_status"] == "PASS"
    assert report["execution"]["pass1_status"] == "WAITING_FOR_AGENT"
    assert report["execution"]["pass2_status"] == "SUCCEEDED"
    assert report["execution"]["attempt_count"] == 1
    assert report["execution"]["event_states"] == [
        "STARTED",
        "WAITING_FOR_AGENT",
    ]
    assert report["execution"]["scene_candidate_count"] == 3
    assert report["closure"]["expected_input_spec_hash"] == report["closure"][
        "actual_input_spec_hash"
    ]
    assert report["closure"]["fresh_validate_all"] == "PASS"
    assert report["closure"]["fresh_execution_history_replay"] == "PASS"

    assert (output_root / report["phase0"]["handoff_path"]).is_file()
    assert (output_root / report["execution"]["receipt_path"]).is_file()
    catalog_path = output_root / report["closure"]["catalog_path"]
    assert catalog_path.is_file()
    assert (output_root / report["closure"]["store_path"]).is_dir()
    assert (output_root / "input" / "preparation-input.json").is_file()
    assert (output_root / "logs" / "04-execute-waiting.stdout.txt").is_file()
    assert (output_root / "logs" / "08-fresh-validate-all.stdout.txt").is_file()

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    segments = {item["segment_id"]: item["normalized_text"] for item in catalog["Segment"]}
    key_candidate = next(
        item for item in catalog["SceneCandidate"] if "阵钥" in item["summary"]
    )
    persistence = key_candidate["persistence"]
    assert persistence["status"] == "KNOWN"
    assert persistence["values"] == ["任务结束前持续"]
    assert len(persistence["support_spans"]) == 1
    support = persistence["support_spans"][0]
    support_text = segments[support["segment_id"]][support["start"] : support["end"]]
    assert "任务结束时" in support_text
    assert "收回阵钥并解除誓印" in support_text
