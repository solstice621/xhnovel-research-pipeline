from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

from .canonical import canonical_dumps
from .constants import PLACEHOLDER_HASHES

SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
SELF_HASH_FIELDS = (
    "bundle_hash",
    "snapshot_hash",
    "export_hash",
    "result_set_hash",
    "output_hash",
    "structure_hash",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def artifact_id_for(data: bytes) -> str:
    return f"sha256:{sha256_bytes(data)}"


def digest_prefix(hex_digest: str) -> str:
    return f"sha256:{hex_digest}"


def strip_sha_prefix(value: str) -> str:
    if value.startswith("sha256:"):
        return value[7:]
    return value


def is_real_sha256(value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = strip_sha_prefix(value.strip().lower())
    if text in PLACEHOLDER_HASHES:
        return False
    return bool(SHA256_HEX.fullmatch(text))


def object_hash(obj: dict[str, Any], omit: Iterable[str] = SELF_HASH_FIELDS) -> str:
    skipped = set(omit)
    payload = {k: v for k, v in obj.items() if k not in skipped}
    return digest_prefix(sha256_bytes(canonical_dumps(payload)))


def sorted_ids(values: Iterable[str]) -> list[str]:
    return sorted(values)
