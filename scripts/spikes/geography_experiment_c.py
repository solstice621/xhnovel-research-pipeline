#!/usr/bin/env python3
"""Score Experiment C geography answers against a frozen model-adjudicated reference.

This tool never reads reference labels while constructing candidate answers. It only
scores already-written answer JSON against FROZEN_MODEL_GOLD artifacts.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import defaultdict
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "spikes"))
import geography_capacity_stats as stats  # noqa: E402

STRESS_ORDINALS = (5, 310, 395, 426, 513, 596)
CONTROL_ORDINALS = (102, 233, 467, 604)
KINDS = ("PLACE_MENTION", "SPATIAL_RELATION")
QUARTERS = ("Q1", "Q2", "Q3", "Q4")


class ScoreError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _ratio(numerator: int | float, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _load_jsonl(path: pathlib.Path, *, label: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ScoreError("E-C-READ", f"cannot read {label}: {path}") from exc
    if not data:
        return rows
    for index, line in enumerate(data.splitlines(), start=1):
        try:
            row = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ScoreError("E-C-JSONL", f"{label} line {index} is invalid") from exc
        if not isinstance(row, dict):
            raise ScoreError("E-C-JSONL", f"{label} line {index} is not an object")
        rows.append(row)
    return rows


def _payload_key(payload: dict[str, Any]) -> bytes:
    return stats._canonical_dumps(payload)


def _place_name(payload: dict[str, Any]) -> str | None:
    if payload.get("kind") != "PLACE_MENTION":
        return None
    name = payload.get("name")
    return name if isinstance(name, str) and name else None


def _cohort(ordinal: int) -> str:
    if ordinal in STRESS_ORDINALS:
        return "stress"
    if ordinal in CONTROL_ORDINALS:
        return "control"
    raise ScoreError("E-C-COHORT", f"ordinal {ordinal} is outside the frozen Experiment B sample")


def _empty_gold(ordinal: int) -> dict[str, Any]:
    return {
        "ordinal": ordinal,
        "payloads": {kind: set() for kind in KINDS},
        "names": set(),
        "typed": {},
        "earliest_bucket": {},
    }


def attach_occurrence_rows(
    unique_rows: list[dict[str, Any]],
    occurrence_rows: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Join unique gold to occurrence evidence for citation diagnostics.

    Unique rows only store annotation_id + quarter. Citation scoring needs the
    compiled occurrence evidence_bindings from occurrences.jsonl.
    """

    if occurrence_rows is None:
        return unique_rows
    by_id = {row["annotation_id"]: row for row in occurrence_rows}
    attached: list[dict[str, Any]] = []
    for unique in unique_rows:
        rows = []
        for occ in unique.get("occurrences", []):
            annotation_id = occ["annotation_id"]
            if annotation_id not in by_id:
                raise ScoreError(
                    "E-C-GOLD",
                    f"unique {unique.get('unique_id')} references missing occurrence {annotation_id}",
                )
            rows.append(by_id[annotation_id])
        item = dict(unique)
        item["occurrence_rows"] = rows
        attached.append(item)
    return attached


