from __future__ import annotations

import json
from typing import Any


def canonical_dumps(value: Any) -> bytes:
    return _encode(value)


def _encode(value: Any) -> bytes:
    if value is None:
        return b"null"
    if value is True:
        return b"true"
    if value is False:
        return b"false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value).encode("ascii")
    if isinstance(value, float):
        raise TypeError("canonical JSON forbids floats")
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False).encode("utf-8")
    if isinstance(value, list):
        return b"[" + b",".join(_encode(item) for item in value) + b"]"
    if isinstance(value, dict):
        parts = []
        for key in sorted(value.keys()):
            if not isinstance(key, str):
                raise TypeError("canonical object keys must be strings")
            parts.append(_encode(key) + b":" + _encode(value[key]))
        return b"{" + b",".join(parts) + b"}"
    raise TypeError(f"unhashable type {type(value)!r}")
