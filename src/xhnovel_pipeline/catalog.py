from __future__ import annotations

from typing import Any, Iterable

from .errors import ValidationError

ID_FIELDS = {
    "ResearchRequest": "request_id",
    "Source": "source_id",
    "Retrieval": "retrieval_id",
    "TriageAssessment": "assessment_id",
    "CollectionDecision": "decision_id",
    "CollectionReview": "review_id",
    "NovelWork": "work_id",
    "NovelChapter": "chapter_id",
    "NovelIngestionRun": "ingestion_run_id",
    "NovelRankingRun": "ranking_run_id",
    "NovelSourceResolution": "resolution_id",
    "ParseRun": "parse_run_id",
    "ParsedDocument": "document_id",
    "Segment": "segment_id",
    "CollectionSnapshot": "snapshot_id",
    "EvidenceBundle": "bundle_id",
    "SceneWindow": "window_id",
    "SceneScoutRun": "scene_scout_run_id",
    "SceneMergeRun": "merge_run_id",
    "SceneCandidate": "scene_candidate_id",
    "ModelAttempt": "attempt_id",
    "ExtractorBuild": "extractor_build_id",
    "EvidenceExport": "export_id",
    "Artifact": "artifact_id",
    "RetrievalArtifact": None,
}


class Catalog:
    def __init__(self) -> None:
        self.by_type: dict[str, list[dict[str, Any]]] = {kind: [] for kind in ID_FIELDS}
        self.frozen_bundle_ids: set[str] = set()

    def add(self, kind: str, obj: dict[str, Any]) -> dict[str, Any]:
        if kind not in ID_FIELDS:
            raise ValidationError("E-CATALOG-KIND", f"unknown catalog record type {kind!r}")
        if not isinstance(obj, dict):
            raise ValidationError("E-CATALOG-RECORD", f"{kind} record must be an object")
        field = ID_FIELDS[kind]
        if field:
            if field not in obj:
                raise ValidationError("E-CATALOG-RECORD", f"{kind} record lacks {field}")
            ident = obj[field]
            for existing in self.by_type[kind]:
                if existing.get(field) == ident:
                    raise ValidationError("E-DUP-ID", f"duplicate {kind} {ident}")
        self.by_type[kind].append(obj)
        return obj

    def get(self, kind: str, ident: str) -> dict[str, Any]:
        if kind not in ID_FIELDS:
            raise ValidationError("E-CATALOG-KIND", f"unknown catalog record type {kind!r}")
        field = ID_FIELDS[kind]
        for obj in self.by_type.get(kind, []):
            if obj.get(field) == ident:
                return obj
        raise ValidationError("E-DANGLING-REF", f"missing {kind} {ident}")

    def all(self, kind: str) -> list[dict[str, Any]]:
        if kind not in ID_FIELDS:
            raise ValidationError("E-CATALOG-KIND", f"unknown catalog record type {kind!r}")
        return list(self.by_type[kind])

    def ids(self, kind: str) -> list[str]:
        if kind not in ID_FIELDS:
            raise ValidationError("E-CATALOG-KIND", f"unknown catalog record type {kind!r}")
        field = ID_FIELDS[kind]
        if field is None:
            raise ValidationError("E-CATALOG-RECORD", f"{kind} has no standalone identifier")
        return [obj[field] for obj in self.all(kind)]

    def extend(self, kind: str, objs: Iterable[dict[str, Any]]) -> None:
        for obj in objs:
            self.add(kind, obj)
