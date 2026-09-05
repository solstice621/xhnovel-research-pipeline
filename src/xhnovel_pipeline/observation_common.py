"""Small deterministic primitives for standalone observation research records.

These objects never enter the evidence Catalog. Visible JSON is an immutable
convenience copy; readers must obtain authoritative records from verified CAS.
"""
from __future__ import annotations

import copy
import json
import pathlib
from dataclasses import dataclass
from typing import Any, Callable

from .canonical import canonical_dumps
from .errors import ValidationError
from .file_io import write_immutable
from .hashing import is_real_sha256, object_hash
from .ids import derived_id
from .schema import validate_schema
from .store import ArtifactStore


@dataclass(frozen=True)
class SealedRecord:
    record: dict[str, Any]
    artifact_id: str
    path: pathlib.Path


def research_store(research_root: pathlib.Path) -> ArtifactStore:
    return ArtifactStore(pathlib.Path(research_root) / "objects")


def read_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("E-OBSERVATION-JSON", f"cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise ValidationError("E-OBSERVATION-JSON", "record must be a JSON object")
    return value


def seal_record(kind: str, body: dict[str, Any], *, id_field: str, hash_field: str) -> dict[str, Any]:
    if id_field in body or hash_field in body:
        raise ValidationError("E-OBSERVATION-IDENTITY", "draft must not supply generated identity")
    record = copy.deepcopy(body)
    try:
        record[id_field] = derived_id(kind, body)
        record[hash_field] = object_hash(body, omit=())
    except (TypeError, ValueError) as exc:
        raise ValidationError("E-OBSERVATION-IDENTITY", "record is not canonical JSON") from exc
    validate_schema(kind, record)
    return record


def validate_record_identity(record: dict[str, Any], kind: str, *, id_field: str, hash_field: str) -> dict[str, Any]:
    validate_schema(kind, record)
    body = {key: value for key, value in record.items() if key not in {id_field, hash_field}}
    try:
        valid = record[id_field] == derived_id(kind, body) and record[hash_field] == object_hash(body, omit=())
    except (TypeError, ValueError) as exc:
        raise ValidationError("E-OBSERVATION-IDENTITY", "record is not canonical JSON") from exc
    if not valid:
        raise ValidationError("E-OBSERVATION-IDENTITY", f"{kind} identity changed")
    return copy.deepcopy(record)


def record_path(research_root: pathlib.Path, kind: str, artifact_id: str) -> pathlib.Path:
    # kind is always a registered schema key, never an arbitrary path.
    from .schema import SCHEMA_BY_TYPE
    if kind not in SCHEMA_BY_TYPE:
        raise ValidationError("E-OBSERVATION-KIND", f"unsupported kind: {kind}")
    if not isinstance(artifact_id, str) or not artifact_id.startswith("sha256:") or not is_real_sha256(artifact_id):
        raise ValidationError("E-OBSERVATION-CAS", "invalid artifact id")
    return pathlib.Path(research_root) / "records" / kind / (artifact_id.removeprefix("sha256:") + ".json")


def put_record(research_root: pathlib.Path, kind: str, record: dict[str, Any]) -> str:
    validate_schema(kind, record)
    artifact_id = research_store(research_root).put(canonical_dumps(record))
    visible = (json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    write_immutable(record_path(research_root, kind, artifact_id), visible)
    return artifact_id


def publish_record(research_root: pathlib.Path, kind: str, record: dict[str, Any]) -> SealedRecord:
    artifact_id = put_record(research_root, kind, record)
    return SealedRecord(copy.deepcopy(record), artifact_id, record_path(research_root, kind, artifact_id))


def get_record(research_root: pathlib.Path, artifact_id: str) -> dict[str, Any]:
    if not isinstance(artifact_id, str) or not artifact_id.startswith("sha256:") or not is_real_sha256(artifact_id):
        raise ValidationError("E-OBSERVATION-CAS", "invalid artifact id")
    raw = research_store(research_root).get(artifact_id)
    try:
        value = json.loads(raw.decode("utf-8"))
        canonical = canonical_dumps(value)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValidationError("E-OBSERVATION-CAS", "artifact is not canonical JSON") from exc
    if not isinstance(value, dict) or raw != canonical:
        raise ValidationError("E-OBSERVATION-CAS", "artifact is not a canonical JSON object")
    return value


def load_record(research_root: pathlib.Path, artifact_id: str, validator: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    return validator(get_record(research_root, artifact_id))
