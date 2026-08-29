from __future__ import annotations

from typing import Any


def decide_campaign_stop(
    *,
    selected_page_count: int,
    fetch_budget_hit: bool,
    first_page_empty: bool,
    query_budget_hit: bool,
) -> tuple[str, str]:
    """Return (campaign status, stop_reason). Terminal campaigns always have a reason."""
    if selected_page_count:
        return "COMPLETED", "coverage_reached"
    if fetch_budget_hit or query_budget_hit:
        return "BUDGET_STOPPED", "budget_exhausted"
    if first_page_empty:
        return "EXHAUSTED", "provider_exhausted"
    return "COMPLETED", "no_new_source"


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
