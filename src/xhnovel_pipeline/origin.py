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
