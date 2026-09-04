from __future__ import annotations

import copy
import pathlib
from collections import defaultdict
from typing import Any, Callable

from .canonical import canonical_dumps
from .errors import ValidationError
from .hashing import object_hash, sha256_bytes

REDUCER_ID_EXACT_PAYLOAD_DEDUP = "exact-payload-dedup/v1"


def _span_key(span: dict[str, Any]) -> tuple[str, int, int, str]:
    return (
        str(span["segment_id"]),
        int(span["start"]),
        int(span["end"]),
        str(span["normalized_text_hash"]),
    )


def exact_payload_dedup(
    observations: list[dict[str, Any]],
    *,
    record_version: int = 1,
) -> list[dict[str, Any]]:
    """Deduplicate byte-identical payloads without resolving entities or conflicts."""

    buckets: dict[bytes, list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        payload = observation.get("payload")
        if not isinstance(payload, dict):
            raise ValidationError("E-REDUCER-INPUT", "observation payload must be an object")
        buckets[canonical_dumps(payload)].append(observation)

    records: list[dict[str, Any]] = []
    for payload_bytes, members in buckets.items():
        payload = copy.deepcopy(members[0]["payload"])
        if canonical_dumps(payload) != payload_bytes:
            raise ValidationError("E-REDUCER-INPUT", "payload changed during reduction")
        member_ids = sorted(str(member["observation_id"]) for member in members)
        spans_by_key: dict[tuple[str, int, int, str], dict[str, Any]] = {}
        for member in members:
            for span in member.get("source_spans", []):
                spans_by_key[_span_key(span)] = copy.deepcopy(span)
        base = {
            "schema_version": f"corpus-record/v{record_version}",
            "bucket_semantics": "EXACT_PAYLOAD_NOT_ENTITY",
            "payload": payload,
            "member_observation_ids": member_ids,
            "source_spans": [spans_by_key[key] for key in sorted(spans_by_key)],
        }
        record_hash = object_hash(base, omit=())
        records.append(
            {
                **base,
                "record_id": "COR-" + record_hash.removeprefix("sha256:")[:20].upper(),
                "record_hash": record_hash,
            }
        )
    return sorted(records, key=lambda record: record["record_hash"])


_REDUCERS: dict[str, Callable[..., list[dict[str, Any]]]] = {
    REDUCER_ID_EXACT_PAYLOAD_DEDUP: exact_payload_dedup,
}


def reducer_implementation_hash(reducer_id: str) -> str:
    if reducer_id not in _REDUCERS:
        raise ValidationError("E-REDUCER", f"unsupported reducer {reducer_id!r}")
    source = pathlib.Path(__file__).read_bytes()
    return "sha256:" + sha256_bytes(
        canonical_dumps({"reducer_id": reducer_id, "module_sha256": sha256_bytes(source)})
    )


def reduce_observations(
    observations: list[dict[str, Any]],
    *,
    reducer_id: str,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    if reducer_id not in _REDUCERS:
        raise ValidationError("E-REDUCER", f"unsupported reducer {reducer_id!r}")
    allowed = {"record_version"}
    if set(config) - allowed:
        raise ValidationError("E-REDUCER-CONFIG", "unsupported exact-payload-dedup config")
    record_version = config.get("record_version", 1)
    if not isinstance(record_version, int) or isinstance(record_version, bool) or record_version < 1:
        raise ValidationError("E-REDUCER-CONFIG", "record_version must be a positive integer")
    return _REDUCERS[reducer_id](observations, record_version=record_version)
