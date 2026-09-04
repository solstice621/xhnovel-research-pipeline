#!/usr/bin/env python3
"""Recompute geography capacity statistics from immutable answer JSON files.

This is deliberately a standalone experiment tool.  It does not import the
production extraction engine and it never treats executor-reported quantities
as verified measurements.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
from collections import Counter, defaultdict
from typing import Any, Iterable


SCHEMA_VERSION = "geography-capacity-stats/v1"
KINDS = ("PLACE_MENTION", "SPATIAL_RELATION")
RELATIONS = ("LOCATED_IN", "PART_OF", "NEAR", "OUTSIDE", "CONNECTED_TO")
COMPLETION_STATUSES = ("COMPLETE", "OVERFLOW", "UNCERTAIN")
EXECUTOR_ASSERTION_TRUST = "UNVERIFIED_EXECUTOR_ASSERTION"


class StatsValidationError(ValueError):
    """Fail-closed validation error for experiment inputs."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _fail(code: str, message: str) -> None:
    raise StatsValidationError(code, message)


def _duplicate_key_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("E-STATS-JSON-DUPLICATE-KEY", f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    _fail("E-STATS-JSON-CONSTANT", f"non-JSON numeric constant {value!r}")


def _load_json(path: pathlib.Path, *, label: str) -> tuple[bytes, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise StatsValidationError("E-STATS-READ", f"cannot read {label}: {path}") from exc
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StatsValidationError("E-STATS-JSON", f"{label} is not UTF-8: {path}") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_duplicate_key_object,
            parse_constant=_reject_json_constant,
        )
    except StatsValidationError:
        raise
    except json.JSONDecodeError as exc:
        raise StatsValidationError(
            "E-STATS-JSON", f"{label} is not valid JSON at line {exc.lineno}: {path}"
        ) from exc
    return data, value


def _canonical_dumps(value: Any) -> bytes:
    """Encode the subset of canonical JSON used by xhnovel logical payloads."""

    if value is None:
        return b"null"
    if value is True:
        return b"true"
    if value is False:
        return b"false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value).encode("ascii")
    if isinstance(value, float):
        _fail("E-STATS-CANONICAL", "canonical logical payloads cannot contain floats")
    if isinstance(value, str):
        try:
            return json.dumps(value, ensure_ascii=False).encode("utf-8")
        except UnicodeEncodeError as exc:
            raise StatsValidationError(
                "E-STATS-CANONICAL", "canonical strings must be valid Unicode"
            ) from exc
    if isinstance(value, list):
        return b"[" + b",".join(_canonical_dumps(item) for item in value) + b"]"
    if isinstance(value, dict):
        parts: list[bytes] = []
        for key in sorted(value):
            if not isinstance(key, str):
                _fail("E-STATS-CANONICAL", "canonical object keys must be strings")
            parts.append(_canonical_dumps(key) + b":" + _canonical_dumps(value[key]))
        return b"{" + b",".join(parts) + b"}"
    _fail("E-STATS-CANONICAL", f"unsupported canonical type {type(value)!r}")


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _object_hash(value: dict[str, Any]) -> str:
    return _sha256(_canonical_dumps(value))


def _logical_hashes(payload_bytes: Iterable[bytes]) -> dict[str, Any]:
    unique_bytes = sorted(set(payload_bytes))
    hashes: list[str] = []
    seen: dict[str, bytes] = {}
    for encoded in unique_bytes:
        digest = _sha256(encoded)
        prior = seen.get(digest)
        if prior is not None and prior != encoded:
            _fail("E-STATS-HASH-COLLISION", f"distinct canonical payloads share {digest}")
        seen[digest] = encoded
        hashes.append(digest)
    hashes.sort()
    return {
        "payload_hashes": hashes,
        "set_hash": _object_hash({"payload_hashes": hashes}),
    }


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _safe_answer_filename(unit_id: str) -> str:
    readable = re.sub(r"[^A-Za-z0-9._-]+", "_", unit_id).strip("._-") or "unit"
    digest = hashlib.sha256(unit_id.encode("utf-8")).hexdigest()[:12]
    return f"{readable}--{digest}.json"


def _escape_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _geo_cue_warnings(value: Any, *, pointer: str = "") -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_pointer = pointer + "/" + _escape_pointer_token(key)
            if key == "geo_cues":
                # Selected-unit rows also use ``geo_cues`` for an already-computed
                # integer score.  Only cue-list fields participate in duplicate
                # cue validation; the numeric diagnostic is never rescored here.
                if isinstance(child, list) and not all(
                    isinstance(item, str) and item for item in child
                ):
                    _fail(
                        "E-STATS-GEO-CUES",
                        f"{child_pointer} must be an array of non-empty strings",
                    )
                if isinstance(child, list):
                    indexes: dict[str, list[int]] = defaultdict(list)
                    for index, cue in enumerate(child):
                        indexes[cue].append(index)
                    duplicates = [
                        {"value": cue, "count": len(locations), "indexes": locations}
                        for cue, locations in sorted(indexes.items())
                        if len(locations) > 1
                    ]
                    if duplicates:
                        warnings.append(
                            {
                                "code": "DUPLICATE_GEO_CUES",
                                "field": child_pointer,
                                "duplicates": duplicates,
                                "message": (
                                    "duplicate geo_cues are selection diagnostics only and are not "
                                    "silently counted by this statistics tool"
                                ),
                            }
                        )
            warnings.extend(_geo_cue_warnings(child, pointer=child_pointer))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            warnings.extend(_geo_cue_warnings(child, pointer=f"{pointer}/{index}"))
    return warnings


def _sample_units(manifest: Any) -> tuple[list[dict[str, Any]], str]:
    if not isinstance(manifest, dict):
        _fail("E-STATS-SAMPLE", "sample manifest must be a JSON object")
    candidates = [key for key in ("selected", "units") if key in manifest]
    if len(candidates) != 1:
        _fail("E-STATS-SAMPLE", "sample manifest must contain exactly one of selected or units")
    field = candidates[0]
    rows = manifest[field]
    if not isinstance(rows, list) or not rows:
        _fail("E-STATS-SAMPLE", f"sample manifest {field} must be a non-empty array")

    units: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_ordinals: set[int] = set()
    seen_answers: set[str] = set()
    for index, row in enumerate(rows):
        where = f"/{field}/{index}"
        if not isinstance(row, dict):
            _fail("E-STATS-SAMPLE", f"{where} must be an object")
        unit_id = row.get("unit_id")
        ordinal = row.get("ordinal")
        if not isinstance(unit_id, str) or not unit_id:
            _fail("E-STATS-SAMPLE", f"{where}/unit_id must be a non-empty string")
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0:
            _fail("E-STATS-SAMPLE", f"{where}/ordinal must be a non-negative integer")
        expected_filename = _safe_answer_filename(unit_id)
        task = row.get("task")
        if task is not None:
            if not isinstance(task, str) or pathlib.PurePath(task).name != task:
                _fail("E-STATS-TASK-MAPPING", f"{where}/task must be a plain filename")
            if task != expected_filename:
                _fail(
                    "E-STATS-TASK-MAPPING",
                    f"{where}/task {task!r} does not match unit_id {unit_id!r}",
                )
        if unit_id in seen_ids:
            _fail("E-STATS-SAMPLE", f"duplicate sample unit_id {unit_id!r}")
        if ordinal in seen_ordinals:
            _fail("E-STATS-SAMPLE", f"duplicate sample ordinal {ordinal}")
        if expected_filename in seen_answers:
            _fail("E-STATS-TASK-MAPPING", f"duplicate answer mapping {expected_filename!r}")
        seen_ids.add(unit_id)
        seen_ordinals.add(ordinal)
        seen_answers.add(expected_filename)
        units.append(
            {
                "sample_index": index,
                "unit_id": unit_id,
                "ordinal": ordinal,
                "answer_file": expected_filename,
            }
        )
    return units, field


def _decode_pointer_token(token: str, *, where: str) -> str:
    decoded = ""
    index = 0
    while index < len(token):
        char = token[index]
        if char != "~":
            decoded += char
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
            _fail("E-STATS-EVIDENCE-PATH", f"invalid JSON Pointer token at {where}")
        decoded += "~" if token[index + 1] == "0" else "/"
        index += 2
    return decoded


def _resolve_pointer(value: Any, pointer: str, *, where: str) -> Any:
    if pointer == "":
        return value
    if not pointer.startswith("/"):
        _fail("E-STATS-EVIDENCE-PATH", f"invalid JSON Pointer {pointer!r} at {where}")
    current = value
    for raw_token in pointer[1:].split("/"):
        token = _decode_pointer_token(raw_token, where=where)
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and not (
            len(token) > 1 and token.startswith("0")
        ):
            offset = int(token)
            if offset >= len(current):
                _fail("E-STATS-EVIDENCE-PATH", f"pointer {pointer!r} does not exist at {where}")
            current = current[offset]
        else:
            _fail("E-STATS-EVIDENCE-PATH", f"pointer {pointer!r} does not exist at {where}")
    return current


def _path_covers(binding_path: str, required_path: str) -> bool:
    return (
        binding_path == ""
        or binding_path == required_path
        or required_path.startswith(binding_path + "/")
    )


def _validate_payload(payload: Any, *, where: str) -> str:
    if not isinstance(payload, dict):
        _fail("E-STATS-PAYLOAD", f"{where} must be an object")
    kind = payload.get("kind")
    if kind == "PLACE_MENTION":
        required = {"kind", "name"}
        allowed = required | {"explicit_type"}
        if not required.issubset(payload) or set(payload) - allowed:
            _fail("E-STATS-PAYLOAD", f"{where} is not a valid PLACE_MENTION payload")
        for key in allowed & set(payload):
            if key == "kind":
                continue
            if not isinstance(payload[key], str) or not payload[key]:
                _fail("E-STATS-PAYLOAD", f"{where}/{key} must be a non-empty string")
        return kind
    if kind == "SPATIAL_RELATION":
        required = {"kind", "subject_name", "relation", "object_name"}
        if set(payload) != required:
            _fail("E-STATS-PAYLOAD", f"{where} is not a valid SPATIAL_RELATION payload")
        for key in ("subject_name", "object_name"):
            if not isinstance(payload[key], str) or not payload[key]:
                _fail("E-STATS-PAYLOAD", f"{where}/{key} must be a non-empty string")
        if payload["relation"] not in RELATIONS:
            _fail("E-STATS-PAYLOAD", f"{where}/relation is unsupported")
        return kind
    _fail("E-STATS-PAYLOAD", f"{where}/kind must be one of {', '.join(KINDS)}")


def _validate_bindings(bindings: Any, payload: dict[str, Any], *, where: str) -> None:
    if not isinstance(bindings, list) or not bindings:
        _fail("E-STATS-EVIDENCE", f"{where} must be a non-empty array")
    binding_paths: list[list[str]] = []
    for binding_index, binding in enumerate(bindings):
        binding_where = f"{where}/{binding_index}"
        if not isinstance(binding, dict) or set(binding) != {"paths", "source_spans"}:
            _fail("E-STATS-EVIDENCE", f"{binding_where} has invalid fields")
        paths = binding["paths"]
        spans = binding["source_spans"]
        if not isinstance(paths, list) or not paths or not all(isinstance(path, str) for path in paths):
            _fail("E-STATS-EVIDENCE", f"{binding_where}/paths must be a non-empty string array")
        if len(set(paths)) != len(paths):
            _fail("E-STATS-EVIDENCE", f"{binding_where}/paths contains duplicates")
        for path_index, pointer in enumerate(paths):
            _resolve_pointer(payload, pointer, where=f"{binding_where}/paths/{path_index}")
        if not isinstance(spans, list) or not spans:
            _fail("E-STATS-EVIDENCE", f"{binding_where}/source_spans must be non-empty")
        span_keys: set[tuple[str, int, int]] = set()
        for span_index, span in enumerate(spans):
            span_where = f"{binding_where}/source_spans/{span_index}"
            if not isinstance(span, dict) or set(span) != {"segment_id", "start", "end"}:
                _fail("E-STATS-EVIDENCE", f"{span_where} has invalid fields")
            segment_id = span["segment_id"]
            start = span["start"]
            end = span["end"]
            if not isinstance(segment_id, str) or not segment_id:
                _fail("E-STATS-EVIDENCE", f"{span_where}/segment_id must be non-empty")
            if (
                not isinstance(start, int)
                or isinstance(start, bool)
                or not isinstance(end, int)
                or isinstance(end, bool)
                or not 0 <= start < end
            ):
                _fail("E-STATS-EVIDENCE", f"{span_where} offsets must satisfy 0 <= start < end")
            span_key = (segment_id, start, end)
            if span_key in span_keys:
                _fail("E-STATS-EVIDENCE", f"{binding_where}/source_spans contains duplicates")
            span_keys.add(span_key)
        binding_paths.append(paths)

    required_paths = (
        ["/name"]
        if payload["kind"] == "PLACE_MENTION"
        else ["/subject_name", "/relation", "/object_name"]
    )
    if not any(
        all(any(_path_covers(candidate, required) for candidate in paths) for required in required_paths)
        for paths in binding_paths
    ):
        _fail("E-STATS-EVIDENCE", f"{where} lacks the required geography evidence group")
    for key in payload:
        if key == "kind":
            continue
        required = "/" + _escape_pointer_token(key)
        if not any(_path_covers(candidate, required) for paths in binding_paths for candidate in paths):
            _fail("E-STATS-EVIDENCE", f"{where} has no binding for payload field {required}")


def _validate_answer(value: Any, *, answer_file: str) -> tuple[list[dict[str, Any]], str | None]:
    if not isinstance(value, dict):
        _fail("E-STATS-ANSWER", f"{answer_file} must contain a JSON object")
    if "records" not in value or set(value) - {"records", "completion"}:
        _fail("E-STATS-ANSWER", f"{answer_file} must contain records and optional completion only")
    records = value["records"]
    if not isinstance(records, list):
        _fail("E-STATS-ANSWER", f"{answer_file}/records must be an array")
    for index, record in enumerate(records):
        where = f"{answer_file}/records/{index}"
        if not isinstance(record, dict) or set(record) != {"payload", "evidence_bindings"}:
            _fail("E-STATS-ANSWER", f"{where} has invalid fields")
        _validate_payload(record["payload"], where=f"{where}/payload")
        _validate_bindings(
            record["evidence_bindings"], record["payload"], where=f"{where}/evidence_bindings"
        )

    completion_status: str | None = None
    if "completion" in value:
        completion = value["completion"]
        if not isinstance(completion, dict) or set(completion) != {"status"}:
            _fail(
                "E-STATS-COMPLETION",
                f"{answer_file}/completion must contain exactly one status field",
            )
        completion_status = completion["status"]
        if completion_status not in COMPLETION_STATUSES:
            _fail(
                "E-STATS-COMPLETION",
                f"{answer_file}/completion/status must be COMPLETE, OVERFLOW, or UNCERTAIN",
            )
    return records, completion_status


def _names_for(payload: dict[str, Any]) -> set[str]:
    if payload["kind"] == "PLACE_MENTION":
        return {payload["name"]}
    return {payload["subject_name"], payload["object_name"]}


def _kind_stats(raw_count: int, unique_payloads: set[bytes]) -> dict[str, Any]:
    unique_count = len(unique_payloads)
    duplicate_count = raw_count - unique_count
    return {
        "raw_count": raw_count,
        "unit_local_exact_payload_unique": unique_count,
        "duplicate_count": duplicate_count,
        "duplicate_rate": _rate(duplicate_count, raw_count),
        "canonical_logical_hashes": _logical_hashes(unique_payloads),
    }


def _unit_stats(
    unit: dict[str, Any],
    *,
    answer_path: pathlib.Path,
) -> tuple[dict[str, Any], dict[str, set[bytes]], set[str], str | None]:
    response_bytes, answer = _load_json(answer_path, label="answer")
    records, completion_status = _validate_answer(answer, answer_file=answer_path.name)
    raw_by_kind: Counter[str] = Counter()
    payloads_by_kind: dict[str, set[bytes]] = {kind: set() for kind in KINDS}
    all_names: set[str] = set()
    for record in records:
        payload = record["payload"]
        kind = payload["kind"]
        encoded = _canonical_dumps(payload)
        raw_by_kind[kind] += 1
        payloads_by_kind[kind].add(encoded)
        all_names.update(_names_for(payload))

    all_payloads = set().union(*(payloads_by_kind[kind] for kind in KINDS))
    raw_count = len(records)
    unique_count = len(all_payloads)
    duplicate_count = raw_count - unique_count
    output = {
        "sample_index": unit["sample_index"],
        "ordinal": unit["ordinal"],
        "unit_id": unit["unit_id"],
        "answer_file": unit["answer_file"],
        "response_bytes": len(response_bytes),
        "response_sha256": _sha256(response_bytes),
        "raw_count": raw_count,
        "unit_local_exact_payload_unique": unique_count,
        "duplicate_count": duplicate_count,
        "duplicate_rate": _rate(duplicate_count, raw_count),
        "per_kind": {
            kind: _kind_stats(raw_by_kind[kind], payloads_by_kind[kind]) for kind in KINDS
        },
        "distinct_names": {
            "count": len(all_names),
            "values": sorted(all_names),
        },
        "canonical_logical_hashes": _logical_hashes(all_payloads),
        "completion": {
            "status": completion_status,
            "presence": "ANSWER_JSON" if completion_status is not None else "LEGACY_ABSENT",
            "trust": EXECUTOR_ASSERTION_TRUST,
        },
    }
    return output, payloads_by_kind, all_names, completion_status


def _executor_report(
    path: pathlib.Path,
    *,
    sample_units: list[dict[str, Any]],
    answered_ids: set[str],
) -> dict[str, Any]:
    data, value = _load_json(path, label="executor report manifest")
    if not isinstance(value, dict):
        _fail("E-STATS-EXECUTOR-REPORT", "executor report manifest must be an object")
    row_fields = [field for field in ("per_unit", "units") if field in value]
    if len(row_fields) != 1 or not isinstance(value[row_fields[0]], list):
        _fail(
            "E-STATS-EXECUTOR-REPORT",
            "executor report manifest must contain exactly one per_unit or units array",
        )
    by_id = {unit["unit_id"]: unit for unit in sample_units}
    order = {unit["unit_id"]: unit["sample_index"] for unit in sample_units}
    assertions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(value[row_fields[0]]):
        where = f"/{row_fields[0]}/{index}"
        if not isinstance(row, dict):
            _fail("E-STATS-EXECUTOR-REPORT", f"{where} must be an object")
        unit_id = row.get("unit_id")
        if not isinstance(unit_id, str) or unit_id not in by_id:
            _fail("E-STATS-EXECUTOR-REPORT", f"{where}/unit_id does not map to the sample")
        if unit_id in seen:
            _fail("E-STATS-EXECUTOR-REPORT", f"duplicate report unit_id {unit_id!r}")
        seen.add(unit_id)
        unit = by_id[unit_id]
        if "ordinal" in row and row["ordinal"] != unit["ordinal"]:
            _fail("E-STATS-EXECUTOR-REPORT", f"{where}/ordinal disagrees with the sample")
        assertions.append(
            {
                "sample_index": unit["sample_index"],
                "unit_id": unit_id,
                "ordinal": unit["ordinal"],
                "mapped_to_answer": unit_id in answered_ids,
                "trust": EXECUTOR_ASSERTION_TRUST,
                "reported_fields": {
                    key: reported for key, reported in row.items() if key not in {"unit_id", "ordinal"}
                },
            }
        )
    assertions.sort(key=lambda item: order[item["unit_id"]])
    return {
        "provided": True,
        "source": str(path),
        "source_sha256": _sha256(data),
        "trust": EXECUTOR_ASSERTION_TRUST,
        "note": "reported fields are retained for diagnostics and never feed computed statistics",
        "assertions": assertions,
    }


def analyze_capacity(
    sample_manifest: pathlib.Path | str,
    answers_dir: pathlib.Path | str,
    executor_report_manifest: pathlib.Path | str | None = None,
) -> dict[str, Any]:
    """Validate inputs and return deterministic capacity statistics."""

    sample_path = pathlib.Path(sample_manifest)
    answer_root = pathlib.Path(answers_dir)
    sample_bytes, manifest = _load_json(sample_path, label="sample manifest")
    sample_units, sample_units_field = _sample_units(manifest)
    warnings = _geo_cue_warnings(manifest)

    if not answer_root.is_dir():
        _fail("E-STATS-ANSWERS", f"answers directory does not exist: {answer_root}")
    mapped_names = {unit["answer_file"] for unit in sample_units}
    json_paths = sorted(
        path for path in answer_root.iterdir() if path.is_file() and path.suffix.lower() == ".json"
    )
    unknown_names = sorted(path.name for path in json_paths if path.name not in mapped_names)
    if unknown_names:
        _fail(
            "E-STATS-TASK-MAPPING",
            "answer JSON files do not map to sample tasks: " + ", ".join(unknown_names),
        )

    available_names = {path.name for path in json_paths}
    answered_units = [unit for unit in sample_units if unit["answer_file"] in available_names]
    missing_units = [unit for unit in sample_units if unit["answer_file"] not in available_names]
    if not answered_units:
        _fail("E-STATS-ANSWERS", "no answer JSON maps to a sample unit")
    if missing_units:
        warnings.append(
            {
                "code": "MISSING_SAMPLE_ANSWERS",
                "field": f"/{sample_units_field}",
                "message": "statistics cover only sample units with mapped answers",
                "units": [
                    {
                        "sample_index": unit["sample_index"],
                        "ordinal": unit["ordinal"],
                        "unit_id": unit["unit_id"],
                        "answer_file": unit["answer_file"],
                    }
                    for unit in missing_units
                ],
            }
        )

    per_unit: list[dict[str, Any]] = []
    global_payloads: dict[str, set[bytes]] = {kind: set() for kind in KINDS}
    global_names: set[str] = set()
    completion_counts: Counter[str] = Counter()
    for unit in answered_units:
        stats, payloads_by_kind, names, completion_status = _unit_stats(
            unit, answer_path=answer_root / unit["answer_file"]
        )
        per_unit.append(stats)
        for kind in KINDS:
            global_payloads[kind].update(payloads_by_kind[kind])
        global_names.update(names)
        completion_counts[completion_status or "LEGACY_ABSENT"] += 1

    raw_count = sum(unit["raw_count"] for unit in per_unit)
    sum_unit_local_unique = sum(unit["unit_local_exact_payload_unique"] for unit in per_unit)
    global_all_payloads = set().union(*(global_payloads[kind] for kind in KINDS))
    global_unique = len(global_all_payloads)
    duplicate_count = raw_count - sum_unit_local_unique
    total_response_bytes = sum(unit["response_bytes"] for unit in per_unit)
    largest = min(
        per_unit,
        key=lambda unit: (-unit["response_bytes"], unit["sample_index"], unit["unit_id"]),
    )

    aggregate_per_kind: dict[str, dict[str, Any]] = {}
    for kind in KINDS:
        kind_raw = sum(unit["per_kind"][kind]["raw_count"] for unit in per_unit)
        kind_unit_unique = sum(
            unit["per_kind"][kind]["unit_local_exact_payload_unique"] for unit in per_unit
        )
        kind_global_unique = len(global_payloads[kind])
        kind_duplicates = kind_raw - kind_unit_unique
        aggregate_per_kind[kind] = {
            "raw_count": kind_raw,
            "sum_unit_local_unique": kind_unit_unique,
            "global_exact_payload_unique": kind_global_unique,
            "unit_local_duplicate_count": kind_duplicates,
            "unit_local_duplicate_rate": _rate(kind_duplicates, kind_raw),
            "cross_unit_exact_payload_overlap_count": kind_unit_unique - kind_global_unique,
            "canonical_logical_hashes": _logical_hashes(global_payloads[kind]),
        }

    answered_ids = {unit["unit_id"] for unit in answered_units}
    report = (
        _executor_report(
            pathlib.Path(executor_report_manifest),
            sample_units=sample_units,
            answered_ids=answered_ids,
        )
        if executor_report_manifest is not None
        else {
            "provided": False,
            "trust": EXECUTOR_ASSERTION_TRUST,
            "note": "computed statistics do not require an executor report",
            "assertions": [],
        }
    )

    warnings.sort(key=lambda warning: (warning["code"], warning.get("field", "")))
    return {
        "schema_version": SCHEMA_VERSION,
        "definitions": {
            "exact_payload": (
                "byte equality of xhnovel canonical JSON payload objects; no alias resolution, "
                "semantic merge, occurrence inference, or evidence-span contribution"
            ),
            "canonical_logical_hash": (
                "sha256 of xhnovel canonical JSON payload bytes; set_hash is sha256 of canonical "
                "JSON {payload_hashes: sorted unique hashes}"
            ),
            "sum_unit_local_unique": (
                "sum of exact-payload unique counts computed independently inside each answered unit"
            ),
            "global_exact_payload_unique": (
                "exact-payload union across all answered units; this may be smaller than the unit sum"
            ),
            "duplicate_count": "raw_count minus unit-local exact-payload unique count",
            "duplicate_rate": "unit-local duplicate_count divided by raw_count; zero when raw_count is zero",
            "distinct_names": (
                "exact Unicode-string union of PLACE_MENTION.name and "
                "SPATIAL_RELATION.subject_name/object_name; no trimming, Unicode normalization, "
                "case folding, or alias resolution"
            ),
            "response_bytes": "length of the answer file's original bytes, including whitespace",
            "executor_assertions": (
                "completion and optional executor-report fields are UNVERIFIED_EXECUTOR_ASSERTION "
                "diagnostics and never inputs to computed counts"
            ),
        },
        "inputs": {
            "sample_manifest": str(sample_path),
            "sample_manifest_sha256": _sha256(sample_bytes),
            "sample_units_field": sample_units_field,
            "sample_units": len(sample_units),
            "answers_dir": str(answer_root),
            "answers_found": len(answered_units),
        },
        "validation": {
            "status": "WARNINGS" if warnings else "PASS",
            "warning_count": len(warnings),
            "check_would_fail": bool(warnings),
            "warnings": warnings,
        },
        "per_unit": per_unit,
        "aggregate": {
            "answered_units": len(per_unit),
            "raw_count": raw_count,
            "sum_unit_local_unique": sum_unit_local_unique,
            "global_exact_payload_unique": global_unique,
            "unit_local_duplicate_count": duplicate_count,
            "unit_local_duplicate_rate": _rate(duplicate_count, raw_count),
            "cross_unit_exact_payload_overlap_count": sum_unit_local_unique - global_unique,
            "per_kind": aggregate_per_kind,
            "response_bytes": total_response_bytes,
            "largest_response": {
                "sample_index": largest["sample_index"],
                "ordinal": largest["ordinal"],
                "unit_id": largest["unit_id"],
                "answer_file": largest["answer_file"],
                "response_bytes": largest["response_bytes"],
                "response_sha256": largest["response_sha256"],
            },
            "distinct_names": {
                "count": len(global_names),
                "values": sorted(global_names),
            },
            "canonical_logical_hashes": _logical_hashes(global_all_payloads),
            "completion_assertions": {
                "trust": EXECUTOR_ASSERTION_TRUST,
                "counts": {
                    status: completion_counts[status]
                    for status in (*COMPLETION_STATUSES, "LEGACY_ABSENT")
                },
            },
        },
        "executor_report": report,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sample_manifest", type=pathlib.Path)
    parser.add_argument("answers_dir", type=pathlib.Path)
    parser.add_argument(
        "--executor-report-manifest",
        "--executor-report",
        dest="executor_report_manifest",
        type=pathlib.Path,
        help="optional executor assertions; never used as computed statistics",
    )
    parser.add_argument("--output", type=pathlib.Path, help="write JSON here instead of stdout")
    parser.add_argument(
        "--check",
        action="store_true",
        help="return exit 1 when validation warnings exist (including duplicate geo_cues)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = analyze_capacity(
            args.sample_manifest,
            args.answers_dir,
            args.executor_report_manifest,
        )
    except StatsValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output is None:
        sys.stdout.write(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 1 if args.check and result["validation"]["check_would_fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
