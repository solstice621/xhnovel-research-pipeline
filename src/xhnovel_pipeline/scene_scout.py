from __future__ import annotations

import json
import os
import pathlib
import tempfile
from contextlib import contextmanager, nullcontext
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from jsonschema import Draft202012Validator

from .agent_files import (
    AGENT_FILES_EXECUTOR_BUILD_ID,
    AGENT_FILES_EXECUTOR_KIND,
    AGENT_FILES_RESPONSE_FORMAT,
    AgentResponsePending,
    AgentResponsesPending,
    agent_task_bytes,
    decode_agent_answer,
)

from .build_identity import BUILD_IDENTITY_FIELDS, build_source_hash
from .canonical import canonical_dumps
from .catalog import Catalog
from .constants import MODEL_EXECUTOR_BUILD_ID, PROFILE_ID, SCHEMA_VERSION
from .errors import ValidationError
from .hashing import artifact_id_for, object_hash, sorted_ids
from .ids import derived_id
from .model_api import (
    API_EXECUTOR_KIND,
    OPENAI_RESPONSES_FORMAT,
    ModelAttemptTrace,
    ModelCallError,
    ModelCallResult,
    SceneScoutExecutor,
    _response_output_text,
)
from .novel_assessment import resolve_validated_bundle_ingestion
from .runtime import repository_commit
from .schema import validate_schema
from .store import ArtifactStore

DEFAULT_WINDOW_CHARS = 10_000
DEFAULT_OVERLAP_CHARS = 1_800
DEFAULT_MAX_WORKERS = 8
SCENE_MERGE_ALGORITHM_ID = "source-span-action-chain-v2"
PROFILE_DIR = pathlib.Path("profiles/xuanhuan-gameplay-scene-v1")
PROMPT_FILE = PROFILE_DIR / "neutral-prompt.md"
OUTPUT_SCHEMA_FILE = PROFILE_DIR / "scene-scout-output.schema.json"
OBSERVATION_FIELDS = (
    "actors",
    "action",
    "target",
    "precondition",
    "state_transition",
    "external_response",
    "immediate_feedback",
    "new_affordances",
    "persistence",
    "mechanic_pressure_point",
)
SCOUT_CHECKPOINT_VERSION = "scene-scout-checkpoint-v1"
CHECKPOINT_INTEGRITY_FIELD = "integrity_hash"
WORK_DIR_LOCK_NAME = ".scene-scout.lock"
# Integrity failures are never a recoverable per-window model failure: a tampered
# task, a corrupt artifact, or a broken checkpoint means the run directory itself
# can no longer be trusted, so abort the whole run with the native code instead of
# demoting it to E-SCENE-PARTIAL.
INTEGRITY_HARD_ABORT_CODES = frozenset(
    {"E-AGENT-TASK-TAMPER", "E-ARTIFACT-CORRUPT", "E-SCENE-CHECKPOINT"}
)


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


