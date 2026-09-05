"""Shared deterministic helpers for standalone Phase 0 records.

These helpers intentionally preserve the existing Phase 0 validation semantics.
Canonical CAS bytes remain owned by :mod:`xhnovel_pipeline.canonical`; visible,
human-readable JSON remains owned by the callers that write those files.
"""

from __future__ import annotations

import pathlib
from typing import Any

from .errors import ValidationError
from .file_io import write_immutable as _publish_immutable
from .hashing import object_hash
from .ids import derived_id


def nonempty(value: Any, *, code: str, message: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(code, message)
    return value.strip()


def require_fields(
    value: dict[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
    code: str,
    label: str,
) -> None:
    fields = set(value)
    allowed = required | (optional or set())
    if not required <= fields or not fields <= allowed:
        raise ValidationError(code, f"{label} has an invalid field set")


def phase0_object_hash(
    value: dict[str, Any],
    *,
    omit: tuple[str, ...],
    code: str,
    label: str,
) -> str:
    try:
        return object_hash(value, omit=omit)
    except (TypeError, ValueError) as exc:
        raise ValidationError(code, f"{label} is not canonical JSON") from exc


def phase0_derived_id(
    kind: str,
    value: dict[str, Any],
    *,
    code: str,
    label: str,
) -> str:
    try:
        return derived_id(kind, value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(code, f"{label} is not canonical JSON") from exc


def sorted_strings(values: Any, *, code: str, field: str) -> list[str]:
    if not isinstance(values, list) or any(
        not isinstance(item, str) or not item.strip() for item in values
    ):
        raise ValidationError(code, f"{field} must be an array of non-empty strings")
    normalized = sorted({item.strip() for item in values})
    if len(normalized) != len(values):
        raise ValidationError(code, f"{field} must contain unique values")
    return normalized


def write_immutable(path: pathlib.Path, data: bytes) -> None:
    _publish_immutable(path, data)
