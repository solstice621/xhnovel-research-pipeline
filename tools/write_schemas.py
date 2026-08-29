#!/usr/bin/env python3
"""Write language-neutral JSON Schema files for v0.1-draft-frozen."""
from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "contracts"
SCHEMA = "https://json-schema.org/draft/2020-12/schema"
BASE = "https://xhnovel.local/contracts"
VERSION = "0.1-draft-frozen"


def dump(name: str, doc: dict) -> None:
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def obj(required: list[str], props: dict, extra: dict | None = None) -> dict:
    doc = {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": props,
    }
    if extra:
        doc.update(extra)
    return doc


idpat = {
    "REQ": r"^REQ-[A-Z0-9][A-Z0-9._:-]{1,}$",
    "CAM": r"^CAM-[A-Z0-9][A-Z0-9._:-]{1,}$",
    "QRY": r"^QRY-[A-Z0-9][A-Z0-9._:-]{1,}$",
    "SRUN": r"^SRUN-[A-Z0-9][A-Z0-9._:-]{1,}$",
    "HIT": r"^HIT-[A-Z0-9][A-Z0-9._:-]{1,}$",
    "SRC": r"^SRC-[A-Z0-9][A-Z0-9._:-]{1,}$",
    "RET": r"^RET-[A-Z0-9][A-Z0-9._:-]{1,}$",
    "TRI": r"^TRI-[A-Z0-9][A-Z0-9._:-]{1,}$",
    "ORI": r"^ORI-[A-Z0-9][A-Z0-9._:-]{1,}$",
    "PRUN": r"^PRUN-[A-Z0-9][A-Z0-9._:-]{1,}$",
    "DOC": r"^DOC-[A-Z0-9][A-Z0-9._:-]{1,}$",
    "SEG": r"^SEG-[A-Z0-9][A-Z0-9._:-]{1,}$",
    "SNP": r"^SNP-[A-Z0-9][A-Z0-9._:-]{1,}$",
    "BND": r"^BND-[A-Z0-9][A-Z0-9._:-]{1,}$",
    "ERUN": r"^ERUN-[A-Z0-9][A-Z0-9._:-]{1,}$",
    "CLM": r"^CLM-[A-Z0-9][A-Z0-9._:-]{1,}$",
    "BLD": r"^BLD-[A-Z0-9][A-Z0-9._:-]{1,}$",
    "QRUN": r"^QRUN-[A-Z0-9][A-Z0-9._:-]{1,}$",
    "ASR": r"^ASR-[A-Z0-9][A-Z0-9._:-]{1,}$",
    "EXP": r"^EXP-[A-Z0-9][A-Z0-9._:-]{1,}$",
}

art = r"^sha256:[0-9a-f]{64}$"
sha = r"^sha256:[0-9a-f]{64}$"
iso = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"


def sid(prefix: str) -> dict:
    return {"type": "string", "pattern": idpat[prefix]}


