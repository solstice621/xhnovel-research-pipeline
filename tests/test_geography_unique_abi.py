from __future__ import annotations

import pathlib
import shutil

import pytest
from jsonschema import Draft202012Validator

from xhnovel_pipeline.catalog import Catalog
from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.generic_extraction import validate_model_output
from xhnovel_pipeline.generic_profile import load_extraction_profile, output_schema_for
from xhnovel_pipeline.hashing import artifact_id_for

ROOT = pathlib.Path(__file__).resolve().parents[1]
GEOGRAPHY_V1_HASH = "sha256:d7256c57bc4668a77d6b98912e66745bdcdda3ce2f6a305808d0a8bceb78e671"


def _segment(text: str = "乌坦城位于加玛帝国。乌坦城。") -> dict:
    return {
        "segment_id": "SEG-TEST-001",
        "document_id": "DOC-TEST",
        "ordinal": 0,
        "normalized_text": text,
        "normalized_text_hash": artifact_id_for(text.encode("utf-8")),
    }


def _unit(segment: dict) -> dict:
    return {
        "unit_id": "XUNIT-TEST",
        "text_snapshot_id": "NTS-TEST",
        "ordinal": 1,
        "text_length": len(segment["normalized_text"]),
        "source_spans": [
            {
                "segment_id": segment["segment_id"],
                "start": 0,
                "end": len(segment["normalized_text"]),
                "normalized_text_hash": segment["normalized_text_hash"],
            }
        ],
    }


def _build() -> dict:
    return {
        "extraction_build_id": "XBLD-TEST",
        "extraction_run_id": "XRUN-TEST",
        "profile_id": "xhnovel.geography.unique",
        "profile_version": "1.0.0",
        "profile_package_hash": "sha256:" + "a" * 64,
        "payload_schema_artifact_id": "sha256:" + "b" * 64,
    }


def _place(name: str, start: int, end: int, *, explicit_type: str | None = None) -> dict:
    payload = {"kind": "PLACE_MENTION", "name": name}
    paths = ["/name"]
    if explicit_type is not None:
        payload["explicit_type"] = explicit_type
        paths.append("/explicit_type")
    return {
        "payload": payload,
        "evidence_bindings": [
            {
                "paths": paths,
                "source_spans": [{"segment_id": "SEG-TEST-001", "start": start, "end": end}],
            }
        ],
    }


def test_geography_v1_extraction_hash_and_occurrence_envelope_unchanged() -> None:
    profile = load_extraction_profile("geography-v1", root=ROOT)
    assert profile.extraction_profile_hash == GEOGRAPHY_V1_HASH
    assert profile.answer_abi == {
        "record_mode": "OCCURRENCE",
        "completion_required": False,
    }
    schema = output_schema_for(profile)
    assert schema["required"] == ["records"]
    assert "completion" not in schema["properties"]
    assert Draft202012Validator(schema).is_valid({"records": []})
    assert not Draft202012Validator(schema).is_valid(
        {"records": [], "completion": {"status": "COMPLETE"}}
    )


def test_unique_profile_requires_completion_and_rejects_exact_duplicates() -> None:
    profile = load_extraction_profile("geography-unique-v1", root=ROOT)
    assert profile.answer_abi == {
        "record_mode": "UNIQUE_PAYLOAD",
        "completion_required": True,
    }
    assert profile.extraction_profile_hash != GEOGRAPHY_V1_HASH
    schema = output_schema_for(profile)
    assert schema["required"] == ["records", "completion"]
    assert schema["properties"]["records"]["maxItems"] == 64
    assert Draft202012Validator(schema).is_valid(
        {"records": [], "completion": {"status": "COMPLETE"}}
    )
    assert not Draft202012Validator(schema).is_valid({"records": []})

    segment = _segment()
    catalog = Catalog()
    catalog.add("Segment", segment)
    unit = _unit(segment)
    first = _place("乌坦城", 0, 3)
    duplicate = _place("乌坦城", 10, 13)
    aliasish = _place("乌坦", 10, 12)
    typed = _place("乌坦城", 0, 3, explicit_type="城")
    kwargs = {
        "snapshot": {"text_snapshot_id": "NTS-TEST", "work_id": "NWK-TEST"},
        "unit": unit,
        "build": _build(),
        "profile": profile,
        "catalog": catalog,
    }
    accepted = validate_model_output(
        {
            "records": [first, aliasish, typed],
            "completion": {"status": "COMPLETE"},
        },
        **kwargs,
    )
    payloads = [row["payload"] for row in accepted]
    assert {"kind": "PLACE_MENTION", "name": "乌坦城"} in payloads
    assert {"kind": "PLACE_MENTION", "name": "乌坦"} in payloads
    assert {"kind": "PLACE_MENTION", "name": "乌坦城", "explicit_type": "城"} in payloads

    with pytest.raises(ValidationError, match="E-GENERIC-UNIQUE-PAYLOAD"):
        validate_model_output(
            {
                "records": [first, duplicate],
                "completion": {"status": "OVERFLOW"},
            },
            **kwargs,
        )


def test_unique_completion_enum_is_closed(tmp_path: pathlib.Path) -> None:
    profiles_root = tmp_path / "profiles"
    shutil.copytree(ROOT / "profiles" / "generic" / "geography-unique-v1", profiles_root / "unique")
    profile = load_extraction_profile("unique", root=ROOT, profiles_root=profiles_root)
    validator = Draft202012Validator(output_schema_for(profile))
    assert validator.is_valid({"records": [], "completion": {"status": "UNCERTAIN"}})
    assert not validator.is_valid({"records": [], "completion": {"status": "DONE"}})
