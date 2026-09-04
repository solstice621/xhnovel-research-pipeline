"""Prepare and compile the runtime-only Experiment B geography gold set.

This is deliberately spike tooling, not a second extraction path.  ``prepare``
copies only source material already embedded in frozen native agent tasks into
content-bound source packets.  ``validate`` and ``derive`` consume blind labels,
while ``freeze`` and ``validate-frozen`` enforce the human-acceptance transition.
No command inspects executor answers or extraction observations.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import tempfile
from dataclasses import dataclass
from typing import Any, Iterable

from jsonschema import Draft202012Validator

from xhnovel_pipeline.canonical import canonical_dumps
from xhnovel_pipeline.hashing import artifact_id_for, is_real_sha256, object_hash
from xhnovel_pipeline.paths import repo_root


SOURCE_SCHEMA_VERSION = "geography-gold-source/v1"
LABEL_SCHEMA_VERSION = "geography-gold-label/v1"
REVIEW_SCHEMA_VERSION = "geography-gold-review/v1"
ANNOTATION_SCHEMA_VERSION = "geography-gold-annotation/v1"
UNIQUE_SCHEMA_VERSION = "geography-gold-unique/v1"
GOLD_MANIFEST_SCHEMA_VERSION = "geography-gold-manifest/v1"
AGENT_PROTOCOL = "xhnovel-generic-agent-files-v1"

class GoldValidationError(Exception):
    """A reproducible, operator-facing failure in the spike workflow."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class GoldInputs:
    sample: dict[str, Any]
    packets: dict[str, dict[str, Any]]
    labels: list[dict[str, Any]]
    review: dict[str, Any]
    annotations: list[dict[str, Any]]
    unique_rows: list[dict[str, Any]]
    incomplete_unit_ids: tuple[str, ...]


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise GoldValidationError("E-GOLD-JSON", f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise GoldValidationError("E-GOLD-JSON", f"non-finite number {value!r} is forbidden")


def _decode_json(data: bytes, *, label: str) -> Any:
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_json_object,
            parse_constant=_reject_constant,
        )
    except GoldValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GoldValidationError("E-GOLD-JSON", f"{label} is not UTF-8 JSON") from exc


def _read_json(path: pathlib.Path, *, label: str) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise GoldValidationError("E-GOLD-INPUT", f"cannot read {label}: {path}") from exc
    value = _decode_json(data, label=label)
    if not isinstance(value, dict):
        raise GoldValidationError("E-GOLD-JSON", f"{label} must be a JSON object")
    return value


def _parse_canonical_json(data: bytes, *, label: str) -> dict[str, Any]:
    value = _decode_json(data, label=label)
    try:
        encoded = canonical_dumps(value)
    except (TypeError, ValueError, UnicodeError) as exc:
        raise GoldValidationError("E-GOLD-CANONICAL", f"{label} is not canonical JSON") from exc
    if not isinstance(value, dict) or encoded != data:
        raise GoldValidationError("E-GOLD-CANONICAL", f"{label} is not canonical JSON")
    return value


def _read_canonical_document(path: pathlib.Path, *, label: str) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise GoldValidationError("E-GOLD-INPUT", f"cannot read {label}: {path}") from exc
    if not data.endswith(b"\n"):
        raise GoldValidationError("E-GOLD-CANONICAL", f"{label} lacks its canonical newline")
    value = _parse_canonical_json(data[:-1], label=label)
    if canonical_dumps(value) + b"\n" != data:
        raise GoldValidationError("E-GOLD-CANONICAL", f"{label} is not canonical JSON")
    return value


def _read_canonical_jsonl(path: pathlib.Path, *, label: str) -> tuple[bytes, list[dict[str, Any]]]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise GoldValidationError("E-GOLD-INPUT", f"cannot read {label}: {path}") from exc
    if not data:
        return data, []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(data.splitlines(keepends=True), start=1):
        if not line.endswith(b"\n"):
            raise GoldValidationError(
                "E-GOLD-CANONICAL", f"{label} line {line_number} lacks a newline"
            )
        raw = line[:-1]
        value = _parse_canonical_json(raw, label=f"{label} line {line_number}")
        rows.append(value)
    return data, rows


def _canonical_jsonl(rows: Iterable[dict[str, Any]]) -> bytes:
    return b"".join(canonical_dumps(row) + b"\n" for row in rows)


def _write_immutable(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != data:
            raise GoldValidationError(
                "E-GOLD-IMMUTABLE", f"refusing to overwrite different content: {path}"
            )
        return
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_name, path)
        except FileExistsError:
            if not path.is_file() or path.read_bytes() != data:
                raise GoldValidationError(
                    "E-GOLD-IMMUTABLE", f"refusing to overwrite different content: {path}"
                )
    finally:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass


def merge_label_drafts(
    *,
    sample_path: pathlib.Path,
    input_paths: list[pathlib.Path],
    output_path: pathlib.Path,
    label_schema_path: pathlib.Path | None = None,
) -> list[dict[str, Any]]:
    if not input_paths:
        raise GoldValidationError("E-GOLD-INPUT", "at least one draft input is required")
    sample = _read_json(sample_path, label="sample manifest")
    _validate_sample(sample)
    label_schema = _load_schema(
        label_schema_path or sample_path.parent / "geography-gold-label.schema.json",
        label="gold label schema",
    )
    sample_order = {unit["unit_id"]: index for index, unit in enumerate(sample["units"])}
    rows: list[dict[str, Any]] = []
    seen: set[bytes] = set()
    for input_path in input_paths:
        _, input_rows = _read_canonical_jsonl(input_path, label=f"draft labels {input_path}")
        for index, row in enumerate(input_rows, start=1):
            _schema_validate(label_schema, row, label=f"{input_path} line {index}")
            if row.get("sample_id") != sample["sample_id"]:
                raise GoldValidationError(
                    "E-GOLD-LABEL", f"sample_id differs in {input_path} line {index}"
                )
            if row.get("unit_id") not in sample_order:
                raise GoldValidationError(
                    "E-GOLD-LABEL", f"unknown unit_id in {input_path} line {index}"
                )
            encoded = canonical_dumps(row)
            if encoded in seen:
                raise GoldValidationError(
                    "E-GOLD-LABEL", f"duplicate label across draft inputs at {input_path} line {index}"
                )
            seen.add(encoded)
            rows.append(row)
    rows.sort(key=lambda row: (sample_order[row["unit_id"]], canonical_dumps(row)))
    _write_immutable(output_path, _canonical_jsonl(rows))
    return rows


