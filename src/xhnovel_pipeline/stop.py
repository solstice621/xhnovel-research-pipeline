from __future__ import annotations

from typing import Any

from .origin import platform_classes


def has_two_independent_secondary_sources(
    *,
    retrievals: list[dict[str, Any]],
    triage_assessments: list[dict[str, Any]],
    origin_assessments: list[dict[str, Any]],
    sources: list[dict[str, Any]],
) -> bool:
    triage_by_id = {item["assessment_id"]: item for item in triage_assessments}
    eligible_sources = {
        retrieval["source_id"]
        for retrieval in retrievals
        if retrieval.get("status") == "FETCHED"
        and triage_by_id.get(retrieval.get("triage_assessment_id"), {}).get("retrieval_id")
        == retrieval["retrieval_id"]
        and triage_by_id.get(retrieval.get("triage_assessment_id"), {}).get("tier") == "B"
    }
    if len(eligible_sources) < 2:
        return False
    classes = platform_classes(sources)
    relations_by_pair: dict[frozenset[str], set[str]] = {}
    for assessment in origin_assessments:
        source_a = assessment.get("source_a")
        source_b = assessment.get("source_b")
        if (
            source_a in eligible_sources
            and source_b in eligible_sources
            and source_a != source_b
            and classes.get(source_a) != classes.get(source_b)
        ):
            relations_by_pair.setdefault(frozenset((source_a, source_b)), set()).add(assessment.get("relation"))
    return any(relations == {"INDEPENDENT"} for relations in relations_by_pair.values())


def decide_campaign_stop(
    *,
    coverage_reached: bool,
    fetch_budget_hit: bool,
    provider_exhausted: bool,
    query_budget_hit: bool,
) -> tuple[str, str]:
    """Return (campaign status, stop_reason). Terminal campaigns always have a reason."""
    if coverage_reached:
        return "COMPLETED", "coverage_reached"
    if fetch_budget_hit or query_budget_hit:
        return "BUDGET_STOPPED", "budget_exhausted"
    if provider_exhausted:
        return "EXHAUSTED", "provider_exhausted"
    return "EXHAUSTED", "no_new_source"


def campaign_report_payload(
    *,
    request: dict[str, Any],
    query: dict[str, Any],
    campaign: dict[str, Any],
    hits: list[dict[str, Any]],
    page_retrievals: list[dict[str, Any]],
    export: dict[str, Any],
    live_claim_count: int,
    report: str,
) -> dict[str, Any]:
    return {
        "request_id": request["request_id"],
        "query": query["query_text"],
        "provider": (hits[0]["search_run_id"] if hits else None),
        "hit_titles": [
            {
                "rank": h["rank"],
                "title": h["title"],
                "selection_status": h["selection_status"],
                "selection_reason": h.get("selection_reason", ""),
            }
            for h in hits
        ],
        "stop_reason": campaign.get("stop_reason"),
        "campaign_status": campaign.get("status"),
        "campaign_report": report,
        "live_claim_count": live_claim_count,
        "export_id": export["export_id"],
        "export_hash": export["export_hash"],
        "bundle_hash": export["bundle"]["bundle_hash"],
        "page_retrievals": page_retrievals,
    }
