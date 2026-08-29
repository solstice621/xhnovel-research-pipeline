from __future__ import annotations

import json
from pathlib import Path

from xhnovel_pipeline.engine import run_local_slice
from xhnovel_pipeline.importer import import_export
from xhnovel_pipeline.paths import repo_root


def test_consumer_layout_import_does_not_modify_export(tmp_path):
    root = repo_root()
    slice_dir = tmp_path / "slice"
    result = run_local_slice(root / "fixtures/positive/minimal-local", slice_dir, repo_root=root)
    consumer = tmp_path / "sandbox" / "research"
    export_dir = consumer / "imports" / "EXP-FIXTURE-001"
    mapping_dir = consumer / "mappings"
    export_dir.mkdir(parents=True)
    mapping_dir.mkdir(parents=True)
    src = result["work_dir"] / "export.json"
    dest = export_dir / "export.json"
    dest.write_bytes(src.read_bytes())
    before = dest.read_bytes()
    lock = export_dir / "import.lock.json"
    first = import_export(dest, lock)
    mapping = mapping_dir / "EXP-FIXTURE-001.md"
    mapping.write_text("# mapping\nThis file must not rewrite imports/export.json\n", encoding="utf-8")
    second = import_export(dest, lock)
    assert first["status"] == "imported"
    assert second["status"] == "idempotent"
    assert dest.read_bytes() == before
    assert "element_mapping" not in json.loads(dest.read_text(encoding="utf-8")).get("scene_facts", {})
