from __future__ import annotations

import copy
from typing import Any

from .catalog import Catalog
from .errors import ValidationError
from .ids import derived_id
from .validate import bundle_hash

FROZEN_STATES = {"FROZEN", "EXTRACTED", "EXPORTED"}


def freeze_bundle(catalog: Catalog, bundle: dict[str, Any]) -> dict[str, Any]:
    if bundle["status"] == "DRAFT":
        bundle["status"] = "FROZEN"
    bundle["bundle_hash"] = bundle_hash(catalog, bundle)
    bundle["bundle_id"] = derived_id("EvidenceBundle", {"bundle_hash": bundle["bundle_hash"]})
    catalog.frozen_bundle_ids.add(bundle["bundle_id"])
    return bundle


def assert_frozen_intact(catalog: Catalog, bundle: dict[str, Any]) -> None:
    if bundle["status"] not in FROZEN_STATES:
        return
    expected = bundle_hash(catalog, bundle)
    if bundle["bundle_hash"] != expected:
        raise ValidationError("E-FROZEN", f"{bundle['bundle_id']} frozen members changed in place")


def refuse_inplace_member_edit(catalog: Catalog, bundle: dict[str, Any], **fields: Any) -> None:
    if bundle["status"] not in FROZEN_STATES:
        bundle.update(fields)
        return
    raise ValidationError("E-FROZEN", f"{bundle['bundle_id']} is frozen; create a new bundle")


def bundle_from_snapshot(
    catalog: Catalog,
    *,
    request_id: str,
    snapshot_id: str,
    document_ids: list[str],
    segment_ids: list[str],
    retrieval_ids: list[str],
    artifact_ids: list[str],
    triage_assessment_ids: list[str],
    origin_assessment_ids: list[str],
    selection_manifest: dict[str, Any],
    profile_id: str,
    policy_bundle_hash: str,
    frozen_at: str,
    schema_version: str,
    supersedes: str | None = None,
) -> dict[str, Any]:
    bundle = {
        "schema_version": schema_version,
        "bundle_id": "BND-PENDING",
        "request_id": request_id,
        "collection_snapshot_ids": [snapshot_id],
        "document_ids": document_ids,
        "segment_ids": segment_ids,
        "retrieval_ids": retrieval_ids,
        "artifact_ids": artifact_ids,
        "triage_assessment_ids": triage_assessment_ids,
        "origin_assessment_ids": origin_assessment_ids,
        "selection_manifest": selection_manifest,
        "profile_id": profile_id,
        "policy_bundle_hash": policy_bundle_hash,
        "bundle_hash": "sha256:" + "0" * 64,
        "frozen_at": frozen_at,
        "supersedes": supersedes,
        "status": "DRAFT",
    }
    freeze_bundle(catalog, bundle)
    return bundle


def clone_bundle_with_selection(
    catalog: Catalog,
    src: dict[str, Any],
    *,
    selection_manifest: dict[str, Any],
) -> dict[str, Any]:
    bundle = copy.deepcopy(src)
    bundle["selection_manifest"] = selection_manifest
    bundle["supersedes"] = src["bundle_id"]
    bundle["status"] = "DRAFT"
    freeze_bundle(catalog, bundle)
    return bundle