def _lock_file_handle(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file_handle(handle: Any) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _exclusive_scene_work_dir(work_dir: pathlib.Path):
    work_dir.mkdir(parents=True, exist_ok=True)
    lock_path = work_dir / WORK_DIR_LOCK_NAME
    try:
        handle = lock_path.open("a+b")
    except OSError as exc:
        raise ValidationError("E-SCENE-WORKDIR-LOCK", f"cannot open {lock_path}") from exc
    locked = False
    try:
        try:
            _lock_file_handle(handle)
            locked = True
        except OSError as exc:
            raise ValidationError(
                "E-SCENE-WORKDIR-LOCKED",
                f"another Scene Scout is already using {work_dir}",
            ) from exc
        yield
    finally:
        try:
            if locked:
                _unlock_file_handle(handle)
        finally:
            handle.close()


def _checkpoint_integrity(state: dict[str, Any]) -> str:
    return object_hash(state, omit=(CHECKPOINT_INTEGRITY_FIELD,))


def _seal_checkpoint(state: dict[str, Any]) -> bytes:
    state[CHECKPOINT_INTEGRITY_FIELD] = _checkpoint_integrity(state)
    return canonical_dumps(state)


def _load_checkpoint(path: pathlib.Path, identity: dict[str, Any]) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("E-SCENE-CHECKPOINT", f"cannot read {path}") from exc
    if (
        not isinstance(state, dict)
        or state.get(CHECKPOINT_INTEGRITY_FIELD) != _checkpoint_integrity(state)
        or any(state.get(key) != value for key, value in identity.items())
        or not isinstance(state.get("completed"), dict)
        or not isinstance(state.get("failures"), dict)
        or not isinstance(state.get("attempt_record_artifact_ids"), list)
        or len(state["attempt_record_artifact_ids"])
        != len(set(state["attempt_record_artifact_ids"]))
    ):
        raise ValidationError("E-SCENE-CHECKPOINT", "scene scout checkpoint identity is invalid")
    return state


def _checkpoint_path(work_dir: pathlib.Path, identity: dict[str, Any]) -> pathlib.Path:
    digest = object_hash(identity, omit=()).removeprefix("sha256:")
    return pathlib.Path(work_dir) / "checkpoints" / f"{digest}.json"


def _put_artifact(
    catalog: Catalog,
    store: ArtifactStore,
    data: bytes,
    *,
    media_type: str,
    created_at: str,
) -> str:
    artifact_id = store.put(data)
    if artifact_id not in catalog.ids("Artifact"):
        catalog.add(
            "Artifact",
            {
                "schema_version": SCHEMA_VERSION,
                "artifact_id": artifact_id,
                "media_type": media_type,
                "byte_length": len(data),
                "retention_policy": "retention-v1",
                "durability_status": "LOCAL",
                "created_at": created_at,
            },
        )
    return artifact_id


def _persist_model_attempts(
    catalog: Catalog,
    store: ArtifactStore,
    state: dict[str, Any],
    *,
    window_id: str,
    request_bytes: bytes,
    traces: tuple[ModelAttemptTrace, ...],
    created_at: str,
) -> tuple[str, list[dict[str, Any]]]:
    request_artifact_id = _put_artifact(
        catalog,
        store,
        request_bytes,
        media_type="application/json",
        created_at=created_at,
    )
    existing = sorted(
        (
            attempt
            for attempt in catalog.all("ModelAttempt")
            if attempt["subject_id"] == window_id
        ),
        key=lambda attempt: attempt["attempt_ordinal"],
    )
    retry_of = existing[-1]["attempt_id"] if existing else None
    next_ordinal = len(existing) + 1
    records: list[dict[str, Any]] = []
    for offset, trace in enumerate(traces):
        response_artifact_id = (
            _put_artifact(
                catalog,
                store,
                trace.response_bytes,
                media_type="application/json",
                created_at=created_at,
            )
            if trace.response_bytes
            else None
        )
        base = {
            "schema_version": SCHEMA_VERSION,
            "operation": "SCENE_SCOUT",
            "subject_id": window_id,
            "request_artifact_id": request_artifact_id,
            "response_artifact_id": response_artifact_id,
            "attempt_ordinal": next_ordinal + offset,
            "status": trace.status,
            "http_status": trace.http_status,
            "error_code": trace.error_code,
            "error_message": trace.error_message,
            "provider_response_id": trace.response_id,
            "usage": {
                **trace.usage,
                "estimated_cost_microusd": None,
            },
            "retry_of": retry_of,
            "recorded_at": created_at,
        }
        record = {**base, "attempt_id": derived_id("ModelAttempt", base)}
        validate_schema("ModelAttempt", record)
        catalog.add("ModelAttempt", record)
        receipt_artifact_id = _put_artifact(
            catalog,
            store,
            canonical_dumps(record),
            media_type="application/vnd.xhnovel.model-attempt+json",
            created_at=created_at,
        )
        state["attempt_record_artifact_ids"].append(receipt_artifact_id)
        records.append(record)
        retry_of = record["attempt_id"]
    return request_artifact_id, records


def _restore_model_attempts(
    catalog: Catalog,
    store: ArtifactStore,
    state: dict[str, Any],
    *,
    created_at: str,
) -> None:
    for receipt_artifact_id in state["attempt_record_artifact_ids"]:
        raw = store.get(receipt_artifact_id)
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("E-SCENE-CHECKPOINT", "attempt receipt is not JSON") from exc
        if raw != canonical_dumps(record):
            raise ValidationError("E-SCENE-CHECKPOINT", "attempt receipt is not canonical")
        artifact_created_at = record.get("recorded_at", created_at)
        for field in ("request_artifact_id", "response_artifact_id"):
            artifact_id = record.get(field)
            if artifact_id:
                _put_artifact(
                    catalog,
                    store,
                    store.get(artifact_id),
                    media_type="application/json",
                    created_at=artifact_created_at,
                )
        _put_artifact(
            catalog,
            store,
            raw,
            media_type="application/vnd.xhnovel.model-attempt+json",
            created_at=artifact_created_at,
        )
        validate_schema("ModelAttempt", record)
        if record["attempt_id"] not in set(catalog.ids("ModelAttempt")):
            catalog.add("ModelAttempt", record)


def _load_profile(repo_root: pathlib.Path) -> tuple[str, bytes, dict[str, Any], bytes]:
    prompt_bytes = (repo_root / PROMPT_FILE).read_bytes()
    schema_bytes = (repo_root / OUTPUT_SCHEMA_FILE).read_bytes()
    try:
        prompt = prompt_bytes.decode("utf-8")
        schema = json.loads(schema_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("E-SCENE-PROFILE", "scene scout profile is not valid UTF-8/JSON") from exc
    return prompt, prompt_bytes, schema, schema_bytes


def bundle_chapter_index(
    catalog: Catalog,
    bundle: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], str]:
    segment_ids = bundle.get("segment_ids")
    if not isinstance(segment_ids, list) or not segment_ids or len(segment_ids) != len(set(segment_ids)):
        raise ValidationError(
            "E-SCENE-LINEAGE", "scene scouting requires a non-empty unique bundle segment set"
        )
    wanted = set(segment_ids)
    document_ids = set(bundle.get("document_ids") or [])
    retrieval_ids = set(bundle.get("retrieval_ids") or [])
    artifact_ids = set(bundle.get("artifact_ids") or [])
    matches_by_segment: dict[str, list[dict[str, Any]]] = {
        segment_id: [] for segment_id in wanted
    }
    for chapter in catalog.all("NovelChapter"):
        if (
            chapter["document_id"] not in document_ids
            or chapter["retrieval_id"] not in retrieval_ids
            or chapter["artifact_id"] not in artifact_ids
        ):
            continue
        for segment_id in wanted & set(chapter.get("segment_ids") or []):
            matches_by_segment[segment_id].append(chapter)
    chapter_by_segment: dict[str, dict[str, Any]] = {}
    for segment_id, matches in matches_by_segment.items():
        if len(matches) != 1:
            raise ValidationError(
                "E-SCENE-LINEAGE",
                f"bundle segment {segment_id} must belong to exactly one novel chapter",
            )
        segment = catalog.get("Segment", segment_id)
        chapter = matches[0]
        if segment["document_id"] != chapter["document_id"]:
            raise ValidationError("E-SCENE-LINEAGE", f"segment {segment_id} document differs")
        chapter_by_segment[segment_id] = chapter
    work_ids = {chapter["work_id"] for chapter in chapter_by_segment.values()}
    if len(work_ids) != 1:
        raise ValidationError("E-SCENE-LINEAGE", "one scout run cannot mix novel works")
    return chapter_by_segment, next(iter(work_ids))


def _ordered_segments(
    catalog: Catalog,
    bundle: dict[str, Any],
    chapter_by_segment: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    wanted = set(bundle["segment_ids"])
    all_segments = [segment for segment in catalog.all("Segment") if segment["segment_id"] in wanted]
    if len(all_segments) != len(wanted):
        raise ValidationError("E-SCENE-LINEAGE", "bundle segment catalog is incomplete")
    event_fact_retrieval_ids = {
        assessment["retrieval_id"]
        for assessment in catalog.all("TriageAssessment")
        if assessment["assessment_id"] in set(bundle.get("triage_assessment_ids") or [])
        and "event-facts" in assessment.get("allowed_uses", [])
    }
    segments = [
        segment
        for segment in all_segments
        if chapter_by_segment[segment["segment_id"]]["retrieval_id"]
        in event_fact_retrieval_ids
    ]
    segments.sort(
        key=lambda segment: (
            chapter_by_segment[segment["segment_id"]]["ordinal"],
            segment["ordinal"],
            segment["segment_id"],
        )
    )
    return segments


def build_scene_windows(
    catalog: Catalog,
    bundle: dict[str, Any],
    *,
    request_id: str,
    window_chars: int = DEFAULT_WINDOW_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[dict[str, Any]]:
    if not 8_000 <= window_chars <= 12_000:
        raise ValidationError("E-SCENE-WINDOW", "window_chars must be between 8000 and 12000")
    if not 0.15 <= overlap_chars / window_chars <= 0.20:
        raise ValidationError("E-SCENE-WINDOW", "overlap must be between 15% and 20%")
    chapter_by_segment, _ = bundle_chapter_index(catalog, bundle)
    segments = _ordered_segments(catalog, bundle, chapter_by_segment)
    positions: list[tuple[dict[str, Any], int, int]] = []
    cursor = 0
    for index, segment in enumerate(segments):
        if index:
            cursor += 2
        start = cursor
        cursor += len(segment["normalized_text"])
        positions.append((segment, start, cursor))
    if cursor == 0:
        return []
    step = window_chars - overlap_chars
    starts = list(range(0, cursor, step))
    windows: list[dict[str, Any]] = []
    for start in starts:
        end = min(cursor, start + window_chars)
        spans = []
        for segment, segment_start, segment_end in positions:
            left = max(start, segment_start)
            right = min(end, segment_end)
            if left >= right:
                continue
            spans.append(
                {
                    "segment_id": segment["segment_id"],
                    "start": left - segment_start,
                    "end": right - segment_start,
                    "normalized_text_hash": segment["normalized_text_hash"],
                }
            )
        if not spans:
            continue
        ordinal = len(windows) + 1
        identity = {
            "request_id": request_id,
            "bundle_id": bundle["bundle_id"],
            "ordinal": ordinal,
            "source_spans": spans,
            "window_chars": window_chars,
            "overlap_chars": overlap_chars,
        }
        window_hash = object_hash(identity, omit=())
        windows.append(
            {
                "schema_version": SCHEMA_VERSION,
                "window_id": derived_id("SceneWindow", {"window_hash": window_hash}),
                **identity,
                "text_length": sum(span["end"] - span["start"] for span in spans),
                "window_hash": window_hash,
            }
        )
        if end == cursor:
            break
    return windows


def _window_input(
    catalog: Catalog,
    window: dict[str, Any],
    *,
    discovery_brief: str,
) -> dict[str, Any]:
    return {
        "request_id": window["request_id"],
        "discovery_brief": discovery_brief,
        "profile_id": PROFILE_ID,
        "window": {
            "window_id": window["window_id"],
            "ordinal": window["ordinal"],
            "source_spans": [
                {
                    **span,
                    "untrusted_text": catalog.get("Segment", span["segment_id"])[
                        "normalized_text"
                    ][span["start"] : span["end"]],
                }
                for span in window["source_spans"]
            ],
        },
    }


def _decode_executor_output(response_format: str, response_bytes: bytes) -> dict[str, Any]:
    if response_format == AGENT_FILES_RESPONSE_FORMAT:
        return decode_agent_answer(response_bytes)
    if response_format == OPENAI_RESPONSES_FORMAT:
        try:
            response = json.loads(response_bytes.decode("utf-8"))
            output = json.loads(_response_output_text(response))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise ValidationError("E-MODEL-RESPONSE", "stored model response is invalid") from exc
        if not isinstance(output, dict):
            raise ValidationError("E-MODEL-OUTPUT", "model output must be an object")
        return output
    raise ValidationError(
        "E-SCENE-EXECUTOR",
        f"unsupported Scene Scout response format {response_format!r}",
    )


def _span_key(span: dict[str, Any]) -> tuple[str, int, int]:
    return span["segment_id"], int(span["start"]), int(span["end"])


def _close_candidate_support_spans(candidate: dict[str, Any]) -> None:
    """Close the redundant top-level span list over every observation support span."""
    spans_by_key = {
        _span_key(span): dict(span) for span in candidate["source_spans"]
    }
    for field in OBSERVATION_FIELDS:
        for span in candidate[field]["support_spans"]:
            spans_by_key.setdefault(_span_key(span), dict(span))
    candidate["source_spans"] = [
        spans_by_key[key] for key in sorted(spans_by_key)
    ]


def _validate_scout_output(
    catalog: Catalog,
    value: dict[str, Any],
    *,
    output_schema: dict[str, Any],
    window: dict[str, Any],
) -> list[dict[str, Any]]:
    errors = sorted(
        Draft202012Validator(output_schema).iter_errors(value), key=lambda error: list(error.path)
    )
    if errors:
        raise ValidationError("E-MODEL-OUTPUT", f"scene scout output: {errors[0].message}")
    for candidate in value["candidates"]:
        _close_candidate_support_spans(candidate)
    allowed: dict[str, list[tuple[int, int]]] = {}
    for span in window["source_spans"]:
        allowed.setdefault(span["segment_id"], []).append((span["start"], span["end"]))
    for candidate in value["candidates"]:
        candidate_spans = {_span_key(span) for span in candidate["source_spans"]}
        for span in candidate["source_spans"]:
            segment = catalog.get("Segment", span["segment_id"])
            if (
                not 0 <= span["start"] < span["end"] <= len(segment["normalized_text"])
                or not any(
                    left <= span["start"] and span["end"] <= right
                    for left, right in allowed.get(span["segment_id"], [])
                )
            ):
                raise ValidationError("E-MODEL-CITATION", "scene span is outside its input window")
        for field in OBSERVATION_FIELDS:
            observation = candidate[field]
            support_keys = {_span_key(span) for span in observation["support_spans"]}
            if not support_keys <= candidate_spans:
                raise ValidationError(
                    "E-MODEL-CITATION", f"{field} cites a span outside its scene candidate"
                )
            if observation["status"] == "UNKNOWN" and (
                observation["values"] or observation["support_spans"]
            ):
                raise ValidationError("E-MODEL-OUTPUT", f"UNKNOWN {field} must have no values/support")
            if observation["status"] != "UNKNOWN" and (
                not observation["values"] or not observation["support_spans"]
            ):
                raise ValidationError(
                    "E-MODEL-OUTPUT", f"known/conflicting {field} needs values and support"
                )
            if observation["status"] == "CONFLICTING" and len(observation["values"]) < 2:
                raise ValidationError("E-MODEL-OUTPUT", f"CONFLICTING {field} needs two values")
    return value["candidates"]


def _normalized_signature(candidate: dict[str, Any]) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    normalize = lambda value: "".join(str(value).casefold().split())
    return (
        normalize(candidate["summary"]),
        tuple(sorted(normalize(value) for value in candidate["action"]["values"])),
        tuple(sorted(normalize(value) for value in candidate["target"]["values"])),
    )


def _normalized_observation_values(candidate: dict[str, Any], field: str) -> set[str]:
    return {"".join(str(value).casefold().split()) for value in candidate[field]["values"]}


def _candidates_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    span_overlap = False
    for first in left["source_spans"]:
        for second in right["source_spans"]:
            if first["segment_id"] == second["segment_id"] and max(
                first["start"], second["start"]
            ) < min(first["end"], second["end"]):
                span_overlap = True
                break
        if span_overlap:
            break
    if not span_overlap:
        return False
    left_signature = _normalized_signature(left)
    return (
        bool(left_signature[0])
        and left_signature == _normalized_signature(right)
    ) or any(
        _normalized_observation_values(left, field)
        & _normalized_observation_values(right, field)
        for field in ("actors", "action", "target")
    )


def _merge_spans(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for span in sorted(spans, key=_span_key):
        if (
            result
            and result[-1]["segment_id"] == span["segment_id"]
            and span["start"] <= result[-1]["end"]
        ):
            result[-1]["end"] = max(result[-1]["end"], span["end"])
        elif not result or _span_key(result[-1]) != _span_key(span):
            result.append(dict(span))
    return result


def _merge_observations(values: list[dict[str, Any]]) -> dict[str, Any]:
    merged_values = sorted({item for value in values for item in value["values"]})
    support = _merge_spans([span for value in values for span in value["support_spans"]])
    if not merged_values:
        return {"status": "UNKNOWN", "values": [], "support_spans": []}
    normalized_value_sets = {
        tuple(sorted("".join(item.casefold().split()) for item in value["values"]))
        for value in values
        if value["values"]
    }
    status = (
        "CONFLICTING"
        if any(value["status"] == "CONFLICTING" for value in values)
        or len(normalized_value_sets) > 1
        else "KNOWN"
    )
    return {"status": status, "values": merged_values, "support_spans": support}


def merge_scene_candidates(
    catalog: Catalog,
    raw_candidates: list[dict[str, Any]],
    *,
    request_id: str,
    bundle_id: str,
    scout_run_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    bundle = catalog.get("EvidenceBundle", bundle_id)
    chapter_by_segment, _ = bundle_chapter_index(catalog, bundle)
    chapter_buckets: dict[int, list[dict[str, Any]]] = {}
    for candidate in raw_candidates:
        chapter_ordinal = min(
            chapter_by_segment[span["segment_id"]]["ordinal"]
            for span in candidate["source_spans"]
        )
        chapter_buckets.setdefault(chapter_ordinal, []).append(candidate)
    local_stage_groups: list[list[dict[str, Any]]] = []
    for chapter_ordinal in sorted(chapter_buckets):
        local_groups: list[list[dict[str, Any]]] = []
        ordered = sorted(
            chapter_buckets[chapter_ordinal],
            key=lambda item: (
                min(
                    (
                        chapter_by_segment[span["segment_id"]]["ordinal"],
                        catalog.get("Segment", span["segment_id"])["ordinal"],
                        span["start"],
                        span["segment_id"],
                    )
                    for span in item["source_spans"]
                ),
                item["raw_hash"],
            ),
        )
        for candidate in ordered:
            matched = [
                group
                for group in local_groups
                if all(_candidates_overlap(candidate, item) for item in group)
            ]
            if not matched:
                local_groups.append([candidate])
                continue
            primary = matched[0]
            primary.append(candidate)
        local_stage_groups.extend(local_groups)

    def group_order(group: list[dict[str, Any]]) -> tuple[Any, ...]:
        return min(
            (
                chapter_by_segment[span["segment_id"]]["ordinal"],
                catalog.get("Segment", span["segment_id"])["ordinal"],
                span["start"],
                span["segment_id"],
                item["raw_hash"],
            )
            for item in group
            for span in item["source_spans"]
        )

    groups: list[list[dict[str, Any]]] = []
    for local_group in sorted(local_stage_groups, key=group_order):
        matched = [
            group
            for group in groups
            if all(
                _candidates_overlap(left, right)
                for left in local_group
                for right in group
            )
        ]
        if matched:
            matched[0].extend(local_group)
        else:
            groups.append(list(local_group))
    input_hashes = sorted(item["raw_hash"] for item in raw_candidates)
    merge_identity = {
        "scene_scout_run_id": scout_run_id,
        "algorithm_id": SCENE_MERGE_ALGORITHM_ID,
        "input_candidate_hashes": input_hashes,
    }
    merge_run_id = derived_id("SceneMergeRun", merge_identity)
    merged: list[dict[str, Any]] = []
    for group in groups:
        spans = _merge_spans([span for item in group for span in item["source_spans"]])
        first_span = min(
            spans,
            key=lambda span: (
                chapter_by_segment[span["segment_id"]]["ordinal"],
                catalog.get("Segment", span["segment_id"])["ordinal"],
                span["start"],
                span["segment_id"],
            ),
        )
        segment = catalog.get("Segment", first_span["segment_id"])
        candidate = {
            "schema_version": SCHEMA_VERSION,
            "scene_scout_run_id": scout_run_id,
            "scene_merge_run_id": merge_run_id,
            "request_id": request_id,
            "bundle_id": bundle_id,
            "status": "DRAFT",
            "verification": "UNVERIFIED",
            "summary": max((item["summary"] for item in group), key=lambda item: (len(item), item)),
            "source_spans": spans,
            "window_ids": sorted({item["window_id"] for item in group}),
            "source_order": {
                "chapter_id": chapter_by_segment[first_span["segment_id"]]["chapter_id"],
                "chapter_ordinal": chapter_by_segment[first_span["segment_id"]]["ordinal"],
                "document_id": segment["document_id"],
                "segment_id": segment["segment_id"],
                "segment_ordinal": segment["ordinal"],
                "start": first_span["start"],
            },
            **{
                field: _merge_observations([item[field] for item in group])
                for field in OBSERVATION_FIELDS
            },
        }
        candidate["adjudication_status"] = (
            "NEEDS_ADJUDICATION"
            if any(candidate[field]["status"] == "CONFLICTING" for field in OBSERVATION_FIELDS)
            else "NOT_REQUIRED"
        )
        candidate["scene_candidate_id"] = derived_id("SceneCandidate", candidate)
        merged.append(candidate)
    merged.sort(
        key=lambda item: (
            item["source_order"]["chapter_ordinal"],
            item["source_order"]["segment_ordinal"],
            item["source_order"]["start"],
            item["scene_candidate_id"],
        )
    )
    merge_run = {
        "schema_version": SCHEMA_VERSION,
        "merge_run_id": merge_run_id,
        **merge_identity,
        "input_candidate_count": len(raw_candidates),
        "stages": [
            {
                "stage": "LOCAL_OVERLAP_MERGE",
                "input_count": len(raw_candidates),
                "output_count": len(local_stage_groups),
            },
            {
                "stage": "WORK_ORDER_REDUCTION",
                "input_count": len(local_stage_groups),
                "output_count": len(merged),
            },
        ],
        "output_candidate_ids": [item["scene_candidate_id"] for item in merged],
        "status": "SUCCEEDED",
    }
    return merge_run, merged


def make_scout_build(
    client: SceneScoutExecutor,
    *,
    repo_root: pathlib.Path,
    created_at: str,
    prompt_bytes: bytes,
    schema_bytes: bytes,
    max_input_chars: int,
    max_request_bytes: int,
    window_chars: int,
    overlap_chars: int,
    max_workers: int,
) -> dict[str, Any]:
    identity = {
        "repository_commit": repository_commit(repo_root),
        "source_tree_hash": build_source_hash(repo_root),
        "model": client.model,
        "prompt_template_hash": artifact_id_for(prompt_bytes),
        "parameters": {
            "executor_kind": client.executor_kind,
            "response_format": client.response_format,
            "endpoint": client.endpoint,
            "timeout_seconds": format(client.timeout, ".17g"),
            "max_attempts": client.max_attempts,
            "structured_output": True,
            "max_input_chars": max_input_chars,
            "max_request_bytes": max_request_bytes,
            "window_chars": window_chars,
            "overlap_chars": overlap_chars,
            "max_workers": max_workers,
            "output_schema_hash": artifact_id_for(schema_bytes),
        },
        "profile_version": PROFILE_ID,
        "executor_build_id": client.executor_build_id,
        "tool_policy_hash": object_hash({"tools": []}, omit=()),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "extractor_build_id": derived_id(
            "ExtractorBuild", {key: identity[key] for key in BUILD_IDENTITY_FIELDS}
        ),
        **identity,
        "created_at": created_at,
        "status": "UNQUALIFIED",
    }


def _run_scene_scout_locked(
    catalog: Catalog,
    store: ArtifactStore,
    bundle: dict[str, Any],
    *,
    client: SceneScoutExecutor,
    repo_root: pathlib.Path,
    created_at: str,
    window_chars: int = DEFAULT_WINDOW_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
    max_input_chars: int = 20_000,
    max_request_bytes: int = 2_000_000,
    max_workers: int = DEFAULT_MAX_WORKERS,
    work_dir: pathlib.Path | None = None,
) -> dict[str, Any]:
    if bundle.get("status") not in {"FROZEN", "EXTRACTED", "EXPORTED"}:
        raise ValidationError("E-FROZEN", "scene scout requires a frozen bundle")
    if catalog.get("EvidenceBundle", bundle.get("bundle_id", "")) != bundle:
        raise ValidationError("E-SCENE-LINEAGE", "scene scout requires the stored frozen bundle")
    resolve_validated_bundle_ingestion(
        catalog,
        store,
        bundle,
        require_external_model=True,
    )
    if not isinstance(max_workers, int) or isinstance(max_workers, bool) or not 1 <= max_workers <= 64:
        raise ValidationError("E-SCENE-CONCURRENCY", "max_workers must be between 1 and 64")
    for field, value in (
        ("max_input_chars", max_input_chars),
        ("max_request_bytes", max_request_bytes),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValidationError("E-SCENE-CONFIG", f"{field} must be a positive integer")
    request = catalog.get("ResearchRequest", bundle["request_id"])
    discovery_brief = request["discovery_brief"]
    prompt, prompt_bytes, output_schema, schema_bytes = _load_profile(repo_root)
    build = make_scout_build(
        client,
        repo_root=repo_root,
        created_at=created_at,
        prompt_bytes=prompt_bytes,
        schema_bytes=schema_bytes,
        max_input_chars=max_input_chars,
        max_request_bytes=max_request_bytes,
        window_chars=window_chars,
        overlap_chars=overlap_chars,
        max_workers=max_workers,
    )
    windows = build_scene_windows(
        catalog,
        bundle,
        request_id=request["request_id"],
        window_chars=window_chars,
        overlap_chars=overlap_chars,
    )
    for window in windows:
        validate_schema("SceneWindow", window)
        catalog.add("SceneWindow", window)

    checkpoint_identity = {
        "checkpoint_version": SCOUT_CHECKPOINT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "request_id": request["request_id"],
        "bundle_id": bundle["bundle_id"],
        "bundle_hash": bundle["bundle_hash"],
        "extractor_build_id": build["extractor_build_id"],
        "discovery_brief_hash": object_hash({"discovery_brief": discovery_brief}, omit=()),
        "window_ids": [window["window_id"] for window in windows],
    }
    checkpoint_file = _checkpoint_path(work_dir, checkpoint_identity) if work_dir else None
    state = _load_checkpoint(checkpoint_file, checkpoint_identity) if checkpoint_file else None
    resumed = state is not None and bool(state["completed"] or state["failures"])
    if state is None:
        state = {
            **checkpoint_identity,
            "created_at": created_at,
            "completed": {},
            "failures": {},
            "attempt_record_artifact_ids": [],
            "status": "RUNNING",
        }
    elif not isinstance(state.get("created_at"), str):
        raise ValidationError("E-SCENE-CHECKPOINT", "scene checkpoint lacks its creation time")

    run_created_at = state["created_at"]
    stored_build = (
        catalog.get("ExtractorBuild", build["extractor_build_id"])
        if build["extractor_build_id"] in catalog.ids("ExtractorBuild")
        else None
    )
    if stored_build is not None:
        build = stored_build
    else:
        build["created_at"] = run_created_at
        catalog.add("ExtractorBuild", build)

    _restore_model_attempts(catalog, store, state, created_at=run_created_at)

    def write_checkpoint() -> None:
        data = _seal_checkpoint(state)
        if checkpoint_file is not None:
            _atomic_write(checkpoint_file, data)

    window_by_id = {window["window_id"]: window for window in windows}
    for window_id, completed in list(state["completed"].items()):
        if window_id not in window_by_id or not isinstance(completed, dict) or set(completed) != {
            "model_request_artifact_id",
            "provider_response_artifact_id",
        }:
            raise ValidationError("E-SCENE-CHECKPOINT", "completed window checkpoint is invalid")
        for artifact_id in completed.values():
            store.verify(artifact_id)
            _put_artifact(
                catalog,
                store,
                store.get(artifact_id),
                media_type="application/json",
                created_at=created_at,
            )

    def invoke(window: dict[str, Any]) -> ModelCallResult:
        input_value = _window_input(catalog, window, discovery_brief=discovery_brief)
        if len(canonical_dumps(input_value).decode("utf-8")) > max_input_chars:
            raise ValidationError("E-MODEL-CONTEXT", f"window {window['window_id']} exceeds context limit")
        request_bytes = client.json_request_bytes(
            instructions=prompt,
            input_value=input_value,
            schema_name="xuanhuan_scene_candidates",
            schema=output_schema,
        )
        if len(request_bytes) > max_request_bytes:
            raise ValidationError(
                "E-MODEL-CONTEXT",
                f"window {window['window_id']} full request exceeds byte limit",
            )
        return client.generate_json(
            instructions=prompt,
            input_value=input_value,
            schema_name="xuanhuan_scene_candidates",
            schema=output_schema,
        )

    pending = [window for window in windows if window["window_id"] not in state["completed"]]
    pending_responses: dict[str, AgentResponsePending] = {}
    if pending:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(pending))) as executor:
            futures = {executor.submit(invoke, window): window for window in pending}
            for future in as_completed(futures):
                window = futures[future]
                window_id = window["window_id"]
                try:
                    call = future.result()
                except AgentResponsePending as exc:
                    pending_responses[window_id] = exc
                except ModelCallError as exc:
                    _persist_model_attempts(
                        catalog,
                        store,
                        state,
                        window_id=window_id,
                        request_bytes=exc.request_bytes,
                        traces=exc.attempts,
                        created_at=created_at,
                    )
                    state["failures"][window_id] = {
                        "error_code": exc.code,
                        "error_message": str(exc),
                    }
                except Exception as exc:
                    if getattr(exc, "code", None) in INTEGRITY_HARD_ABORT_CODES:
                        raise
                    state["failures"][window_id] = {
                        "error_code": getattr(exc, "code", "E-SCENE-WORKER"),
                        "error_message": str(exc),
                    }
                else:
                    traces = call.attempts
                    try:
                        _validate_scout_output(
                            catalog, call.value, output_schema=output_schema, window=window
                        )
                    except Exception as exc:
                        if getattr(exc, "code", None) in INTEGRITY_HARD_ABORT_CODES:
                            raise
                        last = traces[-1]
                        traces = (
                            *traces[:-1],
                            ModelAttemptTrace(
                                ordinal=last.ordinal,
                                status="REJECTED",
                                http_status=last.http_status,
                                response_bytes=last.response_bytes,
                                error_code=getattr(exc, "code", "E-MODEL-OUTPUT"),
                                error_message=str(exc),
                                response_id=last.response_id,
                                usage=last.usage,
                            ),
                        )
                        _persist_model_attempts(
                            catalog,
                            store,
                            state,
                            window_id=window_id,
                            request_bytes=call.request_bytes,
                            traces=traces,
                            created_at=created_at,
                        )
                        state["failures"][window_id] = {
                            "error_code": getattr(exc, "code", "E-MODEL-OUTPUT"),
                            "error_message": str(exc),
                        }
                    else:
                        request_artifact_id, attempts = _persist_model_attempts(
                            catalog,
                            store,
                            state,
                            window_id=window_id,
                            request_bytes=call.request_bytes,
                            traces=traces,
                            created_at=created_at,
                        )
                        response_artifact_id = attempts[-1]["response_artifact_id"]
                        if response_artifact_id is None:
                            raise ValidationError(
                                "E-SCENE-ATTEMPT", "successful model attempt has no response"
                            )
                        state["completed"][window_id] = {
                            "model_request_artifact_id": request_artifact_id,
                            "provider_response_artifact_id": response_artifact_id,
                        }
                        state["failures"].pop(window_id, None)
                write_checkpoint()
    if state["failures"]:
        state["status"] = "PARTIAL"
        write_checkpoint()
        first_window_id = min(
            state["failures"], key=lambda value: window_by_id[value]["ordinal"]
        )
        failure = state["failures"][first_window_id]
        raise ValidationError(
            "E-SCENE-PARTIAL",
            f"{len(state['failures'])} scene window(s) failed; first {first_window_id}: "
            f"{failure['error_code']}",
        )
    if pending_responses:
        state["status"] = "WAITING_FOR_AGENT"
        write_checkpoint()
        raise AgentResponsesPending(list(pending_responses.values()))

    receipt_by_attempt_id: dict[str, str] = {}
    for receipt_artifact_id in state["attempt_record_artifact_ids"]:
        record = json.loads(store.get(receipt_artifact_id).decode("utf-8"))
        receipt_by_attempt_id[record["attempt_id"]] = receipt_artifact_id
    model_attempts = sorted(
        (
            attempt
            for attempt in catalog.all("ModelAttempt")
            if attempt["subject_id"] in window_by_id
        ),
        key=lambda attempt: (
            window_by_id[attempt["subject_id"]]["ordinal"],
            attempt["attempt_ordinal"],
            attempt["attempt_id"],
        ),
    )
    if len(receipt_by_attempt_id) != len(model_attempts) or set(receipt_by_attempt_id) != {
        attempt["attempt_id"] for attempt in model_attempts
    }:
        raise ValidationError("E-SCENE-ATTEMPT", "attempt receipt set is incomplete")
    state["attempt_record_artifact_ids"] = [
        receipt_by_attempt_id[attempt["attempt_id"]] for attempt in model_attempts
    ]
    usage_ledger = {
        "input_tokens": sum(
            attempt["usage"]["input_tokens"] or 0 for attempt in model_attempts
        ),
        "output_tokens": sum(
            attempt["usage"]["output_tokens"] or 0 for attempt in model_attempts
        ),
        "total_tokens": sum(
            attempt["usage"]["total_tokens"] or 0 for attempt in model_attempts
        ),
        "attempts_with_unknown_usage": sum(
            1
            for attempt in model_attempts
            if attempt["usage"]["total_tokens"] is None
        ),
        "estimated_cost_microusd": None,
    }
    state.setdefault("final_resumed_from_checkpoint", resumed)
    state.setdefault("completed_at", created_at)
    state["status"] = "COMPLETE"
    checkpoint_bytes = _seal_checkpoint(state)
    if checkpoint_file is not None:
        _atomic_write(checkpoint_file, checkpoint_bytes)
    checkpoint_artifact_id = _put_artifact(
        catalog,
        store,
        checkpoint_bytes,
        media_type="application/vnd.xhnovel.scene-scout-checkpoint+json",
        created_at=state["completed_at"],
    )
    checkpoint_hash = object_hash(state, omit=())

    request_artifact_ids = [
        state["completed"][window["window_id"]]["model_request_artifact_id"]
        for window in windows
    ]
    response_artifact_ids = [
        state["completed"][window["window_id"]]["provider_response_artifact_id"]
        for window in windows
    ]
    raw_candidates: list[dict[str, Any]] = []
    response_format = build["parameters"]["response_format"]
    for window, response_artifact_id in zip(windows, response_artifact_ids):
        try:
            output_value = _decode_executor_output(
                response_format,
                store.get(response_artifact_id),
            )
        except ValidationError as exc:
            raise ValidationError("E-SCENE-CHECKPOINT", "completed response is invalid") from exc
        for candidate in _validate_scout_output(
            catalog, output_value, output_schema=output_schema, window=window
        ):
            raw = {**candidate, "window_id": window["window_id"]}
            raw["raw_hash"] = object_hash(raw, omit=())
            if any(item["raw_hash"] == raw["raw_hash"] for item in raw_candidates):
                raise ValidationError("E-MODEL-OUTPUT", "scene scout emitted a duplicate candidate")
            raw_candidates.append(raw)
    run_identity = {
        "request_id": request["request_id"],
        "bundle_id": bundle["bundle_id"],
        "bundle_hash": bundle["bundle_hash"],
        "extractor_build_id": build["extractor_build_id"],
        "discovery_brief_hash": object_hash({"discovery_brief": discovery_brief}, omit=()),
        "window_ids": [window["window_id"] for window in windows],
        "model_request_artifact_ids": request_artifact_ids,
        "provider_response_artifact_ids": response_artifact_ids,
        "checkpoint_artifact_id": checkpoint_artifact_id,
        "checkpoint_hash": checkpoint_hash,
        "resumed_from_checkpoint": state["final_resumed_from_checkpoint"],
        "model_attempt_ids": [attempt["attempt_id"] for attempt in model_attempts],
        "attempt_record_artifact_ids": list(state["attempt_record_artifact_ids"]),
        "usage_ledger": usage_ledger,
    }
    scout_run_id = derived_id("SceneScoutRun", run_identity)
    run = {
        "schema_version": SCHEMA_VERSION,
        "scene_scout_run_id": scout_run_id,
        **run_identity,
        "status": "SUCCEEDED",
        "created_at": run_created_at,
    }
    merge_run, candidates = merge_scene_candidates(
        catalog,
        raw_candidates,
        request_id=request["request_id"],
        bundle_id=bundle["bundle_id"],
        scout_run_id=scout_run_id,
    )
    validate_schema("SceneScoutRun", run)
    validate_schema("SceneMergeRun", merge_run)
    for candidate in candidates:
        validate_schema("SceneCandidate", candidate)
    catalog.add("SceneScoutRun", run)
    catalog.add("SceneMergeRun", merge_run)
    for candidate in candidates:
        catalog.add("SceneCandidate", candidate)
    return {
        "build": build,
        "run": run,
        "merge_run": merge_run,
        "windows": windows,
        "candidates": candidates,
        "model_request_artifact_ids": request_artifact_ids,
        "provider_response_artifact_ids": response_artifact_ids,
    }


def run_scene_scout(
    catalog: Catalog,
    store: ArtifactStore,
    bundle: dict[str, Any],
    *,
    client: SceneScoutExecutor,
    repo_root: pathlib.Path,
    created_at: str,
    window_chars: int = DEFAULT_WINDOW_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
    max_input_chars: int = 20_000,
    max_request_bytes: int = 2_000_000,
    max_workers: int = DEFAULT_MAX_WORKERS,
    work_dir: pathlib.Path | None = None,
) -> dict[str, Any]:
    lock = (
        _exclusive_scene_work_dir(pathlib.Path(work_dir))
        if work_dir is not None
        else nullcontext()
    )
    with lock:
        return _run_scene_scout_locked(
            catalog,
            store,
            bundle,
            client=client,
            repo_root=repo_root,
            created_at=created_at,
            window_chars=window_chars,
            overlap_chars=overlap_chars,
            max_input_chars=max_input_chars,
            max_request_bytes=max_request_bytes,
            max_workers=max_workers,
            work_dir=work_dir,
        )


def scene_scout_artifact_ids(
    catalog: Catalog,
    bundle: dict[str, Any],
    scout: dict[str, Any],
) -> list[str]:
    ids = set(bundle.get("artifact_ids") or [])
    for snapshot_id in bundle["collection_snapshot_ids"]:
        ids.update(catalog.get("CollectionSnapshot", snapshot_id)["artifact_ids"])
    ids.update(scout["run"]["model_request_artifact_ids"])
    ids.update(scout["run"]["provider_response_artifact_ids"])
    ids.add(scout["run"]["checkpoint_artifact_id"])
    ids.update(scout["run"]["attempt_record_artifact_ids"])
    for attempt_id in scout["run"]["model_attempt_ids"]:
        attempt = catalog.get("ModelAttempt", attempt_id)
        ids.add(attempt["request_artifact_id"])
        if attempt["response_artifact_id"]:
            ids.add(attempt["response_artifact_id"])
    return sorted_ids(ids)


def scene_scout_distributable_artifact_ids(
    catalog: Catalog,
    scout: dict[str, Any],
) -> list[str]:
    """Return model-output audit artifacts that do not embed source request text."""
    run = scout["run"]
    ids = set(run["provider_response_artifact_ids"])
    ids.add(run["checkpoint_artifact_id"])
    ids.update(run["attempt_record_artifact_ids"])
    for attempt_id in run["model_attempt_ids"]:
        response_artifact_id = catalog.get("ModelAttempt", attempt_id)["response_artifact_id"]
        if response_artifact_id:
            ids.add(response_artifact_id)
    return sorted_ids(ids)


def validate_scene_scouts(
    catalog: Catalog,
    store: ArtifactStore,
    *,
    repo_root: pathlib.Path,
) -> None:
    prompt, prompt_bytes, output_schema, schema_bytes = _load_profile(repo_root)
    for run in catalog.all("SceneScoutRun"):
        validate_schema("SceneScoutRun", run)
        bundle = catalog.get("EvidenceBundle", run["bundle_id"])
        request = catalog.get("ResearchRequest", run["request_id"])
        build = catalog.get("ExtractorBuild", run["extractor_build_id"])
        parameters = build["parameters"]
        executor_kind = parameters.get("executor_kind")
        response_format = parameters.get("response_format")
        expected_executor_build = {
            API_EXECUTOR_KIND: (OPENAI_RESPONSES_FORMAT, MODEL_EXECUTOR_BUILD_ID),
            AGENT_FILES_EXECUTOR_KIND: (
                AGENT_FILES_RESPONSE_FORMAT,
                AGENT_FILES_EXECUTOR_BUILD_ID,
            ),
        }.get(executor_kind)
        if (
            expected_executor_build is None
            or response_format != expected_executor_build[0]
            or build["executor_build_id"] != expected_executor_build[1]
            or run["bundle_hash"] != bundle["bundle_hash"]
            or run["request_id"] != bundle["request_id"]
            or run["discovery_brief_hash"]
            != object_hash({"discovery_brief": request["discovery_brief"]}, omit=())
            or build["prompt_template_hash"] != artifact_id_for(prompt_bytes)
            or parameters.get("output_schema_hash") != artifact_id_for(schema_bytes)
        ):
            raise ValidationError("E-SCENE-REPLAY", "scene scout build/request lineage differs")
        catalog.get("Artifact", run["checkpoint_artifact_id"])
        store.verify(run["checkpoint_artifact_id"])
        try:
            checkpoint = json.loads(store.get(run["checkpoint_artifact_id"]).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("E-SCENE-REPLAY", "scene checkpoint is invalid") from exc
        if (
            object_hash(checkpoint, omit=()) != run["checkpoint_hash"]
            or artifact_id_for(canonical_dumps(checkpoint)) != run["checkpoint_artifact_id"]
            or checkpoint.get(CHECKPOINT_INTEGRITY_FIELD) != _checkpoint_integrity(checkpoint)
            or checkpoint.get("status") != "COMPLETE"
        ):
            raise ValidationError("E-SCENE-REPLAY", "scene checkpoint does not match its run")
        expected_windows = build_scene_windows(
            catalog,
            bundle,
            request_id=request["request_id"],
            window_chars=parameters["window_chars"],
            overlap_chars=parameters["overlap_chars"],
        )
        actual_windows = [catalog.get("SceneWindow", window_id) for window_id in run["window_ids"]]
        if actual_windows != expected_windows:
            raise ValidationError("E-SCENE-REPLAY", "scene windows do not replay")
        request_ids = run["model_request_artifact_ids"]
        response_ids = run["provider_response_artifact_ids"]
        if len(request_ids) != len(expected_windows) or len(response_ids) != len(expected_windows):
            raise ValidationError("E-SCENE-REPLAY", "scene scout exchanges are incomplete")
        expected_completed = {
            window["window_id"]: {
                "model_request_artifact_id": request_artifact_id,
                "provider_response_artifact_id": response_artifact_id,
            }
            for window, request_artifact_id, response_artifact_id in zip(
                expected_windows,
                run["model_request_artifact_ids"],
                run["provider_response_artifact_ids"],
            )
        }
        if checkpoint.get("completed") != expected_completed or checkpoint.get("failures") != {}:
            raise ValidationError("E-SCENE-REPLAY", "scene checkpoint completion set differs")
        if checkpoint.get("attempt_record_artifact_ids") != run["attempt_record_artifact_ids"]:
            raise ValidationError("E-SCENE-REPLAY", "scene checkpoint attempt ledger differs")
        attempts = [catalog.get("ModelAttempt", attempt_id) for attempt_id in run["model_attempt_ids"]]
        if len(attempts) != len(run["attempt_record_artifact_ids"]):
            raise ValidationError("E-SCENE-ATTEMPT", "scene attempt records are incomplete")
        attempt_by_window: dict[str, list[dict[str, Any]]] = {}
        for attempt, receipt_artifact_id in zip(
            attempts, run["attempt_record_artifact_ids"]
        ):
            validate_schema("ModelAttempt", attempt)
            catalog.get("Artifact", receipt_artifact_id)
            store.verify(receipt_artifact_id)
            if store.get(receipt_artifact_id) != canonical_dumps(attempt):
                raise ValidationError("E-SCENE-ATTEMPT", "model attempt receipt differs")
            identity = {key: value for key, value in attempt.items() if key != "attempt_id"}
            if attempt["attempt_id"] != derived_id("ModelAttempt", identity):
                raise ValidationError("E-ID-BIND", "model attempt id differs from receipt")
            for field in ("request_artifact_id", "response_artifact_id"):
                artifact_id = attempt[field]
                if artifact_id:
                    catalog.get("Artifact", artifact_id)
                    store.verify(artifact_id)
            usage = attempt["usage"]
            if (
                usage["input_tokens"] is not None
                and usage["output_tokens"] is not None
                and usage["total_tokens"] is not None
                and usage["total_tokens"]
                != usage["input_tokens"] + usage["output_tokens"]
            ):
                raise ValidationError("E-SCENE-USAGE", "model attempt token totals differ")
            attempt_by_window.setdefault(attempt["subject_id"], []).append(attempt)
        if set(attempt_by_window) != {window["window_id"] for window in expected_windows}:
            raise ValidationError("E-SCENE-ATTEMPT", "not every scene window has an attempt")
        expected_usage_ledger = {
            "input_tokens": sum(item["usage"]["input_tokens"] or 0 for item in attempts),
            "output_tokens": sum(item["usage"]["output_tokens"] or 0 for item in attempts),
            "total_tokens": sum(item["usage"]["total_tokens"] or 0 for item in attempts),
            "attempts_with_unknown_usage": sum(
                1 for item in attempts if item["usage"]["total_tokens"] is None
            ),
            "estimated_cost_microusd": None,
        }
        if run["usage_ledger"] != expected_usage_ledger:
            raise ValidationError("E-SCENE-USAGE", "scene run usage ledger differs")
        for window, request_artifact_id, response_artifact_id in zip(
            expected_windows, request_ids, response_ids
        ):
            window_attempts = attempt_by_window[window["window_id"]]
            if [item["attempt_ordinal"] for item in window_attempts] != list(
                range(1, len(window_attempts) + 1)
            ):
                raise ValidationError("E-SCENE-ATTEMPT", "model attempt ordinals are not consecutive")
            previous = None
            for attempt in window_attempts:
                if attempt["retry_of"] != previous:
                    raise ValidationError("E-SCENE-ATTEMPT", "model attempt retry chain differs")
                previous = attempt["attempt_id"]
            final = window_attempts[-1]
            if (
                final["status"] != "SUCCEEDED"
                or final["request_artifact_id"] != request_artifact_id
                or final["response_artifact_id"] != response_artifact_id
            ):
                raise ValidationError("E-SCENE-ATTEMPT", "successful window exchange differs from ledger")
        raw_candidates: list[dict[str, Any]] = []
        for window, request_artifact_id, response_artifact_id in zip(
            expected_windows, request_ids, response_ids
        ):
            for artifact_id in (request_artifact_id, response_artifact_id):
                catalog.get("Artifact", artifact_id)
                store.verify(artifact_id)
            request_bytes = store.get(request_artifact_id)
            response_bytes = store.get(response_artifact_id)
            expected_input = _window_input(
                catalog, window, discovery_brief=request["discovery_brief"]
            )
            try:
                if response_format == OPENAI_RESPONSES_FORMAT:
                    stored_request = json.loads(request_bytes.decode("utf-8"))
                    input_value = json.loads(stored_request["input"])
                    if (
                        set(stored_request)
                        != {"model", "instructions", "input", "text", "store"}
                        or stored_request["model"] != build["model"]
                        or stored_request["instructions"] != prompt
                        or stored_request["store"] is not False
                        or stored_request["input"]
                        != canonical_dumps(expected_input).decode("utf-8")
                        or input_value != expected_input
                        or stored_request["text"]["format"]
                        != {
                            "type": "json_schema",
                            "name": "xuanhuan_scene_candidates",
                            "strict": True,
                            "schema": output_schema,
                        }
                    ):
                        raise ValidationError(
                            "E-SCENE-REPLAY", "stored scene request differs"
                        )
                elif response_format == AGENT_FILES_RESPONSE_FORMAT:
                    expected_request = agent_task_bytes(
                        instructions=prompt,
                        input_value=expected_input,
                        schema_name="xuanhuan_scene_candidates",
                        schema=output_schema,
                    )
                    if request_bytes != expected_request:
                        raise ValidationError(
                            "E-SCENE-REPLAY", "stored agent task differs"
                        )
                else:
                    raise ValidationError(
                        "E-SCENE-REPLAY", "unknown Scene Scout response format"
                    )
                output_value = _decode_executor_output(response_format, response_bytes)
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                KeyError,
                TypeError,
                ValidationError,
            ) as exc:
                if isinstance(exc, ValidationError) and exc.code == "E-SCENE-REPLAY":
                    raise
                raise ValidationError(
                    "E-SCENE-REPLAY", "stored scene exchange is invalid"
                ) from exc
            for candidate in _validate_scout_output(
                catalog, output_value, output_schema=output_schema, window=window
            ):
                raw = {**candidate, "window_id": window["window_id"]}
                raw["raw_hash"] = object_hash(raw, omit=())
                raw_candidates.append(raw)
        expected_merge, expected_candidates = merge_scene_candidates(
            catalog,
            raw_candidates,
            request_id=request["request_id"],
            bundle_id=bundle["bundle_id"],
            scout_run_id=run["scene_scout_run_id"],
        )
        actual_merge = next(
            (
                item
                for item in catalog.all("SceneMergeRun")
                if item["scene_scout_run_id"] == run["scene_scout_run_id"]
            ),
            None,
        )
        actual_candidates = [
            item
            for item in catalog.all("SceneCandidate")
            if item["scene_scout_run_id"] == run["scene_scout_run_id"]
        ]
        if actual_merge != expected_merge or {
            item["scene_candidate_id"]: item for item in actual_candidates
        } != {item["scene_candidate_id"]: item for item in expected_candidates}:
            raise ValidationError("E-SCENE-REPLAY", "merged scene candidates differ from replay")
