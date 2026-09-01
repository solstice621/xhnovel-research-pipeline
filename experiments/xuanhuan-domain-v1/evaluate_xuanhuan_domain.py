#!/usr/bin/env python3
"""Read-only evaluator for xuanhuan-domain-v1.

This script never creates, patches, filters, or re-merges SceneCandidates.
It only reads native pipeline artifacts plus the pre-registered gold file.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from collections import Counter, defaultdict
from typing import Any

from xhnovel_pipeline.parse import normalize_text

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

STATE_KEYS = ("possession", "ownership", "permission", "binding", "obligation")

CHAPTER_ORDINAL = {
    "chapter-01.txt": 1,
    "chapter-02.txt": 2,
    "chapter-03.txt": 3,
    "chapter-04.txt": 4,
    "chapter-05.txt": 5,
    "chapter-06.txt": 6,
    "chapter-07.txt": 7,
    "chapter-08.txt": 8,
}


def _load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{line_no} is not an object")
        rows.append(row)
    return rows


def verify_gold(gold_path: pathlib.Path, chapters_dir: pathlib.Path) -> list[dict[str, Any]]:
    golds = _load_jsonl(gold_path)
    by_family: Counter[str] = Counter()
    groups: dict[str, list[str]] = defaultdict(list)
    for gold in golds:
        chapter = chapters_dir / gold["chapter_file"]
        text = chapter.read_text(encoding="utf-8")
        quote = gold["quote"]
        start = gold["char_start"]
        end = gold["char_end"]
        if text[start:end] != quote:
            raise SystemExit(f"gold {gold['gold_id']} offset mismatch in {gold['chapter_file']}")
        if text.count(quote) != 1:
            raise SystemExit(f"gold {gold['gold_id']} quote is not unique in {gold['chapter_file']}")
        by_family[gold["family"]] += 1
        if gold.get("adjacent_group"):
            groups[gold["adjacent_group"]].append(gold["gold_id"])
    if by_family["A"] < 8 or by_family["B"] < 8 or by_family["HN"] < 8:
        raise SystemExit(f"gold coverage too small: {dict(by_family)}")
    if len(groups) < 4 or any(len(members) != 2 for members in groups.values()):
        raise SystemExit(f"adjacent groups invalid: {dict(groups)}")
    return golds


def _catalog_records(catalog: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    rows = catalog.get(kind) or []
    if not isinstance(rows, list):
        raise SystemExit(f"catalog {kind} is not an array")
    return rows


def _index_by(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {row[key]: row for row in rows}


def spans_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left["segment_id"] != right["segment_id"]:
        return False
    return max(left["start"], right["start"]) < min(left["end"], right["end"])


def candidate_span_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return any(
        spans_overlap(first, second)
        for first in left.get("source_spans") or []
        for second in right.get("source_spans") or []
    )


def gold_to_spans(
    gold: dict[str, Any],
    chapters: dict[int, dict[str, Any]],
    segments_by_chapter: dict[int, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    ordinal = CHAPTER_ORDINAL[gold["chapter_file"]]
    chapter = chapters[ordinal]
    spans: list[dict[str, Any]] = []
    for segment in segments_by_chapter.get(ordinal, []):
        locator = segment.get("source_locator") or {}
        if locator.get("kind") != "text":
            continue
        left = max(gold["char_start"], locator["start"])
        right = min(gold["char_end"], locator["end"])
        if left >= right:
            continue
        raw_slice = gold["quote"]
        # Reconstruct the overlapping raw slice from the quote using file offsets.
        quote_left = left - gold["char_start"]
        quote_right = right - gold["char_start"]
        raw_slice = gold["quote"][quote_left:quote_right]
        normalized_slice = normalize_text(raw_slice)
        normalized = segment["normalized_text"]
        if not normalized_slice:
            continue
        found = normalized.find(normalized_slice)
        if found < 0:
            # Fall back to full-line coverage when whitespace folding hides a short overlap.
            found = 0
            end = len(normalized)
        else:
            end = found + len(normalized_slice)
        spans.append(
            {
                "segment_id": segment["segment_id"],
                "start": found,
                "end": max(found + 1, end),
                "chapter_id": chapter["chapter_id"],
            }
        )
    return spans


def gold_matches_candidate(gold_spans: list[dict[str, Any]], candidate: dict[str, Any]) -> bool:
    return any(
        spans_overlap(gold_span, cand_span)
        for gold_span in gold_spans
        for cand_span in candidate.get("source_spans") or []
    )


def observation_blob(candidate: dict[str, Any]) -> str:
    parts = [str(candidate.get("summary") or "")]
    for field in OBSERVATION_FIELDS:
        obs = candidate.get(field) or {}
        parts.extend(obs.get("values") or [])
    return "\n".join(parts)


def state_hits(expected: dict[str, str], blob: str) -> dict[str, bool]:
    text = blob.replace(" ", "")
    checks = {}
    for key, value in expected.items():
        if not value:
            continue
        needles = []
        if key == "possession":
            needles = ["持有", "掌心", "手里", "握", "抽", "夺", "松开", "失去"]
        elif key == "ownership":
            needles = ["所有权", "主人", "买主", "霜烬宗", "契"]
        elif key == "permission":
            needles = ["许可", "准许", "使用权", "一夜"]
        elif key == "binding":
            needles = ["禁制", "锁纹", "绑定", "弹回", "催不动"]
        elif key == "obligation":
            needles = ["誓", "欠", "令", "委托", "定金", "违约", "两讫", "拒绝", "提议"]
        checks[key] = any(needle in text for needle in needles) or any(
            token in text for token in re.findall(r"[\u4e00-\u9fff]{2,}", value)[:6]
        )
    return checks


def load_run(work_dir: pathlib.Path) -> dict[str, Any] | None:
    research = work_dir / "research"
    if not research.exists():
        return None
    catalogs = list(research.glob("*/catalog.json"))
    if not catalogs:
        return None
    catalog_path = catalogs[0]
    run_dir = catalog_path.parent
    catalog = _load_json(catalog_path)
    candidates_path = run_dir / "scene-candidates.json"
    scout_path = run_dir / "scene-scout-run.json"
    merge_path = run_dir / "scene-merge-run.json"
    return {
        "work_dir": str(work_dir),
        "catalog_path": str(catalog_path),
        "store_path": str(work_dir / "ingestion" / "objects"),
        "catalog": catalog,
        "candidates": _load_json(candidates_path) if candidates_path.exists() else [],
        "scout_run": _load_json(scout_path) if scout_path.exists() else None,
        "merge_run": _load_json(merge_path) if merge_path.exists() else None,
        "run_dir": str(run_dir),
    }


def attempt_stats(catalog: dict[str, Any], scout_run: dict[str, Any] | None) -> dict[str, Any]:
    attempts = _catalog_records(catalog, "ModelAttempt")
    windows = _catalog_records(catalog, "SceneWindow")
    window_ids = [w["window_id"] for w in windows]
    if scout_run:
        window_ids = list(scout_run.get("window_ids") or window_ids)
    by_window: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for attempt in attempts:
        by_window[attempt["subject_id"]].append(attempt)
    accepted = []
    rejected = []
    reasons: Counter[str] = Counter()
    for window_id in window_ids:
        window_attempts = sorted(by_window.get(window_id, []), key=lambda item: item["attempt_ordinal"])
        if not window_attempts:
            rejected.append({"window_id": window_id, "reason": "NO_ATTEMPT", "status": None})
            reasons["NO_ATTEMPT"] += 1
            continue
        final = window_attempts[-1]
        if final["status"] == "SUCCEEDED":
            accepted.append(window_id)
        else:
            reason = final.get("error_code") or final["status"]
            rejected.append(
                {
                    "window_id": window_id,
                    "reason": reason,
                    "status": final["status"],
                    "attempt_id": final["attempt_id"],
                    "response_artifact_id": final.get("response_artifact_id"),
                }
            )
            reasons[str(reason)] += 1
    usage = (scout_run or {}).get("usage_ledger") or {}
    return {
        "total_windows": len(window_ids),
        "accepted_windows": len(accepted),
        "rejected_windows": len(rejected),
        "acceptance_rate": (len(accepted) / len(window_ids)) if window_ids else 0.0,
        "rejection_reasons": dict(reasons),
        "rejected": rejected,
        "usage": usage,
    }


def evaluate_run(
    name: str,
    payload: dict[str, Any],
    golds: list[dict[str, Any]],
    gold_spans: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    candidates = payload["candidates"]
    catalog = payload["catalog"]
    matched: dict[str, list[str]] = {}
    unmatched = []
    hn_hits = []
    for gold in golds:
        hits = [
            c["scene_candidate_id"]
            for c in candidates
            if gold_matches_candidate(gold_spans[gold["gold_id"]], c)
        ]
        if gold["family"] == "HN":
            if hits:
                hn_hits.append({"gold_id": gold["gold_id"], "candidate_ids": hits})
            continue
        matched[gold["gold_id"]] = hits
        if not hits:
            unmatched.append(gold["gold_id"])
    a_ids = [g["gold_id"] for g in golds if g["family"] == "A"]
    b_ids = [g["gold_id"] for g in golds if g["family"] == "B"]
    a_recall = sum(1 for gid in a_ids if matched.get(gid)) / len(a_ids) if a_ids else 0.0
    b_recall = sum(1 for gid in b_ids if matched.get(gid)) / len(b_ids) if b_ids else 0.0

    fidelity_rows = []
    for gold in golds:
        if gold["family"] == "HN" or not matched.get(gold["gold_id"]):
            continue
        expected = {k: v for k, v in (gold.get("expected_states") or {}).items() if v}
        if not expected:
            continue
        blobs = [
            observation_blob(c)
            for c in candidates
            if c["scene_candidate_id"] in matched[gold["gold_id"]]
        ]
        blob = "\n".join(blobs)
        hits = state_hits(expected, blob)
        fidelity_rows.append(
            {
                "gold_id": gold["gold_id"],
                "fields": hits,
                "ok": all(hits.values()) if hits else False,
            }
        )
    fidelity = (
        sum(1 for row in fidelity_rows if row["ok"]) / len(fidelity_rows)
        if fidelity_rows
        else 0.0
    )

    over_broad = []
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for gold in golds:
        if gold.get("adjacent_group"):
            groups[gold["adjacent_group"]].append(gold)
    for group_id, members in groups.items():
        if len(members) != 2:
            continue
        left, right = members
        for candidate in candidates:
            if gold_matches_candidate(gold_spans[left["gold_id"]], candidate) and gold_matches_candidate(
                gold_spans[right["gold_id"]], candidate
            ):
                over_broad.append(
                    {
                        "group": group_id,
                        "gold_ids": [left["gold_id"], right["gold_id"]],
                        "scene_candidate_id": candidate["scene_candidate_id"],
                    }
                )
                break
    merge_run = payload.get("merge_run") or {}
    stages = merge_run.get("stages") or []
    return {
        "name": name,
        "candidate_count": len(candidates),
        "a_recall": a_recall,
        "b_recall": b_recall,
        "matched": matched,
        "unmatched_positive": unmatched,
        "hard_negative_hits": hn_hits,
        "state_fidelity": fidelity,
        "state_fidelity_rows": fidelity_rows,
        "over_broad_merges": over_broad,
        "over_broad_merge_rate": (len(over_broad) / len(groups)) if groups else 0.0,
        "merge_stages": stages,
        "pipeline": attempt_stats(catalog, payload.get("scout_run")),
        "catalog_path": payload["catalog_path"],
        "store_path": payload["store_path"],
        "run_dir": payload["run_dir"],
    }


def cross_run_jaccard(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> dict[str, Any]:
    matched_left = set()
    matched_right = set()
    pairs = []
    for i, lcand in enumerate(left):
        for j, rcand in enumerate(right):
            if candidate_span_overlap(lcand, rcand):
                matched_left.add(i)
                matched_right.add(j)
                pairs.append((lcand["scene_candidate_id"], rcand["scene_candidate_id"]))
    only_left = len(left) - len(matched_left)
    only_right = len(right) - len(matched_right)
    # Identity set = matched connected pairs as one + unmatched candidates
    # Jaccard on candidate identities via overlap matching:
    # |intersection| = number of matched identities, using max of matched sides
    # after clustering overlap pairs.
    parent = {("L", i): ("L", i) for i in range(len(left))}
    parent.update({("R", j): ("R", j) for j in range(len(right))})

    def find(node: tuple[str, int]) -> tuple[str, int]:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a: tuple[str, int], b: tuple[str, int]) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, lcand in enumerate(left):
        for j, rcand in enumerate(right):
            if candidate_span_overlap(lcand, rcand):
                union(("L", i), ("R", j))
    clusters: dict[tuple[str, int], dict[str, list[int]]] = defaultdict(lambda: {"L": [], "R": []})
    for i in range(len(left)):
        clusters[find(("L", i))]["L"].append(i)
    for j in range(len(right)):
        clusters[find(("R", j))]["R"].append(j)
    intersection = sum(1 for item in clusters.values() if item["L"] and item["R"])
    union_count = len(clusters)
    jac = intersection / union_count if union_count else 1.0
    return {
        "jaccard": jac,
        "separation": 1.0 - jac,
        "intersection_identities": intersection,
        "union_identities": union_count,
        "left_count": len(left),
        "right_count": len(right),
        "matched_pairs": pairs,
        "unmatched_left": only_left,
        "unmatched_right": only_right,
    }


def build_gold_spans(golds: list[dict[str, Any]], catalog: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    chapters = _index_by(_catalog_records(catalog, "NovelChapter"), "chapter_id")
    chapters_by_ordinal = {row["ordinal"]: row for row in chapters.values()}
    segments = _index_by(_catalog_records(catalog, "Segment"), "segment_id")
    by_chapter: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for chapter in chapters.values():
        for segment_id in chapter.get("segment_ids") or []:
            by_chapter[chapter["ordinal"]].append(segments[segment_id])
    return {
        gold["gold_id"]: gold_to_spans(gold, chapters_by_ordinal, by_chapter) for gold in golds
    }


def verdict(metrics: dict[str, Any]) -> str:
    sep_ab = metrics["separation_A1_B"]
    sep_aa = metrics["separation_A1_A2"]
    effect = sep_ab - sep_aa
    a_recall = metrics["A1"]["a_recall"]
    b_recall = metrics["B"]["b_recall"]
    exact = metrics.get("exact_precision")
    fidelity = min(metrics["A1"]["state_fidelity"], metrics["B"]["state_fidelity"])
    accept = min(
        metrics["A1"]["pipeline"]["acceptance_rate"],
        metrics["B"]["pipeline"]["acceptance_rate"],
        metrics["A2"]["pipeline"]["acceptance_rate"],
    )
    failure = (
        sep_ab < 0.30
        or (exact is not None and exact < 0.70)
        or fidelity < 0.60
        or accept < 0.80
    )
    success = (
        sep_ab >= 0.50
        and effect >= 0.15
        and a_recall >= 0.75
        and b_recall >= 0.75
        and (exact is None or exact >= 0.80)
        and fidelity >= 0.75
        and accept >= 0.90
    )
    if failure:
        return "FAILURE"
    if success:
        return "SUCCESS"
    return "MIXED"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[2])
    parser.add_argument("--runtime", type=pathlib.Path, default=None)
    parser.add_argument("--out", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)
    root = args.root
    gold_path = root / "fixtures/xuanhuan-domain-v1/gold-scenes.jsonl"
    chapters_dir = root / "fixtures/xuanhuan-domain-v1/chapters"
    golds = verify_gold(gold_path, chapters_dir)
    runtime = args.runtime or (root / ".runtime/xuanhuan-domain-v1")
    runs = {}
    for name in ("A1", "B", "A2"):
        payload = load_run(runtime / name)
        if payload is None:
            runs[name] = None
            continue
        gold_spans = build_gold_spans(golds, payload["catalog"])
        runs[name] = evaluate_run(name, payload, golds, gold_spans)
        runs[name]["_candidates"] = payload["candidates"]

    missing = [name for name, row in runs.items() if row is None]
    report: dict[str, Any] = {
        "gold_counts": dict(Counter(g["family"] for g in golds)),
        "missing_runs": missing,
    }
    if missing:
        report["DOMAIN_VERDICT"] = None
        report["blocker"] = f"native runs not available: {missing}"
    else:
        sep_ab = cross_run_jaccard(runs["A1"]["_candidates"], runs["B"]["_candidates"])
        sep_aa = cross_run_jaccard(runs["A1"]["_candidates"], runs["A2"]["_candidates"])
        metrics = {
            "A1": {k: v for k, v in runs["A1"].items() if k != "_candidates"},
            "B": {k: v for k, v in runs["B"].items() if k != "_candidates"},
            "A2": {k: v for k, v in runs["A2"].items() if k != "_candidates"},
            "separation_A1_B": sep_ab["separation"],
            "separation_A1_A2": sep_aa["separation"],
            "jaccard_A1_B": sep_ab,
            "jaccard_A1_A2": sep_aa,
            "effect_minus_noise": sep_ab["separation"] - sep_aa["separation"],
        }
        metrics["exact_precision"] = None
        metrics["DOMAIN_VERDICT"] = verdict(metrics)
        report.update(metrics)
    out_path = args.out or (runtime / "evaluation.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in report if k not in {"A1", "B", "A2"}}, ensure_ascii=False, indent=2))
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
