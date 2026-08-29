from __future__ import annotations

from .errors import ValidationError


def normalize_platform(platform: object) -> str:
    return str(platform or "").strip().casefold()


def platform_classes(sources: list[dict]) -> dict[str, str]:
    by_id: dict[str, dict] = {}
    for src in sources:
        sid = src.get("source_id")
        if not sid:
            raise ValidationError("E-SOURCE-ID", "source missing source_id")
        if sid in by_id:
            raise ValidationError("E-DUP-SOURCE", f"duplicate source_id {sid}")
        by_id[sid] = src
    parent = {sid: sid for sid in by_id}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for sid, src in by_id.items():
        alias = src.get("same_platform_as")
        if not alias:
            continue
        if alias not in by_id:
            raise ValidationError("E-DANGLING-ALIAS", f"{sid} same_platform_as {alias!r} does not exist")
        union(sid, alias)
    seen: dict[str, str] = {}
    for sid, src in by_id.items():
        plat = normalize_platform(src.get("platform_id") or src.get("platform"))
        if not plat:
            continue
        if plat in seen:
            union(sid, seen[plat])
        else:
            seen[plat] = sid
    return {sid: find(sid) for sid in parent}


def independent_pair(rel: str) -> bool:
    return rel == "INDEPENDENT"


def token_jaccard(a: str, b: str) -> float:
    ta = {t for t in a.split() if t}
    tb = {t for t in b.split() if t}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def existing_origin_pair(catalog_origins: list[dict], src_a: str, src_b: str) -> dict | None:
    wanted = {src_a, src_b}
    for orig in catalog_origins:
        if {orig["source_a"], orig["source_b"]} == wanted:
            return orig
    return None


def near_duplicate_assessments(
    texts_by_source: dict[str, str],
    *,
    policy_hash: str,
    assessor_build_id: str,
    assessed_at: str,
    schema_version: str,
    existing: list[dict],
    threshold: float = 0.92,
) -> list[dict]:
    """Emit OriginAssessment for near-duplicate texts. Never merge Source objects."""
    extra: list[dict] = []
    ids = list(texts_by_source)
    n = 0
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            if existing_origin_pair(existing, a, b):
                continue
            score = token_jaccard(texts_by_source[a], texts_by_source[b])
            if texts_by_source[a] == texts_by_source[b]:
                relation = "SAME_ORIGIN"
                basis = ["exact_normalized_text"]
                confidence = "HIGH"
            elif score >= threshold:
                relation = "LIKELY_SAME_ORIGIN"
                basis = [f"near_duplicate_jaccard:{score:.2f}"]
                confidence = "MEDIUM"
            else:
                continue
            n += 1
            extra.append(
                {
                    "schema_version": schema_version,
                    "assessment_id": f"ORI-NEARDUP-{n:03d}",
                    "source_a": a,
                    "source_b": b,
                    "relation": relation,
                    "confidence": confidence,
                    "basis": basis,
                    "assessor_build_id": assessor_build_id,
                    "policy_hash": policy_hash,
                    "assessed_at": assessed_at,
                }
            )
    return extra
