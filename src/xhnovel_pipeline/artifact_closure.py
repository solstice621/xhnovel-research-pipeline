from __future__ import annotations

from typing import Any


def live_artifact_ids(catalog_data: dict[str, Any]) -> set[str]:
    """Return the CAS closure required by ingestion, review, analysis and export."""
    ids = {artifact["artifact_id"] for artifact in catalog_data.get("Artifact") or []}
    for snapshot in catalog_data.get("CollectionSnapshot") or []:
        ids.update(snapshot.get("artifact_ids") or [])
    for bundle in catalog_data.get("EvidenceBundle") or []:
        ids.update(bundle.get("artifact_ids") or [])
    for export in catalog_data.get("EvidenceExport") or []:
        ids.update(item["artifact_id"] for item in export.get("artifact_manifest") or [])
    return ids
