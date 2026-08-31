from __future__ import annotations

import pathlib
import subprocess
from datetime import datetime, timezone


# Stable timestamp used only by deterministic tests and examples.
TEST_NOW = "2026-08-29T00:00:00Z"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def repository_commit(root: pathlib.Path) -> str:
    """Return the exact repository commit, including from a linked worktree."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown-dev"
    commit = result.stdout.strip()
    return commit if len(commit) == 40 else "unknown-dev"
