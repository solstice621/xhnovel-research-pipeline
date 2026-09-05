from __future__ import annotations

import copy
import json
import pathlib
from dataclasses import dataclass
from typing import Any, Iterable

from jsonschema import Draft202012Validator, SchemaError

from .canonical import canonical_dumps
from .errors import ValidationError
from .hashing import artifact_id_for, object_hash
from .paths import repo_root

PROFILE_MANIFEST_VERSION = "extraction-profile/v1"
PROFILE_SCHEMA_PATH = pathlib.Path("contracts/generic/extraction-profile-manifest.schema.json")
CORE_PROMPT_PATH = pathlib.Path("profiles/generic/core-prompt.md")
RECORD_MODE_OCCURRENCE = "OCCURRENCE"
RECORD_MODE_UNIQUE_PAYLOAD = "UNIQUE_PAYLOAD"
COMPLETION_STATUSES = ("COMPLETE", "OVERFLOW", "UNCERTAIN")
DEFAULT_ANSWER_ABI = {
    "record_mode": RECORD_MODE_OCCURRENCE,
    "completion_required": False,
}

@dataclass(frozen=True)
class ExtractionProfile:
    slug: str
    root: pathlib.Path
    manifest: dict[str, Any]
    manifest_bytes: bytes
    prompt_bytes: bytes
    payload_schema_bytes: bytes
    payload_schema: dict[str, Any]
    core_prompt_bytes: bytes
    package_hash: str
    extraction_profile_hash: str
    reduction_profile_hash: str
    manifest_artifact_id: str
    prompt_artifact_id: str
    payload_schema_artifact_id: str
    core_prompt_artifact_id: str

    @property
    def profile_id(self) -> str:
        return str(self.manifest["profile_id"])

    @property
    def profile_version(self) -> str:
        return str(self.manifest["profile_version"])

    @property
    def schema_name(self) -> str:
        return str(self.manifest["schema_name"])

    @property
    def unit_policy(self) -> dict[str, Any]:
        return copy.deepcopy(self.manifest["unit_policy"])

    @property
    def limits(self) -> dict[str, Any]:
        return copy.deepcopy(self.manifest["limits"])

    @property
    def evidence_policy(self) -> dict[str, Any]:
        return copy.deepcopy(self.manifest["evidence_policy"])

    @property
    def reduction(self) -> dict[str, Any]:
        return copy.deepcopy(self.manifest["reduction"])

    @property
    def answer_abi(self) -> dict[str, Any]:
        configured = self.manifest.get("answer_abi")
        if not isinstance(configured, dict):
            return copy.deepcopy(DEFAULT_ANSWER_ABI)
        return copy.deepcopy(configured)

    @property
    def instructions(self) -> str:
        return (
            self.core_prompt_bytes.decode("utf-8").rstrip()
            + "\n\n# Profile instructions\n\n"
            + self.prompt_bytes.decode("utf-8").strip()
            + "\n"
        )