def _exact_keys(value: dict[str, Any], expected: set[str], *, label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise GoldValidationError(
            "E-GOLD-SCHEMA", f"{label} fields differ; missing={missing}, extra={extra}"
        )


def _require_hash(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:") or not is_real_sha256(value):
        raise GoldValidationError("E-GOLD-HASH", f"{label} is not a real sha256 identity")
    return value


def _derived_id(prefix: str, payload: dict[str, Any]) -> str:
    digest = object_hash(payload, omit=()).removeprefix("sha256:")
    return f"{prefix}{digest[:20].upper()}"


def _validate_sample(sample: dict[str, Any]) -> None:
    _exact_keys(
        sample,
        {
            "schema_version",
            "sample_id",
            "status",
            "protocol_version",
            "baseline",
            "selection",
            "source_packet",
            "units",
        },
        label="sample",
    )
    if sample["schema_version"] != "geography-gold-sample/v1":
        raise GoldValidationError("E-GOLD-SAMPLE", "unsupported sample schema_version")
    if sample["status"] != "FROZEN_SAMPLE" or sample["protocol_version"] != "geography-gold/v1":
        raise GoldValidationError("E-GOLD-SAMPLE", "sample is not the frozen v1 protocol")
    sample_id = sample["sample_id"]
    if sample_id != "GEOGOLD-B-20260904":
        raise GoldValidationError("E-GOLD-SAMPLE", "unexpected frozen sample_id")

    baseline = sample["baseline"]
    if not isinstance(baseline, dict):
        raise GoldValidationError("E-GOLD-SAMPLE", "baseline must be an object")
    _exact_keys(
        baseline,
        {
            "engine_commit",
            "evidence_commit",
            "extraction_build_id",
            "extraction_build_hash",
            "profile_id",
            "profile_version",
            "extraction_profile_hash",
            "text_snapshot_id",
            "text_snapshot_hash",
            "work_id",
            "ingestion_run_id",
            "input_spec_artifact_id",
            "input_spec_hash",
            "eligible_character_count",
            "chapter_count",
            "document_count",
            "segment_count",
            "unit_policy",
        },
        label="sample.baseline",
    )
    for field in (
        "extraction_build_hash",
        "extraction_profile_hash",
        "text_snapshot_hash",
        "input_spec_artifact_id",
        "input_spec_hash",
    ):
        _require_hash(baseline[field], label=f"sample.baseline.{field}")
    for field in (
        "eligible_character_count",
        "chapter_count",
        "document_count",
        "segment_count",
    ):
        if (
            not isinstance(baseline[field], int)
            or isinstance(baseline[field], bool)
            or baseline[field] < 1
        ):
            raise GoldValidationError("E-GOLD-SAMPLE", f"sample.baseline.{field} is invalid")
    if baseline["profile_id"] != "xhnovel.geography" or baseline["profile_version"] != "1.0.0":
        raise GoldValidationError("E-GOLD-SAMPLE", "sample is not the geography-v1 baseline")
    expected_snapshot_id = _derived_id(
        "NTS-", {"text_snapshot_hash": baseline["text_snapshot_hash"]}
    )
    if baseline["text_snapshot_id"] != expected_snapshot_id:
        raise GoldValidationError(
            "E-GOLD-SNAPSHOT", "text_snapshot_id does not close over text_snapshot_hash"
        )
    policy = baseline["unit_policy"]
    if not isinstance(policy, dict):
        raise GoldValidationError("E-GOLD-SAMPLE", "unit_policy must be an object")
    _exact_keys(policy, {"id", "target_chars", "overlap_chars"}, label="unit_policy")
    if (
        policy["id"] != "sliding-text/v1"
        or not isinstance(policy["target_chars"], int)
        or isinstance(policy["target_chars"], bool)
        or not isinstance(policy["overlap_chars"], int)
        or isinstance(policy["overlap_chars"], bool)
        or policy["target_chars"] < 1
        or not 0 <= policy["overlap_chars"] < policy["target_chars"]
    ):
        raise GoldValidationError("E-GOLD-SAMPLE", "invalid baseline unit policy")

    packet_contract = sample["source_packet"]
    if not isinstance(packet_contract, dict):
        raise GoldValidationError("E-GOLD-SAMPLE", "source_packet must be an object")
    _exact_keys(
        packet_contract,
        {"schema_version", "canonicalization", "text_artifact", "repository_storage"},
        label="sample.source_packet",
    )
    if (
        packet_contract["schema_version"] != SOURCE_SCHEMA_VERSION
        or packet_contract["canonicalization"] != "xhnovel canonical JSON"
        or packet_contract["text_artifact"]
        != "sha256 of concatenated UTF-8 untrusted_text in source-span order"
        or packet_contract["repository_storage"] != "PROHIBITED"
    ):
        raise GoldValidationError("E-GOLD-SAMPLE", "unsupported source packet contract")

    selection = sample["selection"]
    if not isinstance(selection, dict):
        raise GoldValidationError("E-GOLD-SAMPLE", "selection must be an object")
    _exact_keys(
        selection,
        {
            "source_manifest",
            "source_seed",
            "required_ordinals",
            "random_control_rule",
            "random_control_ordinals",
        },
        label="sample.selection",
    )
    if (
        not isinstance(selection["source_manifest"], str)
        or not selection["source_manifest"]
        or not isinstance(selection["random_control_rule"], str)
        or not selection["random_control_rule"]
        or not isinstance(selection["source_seed"], int)
        or isinstance(selection["source_seed"], bool)
    ):
        raise GoldValidationError("E-GOLD-SAMPLE", "invalid selection provenance")
    for field in ("required_ordinals", "random_control_ordinals"):
        values = selection[field]
        if (
            not isinstance(values, list)
            or any(
                not isinstance(value, int) or isinstance(value, bool) or value < 1
                for value in values
            )
            or len(values) != len(set(values))
        ):
            raise GoldValidationError("E-GOLD-SAMPLE", f"invalid selection {field}")
    if set(selection["required_ordinals"]) & set(selection["random_control_ordinals"]):
        raise GoldValidationError("E-GOLD-SAMPLE", "required and control ordinals overlap")

    units = sample["units"]
    if not isinstance(units, list) or not units:
        raise GoldValidationError("E-GOLD-SAMPLE", "sample.units must be non-empty")
    seen_ids: set[str] = set()
    seen_ordinals: set[int] = set()
    for index, unit in enumerate(units):
        if not isinstance(unit, dict):
            raise GoldValidationError("E-GOLD-SAMPLE", f"sample unit {index} must be an object")
        _exact_keys(
            unit,
            {
                "ordinal",
                "unit_id",
                "unit_hash",
                "selection",
                "stratum",
                "semantic_task_artifact_id",
                "agent_request_artifact_id",
                "source_packet_hash",
                "unit_text_artifact_id",
                "text_length",
                "source_span_count",
            },
            label=f"sample.units[{index}]",
        )
        unit_id = unit["unit_id"]
        ordinal = unit["ordinal"]
        if not isinstance(unit_id, str) or not re.fullmatch(r"XUNIT-[A-Z0-9]{20}", unit_id):
            raise GoldValidationError("E-GOLD-SAMPLE", f"invalid unit_id at sample index {index}")
        if unit_id in seen_ids:
            raise GoldValidationError("E-GOLD-SAMPLE", f"duplicate sample unit_id {unit_id}")
        if (
            not isinstance(ordinal, int)
            or isinstance(ordinal, bool)
            or ordinal < 1
            or ordinal in seen_ordinals
        ):
            raise GoldValidationError("E-GOLD-SAMPLE", f"invalid or duplicate ordinal {ordinal!r}")
        seen_ids.add(unit_id)
        seen_ordinals.add(ordinal)
        for field in (
            "unit_hash",
            "semantic_task_artifact_id",
            "agent_request_artifact_id",
            "source_packet_hash",
            "unit_text_artifact_id",
        ):
            _require_hash(unit[field], label=f"{unit_id}.{field}")
        if (
            not isinstance(unit["text_length"], int)
            or isinstance(unit["text_length"], bool)
            or unit["text_length"] < 1
            or not isinstance(unit["source_span_count"], int)
            or isinstance(unit["source_span_count"], bool)
            or unit["source_span_count"] < 1
        ):
            raise GoldValidationError("E-GOLD-SAMPLE", f"invalid sizes for {unit_id}")
    selected_ordinals = set(selection["required_ordinals"]) | set(
        selection["random_control_ordinals"]
    )
    if seen_ordinals != selected_ordinals:
        raise GoldValidationError(
            "E-GOLD-SAMPLE", "selection ordinals do not exactly match sample units"
        )
    random_row_ordinals = {
        unit["ordinal"] for unit in units if unit["selection"] == "random-control"
    }
    if random_row_ordinals != set(selection["random_control_ordinals"]):
        raise GoldValidationError(
            "E-GOLD-SAMPLE", "random-control rows do not match frozen control ordinals"
        )


def _validate_snapshot(snapshot: dict[str, Any], sample: dict[str, Any]) -> set[str]:
    snapshot_schema = _load_schema(
        repo_root() / "contracts" / "generic" / "novel-text-snapshot.schema.json",
        label="NovelTextSnapshot schema",
    )
    _schema_validate(snapshot_schema, snapshot, label="NovelTextSnapshot")
    _exact_keys(
        snapshot,
        {
            "schema_version",
            "record_kind",
            "work_id",
            "ingestion_run_id",
            "input_spec_artifact_id",
            "input_spec_hash",
            "chapter_ids",
            "document_ids",
            "segment_ids",
            "source_quality_tier",
            "coverage_use",
            "eligible_character_count",
            "created_at",
            "status",
            "text_snapshot_id",
            "text_snapshot_hash",
        },
        label="NovelTextSnapshot",
    )
    if (
        snapshot["schema_version"] != "novel-text-snapshot/v1"
        or snapshot["record_kind"] != "NOVEL_TEXT_SNAPSHOT"
        or snapshot["status"] != "FROZEN"
        or snapshot["coverage_use"] != "source-grounded-semantic-extraction/v0-spike"
        or snapshot["source_quality_tier"] not in {"A", "B"}
    ):
        raise GoldValidationError("E-GOLD-SNAPSHOT", "snapshot contract fields differ")
    for field in ("input_spec_artifact_id", "input_spec_hash", "text_snapshot_hash"):
        _require_hash(snapshot[field], label=f"snapshot.{field}")
    for field in ("chapter_ids", "document_ids", "segment_ids"):
        values = snapshot[field]
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value for value in values)
            or len(values) != len(set(values))
        ):
            raise GoldValidationError("E-GOLD-SNAPSHOT", f"snapshot.{field} is invalid")
    eligible_count = snapshot["eligible_character_count"]
    if (
        not isinstance(eligible_count, int)
        or isinstance(eligible_count, bool)
        or eligible_count < 1
    ):
        raise GoldValidationError("E-GOLD-SNAPSHOT", "snapshot eligible_character_count is invalid")
    expected_hash = object_hash(
        snapshot,
        omit=("text_snapshot_id", "text_snapshot_hash"),
    )
    if snapshot["text_snapshot_hash"] != expected_hash:
        raise GoldValidationError("E-GOLD-SNAPSHOT", "NovelTextSnapshot body hash differs")
    if snapshot["text_snapshot_id"] != _derived_id(
        "NTS-", {"text_snapshot_hash": expected_hash}
    ):
        raise GoldValidationError("E-GOLD-SNAPSHOT", "NovelTextSnapshot id differs")

    baseline = sample["baseline"]
    for field in (
        "text_snapshot_id",
        "text_snapshot_hash",
        "work_id",
        "ingestion_run_id",
        "input_spec_artifact_id",
        "input_spec_hash",
    ):
        if snapshot[field] != baseline[field]:
            raise GoldValidationError(
                "E-GOLD-SNAPSHOT", f"snapshot {field} differs from frozen sample"
            )
    count_pairs = (
        ("eligible_character_count", snapshot["eligible_character_count"]),
        ("chapter_count", len(snapshot["chapter_ids"])),
        ("document_count", len(snapshot["document_ids"])),
        ("segment_count", len(snapshot["segment_ids"])),
    )
    for field, actual in count_pairs:
        if baseline[field] != actual:
            raise GoldValidationError(
                "E-GOLD-SNAPSHOT", f"snapshot {field} differs from frozen sample"
            )
    return set(snapshot["segment_ids"])


