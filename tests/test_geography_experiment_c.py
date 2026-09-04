from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "spikes" / "geography_experiment_c.py"
SPEC = importlib.util.spec_from_file_location("geography_experiment_c", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
experiment_c = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = experiment_c
SPEC.loader.exec_module(experiment_c)


def test_zero_denominator_is_null_not_zero_or_one() -> None:
    assert experiment_c._ratio(0, 0) is None
    assert experiment_c._ratio(1, 0) is None
    assert experiment_c._ratio(0, 2) == 0
    assert experiment_c._prf(set(), set()) == {
        "tp": 0,
        "predicted": 0,
        "gold": 0,
        "precision": None,
        "recall": None,
    }


def test_scoring_does_not_alias_merge_and_separates_cohorts() -> None:
    sample = {
        "units": [
            {"ordinal": 310, "unit_id": "XUNIT-STRESS", "selection": "heuristic-dense"},
            {"ordinal": 102, "unit_id": "XUNIT-CONTROL", "selection": "random-control"},
        ]
    }
    unique_rows = [
        {
            "unit_id": "XUNIT-STRESS",
            "ordinal": 310,
            "payload": {"kind": "PLACE_MENTION", "name": "乌坦城"},
            "occurrences": [{"annotation_id": "GEOANN-A", "position_bucket": "Q4"}],
        },
        {
            "unit_id": "XUNIT-STRESS",
            "ordinal": 310,
            "payload": {"kind": "PLACE_MENTION", "name": "乌坦城", "explicit_type": "城"},
            "occurrences": [{"annotation_id": "GEOANN-B", "position_bucket": "Q1"}],
        },
        {
            "unit_id": "XUNIT-CONTROL",
            "ordinal": 102,
            "payload": {
                "kind": "SPATIAL_RELATION",
                "subject_name": "甲",
                "relation": "NEAR",
                "object_name": "乙",
            },
            "occurrences": [{"annotation_id": "GEOANN-C", "position_bucket": "Q2"}],
        },
    ]
    answers = {
        "XUNIT-STRESS": (
            b"{}",
            {
                "records": [
                    {
                        "payload": {"kind": "PLACE_MENTION", "name": "乌坦城"},
                        "evidence_bindings": [
                            {
                                "paths": ["/name"],
                                "source_spans": [{"segment_id": "SEG-A", "start": 0, "end": 3}],
                            }
                        ],
                    }
                ]
            },
        ),
        "XUNIT-CONTROL": (
            b"{}",
            {
                "records": [
                    {
                        "payload": {
                            "kind": "SPATIAL_RELATION",
                            "subject_name": "甲",
                            "relation": "NEAR",
                            "object_name": "乙",
                        },
                        "evidence_bindings": [
                            {
                                "paths": ["/subject_name", "/relation", "/object_name"],
                                "source_spans": [{"segment_id": "SEG-B", "start": 0, "end": 5}],
                            }
                        ],
                    }
                ],
                "completion": {"status": "COMPLETE"},
            },
        ),
    }
    result = experiment_c.score_configuration(
        sample=sample, unique_rows=unique_rows, answers=answers
    )
    stress = next(row for row in result["per_unit"] if row["cohort"] == "stress")
    control = next(row for row in result["per_unit"] if row["cohort"] == "control")
    assert stress["place_unique"]["recall"] == 0.5
    assert stress["place_name"]["recall"] == 1.0
    assert control["relation_unique"]["recall"] == 1.0
    assert result["cohorts"]["stress"]["unit_count"] == 1
    assert result["cohorts"]["control"]["unit_count"] == 1
    assert result["cohorts"]["all10_diagnostic"]["unit_count"] == 2
    assert "not an unbiased whole-book quality estimate" in result["notes"][0]