def _gold_by_unit(unique_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in unique_rows:
        unit_id = row["unit_id"]
        bucket = grouped.setdefault(
            unit_id,
            {
                "ordinal": row["ordinal"],
                "payloads": {kind: set() for kind in KINDS},
                "names": set(),
                "typed": {},
                "earliest_bucket": {},
            },
        )
        payload = row["payload"]
        kind = payload["kind"]
        key = _payload_key(payload)
        bucket["payloads"][kind].add(key)
        name = _place_name(payload)
        if name is not None:
            bucket["names"].add(name)
            bucket["typed"].setdefault(name, set()).add(payload.get("explicit_type"))
        earliest = min(item["position_bucket"] for item in row["occurrences"])
        previous = bucket["earliest_bucket"].get(key)
        if previous is None or QUARTERS.index(earliest) < QUARTERS.index(previous):
            bucket["earliest_bucket"][key] = earliest
    return grouped


def _pred_by_unit(answer: dict[str, Any]) -> dict[str, Any]:
    records, completion_status = stats._validate_answer(answer, answer_file="answer")
    payloads = {kind: set() for kind in KINDS}
    names: set[str] = set()
    typed: dict[str, set[Any]] = {}
    records_by_payload: dict[bytes, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        payload = record["payload"]
        key = _payload_key(payload)
        payloads[payload["kind"]].add(key)
        records_by_payload[key].append(record)
        name = _place_name(payload)
        if name is not None:
            names.add(name)
            typed.setdefault(name, set()).add(payload.get("explicit_type"))
    return {
        "payloads": payloads,
        "names": names,
        "typed": typed,
        "records": records,
        "records_by_payload": records_by_payload,
        "raw_count": len(records),
        "unique_count": sum(len(payloads[kind]) for kind in KINDS),
        "completion_status": completion_status,
    }


def _prf(predicted: set[bytes], gold: set[bytes]) -> dict[str, Any]:
    tp = len(predicted & gold)
    return {
        "tp": tp,
        "predicted": len(predicted),
        "gold": len(gold),
        "precision": _ratio(tp, len(predicted)),
        "recall": _ratio(tp, len(gold)),
    }


def _type_counts(pred_typed: dict[str, set[Any]], gold_typed: dict[str, set[Any]]) -> dict[str, int]:
    matched = sorted(set(pred_typed) & set(gold_typed))
    correct = 0
    for name in matched:
        correct += int(pred_typed[name] == gold_typed[name])
    return {"correct": correct, "matched_names": len(matched)}


def _type_accuracy(pred_typed: dict[str, set[Any]], gold_typed: dict[str, set[Any]]) -> float | None:
    counts = _type_counts(pred_typed, gold_typed)
    return _ratio(counts["correct"], counts["matched_names"])


def _tail_recall(
    predicted: set[bytes],
    gold: set[bytes],
    earliest_bucket: dict[bytes, str],
) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    for quarter in QUARTERS:
        gold_q = {key for key in gold if earliest_bucket.get(key) == quarter}
        result[quarter] = _ratio(len(predicted & gold_q), len(gold_q))
    return result


def _citation_for_match(
    gold_occurrences: list[dict[str, Any]],
    predicted_records: list[dict[str, Any]],
) -> dict[str, Any]:
    if not gold_occurrences or not predicted_records:
        return {
            "exact_span": None,
            "containment": None,
            "cited_characters_gold": None,
            "cited_characters_pred": None,
        }

    def span_set(bindings: list[dict[str, Any]]) -> set[tuple[str, int, int]]:
        spans: set[tuple[str, int, int]] = set()
        for binding in bindings:
            for span in binding.get("source_spans", []):
                spans.add((span["segment_id"], int(span["start"]), int(span["end"])))
        return spans

    gold_sets = [span_set(row.get("evidence_bindings", [])) for row in gold_occurrences]
    pred_sets = [span_set(row.get("evidence_bindings", [])) for row in predicted_records]
    exact = any(pred == gold for pred in pred_sets for gold in gold_sets if gold)

    def contains(pred: set[tuple[str, int, int]], gold: set[tuple[str, int, int]]) -> bool:
        for g_seg, g_start, g_end in gold:
            if not any(
                p_seg == g_seg and p_start <= g_start and g_end <= p_end
                for p_seg, p_start, p_end in pred
            ):
                return False
        return bool(gold)

    contained = any(contains(pred, gold) for pred in pred_sets for gold in gold_sets)
    gold_chars = sum(end - start for spans in gold_sets for _, start, end in spans)
    pred_chars = sum(end - start for spans in pred_sets for _, start, end in spans)
    return {
        "exact_span": exact,
        "containment": contained,
        "cited_characters_gold": gold_chars,
        "cited_characters_pred": pred_chars,
    }


def score_unit(
    *,
    sample_unit: dict[str, Any],
    gold: dict[str, Any],
    pred: dict[str, Any],
    gold_unique_rows: list[dict[str, Any]],
    response_bytes: int,
) -> dict[str, Any]:
    ordinal = sample_unit["ordinal"]
    per_kind = {}
    for kind in KINDS:
        metrics = _prf(pred["payloads"][kind], gold["payloads"][kind])
        metrics["tail_recall"] = _tail_recall(
            pred["payloads"][kind],
            gold["payloads"][kind],
            gold["earliest_bucket"],
        )
        per_kind[kind] = metrics
    citations = []
    citation_ready = any(row.get("occurrence_rows") for row in gold_unique_rows)
    if citation_ready:
        for row in gold_unique_rows:
            key = _payload_key(row["payload"])
            if key not in pred["records_by_payload"]:
                continue
            citations.append(
                _citation_for_match(row["occurrence_rows"], pred["records_by_payload"][key])
            )
    supported = [item for item in citations if item["containment"] is True]
    type_counts = _type_counts(pred["typed"], gold["typed"])
    return {
        "ordinal": ordinal,
        "unit_id": sample_unit["unit_id"],
        "cohort": _cohort(ordinal),
        "raw_count": pred["raw_count"],
        "unit_local_unique_count": pred["unique_count"],
        "duplicate_count": pred["raw_count"] - pred["unique_count"],
        "response_bytes": response_bytes,
        "completion_status": pred["completion_status"],
        "place_unique": per_kind["PLACE_MENTION"],
        "relation_unique": per_kind["SPATIAL_RELATION"],
        "place_name": _prf(
            {stats._canonical_dumps(name) for name in pred["names"]},
            {stats._canonical_dumps(name) for name in gold["names"]},
        ),
        "explicit_type_accuracy": _ratio(type_counts["correct"], type_counts["matched_names"]),
        "explicit_type_counts": type_counts,
        "citation": {
            "matched_payloads": len(citations),
            "containment_rate": _ratio(len(supported), len(citations)) if citation_ready else None,
            "exact_span_rate": (
                _ratio(
                    sum(1 for item in citations if item["exact_span"]),
                    len(citations),
                )
                if citation_ready
                else None
            ),
        },
        "tail_recall": _tail_recall(
            set().union(*(pred["payloads"][kind] for kind in KINDS)),
            set().union(*(gold["payloads"][kind] for kind in KINDS)),
            gold["earliest_bucket"],
        ),
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def collect(path: tuple[str, ...]) -> tuple[int, int, int]:
        tp = pred = gold = 0
        for row in rows:
            node: Any = row
            for key in path:
                node = node[key]
            tp += int(node["tp"])
            pred += int(node["predicted"])
            gold += int(node["gold"])
        return tp, pred, gold

    place_tp, place_pred, place_gold = collect(("place_unique",))
    rel_tp, rel_pred, rel_gold = collect(("relation_unique",))
    name_tp, name_pred, name_gold = collect(("place_name",))
    overflow = sum(1 for row in rows if row["completion_status"] == "OVERFLOW")
    complete = sum(1 for row in rows if row["completion_status"] == "COMPLETE")
    saturated = sum(1 for row in rows if row["raw_count"] >= 64)
    q4_recalls = [row["tail_recall"]["Q4"] for row in rows if row["tail_recall"]["Q4"] is not None]
    type_rates = [row["explicit_type_accuracy"] for row in rows if row["explicit_type_accuracy"] is not None]
    type_correct = sum(row["explicit_type_counts"]["correct"] for row in rows)
    type_matched = sum(row["explicit_type_counts"]["matched_names"] for row in rows)
    return {
        "unit_count": len(rows),
        "place_unique": {
            "precision": _ratio(place_tp, place_pred),
            "recall": _ratio(place_tp, place_gold),
            "tp": place_tp,
            "predicted": place_pred,
            "gold": place_gold,
        },
        "relation_unique": {
            "precision": _ratio(rel_tp, rel_pred),
            "recall": _ratio(rel_tp, rel_gold),
            "tp": rel_tp,
            "predicted": rel_pred,
            "gold": rel_gold,
        },
        "place_name": {
            "precision": _ratio(name_tp, name_pred),
            "recall": _ratio(name_tp, name_gold),
        },
        "mean_explicit_type_accuracy": _ratio(
            sum(type_rates), len(type_rates),
        ),
        "weighted_explicit_type_accuracy": _ratio(type_correct, type_matched),
        "explicit_type_counts": {"correct": type_correct, "matched_names": type_matched},
        "perfect_type_unit_rate": _ratio(sum(rate == 1 for rate in type_rates), len(type_rates)),
        "mean_q4_recall": (
            sum(q4_recalls) / len(q4_recalls) if q4_recalls else None
        ),
        "overflow_units": overflow,
        "complete_units": complete,
        "raw_saturated_units": saturated,
        "mean_raw_count": sum(row["raw_count"] for row in rows) / len(rows) if rows else None,
        "mean_unique_count": (
            sum(row["unit_local_unique_count"] for row in rows) / len(rows) if rows else None
        ),
        "mean_duplicate_count": (
            sum(row["duplicate_count"] for row in rows) / len(rows) if rows else None
        ),
        "mean_response_bytes": (
            sum(row["response_bytes"] for row in rows) / len(rows) if rows else None
        ),
    }


def score_configuration(
    *,
    sample: dict[str, Any],
    unique_rows: list[dict[str, Any]],
    answers: dict[str, tuple[bytes, dict[str, Any]]],
    occurrence_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    unique_rows = attach_occurrence_rows(unique_rows, occurrence_rows)
    gold_units = _gold_by_unit(unique_rows)
    gold_rows_by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in unique_rows:
        gold_rows_by_unit[row["unit_id"]].append(row)
    per_unit = []
    for sample_unit in sample["units"]:
        unit_id = sample_unit["unit_id"]
        if unit_id not in answers:
            raise ScoreError("E-C-ANSWER", f"missing answer for {unit_id}")
        response_bytes, answer = answers[unit_id]
        gold = gold_units.get(unit_id) or _empty_gold(sample_unit["ordinal"])
        per_unit.append(
            score_unit(
                sample_unit=sample_unit,
                gold=gold,
                pred=_pred_by_unit(answer),
                gold_unique_rows=gold_rows_by_unit[unit_id],
                response_bytes=len(response_bytes),
            )
        )
    stress = [row for row in per_unit if row["cohort"] == "stress"]
    control = [row for row in per_unit if row["cohort"] == "control"]
    return {
        "per_unit": per_unit,
        "cohorts": {
            "stress": _aggregate(stress),
            "control": _aggregate(control),
            "all10_diagnostic": _aggregate(per_unit),
        },
        "notes": [
            "all10_diagnostic is not an unbiased whole-book quality estimate",
            "capacity decisions may weight the stress cohort",
            "general quality claims should prioritize controls",
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=pathlib.Path, required=True)
    parser.add_argument("--unique", type=pathlib.Path, required=True)
    parser.add_argument("--occurrences", type=pathlib.Path)
    parser.add_argument("--answers-dir", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        sample = json.loads(args.sample.read_text(encoding="utf-8"))
        unique_rows = _load_jsonl(args.unique, label="unique gold")
        occurrence_rows = (
            _load_jsonl(args.occurrences, label="occurrence gold") if args.occurrences else None
        )
        answers: dict[str, tuple[bytes, dict[str, Any]]] = {}
        for unit in sample["units"]:
            path = args.answers_dir / stats._safe_answer_filename(unit["unit_id"])
            data, value = stats._load_json(path, label="answer")
            answers[unit["unit_id"]] = (data, value)
        result = score_configuration(
            sample=sample,
            unique_rows=unique_rows,
            answers=answers,
            occurrence_rows=occurrence_rows,
        )
        encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(encoded, encoding="utf-8")
        print(encoded, end="")
        return 0
    except (ScoreError, stats.StatsValidationError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
