from __future__ import annotations

import json
import pathlib
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from .errors import SchemaError
from .paths import repo_root

SCHEMA_BY_TYPE = {
    "ResearchRequest": "research-request.schema.json",
    "Source": "source.schema.json",
    "Retrieval": "retrieval.schema.json",
    "RetrievalArtifact": "retrieval-artifact.schema.json",
    "Artifact": "artifact.schema.json",
    "TriageAssessment": "triage-assessment.schema.json",
    "CollectionDecision": "collection-decision.schema.json",
    "CollectionReview": "collection-review.schema.json",
    "NovelWork": "novel-work.schema.json",
    "NovelChapter": "novel-chapter.schema.json",
    "NovelIngestionRun": "novel-ingestion-run.schema.json",
    "NovelRankingRun": "novel-ranking-run.schema.json",
    "NovelSourceResolution": "novel-source-resolution.schema.json",
    "ParseRun": "parse-run.schema.json",
    "ParsedDocument": "parsed-document.schema.json",
    "Segment": "segment.schema.json",
    "CollectionSnapshot": "collection-snapshot.schema.json",
    "EvidenceBundle": "evidence-bundle.schema.json",
    "SceneWindow": "scene-window.schema.json",
    "SceneScoutRun": "scene-scout-run.schema.json",
    "SceneMergeRun": "scene-merge-run.schema.json",
    "SceneCandidate": "scene-candidate.schema.json",
    "ModelAttempt": "model-attempt.schema.json",
    "ExtractorBuild": "extractor-build.schema.json",
    "EvidenceExport": "exports/xuanhuan-evidence-v1.schema.json",
    # Phase 0 records are standalone contracts. They deliberately do not enter
    # Catalog.ID_FIELDS or the core EvidenceBundle closure.
    "ExplorationBrief": "exploration-brief.schema.json",
    "ResearchLead": "research-lead.schema.json",
    "HandoffBuildRequest": "handoff-build-request.schema.json",
    "SourceDeclaration": "source-declaration.schema.json",
    "EvidenceHandoff": "evidence-handoff.schema.json",
    "HandoffAttemptEvent": "handoff-attempt-event.schema.json",
    "EvidenceHandoffExecutionReceipt": "evidence-handoff-execution-receipt.schema.json",
}


def _schema_refs(value: Any):
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str):
            yield ref
        for child in value.values():
            yield from _schema_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _schema_refs(child)


def _registry(contracts: pathlib.Path) -> Registry:
    registry = Registry()
    for path in sorted(contracts.rglob("*.schema.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        uri = data.get("$id") or path.resolve().as_uri()
        registry = registry.with_resource(
            uri,
            Resource.from_contents(data, default_specification=DRAFT202012),
        )
    return registry


def validate_schema_resources(*, root: pathlib.Path | None = None) -> None:
    """Parse every distributed schema and resolve every reference from its own base URI."""

    root = root or repo_root()
    contracts = root / "contracts"
    registry = _registry(contracts)
    for path in sorted(contracts.rglob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        base_uri = schema.get("$id") or path.resolve().as_uri()
        resolver = registry.resolver(base_uri=base_uri)
        for ref in _schema_refs(schema):
            resolver.lookup(ref)


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
