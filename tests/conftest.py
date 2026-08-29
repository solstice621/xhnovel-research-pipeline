from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from xhnovel_pipeline.engine import run_local_slice
from xhnovel_pipeline.paths import repo_root


@pytest.fixture
def slice_result(tmp_path: Path):
    root = repo_root()
    fixture = root / "fixtures/positive/minimal-local"
    return run_local_slice(fixture, tmp_path, repo_root=root)
