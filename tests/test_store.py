from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from xhnovel_pipeline.hashing import artifact_id_for
from xhnovel_pipeline.store import ArtifactStore


def test_concurrent_identical_puts_publish_one_verified_artifact(tmp_path) -> None:
    store = ArtifactStore(tmp_path / "objects")
    payload = b'{"records":[]}\n'
    expected = artifact_id_for(payload)

    with ThreadPoolExecutor(max_workers=32) as pool:
        artifact_ids = list(pool.map(lambda _ordinal: store.put(payload), range(128)))

    assert set(artifact_ids) == {expected}
    assert store.get(expected) == payload
    assert list(store._path(expected).parent.glob(".tmp-*")) == []
