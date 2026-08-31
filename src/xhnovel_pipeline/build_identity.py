from __future__ import annotations

import pathlib

from .errors import ValidationError
from .hashing import digest_prefix, object_hash, sha256_bytes


BUILD_IDENTITY_FIELDS = (
    "repository_commit",
    "source_tree_hash",
    "model",
    "prompt_template_hash",
    "parameters",
    "profile_version",
    "executor_build_id",
    "tool_policy_hash",
)


def build_source_hash(root: pathlib.Path) -> str:
    """Bind model runs to the standalone executable and analysis profile."""
    source_root = root / "src" / "xhnovel_pipeline"
    if not source_root.is_dir():
        source_root = pathlib.Path(__file__).resolve().parent
    paths = [root / "pyproject.toml", root / "requirements.lock"]
    paths.extend(sorted(source_root.glob("*.py")))
    paths.extend(sorted((root / "profiles" / "xuanhuan-gameplay-scene-v1").glob("*")))
    payload = []
    for path in paths:
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = f"installed-package/{path.name}"
        if not path.is_file():
            raise ValidationError("E-BUILD-BIND", f"missing build source {rel}")
        payload.append({"path": rel, "sha256": digest_prefix(sha256_bytes(path.read_bytes()))})
    return object_hash({"build_sources": payload}, omit=())