def _load_json(path: pathlib.Path, *, label: str) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("E-PROFILE", f"invalid {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ValidationError("E-PROFILE", f"{label} must be a JSON object: {path}")
    return raw, value


def _resolve_member(profile_root: pathlib.Path, value: object, *, label: str) -> pathlib.Path:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("E-PROFILE-PATH", f"{label} must be a non-empty relative path")
    relative = pathlib.Path(value)
    if relative.is_absolute():
        raise ValidationError("E-PROFILE-PATH", f"{label} must remain inside the profile")
    resolved_root = profile_root.resolve()
    resolved = (profile_root / relative).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValidationError("E-PROFILE-PATH", f"{label} escapes the profile root") from exc
    if not resolved.is_file():
        raise ValidationError("E-PROFILE-PATH", f"missing {label}: {resolved}")
    return resolved


def _iter_refs(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str):
            yield ref
        for child in value.values():
            yield from _iter_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_refs(child)


def _validate_payload_schema(schema: dict[str, Any]) -> None:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValidationError("E-PROFILE-SCHEMA", "payload schema is not valid JSON Schema") from exc
    for ref in _iter_refs(schema):
        if not ref.startswith("#"):
            raise ValidationError(
                "E-PROFILE-SCHEMA-REF",
                f"payload schema reference must remain internal to the file: {ref!r}",
            )


def _validate_manifest(manifest: dict[str, Any], *, root: pathlib.Path) -> None:
    _, schema = _load_json(root / PROFILE_SCHEMA_PATH, label="profile manifest schema")
    errors = sorted(Draft202012Validator(schema).iter_errors(manifest), key=lambda error: list(error.path))
    if errors:
        first = errors[0]
        raise ValidationError(
            "E-PROFILE-MANIFEST",
            f"profile manifest: {first.message} at {list(first.path)}",
        )


def _file_entry(path: str, data: bytes) -> dict[str, str]:
    return {"path": path, "artifact_id": artifact_id_for(data)}



def profile_package_hash_from_assets(
    manifest_bytes: bytes,
    prompt_bytes: bytes,
    payload_schema_bytes: bytes,
) -> str:
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("E-PROFILE", "archived profile manifest is invalid") from exc
    if not isinstance(manifest, dict):
        raise ValidationError("E-PROFILE", "archived profile manifest must be an object")
    package_files = [
        _file_entry("profile.json", manifest_bytes),
        _file_entry(str(manifest.get("prompt", "")), prompt_bytes),
        _file_entry(str(manifest.get("payload_schema", "")), payload_schema_bytes),
    ]
    return object_hash(
        {
            "profile_id": manifest.get("profile_id"),
            "profile_version": manifest.get("profile_version"),
            "files": sorted(package_files, key=lambda item: item["path"]),
        },
        omit=(),
    )

def load_extraction_profile(
    profile_ref: str,
    *,
    root: pathlib.Path | None = None,
    profiles_root: pathlib.Path | None = None,
) -> ExtractionProfile:
    root = (root or repo_root()).resolve()
    base = (profiles_root or (root / "profiles" / "generic")).resolve()
    if not isinstance(profile_ref, str) or not profile_ref.strip():
        raise ValidationError("E-PROFILE", "profile reference must be non-empty")
    profile_root = (base / profile_ref).resolve()
    try:
        profile_root.relative_to(base)
    except ValueError as exc:
        raise ValidationError("E-PROFILE-PATH", "profile reference escapes the profile root") from exc
    if not profile_root.is_dir():
        raise ValidationError("E-PROFILE", f"unknown built-in profile {profile_ref!r}")

    manifest_bytes, manifest = _load_json(profile_root / "profile.json", label="profile manifest")
    _validate_manifest(manifest, root=root)
    if manifest["profile_manifest_version"] != PROFILE_MANIFEST_VERSION:
        raise ValidationError("E-PROFILE-MANIFEST", "unsupported profile manifest version")

    prompt_path = _resolve_member(profile_root, manifest["prompt"], label="prompt")
    payload_schema_path = _resolve_member(
        profile_root,
        manifest["payload_schema"],
        label="payload schema",
    )
    try:
        prompt_bytes = prompt_path.read_bytes()
        prompt_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValidationError("E-PROFILE", f"profile prompt must be UTF-8: {prompt_path}") from exc
    payload_schema_bytes, payload_schema = _load_json(
        payload_schema_path,
        label="payload schema",
    )
    _validate_payload_schema(payload_schema)

    core_prompt_path = root / CORE_PROMPT_PATH
    try:
        core_prompt_bytes = core_prompt_path.read_bytes()
        core_prompt_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValidationError("E-PROFILE", f"core prompt must be available and UTF-8: {core_prompt_path}") from exc

    package_hash = profile_package_hash_from_assets(
        manifest_bytes, prompt_bytes, payload_schema_bytes
    )
    extraction_body: dict[str, Any] = {
        "profile_id": manifest["profile_id"],
        "profile_version": manifest["profile_version"],
        "prompt_artifact_id": artifact_id_for(prompt_bytes),
        "payload_schema_artifact_id": artifact_id_for(payload_schema_bytes),
        "unit_policy": manifest["unit_policy"],
        "limits": manifest["limits"],
        "evidence_policy": manifest["evidence_policy"],
    }
    if "answer_abi" in manifest:
        extraction_body["answer_abi"] = manifest["answer_abi"]
    extraction_profile_hash = object_hash(extraction_body, omit=())
    reduction_profile_hash = object_hash(
        {
            "profile_id": manifest["profile_id"],
            "profile_version": manifest["profile_version"],
            "reduction": manifest["reduction"],
        },
        omit=(),
    )

    return ExtractionProfile(
        slug=profile_root.name,
        root=profile_root,
        manifest=copy.deepcopy(manifest),
        manifest_bytes=manifest_bytes,
        prompt_bytes=prompt_bytes,
        payload_schema_bytes=payload_schema_bytes,
        payload_schema=payload_schema,
        core_prompt_bytes=core_prompt_bytes,
        package_hash=package_hash,
        extraction_profile_hash=extraction_profile_hash,
        reduction_profile_hash=reduction_profile_hash,
        manifest_artifact_id=artifact_id_for(manifest_bytes),
        prompt_artifact_id=artifact_id_for(prompt_bytes),
        payload_schema_artifact_id=artifact_id_for(payload_schema_bytes),
        core_prompt_artifact_id=artifact_id_for(core_prompt_bytes),
    )


def _embedded_payload_schema(profile: ExtractionProfile) -> dict[str, Any]:
    """Return the payload schema in a provider-friendly embedded form."""

    schema = copy.deepcopy(profile.payload_schema)
    schema.pop("$schema", None)
    schema.pop("$id", None)
    return schema


def output_schema_for(profile: ExtractionProfile) -> dict[str, Any]:
    maximum = int(profile.limits["max_records_per_unit"])
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["records"],
        "properties": {
            "records": {
                "type": "array",
                "maxItems": maximum,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["payload", "evidence_bindings"],
                    "properties": {
                        "payload": _embedded_payload_schema(profile),
                        "evidence_bindings": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["paths", "source_spans"],
                                "properties": {
                                    "paths": {
                                        "type": "array",
                                        "minItems": 1,
                                        "uniqueItems": True,
                                        "items": {"type": "string"},
                                    },
                                    "source_spans": {
                                        "type": "array",
                                        "minItems": 1,
                                        "uniqueItems": True,
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": ["segment_id", "start", "end"],
                                            "properties": {
                                                "segment_id": {"type": "string", "minLength": 1},
                                                "start": {"type": "integer", "minimum": 0},
                                                "end": {"type": "integer", "minimum": 1},
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            }
        },
    }
    if profile.answer_abi.get("completion_required"):
        schema["required"] = ["records", "completion"]
        schema["properties"]["completion"] = {
            "type": "object",
            "additionalProperties": False,
            "required": ["status"],
            "properties": {
                "status": {"enum": list(COMPLETION_STATUSES)},
            },
        }
    return schema



def extraction_assets(profile: ExtractionProfile) -> list[tuple[str, bytes, str]]:
    """Return only assets that can change model extraction semantics."""

    return [
        ("profile-prompt", profile.prompt_bytes, profile.prompt_artifact_id),
        ("payload-schema", profile.payload_schema_bytes, profile.payload_schema_artifact_id),
        ("core-prompt", profile.core_prompt_bytes, profile.core_prompt_artifact_id),
    ]


def profile_assets(profile: ExtractionProfile) -> list[tuple[str, bytes, str]]:
    """Return trusted profile/core assets with their expected artifact identities."""

    return [
        ("profile-manifest", profile.manifest_bytes, profile.manifest_artifact_id),
        ("profile-prompt", profile.prompt_bytes, profile.prompt_artifact_id),
        ("payload-schema", profile.payload_schema_bytes, profile.payload_schema_artifact_id),
        ("core-prompt", profile.core_prompt_bytes, profile.core_prompt_artifact_id),
    ]
