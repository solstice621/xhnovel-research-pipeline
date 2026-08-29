from __future__ import annotations

import json

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
