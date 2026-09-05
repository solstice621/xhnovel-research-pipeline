from concurrent.futures import ThreadPoolExecutor

import pytest

from xhnovel_pipeline import file_io
from xhnovel_pipeline.errors import ValidationError


@pytest.mark.parametrize("replace", [False, True])
def test_interrupted_write_preserves_destination_and_can_retry(tmp_path, monkeypatch, replace):
    destination = tmp_path / "result.json"
    original_bytes = b"previous checkpoint"
    if replace:
        destination.write_bytes(original_bytes)
    original_fdopen = file_io.os.fdopen

    class InterruptedWriter:
        def __init__(self, descriptor, mode):
            self.handle = original_fdopen(descriptor, mode)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def write(self, data):
            self.handle.write(data[:5])
            raise OSError("interrupted file write")

    writer = file_io.atomic_write if replace else file_io.write_immutable
    with monkeypatch.context() as patch:
        patch.setattr(file_io.os, "fdopen", InterruptedWriter)
        with pytest.raises(OSError, match="interrupted file write"):
            writer(destination, b'{"complete":true}')

    if replace:
        assert destination.read_bytes() == original_bytes
    else:
        assert not destination.exists()
    assert list(tmp_path.glob(".result.json.*")) == []
    writer(destination, b'{"complete":true}')
    assert destination.read_bytes() == b'{"complete":true}'


def test_concurrent_immutable_publication_is_idempotent_and_preserves_conflicts(tmp_path):
    destination = tmp_path / "result.json"
    with ThreadPoolExecutor(max_workers=8) as pool:
        created = list(pool.map(lambda _: file_io.write_immutable(destination, b"complete"), range(32)))
    assert sum(created) == 1
    with pytest.raises(ValidationError, match="E-CALLER: caller conflict"):
        file_io.write_immutable(destination, b"different", code="E-CALLER", message="caller conflict")
    assert destination.read_bytes() == b"complete"
    assert list(tmp_path.glob(".result.json.*")) == []
