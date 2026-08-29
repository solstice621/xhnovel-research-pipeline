from __future__ import annotations

import json
import pathlib

from xhnovel_pipeline.audit import verify_export_bytes
from xhnovel_pipeline.errors import ValidationError


def import_export(export_path: pathlib.Path, lock_path: pathlib.Path) -> dict:
    data = export_path.read_bytes()
    export = verify_export_bytes(data)
    lock = {
        "export_id": export["export_id"],
        "export_hash": export["export_hash"],
        "producer_commit": export["producer"]["repository_commit"],
        "schema_version": export["schema_version"],
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
