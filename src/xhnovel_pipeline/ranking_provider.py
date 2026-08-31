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


def _valid_result_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        parsed.port
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


class WikipediaRankingProvider:
    """Bounded title-ranking provider; it is not an evidence collector."""

    provider_id = "wikipedia-opensearch"
    provider_build_id = "wikipedia-opensearch-v1"

    def __init__(
        self,
        *,
        cassette: dict[str, Any] | None = None,
        timeout: float = 20.0,
        fetcher: HttpFetcher | None = None,
    ) -> None:
        self.cassette = cassette
        self.fetcher = fetcher or HttpFetcher(timeout=timeout)

    def search(self, query_text: str, parameters: dict[str, Any]) -> dict[str, Any]:
        page = int(parameters.get("page", 1))
        if self.cassette is not None:
            pages = self.cassette.get("pages") or [self.cassette]
            return next(
                (block for block in pages if int(block.get("page", 1)) == page),
                {"page": page, "hits": []},
            )
        limit = int(parameters.get("limit", 10))
        offset = (page - 1) * limit
        url = WIKI_OPENSEARCH.format(limit=limit, offset=offset, query=quote(query_text))
        raw = b""
        try:
            raw, _, _, _ = self.fetcher.fetch(url)
            payload = json.loads(raw.decode("utf-8"))
        except ValidationError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
            error = ValidationError("E-PROVIDER-SCHEMA", "ranking provider returned invalid JSON")
            error.raw_response_bytes = raw
            raise error from exc
        if (
            not isinstance(payload, list)
            or len(payload) != 4
            or not isinstance(payload[0], str)
            or any(not isinstance(items, list) for items in payload[1:])
            or len({len(items) for items in payload[1:]}) != 1
            or any(not isinstance(item, str) for items in payload[1:] for item in items)
            or any(not _valid_result_url(item) for item in payload[3])
        ):
            error = ValidationError("E-PROVIDER-SCHEMA", "ranking provider response shape changed")
            error.raw_response_bytes = raw
            raise error
        titles, snippets, urls = payload[1:]
        hits = []
        for rank, title in enumerate(titles, start=1):
            result_url = urls[rank - 1]
            hits.append(
                {
                    "hit_id": derived_id(
                        "NovelRankingHit",
                        {"query": query_text, "page": page, "rank": rank, "url": result_url},
                    ),
                    "rank": rank,
                    "url": result_url,
                    "title": title,
                    "snippet": snippets[rank - 1],
                    "selection_status": "SELECTED",
                    "selection_reason": "bounded ranking input",
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
