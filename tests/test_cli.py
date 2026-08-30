from __future__ import annotations

import json
import subprocess
import sys

from xhnovel_pipeline.collection_quality import make_collection_decision
from xhnovel_pipeline.engine import run_local_slice
from xhnovel_pipeline.hashing import artifact_id_for
from xhnovel_pipeline.paths import repo_root


def test_cli_legacy_and_slice(tmp_path):
    root = repo_root()
    subprocess.check_call([sys.executable, "-m", "xhnovel_pipeline.cli", "legacy-check"], cwd=root)
    work = tmp_path / "slice"
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "xhnovel_pipeline.cli",
            "run",
            "local-slice",
            str(root / "fixtures/positive/minimal-local"),
            "--work-dir",
            str(work),
        ],
        cwd=root,
    )
    export = json.loads((work / "export.json").read_text(encoding="utf-8"))
    subprocess.check_call(
        [sys.executable, "-m", "xhnovel_pipeline.cli", "verify-export", str(work / "export.json")],
        cwd=root,
    )
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "xhnovel_pipeline.cli",
            "validate",
            "all",
            str(work / "catalog.json"),
        ],
        cwd=root,
    )
    assert export["export_id"].startswith("EXP-")
    assert len(export["export_id"]) > len("EXP-")


def test_cli_collect_stops_before_extraction(tmp_path):
    root = repo_root()
    work = tmp_path / "collection"
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "xhnovel_pipeline.cli",
            "collect",
            "local",
            str(root / "fixtures/positive/minimal-local"),
            "--work-dir",
            str(work),
        ],
        cwd=root,
    )
    snapshot = json.loads((work / "collection-snapshot.json").read_text(encoding="utf-8"))
    catalog = json.loads((work / "collection-catalog.json").read_text(encoding="utf-8"))
    assert snapshot["status"] == "FROZEN"
    assert "Claim" not in catalog
    assert "EvidenceExport" not in catalog


def test_cli_compares_collection_decisions(tmp_path):
    input_id = artifact_id_for(b"input")
    outcome = {"disposition": "SELECTED"}
    common = {
        "task": "RELEVANCE",
        "subject_ids": ["HIT-CANDIDATE"],
        "input_artifact_ids": [input_id],
        "outcome": outcome,
        "confidence": "HIGH",
        "basis": ["title and snippet match"],
        "created_at": "2026-08-29T00:00:00Z",
    }
    collector = make_collection_decision(
        **common,
        assessor_role="COLLECTOR",
        assessor_build_id="small-model-v1",
        output_artifact_id=artifact_id_for(b"collector-output"),
    )
    reviewer = make_collection_decision(
        **common,
        assessor_role="REVIEWER",
        assessor_build_id="large-model-v1",
        output_artifact_id=artifact_id_for(b"reviewer-output"),
    )
    collector_path = tmp_path / "collector.json"
    reviewer_path = tmp_path / "reviewer.json"
    collector_path.write_text(json.dumps(collector), encoding="utf-8")
    reviewer_path.write_text(json.dumps(reviewer), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "xhnovel_pipeline.cli",
            "review-collection",
            str(collector_path),
            str(reviewer_path),
        ],
        cwd=repo_root(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout)["verdict"] == "AGREE"
