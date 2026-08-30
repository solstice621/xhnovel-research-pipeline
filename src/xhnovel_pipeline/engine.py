from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone
from typing import Any

from .access import is_snippet_kind, normalize_access_kind
from .bundle_ops import bundle_from_snapshot
from .catalog import Catalog
from .constants import (
    COLLECTOR_BUILD_ID,
    FETCHER_BUILD_ID,
    MOCK_EXECUTOR_BUILD_ID,
    PARSER_BUILD_ID,
    PLANNER_BUILD_ID,
    PROFILE_ID,
    SCHEMA_VERSION,
)
from .errors import ValidationError
from .extraction import MOCK_PROMPT, mock_extract
from .hashing import artifact_id_for, object_hash, sha256_bytes, sorted_ids
from .ids import derived_id
from .origin import near_duplicate_assessments
from .page_kind import looks_like_js_shell, looks_like_login_wall
from .parse import parse_artifact, text_hash
from .policies import policy_bundle_hash
from .qualification import build_source_hash, qualify_mock_build
from .schema import validate_schema
from .stop import campaign_report_payload, decide_campaign_stop, has_two_independent_secondary_sources
from .store import ArtifactStore
from .urls import canonicalize_url
from .validate import bundle_hash, validate_all, validate_collection

NOW = "2026-08-29T00:00:00Z"


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class FixtureFetcher:
    def __init__(self, mapping: dict[str, pathlib.Path]) -> None:
        self.mapping = {canonicalize_url(k) if "://" in k else k: pathlib.Path(v) for k, v in mapping.items()}
        # also index raw keys
        for k, v in mapping.items():
            self.mapping[k] = pathlib.Path(v)

    def fetch(self, url: str) -> tuple[bytes, str, int, str]:
        path = self.mapping.get(url) or self.mapping.get(canonicalize_url(url))
        if path is None:
            raise ValidationError("E-UNREACHABLE", f"no fixture for {url}")
        data = path.read_bytes()
        media = "text/html"
        if path.suffix == ".pdf":
            media = "application/pdf"
        elif path.suffix == ".json":
            media = "application/json"
        return data, media, 200, str(path)


class FakeSearchProvider:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.provider_id = response.get("provider_id", "fake-local")
        self.provider_build_id = response.get("provider_build_id", "fake-provider-v1")

    def search(self, query_text: str, parameters: dict[str, Any]) -> dict[str, Any]:
        pages = self.response.get("pages") or [self.response]
        page = int(parameters.get("page", 1))
        for block in pages:
            if int(block.get("page", 1)) == page:
                return block
        return {"page": page, "hits": []}


def make_build(
    *,
    prompt: str | None = None,
    repository_commit: str = "local-dev",
    source_tree_hash: str | None = None,
) -> dict[str, Any]:
    prompt = MOCK_PROMPT if prompt is None else prompt
    prompt_hash = object_hash({"prompt": prompt}, omit=())
    tool_hash = object_hash({"tools": []}, omit=())
    identity = {
        "repository_commit": repository_commit,
        "source_tree_hash": source_tree_hash or object_hash({"source": "local-dev"}, omit=()),
        "model": "mock-deterministic-v1",
        "prompt_template_hash": prompt_hash,
        "parameters": {"temperature": 0},
        "profile_version": PROFILE_ID,
        "executor_build_id": MOCK_EXECUTOR_BUILD_ID,
        "tool_policy_hash": tool_hash,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "extractor_build_id": derived_id("ExtractorBuild", identity),
        **identity,
        "created_at": NOW,
        "status": "UNQUALIFIED",
    }


