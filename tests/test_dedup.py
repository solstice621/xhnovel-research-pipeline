from __future__ import annotations

from xhnovel_pipeline.catalog import Catalog
from xhnovel_pipeline.engine import put_artifact
from xhnovel_pipeline.store import ArtifactStore


def test_exact_duplicate_bytes_share_artifact(tmp_path):
    catalog = Catalog()
    store = ArtifactStore(tmp_path / "objects")
    a = put_artifact(catalog, store, b"same", media_type="text/plain")
    b = put_artifact(catalog, store, b"same", media_type="text/plain")
    assert a == b
    assert len(catalog.all("Artifact")) == 1
