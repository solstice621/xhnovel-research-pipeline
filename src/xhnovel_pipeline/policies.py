from __future__ import annotations

import pathlib
from typing import Any

import yaml

from .canonical import canonical_dumps
from .hashing import digest_prefix, sha256_bytes
from .paths import repo_root


def load_yaml(path: pathlib.Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def policy_bundle_hash(root: pathlib.Path | None = None) -> str:
    root = root or repo_root()
    manifest = load_yaml(root / "policies" / "manifest.yaml")
    payload = []
    for rel in manifest["documents"]:
        path = root / rel
        payload.append({"path": rel, "sha256": digest_prefix(sha256_bytes(path.read_bytes()))})
    return digest_prefix(sha256_bytes(canonical_dumps(payload)))


def load_policy_docs(root: pathlib.Path | None = None) -> dict[str, Any]:
    root = root or repo_root()
    manifest = load_yaml(root / "policies" / "manifest.yaml")
    docs = {}
    for rel in manifest["documents"]:
        docs[rel] = load_yaml(root / rel)
    return docs
