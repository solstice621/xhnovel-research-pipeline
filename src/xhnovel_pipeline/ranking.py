from __future__ import annotations

import json
import pathlib
import re
from typing import Any, Iterable

import yaml

from .catalog import Catalog
from .constants import SCHEMA_VERSION
from .errors import ValidationError
from .file_io import write_immutable
from .hashing import object_hash
from .ids import derived_id
from .schema import validate_schema
from .store import ArtifactStore


def normalize_work_title(title: str) -> str:
    value = title.strip().replace("《", "").replace("》", "")
    value = re.sub(r"\s*[-—_]\s*(维基百科|Wikipedia).*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*[（(](小说|网络小说|作品)[）)]\s*$", "", value)
    return re.sub(r"\s+", " ", value).strip()


def default_fame_queries(genre: str) -> list[str]:
    return [f"最著名的{genre}小说", f"{genre}经典小说", f"{genre}小说代表作"]


def _put_artifact(
    catalog: Catalog,
    store: ArtifactStore,
    data: bytes,
    *,
    media_type: str,
    created_at: str,
) -> str:
    artifact_id = store.put(data)
    if not any(item["artifact_id"] == artifact_id for item in catalog.all("Artifact")):
        catalog.add(
            "Artifact",
            {
                "schema_version": SCHEMA_VERSION,
                "artifact_id": artifact_id,
                "media_type": media_type,
                "byte_length": len(data),
                "retention_policy": "retention-v1",
                "durability_status": "LOCAL",
                "created_at": created_at,
            },
        )
    return artifact_id


def _policy(repo_root: pathlib.Path) -> tuple[dict[str, Any], bytes]:
    path = repo_root / "policies" / "novel-fame-ranking-v1.yaml"
    raw = path.read_bytes()
    value = yaml.safe_load(raw)
    if not isinstance(value, dict):
        raise ValidationError("E-RANKING-POLICY", "ranking policy must be an object")
    return value, raw


def _raw_block(block: dict[str, Any]) -> bytes:
    serializable = {key: value for key, value in block.items() if key != "_raw_response_bytes"}
    try:
        return json.dumps(
            serializable,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "E-RANKING-REPLAY",
            "provider response cannot be serialized for replay",
        ) from exc


