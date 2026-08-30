from __future__ import annotations

import json
import pytest

from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.paths import repo_root
from xhnovel_pipeline.wikipedia import WikipediaOpenSearchProvider


def test_wikipedia_recorded_records_all_hits():
    cassette = json.loads(
        (repo_root() / "fixtures/positive/wikipedia-recorded/provider.json").read_text(encoding="utf-8")
    )
    provider = WikipediaOpenSearchProvider(cassette=cassette)
    page1 = provider.search("青铜", {"page": 1})
    assert len(page1["hits"]) == 2
    assert page1["hits"][0]["selection_status"] == "SELECTED"
    assert page1["hits"][1]["selection_status"] == "REJECTED"
    page2 = provider.search("青铜", {"page": 2})
    assert page2["hits"] == []


def test_wikipedia_429_is_retryable(monkeypatch):
    class FailingFetcher:
        def fetch(self, url):
            raise ValidationError("E-RETRYABLE", "HTTP 429")

    with pytest.raises(ValidationError) as exc:
        WikipediaOpenSearchProvider(fetcher=FailingFetcher()).search("青铜", {"page": 1})
    assert exc.value.code == "E-RETRYABLE"


@pytest.mark.parametrize("payload", [b"{}", b"not-json", b'["query", ["title"], [], []]'])
def test_wikipedia_schema_drift_is_explicit_and_keeps_raw_bytes(payload):
    class PayloadFetcher:
        def fetch(self, url):
            return payload, "application/json", 200, url

    with pytest.raises(ValidationError) as exc:
        WikipediaOpenSearchProvider(fetcher=PayloadFetcher()).search("青铜", {"page": 1})

    assert exc.value.code == "E-PROVIDER-SCHEMA"
    assert exc.value.raw_response_bytes == payload


def test_wikipedia_live_hit_ids_include_query_identity():
    class PayloadFetcher:
        def fetch(self, url):
            payload = json.dumps(["q", ["title"], ["snippet"], ["https://example.com/page"]]).encode()
            return payload, "application/json", 200, url

    provider = WikipediaOpenSearchProvider(fetcher=PayloadFetcher())
    first = provider.search("first", {"page": 1})["hits"][0]["hit_id"]
    second = provider.search("second", {"page": 1})["hits"][0]["hit_id"]
    assert first != second


def test_wikipedia_live_pages_use_non_overlapping_offsets():
    class PayloadFetcher:
        def __init__(self):
            self.urls = []

        def fetch(self, url):
            self.urls.append(url)
            payload = json.dumps(["q", [], [], []]).encode()
            return payload, "application/json", 200, url

    fetcher = PayloadFetcher()
    provider = WikipediaOpenSearchProvider(fetcher=fetcher)
    provider.search("query", {"page": 1, "limit": 10})
    provider.search("query", {"page": 2, "limit": 10})

    assert "offset=0" in fetcher.urls[0]
    assert "offset=10" in fetcher.urls[1]


def test_wikipedia_oversized_json_integer_is_normalized_and_preserves_raw_bytes():
    payload = b'["q", ["title"], ["snippet"], ["https://example.com/page"], ' + b"9" * 5000 + b"]"

    class PayloadFetcher:
        def fetch(self, url):
            return payload, "application/json", 200, url

    with pytest.raises(ValidationError) as exc:
        WikipediaOpenSearchProvider(fetcher=PayloadFetcher()).search("q", {"page": 1})

    assert exc.value.code == "E-PROVIDER-SCHEMA"
    assert exc.value.raw_response_bytes == payload


def test_wikipedia_deep_json_is_normalized_and_preserves_raw_bytes():
    payload = b"[" * 10_000 + b"]" * 10_000

    class PayloadFetcher:
        def fetch(self, url):
            return payload, "application/json", 200, url

    with pytest.raises(ValidationError) as exc:
        WikipediaOpenSearchProvider(fetcher=PayloadFetcher()).search("q", {"page": 1})

    assert exc.value.code == "E-PROVIDER-SCHEMA"
    assert exc.value.raw_response_bytes == payload


def test_wikipedia_invalid_result_port_is_schema_failure_with_raw_bytes():
    payload = json.dumps(
        ["q", ["title"], ["snippet"], ["http://example.com:bad/page"]]
    ).encode()

    class PayloadFetcher:
        def fetch(self, url):
            return payload, "application/json", 200, url

    with pytest.raises(ValidationError) as exc:
        WikipediaOpenSearchProvider(fetcher=PayloadFetcher()).search("q", {"page": 1})

    assert exc.value.code == "E-PROVIDER-SCHEMA"
    assert exc.value.raw_response_bytes == payload
