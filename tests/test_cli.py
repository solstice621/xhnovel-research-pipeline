from __future__ import annotations

import json
import subprocess
import sys

from xhnovel_pipeline.engine import run_local_slice
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
    assert export["export_id"] == "EXP-FIXTURE-001"
