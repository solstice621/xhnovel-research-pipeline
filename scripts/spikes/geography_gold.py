"""Prepare and compile the runtime-only Experiment B geography reference set.

This is deliberately spike tooling, not a second extraction path.  ``prepare``
copies only source material already embedded in frozen native agent tasks into
content-bound source packets.  ``validate`` and ``derive`` consume blind labels,
while ``freeze`` and ``validate-frozen`` enforce the model-adjudication transition.
No command inspects executor answers or extraction observations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Iterable

from jsonschema import Draft202012Validator

from xhnovel_pipeline.canonical import canonical_dumps
from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.file_io import write_immutable
from xhnovel_pipeline.hashing import artifact_id_for, is_real_sha256, object_hash
from xhnovel_pipeline.paths import repo_root


SOURCE_SCHEMA_VERSION = "geography-gold-source/v1"
LABEL_SCHEMA_VERSION = "geography-gold-label/v1"
REVIEW_SCHEMA_VERSION = "geography-gold-review/v2"
ANNOTATION_SCHEMA_VERSION = "geography-gold-annotation/v2"
UNIQUE_SCHEMA_VERSION = "geography-gold-unique/v1"
DISPUTE_SCHEMA_VERSION = "geography-gold-dispute/v1"
GOLD_MANIFEST_SCHEMA_VERSION = "geography-gold-manifest/v2"
SAMPLE_SCHEMA_VERSION = "geography-gold-sample/v2"
PROTOCOL_VERSION = "geography-model-reference/v2"
REVIEW_POLICY = "dual-model-adjudication/v1"
SELECTION_ALGORITHM_ID = "experiment-b-control/v1"
MODEL_REVIEW_ROLES = {
    "BLIND_EXTRACTOR",
    "DRAFT_AUDITOR",
    "DIFFERENCE_ADJUDICATOR",
}
FORBIDDEN_REVIEW_INPUTS = (
    "baseline_answers",
    "candidate_answers",
    "capacity_statistics",
)
CONTROL_STRATA = ((1, 169), (170, 338), (339, 507), (508, 676))
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
    input_labels: list[dict[str, Any]]
    labels: list[dict[str, Any]]
    disputes: list[dict[str, Any]]
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


def _write_immutable(path: pathlib.Path, data: bytes) -> bool:
    try:
        return write_immutable(
            path,
            data,
            code="E-GOLD-IMMUTABLE",
            message=f"refusing to overwrite different content: {path}",
        )
    except ValidationError as exc:
        raise GoldValidationError(
            exc.code, f"refusing to overwrite different content: {path}"
        ) from exc


def _write_immutable_set(entries: list[tuple[pathlib.Path, bytes]]) -> None:
    """Commit an immutable output set with the final entry as its commit marker.

    There is no portable filesystem primitive for atomically replacing several
    unrelated paths.  We preflight existing bytes, finish interrupted sets whose
    manifest is absent, and write that manifest last.  Consumers only recognize
    a set whose final manifest exists and validates.
    """

    if not entries:
        raise GoldValidationError("E-GOLD-OUTPUT", "immutable output set is empty")
    normalized = [path.resolve(strict=False) for path, _ in entries]
    if len(normalized) != len(set(normalized)):
        raise GoldValidationError("E-GOLD-OUTPUT", "immutable output paths must be distinct")

    exists = [path.exists() for path, _ in entries]
    if exists[-1] and not all(exists):
        raise GoldValidationError(
            "E-GOLD-PARTIAL",
            "completed manifest has missing frozen outputs",
        )
    for (path, data), present in zip(entries, exists):
        if present and (not path.is_file() or path.read_bytes() != data):
            raise GoldValidationError(
                "E-GOLD-IMMUTABLE", f"refusing to overwrite different content: {path}"
            )
    if all(exists):
        return

    for path, data in entries:
        _write_immutable(path, data)


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
    label_schema_file = label_schema_path or sample_path.parent / "geography-gold-label.schema.json"
    _verify_bound_artifact(
        label_schema_file,
        sample["protocol"]["schema_artifact_ids"]["label"],
        label="label schema",
    )
    _verify_bound_artifact(
        pathlib.Path(__file__).resolve(),
        sample["protocol"]["compiler_artifact_id"],
        label="geography gold compiler",
    )
    label_schema = _load_schema(
        label_schema_file,
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
            "protocol",
            "baseline",
            "selection",
            "source_packet",
            "units",
        },
        label="sample",
    )
    if sample["schema_version"] != SAMPLE_SCHEMA_VERSION:
        raise GoldValidationError("E-GOLD-SAMPLE", "unsupported sample schema_version")
    if sample["status"] != "FROZEN_SAMPLE" or sample["protocol_version"] != PROTOCOL_VERSION:
        raise GoldValidationError("E-GOLD-SAMPLE", "sample is not the frozen v2 protocol")
    sample_id = sample["sample_id"]
    if sample_id != "GEOGOLD-B-20260904":
        raise GoldValidationError("E-GOLD-SAMPLE", "unexpected frozen sample_id")

    protocol = sample["protocol"]
    if not isinstance(protocol, dict):
        raise GoldValidationError("E-GOLD-SAMPLE", "protocol must be an object")
    _exact_keys(
        protocol,
        {
            "protocol_commit",
            "protocol_artifact_id",
            "review_policy",
            "compiler_artifact_id",
            "schema_artifact_ids",
        },
        label="sample.protocol",
    )
    if (
        not isinstance(protocol["protocol_commit"], str)
        or not re.fullmatch(r"[0-9a-f]{40}", protocol["protocol_commit"])
        or protocol["review_policy"] != REVIEW_POLICY
    ):
        raise GoldValidationError("E-GOLD-SAMPLE", "invalid protocol identity")
    for field in ("protocol_artifact_id", "compiler_artifact_id"):
        _require_hash(protocol[field], label=f"sample.protocol.{field}")
    schema_artifacts = protocol["schema_artifact_ids"]
    if not isinstance(schema_artifacts, dict):
        raise GoldValidationError("E-GOLD-SAMPLE", "schema_artifact_ids must be an object")
    _exact_keys(
        schema_artifacts,
        {"label", "review", "annotation", "unique", "dispute", "manifest"},
        label="sample.protocol.schema_artifact_ids",
    )
    for field, value in schema_artifacts.items():
        _require_hash(value, label=f"sample.protocol.schema_artifact_ids.{field}")

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
    for field in ("engine_commit", "evidence_commit"):
        if not isinstance(baseline[field], str) or not re.fullmatch(
            r"[0-9a-f]{40}", baseline[field]
        ):
            raise GoldValidationError(
                "E-GOLD-SAMPLE", f"sample.baseline.{field} must be a full commit"
            )
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
            "source_selection_commit",
            "source_selection_manifest_path",
            "source_selection_manifest_artifact_id",
            "source_seed",
            "selection_algorithm_id",
            "strata",
            "required_ordinals",
            "random_control_ordinals",
        },
        label="sample.selection",
    )
    if (
        not isinstance(selection["source_selection_commit"], str)
        or not re.fullmatch(r"[0-9a-f]{40}", selection["source_selection_commit"])
        or not isinstance(selection["source_selection_manifest_path"], str)
        or not selection["source_selection_manifest_path"]
        or pathlib.PurePosixPath(selection["source_selection_manifest_path"]).is_absolute()
        or ".." in pathlib.PurePosixPath(selection["source_selection_manifest_path"]).parts
        or selection["selection_algorithm_id"] != SELECTION_ALGORITHM_ID
        or not isinstance(selection["source_seed"], int)
        or isinstance(selection["source_seed"], bool)
    ):
        raise GoldValidationError("E-GOLD-SAMPLE", "invalid selection provenance")
    _require_hash(
        selection["source_selection_manifest_artifact_id"],
        label="sample.selection.source_selection_manifest_artifact_id",
    )
    if selection["strata"] != [list(bounds) for bounds in CONTROL_STRATA]:
        raise GoldValidationError("E-GOLD-SAMPLE", "selection strata differ from frozen policy")
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
    if len(selection["required_ordinals"]) != 6 or len(selection["random_control_ordinals"]) != 4:
        raise GoldValidationError(
            "E-GOLD-SAMPLE", "sample requires exactly six anchors and four controls"
        )

    units = sample["units"]
    if not isinstance(units, list) or len(units) != 10:
        raise GoldValidationError("E-GOLD-SAMPLE", "sample.units must contain exactly ten rows")
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
        if unit["stratum"] not in {f"{start}-{end}" for start, end in CONTROL_STRATA}:
            raise GoldValidationError("E-GOLD-SAMPLE", f"invalid stratum for {unit_id}")
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
    controls_by_stratum: dict[str, int] = {}
    required_ordinals = set(selection["required_ordinals"])
    for unit in units:
        if unit["selection"] == "random-control":
            controls_by_stratum[unit["stratum"]] = controls_by_stratum.get(unit["stratum"], 0) + 1
        elif unit["ordinal"] not in required_ordinals:
            raise GoldValidationError(
                "E-GOLD-SAMPLE", "non-control row is absent from required_ordinals"
            )
    expected_strata = {f"{start}-{end}" for start, end in CONTROL_STRATA}
    if controls_by_stratum != {stratum: 1 for stratum in expected_strata}:
        raise GoldValidationError("E-GOLD-SAMPLE", "sample requires one control per stratum")


def _verify_bound_artifact(path: pathlib.Path, expected: str, *, label: str) -> None:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise GoldValidationError("E-GOLD-CONTRACT", f"cannot read {label}: {path}") from exc
    if artifact_id_for(data) != expected:
        raise GoldValidationError("E-GOLD-CONTRACT", f"{label} differs from frozen identity")


def _verify_compilation_contracts(
    sample: dict[str, Any],
    *,
    protocol_path: pathlib.Path,
    label_schema_path: pathlib.Path,
    review_schema_path: pathlib.Path,
    annotation_schema_path: pathlib.Path,
    unique_schema_path: pathlib.Path,
    dispute_schema_path: pathlib.Path,
    manifest_schema_path: pathlib.Path | None = None,
) -> None:
    protocol = sample["protocol"]
    _verify_bound_artifact(
        protocol_path, protocol["protocol_artifact_id"], label="protocol document"
    )
    _verify_bound_artifact(
        pathlib.Path(__file__).resolve(),
        protocol["compiler_artifact_id"],
        label="geography gold compiler",
    )
    paths = {
        "label": label_schema_path,
        "review": review_schema_path,
        "annotation": annotation_schema_path,
        "unique": unique_schema_path,
        "dispute": dispute_schema_path,
    }
    if manifest_schema_path is not None:
        paths["manifest"] = manifest_schema_path
    for name, path in paths.items():
        _verify_bound_artifact(
            path,
            protocol["schema_artifact_ids"][name],
            label=f"{name} schema",
        )


def _selection_score(seed: int, stratum: str, unit_id: str) -> str:
    value = f"{seed}\0{SELECTION_ALGORITHM_ID}\0{stratum}\0{unit_id}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _read_git_blob(repository_root: pathlib.Path, commit: str, path: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repository_root), "show", f"{commit}:{path}"],
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        raise GoldValidationError("E-GOLD-SELECTION", "cannot execute git") from exc
    if completed.returncode != 0:
        raise GoldValidationError(
            "E-GOLD-SELECTION", "cannot read the frozen source-selection manifest"
        )
    return completed.stdout


def verify_sample_selection(
    *, sample_path: pathlib.Path, repository_root: pathlib.Path
) -> list[dict[str, Any]]:
    sample = _read_json(sample_path, label="sample manifest")
    _validate_sample(sample)
    selection = sample["selection"]
    raw = _read_git_blob(
        repository_root,
        selection["source_selection_commit"],
        selection["source_selection_manifest_path"],
    )
    if artifact_id_for(raw) != selection["source_selection_manifest_artifact_id"]:
        raise GoldValidationError(
            "E-GOLD-SELECTION", "source-selection manifest artifact differs"
        )
    manifest = _decode_json(raw, label="source-selection manifest")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "experiment-sample/v1":
        raise GoldValidationError("E-GOLD-SELECTION", "unsupported source-selection manifest")
    provenance = manifest.get("provenance")
    selected = manifest.get("selected")
    if not isinstance(provenance, dict) or not isinstance(selected, list):
        raise GoldValidationError("E-GOLD-SELECTION", "source-selection manifest is incomplete")
    if (
        provenance.get("seed") != selection["source_seed"]
        or provenance.get("strata") != selection["strata"]
    ):
        raise GoldValidationError("E-GOLD-SELECTION", "source-selection policy differs")

    random_rows = [row for row in selected if isinstance(row, dict) and row.get("selection") == "random"]
    if len(random_rows) != 12:
        raise GoldValidationError(
            "E-GOLD-SELECTION", "source-selection manifest must contain twelve random draws"
        )
    seen_candidate_ordinals: set[int] = set()
    seen_candidate_ids: set[str] = set()
    chosen: list[dict[str, Any]] = []
    sample_controls = {
        unit["stratum"]: unit for unit in sample["units"] if unit["selection"] == "random-control"
    }
    for start, end in CONTROL_STRATA:
        stratum = f"{start}-{end}"
        candidates = [row for row in random_rows if row.get("stratum") == stratum]
        if len(candidates) != 3:
            raise GoldValidationError(
                "E-GOLD-SELECTION", f"stratum {stratum} must contain three random draws"
            )
        for row in candidates:
            ordinal = row.get("ordinal")
            unit_id = row.get("unit_id")
            if (
                not isinstance(ordinal, int)
                or isinstance(ordinal, bool)
                or not start <= ordinal <= end
                or not isinstance(unit_id, str)
                or not re.fullmatch(r"XUNIT-[A-Z0-9]{20}", unit_id)
                or ordinal in seen_candidate_ordinals
                or unit_id in seen_candidate_ids
            ):
                raise GoldValidationError(
                    "E-GOLD-SELECTION", f"invalid random draw in stratum {stratum}"
                )
            seen_candidate_ordinals.add(ordinal)
            seen_candidate_ids.add(unit_id)
        winner = min(
            candidates,
            key=lambda row: _selection_score(selection["source_seed"], stratum, row["unit_id"]),
        )
        control = sample_controls[stratum]
        if winner["ordinal"] != control["ordinal"] or winner["unit_id"] != control["unit_id"]:
            raise GoldValidationError(
                "E-GOLD-SELECTION", f"control selection differs in stratum {stratum}"
            )
        chosen.append(
            {
                "stratum": stratum,
                "ordinal": winner["ordinal"],
                "unit_id": winner["unit_id"],
                "selection_hash": _selection_score(
                    selection["source_seed"], stratum, winner["unit_id"]
                ),
            }
        )
    if {row["ordinal"] for row in chosen} != set(selection["random_control_ordinals"]):
        raise GoldValidationError("E-GOLD-SELECTION", "control ordinal set differs")
    return chosen


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
    protocol_path: pathlib.Path | None = None,
) -> list[pathlib.Path]:
    sample = _read_json(sample_path, label="sample manifest")
    _validate_sample(sample)
    protocol = sample["protocol"]
    _verify_bound_artifact(
        protocol_path or sample_path.parent / "experiment-b-plan.md",
        protocol["protocol_artifact_id"],
        label="protocol document",
    )
    _verify_bound_artifact(
        pathlib.Path(__file__).resolve(),
        protocol["compiler_artifact_id"],
        label="geography gold compiler",
    )
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


def _source_packet_records(sample: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    records = [
        {
            "unit_id": unit["unit_id"],
            "source_packet_artifact_id": unit["source_packet_hash"],
        }
        for unit in sample["units"]
    ]
    return records, object_hash({"source_packets": records}, omit=())


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


def _verified_review_artifact(
    root: pathlib.Path, relative: str, expected: str, *, label: str
) -> pathlib.Path:
    path_value = pathlib.PurePosixPath(relative)
    if path_value.is_absolute() or ".." in path_value.parts or not path_value.parts:
        raise GoldValidationError("E-GOLD-REVIEW", f"unsafe {label} path")
    root_resolved = root.resolve()
    path = root.joinpath(*path_value.parts)
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise GoldValidationError("E-GOLD-REVIEW", f"missing {label} artifact") from exc
    if not resolved.is_relative_to(root_resolved) or not resolved.is_file() or path.is_symlink():
        raise GoldValidationError("E-GOLD-REVIEW", f"unsafe {label} artifact")
    _verify_bound_artifact(resolved, expected, label=label)
    return resolved


def _verify_model_review_artifacts(
    review: dict[str, Any], review_artifacts_dir: pathlib.Path
) -> None:
    if not review_artifacts_dir.is_dir():
        raise GoldValidationError("E-GOLD-REVIEW", "model review artifacts directory is missing")
    seen_paths: set[pathlib.Path] = set()
    for entry in review["model_reviews"]:
        role = entry["role"]
        for kind in ("prompt", "output"):
            path = _verified_review_artifact(
                review_artifacts_dir,
                entry[f"review_{kind}_file"],
                entry[f"review_{kind}_artifact_id"],
                label=f"{role} {kind}",
            )
            if path in seen_paths:
                raise GoldValidationError(
                    "E-GOLD-REVIEW", "model review prompt/output paths must be distinct"
                )
            seen_paths.add(path)


def _validate_review(
    *,
    sample: dict[str, Any],
    review: dict[str, Any],
    review_schema: dict[str, Any],
    labels_artifact_id: str,
    source_packet_set_hash: str,
    input_labels_artifact_id: str | None,
    disputes_artifact_id: str | None,
    disputed_count: int,
    review_artifacts_dir: pathlib.Path | None,
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
    if review.get("source_packet_set_hash") != source_packet_set_hash:
        raise GoldValidationError("E-GOLD-REVIEW", "review source_packet_set_hash differs")
    state = review["review_state"]
    if state != "MODEL_ADJUDICATED" and not allow_draft:
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
    if state == "MODEL_ADJUDICATED":
        if review["reviewer_kind"] != "MODEL" or review.get("review_policy") != REVIEW_POLICY:
            raise GoldValidationError(
                "E-GOLD-NOT-ACCEPTED",
                "MODEL_ADJUDICATED requires the frozen model review policy",
            )
        if incomplete:
            raise GoldValidationError(
                "E-GOLD-NOT-ACCEPTED",
                "MODEL_ADJUDICATED requires every unit review_complete",
            )
        if tuple(review.get("forbidden_inputs", ())) != FORBIDDEN_REVIEW_INPUTS:
            raise GoldValidationError("E-GOLD-REVIEW", "forbidden input declaration differs")
        if review.get("input_labels_artifact_id") != input_labels_artifact_id:
            raise GoldValidationError("E-GOLD-REVIEW", "input labels artifact differs")
        if (
            review.get("disputes_artifact_id") != disputes_artifact_id
            or review.get("disputed_count") != disputed_count
        ):
            raise GoldValidationError("E-GOLD-REVIEW", "dispute artifact or count differs")
        model_reviews = review.get("model_reviews", [])
        by_role = {entry["role"]: entry for entry in model_reviews}
        execution_ids = [entry["execution_id"] for entry in model_reviews]
        output_ids = [entry["review_output_artifact_id"] for entry in model_reviews]
        if (
            set(by_role) != MODEL_REVIEW_ROLES
            or len(by_role) != len(model_reviews)
            or len(execution_ids) != len(set(execution_ids))
            or len(output_ids) != len(set(output_ids))
        ):
            raise GoldValidationError(
                "E-GOLD-REVIEW", "model review roles, executions, or outputs are not unique"
            )
        blind = by_role["BLIND_EXTRACTOR"]
        auditor = by_role["DRAFT_AUDITOR"]
        adjudicator = by_role["DIFFERENCE_ADJUDICATOR"]
        expected_inputs = {
            "BLIND_EXTRACTOR": {source_packet_set_hash},
            "DRAFT_AUDITOR": {
                source_packet_set_hash,
                review["input_labels_artifact_id"],
            },
            "DIFFERENCE_ADJUDICATOR": {
                source_packet_set_hash,
                blind["review_output_artifact_id"],
                auditor["review_output_artifact_id"],
            },
        }
        for role, expected in expected_inputs.items():
            if set(by_role[role]["input_artifact_ids"]) != expected:
                raise GoldValidationError(
                    "E-GOLD-REVIEW", f"{role} input artifact set differs"
                )
        if review["review_output_artifact_id"] != adjudicator["review_output_artifact_id"]:
            raise GoldValidationError(
                "E-GOLD-REVIEW", "top-level review output is not the adjudicator output"
            )
        if review_artifacts_dir is None:
            raise GoldValidationError(
                "E-GOLD-INPUT", "model adjudication requires review artifact bytes"
            )
        _verify_model_review_artifacts(review, review_artifacts_dir)
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


def _validate_input_labels(
    *,
    rows: list[dict[str, Any]],
    sample: dict[str, Any],
    label_schema: dict[str, Any],
) -> dict[str, str]:
    sample_units = {unit["unit_id"] for unit in sample["units"]}
    artifact_units: dict[str, str] = {}
    for index, row in enumerate(rows, start=1):
        _schema_validate(label_schema, row, label=f"input label line {index}")
        if row.get("schema_version") != LABEL_SCHEMA_VERSION:
            raise GoldValidationError("E-GOLD-LABEL", f"unsupported input label line {index}")
        if row.get("sample_id") != sample["sample_id"] or row.get("unit_id") not in sample_units:
            raise GoldValidationError("E-GOLD-LABEL", f"input label lineage differs at line {index}")
        artifact_id = artifact_id_for(canonical_dumps(row))
        if artifact_id in artifact_units:
            raise GoldValidationError("E-GOLD-LABEL", f"duplicate input label at line {index}")
        artifact_units[artifact_id] = row["unit_id"]
    return artifact_units


def _validate_disputes(
    *,
    rows: list[dict[str, Any]],
    sample: dict[str, Any],
    dispute_schema: dict[str, Any],
    candidate_label_units: dict[str, str],
) -> None:
    sample_units = {unit["unit_id"] for unit in sample["units"]}
    seen_ids: set[str] = set()
    for index, row in enumerate(rows, start=1):
        _schema_validate(dispute_schema, row, label=f"dispute line {index}")
        if (
            row.get("schema_version") != DISPUTE_SCHEMA_VERSION
            or row.get("sample_id") != sample["sample_id"]
            or row.get("unit_id") not in sample_units
        ):
            raise GoldValidationError("E-GOLD-DISPUTE", f"dispute lineage differs at line {index}")
        identity = {key: value for key, value in row.items() if key != "dispute_id"}
        if row["dispute_id"] != _derived_id("GEODSP-", identity):
            raise GoldValidationError("E-GOLD-DISPUTE", f"dispute id differs at line {index}")
        if row["dispute_id"] in seen_ids:
            raise GoldValidationError("E-GOLD-DISPUTE", f"duplicate dispute id at line {index}")
        seen_ids.add(row["dispute_id"])
        candidate_ids = row["candidate_label_artifact_ids"]
        if any(candidate_id not in candidate_label_units for candidate_id in candidate_ids):
            raise GoldValidationError(
                "E-GOLD-DISPUTE",
                f"dispute references unknown candidate label at line {index}",
            )
        if any(candidate_label_units[candidate_id] != row["unit_id"] for candidate_id in candidate_ids):
            raise GoldValidationError(
                "E-GOLD-DISPUTE",
                f"dispute candidate label unit differs at line {index}",
            )


def load_and_validate(
    *,
    sample_path: pathlib.Path,
    source_dir: pathlib.Path,
    input_labels_path: pathlib.Path | None,
    labels_path: pathlib.Path,
    disputes_path: pathlib.Path | None,
    review_path: pathlib.Path,
    review_artifacts_dir: pathlib.Path | None,
    label_schema_path: pathlib.Path | None = None,
    review_schema_path: pathlib.Path | None = None,
    annotation_schema_path: pathlib.Path | None = None,
    unique_schema_path: pathlib.Path | None = None,
    dispute_schema_path: pathlib.Path | None = None,
    protocol_path: pathlib.Path | None = None,
    allow_draft: bool = False,
) -> GoldInputs:
    sample = _read_json(sample_path, label="sample manifest")
    _validate_sample(sample)
    schema_root = sample_path.parent
    label_schema_file = label_schema_path or schema_root / "geography-gold-label.schema.json"
    review_schema_file = review_schema_path or schema_root / "geography-gold-review.schema.json"
    annotation_schema_file = (
        annotation_schema_path or schema_root / "geography-gold-annotation.schema.json"
    )
    unique_schema_file = unique_schema_path or schema_root / "geography-gold-unique.schema.json"
    dispute_schema_file = (
        dispute_schema_path or schema_root / "geography-gold-dispute.schema.json"
    )
    _verify_compilation_contracts(
        sample,
        protocol_path=protocol_path or schema_root / "experiment-b-plan.md",
        label_schema_path=label_schema_file,
        review_schema_path=review_schema_file,
        annotation_schema_path=annotation_schema_file,
        unique_schema_path=unique_schema_file,
        dispute_schema_path=dispute_schema_file,
    )
    label_schema = _load_schema(label_schema_file, label="gold label schema")
    review_schema = _load_schema(review_schema_file, label="gold review schema")
    annotation_schema = _load_schema(annotation_schema_file, label="gold annotation schema")
    unique_schema = _load_schema(unique_schema_file, label="gold unique schema")
    dispute_schema = _load_schema(dispute_schema_file, label="gold dispute schema")
    packets = _load_source_packets(sample, source_dir)
    _, source_packet_set_hash = _source_packet_records(sample)
    labels_bytes, labels = _read_canonical_jsonl(labels_path, label="gold labels")
    review = _read_json(review_path, label="review manifest")

    review_state = review.get("review_state")
    if review_state not in {"ANNOTATION_DRAFT", "MODEL_ADJUDICATED"}:
        # Produce a stable error before deriving rows with an invalid state.
        raise GoldValidationError("E-GOLD-REVIEW", "unknown review_state")
    input_labels_bytes = b""
    input_labels: list[dict[str, Any]] = []
    disputes_bytes = b""
    disputes: list[dict[str, Any]] = []
    if review_state == "MODEL_ADJUDICATED":
        if input_labels_path is None or disputes_path is None:
            raise GoldValidationError(
                "E-GOLD-INPUT", "model adjudication requires input labels and disputes"
            )
        input_labels_bytes, input_labels = _read_canonical_jsonl(
            input_labels_path, label="input labels"
        )
        disputes_bytes, disputes = _read_canonical_jsonl(disputes_path, label="disputes")
    input_label_units = _validate_input_labels(
        rows=input_labels,
        sample=sample,
        label_schema=label_schema,
    )
    final_label_units = _validate_input_labels(
        rows=labels,
        sample=sample,
        label_schema=label_schema,
    )
    _validate_disputes(
        rows=disputes,
        sample=sample,
        dispute_schema=dispute_schema,
        candidate_label_units=input_label_units | final_label_units,
    )
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
        source_packet_set_hash=source_packet_set_hash,
        input_labels_artifact_id=(
            artifact_id_for(input_labels_bytes) if input_labels_path is not None else None
        ),
        disputes_artifact_id=(artifact_id_for(disputes_bytes) if disputes_path is not None else None),
        disputed_count=len(disputes),
        review_artifacts_dir=review_artifacts_dir,
        counts=counts,
        allow_draft=allow_draft,
    )
    if state != review_state:
        raise GoldValidationError("E-GOLD-REVIEW", "review state changed during validation")
    unique_rows = _derive_unique(annotations)
    for index, row in enumerate(unique_rows, start=1):
        _schema_validate(unique_schema, row, label=f"unique row {index}")
    return GoldInputs(
        sample=sample,
        packets=packets,
        input_labels=input_labels,
        labels=labels,
        disputes=disputes,
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
        inputs.review.get("review_state") != "MODEL_ADJUDICATED"
        or inputs.review.get("reviewer_kind") != "MODEL"
        or inputs.incomplete_unit_ids
    ):
        raise GoldValidationError(
            "E-GOLD-NOT-ACCEPTED",
            "FROZEN_MODEL_GOLD requires complete MODEL_ADJUDICATED review for every unit",
        )
    annotation_bytes = _canonical_jsonl(inputs.annotations)
    unique_bytes = _canonical_jsonl(inputs.unique_rows)
    labels_bytes = _read_bytes(labels_path, label="gold labels")
    source_packets, source_packet_set_hash = _source_packet_records(inputs.sample)
    include_count = sum(label["decision"] == "INCLUDE" for label in inputs.labels)
    exclude_count = sum(label["decision"] == "EXCLUDE" for label in inputs.labels)
    protocol = inputs.sample["protocol"]
    selection = inputs.sample["selection"]
    review = inputs.review
    base = {
        "schema_version": GOLD_MANIFEST_SCHEMA_VERSION,
        "sample_id": inputs.sample["sample_id"],
        "state": "FROZEN_MODEL_GOLD",
        "review_state": "MODEL_ADJUDICATED",
        "review_policy": protocol["review_policy"],
        "protocol_commit": protocol["protocol_commit"],
        "protocol_artifact_id": protocol["protocol_artifact_id"],
        "compiler_artifact_id": protocol["compiler_artifact_id"],
        "schema_artifact_ids": protocol["schema_artifact_ids"],
        "source_selection_commit": selection["source_selection_commit"],
        "source_selection_manifest_artifact_id": selection[
            "source_selection_manifest_artifact_id"
        ],
        "selection_algorithm_id": selection["selection_algorithm_id"],
        "text_snapshot_id": inputs.sample["baseline"]["text_snapshot_id"],
        "text_snapshot_hash": inputs.sample["baseline"]["text_snapshot_hash"],
        "sample_manifest_artifact_id": artifact_id_for(
            _read_bytes(sample_path, label="sample manifest")
        ),
        "sample_manifest_hash": object_hash(inputs.sample, omit=()),
        "source_packets": source_packets,
        "source_packet_set_hash": source_packet_set_hash,
        "input_labels_artifact_id": review["input_labels_artifact_id"],
        "labels_artifact_id": artifact_id_for(labels_bytes),
        "review_manifest_artifact_id": artifact_id_for(
            _read_bytes(review_path, label="review manifest")
        ),
        "review_output_artifact_id": review["review_output_artifact_id"],
        "disputes_artifact_id": review["disputes_artifact_id"],
        "disputed_count": review["disputed_count"],
        "forbidden_inputs": review["forbidden_inputs"],
        "model_reviews": review["model_reviews"],
        "occurrence_jsonl_artifact_id": artifact_id_for(annotation_bytes),
        "unique_jsonl_artifact_id": artifact_id_for(unique_bytes),
        "counts": {
            "unit_count": len(inputs.packets),
            "label_count": len(inputs.labels),
            "include_count": include_count,
            "exclude_count": exclude_count,
            "occurrence_count": len(inputs.annotations),
            "unique_count": len(inputs.unique_rows),
            "disputed_count": len(inputs.disputes),
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
    input_labels_path: pathlib.Path,
    labels_path: pathlib.Path,
    disputes_path: pathlib.Path,
    review_path: pathlib.Path,
    review_artifacts_dir: pathlib.Path,
    occurrences_out: pathlib.Path,
    unique_out: pathlib.Path,
    manifest_out: pathlib.Path,
    label_schema_path: pathlib.Path | None = None,
    review_schema_path: pathlib.Path | None = None,
    annotation_schema_path: pathlib.Path | None = None,
    unique_schema_path: pathlib.Path | None = None,
    dispute_schema_path: pathlib.Path | None = None,
    gold_manifest_schema_path: pathlib.Path | None = None,
    protocol_path: pathlib.Path | None = None,
) -> dict[str, Any]:
    inputs = load_and_validate(
        sample_path=sample_path,
        source_dir=source_dir,
        input_labels_path=input_labels_path,
        labels_path=labels_path,
        disputes_path=disputes_path,
        review_path=review_path,
        review_artifacts_dir=review_artifacts_dir,
        label_schema_path=label_schema_path,
        review_schema_path=review_schema_path,
        annotation_schema_path=annotation_schema_path,
        unique_schema_path=unique_schema_path,
        dispute_schema_path=dispute_schema_path,
        protocol_path=protocol_path,
    )
    manifest, annotation_bytes, unique_bytes = _build_frozen_gold_manifest(
        inputs=inputs,
        sample_path=sample_path,
        source_dir=source_dir,
        labels_path=labels_path,
        review_path=review_path,
    )
    manifest_schema_file = (
        gold_manifest_schema_path or sample_path.parent / "geography-gold-manifest.schema.json"
    )
    _verify_bound_artifact(
        manifest_schema_file,
        inputs.sample["protocol"]["schema_artifact_ids"]["manifest"],
        label="manifest schema",
    )
    manifest_schema = _load_schema(manifest_schema_file, label="frozen gold manifest schema")
    _schema_validate(manifest_schema, manifest, label="frozen gold manifest")
    _write_immutable_set(
        [
            (occurrences_out, annotation_bytes),
            (unique_out, unique_bytes),
            (manifest_out, canonical_dumps(manifest) + b"\n"),
        ]
    )
    return manifest


def validate_frozen_gold(
    *,
    sample_path: pathlib.Path,
    source_dir: pathlib.Path,
    input_labels_path: pathlib.Path,
    labels_path: pathlib.Path,
    disputes_path: pathlib.Path,
    review_path: pathlib.Path,
    review_artifacts_dir: pathlib.Path,
    occurrences_path: pathlib.Path,
    unique_path: pathlib.Path,
    manifest_path: pathlib.Path,
    label_schema_path: pathlib.Path | None = None,
    review_schema_path: pathlib.Path | None = None,
    annotation_schema_path: pathlib.Path | None = None,
    unique_schema_path: pathlib.Path | None = None,
    dispute_schema_path: pathlib.Path | None = None,
    gold_manifest_schema_path: pathlib.Path | None = None,
    protocol_path: pathlib.Path | None = None,
) -> dict[str, Any]:
    inputs = load_and_validate(
        sample_path=sample_path,
        source_dir=source_dir,
        input_labels_path=input_labels_path,
        labels_path=labels_path,
        disputes_path=disputes_path,
        review_path=review_path,
        review_artifacts_dir=review_artifacts_dir,
        label_schema_path=label_schema_path,
        review_schema_path=review_schema_path,
        annotation_schema_path=annotation_schema_path,
        unique_schema_path=unique_schema_path,
        dispute_schema_path=dispute_schema_path,
        protocol_path=protocol_path,
    )
    expected, annotation_bytes, unique_bytes = _build_frozen_gold_manifest(
        inputs=inputs,
        sample_path=sample_path,
        source_dir=source_dir,
        labels_path=labels_path,
        review_path=review_path,
    )
    manifest = _read_canonical_document(manifest_path, label="frozen gold manifest")
    manifest_schema_file = (
        gold_manifest_schema_path or sample_path.parent / "geography-gold-manifest.schema.json"
    )
    _verify_bound_artifact(
        manifest_schema_file,
        inputs.sample["protocol"]["schema_artifact_ids"]["manifest"],
        label="manifest schema",
    )
    manifest_schema = _load_schema(manifest_schema_file, label="frozen gold manifest schema")
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
    require_model_artifacts: bool = False,
) -> None:
    parser.add_argument("--sample", type=pathlib.Path, required=True)
    parser.add_argument("--source-dir", type=pathlib.Path, required=True)
    parser.add_argument("--input-labels", type=pathlib.Path, required=require_model_artifacts)
    parser.add_argument("--labels", type=pathlib.Path, required=True)
    parser.add_argument("--disputes", type=pathlib.Path, required=require_model_artifacts)
    parser.add_argument("--review-manifest", type=pathlib.Path, required=True)
    parser.add_argument(
        "--review-artifacts-dir", type=pathlib.Path, required=require_model_artifacts
    )
    parser.add_argument("--protocol-document", type=pathlib.Path)
    parser.add_argument("--label-schema", type=pathlib.Path)
    parser.add_argument("--review-schema", type=pathlib.Path)
    parser.add_argument("--annotation-schema", type=pathlib.Path)
    parser.add_argument("--unique-schema", type=pathlib.Path)
    parser.add_argument("--dispute-schema", type=pathlib.Path)
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
    prepare.add_argument("--protocol-document", type=pathlib.Path)

    verify_selection = subparsers.add_parser(
        "verify-sample-selection", help="replay controls from the pinned A-1 Git manifest"
    )
    verify_selection.add_argument("--sample", type=pathlib.Path, required=True)
    verify_selection.add_argument("--repository-root", type=pathlib.Path, required=True)

    validate = subparsers.add_parser("validate", help="validate source, labels, and review closure")
    _add_compile_inputs(validate, include_allow_draft=True)

    derive = subparsers.add_parser("derive", help="validate and write occurrence/unique JSONL")
    _add_compile_inputs(derive, include_allow_draft=True)
    derive.add_argument("--occurrences-out", type=pathlib.Path, required=True)
    derive.add_argument("--unique-out", type=pathlib.Path, required=True)

    freeze = subparsers.add_parser(
        "freeze",
        help="commit MODEL_ADJUDICATED occurrence, unique, and FROZEN_MODEL_GOLD artifacts",
    )
    _add_compile_inputs(
        freeze, include_gold_manifest_schema=True, require_model_artifacts=True
    )
    freeze.add_argument("--occurrences-out", type=pathlib.Path, required=True)
    freeze.add_argument("--unique-out", type=pathlib.Path, required=True)
    freeze.add_argument("--gold-manifest-out", type=pathlib.Path, required=True)

    validate_frozen = subparsers.add_parser(
        "validate-frozen", help="replay and validate a complete FROZEN_MODEL_GOLD artifact set"
    )
    _add_compile_inputs(
        validate_frozen, include_gold_manifest_schema=True, require_model_artifacts=True
    )
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
        "review_policy": inputs.review.get("review_policy"),
        "source_packet_count": len(inputs.packets),
        "label_count": len(inputs.labels),
        "labels_artifact_id": artifact_id_for(labels_bytes),
        "occurrence_count": len(inputs.annotations),
        "occurrence_jsonl_artifact_id": artifact_id_for(annotation_bytes),
        "unique_count": len(inputs.unique_rows),
        "unique_jsonl_artifact_id": artifact_id_for(unique_bytes),
        "disputed_count": len(inputs.disputes),
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
                args.protocol_document,
            )
            print(
                json.dumps(
                    {"prepared": len(paths), "output_dir": str(args.output_dir)},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

        if args.command == "verify-sample-selection":
            controls = verify_sample_selection(
                sample_path=args.sample,
                repository_root=args.repository_root,
            )
            print(
                json.dumps(
                    {"selection_algorithm_id": SELECTION_ALGORITHM_ID, "controls": controls},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

        if args.command == "freeze":
            manifest = freeze_gold(
                sample_path=args.sample,
                source_dir=args.source_dir,
                input_labels_path=args.input_labels,
                labels_path=args.labels,
                disputes_path=args.disputes,
                review_path=args.review_manifest,
                review_artifacts_dir=args.review_artifacts_dir,
                occurrences_out=args.occurrences_out,
                unique_out=args.unique_out,
                manifest_out=args.gold_manifest_out,
                label_schema_path=args.label_schema,
                review_schema_path=args.review_schema,
                annotation_schema_path=args.annotation_schema,
                unique_schema_path=args.unique_schema,
                dispute_schema_path=args.dispute_schema,
                gold_manifest_schema_path=args.gold_manifest_schema,
                protocol_path=args.protocol_document,
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
                input_labels_path=args.input_labels,
                labels_path=args.labels,
                disputes_path=args.disputes,
                review_path=args.review_manifest,
                review_artifacts_dir=args.review_artifacts_dir,
                occurrences_path=args.occurrences,
                unique_path=args.unique,
                manifest_path=args.gold_manifest,
                label_schema_path=args.label_schema,
                review_schema_path=args.review_schema,
                annotation_schema_path=args.annotation_schema,
                unique_schema_path=args.unique_schema,
                dispute_schema_path=args.dispute_schema,
                gold_manifest_schema_path=args.gold_manifest_schema,
                protocol_path=args.protocol_document,
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
            input_labels_path=args.input_labels,
            labels_path=args.labels,
            disputes_path=args.disputes,
            review_path=args.review_manifest,
            review_artifacts_dir=args.review_artifacts_dir,
            label_schema_path=args.label_schema,
            review_schema_path=args.review_schema,
            annotation_schema_path=args.annotation_schema,
            unique_schema_path=args.unique_schema,
            dispute_schema_path=args.dispute_schema,
            protocol_path=args.protocol_document,
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
