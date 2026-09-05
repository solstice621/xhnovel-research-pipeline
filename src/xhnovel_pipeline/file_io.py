"""Publish complete files; interrupted writes never occupy the destination."""

from __future__ import annotations

import os
import pathlib
import tempfile

from .errors import ValidationError


def _write_temporary(path: pathlib.Path, data: bytes) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = pathlib.Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def atomic_write(path: pathlib.Path, data: bytes) -> None:
    """Replace a mutable checkpoint only after its complete bytes are durable."""
    temporary = _write_temporary(path, data)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def publish_immutable(path: pathlib.Path, data: bytes) -> None:
    """Publish without replacement, raising FileExistsError if already present."""
    temporary = _write_temporary(path, data)
    try:
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_immutable(
    path: pathlib.Path,
    data: bytes,
    *,
    code: str = "E-IMMUTABLE-OUTPUT",
    message: str | None = None,
) -> bool:
    """Publish once, returning whether this call created the destination."""
    def verify_existing() -> None:
        if not path.is_file() or path.read_bytes() != data:
            raise ValidationError(code, message or f"refusing to overwrite {path}")

    if path.exists():
        verify_existing()
        return False
    try:
        publish_immutable(path, data)
    except FileExistsError:
        verify_existing()
        return False
    return True
