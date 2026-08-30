from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone

from xhnovel_pipeline.audit import verify_export_bytes
from xhnovel_pipeline.catalog import Catalog
from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.store import ArtifactStore


def _load_trusted_catalog(path: pathlib.Path) -> Catalog:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("E-IMPORT-TRUST", f"cannot read trusted catalog {path}") from exc
    if not isinstance(data, dict):
        raise ValidationError("E-IMPORT-TRUST", "trusted catalog must be a JSON object")
    catalog = Catalog()
    for kind, objects in data.items():
        if kind not in catalog.by_type or not isinstance(objects, list):
            raise ValidationError("E-IMPORT-TRUST", f"invalid trusted catalog section {kind!r}")
        for obj in objects:
            if not isinstance(obj, dict):
                raise ValidationError("E-IMPORT-TRUST", f"invalid object in trusted catalog section {kind!r}")
            catalog.add(kind, obj)
    return catalog


def import_export(
    export_path: pathlib.Path,
    lock_path: pathlib.Path,
    *,
    trusted_catalog_path: pathlib.Path | None = None,
    artifact_store_path: pathlib.Path | None = None,
) -> dict:
    if trusted_catalog_path is None or artifact_store_path is None:
        raise ValidationError(
            "E-IMPORT-TRUST",
            "import requires an explicitly trusted producer catalog and ArtifactStore",
        )
    if not artifact_store_path.is_dir():
        raise ValidationError("E-IMPORT-TRUST", f"artifact store does not exist: {artifact_store_path}")
    data = export_path.read_bytes()
    catalog = _load_trusted_catalog(trusted_catalog_path)
    export = verify_export_bytes(data, catalog, ArtifactStore(artifact_store_path))
    if export["assurance"]["level"] == "UNQUALIFIED":
        raise ValidationError("E-ASSURANCE", "consumer import requires at least BUILD_QUALIFIED")
    lock = {
        "export_id": export["export_id"],
        "export_hash": export["export_hash"],
        "producer_commit": export["producer"]["repository_commit"],
        "schema_version": export["schema_version"],
        "imported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if lock_path.exists():
        existing = json.loads(lock_path.read_text(encoding="utf-8")) if lock_path.suffix == ".json" else None
        if lock_path.suffix in {".yaml", ".yml"}:
            import yaml

            existing = yaml.safe_load(lock_path.read_text(encoding="utf-8"))
        if existing and existing.get("export_hash") not in {None, "replace-after-slice"}:
            if existing.get("export_hash") != export["export_hash"]:
                raise ValidationError("E-IMPORT-LOCK", "import.lock does not match export hash")
            return {"status": "idempotent", "lock": existing}
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    return {"status": "imported", "lock": lock}
