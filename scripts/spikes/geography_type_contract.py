"""Analysis-only projections for Geography type-contract experiment D.

Consumes already validated outputs. Never changes native payloads or identities.
The projection is shared across legacy typed mentions and split type assertions.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

Atom = tuple[str, ...]
FAMILIES = ("PLACE", "TYPE", "REL")


def project_payload(payload: dict[str, Any]) -> set[Atom]:
    kind = payload["kind"]
    if kind == "PLACE_MENTION":
        atoms = {("PLACE", payload["name"])}
        if "explicit_type" in payload:
            atoms.add(("TYPE", payload["name"], payload["explicit_type"]))
        return atoms
    if kind == "PLACE_TYPE_ASSERTION":
        # A type assertion never repairs a missing PLACE_MENTION.
        return {("TYPE", payload["place_name"], payload["explicit_type"])}
    if kind == "SPATIAL_RELATION":
        return {("REL", payload["subject_name"], payload["relation"], payload["object_name"])}
    raise ValueError(f"unsupported geography payload kind: {kind}")


def project_records(records: Iterable[dict[str, Any]]) -> set[Atom]:
    return {atom for row in records for atom in project_payload(row["payload"])}


def ratio(n: int | float, d: int) -> float | None:
    return n / d if d else None


def prf(predicted: set, gold: set) -> dict[str, Any]:
    tp = len(predicted & gold)
    return {"tp": tp, "predicted": len(predicted), "gold": len(gold),
            "precision": ratio(tp, len(predicted)), "recall": ratio(tp, len(gold))}


def joint_places(atoms: set[Atom]) -> set[tuple[str, tuple[str, ...]]]:
    types: dict[str, set[str]] = defaultdict(set)
    for atom in atoms:
        if atom[0] == "TYPE":
            types[atom[1]].add(atom[2])
    return {(atom[1], tuple(sorted(types[atom[1]]))) for atom in atoms if atom[0] == "PLACE"}


def score_atoms(predicted: set[Atom], gold: set[Atom]) -> dict[str, Any]:
    result = {family: prf({a for a in predicted if a[0] == family},
                          {a for a in gold if a[0] == family}) for family in FAMILIES}
    result["JOINT_PLACE"] = prf(joint_places(predicted), joint_places(gold))
    return result


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for family in (*FAMILIES, "JOINT_PLACE"):
        counts = {k: sum(row[family][k] for row in rows) for k in ("tp", "predicted", "gold")}
        result[family] = {**counts, "precision": ratio(counts["tp"], counts["predicted"]),
                          "recall": ratio(counts["tp"], counts["gold"])}
    return result


def attribution(predicted: set[Atom], gold: set[Atom]) -> dict[str, Any]:
    """Reference-relative disagreements, not automated semantic adjudication."""
    pred_names = {a[1] for a in predicted if a[0] == "PLACE"}
    gold_names = {a[1] for a in gold if a[0] == "PLACE"}
    extra_types = {a for a in predicted - gold if a[0] == "TYPE"}
    missing_types = {a for a in gold - predicted if a[0] == "TYPE"}
    substitutions = {a[1] for a in extra_types} & {a[1] for a in missing_types}
    return {
        "extra_names_need_semantic_review": sorted(pred_names - gold_names),
        "missing_names": sorted(gold_names - pred_names),
        "same_name_type_substitution": sorted(substitutions & pred_names & gold_names),
        "extra_types": sorted(extra_types), "missing_types": sorted(missing_types),
        "extra_relations_need_semantic_review": sorted(a for a in predicted - gold if a[0] == "REL"),
        "missing_relations": sorted(a for a in gold - predicted if a[0] == "REL"),
    }


def contains(gold_spans: list[tuple[int, int]], pred_spans: list[tuple[int, int]]) -> bool:
    # Each interval remains source-exact; do not manufacture cross-gap support.
    return bool(gold_spans) and all(any(ps <= gs and ge <= pe for ps, pe in pred_spans)
                                   for gs, ge in gold_spans)
