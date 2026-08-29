from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

from .errors import ValidationError
from .user_agent import USER_AGENT

WIKI_OPENSEARCH = "https://zh.wikipedia.org/w/api.php?action=opensearch&format=json&namespace=0&limit={limit}&search={query}"
PROVIDER_ID = "wikipedia-opensearch"
PROVIDER_BUILD_ID = "wikipedia-opensearch-v1"


class WikipediaOpenSearchProvider:
    def __init__(self, *, cassette: dict[str, Any] | None = None, timeout: float = 20.0) -> None:
        self.cassette = cassette
        self.timeout = timeout
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
        if page > 1:
            return {"page": page, "hits": []}
        limit = int(parameters.get("limit", 10))
        url = WIKI_OPENSEARCH.format(limit=limit, query=quote(query_text))
        req = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            raw = urlopen(req, timeout=self.timeout).read()
        except Exception as exc:
            raise ValidationError("E-PROVIDER", f"wikipedia opensearch failed: {exc}") from exc
        payload = json.loads(raw.decode("utf-8"))
        titles = payload[1] if len(payload) > 1 else []
        snippets = payload[2] if len(payload) > 2 else []
        urls = payload[3] if len(payload) > 3 else []
        hits = []
        for i, title in enumerate(titles, start=1):
            hits.append(
                {
                    "hit_id": f"HIT-WIKI-{page:02d}-{i:02d}",
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
        }