def snapshot_hash_of(catalog: Catalog, snapshot: dict[str, Any]) -> str:
    return object_hash(
        {
            "campaign_id": snapshot["campaign_id"],
            "search_run_ids": sorted_ids(snapshot["search_run_ids"]),
            "hit_ids": sorted_ids(snapshot["hit_ids"]),
            "retrieval_ids": sorted_ids(snapshot["retrieval_ids"]),
            "artifact_ids": sorted_ids(snapshot["artifact_ids"]),
            "triage_assessment_ids": sorted_ids(snapshot["triage_assessment_ids"]),
            "origin_assessment_ids": sorted_ids(snapshot["origin_assessment_ids"]),
        }
    )


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _write_immutable(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(data)
        return
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValidationError("E-IMMUTABLE-OUTPUT", f"refusing to overwrite {path}")


def _write_legacy_alias_once(path: pathlib.Path, data: bytes) -> None:
    try:
        _write_immutable(path, data)
    except ValidationError as exc:
        if exc.code != "E-IMMUTABLE-OUTPUT":
            raise


def _persist_failed_campaign(work_dir: pathlib.Path, catalog: Catalog, campaign: dict[str, Any]) -> pathlib.Path:
    failure_id = derived_id(
        "SearchRun",
        {"campaign_id": campaign["campaign_id"], "search_run_ids": catalog.ids("SearchRun")},
    )
    failure_dir = work_dir / "failures" / failure_id
    _write_immutable(
        failure_dir / "catalog.json",
        _json_bytes({kind: values for kind, values in catalog.by_type.items() if values}),
    )
    _write_immutable(
        failure_dir / "campaign-report.json",
        _json_bytes(
            {
                "campaign_id": campaign["campaign_id"],
                "request_id": campaign["request_id"],
                "campaign_status": campaign["status"],
                "stop_reason": campaign["stop_reason"],
                "search_run_ids": catalog.ids("SearchRun"),
            }
        ),
    )
    return failure_dir


def git_commit(root: pathlib.Path) -> str:
    head = root / ".git" / "HEAD"
    if not head.exists():
        return "unknown-dev"
    text = head.read_text(encoding="utf-8").strip()
    if text.startswith("ref:"):
        ref = text.split(" ", 1)[1].strip()
        ref_path = root / ".git" / ref
        if ref_path.exists():
            return ref_path.read_text(encoding="utf-8").strip()
    return text[:40]


def put_artifact(catalog: Catalog, store: ArtifactStore, data: bytes, *, media_type: str) -> str:
    artifact_id = store.put(data)
    if not any(a["artifact_id"] == artifact_id for a in catalog.all("Artifact")):
        catalog.add(
            "Artifact",
            {
                "schema_version": SCHEMA_VERSION,
                "artifact_id": artifact_id,
                "media_type": media_type,
                "byte_length": len(data),
                "retention_policy": "retention-v1",
                "durability_status": "LOCAL",
                "created_at": NOW,
            },
        )
    return artifact_id


def run_local_slice(
    fixture_dir: pathlib.Path,
    work_dir: pathlib.Path,
    *,
    repo_root: pathlib.Path,
    provider: Any | None = None,
    fetcher: Any | None = None,
    collection_only: bool = False,
) -> dict[str, Any]:
    fixture_dir = pathlib.Path(fixture_dir)
    work_dir = pathlib.Path(work_dir)
    store = ArtifactStore(work_dir / "objects")
    catalog = Catalog()
    request = load_json(fixture_dir / "request.json")
    if provider is None:
        provider_json = load_json(fixture_dir / "provider.json")
        provider = FakeSearchProvider(provider_json)
    if fetcher is None:
        mapping_file = json.loads((fixture_dir / "fetch-map.json").read_text(encoding="utf-8"))
        mapping = {k: fixture_dir / v for k, v in mapping_file.items()}
        fetcher = FixtureFetcher(mapping)

    catalog.add("ResearchRequest", request)
    validate_schema("ResearchRequest", request)

    coverage_goals = ["two independent secondary sources"]
    stop_policy_hash = object_hash(
        {
            "coverage": "two fetched Tier B retrievals with explicit INDEPENDENT origin assessment",
            "stops": ["coverage_reached", "budget_exhausted", "provider_exhausted", "no_new_source", "failed"],
        },
        omit=(),
    )
    campaign_input = {
        "request_id": request["request_id"],
        "planner_build_id": PLANNER_BUILD_ID,
        "coverage_goals": coverage_goals,
        "budget": request["budget"],
        "stop_policy_hash": stop_policy_hash,
    }
    campaign = {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": derived_id("SearchCampaign", campaign_input),
        "request_id": request["request_id"],
        "planner_build_id": PLANNER_BUILD_ID,
        "coverage_goals": coverage_goals,
        "budget": request["budget"],
        "iterations": 1,
        "stop_policy_hash": stop_policy_hash,
        "status": "RUNNING",
        "created_at": NOW,
    }
    catalog.add("SearchCampaign", campaign)

    search_constraints = request.get("search_constraints") or {}
    query_text = search_constraints.get("query_text") or request["discovery_brief"]
    query_input = {
        "campaign_id": campaign["campaign_id"],
        "query_text": query_text,
        "query_role": "DISCOVER",
        "locale": "zh-CN",
    }
    query = {
        "schema_version": SCHEMA_VERSION,
        "query_id": derived_id("QuerySpec", query_input),
        "campaign_id": campaign["campaign_id"],
        "query_text": query_text,
        "query_role": "DISCOVER",
        "parent_query_id": None,
        "derived_from_hit_ids": [],
        "generated_by": search_constraints.get("generated_by", PLANNER_BUILD_ID),
        "rationale": search_constraints.get("query_rationale", "request discovery brief"),
        "locale": "zh-CN",
    }
    catalog.add("QuerySpec", query)

    raw_pages = []
    all_hits = []
    hit_extras: dict[str, dict[str, Any]] = {}
    max_queries = request["budget"]["max_queries"]
    provider_exhausted = False
    query_budget_hit = False
    fetch_budget_hit = False
    for page in range(1, max_queries + 1):
        page_retry_of = None
        block = None
        last_exc: ValidationError | None = None
        successful_attempt = 0
        for attempt in range(1, 4):
            try:
                block = provider.search(query["query_text"], {"page": page, "locale": "zh-CN"})
                successful_attempt = attempt
                break
            except ValidationError as exc:
                failed_raw = getattr(exc, "raw_response_bytes", None)
                failed_raw_id = (
                    put_artifact(catalog, store, failed_raw, media_type="application/json")
                    if isinstance(failed_raw, bytes)
                    else None
                )
                parameters = {
                    "page": page,
                    "locale": "zh-CN",
                    "attempt": attempt,
                    "error_code": exc.code,
                    "error_message": str(exc),
                }
                failed_run = {
                    "schema_version": SCHEMA_VERSION,
                    "search_run_id": derived_id(
                        "SearchRun",
                        {
                            "query_id": query["query_id"],
                            "provider_id": provider.provider_id,
                            "provider_build_id": provider.provider_build_id,
                            "parameters": parameters,
                            "raw_response_artifact_id": failed_raw_id,
                            "status": "FAILED",
                            "retry_of": page_retry_of,
                        },
                    ),
                    "query_id": query["query_id"],
                    "provider_id": provider.provider_id,
                    "provider_build_id": provider.provider_build_id,
                    "parameters": parameters,
                    "started_at": NOW,
                    "finished_at": NOW,
                    "result_set_hash": object_hash({"hits": []}, omit=()),
                    "status": "FAILED",
                    "retry_of": page_retry_of,
                }
                if failed_raw_id:
                    failed_run["raw_response_artifact_id"] = failed_raw_id
                catalog.add("SearchRun", failed_run)
                page_retry_of = failed_run["search_run_id"]
                last_exc = exc
                if exc.code != "E-RETRYABLE":
                    break
        if block is None:
            campaign["status"] = "FAILED"
            campaign["stop_reason"] = "failed"
            _persist_failed_campaign(work_dir, catalog, campaign)
            raise last_exc or ValidationError("E-PROVIDER", "search failed")
        exact_raw = block.get("_raw_response_bytes")
        serializable_block = {key: value for key, value in block.items() if key != "_raw_response_bytes"}
        raw_bytes = (
            exact_raw
            if isinstance(exact_raw, bytes)
            else json.dumps(serializable_block, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        raw_id = put_artifact(catalog, store, raw_bytes, media_type="application/json")
        raw_pages.append(raw_id)
        parameters = {"page": page, "locale": "zh-CN", "attempt": successful_attempt}
        hits_payload = [
            {
                "rank": hit["rank"],
                "url": hit["url"],
                "snippet": hit["snippet"],
                "hit_id": hit["hit_id"],
            }
            for hit in block.get("hits") or []
        ]
        result_set_hash = object_hash({"hits": hits_payload}, omit=())
        run = {
            "schema_version": SCHEMA_VERSION,
            "search_run_id": derived_id(
                "SearchRun",
                {
                    "query_id": query["query_id"],
                    "provider_id": provider.provider_id,
                    "provider_build_id": provider.provider_build_id,
                    "parameters": parameters,
                    "raw_response_artifact_id": raw_id,
                    "result_set_hash": result_set_hash,
                    "status": "SUCCEEDED",
                    "retry_of": page_retry_of,
                },
            ),
            "query_id": query["query_id"],
            "provider_id": provider.provider_id,
            "provider_build_id": provider.provider_build_id,
            "parameters": parameters,
            "started_at": NOW,
            "finished_at": NOW,
            "raw_response_artifact_id": raw_id,
            "result_set_hash": result_set_hash,
            "status": "SUCCEEDED",
            "retry_of": page_retry_of,
        }
        for hit in block.get("hits") or []:
            rec = {
                "schema_version": SCHEMA_VERSION,
                "hit_id": hit["hit_id"],
                "search_run_id": run["search_run_id"],
                "rank": hit["rank"],
                "url": hit["url"],
                "title": hit["title"],
                "snippet": hit["snippet"],
                "selection_status": hit["selection_status"],
                "selection_reason": hit.get("selection_reason", ""),
            }
            catalog.add("DiscoveryHit", rec)
            all_hits.append(rec)
            hit_extras[hit["hit_id"]] = {
                "platform_id": hit.get("platform_id") or hit["hit_id"],
                "tier": hit.get("tier", "B"),
                "access_kind": hit.get("access_kind", "full_page"),
                "access_legitimacy": hit.get("access_legitimacy", "PUBLIC"),
            }
        catalog.add("SearchRun", run)
        if not block.get("hits"):
            provider_exhausted = True
            break
    else:
        query_budget_hit = True

    # Provider JSON retrieval (lead-only, not a page)
    src_provider = {
        "schema_version": SCHEMA_VERSION,
        "source_id": derived_id(
            "Source",
            {"provider_id": provider.provider_id, "provider_build_id": provider.provider_build_id},
        ),
        "canonical_url": f"provider://{provider.provider_id}/search",
        "platform_id": provider.provider_id,
        "title": "provider response",
    }
    catalog.add("Source", src_provider)

    policy_hash = policy_bundle_hash(repo_root)
    retrievals_for_docs: dict[str, dict[str, Any]] = {}
    fetch_count = 0
    for hit in all_hits:
        # snippet retrieval always
        src_id = f"SRC-{hit['hit_id'][4:]}"
        if not any(s["source_id"] == src_id for s in catalog.all("Source")):
            catalog.add(
                "Source",
                {
                    "schema_version": SCHEMA_VERSION,
                    "source_id": src_id,
                    "canonical_url": canonicalize_url(hit["url"]),
                    "platform_id": hit_extras.get(hit["hit_id"], {}).get("platform_id") or hit["hit_id"],
                    "title": hit["title"],
                    "same_platform_as": None,
                },
            )
        snippet_bytes = hit["snippet"].encode("utf-8")
        snippet_aid = put_artifact(catalog, store, snippet_bytes, media_type="text/plain")
        snip_ret = {
            "schema_version": SCHEMA_VERSION,
            "retrieval_id": f"RET-{hit['hit_id'][4:]}-SNIP",
            "source_id": src_id,
            "discovery_hit_id": hit["hit_id"],
            "requested_url": hit["url"],
            "final_url": hit["url"],
            "access_kind": "search_snippet",
            "retrieved_at": NOW,
            "http_status": None,
            "content_type": "text/plain",
            "fetcher_build_id": FETCHER_BUILD_ID,
            "status": "FETCHED",
            "triage_assessment_id": f"TRI-{hit['hit_id'][4:]}-SNIP",
            "retry_of": None,
        }
        catalog.add("Retrieval", snip_ret)
        catalog.add(
            "RetrievalArtifact",
            {
                "schema_version": SCHEMA_VERSION,
                "retrieval_id": snip_ret["retrieval_id"],
                "artifact_id": snippet_aid,
                "role": "RAW_RESPONSE",
            },
        )
        catalog.add(
            "TriageAssessment",
            {
                "schema_version": SCHEMA_VERSION,
                "assessment_id": snip_ret["triage_assessment_id"],
                "retrieval_id": snip_ret["retrieval_id"],
                "tier": "D",
                "access_legitimacy": "PUBLIC",
                "suspected_reprint": False,
                "allowed_uses": ["discovery-lead"],
                "selection_decision": "lead-only",
                "decision_reason": "search snippet",
                "assessor_build_id": COLLECTOR_BUILD_ID,
                "policy_hash": policy_hash,
                "assessed_at": NOW,
            },
        )
        if hit["selection_status"] != "SELECTED":
            continue
        page_ret = None
        page_retry_of = None
        aid = None
        data = b""
        media = ""
        for attempt in range(1, 4):
            if fetch_count >= request["budget"]["max_fetches"]:
                fetch_budget_hit = True
                break
            fetch_count += 1
            try:
                fetched = fetcher.fetch(hit["url"])
                data, media, http_status = fetched[0], fetched[1], fetched[2]
                final_url = fetched[3] if len(fetched) > 3 else hit["url"]
                if ";" in media:
                    media = media.split(";", 1)[0].strip()
            except ValidationError as exc:
                if exc.code.startswith("E-SSRF"):
                    page_status = "BLOCKED"
                elif exc.code == "E-UNREACHABLE":
                    page_status = "UNREACHABLE"
                else:
                    page_status = "FAILED"
                failed_raw = getattr(exc, "raw_response_bytes", None)
                failed_artifact_id = (
                    put_artifact(catalog, store, failed_raw, media_type="application/octet-stream")
                    if isinstance(failed_raw, bytes)
                    else None
                )
                retrieval_input = {
                    "source_id": src_id,
                    "discovery_hit_id": hit["hit_id"],
                    "requested_url": hit["url"],
                    "attempt": attempt,
                    "status": page_status,
                    "error_code": exc.code,
                    "error_message": str(exc),
                    "artifact_id": failed_artifact_id,
                    "retry_of": page_retry_of,
                }
                retrieval_id = derived_id("Retrieval", retrieval_input)
                triage_id = derived_id("TriageAssessment", {"retrieval_id": retrieval_id, "status": page_status})
                page_ret = {
                    "schema_version": SCHEMA_VERSION,
                    "retrieval_id": retrieval_id,
                    "source_id": src_id,
                    "discovery_hit_id": hit["hit_id"],
                    "requested_url": hit["url"],
                    "final_url": hit["url"],
                    "access_kind": hit_extras.get(hit["hit_id"], {}).get("access_kind", "full_page"),
                    "retrieved_at": NOW,
                    "http_status": None,
                    "content_type": "",
                    "fetcher_build_id": FETCHER_BUILD_ID,
                    "status": page_status,
                    "triage_assessment_id": triage_id,
                    "retry_of": page_retry_of,
                }
                catalog.add("Retrieval", page_ret)
                if failed_artifact_id:
                    catalog.add(
                        "RetrievalArtifact",
                        {
                            "schema_version": SCHEMA_VERSION,
                            "retrieval_id": retrieval_id,
                            "artifact_id": failed_artifact_id,
                            "role": "RAW_RESPONSE",
                        },
                    )
                catalog.add(
                    "TriageAssessment",
                    {
                        "schema_version": SCHEMA_VERSION,
                        "assessment_id": triage_id,
                        "retrieval_id": retrieval_id,
                        "tier": "D",
                        "access_legitimacy": "UNKNOWN",
                        "suspected_reprint": False,
                        "allowed_uses": [],
                        "selection_decision": "failed" if page_status == "FAILED" else "blocked",
                        "decision_reason": f"attempt {attempt}: {exc}",
                        "assessor_build_id": COLLECTOR_BUILD_ID,
                        "policy_hash": policy_hash,
                        "assessed_at": NOW,
                    },
                )
                page_retry_of = retrieval_id
                if exc.code == "E-RETRYABLE" and attempt < 3:
                    continue
                break

            aid = put_artifact(catalog, store, data, media_type=media)
            page_status = "FETCHED"
            if looks_like_login_wall(data, http_status):
                page_status = "BLOCKED"
            elif looks_like_js_shell(data):
                page_status = "NEEDS_RENDERER"
            retrieval_input = {
                "source_id": src_id,
                "discovery_hit_id": hit["hit_id"],
                "requested_url": hit["url"],
                "final_url": final_url,
                "attempt": attempt,
                "artifact_id": aid,
                "status": page_status,
                "retry_of": page_retry_of,
            }
            retrieval_id = derived_id("Retrieval", retrieval_input)
            triage_id = derived_id("TriageAssessment", {"retrieval_id": retrieval_id, "status": page_status})
            page_ret = {
                "schema_version": SCHEMA_VERSION,
                "retrieval_id": retrieval_id,
                "source_id": src_id,
                "discovery_hit_id": hit["hit_id"],
                "requested_url": hit["url"],
                "final_url": final_url,
                "access_kind": hit_extras.get(hit["hit_id"], {}).get("access_kind", "full_page"),
                "retrieved_at": NOW,
                "http_status": http_status,
                "content_type": media,
                "fetcher_build_id": FETCHER_BUILD_ID,
                "status": page_status,
                "triage_assessment_id": triage_id,
                "retry_of": page_retry_of,
            }
            catalog.add("Retrieval", page_ret)
            catalog.add(
                "RetrievalArtifact",
                {
                    "schema_version": SCHEMA_VERSION,
                    "retrieval_id": page_ret["retrieval_id"],
                    "artifact_id": aid,
                    "role": "RAW_RESPONSE",
                },
            )
            catalog.add(
                "TriageAssessment",
                {
                    "schema_version": SCHEMA_VERSION,
                    "assessment_id": triage_id,
                    "retrieval_id": retrieval_id,
                    "tier": hit_extras.get(hit["hit_id"], {}).get("tier", "B")
                    if page_status == "FETCHED"
                    else "D",
                    "access_legitimacy": hit_extras.get(hit["hit_id"], {}).get("access_legitimacy", "PUBLIC")
                    if page_status == "FETCHED"
                    else "UNKNOWN",
                    "suspected_reprint": False,
                    "allowed_uses": ["event-facts"] if page_status == "FETCHED" else [],
                    "selection_decision": "selected" if page_status == "FETCHED" else page_status.lower(),
                    "decision_reason": f"attempt {attempt}: {hit.get('selection_reason', 'fixture')}"
                    if page_status == "FETCHED"
                    else f"attempt {attempt}: {page_status}",
                    "assessor_build_id": COLLECTOR_BUILD_ID,
                    "policy_hash": policy_hash,
                    "assessed_at": NOW,
                },
            )
            break

        if page_ret is None or page_ret["status"] != "FETCHED" or aid is None:
            if fetch_budget_hit:
                break
            continue
        doc_id = derived_id(
            "ParsedDocument",
            {"artifact_id": aid, "parser_build_id": PARSER_BUILD_ID, "media_type": media},
        )
        parse_input = {
            "input_artifact_id": aid,
            "parser_build_id": PARSER_BUILD_ID,
            "parameters": {"media_type": media},
            "retry_of": None,
            "supersedes": None,
        }
        try:
            parsed = parse_artifact(aid, data, media, doc_id)
        except ValidationError as exc:
            catalog.add(
                "ParseRun",
                {
                    "schema_version": SCHEMA_VERSION,
                    "parse_run_id": derived_id("ParseRun", {**parse_input, "status": "FAILED"}),
                    "input_artifact_id": aid,
                    "parser_build_id": PARSER_BUILD_ID,
                    "parameters": {"media_type": media},
                    "status": "FAILED",
                    "retry_of": None,
                    "supersedes": None,
                },
            )
            if exc.code not in {"E-PARSE"}:
                raise
            continue
        catalog.add("ParsedDocument", parsed["document"])
        output_hash = object_hash({"document": parsed["document"], "segments": parsed["segments"]})
        catalog.add(
            "ParseRun",
            {
                "schema_version": SCHEMA_VERSION,
                "parse_run_id": derived_id(
                    "ParseRun",
                    {**parse_input, "status": "SUCCEEDED", "output_hash": output_hash},
                ),
                "input_artifact_id": aid,
                "parser_build_id": PARSER_BUILD_ID,
                "parameters": {"media_type": media},
                "output_document_id": doc_id,
                "output_hash": output_hash,
                "status": "SUCCEEDED",
                "retry_of": None,
                "supersedes": None,
            },
        )
        for seg in parsed["segments"]:
            catalog.add("Segment", seg)
        retrievals_for_docs[doc_id] = {"retrieval_id": page_ret["retrieval_id"], "artifact_id": aid}

    # origin assessments from fixture
    origin_path = fixture_dir / "origin.json"
    origins = json.loads(origin_path.read_text(encoding="utf-8")) if origin_path.exists() else []
    for orig in origins:
        orig.setdefault("schema_version", SCHEMA_VERSION)
        orig.setdefault("policy_hash", policy_hash)
        orig.setdefault("assessed_at", NOW)
        orig.setdefault("assessor_build_id", COLLECTOR_BUILD_ID)
        catalog.add("OriginAssessment", orig)

    texts_by_source: dict[str, str] = {}
    for doc in catalog.all("ParsedDocument"):
        meta = retrievals_for_docs.get(doc["document_id"])
        if not meta:
            continue
        ret = catalog.get("Retrieval", meta["retrieval_id"])
        text = " ".join(
            s["normalized_text"] for s in catalog.all("Segment") if s["document_id"] == doc["document_id"]
        )
        texts_by_source[ret["source_id"]] = text
    for extra in near_duplicate_assessments(
        texts_by_source,
        policy_hash=policy_hash,
        assessor_build_id=COLLECTOR_BUILD_ID,
        assessed_at=NOW,
        schema_version=SCHEMA_VERSION,
        existing=catalog.all("OriginAssessment"),
    ):
        catalog.add("OriginAssessment", extra)

    selected_page_rets = [
        r
        for r in catalog.all("Retrieval")
        if normalize_access_kind(r["access_kind"]) != "search_snippet" and r["status"] == "FETCHED"
    ]
    coverage_reached = has_two_independent_secondary_sources(
        retrievals=selected_page_rets,
        triage_assessments=catalog.all("TriageAssessment"),
        origin_assessments=catalog.all("OriginAssessment"),
        sources=catalog.all("Source"),
    )
    status, stop_reason = decide_campaign_stop(
        coverage_reached=coverage_reached,
        fetch_budget_hit=fetch_budget_hit,
        provider_exhausted=provider_exhausted,
        query_budget_hit=query_budget_hit,
    )
    campaign["status"] = status
    campaign["stop_reason"] = stop_reason

    snapshot_members = {
        "campaign_id": campaign["campaign_id"],
        "search_run_ids": catalog.ids("SearchRun"),
        "hit_ids": catalog.ids("DiscoveryHit"),
        "retrieval_ids": catalog.ids("Retrieval"),
        "artifact_ids": catalog.ids("Artifact"),
        "triage_assessment_ids": catalog.ids("TriageAssessment"),
        "origin_assessment_ids": catalog.ids("OriginAssessment"),
    }
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": derived_id("CollectionSnapshot", snapshot_members),
        **snapshot_members,
        "snapshot_hash": "sha256:" + "0" * 64,
        "frozen_at": NOW,
        "supersedes": None,
        "status": "FROZEN",
    }
    snapshot["snapshot_hash"] = snapshot_hash_of(catalog, snapshot)
    catalog.add("CollectionSnapshot", snapshot)

    if collection_only:
        validate_collection(catalog, store)
        output_dir = work_dir / "collections" / snapshot["snapshot_id"]
        catalog_bytes = _json_bytes({kind: values for kind, values in catalog.by_type.items() if values})
        snapshot_bytes = _json_bytes(snapshot)
        collection_report = {
            "schema_version": SCHEMA_VERSION,
            "request_id": request["request_id"],
            "campaign_id": campaign["campaign_id"],
            "snapshot_id": snapshot["snapshot_id"],
            "campaign_status": campaign["status"],
            "stop_reason": campaign["stop_reason"],
            "search_run_count": len(catalog.all("SearchRun")),
            "hit_count": len(all_hits),
            "retrieval_count": len(catalog.all("Retrieval")),
            "artifact_count": len(catalog.all("Artifact")),
            "parsed_document_count": len(catalog.all("ParsedDocument")),
            "contains_claims": False,
        }
        report_bytes = _json_bytes(collection_report)
        _write_immutable(output_dir / "catalog.json", catalog_bytes)
        _write_immutable(output_dir / "snapshot.json", snapshot_bytes)
        _write_immutable(output_dir / "collection-report.json", report_bytes)
        _write_legacy_alias_once(work_dir / "collection-catalog.json", catalog_bytes)
        _write_legacy_alias_once(work_dir / "collection-snapshot.json", snapshot_bytes)
        _write_legacy_alias_once(work_dir / "collection-report.json", report_bytes)
        return {
            "snapshot": snapshot,
            "catalog": catalog,
            "store": store,
            "collection_report": collection_report,
            "work_dir": output_dir,
            "root_work_dir": work_dir,
        }

    selection_manifest = {
        "selected_hit_ids": [h["hit_id"] for h in all_hits if h["selection_status"] == "SELECTED"],
        "rejected_hit_ids": [h["hit_id"] for h in all_hits if h["selection_status"] != "SELECTED"],
    }
    bundle_input = {
        "request_id": request["request_id"],
        "snapshot_id": snapshot["snapshot_id"],
        "document_ids": catalog.ids("ParsedDocument"),
        "segment_ids": catalog.ids("Segment"),
        "retrieval_ids": [r["retrieval_id"] for r in selected_page_rets],
        "artifact_ids": [a["artifact_id"] for a in catalog.all("Artifact") if a["media_type"] != "application/json"],
        "triage_assessment_ids": catalog.ids("TriageAssessment"),
        "origin_assessment_ids": catalog.ids("OriginAssessment"),
        "selection_manifest": selection_manifest,
        "profile_id": PROFILE_ID,
        "policy_bundle_hash": policy_hash,
    }
    bundle = bundle_from_snapshot(
        catalog,
        request_id=bundle_input["request_id"],
        snapshot_id=bundle_input["snapshot_id"],
        document_ids=bundle_input["document_ids"],
        segment_ids=bundle_input["segment_ids"],
        retrieval_ids=bundle_input["retrieval_ids"],
        artifact_ids=bundle_input["artifact_ids"],
        triage_assessment_ids=bundle_input["triage_assessment_ids"],
        origin_assessment_ids=bundle_input["origin_assessment_ids"],
        selection_manifest=selection_manifest,
        profile_id=PROFILE_ID,
        policy_bundle_hash=policy_hash,
        frozen_at=NOW,
        schema_version=SCHEMA_VERSION,
    )
    catalog.add("EvidenceBundle", bundle)

    build = make_build(
        repository_commit=git_commit(repo_root),
        source_tree_hash=build_source_hash(repo_root),
    )
    catalog.add("ExtractorBuild", build)

    prompt_hash = build["prompt_template_hash"]
    extraction_input = {
        "bundle_id": bundle["bundle_id"],
        "bundle_hash": bundle["bundle_hash"],
        "extractor_build_id": build["extractor_build_id"],
        "trigger": {"type": "USER", "actor_id": "fixture", "reason": "local-slice"},
        "input_manifest": {
            "segment_ids": catalog.ids("Segment"),
            "system_prompt_hash": prompt_hash,
            "user_prompt_hash": prompt_hash,
            "tool_input_hashes": [],
            "allowed_context_artifact_ids": list(bundle["artifact_ids"]),
            "forbidden_context_policy_hash": policy_hash,
        },
        "execution_environment": {
            "executor_build_id": MOCK_EXECUTOR_BUILD_ID,
            "context_isolation_mode": "ALLOWLIST",
            "model_snapshot": "mock-deterministic-v1",
            "parameters": build["parameters"],
            "tool_policy_hash": build["tool_policy_hash"],
        },
        "retry_of": None,
    }
    extraction_run_id = derived_id("ExtractionRun", extraction_input)
    segments = catalog.all("Segment")
    claims = mock_extract(
        segments,
        retrievals_for_docs,
        extraction_run_id=extraction_run_id,
        project_context=load_json(repo_root / "fixtures/positive/minimal-local/run-a.json"),
    )
    injected = mock_extract(
        segments,
        retrievals_for_docs,
        extraction_run_id=extraction_run_id,
        project_context=load_json(repo_root / "fixtures/positive/minimal-local/run-b.json"),
    )
    if claims != injected:
        raise ValidationError("E-ADV-FAIL", "project injection changed claims")
    qrun = qualify_mock_build(
        repo_root,
        qualified_at=NOW,
        build=build,
    )
    for claim in claims:
        catalog.add("Claim", claim)

    run = {
        "schema_version": SCHEMA_VERSION,
        "extraction_run_id": extraction_run_id,
        **extraction_input,
        "status": "SUCCEEDED",
    }
    catalog.add("ExtractionRun", run)
    live_claims = [c for c in catalog.all("Claim") if c["status"] == "ACTIVE"]
    report = "CLAIMS_PRODUCED" if live_claims else "NO_QUALIFYING_CASE_FOUND"
    if qrun["result"] == "PASS":
        build["status"] = "QUALIFIED"
        assurance_level = "BUILD_QUALIFIED"
        catalog.add(
            "AssuranceRecord",
            {
                "schema_version": SCHEMA_VERSION,
                "subject_type": "BUILD",
                "subject_id": build["extractor_build_id"],
                "level": "BUILD_QUALIFIED",
                "qualification_run_id": qrun["qualification_run_id"],
                "policy_hash": policy_hash,
                "created_at": NOW,
            },
        )
    else:
        build["status"] = "UNQUALIFIED"
        assurance_level = "UNQUALIFIED"
    catalog.add("QualificationRun", qrun)
    scene_facts = {
        "claims": [{"claim_id": c["claim_id"], "statement": c["statement"], "grade": c["grade"]} for c in live_claims],
        "campaign_report": report,
    }
    export = {
        "schema_version": SCHEMA_VERSION,
        "producer": {
            "repository_commit": git_commit(repo_root),
            "collector_build_id": COLLECTOR_BUILD_ID,
            "parser_build_id": PARSER_BUILD_ID,
            "extractor_build_id": build["extractor_build_id"],
        },
        "origin_request": request,
        "bundle": {"bundle_id": bundle["bundle_id"], "bundle_hash": bundle["bundle_hash"]},
        "claims": live_claims,
        "scene_facts": scene_facts,
        "policies": {"policy_bundle_hash": policy_hash},
        "assurance": {
            "level": assurance_level,
            "qualification_run_id": qrun["qualification_run_id"],
            "auditability": "FULL",
        },
        "artifact_manifest": [
            {
                "artifact_id": a["artifact_id"],
                "byte_length": a["byte_length"],
                "durability_status": a["durability_status"],
                "availability": "AVAILABLE",
            }
            for a in catalog.all("Artifact")
        ],
        "created_at": NOW,
        "revocation": None,
    }
    export["export_id"] = derived_id("EvidenceExport", export)
    export["export_hash"] = "sha256:" + "0" * 64
    export["export_hash"] = object_hash(export, omit=("export_hash",))
    catalog.add("EvidenceExport", export)

    validate_all(catalog, store)

    output_dir = work_dir / "runs" / export["export_id"]
    export_bytes = _json_bytes(export)
    catalog_bytes = _json_bytes({k: v for k, v in catalog.by_type.items() if v})
    _write_immutable(output_dir / "export.json", export_bytes)
    _write_immutable(output_dir / "catalog.json", catalog_bytes)
    page_retrievals = [
        {
            "id": r["retrieval_id"],
            "status": r["status"],
            "http_status": r.get("http_status"),
            "final_url": r.get("final_url"),
            "retry_of": r.get("retry_of"),
        }
        for r in catalog.all("Retrieval")
        if normalize_access_kind(r["access_kind"]) != "search_snippet"
    ]
    report_bytes = _json_bytes(
        campaign_report_payload(
            request=request,
            query=query,
            campaign=campaign,
            hits=all_hits,
            page_retrievals=page_retrievals,
            export=export,
            live_claim_count=len(live_claims),
            report=report,
        )
    )
    _write_immutable(output_dir / "campaign-report.json", report_bytes)
    _write_legacy_alias_once(work_dir / "export.json", export_bytes)
    _write_legacy_alias_once(work_dir / "catalog.json", catalog_bytes)
    _write_legacy_alias_once(work_dir / "campaign-report.json", report_bytes)
    return {
        "export": export,
        "catalog": catalog,
        "store": store,
        "work_dir": output_dir,
        "root_work_dir": work_dir,
    }
