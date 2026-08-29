from __future__ import annotations

from xhnovel_pipeline.engine import run_local_slice
from xhnovel_pipeline.importer import import_export
from xhnovel_pipeline.paths import repo_root


def test_import_idempotent(tmp_path):
    root = repo_root()
    result = run_local_slice(root / "fixtures/positive/minimal-local", tmp_path / "slice", repo_root=root)
    export_path = result["work_dir"] / "export.json"
    lock = tmp_path / "import.lock.json"
    first = import_export(export_path, lock)
    second = import_export(export_path, lock)
    assert first["status"] == "imported"
    assert second["status"] == "idempotent"
