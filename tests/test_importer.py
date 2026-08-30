from __future__ import annotations

from xhnovel_pipeline.engine import run_local_slice
from xhnovel_pipeline.importer import import_export
from xhnovel_pipeline.paths import repo_root


def test_import_idempotent(tmp_path):
    root = repo_root()
    result = run_local_slice(root / "fixtures/positive/minimal-local", tmp_path / "slice", repo_root=root)
    export_path = result["work_dir"] / "export.json"
    catalog_path = result["work_dir"] / "catalog.json"
    lock = tmp_path / "import.lock.json"
    store_path = result["root_work_dir"] / "objects"
    first = import_export(export_path, lock, trusted_catalog_path=catalog_path, artifact_store_path=store_path)
    second = import_export(export_path, lock, trusted_catalog_path=catalog_path, artifact_store_path=store_path)
    assert first["status"] == "imported"
    assert second["status"] == "idempotent"