def _load_schema(path: pathlib.Path, *, label: str) -> dict[str, Any]:
    schema = _read_json(path, label=label)
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:  # jsonschema exposes several version-specific subclasses.
        raise GoldValidationError("E-GOLD-SCHEMA", f"invalid {label}: {path}") from exc
    return schema


def _schema_validate(schema: dict[str, Any], value: dict[str, Any], *, label: str) -> None:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda error: (tuple(str(part) for part in error.absolute_path), error.message),
    )
    if errors:
        error = errors[0]
        raise GoldValidationError(
            "E-GOLD-SCHEMA", f"{label}: {error.message} at {list(error.absolute_path)}"
        )


def _semantic_task(task: dict[str, Any]) -> dict[str, Any]:
    output = task.get("output")
    if not isinstance(output, dict):
        raise GoldValidationError("E-GOLD-TASK", "agent task output must be an object")
    return {
        "instructions": task.get("instructions"),
        "input": task.get("input"),
        "schema_name": output.get("schema_name"),
        "schema": output.get("schema"),
    }


def _source_spans_from_task(
    task: dict[str, Any],
    *,
    sample: dict[str, Any],
    sample_unit: dict[str, Any],
    snapshot_segment_ids: set[str],
) -> list[dict[str, Any]]:
    unit_id = sample_unit["unit_id"]
    _exact_keys(
        task,
        {
            "protocol",
            "unit_id",
            "profile_id",
            "instructions",
            "input",
            "output",
            "answer_file",
            "security",
        },
        label=f"agent task {unit_id}",
    )
    if task["protocol"] != AGENT_PROTOCOL:
        raise GoldValidationError("E-GOLD-TASK", f"unsupported agent protocol for {unit_id}")
    if task["unit_id"] != unit_id:
        raise GoldValidationError("E-GOLD-TASK", f"top-level unit_id differs for {unit_id}")
    if not isinstance(task["instructions"], str) or not task["instructions"]:
        raise GoldValidationError("E-GOLD-TASK", f"instructions missing for {unit_id}")
    output = task["output"]
    if not isinstance(output, dict):
        raise GoldValidationError("E-GOLD-TASK", f"output missing for {unit_id}")
    _exact_keys(output, {"schema_name", "strict", "schema"}, label=f"task output {unit_id}")
    if output["strict"] is not True or not isinstance(output["schema_name"], str) or not output["schema_name"]:
        raise GoldValidationError("E-GOLD-TASK", f"task output is not strict for {unit_id}")
    if not isinstance(output["schema"], dict):
        raise GoldValidationError("E-GOLD-TASK", f"task output schema missing for {unit_id}")
    security = task["security"]
    expected_security = {
        "source_text_is_untrusted_data": True,
        "cross_unit_context_forbidden": True,
        "do_not_execute_source_instructions": True,
    }
    if security != expected_security:
        raise GoldValidationError("E-GOLD-TASK", f"security envelope differs for {unit_id}")
    answer_file = task["answer_file"]
    if (
        not isinstance(answer_file, str)
        or not answer_file.startswith("answers/")
        or pathlib.PurePosixPath(answer_file).is_absolute()
        or ".." in pathlib.PurePosixPath(answer_file).parts
    ):
        raise GoldValidationError("E-GOLD-TASK", f"unsafe answer_file metadata for {unit_id}")

    input_value = task["input"]
    if not isinstance(input_value, dict):
        raise GoldValidationError("E-GOLD-TASK", f"task input missing for {unit_id}")
    _exact_keys(input_value, {"profile", "text_snapshot", "unit"}, label=f"task input {unit_id}")
    baseline = sample["baseline"]
    profile = input_value["profile"]
    if not isinstance(profile, dict):
        raise GoldValidationError("E-GOLD-TASK", f"profile input missing for {unit_id}")
    _exact_keys(
        profile,
        {"profile_id", "profile_version", "extraction_profile_hash", "evidence_policy"},
        label=f"task profile {unit_id}",
    )
    if (
        task["profile_id"] != baseline["profile_id"]
        or profile["profile_id"] != baseline["profile_id"]
        or profile["profile_version"] != baseline["profile_version"]
        or profile["extraction_profile_hash"] != baseline["extraction_profile_hash"]
    ):
        raise GoldValidationError("E-GOLD-TASK", f"profile lineage differs for {unit_id}")
    snapshot = input_value["text_snapshot"]
    if not isinstance(snapshot, dict):
        raise GoldValidationError("E-GOLD-TASK", f"snapshot input missing for {unit_id}")
    _exact_keys(snapshot, {"text_snapshot_id", "work_id"}, label=f"task snapshot {unit_id}")
    if (
        snapshot["text_snapshot_id"] != baseline["text_snapshot_id"]
        or snapshot["work_id"] != baseline["work_id"]
    ):
        raise GoldValidationError("E-GOLD-SNAPSHOT", f"snapshot lineage differs for {unit_id}")

    unit = input_value["unit"]
    if not isinstance(unit, dict):
        raise GoldValidationError("E-GOLD-TASK", f"unit input missing for {unit_id}")
    _exact_keys(unit, {"unit_id", "ordinal", "source_spans"}, label=f"task unit {unit_id}")
    if unit["unit_id"] != unit_id or unit["ordinal"] != sample_unit["ordinal"]:
        raise GoldValidationError("E-GOLD-UNIT", f"unit identity differs for {unit_id}")
    spans = unit["source_spans"]
    if not isinstance(spans, list) or len(spans) != sample_unit["source_span_count"]:
        raise GoldValidationError("E-GOLD-SOURCE", f"source span count differs for {unit_id}")
    seen_ranges: set[tuple[str, int, int]] = set()
    text_length = 0
    clean_spans: list[dict[str, Any]] = []
    for span_index, span in enumerate(spans):
        if not isinstance(span, dict):
            raise GoldValidationError("E-GOLD-SOURCE", f"span {span_index} is not an object")
        _exact_keys(
            span,
            {"segment_id", "start", "end", "normalized_text_hash", "untrusted_text"},
            label=f"{unit_id} source span {span_index}",
        )
        segment_id = span["segment_id"]
        start = span["start"]
        end = span["end"]
        text = span["untrusted_text"]
        if (
            not isinstance(segment_id, str)
            or not segment_id.startswith("SEG-")
            or not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or not 0 <= start < end
            or not isinstance(text, str)
            or len(text) != end - start
        ):
            raise GoldValidationError("E-GOLD-SOURCE", f"invalid source span {span_index} for {unit_id}")
        _require_hash(span["normalized_text_hash"], label=f"{unit_id} span hash")
        if segment_id not in snapshot_segment_ids:
            raise GoldValidationError(
                "E-GOLD-SNAPSHOT", f"{unit_id} references a segment outside NovelTextSnapshot"
            )
        range_key = (segment_id, start, end)
        if range_key in seen_ranges:
            raise GoldValidationError("E-GOLD-SOURCE", f"duplicate source span for {unit_id}")
        seen_ranges.add(range_key)
        text_length += len(text)
        clean_spans.append(dict(span))
    if text_length != sample_unit["text_length"]:
        raise GoldValidationError("E-GOLD-SOURCE", f"unit text length differs for {unit_id}")

    policy = baseline["unit_policy"]
    identity = {
        "schema_version": "extraction-unit/v1",
        "text_snapshot_id": baseline["text_snapshot_id"],
        "unit_policy_id": policy["id"],
        "unit_policy_hash": object_hash(policy, omit=()),
        "ordinal": sample_unit["ordinal"],
        "source_spans": [
            {key: span[key] for key in ("segment_id", "start", "end", "normalized_text_hash")}
            for span in clean_spans
        ],
        "text_length": text_length,
    }
    unit_hash = object_hash(identity, omit=())
    expected_unit_id = _derived_id("XUNIT-", {"unit_hash": unit_hash})
    if unit_hash != sample_unit["unit_hash"] or expected_unit_id != unit_id:
        raise GoldValidationError("E-GOLD-UNIT", f"unit hash closure differs for {unit_id}")
    return clean_spans


