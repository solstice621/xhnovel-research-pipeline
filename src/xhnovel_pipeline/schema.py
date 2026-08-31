from __future__ import annotations

import json
import pathlib
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from .errors import SchemaError
from .paths import repo_root

PROFILE_SCHEMA_BY_ID = {
    "xuanhuan-gameplay-scene/v1": "profiles/xuanhuan-gameplay-scene-v1/profile.schema.json",
}

SCHEMA_BY_TYPE = {
    "ResearchRequest": "research-request.schema.json",
    "SearchCampaign": "search-campaign.schema.json",
    "QuerySpec": "query-spec.schema.json",
    "SearchRun": "search-run.schema.json",
    "DiscoveryHit": "discovery-hit.schema.json",
    "Source": "source.schema.json",
    "Retrieval": "retrieval.schema.json",
    "RetrievalArtifact": "retrieval-artifact.schema.json",
    "Artifact": "artifact.schema.json",
    "ArtifactReplicaStatus": "artifact-replica-status.schema.json",
    "TriageAssessment": "triage-assessment.schema.json",
    "OriginAssessment": "origin-assessment.schema.json",
    "CollectionDecision": "collection-decision.schema.json",
    "CollectionReview": "collection-review.schema.json",
    "NovelWork": "novel-work.schema.json",
    "NovelChapter": "novel-chapter.schema.json",
    "NovelIngestionRun": "novel-ingestion-run.schema.json",
    "NovelRankingRun": "novel-ranking-run.schema.json",
    "NovelSourceResolution": "novel-source-resolution.schema.json",
    "PlotAnalysis": "plot-analysis.schema.json",
    "ParseRun": "parse-run.schema.json",
    "ParsedDocument": "parsed-document.schema.json",
    "Segment": "segment.schema.json",
    "CollectionSnapshot": "collection-snapshot.schema.json",
    "EvidenceBundle": "evidence-bundle.schema.json",
    "ExtractionRun": "extraction-run.schema.json",
    "Claim": "claim.schema.json",
    "ExtractorBuild": "extractor-build.schema.json",
    "QualificationRun": "qualification.schema.json",
    "AssuranceRecord": "assurance-record.schema.json",
    "EvidenceExport": "exports/xuanhuan-evidence-v1.schema.json",
}


def _registry(contracts: pathlib.Path) -> Registry:
    registry = Registry()
    for path in sorted(contracts.rglob("*.schema.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        uri = data.get("$id") or path.resolve().as_uri()
        registry = registry.with_resource(uri, Resource.from_contents(data, default_specification=DRAFT202012))
    return registry


def validate_schema(kind: str, obj: dict[str, Any], *, root: pathlib.Path | None = None) -> None:
    root = root or repo_root()
    rel = SCHEMA_BY_TYPE[kind]
    path = root / "contracts" / rel
    schema = json.loads(path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, registry=_registry(root / "contracts"))
    errors = sorted(validator.iter_errors(obj), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        raise SchemaError("E-SCHEMA", f"{kind}: {first.message} at {list(first.path)}")


def validate_profile_payload(profile_id: str, payload: dict[str, Any], *, root: pathlib.Path | None = None) -> None:
    root = root or repo_root()
    rel = PROFILE_SCHEMA_BY_ID.get(profile_id)
    if rel is None:
        raise SchemaError("E-PROFILE-SCHEMA", f"unsupported profile schema {profile_id!r}")
    schema = json.loads((root / rel).read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        raise SchemaError("E-PROFILE-SCHEMA", f"{profile_id}: {first.message} at {list(first.path)}")
