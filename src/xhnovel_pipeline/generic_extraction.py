from __future__ import annotations

import copy
import json
import os
import pathlib
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Protocol

from jsonschema import Draft202012Validator

from .canonical import canonical_dumps
from .errors import PipelineError, ValidationError
from .generic_agent_files import (
    GENERIC_AGENT_FILES_EXECUTOR_KIND,
    GenericAgentResponsePending,
    GenericAgentResponsesPending,
    PendingGenericAnswer,
    generic_agent_task_bytes,
)
from .generic_profile import (
    ExtractionProfile,
    load_extraction_profile,
    extraction_assets,
    output_schema_for,
    profile_assets,
    profile_package_hash_from_assets,
)
from .generic_reducers import (
    reduce_observations,
    reducer_implementation_hash,
)
from .hashing import artifact_id_for, object_hash, sha256_bytes
from .model_api import (
    API_EXECUTOR_KIND,
    ModelAttemptTrace,
    ModelCallError,
    ModelCallResult,
    OPENAI_RESPONSES_FORMAT,
)
from .novel_assessment import declared_rights, declared_source_quality, source_quality_tier
from .novel_ingest import run_novel_ingestion, validate_novel_ingestion
from .paths import repo_root
from .runtime import repository_commit
from .store import ArtifactStore

GENERIC_CORE_VERSION = "generic-extraction/v0.1"
GENERIC_ALLOWED_USE = "source-grounded-semantic-extraction/v0-spike"
CHECKPOINT_VERSION = "generic-extraction-checkpoint/v1"
CHECKPOINT_INTEGRITY_FIELD = "integrity_hash"

_SCHEMA_FILES = {
    "NovelTextSnapshot": "novel-text-snapshot.schema.json",
    "ExtractionBuild": "extraction-build.schema.json",
    "ExtractionRun": "extraction-run.schema.json",
    "ReductionRun": "reduction-run.schema.json",
    "CorpusSnapshot": "corpus-snapshot.schema.json",
    "ModelAttemptV2": "model-attempt-v2.schema.json",
}


class StructuredExecutor(Protocol):
    model: str
    endpoint: str
    timeout: float
    max_attempts: int
    executor_kind: str
    response_format: str
    executor_build_id: str

    def json_request_bytes(
        self,
        *,
        instructions: str,
        input_value: dict[str, Any],
        schema_name: str,
        schema: dict[str, Any],
    ) -> bytes: ...

    def generate_json(
        self,
        *,
        instructions: str,
        input_value: dict[str, Any],
        schema_name: str,
        schema: dict[str, Any],
    ) -> ModelCallResult: ...


@dataclass(frozen=True)
class GenericRunPaths:
    shared_root: pathlib.Path
    snapshot_path: pathlib.Path
    profile_root: pathlib.Path
    extraction_root: pathlib.Path
    extraction_build_path: pathlib.Path
    extraction_run_path: pathlib.Path
    units_path: pathlib.Path
    unit_results_path: pathlib.Path
    attempts_path: pathlib.Path
    observations_path: pathlib.Path
    checkpoint_path: pathlib.Path


@dataclass(frozen=True)
class GenericExtractionResult:
    catalog: Any
    store: ArtifactStore
    ingestion: dict[str, Any]
    snapshot: dict[str, Any]
    profile: ExtractionProfile
    build: dict[str, Any]
    run: dict[str, Any]
    units: list[dict[str, Any]]
    unit_results: list[dict[str, Any]]
    attempts: list[dict[str, Any]]
    observations: list[dict[str, Any]]
    paths: GenericRunPaths
    reused_extraction: bool


@dataclass(frozen=True)
class GenericCorpusResult:
    extraction: GenericExtractionResult
    reduction_run: dict[str, Any]
    corpus_snapshot: dict[str, Any]
    corpus_records: list[dict[str, Any]]
    reduction_root: pathlib.Path
    reduction_run_path: pathlib.Path
    corpus_path: pathlib.Path
    corpus_snapshot_path: pathlib.Path


class GenericExtractionPartial(PipelineError):
    def __init__(self, failed: dict[str, dict[str, Any]], checkpoint_path: pathlib.Path) -> None:
        self.failed = copy.deepcopy(failed)
        self.checkpoint_path = checkpoint_path
        super().__init__(
            "E-GENERIC-EXTRACTION-PARTIAL",
            f"{len(failed)} extraction unit(s) failed; checkpoint retained at {checkpoint_path}",
        )


def _generic_id(prefix: str, payload: dict[str, Any], *, length: int = 20) -> str:
    digest = object_hash(payload, omit=()).removeprefix("sha256:")
    return f"{prefix}{digest[:length].upper()}"


def _canonical_json_bytes(value: Any) -> bytes:
    return canonical_dumps(value) + b"\n"


def _canonical_jsonl_bytes(records: list[dict[str, Any]]) -> bytes:
    return b"".join(canonical_dumps(record) + b"\n" for record in records)