def _task_path(tasks_dir: pathlib.Path, unit_id: str) -> pathlib.Path:
    matches = sorted(tasks_dir.glob(f"{unit_id}--*.json"))
    if len(matches) != 1 or not matches[0].is_file():
        raise GoldValidationError(
            "E-GOLD-TASK", f"expected exactly one native task for {unit_id}; found {len(matches)}"
        )
    return matches[0]


def _packet_from_task(
    *,
    sample: dict[str, Any],
    sample_unit: dict[str, Any],
    tasks_dir: pathlib.Path,
    snapshot_segment_ids: set[str],
) -> dict[str, Any]:
    unit_id = sample_unit["unit_id"]
    path = _task_path(tasks_dir, unit_id)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise GoldValidationError("E-GOLD-TASK", f"cannot read native task: {path}") from exc
    if artifact_id_for(raw) != sample_unit["agent_request_artifact_id"]:
        raise GoldValidationError("E-GOLD-TASK-HASH", f"agent request artifact differs for {unit_id}")
    task = _parse_canonical_json(raw, label=f"native task {unit_id}")
    if artifact_id_for(canonical_dumps(_semantic_task(task))) != sample_unit["semantic_task_artifact_id"]:
        raise GoldValidationError("E-GOLD-TASK-HASH", f"semantic task artifact differs for {unit_id}")
    spans = _source_spans_from_task(
        task,
        sample=sample,
        sample_unit=sample_unit,
        snapshot_segment_ids=snapshot_segment_ids,
    )
    packet = {
        "schema_version": SOURCE_SCHEMA_VERSION,
        "text_snapshot_id": sample["baseline"]["text_snapshot_id"],
        "text_snapshot_hash": sample["baseline"]["text_snapshot_hash"],
        "unit_id": unit_id,
        "unit_hash": sample_unit["unit_hash"],
        "ordinal": sample_unit["ordinal"],
        "source_spans": spans,
    }
    if object_hash(packet, omit=()) != sample_unit["source_packet_hash"]:
        raise GoldValidationError("E-GOLD-SOURCE-HASH", f"source packet hash differs for {unit_id}")
    unit_text = "".join(span["untrusted_text"] for span in spans).encode("utf-8")
    if artifact_id_for(unit_text) != sample_unit["unit_text_artifact_id"]:
        raise GoldValidationError("E-GOLD-SOURCE-HASH", f"unit text artifact differs for {unit_id}")
    return packet


def prepare_source_packets(
    sample_path: pathlib.Path,
    snapshot_path: pathlib.Path,
    tasks_dir: pathlib.Path,
    output_dir: pathlib.Path,
) -> list[pathlib.Path]:
    sample = _read_json(sample_path, label="sample manifest")
    _validate_sample(sample)
    snapshot = _read_canonical_document(snapshot_path, label="NovelTextSnapshot")
    snapshot_segment_ids = _validate_snapshot(snapshot, sample)
    if not tasks_dir.is_dir():
        raise GoldValidationError("E-GOLD-TASK", f"tasks directory does not exist: {tasks_dir}")
    paths: list[pathlib.Path] = []
    for sample_unit in sample["units"]:
        packet = _packet_from_task(
            sample=sample,
            sample_unit=sample_unit,
            tasks_dir=tasks_dir,
            snapshot_segment_ids=snapshot_segment_ids,
        )
        path = output_dir / f"{sample_unit['unit_id']}.json"
        _write_immutable(path, canonical_dumps(packet))
        paths.append(path)
    return paths