def _normalized_provider_hits(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValidationError("E-RANKING-REPLAY", "provider response hits must be an array")
    hits: list[dict[str, Any]] = []
    for expected_rank, raw_hit in enumerate(value, start=1):
        if not isinstance(raw_hit, dict):
            raise ValidationError("E-RANKING-REPLAY", "provider response hit must be an object")
        rank = raw_hit.get("rank")
        title = raw_hit.get("title")
        url = raw_hit.get("url")
        if (
            not isinstance(rank, int)
            or isinstance(rank, bool)
            or rank != expected_rank
            or not isinstance(title, str)
            or not title.strip()
            or not isinstance(url, str)
            or not url.strip()
        ):
            raise ValidationError(
                "E-RANKING-REPLAY",
                "provider response hits need contiguous ranks, titles and URLs",
            )
        hits.append({"rank": rank, "title": title, "url": url})
    return hits


def _replay_provider_hits(
    raw_response: bytes,
    *,
    provider_id: str,
    query: str,
) -> list[dict[str, Any]]:
    try:
        payload = json.loads(raw_response.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ValidationError(
            "E-RANKING-REPLAY",
            f"stored response for {provider_id} is not replayable JSON",
        ) from exc

    # Live Wikipedia OpenSearch stores the exact transport response. Recorded
    # providers and local fakes store their normalized response object instead.
    if provider_id == "wikipedia-opensearch" and isinstance(payload, list):
        if (
            len(payload) != 4
            or payload[0] != query
            or any(not isinstance(items, list) for items in payload[1:])
            or len({len(items) for items in payload[1:]}) != 1
            or any(not isinstance(item, str) for items in payload[1:] for item in items)
        ):
            raise ValidationError(
                "E-RANKING-REPLAY",
                "stored Wikipedia response does not match its ranking window",
            )
        titles, _, urls = payload[1:]
        return [
            {"rank": rank, "title": title, "url": urls[rank - 1]}
            for rank, title in enumerate(titles, start=1)
        ]

    if not isinstance(payload, dict):
        raise ValidationError(
            "E-RANKING-REPLAY",
            f"stored response for {provider_id} must be an object",
        )
    return _normalized_provider_hits(payload.get("hits"))


def _replayable_raw_block(
    provider: Any,
    block: dict[str, Any],
    *,
    query: str,
) -> tuple[bytes, list[dict[str, Any]]]:
    returned_hits = _normalized_provider_hits(block.get("hits"))
    exact = block.get("_raw_response_bytes")
    raw_payload = block.get("raw")

    if isinstance(exact, bytes):
        raw = exact
    elif provider.provider_id == "wikipedia-opensearch" and isinstance(raw_payload, list):
        raw = json.dumps(
            raw_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    else:
        # Providers without transport bytes (recordings and local fakes) use
        # their complete normalized return block as the replay boundary.
        raw = _raw_block(block)
    replayed_hits = _replay_provider_hits(
        raw,
        provider_id=provider.provider_id,
        query=query,
    )
    if replayed_hits != returned_hits:
        raise ValidationError(
            "E-RANKING-REPLAY",
            f"{provider.provider_id} normalized hits differ from its saved response",
        )
    return raw, replayed_hits


def rank_candidates(signals: Iterable[dict[str, Any]], *, rrf_k: int, score_scale: int) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    seen_signal_keys: set[tuple[str, str, str]] = set()
    for signal in signals:
        canonical_title = normalize_work_title(str(signal["title"]))
        if not canonical_title:
            continue
        signal_key = (str(signal["provider_id"]), str(signal["query"]), str(signal["url"]))
        if signal_key in seen_signal_keys:
            continue
        seen_signal_keys.add(signal_key)
        candidate = grouped.setdefault(
            canonical_title.casefold(),
            {
                "candidate_id": derived_id(
                    "NovelRankingRun",
                    {"candidate_title": canonical_title.casefold()},
                ).replace("NRNK-", "NCAN-", 1),
                "title": canonical_title,
                "canonical_title": canonical_title.casefold(),
                "aliases": [],
                "score": 0,
                "signals": [],
            },
        )
        raw_title = str(signal["title"]).strip()
        if raw_title != candidate["title"] and raw_title not in candidate["aliases"]:
            candidate["aliases"].append(raw_title)
        points = score_scale // (rrf_k + int(signal["global_rank"]))
        candidate["score"] += points
        candidate["signals"].append({**signal, "rrf_points": points})
    ordered = sorted(grouped.values(), key=lambda item: (-item["score"], item["canonical_title"]))
    for rank, candidate in enumerate(ordered, start=1):
        candidate["rank"] = rank
        candidate["aliases"].sort()
        candidate["signals"].sort(
            key=lambda item: (item["provider_id"], item["query"], item["global_rank"], item["url"])
        )
    return ordered


def run_fame_ranking(
    *,
    genre: str,
    providers: list[Any],
    store: ArtifactStore,
    catalog: Catalog,
    repo_root: pathlib.Path,
    created_at: str,
    queries: list[str] | None = None,
    pages_per_query: int = 1,
    limit: int = 10,
) -> dict[str, Any]:
    if not isinstance(genre, str) or not genre.strip():
        raise ValidationError("E-RANKING-INPUT", "genre is required")
    if not providers:
        raise ValidationError("E-RANKING-INPUT", "at least one provider is required")
    if (
        not isinstance(pages_per_query, int)
        or isinstance(pages_per_query, bool)
        or not 1 <= pages_per_query <= 100
        or not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= 100
    ):
        raise ValidationError("E-RANKING-INPUT", "invalid ranking search window")
    policy, policy_bytes = _policy(repo_root)
    rrf_k = int(policy["rrf_k"])
    score_scale = int(policy["score_scale"])
    if queries is None:
        raw_query_texts = default_fame_queries(genre)
    elif isinstance(queries, list):
        raw_query_texts = list(queries)
    else:
        raise ValidationError(
            "E-RANKING-INPUT",
            "ranking queries must be 1-100 unique non-empty strings",
        )
    if (
        not 1 <= len(raw_query_texts) <= 100
        or any(not isinstance(query, str) for query in raw_query_texts)
    ):
        raise ValidationError(
            "E-RANKING-INPUT",
            "ranking queries must be 1-100 unique non-empty strings",
        )
    query_texts = [query.strip() for query in raw_query_texts]
    if any(not query for query in query_texts) or len(set(query_texts)) != len(query_texts):
        raise ValidationError(
            "E-RANKING-INPUT",
            "ranking queries must be 1-100 unique non-empty strings",
        )
    provider_keys = [
        (str(provider.provider_id), str(provider.provider_build_id)) for provider in providers
    ]
    if len(set(provider_keys)) != len(provider_keys):
        raise ValidationError("E-RANKING-INPUT", "ranking providers must have unique build identities")
    policy_artifact_id = _put_artifact(
        catalog,
        store,
        policy_bytes,
        media_type="application/yaml",
        created_at=created_at,
    )
    windows: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    for provider in providers:
        for query in query_texts:
            for page in range(1, pages_per_query + 1):
                try:
                    block = provider.search(query, {"page": page, "limit": limit, "locale": "zh-CN"})
                except ValidationError as exc:
                    raw = getattr(exc, "raw_response_bytes", None)
                    raw_id = (
                        _put_artifact(catalog, store, raw, media_type="application/json", created_at=created_at)
                        if isinstance(raw, bytes)
                        else None
                    )
                    windows.append(
                        {
                            "provider_id": provider.provider_id,
                            "provider_build_id": provider.provider_build_id,
                            "query": query,
                            "page": page,
                            "status": "FAILED",
                            "raw_response_artifact_id": raw_id,
                            "hit_count": 0,
                            "error_code": exc.code,
                        }
                    )
                    break
                raw_response, hits = _replayable_raw_block(provider, block, query=query)
                if len(hits) > limit:
                    raise ValidationError(
                        "E-RANKING-WINDOW",
                        f"{provider.provider_id} returned {len(hits)} hits for declared limit {limit}",
                    )
                raw_id = _put_artifact(
                    catalog,
                    store,
                    raw_response,
                    media_type="application/json",
                    created_at=created_at,
                )
                windows.append(
                    {
                        "provider_id": provider.provider_id,
                        "provider_build_id": provider.provider_build_id,
                        "query": query,
                        "page": page,
                        "status": "SUCCEEDED",
                        "raw_response_artifact_id": raw_id,
                        "hit_count": len(hits),
                        "error_code": None,
                    }
                )
                for hit in hits:
                    rank = int(hit["rank"])
                    signals.append(
                        {
                            "provider_id": provider.provider_id,
                            "provider_build_id": provider.provider_build_id,
                            "query": query,
                            "page": page,
                            "rank": rank,
                            "global_rank": (page - 1) * limit + rank,
                            "title": str(hit["title"]),
                            "url": str(hit["url"]),
                            "raw_response_artifact_id": raw_id,
                        }
                    )
                if not hits:
                    break
    successful_windows = [window for window in windows if window["status"] == "SUCCEEDED"]
    if not successful_windows:
        raise ValidationError("E-RANKING-PROVIDER", "all ranking provider windows failed")
    candidates = rank_candidates(signals, rrf_k=rrf_k, score_scale=score_scale)
    base = {
        "schema_version": SCHEMA_VERSION,
        "genre": genre.strip(),
        "locale": "zh-CN",
        "queries": query_texts,
        "provider_windows": windows,
        "pages_per_query": pages_per_query,
        "results_per_page": limit,
        "policy_id": str(policy["id"]),
        "policy_artifact_id": policy_artifact_id,
        "rrf_k": rrf_k,
        "score_scale": score_scale,
        "candidates": candidates,
        "limitations": [
            "ranking covers only the declared providers, queries, pages and result limits",
            "candidate relevance requires collection review before selection",
            "the result is not an exhaustive ranking of the internet",
        ],
        "status": "SUCCEEDED" if all(window["status"] == "SUCCEEDED" for window in windows) else "PARTIAL",
        "created_at": created_at,
    }
    record = {
        **base,
        "ranking_run_id": derived_id("NovelRankingRun", base),
    }
    validate_schema("NovelRankingRun", record)
    catalog.add("NovelRankingRun", record)
    return record


def validate_fame_ranking(catalog: Catalog, store: ArtifactStore) -> None:
    for run in catalog.all("NovelRankingRun"):
        validate_schema("NovelRankingRun", run)
        store.verify(run["policy_artifact_id"])
        try:
            policy = yaml.safe_load(store.get(run["policy_artifact_id"]))
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            raise ValidationError("E-RANKING-POLICY", "stored ranking policy is invalid") from exc
        if not isinstance(policy, dict) or (
            policy.get("id") != run["policy_id"]
            or policy.get("method") != "reciprocal_rank_fusion"
            or int(policy.get("rrf_k", 0)) != run["rrf_k"]
            or int(policy.get("score_scale", 0)) != run["score_scale"]
        ):
            raise ValidationError("E-RANKING-POLICY", "ranking parameters differ from stored policy")
        window_keys: set[tuple[str, str, int]] = set()
        successful_windows: dict[tuple[str, str, int], dict[str, Any]] = {}
        replayed_signals: list[dict[str, Any]] = []
        for window in run["provider_windows"]:
            key = (window["provider_id"], window["query"], window["page"])
            if key in window_keys or window["query"] not in run["queries"]:
                raise ValidationError("E-RANKING-WINDOW", "duplicate or undeclared ranking window")
            window_keys.add(key)
            artifact_id = window.get("raw_response_artifact_id")
            if artifact_id:
                catalog.get("Artifact", artifact_id)
                store.verify(artifact_id)
            if window["status"] == "SUCCEEDED":
                if not artifact_id:
                    raise ValidationError("E-RANKING-WINDOW", "successful window has no raw response")
                if window["hit_count"] > run["results_per_page"]:
                    raise ValidationError(
                        "E-RANKING-WINDOW",
                        "ranking provider window exceeds its declared result limit",
                    )
                replayed_hits = _replay_provider_hits(
                    store.get(artifact_id),
                    provider_id=window["provider_id"],
                    query=window["query"],
                )
                if len(replayed_hits) != window["hit_count"]:
                    raise ValidationError(
                        "E-RANKING-REPLAY",
                        "ranking hit count differs from stored provider response",
                    )
                for hit in replayed_hits:
                    replayed_signals.append(
                        {
                            "provider_id": window["provider_id"],
                            "provider_build_id": window["provider_build_id"],
                            "query": window["query"],
                            "page": window["page"],
                            "rank": hit["rank"],
                            "global_rank": (window["page"] - 1) * run["results_per_page"]
                            + hit["rank"],
                            "title": hit["title"],
                            "url": hit["url"],
                            "raw_response_artifact_id": artifact_id,
                        }
                    )
                successful_windows[key] = window
        if not successful_windows:
            raise ValidationError("E-RANKING-WINDOW", "ranking has no successful provider window")
        provider_keys = {
            (window["provider_id"], window["provider_build_id"])
            for window in run["provider_windows"]
        }
        for provider_id, provider_build_id in provider_keys:
            for query in run["queries"]:
                query_windows = sorted(
                    (
                        window
                        for window in run["provider_windows"]
                        if window["provider_id"] == provider_id
                        and window["provider_build_id"] == provider_build_id
                        and window["query"] == query
                    ),
                    key=lambda item: item["page"],
                )
                pages = [window["page"] for window in query_windows]
                if not pages or pages != list(range(1, len(pages) + 1)):
                    raise ValidationError("E-RANKING-WINDOW", "ranking pages are incomplete or non-contiguous")
                if len(pages) > run["pages_per_query"]:
                    raise ValidationError("E-RANKING-WINDOW", "ranking exceeded its declared page window")
                last = query_windows[-1]
                stopped_early = len(pages) < run["pages_per_query"]
                if stopped_early and last["status"] == "SUCCEEDED" and last["hit_count"] > 0:
                    raise ValidationError("E-RANKING-WINDOW", "ranking stopped before its declared window")
        expected_status = (
            "SUCCEEDED"
            if all(window["status"] == "SUCCEEDED" for window in run["provider_windows"])
            else "PARTIAL"
        )
        if run["status"] != expected_status:
            raise ValidationError("E-RANKING-WINDOW", "ranking status differs from provider windows")
        if [item["rank"] for item in run["candidates"]] != list(
            range(1, len(run["candidates"]) + 1)
        ):
            raise ValidationError("E-RANKING-BIND", "candidate ranks are not contiguous and ordered")
        canonical_titles: set[str] = set()
        for candidate in run["candidates"]:
            canonical_title = normalize_work_title(candidate["title"]).casefold()
            if candidate["canonical_title"] != canonical_title or canonical_title in canonical_titles:
                raise ValidationError("E-RANKING-BIND", "candidate title normalization is inconsistent")
            canonical_titles.add(canonical_title)
            expected_candidate_id = derived_id(
                "NovelRankingRun", {"candidate_title": canonical_title}
            ).replace("NRNK-", "NCAN-", 1)
            if candidate["candidate_id"] != expected_candidate_id:
                raise ValidationError("E-RANKING-BIND", "candidate id does not match title")
            for signal in candidate["signals"]:
                key = (signal["provider_id"], signal["query"], signal["page"])
                window = successful_windows.get(key)
                if (
                    window is None
                    or signal["provider_build_id"] != window["provider_build_id"]
                    or signal["raw_response_artifact_id"] != window["raw_response_artifact_id"]
                    or signal["rank"] > window["hit_count"]
                    or signal["global_rank"]
                    != (signal["page"] - 1) * run["results_per_page"] + signal["rank"]
                ):
                    raise ValidationError("E-RANKING-WINDOW", "candidate signal does not match its window")
        expected = rank_candidates(
            replayed_signals,
            rrf_k=run["rrf_k"],
            score_scale=run["score_scale"],
        )
        actual_signals = sorted(
            (signal for candidate in run["candidates"] for signal in candidate["signals"]),
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        )
        expected_signals = sorted(
            (signal for candidate in expected for signal in candidate["signals"]),
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        )
        if actual_signals != expected_signals:
            raise ValidationError(
                "E-RANKING-REPLAY",
                f"{run['ranking_run_id']} signals differ from stored provider responses",
            )
        if expected != run["candidates"]:
            raise ValidationError(
                "E-RANKING-BIND",
                f"{run['ranking_run_id']} ranking does not replay from its signals",
            )
        identity = {key: value for key, value in run.items() if key != "ranking_run_id"}
        if run["ranking_run_id"] != derived_id("NovelRankingRun", identity):
            raise ValidationError("E-ID-BIND", f"{run['ranking_run_id']} does not match content")


def write_ranking_result(
    run: dict[str, Any], catalog: Catalog, work_dir: pathlib.Path
) -> pathlib.Path:
    output_dir = work_dir / "rankings" / run["ranking_run_id"]
    output_dir.mkdir(parents=True, exist_ok=True)
    payloads = {
        "ranking.json": _json_bytes(run),
        "catalog.json": _json_bytes({kind: rows for kind, rows in catalog.by_type.items() if rows}),
    }
    for name, data in payloads.items():
        path = output_dir / name
        write_immutable(path, data)
    return output_dir


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
