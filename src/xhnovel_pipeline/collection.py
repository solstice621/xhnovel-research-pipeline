from __future__ import annotations

import pathlib
from typing import Any

from .engine import run_local_slice


def run_collection(
    fixture_dir: pathlib.Path,
    work_dir: pathlib.Path,
    *,
    repo_root: pathlib.Path,
    provider: Any | None = None,
    fetcher: Any | None = None,
) -> dict[str, Any]:
    """Run discovery, retrieval, parsing and snapshot freeze without extraction."""
    return run_local_slice(
        fixture_dir,
        work_dir,
        repo_root=repo_root,
        provider=provider,
        fetcher=fetcher,
        collection_only=True,
    )
