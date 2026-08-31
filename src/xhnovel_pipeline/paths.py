from __future__ import annotations

import pathlib
import sysconfig
from importlib import metadata


def repo_root() -> pathlib.Path:
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "contracts").is_dir():
            return parent
    try:
        installed_files = metadata.files("xhnovel-pipeline") or []
    except metadata.PackageNotFoundError:
        installed_files = []
    for item in installed_files:
        if item.as_posix().endswith(
            "xhnovel_pipeline_data/contracts/research-request.schema.json"
        ):
            candidate = pathlib.Path(item.locate()).resolve().parents[1]
            if (candidate / "contracts").is_dir() and (candidate / "profiles").is_dir():
                return candidate
    candidate = pathlib.Path(sysconfig.get_path("data")) / "xhnovel_pipeline_data"
    if (candidate / "contracts").is_dir() and (candidate / "profiles").is_dir():
        return candidate.resolve()
    raise RuntimeError("cannot locate repository root")