def _parse_canonical_json_bytes(data: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("E-GENERIC-JSON", f"{label} is not UTF-8 JSON") from exc
    if not isinstance(value, dict) or _canonical_json_bytes(value) != data:
        raise ValidationError("E-GENERIC-JSON", f"{label} is not canonical JSON")
    return value


def _parse_canonical_jsonl_bytes(data: bytes, *, label: str) -> list[dict[str, Any]]:
    if not data:
        return []
    records: list[dict[str, Any]] = []
    for ordinal, line in enumerate(data.splitlines(keepends=True), start=1):
        if not line.endswith(b"\n"):
            raise ValidationError("E-GENERIC-JSONL", f"{label} line {ordinal} lacks newline")
        try:
            value = json.loads(line[:-1].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("E-GENERIC-JSONL", f"{label} line {ordinal} is invalid") from exc
        if not isinstance(value, dict) or canonical_dumps(value) + b"\n" != line:
            raise ValidationError("E-GENERIC-JSONL", f"{label} line {ordinal} is not canonical")
        records.append(value)
    return records


def _logical_result_hash(records: list[dict[str, Any]], hash_field: str) -> str:
    hashes = [record[hash_field] for record in records]
    return object_hash({"record_hashes": hashes}, omit=())


def _atomic_write(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _write_immutable(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(data)
    except FileExistsError:
        if not path.is_file() or path.read_bytes() != data:
            raise ValidationError("E-IMMUTABLE-OUTPUT", f"refusing to overwrite {path}")


def _schema_path(root: pathlib.Path, kind: str) -> pathlib.Path:
    return root / "contracts" / "generic" / _SCHEMA_FILES[kind]


def _validate_generic_schema(kind: str, value: dict[str, Any], *, root: pathlib.Path) -> None:
    path = _schema_path(root, kind)
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("E-GENERIC-SCHEMA", f"cannot load schema for {kind}") from exc
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda error: list(error.path))
    if errors:
        first = errors[0]
        raise ValidationError(
            "E-GENERIC-SCHEMA",
            f"{kind}: {first.message} at {list(first.path)}",
        )


def _put_expected(store: ArtifactStore, data: bytes, expected_id: str, *, label: str) -> str:
    actual = store.put(data)
    if actual != expected_id:
        raise ValidationError("E-GENERIC-HASH", f"{label} artifact identity differs")
    return actual


def _artifact_and_write(store: ArtifactStore, path: pathlib.Path, data: bytes) -> str:
    artifact_id = store.put(data)
    _write_immutable(path, data)
    return artifact_id


def _checkpoint_hash(checkpoint: dict[str, Any]) -> str:
    return object_hash(checkpoint, omit=(CHECKPOINT_INTEGRITY_FIELD,))


def _write_checkpoint(path: pathlib.Path, checkpoint: dict[str, Any]) -> None:
    body = copy.deepcopy(checkpoint)
    body[CHECKPOINT_INTEGRITY_FIELD] = _checkpoint_hash(body)
    _atomic_write(path, _canonical_json_bytes(body))


def _load_checkpoint(path: pathlib.Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    checkpoint = _parse_canonical_json_bytes(path.read_bytes(), label="generic checkpoint")
    integrity_hash = checkpoint.get(CHECKPOINT_INTEGRITY_FIELD)
    if not isinstance(integrity_hash, str) or integrity_hash != _checkpoint_hash(checkpoint):
        raise ValidationError("E-GENERIC-CHECKPOINT-INTEGRITY", "checkpoint hash differs")
    return checkpoint


def _source_spec_from_ingestion(
    ingestion: dict[str, Any],
    store: ArtifactStore,
) -> dict[str, Any]:
    raw = store.get(ingestion["input_spec_artifact_id"])
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("E-GENERIC-LINEAGE", "stored ingestion spec is invalid") from exc
    if not isinstance(value, dict) or object_hash(value, omit=()) != ingestion["input_spec_hash"]:
        raise ValidationError("E-GENERIC-LINEAGE", "stored ingestion spec hash differs")
    return value


def build_novel_text_snapshot(
    catalog: Any,
    store: ArtifactStore,
    ingestion: dict[str, Any],
    spec: dict[str, Any],
    *,
    root: pathlib.Path,
) -> dict[str, Any]:
    validate_novel_ingestion(catalog, store)
    stored_spec = _source_spec_from_ingestion(ingestion, store)
    if object_hash(spec, omit=()) != ingestion["input_spec_hash"] or stored_spec != spec:
        raise ValidationError("E-GENERIC-LINEAGE", "runtime spec differs from frozen ingestion spec")
    declared_rights(spec, require_storage=True, require_external_model=True)
    quality = declared_source_quality(spec)
    tier = source_quality_tier(quality)
    if tier not in {"A", "B"}:
        raise ValidationError(
            "E-GENERIC-SOURCE-QUALITY",
            "generic semantic extraction requires Tier A or Tier B source quality",
        )

    chapter_ids = list(ingestion["ready_chapter_ids"])
    if not chapter_ids:
        raise ValidationError("E-GENERIC-EMPTY", "ingestion has no eligible narrative chapters")
    chapters = [catalog.get("NovelChapter", chapter_id) for chapter_id in chapter_ids]
    if any(chapter["work_id"] != ingestion["work_id"] for chapter in chapters):
        raise ValidationError("E-GENERIC-LINEAGE", "chapter belongs to another work")
    document_ids = list(dict.fromkeys(chapter["document_id"] for chapter in chapters))
    segment_ids = [segment_id for chapter in chapters for segment_id in chapter["segment_ids"]]
    segments = [catalog.get("Segment", segment_id) for segment_id in segment_ids]
    if any(segment["document_id"] not in set(document_ids) for segment in segments):
        raise ValidationError("E-GENERIC-LINEAGE", "segment belongs to another document")
    eligible_characters = sum(len(segment["normalized_text"]) for segment in segments)
    if eligible_characters < 1:
        raise ValidationError("E-GENERIC-EMPTY", "eligible normalized text is empty")

    body = {
        "schema_version": "novel-text-snapshot/v1",
        "record_kind": "NOVEL_TEXT_SNAPSHOT",
        "work_id": ingestion["work_id"],
        "ingestion_run_id": ingestion["ingestion_run_id"],
        "input_spec_artifact_id": ingestion["input_spec_artifact_id"],
        "input_spec_hash": ingestion["input_spec_hash"],
        "chapter_ids": chapter_ids,
        "document_ids": document_ids,
        "segment_ids": segment_ids,
        "source_quality_tier": tier,
        "coverage_use": GENERIC_ALLOWED_USE,
        "eligible_character_count": eligible_characters,
        "created_at": ingestion["started_at"],
        "status": "FROZEN",
    }
    snapshot_hash = object_hash(body, omit=())
    snapshot = {
        **body,
        "text_snapshot_id": _generic_id("NTS-", {"text_snapshot_hash": snapshot_hash}),
        "text_snapshot_hash": snapshot_hash,
    }
    _validate_generic_schema("NovelTextSnapshot", snapshot, root=root)
    return snapshot


def validate_novel_text_snapshot(
    snapshot: dict[str, Any],
    catalog: Any,
    store: ArtifactStore,
    ingestion: dict[str, Any],
    spec: dict[str, Any],
    *,
    root: pathlib.Path,
) -> None:
    _validate_generic_schema("NovelTextSnapshot", snapshot, root=root)
    expected = build_novel_text_snapshot(catalog, store, ingestion, spec, root=root)
    if snapshot != expected:
        raise ValidationError("E-GENERIC-SNAPSHOT", "NovelTextSnapshot differs from ingestion replay")


def _unit_policy_hash(profile: ExtractionProfile) -> str:
    return object_hash(profile.unit_policy, omit=())


def build_extraction_units(
    snapshot: dict[str, Any],
    catalog: Any,
    profile: ExtractionProfile,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    policy = profile.unit_policy
    if policy.get("id") != "sliding-text/v1":
        raise ValidationError("E-GENERIC-UNIT-POLICY", "unsupported unit policy")
    target = policy.get("target_chars")
    overlap = policy.get("overlap_chars")
    if (
        not isinstance(target, int)
        or isinstance(target, bool)
        or target < 1
        or not isinstance(overlap, int)
        or isinstance(overlap, bool)
        or overlap < 0
        or overlap >= target
    ):
        raise ValidationError("E-GENERIC-UNIT-POLICY", "invalid sliding-text parameters")

    segment_ids = list(snapshot["segment_ids"])
    segments = [catalog.get("Segment", segment_id) for segment_id in segment_ids]
    virtual_ranges: list[tuple[int, int, dict[str, Any]]] = []
    cursor = 0
    for segment in segments:
        length = len(segment["normalized_text"])
        if length:
            virtual_ranges.append((cursor, cursor + length, segment))
            cursor += length
    total_length = cursor
    if total_length != snapshot["eligible_character_count"]:
        raise ValidationError("E-GENERIC-UNIT-COVERAGE", "snapshot character count differs")

    ranges: list[tuple[int, int]] = []
    start = 0
    while start < total_length:
        end = min(total_length, start + target)
        ranges.append((start, end))
        if end == total_length:
            break
        start = end - overlap

    unit_policy_hash = _unit_policy_hash(profile)
    units: list[dict[str, Any]] = []
    for ordinal, (window_start, window_end) in enumerate(ranges, start=1):
        spans: list[dict[str, Any]] = []
        for segment_start, segment_end, segment in virtual_ranges:
            if segment_end <= window_start:
                continue
            if segment_start >= window_end:
                break
            local_start = max(window_start, segment_start) - segment_start
            local_end = min(window_end, segment_end) - segment_start
            spans.append(
                {
                    "segment_id": segment["segment_id"],
                    "start": local_start,
                    "end": local_end,
                    "normalized_text_hash": segment["normalized_text_hash"],
                }
            )
        base = {
            "schema_version": "extraction-unit/v1",
            "text_snapshot_id": snapshot["text_snapshot_id"],
            "unit_policy_id": policy["id"],
            "unit_policy_hash": unit_policy_hash,
            "ordinal": ordinal,
            "source_spans": spans,
            "text_length": sum(span["end"] - span["start"] for span in spans),
        }
        unit_hash = object_hash(base, omit=())
        units.append(
            {
                **base,
                "unit_id": _generic_id("XUNIT-", {"unit_hash": unit_hash}),
                "unit_hash": unit_hash,
            }
        )

    _validate_unit_coverage(snapshot, catalog, units)
    coverage = {
        "text_coverage": "FULL",
        "semantic_coverage": "UNMEASURED",
        "eligible_character_count": total_length,
        "covered_character_count": total_length,
        "uncovered_ranges": [],
    }
    return units, coverage


def _validate_unit_coverage(
    snapshot: dict[str, Any],
    catalog: Any,
    units: list[dict[str, Any]],
) -> None:
    if not units or [unit["ordinal"] for unit in units] != list(range(1, len(units) + 1)):
        raise ValidationError("E-GENERIC-UNIT-COVERAGE", "unit ordinals are incomplete")
    allowed_segments = set(snapshot["segment_ids"])
    coverage_by_segment: dict[str, list[tuple[int, int]]] = {
        segment_id: [] for segment_id in snapshot["segment_ids"]
    }
    seen_ids: set[str] = set()
    for unit in units:
        if unit["unit_id"] in seen_ids:
            raise ValidationError("E-GENERIC-UNIT", "duplicate unit id")
        seen_ids.add(unit["unit_id"])
        identity = {
            key: unit[key]
            for key in (
                "schema_version",
                "text_snapshot_id",
                "unit_policy_id",
                "unit_policy_hash",
                "ordinal",
                "source_spans",
                "text_length",
            )
        }
        expected_hash = object_hash(identity, omit=())
        if (
            unit["text_snapshot_id"] != snapshot["text_snapshot_id"]
            or unit["unit_hash"] != expected_hash
            or unit["unit_id"] != _generic_id("XUNIT-", {"unit_hash": expected_hash})
        ):
            raise ValidationError("E-GENERIC-UNIT", "unit identity differs")
        actual_length = 0
        for span in unit["source_spans"]:
            segment_id = span["segment_id"]
            if segment_id not in allowed_segments:
                raise ValidationError("E-GENERIC-SPAN", "unit references segment outside snapshot")
            segment = catalog.get("Segment", segment_id)
            if (
                span["normalized_text_hash"] != segment["normalized_text_hash"]
                or not 0 <= span["start"] < span["end"] <= len(segment["normalized_text"])
            ):
                raise ValidationError("E-GENERIC-SPAN", "unit span is invalid")
            coverage_by_segment[segment_id].append((span["start"], span["end"]))
            actual_length += span["end"] - span["start"]
        if actual_length != unit["text_length"]:
            raise ValidationError("E-GENERIC-UNIT", "unit text length differs")

    for segment_id in snapshot["segment_ids"]:
        segment = catalog.get("Segment", segment_id)
        length = len(segment["normalized_text"])
        if not length:
            continue
        intervals = sorted(coverage_by_segment[segment_id])
        if not intervals or intervals[0][0] != 0:
            raise ValidationError("E-GENERIC-UNIT-COVERAGE", f"{segment_id} has uncovered prefix")
        end = intervals[0][1]
        for start, next_end in intervals[1:]:
            if start > end:
                raise ValidationError("E-GENERIC-UNIT-COVERAGE", f"{segment_id} has uncovered gap")
            end = max(end, next_end)
        if end != length:
            raise ValidationError("E-GENERIC-UNIT-COVERAGE", f"{segment_id} has uncovered suffix")


def _generic_engine_source_hash(root: pathlib.Path) -> str:
    source_root = root / "src" / "xhnovel_pipeline"
    if not source_root.is_dir():
        source_root = pathlib.Path(__file__).resolve().parent
    names = (
        "canonical.py",
        "generic_agent_files.py",
        "generic_extraction.py",
        "generic_profile.py",
        "hashing.py",
        "model_api.py",
        "novel_assessment.py",
        "novel_ingest.py",
        "parse.py",
        "store.py",
    )
    files: list[dict[str, str]] = []
    for name in names:
        path = source_root / name
        if not path.is_file():
            raise ValidationError("E-GENERIC-BUILD", f"missing engine source {name}")
        files.append({"path": name, "sha256": sha256_bytes(path.read_bytes())})
    return object_hash({"engine_sources": files}, omit=())


def generic_engine_source_hash(root: pathlib.Path | None = None) -> str:
    return _generic_engine_source_hash((root or repo_root()).resolve())


def _executor_descriptor(executor: StructuredExecutor) -> dict[str, Any]:
    timeout = getattr(executor, "timeout", 0.0)
    timeout_ms = int(round(float(timeout) * 1000))
    return {
        "kind": str(executor.executor_kind),
        "build_id": str(executor.executor_build_id),
        "model": str(executor.model),
        "response_format": str(executor.response_format),
        "endpoint": str(executor.endpoint),
        "timeout_ms": timeout_ms,
        "max_attempts": int(executor.max_attempts),
    }


def build_extraction_build(
    profile: ExtractionProfile,
    executor: StructuredExecutor,
    store: ArtifactStore,
    *,
    root: pathlib.Path,
    created_at: str,
) -> dict[str, Any]:
    for label, data, expected_id in profile_assets(profile):
        _put_expected(store, data, expected_id, label=label)
    identity = {
        "schema_version": "extraction-build/v1",
        "engine_repository_commit": repository_commit(root),
        "engine_source_hash": _generic_engine_source_hash(root),
        "profile_id": profile.profile_id,
        "profile_version": profile.profile_version,
        "extraction_profile_hash": profile.extraction_profile_hash,
        "core_prompt_artifact_id": profile.core_prompt_artifact_id,
        "profile_prompt_artifact_id": profile.prompt_artifact_id,
        "payload_schema_artifact_id": profile.payload_schema_artifact_id,
        "unit_policy": profile.unit_policy,
        "executor": _executor_descriptor(executor),
        "status": "UNQUALIFIED",
    }
    build_hash = object_hash(identity, omit=())
    build = {
        **identity,
        "extraction_build_id": _generic_id("XBLD-", {"extraction_build_hash": build_hash}),
        "extraction_build_hash": build_hash,
        "created_at": created_at,
    }
    _validate_generic_schema("ExtractionBuild", build, root=root)
    return build


def _task_input(
    snapshot: dict[str, Any],
    unit: dict[str, Any],
    profile: ExtractionProfile,
    catalog: Any,
) -> dict[str, Any]:
    spans: list[dict[str, Any]] = []
    for span in unit["source_spans"]:
        segment = catalog.get("Segment", span["segment_id"])
        spans.append(
            {
                **span,
                "untrusted_text": segment["normalized_text"][span["start"] : span["end"]],
            }
        )
    return {
        "profile": {
            "profile_id": profile.profile_id,
            "profile_version": profile.profile_version,
            "extraction_profile_hash": profile.extraction_profile_hash,
            "evidence_policy": profile.evidence_policy,
        },
        "text_snapshot": {
            "text_snapshot_id": snapshot["text_snapshot_id"],
            "work_id": snapshot["work_id"],
        },
        "unit": {
            "unit_id": unit["unit_id"],
            "ordinal": unit["ordinal"],
            "source_spans": spans,
        },
    }


def _task_bytes(
    *,
    instructions: str,
    input_value: dict[str, Any],
    schema_name: str,
    schema: dict[str, Any],
) -> bytes:
    return canonical_dumps(
        {
            "instructions": instructions,
            "input": input_value,
            "schema_name": schema_name,
            "schema": schema,
        }
    )


def _api_request_bytes(
    *,
    model: str,
    instructions: str,
    input_value: dict[str, Any],
    schema_name: str,
    schema: dict[str, Any],
) -> bytes:
    return canonical_dumps(
        {
            "model": model,
            "instructions": instructions,
            "input": canonical_dumps(input_value).decode("utf-8"),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
            "store": False,
        }
    )


def _rebuild_executor_request(
    build: dict[str, Any],
    *,
    instructions: str,
    input_value: dict[str, Any],
    schema_name: str,
    schema: dict[str, Any],
) -> bytes:
    descriptor = build["executor"]
    if descriptor["kind"] == API_EXECUTOR_KIND and descriptor["response_format"] == OPENAI_RESPONSES_FORMAT:
        return _api_request_bytes(
            model=descriptor["model"],
            instructions=instructions,
            input_value=input_value,
            schema_name=schema_name,
            schema=schema,
        )
    if descriptor["kind"] == GENERIC_AGENT_FILES_EXECUTOR_KIND:
        return generic_agent_task_bytes(
            instructions=instructions,
            input_value=input_value,
            schema_name=schema_name,
            schema=schema,
        )
    raise ValidationError(
        "E-GENERIC-REPLAY-RUNTIME",
        f"no exact request rebuilder for executor {descriptor['kind']!r}",
    )


def _decode_pointer_token(token: str) -> str:
    result = ""
    index = 0
    while index < len(token):
        char = token[index]
        if char != "~":
            result += char
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
            raise ValidationError("E-GENERIC-EVIDENCE-PATH", f"invalid JSON Pointer token {token!r}")
        result += "~" if token[index + 1] == "0" else "/"
        index += 2
    return result


def _resolve_pointer(value: Any, pointer: str) -> Any:
    if pointer == "":
        return value
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValidationError("E-GENERIC-EVIDENCE-PATH", f"invalid JSON Pointer {pointer!r}")
    current = value
    for raw_token in pointer[1:].split("/"):
        token = _decode_pointer_token(raw_token)
        if isinstance(current, dict):
            if token not in current:
                raise ValidationError("E-GENERIC-EVIDENCE-PATH", f"pointer does not exist: {pointer}")
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
                raise ValidationError("E-GENERIC-EVIDENCE-PATH", f"invalid array pointer: {pointer}")
            index = int(token)
            if index >= len(current):
                raise ValidationError("E-GENERIC-EVIDENCE-PATH", f"pointer does not exist: {pointer}")
            current = current[index]
        else:
            raise ValidationError("E-GENERIC-EVIDENCE-PATH", f"pointer crosses a scalar: {pointer}")
    return current


def _pointer_for_key(key: str) -> str:
    return "/" + key.replace("~", "~0").replace("/", "~1")


def _binding_path_covers(binding_path: str, required_path: str) -> bool:
    return binding_path == "" or binding_path == required_path or required_path.startswith(binding_path + "/")


def _span_within_unit(
    raw_span: dict[str, Any],
    unit: dict[str, Any],
    catalog: Any,
) -> dict[str, Any]:
    segment_id = raw_span["segment_id"]
    start = raw_span["start"]
    end = raw_span["end"]
    if not isinstance(start, int) or isinstance(start, bool) or not isinstance(end, int) or isinstance(end, bool):
        raise ValidationError("E-GENERIC-SPAN", "source span offsets must be integers")
    matches = [
        span
        for span in unit["source_spans"]
        if span["segment_id"] == segment_id and span["start"] <= start < end <= span["end"]
    ]
    if not matches:
        raise ValidationError("E-GENERIC-SPAN", "source span is outside the extraction unit")
    segment = catalog.get("Segment", segment_id)
    expected_hash = matches[0]["normalized_text_hash"]
    if expected_hash != segment["normalized_text_hash"]:
        raise ValidationError("E-GENERIC-SPAN", "source span text hash differs")
    return {
        "segment_id": segment_id,
        "start": start,
        "end": end,
        "normalized_text_hash": expected_hash,
    }


def _observation_from_raw(
    raw: dict[str, Any],
    *,
    snapshot: dict[str, Any],
    unit: dict[str, Any],
    build: dict[str, Any],
    profile: ExtractionProfile,
    catalog: Any,
) -> dict[str, Any]:
    payload = raw["payload"]
    payload_bytes = canonical_dumps(payload)
    if len(payload_bytes) > int(profile.limits["max_payload_bytes_per_record"]):
        raise ValidationError("E-GENERIC-PAYLOAD-SIZE", "payload exceeds profile limit")
    kind = payload.get("kind")
    policy = profile.evidence_policy["by_kind"].get(kind)
    if not isinstance(policy, dict):
        raise ValidationError("E-GENERIC-EVIDENCE-POLICY", f"no evidence policy for kind {kind!r}")

    segment_order: dict[str, int] = {}
    for ordinal, span in enumerate(unit["source_spans"]):
        segment_order.setdefault(span["segment_id"], ordinal)
    bindings: list[dict[str, Any]] = []
    all_binding_paths: list[list[str]] = []
    all_spans: dict[tuple[str, int, int, str], dict[str, Any]] = {}
    for binding in raw["evidence_bindings"]:
        paths = sorted(set(binding["paths"]))
        for pointer in paths:
            _resolve_pointer(payload, pointer)
        spans_by_key: dict[tuple[str, int, int, str], dict[str, Any]] = {}
        for raw_span in binding["source_spans"]:
            span = _span_within_unit(raw_span, unit, catalog)
            key = (
                span["segment_id"],
                span["start"],
                span["end"],
                span["normalized_text_hash"],
            )
            spans_by_key[key] = span
            all_spans[key] = span
        spans = sorted(
            spans_by_key.values(),
            key=lambda span: (
                segment_order.get(span["segment_id"], 10**9),
                span["start"],
                span["end"],
                span["segment_id"],
            ),
        )
        bindings.append({"paths": paths, "source_spans": spans})
        all_binding_paths.append(paths)

    required_groups = policy["required_groups"]
    for group in required_groups:
        if not any(
            all(
                any(_binding_path_covers(binding_path, required_path) for binding_path in paths)
                for required_path in group
            )
            for paths in all_binding_paths
        ):
            raise ValidationError(
                "E-GENERIC-EVIDENCE-MISSING",
                f"payload kind {kind} lacks one required evidence group: {group}",
            )

    exempt_paths = set(policy["exempt_paths"])
    for key in payload:
        path = _pointer_for_key(key)
        if path in exempt_paths:
            continue
        if not any(
            _binding_path_covers(binding_path, path)
            for paths in all_binding_paths
            for binding_path in paths
        ):
            raise ValidationError(
                "E-GENERIC-EVIDENCE-MISSING",
                f"payload field {path} has no evidence binding",
            )

    bindings_by_bytes = {canonical_dumps(binding): binding for binding in bindings}
    bindings = [bindings_by_bytes[key] for key in sorted(bindings_by_bytes)]
    source_spans = sorted(
        all_spans.values(),
        key=lambda span: (
            segment_order.get(span["segment_id"], 10**9),
            span["start"],
            span["end"],
            span["segment_id"],
        ),
    )
    base = {
        "schema_version": "local-observation/v1",
        "text_snapshot_id": snapshot["text_snapshot_id"],
        "work_id": snapshot["work_id"],
        "unit_id": unit["unit_id"],
        "extraction_build_id": build["extraction_build_id"],
        "profile_id": profile.profile_id,
        "profile_version": profile.profile_version,
        "extraction_profile_hash": profile.extraction_profile_hash,
        "payload_schema_artifact_id": profile.payload_schema_artifact_id,
        "payload": copy.deepcopy(payload),
        "evidence_bindings": bindings,
        "source_spans": source_spans,
        "status": "DRAFT",
        "verification": "UNVERIFIED",
    }
    observation_hash = object_hash(base, omit=())
    return {
        **base,
        "observation_id": _generic_id("OBS-", {"observation_hash": observation_hash}),
        "observation_hash": observation_hash,
    }


def validate_model_output(
    value: dict[str, Any],
    *,
    snapshot: dict[str, Any],
    unit: dict[str, Any],
    build: dict[str, Any],
    profile: ExtractionProfile,
    catalog: Any,
) -> list[dict[str, Any]]:
    schema = output_schema_for(profile)
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda error: list(error.path))
    if errors:
        first = errors[0]
        raise ValidationError(
            "E-GENERIC-MODEL-SCHEMA",
            f"model output: {first.message} at {list(first.path)}",
        )
    observations_by_id: dict[str, dict[str, Any]] = {}
    for raw in value["records"]:
        observation = _observation_from_raw(
            raw,
            snapshot=snapshot,
            unit=unit,
            build=build,
            profile=profile,
            catalog=catalog,
        )
        observations_by_id[observation["observation_id"]] = observation
    return sorted(observations_by_id.values(), key=lambda item: item["observation_hash"])


def _attempt_record(
    *,
    unit_id: str,
    extraction_build_id: str,
    task_artifact_id: str,
    request_artifact_id: str,
    trace: ModelAttemptTrace,
    attempt_ordinal: int,
    response_artifact_id: str | None,
    status: str | None,
    error_code: str | None,
    error_message: str | None,
    retry_of: str | None,
    recorded_at: str,
) -> dict[str, Any]:
    body = {
        "schema_version": "model-attempt/v2",
        "operation": "PROFILE_EXTRACTION",
        "subject_kind": "ExtractionUnit",
        "subject_id": unit_id,
        "extraction_build_id": extraction_build_id,
        "task_artifact_id": task_artifact_id,
        "request_artifact_id": request_artifact_id,
        "response_artifact_id": response_artifact_id,
        "attempt_ordinal": attempt_ordinal,
        "status": status or trace.status,
        "http_status": trace.http_status,
        "error_code": error_code if error_code is not None else trace.error_code,
        "error_message": error_message if error_message is not None else trace.error_message,
        "provider_response_id": trace.response_id,
        "usage": {
            "input_tokens": trace.usage.get("input_tokens"),
            "output_tokens": trace.usage.get("output_tokens"),
            "total_tokens": trace.usage.get("total_tokens"),
            "estimated_cost_microusd": None,
        },
        "retry_of": retry_of,
        "recorded_at": recorded_at,
    }
    attempt_id = _generic_id("MAT2-", body)
    return {**body, "attempt_id": attempt_id}


def _attempt_records_from_traces(
    *,
    unit_id: str,
    build: dict[str, Any],
    task_artifact_id: str,
    request_artifact_id: str,
    traces: tuple[ModelAttemptTrace, ...],
    store: ArtifactStore,
    prior_attempts: list[dict[str, Any]],
    recorded_at: str,
    final_status_override: str | None = None,
    final_error_code: str | None = None,
    final_error_message: str | None = None,
) -> list[dict[str, Any]]:
    records = copy.deepcopy(prior_attempts)
    if [trace.ordinal for trace in traces] != list(range(1, len(traces) + 1)):
        raise ValidationError(
            "E-GENERIC-ATTEMPT",
            "executor attempt ordinals must restart at one and be consecutive",
        )
    retry_of = records[-1]["attempt_id"] if records else None
    offset = len(records)
    for index, trace in enumerate(traces, start=1):
        response_artifact_id = store.put(trace.response_bytes) if trace.response_bytes else None
        is_last = index == len(traces)
        record = _attempt_record(
            unit_id=unit_id,
            extraction_build_id=build["extraction_build_id"],
            task_artifact_id=task_artifact_id,
            request_artifact_id=request_artifact_id,
            trace=trace,
            attempt_ordinal=offset + index,
            response_artifact_id=response_artifact_id,
            status=final_status_override if is_last else None,
            error_code=final_error_code if is_last else None,
            error_message=final_error_message if is_last else None,
            retry_of=retry_of,
            recorded_at=recorded_at,
        )
        records.append(record)
        retry_of = record["attempt_id"]
    return records


def _unit_result_record(
    *,
    unit: dict[str, Any],
    task_artifact_id: str,
    request_artifact_id: str,
    provider_response_artifact_id: str | None,
    output_artifact_id: str,
    attempt_ids: list[str],
    observation_ids: list[str],
) -> dict[str, Any]:
    body = {
        "schema_version": "extraction-unit-result/v1",
        "unit_id": unit["unit_id"],
        "task_artifact_id": task_artifact_id,
        "request_artifact_id": request_artifact_id,
        "provider_response_artifact_id": provider_response_artifact_id,
        "output_artifact_id": output_artifact_id,
        "attempt_ids": attempt_ids,
        "observation_ids": observation_ids,
        "status": "SUCCEEDED",
    }
    return {**body, "result_hash": object_hash(body, omit=())}


def _execute_unit(
    *,
    unit: dict[str, Any],
    snapshot: dict[str, Any],
    build: dict[str, Any],
    profile: ExtractionProfile,
    catalog: Any,
    store: ArtifactStore,
    executor: StructuredExecutor,
    prior_attempts: list[dict[str, Any]],
    recorded_at: str,
) -> dict[str, Any]:
    schema = output_schema_for(profile)
    input_value = _task_input(snapshot, unit, profile, catalog)
    task_bytes = _task_bytes(
        instructions=profile.instructions,
        input_value=input_value,
        schema_name=profile.schema_name,
        schema=schema,
    )
    task_artifact_id = store.put(task_bytes)
    request_bytes = executor.json_request_bytes(
        instructions=profile.instructions,
        input_value=input_value,
        schema_name=profile.schema_name,
        schema=schema,
    )
    request_artifact_id = store.put(request_bytes)
    try:
        result = executor.generate_json(
            instructions=profile.instructions,
            input_value=input_value,
            schema_name=profile.schema_name,
            schema=schema,
        )
    except GenericAgentResponsePending:
        raise
    except ModelCallError as exc:
        if exc.request_bytes != request_bytes:
            raise ValidationError("E-GENERIC-REQUEST", "executor failure request differs") from exc
        attempts = _attempt_records_from_traces(
            unit_id=unit["unit_id"],
            build=build,
            task_artifact_id=task_artifact_id,
            request_artifact_id=request_artifact_id,
            traces=exc.attempts,
            store=store,
            prior_attempts=prior_attempts,
            recorded_at=recorded_at,
        )
        return {
            "status": "FAILED",
            "unit_id": unit["unit_id"],
            "request_artifact_id": request_artifact_id,
            "task_artifact_id": task_artifact_id,
            "attempts": attempts,
            "error_code": exc.code,
            "error_message": str(exc),
        }
    except PipelineError as exc:
        return {
            "status": "FAILED",
            "unit_id": unit["unit_id"],
            "request_artifact_id": request_artifact_id,
            "task_artifact_id": task_artifact_id,
            "attempts": copy.deepcopy(prior_attempts),
            "error_code": exc.code,
            "error_message": str(exc),
        }

    if result.request_bytes != request_bytes:
        raise ValidationError("E-GENERIC-REQUEST", "executor request bytes differ from preflight")
    if not result.attempts:
        raise ValidationError("E-GENERIC-ATTEMPT", "successful executor result lacks an attempt trace")
    provider_response_artifact_id = store.put(result.response_bytes) if result.response_bytes else None
    output_bytes = canonical_dumps(result.value)
    output_artifact_id = store.put(output_bytes)
    try:
        observations = validate_model_output(
            result.value,
            snapshot=snapshot,
            unit=unit,
            build=build,
            profile=profile,
            catalog=catalog,
        )
    except PipelineError as exc:
        attempts = _attempt_records_from_traces(
            unit_id=unit["unit_id"],
            build=build,
            task_artifact_id=task_artifact_id,
            request_artifact_id=request_artifact_id,
            traces=result.attempts,
            store=store,
            prior_attempts=prior_attempts,
            recorded_at=recorded_at,
            final_status_override="REJECTED",
            final_error_code=exc.code,
            final_error_message=str(exc),
        )
        return {
            "status": "FAILED",
            "unit_id": unit["unit_id"],
            "request_artifact_id": request_artifact_id,
            "task_artifact_id": task_artifact_id,
            "provider_response_artifact_id": provider_response_artifact_id,
            "output_artifact_id": output_artifact_id,
            "attempts": attempts,
            "error_code": exc.code,
            "error_message": str(exc),
        }

    attempts = _attempt_records_from_traces(
        unit_id=unit["unit_id"],
        build=build,
        task_artifact_id=task_artifact_id,
        request_artifact_id=request_artifact_id,
        traces=result.attempts,
        store=store,
        prior_attempts=prior_attempts,
        recorded_at=recorded_at,
    )
    unit_result = _unit_result_record(
        unit=unit,
        task_artifact_id=task_artifact_id,
        request_artifact_id=request_artifact_id,
        provider_response_artifact_id=provider_response_artifact_id,
        output_artifact_id=output_artifact_id,
        attempt_ids=[attempt["attempt_id"] for attempt in attempts],
        observation_ids=[observation["observation_id"] for observation in observations],
    )
    return {
        "status": "SUCCEEDED",
        "unit_id": unit["unit_id"],
        "unit_result": unit_result,
        "attempts": attempts,
        "observations": observations,
    }


def _paths_for(
    work_dir: pathlib.Path,
    profile: ExtractionProfile,
    build: dict[str, Any],
) -> GenericRunPaths:
    shared_root = work_dir / "generic-extraction"
    profile_root = shared_root / "profiles" / profile.slug
    extraction_root = profile_root / "extractions" / build["extraction_build_id"]
    return GenericRunPaths(
        shared_root=shared_root,
        snapshot_path=shared_root / "novel-text-snapshot.json",
        profile_root=profile_root,
        extraction_root=extraction_root,
        extraction_build_path=extraction_root / "extraction-build.json",
        extraction_run_path=extraction_root / "extraction-run.json",
        units_path=extraction_root / "units.jsonl",
        unit_results_path=extraction_root / "unit-results.jsonl",
        attempts_path=extraction_root / "attempts.jsonl",
        observations_path=extraction_root / "observations.jsonl",
        checkpoint_path=extraction_root / "checkpoint.json",
    )


def _checkpoint_identity(
    snapshot: dict[str, Any],
    build: dict[str, Any],
    units: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": CHECKPOINT_VERSION,
        "text_snapshot_id": snapshot["text_snapshot_id"],
        "text_snapshot_hash": snapshot["text_snapshot_hash"],
        "extraction_build_id": build["extraction_build_id"],
        "extraction_build_hash": build["extraction_build_hash"],
        "unit_result_hash": _logical_result_hash(units, "unit_hash"),
    }


def _load_or_initialize_checkpoint(
    path: pathlib.Path,
    *,
    snapshot: dict[str, Any],
    build: dict[str, Any],
    units: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = _checkpoint_identity(snapshot, build, units)
    checkpoint = _load_checkpoint(path)
    if checkpoint is None:
        checkpoint = {**expected, "completed": {}, "failed": {}}
        _write_checkpoint(path, checkpoint)
        return checkpoint
    actual = {key: checkpoint.get(key) for key in expected}
    if actual != expected:
        raise ValidationError("E-GENERIC-CHECKPOINT-BIND", "checkpoint belongs to another build")
    if not isinstance(checkpoint.get("completed"), dict) or not isinstance(checkpoint.get("failed"), dict):
        raise ValidationError("E-GENERIC-CHECKPOINT-INTEGRITY", "checkpoint result maps are invalid")
    return checkpoint


def _observations_from_output_artifact(
    *,
    output_artifact_id: str,
    unit: dict[str, Any],
    snapshot: dict[str, Any],
    build: dict[str, Any],
    profile: ExtractionProfile,
    catalog: Any,
    store: ArtifactStore,
) -> list[dict[str, Any]]:
    raw = store.get(output_artifact_id)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("E-GENERIC-OUTPUT", "normalized model output is invalid") from exc
    if not isinstance(value, dict) or canonical_dumps(value) != raw:
        raise ValidationError("E-GENERIC-OUTPUT", "normalized model output is not canonical")
    return validate_model_output(
        value,
        snapshot=snapshot,
        unit=unit,
        build=build,
        profile=profile,
        catalog=catalog,
    )


def _reconstruct_from_checkpoint(
    checkpoint: dict[str, Any],
    *,
    units: list[dict[str, Any]],
    snapshot: dict[str, Any],
    build: dict[str, Any],
    profile: ExtractionProfile,
    catalog: Any,
    store: ArtifactStore,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    units_by_id = {unit["unit_id"]: unit for unit in units}
    unit_results: list[dict[str, Any]] = []
    attempts_by_id: dict[str, dict[str, Any]] = {}
    observations_by_id: dict[str, dict[str, Any]] = {}
    for unit_id, completed in checkpoint["completed"].items():
        if unit_id not in units_by_id or not isinstance(completed, dict):
            raise ValidationError("E-GENERIC-CHECKPOINT-INTEGRITY", "checkpoint cites unknown unit")
        unit_result = completed.get("unit_result")
        attempts = completed.get("attempts")
        if not isinstance(unit_result, dict) or not isinstance(attempts, list):
            raise ValidationError("E-GENERIC-CHECKPOINT-INTEGRITY", "completed unit shape is invalid")
        for artifact_field in (
            "task_artifact_id",
            "request_artifact_id",
            "output_artifact_id",
        ):
            store.verify(unit_result[artifact_field])
        if unit_result.get("provider_response_artifact_id"):
            store.verify(unit_result["provider_response_artifact_id"])
        observations = _observations_from_output_artifact(
            output_artifact_id=unit_result["output_artifact_id"],
            unit=units_by_id[unit_id],
            snapshot=snapshot,
            build=build,
            profile=profile,
            catalog=catalog,
            store=store,
        )
        if [observation["observation_id"] for observation in observations] != unit_result["observation_ids"]:
            raise ValidationError("E-GENERIC-CHECKPOINT-INTEGRITY", "observation ids differ on replay")
        for attempt in attempts:
            attempts_by_id[attempt["attempt_id"]] = attempt
        for observation in observations:
            observations_by_id[observation["observation_id"]] = observation
        unit_results.append(unit_result)
    unit_order = {unit["unit_id"]: unit["ordinal"] for unit in units}
    return (
        sorted(unit_results, key=lambda item: unit_order[item["unit_id"]]),
        sorted(
            attempts_by_id.values(),
            key=lambda item: (unit_order[item["subject_id"]], item["attempt_ordinal"]),
        ),
        sorted(observations_by_id.values(), key=lambda item: item["observation_hash"]),
    )


def _run_extraction_units(
    *,
    paths: GenericRunPaths,
    snapshot: dict[str, Any],
    build: dict[str, Any],
    units: list[dict[str, Any]],
    profile: ExtractionProfile,
    catalog: Any,
    store: ArtifactStore,
    executor: StructuredExecutor,
    recorded_at: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], str]:
    checkpoint = _load_or_initialize_checkpoint(
        paths.checkpoint_path,
        snapshot=snapshot,
        build=build,
        units=units,
    )
    units_by_id = {unit["unit_id"]: unit for unit in units}
    pending_units = [unit for unit in units if unit["unit_id"] not in checkpoint["completed"]]
    pending_answers: list[PendingGenericAnswer] = []

    if pending_units:
        max_workers = min(int(profile.limits["max_workers"]), len(pending_units))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures: dict[Future[dict[str, Any]], str] = {}
            for unit in pending_units:
                failed_entry = checkpoint["failed"].get(unit["unit_id"], {})
                prior_attempts = failed_entry.get("attempts", []) if isinstance(failed_entry, dict) else []
                futures[
                    pool.submit(
                        _execute_unit,
                        unit=unit,
                        snapshot=snapshot,
                        build=build,
                        profile=profile,
                        catalog=catalog,
                        store=store,
                        executor=executor,
                        prior_attempts=prior_attempts,
                        recorded_at=recorded_at,
                    )
                ] = unit["unit_id"]
            for future in as_completed(futures):
                unit_id = futures[future]
                try:
                    result = future.result()
                except GenericAgentResponsePending as exc:
                    pending_answers.append(exc.pending)
                    continue
                except Exception as exc:
                    if isinstance(exc, PipelineError):
                        code = exc.code
                    else:
                        code = "E-GENERIC-EXECUTOR"
                    checkpoint["failed"][unit_id] = {
                        "attempts": checkpoint["failed"].get(unit_id, {}).get("attempts", []),
                        "error_code": code,
                        "error_message": str(exc),
                    }
                    _write_checkpoint(paths.checkpoint_path, checkpoint)
                    continue
                if result["status"] == "SUCCEEDED":
                    checkpoint["completed"][unit_id] = {
                        "unit_result": result["unit_result"],
                        "attempts": result["attempts"],
                    }
                    checkpoint["failed"].pop(unit_id, None)
                else:
                    checkpoint["failed"][unit_id] = {
                        "attempts": result.get("attempts", []),
                        "error_code": result["error_code"],
                        "error_message": result["error_message"],
                    }
                _write_checkpoint(paths.checkpoint_path, checkpoint)

    if pending_answers:
        raise GenericAgentResponsesPending(pending_answers)
    if checkpoint["failed"]:
        raise GenericExtractionPartial(checkpoint["failed"], paths.checkpoint_path)
    if set(checkpoint["completed"]) != set(units_by_id):
        raise ValidationError("E-GENERIC-CHECKPOINT-INTEGRITY", "checkpoint completion set differs")

    unit_results, attempts, observations = _reconstruct_from_checkpoint(
        checkpoint,
        units=units,
        snapshot=snapshot,
        build=build,
        profile=profile,
        catalog=catalog,
        store=store,
    )
    checkpoint_bytes = paths.checkpoint_path.read_bytes()
    checkpoint_artifact_id = store.put(checkpoint_bytes)
    return unit_results, attempts, observations, checkpoint_artifact_id


def _build_extraction_run(
    *,
    snapshot: dict[str, Any],
    build: dict[str, Any],
    profile: ExtractionProfile,
    units_artifact_id: str,
    units: list[dict[str, Any]],
    unit_results_artifact_id: str,
    unit_results: list[dict[str, Any]],
    attempts_artifact_id: str,
    attempts: list[dict[str, Any]],
    observations_artifact_id: str,
    observations: list[dict[str, Any]],
    checkpoint_artifact_id: str,
    coverage: dict[str, Any],
    created_at: str,
) -> dict[str, Any]:
    body = {
        "schema_version": "extraction-run/v1",
        "record_kind": "EXTRACTION_RUN",
        "text_snapshot_id": snapshot["text_snapshot_id"],
        "text_snapshot_hash": snapshot["text_snapshot_hash"],
        "extraction_build_id": build["extraction_build_id"],
        "extraction_build_hash": build["extraction_build_hash"],
        "profile_id": profile.profile_id,
        "profile_version": profile.profile_version,
        "extraction_profile_hash": profile.extraction_profile_hash,
        "units_artifact_id": units_artifact_id,
        "unit_count": len(units),
        "unit_result_hash": _logical_result_hash(units, "unit_hash"),
        "unit_results_artifact_id": unit_results_artifact_id,
        "unit_result_count": len(unit_results),
        "unit_results_hash": object_hash(
            {"result_hashes": [item["result_hash"] for item in unit_results]}, omit=()
        ),
        "model_attempts_artifact_id": attempts_artifact_id,
        "model_attempt_count": len(attempts),
        "model_attempts_hash": object_hash(
            {"attempt_ids": [attempt["attempt_id"] for attempt in attempts]}, omit=()
        ),
        "observations_artifact_id": observations_artifact_id,
        "observation_count": len(observations),
        "observation_result_hash": _logical_result_hash(observations, "observation_hash"),
        "checkpoint_artifact_id": checkpoint_artifact_id,
        "coverage": copy.deepcopy(coverage),
        "artifact_integrity": "VALID",
        "exact_runtime": "AVAILABLE",
        "functional_replay": "VERIFIED",
        "semantic_assurance": "UNQUALIFIED",
        "status": "SUCCEEDED",
    }
    run_hash = object_hash(body, omit=())
    return {
        **body,
        "extraction_run_id": _generic_id("XRUN-", {"extraction_run_hash": run_hash}),
        "extraction_run_hash": run_hash,
        "started_at": created_at,
        "completed_at": created_at,
    }


def _load_cached_extraction(
    *,
    paths: GenericRunPaths,
    snapshot: dict[str, Any],
    build: dict[str, Any],
    profile: ExtractionProfile,
    units: list[dict[str, Any]],
    coverage: dict[str, Any],
    catalog: Any,
    store: ArtifactStore,
    root: pathlib.Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]] | None:
    if not paths.extraction_run_path.exists():
        return None
    build_on_disk = _parse_canonical_json_bytes(
        paths.extraction_build_path.read_bytes(), label="cached extraction build"
    )
    if build_on_disk != build:
        raise ValidationError("E-GENERIC-CACHE-BIND", "cached build differs")
    run = _parse_canonical_json_bytes(paths.extraction_run_path.read_bytes(), label="cached extraction run")
    units_on_disk = _parse_canonical_jsonl_bytes(
        paths.units_path.read_bytes(), label="cached units"
    )
    if units_on_disk != units:
        raise ValidationError("E-GENERIC-CACHE-BIND", "cached unit plan differs")
    unit_results = _parse_canonical_jsonl_bytes(
        paths.unit_results_path.read_bytes(), label="cached unit results"
    )
    attempts = _parse_canonical_jsonl_bytes(paths.attempts_path.read_bytes(), label="cached attempts")
    observations = _parse_canonical_jsonl_bytes(
        paths.observations_path.read_bytes(), label="cached observations"
    )
    if paths.checkpoint_path.read_bytes() != store.get(run["checkpoint_artifact_id"]):
        raise ValidationError("E-GENERIC-CACHE-BIND", "cached checkpoint differs")
    validate_generic_extraction_artifacts(
        snapshot=snapshot,
        build=build,
        run=run,
        units=units,
        unit_results=unit_results,
        attempts=attempts,
        observations=observations,
        coverage=coverage,
        profile=profile,
        catalog=catalog,
        store=store,
        root=root,
    )
    return run, unit_results, attempts, observations


def run_generic_extraction(
    spec: dict[str, Any],
    work_dir: pathlib.Path,
    *,
    profile_ref: str,
    executor: StructuredExecutor,
    root: pathlib.Path | None = None,
    profiles_root: pathlib.Path | None = None,
    now: str,
    fetcher: Any | None = None,
) -> GenericExtractionResult:
    root = (root or repo_root()).resolve()
    work_dir = pathlib.Path(work_dir)
    declared_rights(spec, require_storage=True, require_external_model=True)
    ingestion_result = run_novel_ingestion(
        spec,
        work_dir / "ingestion",
        repo_root=root,
        fetcher=fetcher,
        now=now,
    )
    catalog = ingestion_result["catalog"]
    store = ingestion_result["store"]
    ingestion = ingestion_result["ingestion"]
    if ingestion["status"] == "FAILED":
        raise ValidationError("E-GENERIC-INGESTION", "failed ingestion cannot enter extraction")

    profile = load_extraction_profile(
        profile_ref,
        root=root,
        profiles_root=profiles_root,
    )
    snapshot = build_novel_text_snapshot(catalog, store, ingestion, spec, root=root)
    build = build_extraction_build(
        profile, executor, store, root=root, created_at=snapshot["created_at"]
    )
    paths = _paths_for(work_dir, profile, build)
    _write_immutable(paths.snapshot_path, _canonical_json_bytes(snapshot))
    _write_immutable(paths.extraction_build_path, _canonical_json_bytes(build))

    units, coverage = build_extraction_units(snapshot, catalog, profile)
    cached = _load_cached_extraction(
        paths=paths,
        snapshot=snapshot,
        build=build,
        profile=profile,
        units=units,
        coverage=coverage,
        catalog=catalog,
        store=store,
        root=root,
    )
    if cached is not None:
        run, unit_results, attempts, observations = cached
        return GenericExtractionResult(
            catalog=catalog,
            store=store,
            ingestion=ingestion,
            snapshot=snapshot,
            profile=profile,
            build=build,
            run=run,
            units=units,
            unit_results=unit_results,
            attempts=attempts,
            observations=observations,
            paths=paths,
            reused_extraction=True,
        )

    unit_results, attempts, observations, checkpoint_artifact_id = _run_extraction_units(
        paths=paths,
        snapshot=snapshot,
        build=build,
        units=units,
        profile=profile,
        catalog=catalog,
        store=store,
        executor=executor,
        recorded_at=now,
    )
    for attempt in attempts:
        _validate_generic_schema("ModelAttemptV2", attempt, root=root)

    units_bytes = _canonical_jsonl_bytes(units)
    unit_results_bytes = _canonical_jsonl_bytes(unit_results)
    attempts_bytes = _canonical_jsonl_bytes(attempts)
    observations_bytes = _canonical_jsonl_bytes(observations)
    units_artifact_id = _artifact_and_write(store, paths.units_path, units_bytes)
    unit_results_artifact_id = _artifact_and_write(
        store, paths.unit_results_path, unit_results_bytes
    )
    attempts_artifact_id = _artifact_and_write(store, paths.attempts_path, attempts_bytes)
    observations_artifact_id = _artifact_and_write(
        store, paths.observations_path, observations_bytes
    )
    run = _build_extraction_run(
        snapshot=snapshot,
        build=build,
        profile=profile,
        units_artifact_id=units_artifact_id,
        units=units,
        unit_results_artifact_id=unit_results_artifact_id,
        unit_results=unit_results,
        attempts_artifact_id=attempts_artifact_id,
        attempts=attempts,
        observations_artifact_id=observations_artifact_id,
        observations=observations,
        checkpoint_artifact_id=checkpoint_artifact_id,
        coverage=coverage,
        created_at=now,
    )
    _validate_generic_schema("ExtractionRun", run, root=root)
    _write_immutable(paths.extraction_run_path, _canonical_json_bytes(run))
    validate_generic_extraction_artifacts(
        snapshot=snapshot,
        build=build,
        run=run,
        units=units,
        unit_results=unit_results,
        attempts=attempts,
        observations=observations,
        coverage=coverage,
        profile=profile,
        catalog=catalog,
        store=store,
        root=root,
    )
    return GenericExtractionResult(
        catalog=catalog,
        store=store,
        ingestion=ingestion,
        snapshot=snapshot,
        profile=profile,
        build=build,
        run=run,
        units=units,
        unit_results=unit_results,
        attempts=attempts,
        observations=observations,
        paths=paths,
        reused_extraction=False,
    )



_UNIT_RESULT_FIELDS = {
    "schema_version",
    "unit_id",
    "task_artifact_id",
    "request_artifact_id",
    "provider_response_artifact_id",
    "output_artifact_id",
    "attempt_ids",
    "observation_ids",
    "status",
    "result_hash",
}


def _validate_attempt_identity(
    attempt: dict[str, Any],
    *,
    build: dict[str, Any],
    unit: dict[str, Any],
    result: dict[str, Any],
    store: ArtifactStore,
) -> None:
    body = {key: value for key, value in attempt.items() if key != "attempt_id"}
    if attempt["attempt_id"] != _generic_id("MAT2-", body):
        raise ValidationError("E-GENERIC-ATTEMPT", "model attempt identity differs")
    if (
        attempt["extraction_build_id"] != build["extraction_build_id"]
        or attempt["subject_kind"] != "ExtractionUnit"
        or attempt["subject_id"] != unit["unit_id"]
        or attempt["task_artifact_id"] != result["task_artifact_id"]
        or attempt["request_artifact_id"] != result["request_artifact_id"]
    ):
        raise ValidationError("E-GENERIC-ATTEMPT", "model attempt lineage differs")
    store.verify(attempt["task_artifact_id"])
    store.verify(attempt["request_artifact_id"])
    if attempt["response_artifact_id"] is not None:
        store.verify(attempt["response_artifact_id"])


def _validate_completed_checkpoint(
    *,
    checkpoint_bytes: bytes,
    snapshot: dict[str, Any],
    build: dict[str, Any],
    units: list[dict[str, Any]],
    unit_results: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
) -> None:
    checkpoint = _parse_canonical_json_bytes(
        checkpoint_bytes,
        label="completed generic checkpoint",
    )
    if checkpoint.get(CHECKPOINT_INTEGRITY_FIELD) != _checkpoint_hash(checkpoint):
        raise ValidationError(
            "E-GENERIC-CHECKPOINT-INTEGRITY",
            "completed checkpoint hash differs",
        )
    identity = _checkpoint_identity(snapshot, build, units)
    if {key: checkpoint.get(key) for key in identity} != identity:
        raise ValidationError(
            "E-GENERIC-CHECKPOINT-BIND",
            "completed checkpoint belongs to another extraction",
        )
    if checkpoint.get("failed") != {}:
        raise ValidationError(
            "E-GENERIC-CHECKPOINT-INTEGRITY",
            "successful extraction retains failed units",
        )
    results_by_unit = {result["unit_id"]: result for result in unit_results}
    attempts_by_unit: dict[str, list[dict[str, Any]]] = {}
    for attempt in attempts:
        attempts_by_unit.setdefault(attempt["subject_id"], []).append(attempt)
    expected_completed = {
        unit["unit_id"]: {
            "unit_result": results_by_unit[unit["unit_id"]],
            "attempts": sorted(
                attempts_by_unit.get(unit["unit_id"], []),
                key=lambda attempt: attempt["attempt_ordinal"],
            ),
        }
        for unit in units
    }
    if checkpoint.get("completed") != expected_completed:
        raise ValidationError(
            "E-GENERIC-CHECKPOINT-INTEGRITY",
            "completed checkpoint records differ from immutable outputs",
        )


def _validate_unit_results_and_attempts(
    *,
    units: list[dict[str, Any]],
    unit_results: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    build: dict[str, Any],
    store: ArtifactStore,
) -> None:
    unit_order = {unit["unit_id"]: unit["ordinal"] for unit in units}
    if len(unit_order) != len(units):
        raise ValidationError("E-GENERIC-REPLAY", "duplicate extraction unit identity")
    if [result.get("unit_id") for result in unit_results] != [
        unit["unit_id"] for unit in units
    ]:
        raise ValidationError(
            "E-GENERIC-REPLAY",
            "successful unit result set or order differs from unit plan",
        )
    if observations != sorted(observations, key=lambda item: item["observation_hash"]):
        raise ValidationError("E-GENERIC-REPLAY", "observations are not canonically ordered")
    expected_attempt_order = sorted(
        attempts,
        key=lambda attempt: (
            unit_order.get(attempt.get("subject_id"), 10**9),
            attempt.get("attempt_ordinal", 10**9),
        ),
    )
    if attempts != expected_attempt_order:
        raise ValidationError("E-GENERIC-REPLAY", "model attempts are not canonically ordered")

    observations_by_id = {
        observation["observation_id"]: observation for observation in observations
    }
    if len(observations_by_id) != len(observations):
        raise ValidationError("E-GENERIC-REPLAY", "duplicate observation identity")
    attempts_by_id = {attempt["attempt_id"]: attempt for attempt in attempts}
    if len(attempts_by_id) != len(attempts):
        raise ValidationError("E-GENERIC-REPLAY", "duplicate model attempt identity")

    referenced_attempt_ids: list[str] = []
    referenced_observation_ids: list[str] = []
    for unit, result in zip(units, unit_results, strict=True):
        if set(result) != _UNIT_RESULT_FIELDS:
            raise ValidationError("E-GENERIC-REPLAY", "unit result has an invalid shape")
        body = {key: value for key, value in result.items() if key != "result_hash"}
        if (
            result["schema_version"] != "extraction-unit-result/v1"
            or result["status"] != "SUCCEEDED"
            or result["result_hash"] != object_hash(body, omit=())
        ):
            raise ValidationError("E-GENERIC-REPLAY", "unit result identity differs")
        for field in ("task_artifact_id", "request_artifact_id", "output_artifact_id"):
            store.verify(result[field])
        if result["provider_response_artifact_id"] is not None:
            store.verify(result["provider_response_artifact_id"])

        chain = []
        for attempt_id in result["attempt_ids"]:
            attempt = attempts_by_id.get(attempt_id)
            if attempt is None:
                raise ValidationError("E-GENERIC-ATTEMPT", "unit result omits attempt record")
            chain.append(attempt)
            _validate_attempt_identity(
                attempt,
                build=build,
                unit=unit,
                result=result,
                store=store,
            )
        if not chain:
            raise ValidationError("E-GENERIC-ATTEMPT", "successful unit has no attempts")
        if [attempt["attempt_ordinal"] for attempt in chain] != list(
            range(1, len(chain) + 1)
        ):
            raise ValidationError("E-GENERIC-ATTEMPT", "attempt ordinals are not consecutive")
        expected_retry = None
        for index, attempt in enumerate(chain):
            if attempt["retry_of"] != expected_retry:
                raise ValidationError("E-GENERIC-ATTEMPT", "attempt retry chain differs")
            expected_retry = attempt["attempt_id"]
            if index < len(chain) - 1 and attempt["status"] == "SUCCEEDED":
                raise ValidationError(
                    "E-GENERIC-ATTEMPT",
                    "successful attempt cannot precede another retry",
                )
        final_attempt = chain[-1]
        if (
            final_attempt["status"] != "SUCCEEDED"
            or final_attempt["error_code"] is not None
            or final_attempt["error_message"] is not None
            or final_attempt["response_artifact_id"]
            != result["provider_response_artifact_id"]
        ):
            raise ValidationError("E-GENERIC-ATTEMPT", "final successful attempt differs")

        if len(result["observation_ids"]) != len(set(result["observation_ids"])):
            raise ValidationError("E-GENERIC-REPLAY", "unit result duplicates observations")
        for observation_id in result["observation_ids"]:
            observation = observations_by_id.get(observation_id)
            if observation is None or observation["unit_id"] != unit["unit_id"]:
                raise ValidationError("E-GENERIC-REPLAY", "observation lineage differs")
        referenced_attempt_ids.extend(result["attempt_ids"])
        referenced_observation_ids.extend(result["observation_ids"])

    if referenced_attempt_ids != [attempt["attempt_id"] for attempt in attempts]:
        raise ValidationError("E-GENERIC-ATTEMPT", "attempt set differs from unit results")
    if sorted(referenced_observation_ids) != sorted(observations_by_id):
        raise ValidationError("E-GENERIC-REPLAY", "observation set differs from unit results")


def validate_generic_extraction_artifacts(
    *,
    snapshot: dict[str, Any],
    build: dict[str, Any],
    run: dict[str, Any],
    units: list[dict[str, Any]],
    unit_results: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    coverage: dict[str, Any],
    profile: ExtractionProfile,
    catalog: Any,
    store: ArtifactStore,
    root: pathlib.Path,
) -> None:
    _validate_generic_schema("NovelTextSnapshot", snapshot, root=root)
    _validate_generic_schema("ExtractionBuild", build, root=root)
    build_body = {
        key: value
        for key, value in build.items()
        if key not in {"extraction_build_id", "extraction_build_hash", "created_at"}
    }
    expected_build_hash = object_hash(build_body, omit=())
    if (
        build["extraction_build_hash"] != expected_build_hash
        or build["extraction_build_id"]
        != _generic_id("XBLD-", {"extraction_build_hash": expected_build_hash})
    ):
        raise ValidationError("E-GENERIC-BUILD-BIND", "extraction build identity differs")
    _validate_generic_schema("ExtractionRun", run, root=root)
    for attempt in attempts:
        _validate_generic_schema("ModelAttemptV2", attempt, root=root)
    if (
        build["engine_repository_commit"] != repository_commit(root)
        or build["engine_source_hash"] != _generic_engine_source_hash(root)
    ):
        raise ValidationError("E-GENERIC-BUILD-BIND", "exact engine runtime differs")
    if (
        build["profile_id"] != profile.profile_id
        or build["profile_version"] != profile.profile_version
        or build["extraction_profile_hash"] != profile.extraction_profile_hash
        or build["unit_policy"] != profile.unit_policy
    ):
        raise ValidationError("E-GENERIC-BUILD-BIND", "profile build identity differs")
    for label, data, expected_id in extraction_assets(profile):
        if store.get(expected_id) != data:
            raise ValidationError("E-GENERIC-BUILD-BIND", f"{label} artifact differs")
    expected_units, expected_coverage = build_extraction_units(snapshot, catalog, profile)
    if units != expected_units or coverage != expected_coverage:
        raise ValidationError("E-GENERIC-REPLAY", "unit plan differs")
    if run["coverage"] != coverage:
        raise ValidationError("E-GENERIC-REPLAY", "run coverage differs")
    if (
        run["text_snapshot_id"] != snapshot["text_snapshot_id"]
        or run["text_snapshot_hash"] != snapshot["text_snapshot_hash"]
        or run["extraction_build_id"] != build["extraction_build_id"]
        or run["extraction_build_hash"] != build["extraction_build_hash"]
        or run["profile_id"] != profile.profile_id
        or run["profile_version"] != profile.profile_version
        or run["extraction_profile_hash"] != profile.extraction_profile_hash
    ):
        raise ValidationError("E-GENERIC-REPLAY", "extraction run lineage differs")

    _validate_unit_results_and_attempts(
        units=units,
        unit_results=unit_results,
        attempts=attempts,
        observations=observations,
        build=build,
        store=store,
    )

    units_data = _canonical_jsonl_bytes(units)
    unit_results_data = _canonical_jsonl_bytes(unit_results)
    attempts_data = _canonical_jsonl_bytes(attempts)
    observations_data = _canonical_jsonl_bytes(observations)
    for field, data in (
        ("units_artifact_id", units_data),
        ("unit_results_artifact_id", unit_results_data),
        ("model_attempts_artifact_id", attempts_data),
        ("observations_artifact_id", observations_data),
    ):
        if store.get(run[field]) != data:
            raise ValidationError("E-GENERIC-ARTIFACT", f"{field} bytes differ")

    if (
        run["unit_count"] != len(units)
        or run["unit_result_count"] != len(unit_results)
        or run["model_attempt_count"] != len(attempts)
        or run["observation_count"] != len(observations)
        or run["unit_result_hash"] != _logical_result_hash(units, "unit_hash")
        or run["unit_results_hash"]
        != object_hash({"result_hashes": [item["result_hash"] for item in unit_results]}, omit=())
        or run["model_attempts_hash"]
        != object_hash({"attempt_ids": [attempt["attempt_id"] for attempt in attempts]}, omit=())
        or run["observation_result_hash"]
        != _logical_result_hash(observations, "observation_hash")
    ):
        raise ValidationError("E-GENERIC-REPLAY", "run counts or logical hashes differ")

    units_by_id = {unit["unit_id"]: unit for unit in units}
    attempts_by_id = {attempt["attempt_id"]: attempt for attempt in attempts}
    observations_by_id = {observation["observation_id"]: observation for observation in observations}
    if len(attempts_by_id) != len(attempts) or len(observations_by_id) != len(observations):
        raise ValidationError("E-GENERIC-REPLAY", "duplicate attempt or observation identity")
    replayed_observations: dict[str, dict[str, Any]] = {}
    schema = output_schema_for(profile)
    for result in unit_results:
        unit = units_by_id.get(result["unit_id"])
        if unit is None:
            raise ValidationError("E-GENERIC-REPLAY", "unit result references unknown unit")
        body = {key: value for key, value in result.items() if key != "result_hash"}
        if result["result_hash"] != object_hash(body, omit=()):
            raise ValidationError("E-GENERIC-REPLAY", "unit result hash differs")
        input_value = _task_input(snapshot, unit, profile, catalog)
        task_bytes = _task_bytes(
            instructions=profile.instructions,
            input_value=input_value,
            schema_name=profile.schema_name,
            schema=schema,
        )
        request_bytes = _rebuild_executor_request(
            build,
            instructions=profile.instructions,
            input_value=input_value,
            schema_name=profile.schema_name,
            schema=schema,
        )
        if store.get(result["task_artifact_id"]) != task_bytes:
            raise ValidationError("E-GENERIC-REPLAY", "semantic task differs")
        if store.get(result["request_artifact_id"]) != request_bytes:
            raise ValidationError("E-GENERIC-REPLAY", "executor request differs")
        for attempt_id in result["attempt_ids"]:
            attempt = attempts_by_id.get(attempt_id)
            if attempt is None or attempt["subject_id"] != unit["unit_id"]:
                raise ValidationError("E-GENERIC-REPLAY", "attempt lineage differs")
        unit_observations = _observations_from_output_artifact(
            output_artifact_id=result["output_artifact_id"],
            unit=unit,
            snapshot=snapshot,
            build=build,
            profile=profile,
            catalog=catalog,
            store=store,
        )
        if [item["observation_id"] for item in unit_observations] != result["observation_ids"]:
            raise ValidationError("E-GENERIC-REPLAY", "unit observations differ")
        for observation in unit_observations:
            replayed_observations[observation["observation_id"]] = observation
    if sorted(replayed_observations.values(), key=lambda item: item["observation_hash"]) != observations:
        raise ValidationError("E-GENERIC-REPLAY", "observation set differs from output replay")

    checkpoint_bytes = store.get(run["checkpoint_artifact_id"])
    _validate_completed_checkpoint(
        checkpoint_bytes=checkpoint_bytes,
        snapshot=snapshot,
        build=build,
        units=units,
        unit_results=unit_results,
        attempts=attempts,
    )

    run_body = {
        key: value
        for key, value in run.items()
        if key not in {"extraction_run_id", "extraction_run_hash", "started_at", "completed_at"}
    }
    expected_hash = object_hash(run_body, omit=())
    if (
        run["extraction_run_hash"] != expected_hash
        or run["extraction_run_id"] != _generic_id("XRUN-", {"extraction_run_hash": expected_hash})
    ):
        raise ValidationError("E-GENERIC-ID-BIND", "extraction run identity differs")
    if run["semantic_assurance"] != "UNQUALIFIED":
        raise ValidationError("E-GENERIC-ASSURANCE", "unqualified extraction was promoted")


def run_generic_reduction(
    extraction: GenericExtractionResult,
    *,
    root: pathlib.Path | None = None,
    now: str,
) -> GenericCorpusResult:
    root = (root or repo_root()).resolve()
    profile = extraction.profile
    reduction = profile.reduction
    reducer_id = str(reduction["reducer_id"])
    config = copy.deepcopy(reduction["config"])
    implementation_hash = reducer_implementation_hash(reducer_id)
    config_hash = object_hash(config, omit=())
    profile_manifest_artifact_id = _put_expected(
        extraction.store,
        profile.manifest_bytes,
        profile.manifest_artifact_id,
        label="profile-manifest",
    )
    reduction_time = extraction.run["completed_at"]
    records = reduce_observations(
        extraction.observations,
        reducer_id=reducer_id,
        config=config,
    )
    corpus_bytes = _canonical_jsonl_bytes(records)
    corpus_artifact_id = extraction.store.put(corpus_bytes)
    corpus_result_hash = _logical_result_hash(records, "record_hash")
    body = {
        "schema_version": "reduction-run/v1",
        "record_kind": "REDUCTION_RUN",
        "extraction_run_id": extraction.run["extraction_run_id"],
        "extraction_run_hash": extraction.run["extraction_run_hash"],
        "profile_id": profile.profile_id,
        "profile_version": profile.profile_version,
        "profile_package_hash": profile.package_hash,
        "profile_manifest_artifact_id": profile_manifest_artifact_id,
        "reduction_profile_hash": profile.reduction_profile_hash,
        "reducer_id": reducer_id,
        "reducer_implementation_hash": implementation_hash,
        "reducer_config": config,
        "reducer_config_hash": config_hash,
        "input_observations_artifact_id": extraction.run["observations_artifact_id"],
        "input_observation_count": len(extraction.observations),
        "input_observation_result_hash": extraction.run["observation_result_hash"],
        "corpus_artifact_id": corpus_artifact_id,
        "corpus_record_count": len(records),
        "corpus_result_hash": corpus_result_hash,
        "status": "SUCCEEDED",
    }
    reduction_hash = object_hash(body, omit=())
    reduction_run = {
        **body,
        "reduction_run_id": _generic_id("RRUN-", {"reduction_run_hash": reduction_hash}),
        "reduction_run_hash": reduction_hash,
        "created_at": reduction_time,
    }
    _validate_generic_schema("ReductionRun", reduction_run, root=root)

    reduction_root = extraction.paths.extraction_root / "reductions" / reduction_run["reduction_run_id"]
    reduction_run_path = reduction_root / "reduction-run.json"
    corpus_path = reduction_root / "corpus.jsonl"
    snapshot_path = reduction_root / "corpus-snapshot.json"
    _write_immutable(corpus_path, corpus_bytes)
    _write_immutable(reduction_run_path, _canonical_json_bytes(reduction_run))

    snapshot_body = {
        "schema_version": "corpus-snapshot/v1",
        "record_kind": "CORPUS_SNAPSHOT",
        "text_snapshot_id": extraction.snapshot["text_snapshot_id"],
        "text_snapshot_hash": extraction.snapshot["text_snapshot_hash"],
        "extraction_run_id": extraction.run["extraction_run_id"],
        "extraction_run_hash": extraction.run["extraction_run_hash"],
        "reduction_run_id": reduction_run["reduction_run_id"],
        "reduction_run_hash": reduction_run["reduction_run_hash"],
        "profile_id": profile.profile_id,
        "profile_version": profile.profile_version,
        "profile_package_hash": profile.package_hash,
        "corpus_artifact_id": corpus_artifact_id,
        "corpus_record_count": len(records),
        "corpus_result_hash": corpus_result_hash,
        "text_coverage": "FULL",
        "semantic_coverage": "UNMEASURED",
        "artifact_integrity": "VALID",
        "exact_runtime": "AVAILABLE",
        "functional_replay": "VERIFIED",
        "semantic_assurance": "UNQUALIFIED",
        "status": "FROZEN",
    }
    corpus_snapshot_hash = object_hash(snapshot_body, omit=())
    corpus_snapshot = {
        **snapshot_body,
        "corpus_snapshot_id": _generic_id(
            "CPS-", {"corpus_snapshot_hash": corpus_snapshot_hash}
        ),
        "corpus_snapshot_hash": corpus_snapshot_hash,
        "created_at": reduction_time,
    }
    _validate_generic_schema("CorpusSnapshot", corpus_snapshot, root=root)
    _write_immutable(snapshot_path, _canonical_json_bytes(corpus_snapshot))
    validate_generic_corpus(
        extraction=extraction,
        reduction_run=reduction_run,
        corpus_snapshot=corpus_snapshot,
        records=records,
        root=root,
    )
    return GenericCorpusResult(
        extraction=extraction,
        reduction_run=reduction_run,
        corpus_snapshot=corpus_snapshot,
        corpus_records=records,
        reduction_root=reduction_root,
        reduction_run_path=reduction_run_path,
        corpus_path=corpus_path,
        corpus_snapshot_path=snapshot_path,
    )


def validate_generic_corpus(
    *,
    extraction: GenericExtractionResult,
    reduction_run: dict[str, Any],
    corpus_snapshot: dict[str, Any],
    records: list[dict[str, Any]],
    root: pathlib.Path,
) -> None:
    _validate_generic_schema("ReductionRun", reduction_run, root=root)
    _validate_generic_schema("CorpusSnapshot", corpus_snapshot, root=root)
    manifest_bytes = extraction.store.get(reduction_run["profile_manifest_artifact_id"])
    prompt_bytes = extraction.store.get(extraction.build["profile_prompt_artifact_id"])
    payload_schema_bytes = extraction.store.get(extraction.build["payload_schema_artifact_id"])
    try:
        archived_manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(
            "E-GENERIC-REDUCTION-REPLAY",
            "archived profile manifest is invalid",
        ) from exc
    if not isinstance(archived_manifest, dict):
        raise ValidationError(
            "E-GENERIC-REDUCTION-REPLAY",
            "archived profile manifest must be an object",
        )
    expected_package_hash = profile_package_hash_from_assets(
        manifest_bytes, prompt_bytes, payload_schema_bytes
    )
    archived_reduction = archived_manifest.get("reduction")
    expected_reduction_profile_hash = object_hash(
        {
            "profile_id": archived_manifest.get("profile_id"),
            "profile_version": archived_manifest.get("profile_version"),
            "reduction": archived_reduction,
        },
        omit=(),
    )
    if (
        reduction_run["profile_package_hash"] != expected_package_hash
        or reduction_run["profile_id"] != archived_manifest.get("profile_id")
        or reduction_run["profile_version"] != archived_manifest.get("profile_version")
        or not isinstance(archived_reduction, dict)
        or reduction_run["reducer_id"] != archived_reduction.get("reducer_id")
        or reduction_run["reducer_config"] != archived_reduction.get("config")
        or reduction_run["reduction_profile_hash"] != expected_reduction_profile_hash
    ):
        raise ValidationError(
            "E-GENERIC-REDUCTION-REPLAY",
            "reduction is not bound to its archived profile manifest",
        )
    if (
        reduction_run["extraction_run_id"] != extraction.run["extraction_run_id"]
        or reduction_run["extraction_run_hash"] != extraction.run["extraction_run_hash"]
        or reduction_run["input_observations_artifact_id"]
        != extraction.run["observations_artifact_id"]
        or reduction_run["input_observation_count"] != len(extraction.observations)
        or reduction_run["input_observation_result_hash"]
        != extraction.run["observation_result_hash"]
    ):
        raise ValidationError(
            "E-GENERIC-REDUCTION-REPLAY",
            "reduction input differs from extraction output",
        )
    expected_records = reduce_observations(
        extraction.observations,
        reducer_id=reduction_run["reducer_id"],
        config=reduction_run["reducer_config"],
    )
    if records != expected_records:
        raise ValidationError("E-GENERIC-REDUCTION-REPLAY", "corpus records differ")
    corpus_bytes = _canonical_jsonl_bytes(records)
    if extraction.store.get(reduction_run["corpus_artifact_id"]) != corpus_bytes:
        raise ValidationError("E-GENERIC-REDUCTION-REPLAY", "corpus artifact differs")
    expected_result_hash = _logical_result_hash(records, "record_hash")
    if (
        reduction_run["reducer_implementation_hash"]
        != reducer_implementation_hash(reduction_run["reducer_id"])
        or reduction_run["reducer_config_hash"]
        != object_hash(reduction_run["reducer_config"], omit=())
        or reduction_run["input_observation_result_hash"]
        != extraction.run["observation_result_hash"]
        or reduction_run["corpus_result_hash"] != expected_result_hash
        or reduction_run["corpus_record_count"] != len(records)
    ):
        raise ValidationError("E-GENERIC-REDUCTION-REPLAY", "reduction lineage differs")
    reduction_body = {
        key: value
        for key, value in reduction_run.items()
        if key not in {"reduction_run_id", "reduction_run_hash", "created_at"}
    }
    expected_reduction_hash = object_hash(reduction_body, omit=())
    if (
        reduction_run["reduction_run_hash"] != expected_reduction_hash
        or reduction_run["reduction_run_id"]
        != _generic_id("RRUN-", {"reduction_run_hash": expected_reduction_hash})
    ):
        raise ValidationError("E-GENERIC-ID-BIND", "reduction run identity differs")
    if records != sorted(records, key=lambda record: record["record_hash"]):
        raise ValidationError("E-GENERIC-CORPUS", "corpus records are not canonically ordered")
    if len({record["record_id"] for record in records}) != len(records):
        raise ValidationError("E-GENERIC-CORPUS", "corpus contains duplicate record identities")
    expected_snapshot_bindings = {
        "text_snapshot_id": extraction.snapshot["text_snapshot_id"],
        "text_snapshot_hash": extraction.snapshot["text_snapshot_hash"],
        "extraction_run_id": extraction.run["extraction_run_id"],
        "extraction_run_hash": extraction.run["extraction_run_hash"],
        "reduction_run_id": reduction_run["reduction_run_id"],
        "reduction_run_hash": reduction_run["reduction_run_hash"],
        "profile_id": reduction_run["profile_id"],
        "profile_version": reduction_run["profile_version"],
        "profile_package_hash": reduction_run["profile_package_hash"],
        "corpus_artifact_id": reduction_run["corpus_artifact_id"],
        "corpus_record_count": reduction_run["corpus_record_count"],
        "corpus_result_hash": reduction_run["corpus_result_hash"],
    }
    if any(corpus_snapshot.get(key) != value for key, value in expected_snapshot_bindings.items()):
        raise ValidationError("E-GENERIC-CORPUS", "corpus snapshot lineage differs")
    snapshot_body = {
        key: value
        for key, value in corpus_snapshot.items()
        if key not in {"corpus_snapshot_id", "corpus_snapshot_hash", "created_at"}
    }
    expected_snapshot_hash = object_hash(snapshot_body, omit=())
    if (
        corpus_snapshot["corpus_snapshot_hash"] != expected_snapshot_hash
        or corpus_snapshot["corpus_snapshot_id"]
        != _generic_id("CPS-", {"corpus_snapshot_hash": expected_snapshot_hash})
        or corpus_snapshot["corpus_result_hash"] != expected_result_hash
        or corpus_snapshot["semantic_assurance"] != "UNQUALIFIED"
        or corpus_snapshot["semantic_coverage"] != "UNMEASURED"
    ):
        raise ValidationError("E-GENERIC-CORPUS", "corpus snapshot differs")


def run_generic_corpus_workflow(
    spec: dict[str, Any],
    work_dir: pathlib.Path,
    *,
    profile_ref: str,
    executor: StructuredExecutor,
    root: pathlib.Path | None = None,
    profiles_root: pathlib.Path | None = None,
    now: str,
    fetcher: Any | None = None,
) -> GenericCorpusResult:
    extraction = run_generic_extraction(
        spec,
        work_dir,
        profile_ref=profile_ref,
        executor=executor,
        root=root,
        profiles_root=profiles_root,
        now=now,
        fetcher=fetcher,
    )
    return run_generic_reduction(extraction, root=root, now=now)


def validate_generic_work_dir(
    spec: dict[str, Any],
    work_dir: pathlib.Path,
    *,
    profile_ref: str,
    root: pathlib.Path | None = None,
    profiles_root: pathlib.Path | None = None,
    now: str,
    fetcher: Any | None = None,
) -> list[GenericCorpusResult]:
    """Validate every completed reduction for one built-in Profile without model access."""

    root = (root or repo_root()).resolve()
    work_dir = pathlib.Path(work_dir)
    ingestion_result = run_novel_ingestion(
        spec,
        work_dir / "ingestion",
        repo_root=root,
        fetcher=fetcher,
        now=now,
    )
    catalog = ingestion_result["catalog"]
    store = ingestion_result["store"]
    ingestion = ingestion_result["ingestion"]
    profile = load_extraction_profile(profile_ref, root=root, profiles_root=profiles_root)
    snapshot_path = work_dir / "generic-extraction" / "novel-text-snapshot.json"
    if not snapshot_path.is_file():
        raise ValidationError("E-GENERIC-VALIDATE", f"missing {snapshot_path}")
    snapshot = _parse_canonical_json_bytes(snapshot_path.read_bytes(), label="NovelTextSnapshot")
    validate_novel_text_snapshot(snapshot, catalog, store, ingestion, spec, root=root)
    expected_units, coverage = build_extraction_units(snapshot, catalog, profile)

    profile_root = work_dir / "generic-extraction" / "profiles" / profile.slug
    extraction_roots = sorted((profile_root / "extractions").glob("XBLD-*"))
    if not extraction_roots:
        raise ValidationError("E-GENERIC-VALIDATE", f"no completed extraction under {profile_root}")
    results: list[GenericCorpusResult] = []
    for extraction_root in extraction_roots:
        build = _parse_canonical_json_bytes(
            (extraction_root / "extraction-build.json").read_bytes(),
            label="ExtractionBuild",
        )
        if build.get("extraction_profile_hash") != profile.extraction_profile_hash:
            continue
        run = _parse_canonical_json_bytes(
            (extraction_root / "extraction-run.json").read_bytes(),
            label="ExtractionRun",
        )
        units = _parse_canonical_jsonl_bytes(
            (extraction_root / "units.jsonl").read_bytes(), label="units"
        )
        unit_results = _parse_canonical_jsonl_bytes(
            (extraction_root / "unit-results.jsonl").read_bytes(), label="unit results"
        )
        attempts = _parse_canonical_jsonl_bytes(
            (extraction_root / "attempts.jsonl").read_bytes(), label="attempts"
        )
        observations = _parse_canonical_jsonl_bytes(
            (extraction_root / "observations.jsonl").read_bytes(), label="observations"
        )
        validate_generic_extraction_artifacts(
            snapshot=snapshot,
            build=build,
            run=run,
            units=units,
            unit_results=unit_results,
            attempts=attempts,
            observations=observations,
            coverage=coverage,
            profile=profile,
            catalog=catalog,
            store=store,
            root=root,
        )
        paths = GenericRunPaths(
            shared_root=work_dir / "generic-extraction",
            snapshot_path=snapshot_path,
            profile_root=profile_root,
            extraction_root=extraction_root,
            extraction_build_path=extraction_root / "extraction-build.json",
            extraction_run_path=extraction_root / "extraction-run.json",
            units_path=extraction_root / "units.jsonl",
            unit_results_path=extraction_root / "unit-results.jsonl",
            attempts_path=extraction_root / "attempts.jsonl",
            observations_path=extraction_root / "observations.jsonl",
            checkpoint_path=extraction_root / "checkpoint.json",
        )
        extraction = GenericExtractionResult(
            catalog=catalog,
            store=store,
            ingestion=ingestion,
            snapshot=snapshot,
            profile=profile,
            build=build,
            run=run,
            units=units,
            unit_results=unit_results,
            attempts=attempts,
            observations=observations,
            paths=paths,
            reused_extraction=True,
        )
        reduction_roots = sorted((extraction_root / "reductions").glob("RRUN-*"))
        for reduction_root in reduction_roots:
            reduction_run = _parse_canonical_json_bytes(
                (reduction_root / "reduction-run.json").read_bytes(),
                label="ReductionRun",
            )
            corpus_snapshot = _parse_canonical_json_bytes(
                (reduction_root / "corpus-snapshot.json").read_bytes(),
                label="CorpusSnapshot",
            )
            records = _parse_canonical_jsonl_bytes(
                (reduction_root / "corpus.jsonl").read_bytes(), label="corpus"
            )
            validate_generic_corpus(
                extraction=extraction,
                reduction_run=reduction_run,
                corpus_snapshot=corpus_snapshot,
                records=records,
                root=root,
            )
            results.append(
                GenericCorpusResult(
                    extraction=extraction,
                    reduction_run=reduction_run,
                    corpus_snapshot=corpus_snapshot,
                    corpus_records=records,
                    reduction_root=reduction_root,
                    reduction_run_path=reduction_root / "reduction-run.json",
                    corpus_path=reduction_root / "corpus.jsonl",
                    corpus_snapshot_path=reduction_root / "corpus-snapshot.json",
                )
            )
    if not results:
        raise ValidationError("E-GENERIC-VALIDATE", "no completed CorpusSnapshot found")
    return results