def _load_source_packets(
    sample: dict[str, Any], source_dir: pathlib.Path
) -> dict[str, dict[str, Any]]:
    packets: dict[str, dict[str, Any]] = {}
    baseline = sample["baseline"]
    for sample_unit in sample["units"]:
        unit_id = sample_unit["unit_id"]
        path = source_dir / f"{unit_id}.json"
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise GoldValidationError("E-GOLD-SOURCE", f"missing source packet for {unit_id}") from exc
        packet = _parse_canonical_json(raw, label=f"source packet {unit_id}")
        _exact_keys(
            packet,
            {
                "schema_version",
                "text_snapshot_id",
                "text_snapshot_hash",
                "unit_id",
                "unit_hash",
                "ordinal",
                "source_spans",
            },
            label=f"source packet {unit_id}",
        )
        if (
            packet["schema_version"] != SOURCE_SCHEMA_VERSION
            or packet["text_snapshot_id"] != baseline["text_snapshot_id"]
            or packet["text_snapshot_hash"] != baseline["text_snapshot_hash"]
            or packet["unit_id"] != unit_id
            or packet["unit_hash"] != sample_unit["unit_hash"]
            or packet["ordinal"] != sample_unit["ordinal"]
            or object_hash(packet, omit=()) != sample_unit["source_packet_hash"]
            or artifact_id_for(raw) != sample_unit["source_packet_hash"]
        ):
            raise GoldValidationError("E-GOLD-SOURCE-HASH", f"source packet differs for {unit_id}")
        spans = packet["source_spans"]
        if not isinstance(spans, list) or len(spans) != sample_unit["source_span_count"]:
            raise GoldValidationError("E-GOLD-SOURCE", f"source span count differs for {unit_id}")
        text_parts: list[str] = []
        length = 0
        for span_index, span in enumerate(spans):
            if not isinstance(span, dict):
                raise GoldValidationError("E-GOLD-SOURCE", f"invalid packet span for {unit_id}")
            _exact_keys(
                span,
                {"segment_id", "start", "end", "normalized_text_hash", "untrusted_text"},
                label=f"source packet {unit_id} span {span_index}",
            )
            text = span["untrusted_text"]
            if (
                not isinstance(text, str)
                or not isinstance(span["start"], int)
                or isinstance(span["start"], bool)
                or not isinstance(span["end"], int)
                or isinstance(span["end"], bool)
                or not 0 <= span["start"] < span["end"]
                or len(text) != span["end"] - span["start"]
            ):
                raise GoldValidationError("E-GOLD-SOURCE", f"invalid packet span for {unit_id}")
            _require_hash(span["normalized_text_hash"], label=f"{unit_id} packet span hash")
            text_parts.append(text)
            length += len(text)
        if length != sample_unit["text_length"]:
            raise GoldValidationError("E-GOLD-SOURCE", f"packet text length differs for {unit_id}")
        if artifact_id_for("".join(text_parts).encode("utf-8")) != sample_unit["unit_text_artifact_id"]:
            raise GoldValidationError("E-GOLD-SOURCE-HASH", f"unit text differs for {unit_id}")
        packets[unit_id] = packet
    return packets


def _pointer_exists(payload: dict[str, Any], pointer: str) -> bool:
    if not isinstance(pointer, str) or not pointer.startswith("/") or pointer == "/":
        return False
    current: Any = payload
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if "~" in raw_token.replace("~0", "").replace("~1", ""):
            return False
        if not isinstance(current, dict) or token not in current:
            return False
        current = current[token]
    return True


def _binding_covers(binding_path: str, required_path: str) -> bool:
    return binding_path == required_path or required_path.startswith(binding_path + "/")


def _materialize_span(
    source_span: dict[str, Any], packet: dict[str, Any], *, label: str
) -> tuple[dict[str, Any], int, int]:
    matches: list[tuple[int, dict[str, Any]]] = []
    cursor = 0
    for container in packet["source_spans"]:
        if (
            source_span["segment_id"] == container["segment_id"]
            and container["start"] <= source_span["start"] < source_span["end"] <= container["end"]
        ):
            matches.append((cursor, container))
        cursor += len(container["untrusted_text"])
    if len(matches) != 1:
        raise GoldValidationError(
            "E-GOLD-SPAN", f"{label} must fall within exactly one frozen unit source span"
        )
    prefix, container = matches[0]
    materialized = {
        "segment_id": source_span["segment_id"],
        "start": source_span["start"],
        "end": source_span["end"],
        "normalized_text_hash": container["normalized_text_hash"],
    }
    return (
        materialized,
        prefix + source_span["start"] - container["start"],
        prefix + source_span["end"] - container["start"],
    )


def _validated_span_positions(
    spans: list[dict[str, Any]], packet: dict[str, Any], *, label: str
) -> list[tuple[int, int]]:
    positions: list[tuple[int, int]] = []
    seen: set[bytes] = set()
    for index, span in enumerate(spans):
        if not isinstance(span, dict):
            raise GoldValidationError("E-GOLD-SPAN", f"{label} span {index} must be an object")
        canonical = canonical_dumps(span)
        if canonical in seen:
            raise GoldValidationError("E-GOLD-SPAN", f"{label} contains a duplicate source span")
        seen.add(canonical)
        _, start, end = _materialize_span(span, packet, label=f"{label} span {index}")
        positions.append((start, end))
    return positions


def _validate_include_label(
    label: dict[str, Any], packet: dict[str, Any], *, label_name: str
) -> tuple[int, int, list[dict[str, Any]]]:
    payload = label["payload"]
    bindings = label["evidence_bindings"]
    unit_text = "".join(item["untrusted_text"] for item in packet["source_spans"])
    all_positions: list[tuple[int, int]] = []
    compiled_bindings: list[dict[str, Any]] = []
    for binding_index, binding in enumerate(bindings):
        paths = binding["paths"]
        if len(paths) != len(set(paths)):
            raise GoldValidationError("E-GOLD-EVIDENCE", f"{label_name} has duplicate paths")
        for pointer in paths:
            if not _pointer_exists(payload, pointer):
                raise GoldValidationError(
                    "E-GOLD-EVIDENCE", f"{label_name} path does not exist in payload: {pointer!r}"
                )
        compiled_spans: list[dict[str, Any]] = []
        cited_text_parts: list[str] = []
        seen_spans: set[bytes] = set()
        for span_index, span in enumerate(binding["source_spans"]):
            span_bytes = canonical_dumps(span)
            if span_bytes in seen_spans:
                raise GoldValidationError(
                    "E-GOLD-SPAN", f"{label_name} binding {binding_index} has a duplicate span"
                )
            seen_spans.add(span_bytes)
            materialized, start, end = _materialize_span(
                span,
                packet,
                label=f"{label_name} binding {binding_index} span {span_index}",
            )
            compiled_spans.append(materialized)
            all_positions.append((start, end))
            cited_text_parts.append(unit_text[start:end])
        cited_text = "".join(cited_text_parts)
        for pointer in paths:
            if pointer in {"/kind", "/relation"}:
                continue
            field = pointer.removeprefix("/").replace("~1", "/").replace("~0", "~")
            value = payload.get(field)
            if isinstance(value, str) and value not in cited_text:
                raise GoldValidationError(
                    "E-GOLD-EVIDENCE",
                    f"{label_name} cited text does not contain exact value for {pointer}",
                )
        compiled_bindings.append({"paths": list(paths), "source_spans": compiled_spans})
    required = {
        f"/{key}" for key in payload if key != "kind"
    }
    required_groups = (
        (("/name",),)
        if payload["kind"] == "PLACE_MENTION"
        else (("/subject_name", "/relation", "/object_name"),)
    )
    for group in required_groups:
        if not any(
            all(
                any(_binding_covers(binding_path, required_path) for binding_path in binding["paths"])
                for required_path in group
            )
            for binding in bindings
        ):
            raise GoldValidationError(
                "E-GOLD-EVIDENCE",
                f"{label_name} lacks one binding covering required group {list(group)}",
            )
    bound = {
        required_path
        for required_path in required
        if any(
            _binding_covers(binding_path, required_path)
            for binding in bindings
            for binding_path in binding["paths"]
        )
    }
    if bound != required:
        raise GoldValidationError(
            "E-GOLD-EVIDENCE", f"{label_name} lacks evidence for {sorted(required - bound)}"
        )
    return (
        min(start for start, _ in all_positions),
        max(end for _, end in all_positions),
        compiled_bindings,
    )


