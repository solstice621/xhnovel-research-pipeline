from __future__ import annotations

import pathlib

import pytest

from xhnovel_pipeline.catalog import Catalog
from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.generic_extraction import (
    _validate_unit_coverage,
    build_extraction_units,
)
from xhnovel_pipeline.generic_profile import load_extraction_profile
from xhnovel_pipeline.generic_reducers import reduce_observations
from xhnovel_pipeline.hashing import artifact_id_for

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _segment(segment_id: str, text: str, ordinal: int) -> dict:
    return {
        "segment_id": segment_id,
        "document_id": "DOC-TEST",
        "ordinal": ordinal,
        "normalized_text": text,
        "normalized_text_hash": artifact_id_for(text.encode("utf-8")),
    }


def test_unit_plan_proves_full_text_coverage() -> None:
    catalog = Catalog()
    first = _segment("SEG-TEST-A", "甲" * 12000, 0)
    second = _segment("SEG-TEST-B", "乙" * 9500, 1)
    catalog.add("Segment", first)
    catalog.add("Segment", second)
    snapshot = {
        "text_snapshot_id": "NTS-TEST",
        "segment_ids": [first["segment_id"], second["segment_id"]],
        "eligible_character_count": 21500,
    }
    profile = load_extraction_profile("geography-v1", root=ROOT)
    units, coverage = build_extraction_units(snapshot, catalog, profile)

    assert len(units) >= 3
    assert coverage == {
        "text_coverage": "FULL",
        "semantic_coverage": "UNMEASURED",
        "eligible_character_count": 21500,
        "covered_character_count": 21500,
        "uncovered_ranges": [],
    }
    _validate_unit_coverage(snapshot, catalog, units)

    tampered = [dict(unit) for unit in units]
    tampered[0] = {**tampered[0], "source_spans": [{**tampered[0]["source_spans"][0], "start": 1}]}
    with pytest.raises(ValidationError):
        _validate_unit_coverage(snapshot, catalog, tampered)


def test_exact_payload_reducer_deduplicates_without_entity_resolution() -> None:
    span_a = {
        "segment_id": "SEG-A",
        "start": 1,
        "end": 4,
        "normalized_text_hash": "sha256:" + "a" * 64,
    }
    span_b = {
        "segment_id": "SEG-B",
        "start": 5,
        "end": 8,
        "normalized_text_hash": "sha256:" + "b" * 64,
    }
    observations = [
        {
            "observation_id": "OBS-A",
            "payload": {"kind": "PLACE_MENTION", "name": "中州"},
            "source_spans": [span_a],
        },
        {
            "observation_id": "OBS-B",
            "payload": {"kind": "PLACE_MENTION", "name": "中州"},
            "source_spans": [span_b],
        },
        {
            "observation_id": "OBS-C",
            "payload": {"kind": "PLACE_MENTION", "name": "中州", "explicit_type": "大陆"},
            "source_spans": [span_b],
        },
    ]
    records = reduce_observations(
        observations,
        reducer_id="exact-payload-dedup/v1",
        config={"record_version": 1},
    )
    assert len(records) == 2
    merged = next(record for record in records if record["payload"] == {"kind": "PLACE_MENTION", "name": "中州"})
    assert merged["bucket_semantics"] == "EXACT_PAYLOAD_NOT_ENTITY"
    assert merged["member_observation_ids"] == ["OBS-A", "OBS-B"]
    assert len(merged["source_spans"]) == 2
