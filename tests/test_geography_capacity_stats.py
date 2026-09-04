from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "spikes" / "geography_capacity_stats.py"
SPEC = importlib.util.spec_from_file_location("geography_capacity_stats", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
stats = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = stats
SPEC.loader.exec_module(stats)


def _write_json(path: pathlib.Path, value: object, *, indent: int | None = 2) -> int:
    body = json.dumps(value, ensure_ascii=False, indent=indent) + "\n"
    path.write_text(body, encoding="utf-8")
    return len(body.encode("utf-8"))


def _sample(*units: tuple[int, str], geo_cues: list[str] | None = None) -> dict:
    value = {
        "schema_version": "experiment-sample/v1",
        "selected": [
            {
                "ordinal": ordinal,
                "unit_id": unit_id,
                "task": stats._safe_answer_filename(unit_id),
                "geo_cues": 42,
            }
            for ordinal, unit_id in units
        ],
    }
    if geo_cues is not None:
        value["provenance"] = {"geo_cues": geo_cues}
    return value


def _span(start: int = 0, end: int = 1) -> dict:
    return {"segment_id": "SEG-TEST", "start": start, "end": end}


def _place(name: str, *, explicit_type: str | None = None, start: int = 0) -> dict:
    payload = {"kind": "PLACE_MENTION", "name": name}
    paths = ["/name"]
    if explicit_type is not None:
        payload["explicit_type"] = explicit_type
        paths.append("/explicit_type")
    return {
        "payload": payload,
        "evidence_bindings": [{"paths": paths, "source_spans": [_span(start, start + 1)]}],
    }


def _relation(subject: str, relation: str, obj: str, *, start: int = 0) -> dict:
    return {
        "payload": {
            "kind": "SPATIAL_RELATION",
            "subject_name": subject,
            "relation": relation,
            "object_name": obj,
        },
        "evidence_bindings": [
            {
                "paths": ["/subject_name", "/relation", "/object_name"],
                "source_spans": [_span(start, start + 1)],
            }
        ],
    }


def _setup(tmp_path: pathlib.Path, manifest: dict) -> tuple[pathlib.Path, pathlib.Path]:
    sample_path = tmp_path / "sample.json"
    answers_dir = tmp_path / "answers"
    answers_dir.mkdir()
    _write_json(sample_path, manifest)
    return sample_path, answers_dir


def test_stats_separate_unit_local_and_global_exact_payloads(tmp_path: pathlib.Path) -> None:
    sample_path, answers_dir = _setup(tmp_path, _sample((1, "XUNIT-A"), (2, "XUNIT-B")))
    answer_a = {
        "records": [
            _place("甲城", start=1),
            # Different key insertion order and evidence do not change the logical payload.
            {
                "payload": {"name": "甲城", "kind": "PLACE_MENTION"},
                "evidence_bindings": [
                    {"paths": ["/name"], "source_spans": [_span(2, 3)]}
                ],
            },
            _relation("甲城", "LOCATED_IN", "乙国", start=3),
        ]
    }
    answer_b = {
        "records": [
            _place("甲城", start=4),
            _relation("甲城", "LOCATED_IN", "乙国", start=5),
            _place("丙山", start=6),
        ],
        "completion": {"status": "COMPLETE"},
    }
    bytes_a = _write_json(answers_dir / stats._safe_answer_filename("XUNIT-A"), answer_a)
    bytes_b = _write_json(
        answers_dir / stats._safe_answer_filename("XUNIT-B"), answer_b, indent=6
    )

    result = stats.analyze_capacity(sample_path, answers_dir)

    assert result["validation"] == {
        "status": "PASS",
        "warning_count": 0,
        "check_would_fail": False,
        "warnings": [],
    }
    assert [unit["unit_id"] for unit in result["per_unit"]] == ["XUNIT-A", "XUNIT-B"]
    first, second = result["per_unit"]
    assert (first["raw_count"], first["unit_local_exact_payload_unique"]) == (3, 2)
    assert (first["duplicate_count"], first["duplicate_rate"]) == (1, pytest.approx(1 / 3))
    assert first["response_bytes"] == bytes_a
    assert first["completion"] == {
        "status": None,
        "presence": "LEGACY_ABSENT",
        "trust": "UNVERIFIED_EXECUTOR_ASSERTION",
    }
    assert second["response_bytes"] == bytes_b
    assert second["completion"]["status"] == "COMPLETE"

    aggregate = result["aggregate"]
    assert aggregate["raw_count"] == 6
    assert aggregate["sum_unit_local_unique"] == 5
    assert aggregate["global_exact_payload_unique"] == 3
    assert aggregate["unit_local_duplicate_count"] == 1
    assert aggregate["cross_unit_exact_payload_overlap_count"] == 2
    assert aggregate["per_kind"]["PLACE_MENTION"] == {
        "raw_count": 4,
        "sum_unit_local_unique": 3,
        "global_exact_payload_unique": 2,
        "unit_local_duplicate_count": 1,
        "unit_local_duplicate_rate": pytest.approx(0.25),
        "cross_unit_exact_payload_overlap_count": 1,
        "canonical_logical_hashes": aggregate["per_kind"]["PLACE_MENTION"][
            "canonical_logical_hashes"
        ],
    }
    assert aggregate["per_kind"]["SPATIAL_RELATION"]["sum_unit_local_unique"] == 2
    assert aggregate["per_kind"]["SPATIAL_RELATION"]["global_exact_payload_unique"] == 1
    assert aggregate["distinct_names"] == {"count": 3, "values": ["丙山", "乙国", "甲城"]}
    assert aggregate["response_bytes"] == bytes_a + bytes_b
    assert aggregate["largest_response"]["unit_id"] == "XUNIT-B"
    assert aggregate["completion_assertions"]["counts"] == {
        "COMPLETE": 1,
        "OVERFLOW": 0,
        "UNCERTAIN": 0,
        "LEGACY_ABSENT": 1,
    }

    expected_place_hash = stats._sha256(
        stats._canonical_dumps({"kind": "PLACE_MENTION", "name": "甲城"})
    )
    assert expected_place_hash in first["canonical_logical_hashes"]["payload_hashes"]
    assert "PLACE_MENTION.name" in result["definitions"]["distinct_names"]


def test_duplicate_geo_cues_are_a_warning_and_check_failure(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sample_path, answers_dir = _setup(
        tmp_path,
        _sample((1, "XUNIT-A"), geo_cues=["帝国", "城", "帝国"]),
    )
    _write_json(answers_dir / stats._safe_answer_filename("XUNIT-A"), {"records": []})

    result = stats.analyze_capacity(sample_path, answers_dir)
    assert result["aggregate"]["raw_count"] == 0
    assert result["validation"]["status"] == "WARNINGS"
    assert result["validation"]["warnings"] == [
        {
            "code": "DUPLICATE_GEO_CUES",
            "field": "/provenance/geo_cues",
            "duplicates": [{"value": "帝国", "count": 2, "indexes": [0, 2]}],
            "message": (
                "duplicate geo_cues are selection diagnostics only and are not silently counted "
                "by this statistics tool"
            ),
        }
    ]

    assert stats.main([str(sample_path), str(answers_dir)]) == 0
    normal_output = json.loads(capsys.readouterr().out)
    assert normal_output["validation"]["check_would_fail"] is True
    assert stats.main([str(sample_path), str(answers_dir), "--check"]) == 1
    checked_output = json.loads(capsys.readouterr().out)
    assert checked_output["validation"]["warnings"][0]["code"] == "DUPLICATE_GEO_CUES"


def test_missing_answers_are_explicit_and_check_fails(tmp_path: pathlib.Path) -> None:
    sample_path, answers_dir = _setup(tmp_path, _sample((1, "XUNIT-A"), (2, "XUNIT-B")))
    _write_json(answers_dir / stats._safe_answer_filename("XUNIT-A"), {"records": []})

    result = stats.analyze_capacity(sample_path, answers_dir)

    assert result["inputs"]["sample_units"] == 2
    assert result["inputs"]["answers_found"] == 1
    warning = result["validation"]["warnings"][0]
    assert warning["code"] == "MISSING_SAMPLE_ANSWERS"
    assert warning["units"][0]["unit_id"] == "XUNIT-B"
    assert result["validation"]["check_would_fail"] is True


def test_units_manifest_derives_answer_mapping_and_handles_empty_answer(
    tmp_path: pathlib.Path,
) -> None:
    manifest = {
        "schema_version": "geography-gold-sample/v1",
        "units": [{"ordinal": 0, "unit_id": "XUNIT-ONLY"}],
    }
    sample_path, answers_dir = _setup(tmp_path, manifest)
    answer_path = answers_dir / stats._safe_answer_filename("XUNIT-ONLY")
    response_bytes = _write_json(
        answer_path,
        {"records": [], "completion": {"status": "UNCERTAIN"}},
    )

    result = stats.analyze_capacity(sample_path, answers_dir)

    assert result["inputs"]["sample_units_field"] == "units"
    assert result["aggregate"]["raw_count"] == 0
    assert result["aggregate"]["sum_unit_local_unique"] == 0
    assert result["aggregate"]["global_exact_payload_unique"] == 0
    assert result["aggregate"]["unit_local_duplicate_rate"] == 0.0
    assert result["aggregate"]["response_bytes"] == response_bytes
    assert result["aggregate"]["distinct_names"] == {"count": 0, "values": []}
    assert result["aggregate"]["completion_assertions"]["counts"]["UNCERTAIN"] == 1


def test_executor_report_is_unverified_and_cannot_override_computed_counts(
    tmp_path: pathlib.Path,
) -> None:
    sample_path, answers_dir = _setup(tmp_path, _sample((7, "XUNIT-A")))
    _write_json(
        answers_dir / stats._safe_answer_filename("XUNIT-A"),
        {"records": [_place("甲城")]},
    )
    report_path = tmp_path / "executor-report.json"
    _write_json(
        report_path,
        {
            "schema_version": "executor-report/example",
            "per_unit": [
                {
                    "ordinal": 7,
                    "unit_id": "XUNIT-A",
                    "emitted": 999,
                    "unique_payloads": 999,
                    "identified_total": 1234,
                }
            ],
            "aggregate": {"raw_count": 999},
        },
    )

    result = stats.analyze_capacity(sample_path, answers_dir, report_path)

    assert result["aggregate"]["raw_count"] == 1
    assert result["aggregate"]["sum_unit_local_unique"] == 1
    assert result["executor_report"]["trust"] == "UNVERIFIED_EXECUTOR_ASSERTION"
    assertion = result["executor_report"]["assertions"][0]
    assert assertion["trust"] == "UNVERIFIED_EXECUTOR_ASSERTION"
    assert assertion["reported_fields"] == {
        "emitted": 999,
        "unique_payloads": 999,
        "identified_total": 1234,
    }
    assert "aggregate" not in result["executor_report"]


def test_task_and_answer_mapping_fail_closed(tmp_path: pathlib.Path) -> None:
    bad_manifest = {
        "selected": [{"ordinal": 1, "unit_id": "XUNIT-A", "task": "wrong.json"}]
    }
    sample_path, answers_dir = _setup(tmp_path, bad_manifest)
    with pytest.raises(stats.StatsValidationError, match="E-STATS-TASK-MAPPING"):
        stats.analyze_capacity(sample_path, answers_dir)

    _write_json(sample_path, _sample((1, "XUNIT-A")))
    _write_json(answers_dir / stats._safe_answer_filename("XUNIT-A"), {"records": []})
    _write_json(answers_dir / "not-in-sample.json", {"records": []})
    with pytest.raises(stats.StatsValidationError, match="E-STATS-TASK-MAPPING"):
        stats.analyze_capacity(sample_path, answers_dir)


def test_duplicate_json_keys_and_malformed_payloads_fail_closed(tmp_path: pathlib.Path) -> None:
    sample_path, answers_dir = _setup(tmp_path, _sample((1, "XUNIT-A")))
    answer_path = answers_dir / stats._safe_answer_filename("XUNIT-A")
    answer_path.write_text('{"records": [], "records": []}\n', encoding="utf-8")
    with pytest.raises(stats.StatsValidationError, match="E-STATS-JSON-DUPLICATE-KEY"):
        stats.analyze_capacity(sample_path, answers_dir)

    malformed = _place("甲城")
    malformed["payload"]["unexpected"] = "not in geography-v1"
    _write_json(answer_path, {"records": [malformed]})
    with pytest.raises(stats.StatsValidationError, match="E-STATS-PAYLOAD"):
        stats.analyze_capacity(sample_path, answers_dir)


@pytest.mark.parametrize(
    "completion",
    [
        {"status": "COMPLETE", "identified_total": 10},
        {"status": "DONE"},
        "COMPLETE",
    ],
)
def test_completion_block_is_strict_and_never_accepts_counts(
    tmp_path: pathlib.Path, completion: object
) -> None:
    sample_path, answers_dir = _setup(tmp_path, _sample((1, "XUNIT-A")))
    _write_json(
        answers_dir / stats._safe_answer_filename("XUNIT-A"),
        {"records": [], "completion": completion},
    )

    with pytest.raises(stats.StatsValidationError, match="E-STATS-COMPLETION"):
        stats.analyze_capacity(sample_path, answers_dir)


def test_evidence_shape_is_validated_before_counting(tmp_path: pathlib.Path) -> None:
    sample_path, answers_dir = _setup(tmp_path, _sample((1, "XUNIT-A")))
    record = _relation("甲城", "LOCATED_IN", "乙国")
    record["evidence_bindings"][0]["paths"] = ["/subject_name"]
    _write_json(
        answers_dir / stats._safe_answer_filename("XUNIT-A"),
        {"records": [record]},
    )

    with pytest.raises(stats.StatsValidationError, match="required geography evidence group"):
        stats.analyze_capacity(sample_path, answers_dir)
