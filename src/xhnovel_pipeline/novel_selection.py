from __future__ import annotations

import copy
import json
import pathlib
from typing import Any

from .canonical import canonical_dumps
from .catalog import Catalog
from .constants import SCHEMA_VERSION
from .errors import ValidationError
from .hashing import object_hash
from .ids import derived_id
from .ranking import normalize_work_title
from .schema import validate_schema
from .store import ArtifactStore

RESOLVER_BUILD_ID = "declared-source-catalog-v1"
SOURCE_ENTRY_KEYS = {
    "scene_scout",
    "candidate_id",
    "candidate_titles",
    "source",
    "evidence",
    "rights",
    "source_quality",
    "limits",
    "strict_order",
    "request",
}
RESOLVED_SPEC_KEYS = {
    "scene_scout",
    "source",
    "evidence",
    "rights",
    "source_quality",
    "limits",
    "strict_order",
    "request",
}


def _artifact_record(artifact_id: str, data: bytes, *, created_at: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "media_type": "application/json",
        "byte_length": len(data),
        "retention_policy": "retention-v1",
        "durability_status": "LOCAL",
        "created_at": created_at,
    }


def _put_json_artifact(
    catalog: Catalog,
    store: ArtifactStore,
    value: Any,
    *,
    created_at: str,
) -> tuple[str, bytes]:
    data = canonical_dumps(value)
    artifact_id = store.put(data)
    if not any(item["artifact_id"] == artifact_id for item in catalog.all("Artifact")):
        catalog.add("Artifact", _artifact_record(artifact_id, data, created_at=created_at))
    return artifact_id, data


def validated_source_catalog_input(
    source_catalog: list[dict[str, Any]], defaults: dict[str, Any] | None
) -> dict[str, Any]:
    if not isinstance(source_catalog, list) or not source_catalog:
        raise ValidationError("E-NOVEL-SOURCE-CATALOG", "source_catalog must be a non-empty array")
    normalized_entries: list[dict[str, Any]] = []
    for index, raw_entry in enumerate(source_catalog):
        if not isinstance(raw_entry, dict):
            raise ValidationError("E-NOVEL-SOURCE-CATALOG", f"source_catalog[{index}] must be an object")
        unknown = set(raw_entry) - SOURCE_ENTRY_KEYS
        if unknown:
            raise ValidationError(
                "E-NOVEL-SOURCE-CATALOG",
                f"source_catalog[{index}] has unknown fields: {sorted(unknown)}",
            )
        entry = copy.deepcopy(raw_entry)
        candidate_id = entry.get("candidate_id")
        titles = entry.get("candidate_titles")
        if candidate_id is None and not titles:
            raise ValidationError(
                "E-NOVEL-SOURCE-CATALOG",
                f"source_catalog[{index}] needs candidate_id or candidate_titles",
            )
        if candidate_id is not None and (not isinstance(candidate_id, str) or not candidate_id):
            raise ValidationError("E-NOVEL-SOURCE-CATALOG", f"invalid candidate_id at index {index}")
        if titles is not None and (
            not isinstance(titles, list)
            or not titles
            or any(not isinstance(title, str) or not normalize_work_title(title) for title in titles)
        ):
            raise ValidationError("E-NOVEL-SOURCE-CATALOG", f"invalid candidate_titles at index {index}")
        if not isinstance(entry.get("source"), dict):
            raise ValidationError("E-NOVEL-SOURCE-CATALOG", f"missing source at index {index}")
        normalized_entries.append(entry)
    if defaults is None:
        normalized_defaults: Any = {}
    else:
        normalized_defaults = copy.deepcopy(defaults)
    if not isinstance(normalized_defaults, dict) or set(normalized_defaults) - (
        RESOLVED_SPEC_KEYS - {"source"}
    ):
        raise ValidationError("E-NOVEL-SOURCE-CATALOG", "defaults contain unsupported fields")
    return {"source_catalog": normalized_entries, "defaults": normalized_defaults}


