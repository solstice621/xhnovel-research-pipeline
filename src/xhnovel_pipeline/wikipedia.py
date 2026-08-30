from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, urlparse

from .errors import ValidationError
from .http_fetch import HttpFetcher
from .ids import derived_id

WIKI_OPENSEARCH = (
    "https://zh.wikipedia.org/w/api.php?action=opensearch&format=json&namespace=0"
    "&limit={limit}&offset={offset}&search={query}"
)
PROVIDER_ID = "wikipedia-opensearch"
PROVIDER_BUILD_ID = "wikipedia-opensearch-v1"


def _valid_result_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        parsed.port
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


class WikipediaOpenSearchProvider:
    def __init__(
        self,
        *,
        cassette: dict[str, Any] | None = None,
        timeout: float = 20.0,
        fetcher: HttpFetcher | None = None,
    ) -> None:
        self.cassette = cassette
        self.timeout = timeout
        self.fetcher = fetcher or HttpFetcher(timeout=timeout)
        self.provider_id = PROVIDER_ID
        self.provider_build_id = PROVIDER_BUILD_ID

    def search(self, query_text: str, parameters: dict[str, Any]) -> dict[str, Any]:
        page = int(parameters.get("page", 1))
        if self.cassette is not None:
            pages = self.cassette.get("pages") or [self.cassette]
            for block in pages:
                if int(block.get("page", 1)) == page:
                    return block
            return {"page": page, "hits": []}
        limit = int(parameters.get("limit", 10))
        offset = (page - 1) * limit
        url = WIKI_OPENSEARCH.format(limit=limit, offset=offset, query=quote(query_text))
        try:
            raw, _, _, _ = self.fetcher.fetch(url)
            payload = json.loads(raw.decode("utf-8"))
        except ValidationError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
            error = ValidationError("E-PROVIDER-SCHEMA", "wikipedia opensearch returned invalid JSON")
            error.raw_response_bytes = raw
            raise error from exc
        if (
            not isinstance(payload, list)
            or len(payload) != 4
            or not isinstance(payload[0], str)
            or any(not isinstance(items, list) for items in payload[1:])
            or len({len(items) for items in payload[1:]}) != 1
            or any(not isinstance(item, str) for items in payload[1:] for item in items)
            or any(not _valid_result_url(url) for url in payload[3])
        ):
            error = ValidationError("E-PROVIDER-SCHEMA", "wikipedia opensearch response shape changed")
            error.raw_response_bytes = raw
            raise error
        titles, snippets, urls = payload[1:]
        hits = []
        for i, title in enumerate(titles, start=1):
            hit_id = derived_id(
                "DiscoveryHit",
                {"query": query_text, "page": page, "rank": i, "url": urls[i - 1]},
            )
            hits.append(
                {
                    "hit_id": hit_id,
                    "rank": i,
                    "url": urls[i - 1] if i - 1 < len(urls) else "",
                    "title": title,
                    "snippet": snippets[i - 1] if i - 1 < len(snippets) else "",
                    "selection_status": "SELECTED" if i <= int(parameters.get("select_first", 2)) else "REJECTED",
                    "selection_reason": "wikipedia encyclopedia" if i <= int(parameters.get("select_first", 2)) else "over fetch budget",
                    "platform_id": "zh.wikipedia.org",
                    "tier": "B",
                    "access_kind": "full_page",
                }
            )
        return {
            "page": page,
            "query": query_text,
            "raw": payload,
            "hits": hits,
            "provider_id": self.provider_id,
            "provider_build_id": self.provider_build_id,
            "_raw_response_bytes": raw,
        }
