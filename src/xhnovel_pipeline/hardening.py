from __future__ import annotations

import json
import pathlib
import shutil
from typing import Any

from .audit import restore_export, verify_export_bytes
from .errors import ValidationError
from .hashing import strip_sha_prefix
from .store import ArtifactStore


def live_artifact_ids(catalog_data: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for art in catalog_data.get("Artifact") or []:
        ids.add(art["artifact_id"])
    for snap in catalog_data.get("CollectionSnapshot") or []:
        ids.update(snap.get("artifact_ids") or [])
    for bundle in catalog_data.get("EvidenceBundle") or []:
        ids.update(bundle.get("artifact_ids") or [])
    for export in catalog_data.get("EvidenceExport") or []:
        for item in export.get("artifact_manifest") or []:
            ids.add(item["artifact_id"])
        if export.get("bundle", {}).get("bundle_hash"):
            pass
    return ids


def backup_export(export_path: pathlib.Path, store: ArtifactStore, dest: pathlib.Path) -> dict[str, Any]:
    dest.mkdir(parents=True, exist_ok=True)
    data = export_path.read_bytes()
    export = verify_export_bytes(data)
    (dest / "export.json").write_bytes(data)
    art_dir = dest / "artifacts"
    art_dir.mkdir(exist_ok=True)
    copied = []
    for item in export["artifact_manifest"]:
        aid = item["artifact_id"]
        blob = store.get(aid)
        (art_dir / strip_sha_prefix(aid)).write_bytes(blob)
        copied.append(aid)
    return {"export_id": export["export_id"], "copied": copied, "dest": str(dest)}


def restore_from_backup(backup_dir: pathlib.Path, store: ArtifactStore) -> dict[str, Any]:
    return restore_export(backup_dir / "export.json", store, backup_dir / "artifacts")


def apply_gc(store: ArtifactStore, live_ids: set[str]) -> list[str]:
    removed = []
    root = store.root / "sha256"
    if not root.exists():
        return removed
    for path in list(root.rglob("*")):
        if not path.is_file() or path.name.startswith(".tmp-"):
            continue
        aid = f"sha256:{path.name}"
        if aid not in live_ids:
            path.unlink()
            removed.append(aid)
    return removed


def write_revocation(export_path: pathlib.Path, *, reason: str, created_at: str) -> dict[str, Any]:
    export = json.loads(export_path.read_text(encoding="utf-8"))
    record = {
        "export_id": export["export_id"],
        "export_hash": export["export_hash"],
        "status": "REVOKED",
        "reason": reason,
        "created_at": created_at,
    }
    side = export_path.with_suffix(".revocation.json")
    side.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def copy_tree(src: pathlib.Path, dest: pathlib.Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
