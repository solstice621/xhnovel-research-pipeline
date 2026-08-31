from __future__ import annotations

import pathlib


def repo_root() -> pathlib.Path:
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "contracts").is_dir():
            return parent
    raise RuntimeError("cannot locate repository root")
