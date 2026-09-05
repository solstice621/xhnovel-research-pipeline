from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
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
        self._indexes: dict[str, dict[Any, dict[str, Any]]] | None = None

    @contextmanager
    def indexed(self):
        """Index a controlled read or construction batch, discarding it afterward.

        ``add`` maintains the index. Direct changes to record IDs or ``by_type``
        must happen outside this scope; ordinary mutable access stays unchanged.
        Nested scopes reuse the current index.
        """
        if self._indexes is not None:
            yield self
            return
        indexes = {kind: {} for kind, field in ID_FIELDS.items() if field}
        for kind, index in indexes.items():
            field = ID_FIELDS[kind]
            for obj in self.by_type[kind]:
                try:
                    index.setdefault(obj.get(field), obj)
                except TypeError:
                    # Leave malformed, unhashable IDs to the normal schema validator.
                    pass
        self._indexes = indexes
        try:
            yield self
        finally:
            self._indexes = None

    def _find(self, kind: str, ident: Any) -> dict[str, Any] | None:
        field = ID_FIELDS[kind]
        if self._indexes is not None and field:
            try:
                return self._indexes[kind].get(ident)
            except TypeError:
                pass
        return next((obj for obj in self.by_type[kind] if obj.get(field) == ident), None)

    @classmethod
    def from_mapping(
        cls,
        data: Any,
        *,
        array_error_code: str = "E-CATALOG-RECORD",
        array_label: str = "catalog {kind} value",
    ) -> "Catalog":
        """Build a strict Catalog from a decoded JSON object.

        Unknown kinds are rejected before their value shape is inspected, including
        the otherwise-easy-to-drop ``UnknownKind: []`` case.  Callers may retain a
        context-specific error code/label for known kinds whose values are not
        arrays, while sharing the load-bearing kind and record validation.
        """

        if not isinstance(data, dict):
            raise ValidationError("E-CATALOG-RECORD", "catalog root must be an object")
        catalog = cls()
        with catalog.indexed():
            for kind, records in data.items():
                if kind not in ID_FIELDS:
                    raise ValidationError(
                        "E-CATALOG-KIND",
                        f"unknown catalog record type {kind!r}",
                    )
                if not isinstance(records, list):
                    raise ValidationError(
                        array_error_code,
                        f"{array_label.format(kind=kind)} must be an array",
                    )
                for record in records:
                    catalog.add(kind, record)
        return catalog

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
            if self._find(kind, ident) is not None:
                raise ValidationError("E-DUP-ID", f"duplicate {kind} {ident}")
        self.by_type[kind].append(obj)
        if self._indexes is not None and field:
            try:
                self._indexes[kind][obj[field]] = obj
            except TypeError:
                pass
        return obj

    def get(self, kind: str, ident: str) -> dict[str, Any]:
        if kind not in ID_FIELDS:
            raise ValidationError("E-CATALOG-KIND", f"unknown catalog record type {kind!r}")
        obj = self._find(kind, ident)
        if obj is not None:
            return obj
        raise ValidationError("E-DANGLING-REF", f"missing {kind} {ident}")

    def contains(self, kind: str, ident: str) -> bool:
        if kind not in ID_FIELDS:
            raise ValidationError("E-CATALOG-KIND", f"unknown catalog record type {kind!r}")
        return self._find(kind, ident) is not None

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


def indexed_catalog(function):
    """Use scoped lookups in a validator whose first argument is its catalog."""
    @wraps(function)
    def indexed(catalog: Catalog, *args, **kwargs):
        with catalog.indexed():
            return function(catalog, *args, **kwargs)

    return indexed
