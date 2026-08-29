#!/usr/bin/env python3
"""Recompute SHA-256 of the frozen sandbox research snapshot and compare to the manifest."""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "fixtures/legacy/baseline-manifest.json"
SNAPSHOT = ROOT / "fixtures/legacy/sandbox-research-ff8b8bb"


def main() -> int:
    expected = json.loads(MANIFEST.read_text(encoding="utf-8"))
    files = expected["files"]
    errors: list[str] = []
    seen: set[str] = set()
    for path in sorted(SNAPSHOT.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        rel = path.relative_to(SNAPSHOT).as_posix()
        seen.add(rel)
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        meta = files.get(rel)
        if meta is None:
            errors.append(f"unexpected file {rel}")
            continue
        if digest != meta["sha256"] or len(data) != meta["bytes"]:
            errors.append(
                f"{rel}: got sha256={digest} bytes={len(data)} "
                f"expected sha256={meta['sha256']} bytes={meta['bytes']}"
            )
    missing = sorted(set(files) - seen)
    for rel in missing:
        errors.append(f"missing {rel}")
    if errors:
        print("FAIL: migration baseline hash mismatch", file=sys.stderr)
        for err in errors:
            print(err, file=sys.stderr)
        return 1
    print(
        f"OK: recomputed {len(files)} files; "
        f"legacy_contract_commit={expected['legacy_contract_commit']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