def main() -> None:
    dump(
        "defs.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/defs.schema.json",
            "$defs": {
                "schema_version": {"type": "string", "const": VERSION},
                "artifact_id": {"type": "string", "pattern": art},
                "sha256": {"type": "string", "pattern": sha},
                "iso_datetime": {"type": "string", "pattern": iso},
            },
        },
    )
    dump(
        "research-request.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/research-request.schema.json",
            "title": "ResearchRequest",
            **obj(
                [
                    "schema_version",
                    "request_id",
                    "origin",
                    "mode",
                    "discovery_brief",
                    "search_constraints",
                    "extraction_profile",
                    "budget",
                    "created_at",
                ],
                {
                    "schema_version": {"const": VERSION},
                    "request_id": sid("REQ"),
                    "origin": obj(
                        ["repository", "commit"],
                        {
                            "repository": {"type": "string", "minLength": 1},
                            "commit": {"type": "string", "minLength": 7},
                            "external_question_id": {"type": "string"},
                        },
                    ),
                    "mode": {"enum": ["EXPLORE", "DEEPEN", "CONFIRM", "CHALLENGE"]},
                    "discovery_brief": {"type": "string", "minLength": 1},
                    "search_constraints": {"type": "object", "additionalProperties": True},
                    "extraction_profile": {"type": "string", "minLength": 1},
                    "budget": obj(
                        ["max_queries", "max_fetches"],
                        {
                            "max_queries": {"type": "integer", "minimum": 1},
                            "max_fetches": {"type": "integer", "minimum": 1},
                            "max_bytes": {"type": "integer", "minimum": 1},
                        },
                    ),
                    "created_at": {"type": "string", "pattern": iso},
                    "supersedes": {"type": ["string", "null"]},
                },
            ),
        },
    )
    dump(
        "search-campaign.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/search-campaign.schema.json",
            **obj(
                [
                    "schema_version",
                    "campaign_id",
                    "request_id",
                    "planner_build_id",
                    "coverage_goals",
                    "budget",
                    "status",
                    "created_at",
                ],
                {
                    "schema_version": {"const": VERSION},
                    "campaign_id": sid("CAM"),
                    "request_id": sid("REQ"),
                    "planner_build_id": {"type": "string", "minLength": 1},
                    "coverage_goals": {"type": "array", "items": {"type": "string"}},
                    "budget": {"type": "object", "additionalProperties": True},
                    "iterations": {"type": "integer", "minimum": 0},
                    "stop_policy_hash": {"type": "string", "pattern": sha},
                    "status": {
                        "enum": [
                            "DRAFT",
                            "RUNNING",
                            "COMPLETED",
                            "EXHAUSTED",
                            "BUDGET_STOPPED",
                            "FAILED",
                            "CANCELLED",
                        ]
                    },
                    "stop_reason": {
                        "enum": [
                            "coverage_reached",
                            "budget_exhausted",
                            "no_new_source",
                            "provider_exhausted",
                            "manual_stop",
                            "failed",
                        ]
                    },
                    "created_at": {"type": "string", "pattern": iso},
                },
            ),
        },
    )
    dump(
        "query-spec.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/query-spec.schema.json",
            **obj(
                [
                    "schema_version",
                    "query_id",
                    "campaign_id",
                    "query_text",
                    "query_role",
                    "generated_by",
                    "locale",
                ],
                {
                    "schema_version": {"const": VERSION},
                    "query_id": sid("QRY"),
                    "campaign_id": sid("CAM"),
                    "query_text": {"type": "string", "minLength": 1},
                    "query_role": {
                        "enum": ["DISCOVER", "PRIMARY_SOURCE", "CONFIRM", "CHALLENGE", "CONFLICT"]
                    },
                    "parent_query_id": {"type": ["string", "null"]},
                    "derived_from_hit_ids": {"type": "array", "items": sid("HIT")},
                    "generated_by": {"type": "string"},
                    "rationale": {"type": "string"},
                    "locale": {"type": "string"},
                },
            ),
        },
    )
    dump(
        "search-run.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/search-run.schema.json",
            **obj(
                [
                    "schema_version",
                    "search_run_id",
                    "query_id",
                    "provider_id",
                    "provider_build_id",
                    "parameters",
                    "started_at",
                    "status",
                ],
                {
                    "schema_version": {"const": VERSION},
                    "search_run_id": sid("SRUN"),
                    "query_id": sid("QRY"),
                    "provider_id": {"type": "string"},
                    "provider_build_id": {"type": "string"},
                    "parameters": {"type": "object"},
                    "started_at": {"type": "string", "pattern": iso},
                    "finished_at": {"type": "string", "pattern": iso},
                    "raw_response_artifact_id": {"type": "string", "pattern": art},
                    "result_set_hash": {"type": "string", "pattern": sha},
                    "status": {"enum": ["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]},
                    "retry_of": {"type": ["string", "null"]},
                },
            ),
        },
    )
    dump(
        "discovery-hit.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/discovery-hit.schema.json",
            **obj(
                [
                    "schema_version",
                    "hit_id",
                    "search_run_id",
                    "rank",
                    "url",
                    "title",
                    "snippet",
                    "selection_status",
                ],
                {
                    "schema_version": {"const": VERSION},
                    "hit_id": sid("HIT"),
                    "search_run_id": sid("SRUN"),
                    "rank": {"type": "integer", "minimum": 1},
                    "url": {"type": "string", "minLength": 1},
                    "title": {"type": "string"},
                    "snippet": {"type": "string"},
                    "selection_status": {
                        "enum": ["SELECTED", "REJECTED", "DUPLICATE", "UNREACHABLE"]
                    },
                    "selection_reason": {"type": "string"},
                },
            ),
        },
    )
    dump(
        "source.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/source.schema.json",
            **obj(
                ["schema_version", "source_id", "canonical_url", "platform_id"],
                {
                    "schema_version": {"const": VERSION},
                    "source_id": sid("SRC"),
                    "canonical_url": {"type": "string"},
                    "platform_id": {"type": "string"},
                    "title": {"type": "string"},
                    "author": {"type": "string"},
                    "work": {"type": "string"},
                    "document_location": {"type": "string"},
                    "same_platform_as": {"type": ["string", "null"]},
                },
            ),
        },
    )
    dump(
        "retrieval.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/retrieval.schema.json",
            **obj(
                [
                    "schema_version",
                    "retrieval_id",
                    "source_id",
                    "requested_url",
                    "access_kind",
                    "status",
                ],
                {
                    "schema_version": {"const": VERSION},
                    "retrieval_id": sid("RET"),
                    "source_id": sid("SRC"),
                    "discovery_hit_id": sid("HIT"),
                    "requested_url": {"type": "string"},
                    "final_url": {"type": "string"},
                    "access_kind": {"type": "string", "minLength": 1},
                    "retrieved_at": {"type": "string", "pattern": iso},
                    "http_status": {"type": ["integer", "null"]},
                    "content_type": {"type": "string"},
                    "fetcher_build_id": {"type": "string"},
                    "status": {
                        "enum": [
                            "QUEUED",
                            "FETCHING",
                            "FETCHED",
                            "BLOCKED",
                            "UNREACHABLE",
                            "FAILED",
                            "NEEDS_RENDERER",
                        ]
                    },
                    "triage_assessment_id": sid("TRI"),
                    "retry_of": {"type": ["string", "null"]},
                    "post_isolation": {"type": "boolean"},
                },
            ),
        },
    )
    dump(
        "retrieval-artifact.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/retrieval-artifact.schema.json",
            **obj(
                ["schema_version", "retrieval_id", "artifact_id", "role"],
                {
                    "schema_version": {"const": VERSION},
                    "retrieval_id": sid("RET"),
                    "artifact_id": {"type": "string", "pattern": art},
                    "role": {
                        "enum": [
                            "RAW_RESPONSE",
                            "RESPONSE_HEADERS",
                            "RENDERED_DOM",
                            "SCREENSHOT",
                            "PROVIDER_JSON",
                            "PDF_BYTES",
                        ]
                    },
                    "parent_artifact_id": {"type": "string", "pattern": art},
                    "transform_build_id": {"type": "string"},
                },
            ),
        },
    )
    dump(
        "artifact.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/artifact.schema.json",
            **obj(
                [
                    "schema_version",
                    "artifact_id",
                    "media_type",
                    "byte_length",
                    "retention_policy",
                    "durability_status",
                    "created_at",
                ],
                {
                    "schema_version": {"const": VERSION},
                    "artifact_id": {"type": "string", "pattern": art},
                    "media_type": {"type": "string"},
                    "byte_length": {"type": "integer", "minimum": 0},
                    "retention_policy": {"type": "string"},
                    "durability_status": {"enum": ["EPHEMERAL", "LOCAL", "DURABLE"]},
                    "created_at": {"type": "string", "pattern": iso},
                    "parent_artifact_id": {"type": "string", "pattern": art},
                    "transform_build_id": {"type": "string"},
                },
            ),
        },
    )
    dump(
        "artifact-replica-status.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/artifact-replica-status.schema.json",
            **obj(
                [
                    "schema_version",
                    "artifact_id",
                    "backend_id",
                    "integrity_status",
                    "availability",
                ],
                {
                    "schema_version": {"const": VERSION},
                    "artifact_id": {"type": "string", "pattern": art},
                    "backend_id": {"type": "string"},
                    "storage_uri": {"type": "string"},
                    "integrity_status": {"enum": ["OK", "MISSING", "CORRUPT"]},
                    "last_verified_at": {"type": "string", "pattern": iso},
                    "availability": {
                        "enum": ["AVAILABLE", "MISSING", "CORRUPT", "RETENTION_DELETED"]
                    },
                },
            ),
        },
    )
    dump(
        "triage-assessment.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/triage-assessment.schema.json",
            **obj(
                [
                    "schema_version",
                    "assessment_id",
                    "retrieval_id",
                    "tier",
                    "access_legitimacy",
                    "assessor_build_id",
                    "policy_hash",
                    "assessed_at",
                ],
                {
                    "schema_version": {"const": VERSION},
                    "assessment_id": sid("TRI"),
                    "retrieval_id": sid("RET"),
                    "tier": {"enum": ["A", "B", "C", "D"]},
                    "access_legitimacy": {
                        "enum": [
                            "UNKNOWN",
                            "AUTHORIZED",
                            "UNAUTHORIZED_REPRINT",
                            "PUBLIC",
                            "RESTRICTED",
                        ]
                    },
                    "suspected_reprint": {"type": "boolean"},
                    "allowed_uses": {"type": "array", "items": {"type": "string"}},
                    "selection_decision": {"type": "string"},
                    "decision_reason": {"type": "string"},
                    "assessor_build_id": {"type": "string"},
                    "policy_hash": {"type": "string", "pattern": sha},
                    "assessed_at": {"type": "string", "pattern": iso},
                },
            ),
        },
    )
    dump(
        "origin-assessment.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/origin-assessment.schema.json",
            **obj(
                [
                    "schema_version",
                    "assessment_id",
                    "source_a",
                    "source_b",
                    "relation",
                    "confidence",
                    "basis",
                    "assessor_build_id",
                    "policy_hash",
                    "assessed_at",
                ],
                {
                    "schema_version": {"const": VERSION},
                    "assessment_id": sid("ORI"),
                    "source_a": sid("SRC"),
                    "source_b": sid("SRC"),
                    "relation": {
                        "enum": ["SAME_ORIGIN", "LIKELY_SAME_ORIGIN", "INDEPENDENT", "UNKNOWN"]
                    },
                    "confidence": {"enum": ["LOW", "MEDIUM", "HIGH"]},
                    "basis": {"type": "array", "items": {"type": "string"}},
                    "assessor_build_id": {"type": "string"},
                    "policy_hash": {"type": "string", "pattern": sha},
                    "assessed_at": {"type": "string", "pattern": iso},
                },
            ),
        },
    )
    dump(
        "parse-run.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/parse-run.schema.json",
            **obj(
                [
                    "schema_version",
                    "parse_run_id",
                    "input_artifact_id",
                    "parser_build_id",
                    "status",
                ],
                {
                    "schema_version": {"const": VERSION},
                    "parse_run_id": sid("PRUN"),
                    "input_artifact_id": {"type": "string", "pattern": art},
                    "parser_build_id": {"type": "string"},
                    "parameters": {"type": "object"},
                    "output_document_id": sid("DOC"),
                    "output_hash": {"type": "string", "pattern": sha},
                    "status": {"enum": ["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "INCONCLUSIVE"]},
                    "retry_of": {"type": ["string", "null"]},
                    "supersedes": {"type": ["string", "null"]},
                },
            ),
        },
    )
    dump(
        "parsed-document.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/parsed-document.schema.json",
            **obj(
                [
                    "schema_version",
                    "document_id",
                    "input_artifact_id",
                    "parser_build_id",
                    "structure_hash",
                ],
                {
                    "schema_version": {"const": VERSION},
                    "document_id": sid("DOC"),
                    "input_artifact_id": {"type": "string", "pattern": art},
                    "parser_build_id": {"type": "string"},
                    "title": {"type": "string"},
                    "language": {"type": "string"},
                    "structure_hash": {"type": "string", "pattern": sha},
                },
            ),
        },
    )
    dump(
        "segment.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/segment.schema.json",
            **obj(
                [
                    "schema_version",
                    "segment_id",
                    "document_id",
                    "ordinal",
                    "segment_type",
                    "normalized_text",
                    "normalized_text_hash",
                    "source_locator",
                ],
                {
                    "schema_version": {"const": VERSION},
                    "segment_id": sid("SEG"),
                    "document_id": sid("DOC"),
                    "parent_segment_id": {"type": ["string", "null"]},
                    "ordinal": {"type": "integer", "minimum": 0},
                    "segment_type": {"enum": ["document", "section", "paragraph", "sentence", "quote"]},
                    "normalized_text": {"type": "string"},
                    "normalized_text_hash": {"type": "string", "pattern": sha},
                    "source_locator": obj(
                        ["kind"],
                        {
                            "kind": {"enum": ["html", "pdf", "text"]},
                            "selector": {"type": "string"},
                            "start": {"type": "integer"},
                            "end": {"type": "integer"},
                            "page": {"type": "integer"},
                        },
                    ),
                },
            ),
        },
    )
    dump(
        "collection-snapshot.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/collection-snapshot.schema.json",
            **obj(
                [
                    "schema_version",
                    "snapshot_id",
                    "campaign_id",
                    "search_run_ids",
                    "hit_ids",
                    "retrieval_ids",
                    "artifact_ids",
                    "triage_assessment_ids",
                    "origin_assessment_ids",
                    "snapshot_hash",
                    "frozen_at",
                ],
                {
                    "schema_version": {"const": VERSION},
                    "snapshot_id": sid("SNP"),
                    "campaign_id": sid("CAM"),
                    "search_run_ids": {"type": "array", "items": sid("SRUN")},
                    "hit_ids": {"type": "array", "items": sid("HIT")},
                    "retrieval_ids": {"type": "array", "items": sid("RET")},
                    "artifact_ids": {"type": "array", "items": {"type": "string", "pattern": art}},
                    "triage_assessment_ids": {"type": "array", "items": sid("TRI")},
                    "origin_assessment_ids": {"type": "array", "items": sid("ORI")},
                    "snapshot_hash": {"type": "string", "pattern": sha},
                    "frozen_at": {"type": "string", "pattern": iso},
                    "supersedes": {"type": ["string", "null"]},
                    "status": {"enum": ["DRAFT", "FROZEN"]},
                },
            ),
        },
    )
    dump(
        "evidence-bundle.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/evidence-bundle.schema.json",
            **obj(
                [
                    "schema_version",
                    "bundle_id",
                    "request_id",
                    "collection_snapshot_ids",
                    "document_ids",
                    "segment_ids",
                    "selection_manifest",
                    "profile_id",
                    "policy_bundle_hash",
                    "bundle_hash",
                    "frozen_at",
                    "status",
                ],
                {
                    "schema_version": {"const": VERSION},
                    "bundle_id": sid("BND"),
                    "request_id": sid("REQ"),
                    "collection_snapshot_ids": {"type": "array", "items": sid("SNP")},
                    "document_ids": {"type": "array", "items": sid("DOC")},
                    "segment_ids": {"type": "array", "items": sid("SEG")},
                    "retrieval_ids": {"type": "array", "items": sid("RET")},
                    "artifact_ids": {"type": "array", "items": {"type": "string", "pattern": art}},
                    "triage_assessment_ids": {"type": "array", "items": sid("TRI")},
                    "origin_assessment_ids": {"type": "array", "items": sid("ORI")},
                    "selection_manifest": {"type": "object"},
                    "profile_id": {"type": "string"},
                    "policy_bundle_hash": {"type": "string", "pattern": sha},
                    "bundle_hash": {"type": "string", "pattern": sha},
                    "frozen_at": {"type": "string", "pattern": iso},
                    "supersedes": {"type": ["string", "null"]},
                    "status": {"enum": ["DRAFT", "FROZEN", "EXTRACTED", "EXPORTED", "SUPERSEDED"]},
                },
            ),
        },
    )
    dump(
        "extraction-run.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/extraction-run.schema.json",
            **obj(
                [
                    "schema_version",
                    "extraction_run_id",
                    "bundle_id",
                    "bundle_hash",
                    "extractor_build_id",
                    "trigger",
                    "input_manifest",
                    "execution_environment",
                    "status",
                ],
                {
                    "schema_version": {"const": VERSION},
                    "extraction_run_id": sid("ERUN"),
                    "bundle_id": sid("BND"),
                    "bundle_hash": {"type": "string", "pattern": sha},
                    "extractor_build_id": sid("BLD"),
                    "trigger": obj(
                        ["type"],
                        {
                            "type": {"enum": ["USER", "POLICY", "SCHEDULE", "RETRY", "MIGRATION"]},
                            "actor_id": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                    ),
                    "input_manifest": obj(
                        ["segment_ids", "allowed_context_artifact_ids"],
                        {
                            "segment_ids": {"type": "array", "items": sid("SEG")},
                            "system_prompt_hash": {"type": "string", "pattern": sha},
                            "user_prompt_hash": {"type": "string", "pattern": sha},
                            "tool_input_hashes": {"type": "array", "items": {"type": "string"}},
                            "allowed_context_artifact_ids": {
                                "type": "array",
                                "items": {"type": "string", "pattern": art},
                            },
                            "forbidden_context_policy_hash": {"type": "string", "pattern": sha},
                        },
                    ),
                    "execution_environment": obj(
                        ["executor_build_id", "context_isolation_mode", "model_snapshot"],
                        {
                            "executor_build_id": {"type": "string"},
                            "context_isolation_mode": {"enum": ["ALLOWLIST", "EMPTY"]},
                            "model_snapshot": {"type": "string"},
                        },
                    ),
                    "status": {"enum": ["QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "INCONCLUSIVE"]},
                    "retry_of": {"type": ["string", "null"]},
                },
            ),
        },
    )
    dump(
        "claim.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/claim.schema.json",
            **obj(
                [
                    "schema_version",
                    "claim_id",
                    "kind",
                    "status",
                    "grade",
                    "statement",
                    "profile_schema",
                    "profile_payload",
                    "support",
                ],
                {
                    "schema_version": {"const": VERSION},
                    "claim_id": sid("CLM"),
                    "kind": {"enum": ["ORIGINAL_FACT", "RECEPTION"]},
                    "status": {"enum": ["ACTIVE", "SUPERSEDED", "ARCHIVED"]},
                    "grade": {"enum": ["CONFIRMED", "SUPPORTED", "INFERRED", "UNKNOWN", "CONFLICTING"]},
                    "statement": {"type": "string", "minLength": 1},
                    "profile_schema": {"type": "string"},
                    "profile_payload": {"type": "object"},
                    "support": {
                        "type": "array",
                        "minItems": 1,
                        "items": obj(
                            ["retrieval_id", "artifact_id", "segment_id", "normalized_text_hash"],
                            {
                                "retrieval_id": sid("RET"),
                                "artifact_id": {"type": "string", "pattern": art},
                                "segment_id": sid("SEG"),
                                "normalized_text_hash": {"type": "string", "pattern": sha},
                            },
                        ),
                    },
                },
            ),
        },
    )
    dump(
        "extractor-build.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/extractor-build.schema.json",
            **obj(
                [
                    "schema_version",
                    "extractor_build_id",
                    "model",
                    "prompt_template_hash",
                    "parameters",
                    "profile_version",
                    "executor_build_id",
                    "tool_policy_hash",
                    "created_at",
                    "status",
                ],
                {
                    "schema_version": {"const": VERSION},
                    "extractor_build_id": sid("BLD"),
                    "model": {"type": "string"},
                    "prompt_template_hash": {"type": "string", "pattern": sha},
                    "parameters": {"type": "object"},
                    "profile_version": {"type": "string"},
                    "executor_build_id": {"type": "string"},
                    "tool_policy_hash": {"type": "string", "pattern": sha},
                    "repository_commit": {"type": "string"},
                    "created_at": {"type": "string", "pattern": iso},
                    "status": {"enum": ["UNQUALIFIED", "QUALIFIED", "INVALIDATED"]},
                },
            ),
        },
    )
    dump(
        "qualification.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/qualification.schema.json",
            **obj(
                [
                    "schema_version",
                    "qualification_run_id",
                    "extractor_build_id",
                    "fixture_suite_hash",
                    "run_a",
                    "run_b",
                    "adversarial_project_expectation",
                    "source_content_injection",
                    "reproducibility",
                    "result",
                    "qualified_at",
                ],
                {
                    "schema_version": {"const": VERSION},
                    "qualification_run_id": sid("QRUN"),
                    "extractor_build_id": sid("BLD"),
                    "fixture_suite_hash": {"type": "string", "pattern": sha},
                    "run_a": {"type": "string"},
                    "run_b": {"type": "string"},
                    "run_a_hash": {"type": "string", "pattern": sha},
                    "run_b_hash": {"type": "string", "pattern": sha},
                    "adversarial_project_expectation": {"enum": ["PASS", "FAIL"]},
                    "source_content_injection": {"enum": ["PASS", "FAIL"]},
                    "reproducibility": {"enum": ["PASS", "INCONCLUSIVE"]},
                    "result": {"enum": ["PASS", "FAIL", "INCONCLUSIVE"]},
                    "qualified_at": {"type": "string", "pattern": iso},
                },
            ),
        },
    )
    dump(
        "assurance-record.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/assurance-record.schema.json",
            **obj(
                ["schema_version", "subject_type", "subject_id", "level", "policy_hash", "created_at"],
                {
                    "schema_version": {"const": VERSION},
                    "subject_type": {"enum": ["BUILD", "BUNDLE", "EXPORT"]},
                    "subject_id": {"type": "string"},
                    "level": {
                        "enum": ["UNQUALIFIED", "BUILD_QUALIFIED", "BUNDLE_VERIFIED", "HUMAN_AUDITED"]
                    },
                    "policy_hash": {"type": "string", "pattern": sha},
                    "created_at": {"type": "string", "pattern": iso},
                },
            ),
        },
    )
    dump(
        "exports/xuanhuan-evidence-v1.schema.json",
        {
            "$schema": SCHEMA,
            "$id": f"{BASE}/exports/xuanhuan-evidence-v1.schema.json",
            **obj(
                [
                    "schema_version",
                    "export_id",
                    "export_hash",
                    "producer",
                    "origin_request",
                    "bundle",
                    "claims",
                    "scene_facts",
                    "policies",
                    "assurance",
                    "artifact_manifest",
                    "created_at",
                ],
                {
                    "schema_version": {"const": VERSION},
                    "export_id": sid("EXP"),
                    "export_hash": {"type": "string", "pattern": sha},
                    "producer": obj(
                        [
                            "repository_commit",
                            "collector_build_id",
                            "parser_build_id",
                            "extractor_build_id",
                        ],
                        {
                            "repository_commit": {"type": "string", "minLength": 7},
                            "collector_build_id": {"type": "string"},
                            "parser_build_id": {"type": "string"},
                            "extractor_build_id": sid("BLD"),
                        },
                    ),
                    "origin_request": {"type": "object"},
                    "bundle": obj(
                        ["bundle_id", "bundle_hash"],
                        {
                            "bundle_id": sid("BND"),
                            "bundle_hash": {"type": "string", "pattern": sha},
                        },
                    ),
                    "claims": {"type": "array", "items": {"type": "object"}},
                    "scene_facts": {"type": "object"},
                    "policies": {"type": "object"},
                    "assurance": obj(
                        ["level"],
                        {
                            "level": {
                                "enum": [
                                    "UNQUALIFIED",
                                    "BUILD_QUALIFIED",
                                    "BUNDLE_VERIFIED",
                                    "HUMAN_AUDITED",
                                ]
                            },
                            "qualification_run_id": sid("QRUN"),
                            "auditability": {
                                "enum": ["FULL", "DEGRADED", "LIMITED_BY_RETENTION_POLICY"]
                            },
                        },
                    ),
                    "artifact_manifest": {
                        "type": "array",
                        "items": obj(
                            ["artifact_id", "byte_length", "durability_status"],
                            {
                                "artifact_id": {"type": "string", "pattern": art},
                                "byte_length": {"type": "integer"},
                                "durability_status": {"enum": ["EPHEMERAL", "LOCAL", "DURABLE"]},
                                "availability": {"type": "string"},
                            },
                        ),
                    },
                    "created_at": {"type": "string", "pattern": iso},
                    "revocation": {"type": ["object", "null"]},
                },
            ),
        },
    )
    dump(
        "id-prefixes.json",
        {"schema_version": VERSION, "prefixes": idpat, "search_run_note": "SRUN- not RUN-"},
    )


if __name__ == "__main__":
    main()