def _unit_position(start: int, end: int, text_length: int) -> dict[str, Any]:
    if not 0 <= start < end <= text_length:
        raise GoldValidationError("E-GOLD-POSITION", "derived unit position is outside unit text")
    millionths = start * 1_000_000 // text_length
    bucket_index = min(3, start * 4 // text_length)
    return {
        "start": start,
        "end": end,
        "start_fraction_ppm": millionths,
        "bucket": ("Q1", "Q2", "Q3", "Q4")[bucket_index],
    }


def _compile_annotations(
    *,
    sample: dict[str, Any],
    packets: dict[str, dict[str, Any]],
    labels: list[dict[str, Any]],
    review_state: str,
    label_schema: dict[str, Any],
    annotation_schema: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, tuple[int, int]]]:
    sample_units = {unit["unit_id"]: unit for unit in sample["units"]}
    sample_order = {unit["unit_id"]: index for index, unit in enumerate(sample["units"])}
    seen_labels: set[bytes] = set()
    seen_occurrences: set[tuple[str, bytes, int, int]] = set()
    include_drafts: list[tuple[dict[str, Any], int, int, list[dict[str, Any]]]] = []
    counts = {unit_id: [0, 0] for unit_id in sample_units}
    for index, label in enumerate(labels, start=1):
        _schema_validate(label_schema, label, label=f"label line {index}")
        if label.get("schema_version") != LABEL_SCHEMA_VERSION:
            raise GoldValidationError("E-GOLD-LABEL", f"unsupported label line {index}")
        if label.get("sample_id") != sample["sample_id"]:
            raise GoldValidationError("E-GOLD-LABEL", f"sample_id differs at label line {index}")
        unit_id = label.get("unit_id")
        if unit_id not in sample_units:
            raise GoldValidationError("E-GOLD-LABEL", f"unknown unit_id at label line {index}")
        label_bytes = canonical_dumps(label)
        if label_bytes in seen_labels:
            raise GoldValidationError("E-GOLD-LABEL", f"duplicate label at line {index}")
        seen_labels.add(label_bytes)
        packet = packets[unit_id]
        if label["decision"] == "INCLUDE":
            counts[unit_id][0] += 1
            start, end, compiled_bindings = _validate_include_label(
                label, packet, label_name=f"label line {index}"
            )
            occurrence_key = (unit_id, canonical_dumps(label["payload"]), start, end)
            if occurrence_key in seen_occurrences:
                raise GoldValidationError(
                    "E-GOLD-LABEL",
                    f"duplicate payload occurrence at label line {index}",
                )
            seen_occurrences.add(occurrence_key)
            include_drafts.append((label, start, end, compiled_bindings))
        elif label["decision"] == "EXCLUDE":
            counts[unit_id][1] += 1
            _validated_span_positions(
                label["source_spans"], packet, label=f"excluded label line {index}"
            )
        else:  # The schema should catch this; retain an explicit fail-closed guard.
            raise GoldValidationError("E-GOLD-LABEL", f"unknown decision at label line {index}")

    include_drafts.sort(
        key=lambda item: (
            sample_order[item[0]["unit_id"]],
            item[1],
            canonical_dumps(item[0]["payload"]),
            canonical_dumps(item[3]),
        )
    )
    next_ordinal = {unit_id: 1 for unit_id in sample_units}
    annotations: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for label, start, end, compiled_bindings in include_drafts:
        unit_id = label["unit_id"]
        sample_unit = sample_units[unit_id]
        base = {
            "schema_version": ANNOTATION_SCHEMA_VERSION,
            "sample_id": sample["sample_id"],
            "annotation_state": review_state,
            "unit_id": unit_id,
            "unit_hash": sample_unit["unit_hash"],
            "ordinal": sample_unit["ordinal"],
            "occurrence_ordinal": next_ordinal[unit_id],
            "payload": label["payload"],
            "evidence_bindings": compiled_bindings,
            "unit_position": _unit_position(start, end, sample_unit["text_length"]),
        }
        next_ordinal[unit_id] += 1
        annotation_identity = {
            key: value
            for key, value in base.items()
            if key not in {"annotation_state", "ordinal", "occurrence_ordinal"}
        }
        annotation = {**base, "annotation_id": _derived_id("GEOANN-", annotation_identity)}
        if annotation["annotation_id"] in seen_ids:
            raise GoldValidationError("E-GOLD-ID", "duplicate derived annotation_id")
        seen_ids.add(annotation["annotation_id"])
        _schema_validate(annotation_schema, annotation, label=annotation["annotation_id"])
        annotations.append(annotation)
    return annotations, {unit_id: (values[0], values[1]) for unit_id, values in counts.items()}


def _validate_review(
    *,
    sample: dict[str, Any],
    review: dict[str, Any],
    review_schema: dict[str, Any],
    labels_artifact_id: str,
    counts: dict[str, tuple[int, int]],
    allow_draft: bool,
) -> tuple[str, tuple[str, ...]]:
    _schema_validate(review_schema, review, label="review manifest")
    if review.get("schema_version") != REVIEW_SCHEMA_VERSION:
        raise GoldValidationError("E-GOLD-REVIEW", "unsupported review manifest")
    if review.get("sample_id") != sample["sample_id"]:
        raise GoldValidationError("E-GOLD-REVIEW", "review sample_id differs")
    if review.get("labels_artifact_id") != labels_artifact_id:
        raise GoldValidationError("E-GOLD-REVIEW", "review labels_artifact_id differs")
    state = review["review_state"]
    if state != "HUMAN_ACCEPTED" and not allow_draft:
        raise GoldValidationError(
            "E-GOLD-NOT-ACCEPTED", "draft labels require the explicit --allow-draft flag"
        )
    expected_ids = [unit["unit_id"] for unit in sample["units"]]
    entries = review["units"]
    by_id: dict[str, dict[str, Any]] = {}
    for entry in entries:
        unit_id = entry["unit_id"]
        if unit_id in by_id:
            raise GoldValidationError("E-GOLD-REVIEW", f"duplicate review unit {unit_id}")
        by_id[unit_id] = entry
    if set(by_id) != set(expected_ids):
        raise GoldValidationError(
            "E-GOLD-REVIEW", "review manifest must explicitly list every sample unit"
        )
    incomplete: list[str] = []
    for unit_id in expected_ids:
        entry = by_id[unit_id]
        actual_included, actual_excluded = counts[unit_id]
        if (
            entry["included_count"] != actual_included
            or entry["excluded_count"] != actual_excluded
        ):
            raise GoldValidationError("E-GOLD-REVIEW", f"review counts differ for {unit_id}")
        if not entry["review_complete"]:
            incomplete.append(unit_id)
    if state == "HUMAN_ACCEPTED":
        if review["reviewer_kind"] != "HUMAN":
            raise GoldValidationError(
                "E-GOLD-NOT-ACCEPTED", "HUMAN_ACCEPTED requires reviewer_kind HUMAN"
            )
        if incomplete:
            raise GoldValidationError(
                "E-GOLD-NOT-ACCEPTED", "HUMAN_ACCEPTED requires every unit review_complete"
            )
    return state, tuple(incomplete)


