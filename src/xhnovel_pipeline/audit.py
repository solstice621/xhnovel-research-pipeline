from __future__ import annotations

import json
import pathlib
from typing import Any

from .catalog import Catalog
from .errors import ValidationError
from .hashing import object_hash
from .store import ArtifactStore
from .validate import validate_export


def explain_claim(catalog: Catalog, claim_id: str) -> dict[str, Any]:
    claim = catalog.get("Claim", claim_id)
    chain = []
    for sup in claim["support"]:
        ret = catalog.get("Retrieval", sup["retrieval_id"])
        seg = catalog.get("Segment", sup["segment_id"])
        chain.append(
            {
                "retrieval_id": ret["retrieval_id"],
                "source_id": ret["source_id"],
                "artifact_id": sup["artifact_id"],
                "segment_id": seg["segment_id"],
                "locator": seg["source_locator"],
                "normalized_text": seg["normalized_text"],
            }
        )
    return {"claim": claim, "chain": chain}


def trace_request(catalog: Catalog, request_id: str) -> dict[str, Any]:
    req = catalog.get("ResearchRequest", request_id)
    campaigns = [c for c in catalog.all("SearchCampaign") if c["request_id"] == request_id]
    return {
        "request": req,
        "campaigns": campaigns,
        "search_runs": catalog.ids("SearchRun"),
        "hits": catalog.ids("DiscoveryHit"),
        "exports": catalog.ids("EvidenceExport"),
    }


def verify_export_bytes(data: bytes, catalog: Catalog | None = None) -> dict[str, Any]:
    try:
        export = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError("E-EXPORT-TAMPER", "export is not valid JSON") from exc
    expected = object_hash(export, omit=("export_hash",))
    if export.get("export_hash") != expected:
        raise ValidationError("E-EXPORT-TAMPER", "export_hash does not match payload")
    if catalog is not None:
        # replace existing export with this one for validation
        catalog.by_type["EvidenceExport"] = [export]
        validate_export(catalog)
    return export


def check_artifact(store: ArtifactStore, artifact_id: str) -> str:
    store.verify(artifact_id)
    return "OK"


def diff_bundle(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    def s(key: str) -> tuple[set[str], set[str]]:
        left = set(a.get(key) or [])
        right = set(b.get(key) or [])
        return sorted(left - right), sorted(right - left)

    removed_seg, added_seg = s("segment_ids")
    removed_ret, added_ret = s("retrieval_ids")
    return {
        "removed_segments": removed_seg,
        "added_segments": added_seg,
        "removed_retrievals": removed_ret,
        "added_retrievals": added_ret,
        "policy_changed": a.get("policy_bundle_hash") != b.get("policy_bundle_hash"),
        "profile_changed": a.get("profile_id") != b.get("profile_id"),
        "hash_changed": a.get("bundle_hash") != b.get("bundle_hash"),
    }


def scan_artifacts(store: ArtifactStore, artifact_ids: list[str]) -> list[dict[str, str]]:
    out = []
    for aid in artifact_ids:
        try:
            store.verify(artifact_id=aid)
            out.append({"artifact_id": aid, "status": "OK"})
        except ValidationError as exc:
            out.append({"artifact_id": aid, "status": exc.code})
    return out


def gc_cas(store: ArtifactStore, live_ids: set[str]) -> list[str]:
    removed = []
    root = store.root / "sha256"
    if not root.exists():
        return removed
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        digest = path.name
        aid = f"sha256:{digest}"
        if aid not in live_ids:
            # do not delete; record candidate only. GC of unreferenced objects is opt-in.
            removed.append(aid)
    return removed


def restore_export(export_path: pathlib.Path, store: ArtifactStore, artifacts_dir: pathlib.Path) -> dict[str, Any]:
    export = json.loads(export_path.read_text(encoding="utf-8"))
    restored = []
    for item in export["artifact_manifest"]:
        aid = item["artifact_id"]
        digest = aid.split(":", 1)[1]
        src = artifacts_dir / digest
        if not src.is_file():
            continue
        got = store.put(src.read_bytes())
        if got != aid:
            raise ValidationError("E-HASH-MISMATCH", f"restored file is not {aid}")
        restored.append(aid)
    return {"export_id": export["export_id"], "restored": restored}
