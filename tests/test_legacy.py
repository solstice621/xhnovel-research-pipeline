from __future__ import annotations

from xhnovel_pipeline.paths import repo_root
from xhnovel_pipeline.tools_legacy import check_legacy_scene_001, check_scene_002_tombstone


def test_baseline_tool():
    import subprocess
    import sys

    root = repo_root()
    out = subprocess.check_output([sys.executable, str(root / "tools/verify_migration_baseline.py")], text=True)
    assert "OK" in out


def test_legacy_roles():
    root = repo_root()
    check_legacy_scene_001(root)
    check_scene_002_tombstone(root)