def _derive_unique(annotations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, bytes], list[dict[str, Any]]] = {}
    for annotation in annotations:
        payload_bytes = canonical_dumps(annotation["payload"])
        grouped.setdefault((annotation["unit_id"], payload_bytes), []).append(annotation)
    rows: list[dict[str, Any]] = []
    unit_order: dict[str, int] = {}
    for annotation in annotations:
        unit_order.setdefault(annotation["unit_id"], len(unit_order))
    for (unit_id, payload_bytes), members in grouped.items():
        members.sort(key=lambda item: item["occurrence_ordinal"])
        payload = _decode_json(payload_bytes, label="canonical payload")
        base = {
            "schema_version": UNIQUE_SCHEMA_VERSION,
            "sample_id": members[0]["sample_id"],
            "annotation_state": members[0]["annotation_state"],
            "unit_id": unit_id,
            "unit_hash": members[0]["unit_hash"],
            "ordinal": members[0]["ordinal"],
            "payload": payload,
            "payload_hash": object_hash(payload, omit=()),
            "occurrences": [
                {
                    "annotation_id": member["annotation_id"],
                    "position_bucket": member["unit_position"]["bucket"],
                }
                for member in members
            ],
        }
        unique_hash = object_hash(base, omit=())
        rows.append(
            {
                **base,
                "unique_id": _derived_id("GEOUNIQ-", {"unique_hash": unique_hash}),
                "unique_hash": unique_hash,
            }
        )
    rows.sort(
        key=lambda row: (
            unit_order[row["unit_id"]],
            canonical_dumps(row["payload"]),
        )
    )
    ids = [row["unique_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise GoldValidationError("E-GOLD-ID", "duplicate derived unique_id")
    return rows


def load_and_validate(
    *,
    sample_path: pathlib.Path,
    source_dir: pathlib.Path,
    labels_path: pathlib.Path,
    review_path: pathlib.Path,
    label_schema_path: pathlib.Path | None = None,
    review_schema_path: pathlib.Path | None = None,
    annotation_schema_path: pathlib.Path | None = None,
    allow_draft: bool = False,
) -> GoldInputs:
    sample = _read_json(sample_path, label="sample manifest")
    _validate_sample(sample)
    schema_root = sample_path.parent
    label_schema = _load_schema(
        label_schema_path or schema_root / "geography-gold-label.schema.json",
        label="gold label schema",
    )
    review_schema = _load_schema(
        review_schema_path or schema_root / "geography-gold-review.schema.json",
        label="gold review schema",
    )
    annotation_schema = _load_schema(
        annotation_schema_path or schema_root / "geography-gold-annotation.schema.json",
        label="gold annotation schema",
    )
    packets = _load_source_packets(sample, source_dir)
    labels_bytes, labels = _read_canonical_jsonl(labels_path, label="gold labels")
    review = _read_json(review_path, label="review manifest")

    review_state = review.get("review_state")
    if review_state not in {"ANNOTATION_DRAFT", "HUMAN_ACCEPTED"}:
        # Produce a stable error before deriving rows with an invalid state.
        raise GoldValidationError("E-GOLD-REVIEW", "unknown review_state")
    annotations, counts = _compile_annotations(
        sample=sample,
        packets=packets,
        labels=labels,
        review_state=review_state,
        label_schema=label_schema,
        annotation_schema=annotation_schema,
    )
    state, incomplete = _validate_review(
        sample=sample,
        review=review,
        review_schema=review_schema,
        labels_artifact_id=artifact_id_for(labels_bytes),
        counts=counts,
        allow_draft=allow_draft,
    )
    if state != review_state:
        raise GoldValidationError("E-GOLD-REVIEW", "review state changed during validation")
    unique_rows = _derive_unique(annotations)
    return GoldInputs(
        sample=sample,
        packets=packets,
        labels=labels,
        review=review,
        annotations=annotations,
        unique_rows=unique_rows,
        incomplete_unit_ids=incomplete,
    )


def _read_bytes(path: pathlib.Path, *, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise GoldValidationError("E-GOLD-INPUT", f"cannot read {label}: {path}") from exc


def _build_frozen_gold_manifest(
    *,
    inputs: GoldInputs,
    sample_path: pathlib.Path,
    source_dir: pathlib.Path,
    labels_path: pathlib.Path,
    review_path: pathlib.Path,
) -> tuple[dict[str, Any], bytes, bytes]:
    if (
        inputs.review.get("review_state") != "HUMAN_ACCEPTED"
        or inputs.review.get("reviewer_kind") != "HUMAN"
        or inputs.incomplete_unit_ids
    ):
        raise GoldValidationError(
            "E-GOLD-NOT-ACCEPTED",
            "FROZEN_GOLD requires complete HUMAN_ACCEPTED review for every unit",
        )
    annotation_bytes = _canonical_jsonl(inputs.annotations)
    unique_bytes = _canonical_jsonl(inputs.unique_rows)
    labels_bytes = _read_bytes(labels_path, label="gold labels")
    source_packets: list[dict[str, Any]] = []
    for sample_unit in inputs.sample["units"]:
        unit_id = sample_unit["unit_id"]
        packet_bytes = _read_bytes(
            source_dir / f"{unit_id}.json", label=f"source packet {unit_id}"
        )
        source_packets.append(
            {
                "unit_id": unit_id,
                "source_packet_artifact_id": artifact_id_for(packet_bytes),
            }
        )
    source_packet_set_hash = object_hash({"source_packets": source_packets}, omit=())
    include_count = sum(label["decision"] == "INCLUDE" for label in inputs.labels)
    exclude_count = sum(label["decision"] == "EXCLUDE" for label in inputs.labels)
    base = {
        "schema_version": GOLD_MANIFEST_SCHEMA_VERSION,
        "sample_id": inputs.sample["sample_id"],
        "state": "FROZEN_GOLD",
        "text_snapshot_id": inputs.sample["baseline"]["text_snapshot_id"],
        "text_snapshot_hash": inputs.sample["baseline"]["text_snapshot_hash"],
        "sample_manifest_artifact_id": artifact_id_for(
            _read_bytes(sample_path, label="sample manifest")
        ),
        "sample_manifest_hash": object_hash(inputs.sample, omit=()),
        "source_packets": source_packets,
        "source_packet_set_hash": source_packet_set_hash,
        "labels_artifact_id": artifact_id_for(labels_bytes),
        "review_manifest_artifact_id": artifact_id_for(
            _read_bytes(review_path, label="review manifest")
        ),
        "occurrence_jsonl_artifact_id": artifact_id_for(annotation_bytes),
        "unique_jsonl_artifact_id": artifact_id_for(unique_bytes),
        "counts": {
            "unit_count": len(inputs.packets),
            "label_count": len(inputs.labels),
            "include_count": include_count,
            "exclude_count": exclude_count,
            "occurrence_count": len(inputs.annotations),
            "unique_count": len(inputs.unique_rows),
        },
    }
    gold_hash = object_hash(base, omit=())
    manifest = {
        **base,
        "gold_id": _derived_id("GOLD-", {"gold_hash": gold_hash}),
        "gold_hash": gold_hash,
    }
    return manifest, annotation_bytes, unique_bytes


def freeze_gold(
    *,
    sample_path: pathlib.Path,
    source_dir: pathlib.Path,
    labels_path: pathlib.Path,
    review_path: pathlib.Path,
    occurrences_out: pathlib.Path,
    unique_out: pathlib.Path,
    manifest_out: pathlib.Path,
    label_schema_path: pathlib.Path | None = None,
    review_schema_path: pathlib.Path | None = None,
    annotation_schema_path: pathlib.Path | None = None,
    gold_manifest_schema_path: pathlib.Path | None = None,
) -> dict[str, Any]:
    inputs = load_and_validate(
        sample_path=sample_path,
        source_dir=source_dir,
        labels_path=labels_path,
        review_path=review_path,
        label_schema_path=label_schema_path,
        review_schema_path=review_schema_path,
        annotation_schema_path=annotation_schema_path,
    )
    manifest, annotation_bytes, unique_bytes = _build_frozen_gold_manifest(
        inputs=inputs,
        sample_path=sample_path,
        source_dir=source_dir,
        labels_path=labels_path,
        review_path=review_path,
    )
    manifest_schema = _load_schema(
        gold_manifest_schema_path
        or sample_path.parent / "geography-gold-manifest.schema.json",
        label="frozen gold manifest schema",
    )
    _schema_validate(manifest_schema, manifest, label="frozen gold manifest")
    _write_immutable(occurrences_out, annotation_bytes)
    _write_immutable(unique_out, unique_bytes)
    _write_immutable(manifest_out, canonical_dumps(manifest) + b"\n")
    return manifest


def validate_frozen_gold(
    *,
    sample_path: pathlib.Path,
    source_dir: pathlib.Path,
    labels_path: pathlib.Path,
    review_path: pathlib.Path,
    occurrences_path: pathlib.Path,
    unique_path: pathlib.Path,
    manifest_path: pathlib.Path,
    label_schema_path: pathlib.Path | None = None,
    review_schema_path: pathlib.Path | None = None,
    annotation_schema_path: pathlib.Path | None = None,
    gold_manifest_schema_path: pathlib.Path | None = None,
) -> dict[str, Any]:
    inputs = load_and_validate(
        sample_path=sample_path,
        source_dir=source_dir,
        labels_path=labels_path,
        review_path=review_path,
        label_schema_path=label_schema_path,
        review_schema_path=review_schema_path,
        annotation_schema_path=annotation_schema_path,
    )
    expected, annotation_bytes, unique_bytes = _build_frozen_gold_manifest(
        inputs=inputs,
        sample_path=sample_path,
        source_dir=source_dir,
        labels_path=labels_path,
        review_path=review_path,
    )
    manifest = _read_canonical_document(manifest_path, label="frozen gold manifest")
    manifest_schema = _load_schema(
        gold_manifest_schema_path
        or sample_path.parent / "geography-gold-manifest.schema.json",
        label="frozen gold manifest schema",
    )
    _schema_validate(manifest_schema, manifest, label="frozen gold manifest")
    if manifest != expected:
        raise GoldValidationError(
            "E-GOLD-MANIFEST", "frozen gold manifest does not replay from its inputs"
        )
    if _read_bytes(occurrences_path, label="occurrence JSONL") != annotation_bytes:
        raise GoldValidationError("E-GOLD-MANIFEST", "occurrence JSONL differs from replay")
    if _read_bytes(unique_path, label="unique JSONL") != unique_bytes:
        raise GoldValidationError("E-GOLD-MANIFEST", "unique JSONL differs from replay")
    return manifest


def _add_compile_inputs(
    parser: argparse.ArgumentParser,
    *,
    include_allow_draft: bool = False,
    include_gold_manifest_schema: bool = False,
) -> None:
    parser.add_argument("--sample", type=pathlib.Path, required=True)
    parser.add_argument("--source-dir", type=pathlib.Path, required=True)
    parser.add_argument("--labels", type=pathlib.Path, required=True)
    parser.add_argument("--review-manifest", type=pathlib.Path, required=True)
    parser.add_argument("--label-schema", type=pathlib.Path)
    parser.add_argument("--review-schema", type=pathlib.Path)
    parser.add_argument("--annotation-schema", type=pathlib.Path)
    if include_gold_manifest_schema:
        parser.add_argument("--gold-manifest-schema", type=pathlib.Path)
    if include_allow_draft:
        parser.add_argument("--allow-draft", action="store_true")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    merge = subparsers.add_parser(
        "merge-drafts", help="canonicalize and deterministically merge blind label drafts"
    )
    merge.add_argument("--sample", type=pathlib.Path, required=True)
    merge.add_argument("--input", type=pathlib.Path, action="append", required=True)
    merge.add_argument("--output", type=pathlib.Path, required=True)
    merge.add_argument("--label-schema", type=pathlib.Path)

    prepare = subparsers.add_parser("prepare", help="prepare frozen runtime-only source packets")
    prepare.add_argument("--sample", type=pathlib.Path, required=True)
    prepare.add_argument("--snapshot", type=pathlib.Path, required=True)
    prepare.add_argument("--tasks-dir", type=pathlib.Path, required=True)
    prepare.add_argument("--output-dir", type=pathlib.Path, required=True)

    validate = subparsers.add_parser("validate", help="validate source, labels, and review closure")
    _add_compile_inputs(validate, include_allow_draft=True)

    derive = subparsers.add_parser("derive", help="validate and write occurrence/unique JSONL")
    _add_compile_inputs(derive, include_allow_draft=True)
    derive.add_argument("--occurrences-out", type=pathlib.Path, required=True)
    derive.add_argument("--unique-out", type=pathlib.Path, required=True)

    freeze = subparsers.add_parser(
        "freeze", help="write HUMAN_ACCEPTED occurrence, unique, and FROZEN_GOLD artifacts"
    )
    _add_compile_inputs(freeze, include_gold_manifest_schema=True)
    freeze.add_argument("--occurrences-out", type=pathlib.Path, required=True)
    freeze.add_argument("--unique-out", type=pathlib.Path, required=True)
    freeze.add_argument("--gold-manifest-out", type=pathlib.Path, required=True)

    validate_frozen = subparsers.add_parser(
        "validate-frozen", help="replay and validate a complete FROZEN_GOLD artifact set"
    )
    _add_compile_inputs(validate_frozen, include_gold_manifest_schema=True)
    validate_frozen.add_argument("--occurrences", type=pathlib.Path, required=True)
    validate_frozen.add_argument("--unique", type=pathlib.Path, required=True)
    validate_frozen.add_argument("--gold-manifest", type=pathlib.Path, required=True)
    return parser


def _summary(inputs: GoldInputs) -> dict[str, Any]:
    labels_bytes = _canonical_jsonl(inputs.labels)
    annotation_bytes = _canonical_jsonl(inputs.annotations)
    unique_bytes = _canonical_jsonl(inputs.unique_rows)
    return {
        "sample_id": inputs.sample["sample_id"],
        "review_state": inputs.review["review_state"],
        "source_packet_count": len(inputs.packets),
        "label_count": len(inputs.labels),
        "labels_artifact_id": artifact_id_for(labels_bytes),
        "occurrence_count": len(inputs.annotations),
        "occurrence_jsonl_artifact_id": artifact_id_for(annotation_bytes),
        "unique_count": len(inputs.unique_rows),
        "unique_jsonl_artifact_id": artifact_id_for(unique_bytes),
        "incomplete_unit_ids": list(inputs.incomplete_unit_ids),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "merge-drafts":
            rows = merge_label_drafts(
                sample_path=args.sample,
                input_paths=args.input,
                output_path=args.output,
                label_schema_path=args.label_schema,
            )
            print(
                json.dumps(
                    {
                        "labels_artifact_id": artifact_id_for(_canonical_jsonl(rows)),
                        "merged": len(rows),
                        "output": str(args.output),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

        if args.command == "prepare":
            paths = prepare_source_packets(
                args.sample,
                args.snapshot,
                args.tasks_dir,
                args.output_dir,
            )
            print(
                json.dumps(
                    {"prepared": len(paths), "output_dir": str(args.output_dir)},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

        if args.command == "freeze":
            manifest = freeze_gold(
                sample_path=args.sample,
                source_dir=args.source_dir,
                labels_path=args.labels,
                review_path=args.review_manifest,
                occurrences_out=args.occurrences_out,
                unique_out=args.unique_out,
                manifest_out=args.gold_manifest_out,
                label_schema_path=args.label_schema,
                review_schema_path=args.review_schema,
                annotation_schema_path=args.annotation_schema,
                gold_manifest_schema_path=args.gold_manifest_schema,
            )
            print(
                json.dumps(
                    {
                        "gold_id": manifest["gold_id"],
                        "gold_hash": manifest["gold_hash"],
                        "state": manifest["state"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

        if args.command == "validate-frozen":
            manifest = validate_frozen_gold(
                sample_path=args.sample,
                source_dir=args.source_dir,
                labels_path=args.labels,
                review_path=args.review_manifest,
                occurrences_path=args.occurrences,
                unique_path=args.unique,
                manifest_path=args.gold_manifest,
                label_schema_path=args.label_schema,
                review_schema_path=args.review_schema,
                annotation_schema_path=args.annotation_schema,
                gold_manifest_schema_path=args.gold_manifest_schema,
            )
            print(
                json.dumps(
                    {
                        "gold_id": manifest["gold_id"],
                        "gold_hash": manifest["gold_hash"],
                        "state": manifest["state"],
                        "validation": "PASS",
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

        inputs = load_and_validate(
            sample_path=args.sample,
            source_dir=args.source_dir,
            labels_path=args.labels,
            review_path=args.review_manifest,
            label_schema_path=args.label_schema,
            review_schema_path=args.review_schema,
            annotation_schema_path=args.annotation_schema,
            allow_draft=args.allow_draft,
        )
        if args.command == "derive":
            _write_immutable(args.occurrences_out, _canonical_jsonl(inputs.annotations))
            _write_immutable(args.unique_out, _canonical_jsonl(inputs.unique_rows))
        print(json.dumps(_summary(inputs), ensure_ascii=False, sort_keys=True))
        return 0
    except GoldValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