def _entry_match(candidate: dict[str, Any], entry: dict[str, Any]) -> tuple[str, str] | None:
    candidate_id = entry.get("candidate_id")
    if candidate_id is not None:
        if candidate_id == candidate["candidate_id"]:
            return "CANDIDATE_ID", candidate_id
        return None
    candidate_names = {
        normalize_work_title(candidate["title"]).casefold(),
        candidate["canonical_title"].casefold(),
        *(normalize_work_title(alias).casefold() for alias in candidate.get("aliases") or []),
    }
    for title in entry.get("candidate_titles") or []:
        if normalize_work_title(title).casefold() in candidate_names:
            return "NORMALIZED_TITLE", title
    return None


def _select(
    ranking: dict[str, Any], catalog_input: dict[str, Any]
) -> tuple[dict[str, Any], int, dict[str, Any], str, str]:
    entries = catalog_input["source_catalog"]
    for candidate in sorted(ranking["candidates"], key=lambda item: (item["rank"], item["candidate_id"])):
        matches = []
        for index, entry in enumerate(entries):
            match = _entry_match(candidate, entry)
            if match is not None:
                matches.append((index, entry, *match))
        if len(matches) > 1:
            raise ValidationError(
                "E-NOVEL-SOURCE-AMBIGUOUS",
                f"multiple source catalog entries match {candidate['candidate_id']}",
            )
        if matches:
            index, entry, method, selector = matches[0]
            return candidate, index, entry, method, selector
    raise ValidationError(
        "E-NOVEL-SOURCE-NOT-FOUND",
        "no ranked candidate has a declared source catalog entry",
    )


def _resolved_spec(
    candidate: dict[str, Any], entry: dict[str, Any], defaults: dict[str, Any]
) -> dict[str, Any]:
    resolved = copy.deepcopy(defaults)
    for key in RESOLVED_SPEC_KEYS:
        if key in entry:
            resolved[key] = copy.deepcopy(entry[key])
    source = resolved.get("source")
    if not isinstance(source, dict):
        raise ValidationError("E-NOVEL-SOURCE-CATALOG", "selected catalog entry has no source")
    source.setdefault("title", candidate["title"])
    source_kind = str(source.get("kind", "")).casefold()
    if source_kind not in {"txt", "epub", "directory", "chapter-directory", "site", "static-site"}:
        raise ValidationError("E-NOVEL-SOURCE-CATALOG", f"unsupported selected source kind {source_kind!r}")
    return resolved


def resolve_ranked_source(
    ranking: dict[str, Any],
    source_catalog: list[dict[str, Any]],
    catalog: Catalog,
    store: ArtifactStore,
    *,
    defaults: dict[str, Any] | None = None,
    created_at: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    catalog_input = validated_source_catalog_input(source_catalog, defaults)
    candidate, index, entry, method, selector = _select(ranking, catalog_input)
    resolved_spec = _resolved_spec(candidate, entry, catalog_input["defaults"])
    source = resolved_spec["source"]
    source_locator = source.get("path") or source.get("index_url")
    if not isinstance(source_locator, str) or not source_locator:
        raise ValidationError("E-NOVEL-SOURCE-CATALOG", "selected source needs path or index_url")
    source_catalog_artifact_id, _ = _put_json_artifact(
        catalog, store, catalog_input, created_at=created_at
    )
    source_spec_artifact_id, _ = _put_json_artifact(
        catalog, store, resolved_spec, created_at=created_at
    )
    base = {
        "schema_version": SCHEMA_VERSION,
        "ranking_run_id": ranking["ranking_run_id"],
        "candidate_id": candidate["candidate_id"],
        "candidate_rank": candidate["rank"],
        "candidate_title": candidate["title"],
        "candidate_score": candidate["score"],
        "match_method": method,
        "matched_selector": selector,
        "source_catalog_entry_index": index,
        "resolver_build_id": RESOLVER_BUILD_ID,
        "source_catalog_artifact_id": source_catalog_artifact_id,
        "source_spec_artifact_id": source_spec_artifact_id,
        "source_spec_hash": object_hash(resolved_spec, omit=()),
        "source_kind": str(source["kind"]).casefold(),
        "source_locator": source_locator,
        "status": "SELECTED",
        "created_at": created_at,
    }
    resolution = {**base, "resolution_id": derived_id("NovelSourceResolution", base)}
    validate_schema("NovelSourceResolution", resolution)
    catalog.add("NovelSourceResolution", resolution)
    validate_source_resolutions(catalog, store)
    return resolution, resolved_spec


def validate_source_resolutions(catalog: Catalog, store: ArtifactStore) -> None:
    for resolution in catalog.all("NovelSourceResolution"):
        validate_schema("NovelSourceResolution", resolution)
        ranking = catalog.get("NovelRankingRun", resolution["ranking_run_id"])
        candidate = next(
            (
                item
                for item in ranking["candidates"]
                if item["candidate_id"] == resolution["candidate_id"]
            ),
            None,
        )
        if candidate is None:
            raise ValidationError("E-NOVEL-SOURCE-BIND", "resolution candidate is absent from ranking")
        for artifact_field in ("source_catalog_artifact_id", "source_spec_artifact_id"):
            artifact_id = resolution[artifact_field]
            catalog.get("Artifact", artifact_id)
            store.verify(artifact_id)
        try:
            catalog_input = json.loads(store.get(resolution["source_catalog_artifact_id"]))
            resolved_spec = json.loads(store.get(resolution["source_spec_artifact_id"]))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("E-NOVEL-SOURCE-BIND", "resolution artifacts are not JSON") from exc
        catalog_input = validated_source_catalog_input(
            catalog_input.get("source_catalog"), catalog_input.get("defaults")
        )
        expected_candidate, index, entry, method, selector = _select(ranking, catalog_input)
        expected_spec = _resolved_spec(expected_candidate, entry, catalog_input["defaults"])
        if resolved_spec != expected_spec or canonical_dumps(resolved_spec) != store.get(
            resolution["source_spec_artifact_id"]
        ):
            raise ValidationError("E-NOVEL-SOURCE-BIND", "resolved source spec does not replay")
        expected_fields = {
            "candidate_id": expected_candidate["candidate_id"],
            "candidate_rank": expected_candidate["rank"],
            "candidate_title": expected_candidate["title"],
            "candidate_score": expected_candidate["score"],
            "match_method": method,
            "matched_selector": selector,
            "source_catalog_entry_index": index,
            "source_spec_hash": object_hash(expected_spec, omit=()),
            "source_kind": str(expected_spec["source"]["kind"]).casefold(),
            "source_locator": expected_spec["source"].get("path")
            or expected_spec["source"].get("index_url"),
        }
        if any(resolution[key] != value for key, value in expected_fields.items()):
            raise ValidationError("E-NOVEL-SOURCE-BIND", "resolution differs from deterministic replay")
        identity = {key: value for key, value in resolution.items() if key != "resolution_id"}
        if resolution["resolution_id"] != derived_id("NovelSourceResolution", identity):
            raise ValidationError("E-ID-BIND", f"{resolution['resolution_id']} does not match content")


def write_source_resolution(
    resolution: dict[str, Any], catalog: Catalog, work_dir: pathlib.Path
) -> pathlib.Path:
    output_dir = pathlib.Path(work_dir) / "source-resolutions" / resolution["resolution_id"]
    output_dir.mkdir(parents=True, exist_ok=True)
    payloads = {
        "source-resolution.json": (
            json.dumps(resolution, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8"),
        "catalog.json": (
            json.dumps(
                {kind: rows for kind, rows in catalog.by_type.items() if rows},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8"),
    }
    for name, data in payloads.items():
        path = output_dir / name
        try:
            with path.open("xb") as handle:
                handle.write(data)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValidationError("E-IMMUTABLE-OUTPUT", f"refusing to overwrite {path}")
    return output_dir
