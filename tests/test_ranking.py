from __future__ import annotations

import copy
import json

import pytest

from xhnovel_pipeline.catalog import Catalog
from xhnovel_pipeline.runtime import TEST_NOW as NOW
from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.ids import derived_id
from xhnovel_pipeline.paths import repo_root
from xhnovel_pipeline.ranking import (
    normalize_work_title,
    rank_candidates,
    run_fame_ranking,
    validate_fame_ranking,
)
from xhnovel_pipeline.store import ArtifactStore
from xhnovel_pipeline.ranking_provider import WikipediaRankingProvider


class RankingProvider:
    provider_id = "ranking-fixture"
    provider_build_id = "ranking-fixture-v1"

    def search(self, query_text, parameters):
        if parameters["page"] > 1:
            return {"hits": []}
        if "经典" in query_text:
            titles = ["《凡人修仙传》", "诛仙"]
        else:
            titles = ["凡人修仙传 - 维基百科", "斗破苍穹"]
        return {
            "hits": [
                {
                    "rank": index,
                    "title": title,
                    "url": f"https://example.com/{query_text}/{index}",
                }
                for index, title in enumerate(titles, start=1)
            ],
        }


def test_normalize_work_title_removes_book_marks_and_wikipedia_suffix():
    assert normalize_work_title("《凡人修仙传》") == "凡人修仙传"
    assert normalize_work_title("凡人修仙传 - 维基百科") == "凡人修仙传"


def test_ranking_records_windows_and_replays_scores(tmp_path):
    catalog = Catalog()
    store = ArtifactStore(tmp_path / "objects")
    run = run_fame_ranking(
        genre="仙侠",
        providers=[RankingProvider()],
        store=store,
        catalog=catalog,
        repo_root=repo_root(),
        created_at=NOW,
        queries=["仙侠经典小说", "仙侠小说代表作"],
        pages_per_query=1,
        limit=10,
    )

    assert run["candidates"][0]["title"] == "凡人修仙传"
    assert len(run["candidates"][0]["signals"]) == 2
    assert all(window["raw_response_artifact_id"] for window in run["provider_windows"])
    assert "not an exhaustive ranking" in run["limitations"][2]
    validate_fame_ranking(catalog, store)


def test_wikipedia_ranking_replays_exact_transport_response(tmp_path):
    payload = json.dumps(
        [
            "仙侠代表作",
            ["《凡人修仙传》", "诛仙"],
            ["凡人修仙传简介", "诛仙简介"],
            ["https://example.com/fanren", "https://example.com/zhuxian"],
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    class PayloadFetcher:
        def fetch(self, url):
            return payload, "application/json", 200, url

    catalog = Catalog()
    store = ArtifactStore(tmp_path / "objects")
    run = run_fame_ranking(
        genre="仙侠",
        providers=[WikipediaRankingProvider(fetcher=PayloadFetcher())],
        store=store,
        catalog=catalog,
        repo_root=repo_root(),
        created_at=NOW,
        queries=["仙侠代表作"],
    )

    raw_id = run["provider_windows"][0]["raw_response_artifact_id"]
    assert store.get(raw_id) == payload
    assert [candidate["title"] for candidate in run["candidates"]] == ["凡人修仙传", "诛仙"]
    validate_fame_ranking(catalog, store)


def test_wikipedia_ranking_rejects_normalized_hits_that_diverge_from_transport(tmp_path):
    payload = json.dumps(
        ["仙侠代表作", ["原始作品"], ["简介"], ["https://example.com/original"]],
        ensure_ascii=False,
    ).encode("utf-8")

    class DivergentWikipediaProvider:
        provider_id = "wikipedia-opensearch"
        provider_build_id = "wikipedia-opensearch-v1"

        def search(self, query, parameters):
            return {
                "hits": [
                    {
                        "rank": 1,
                        "title": "伪造作品",
                        "url": "https://attacker.invalid/forged",
                    }
                ],
                "_raw_response_bytes": payload,
            }

    with pytest.raises(ValidationError) as exc:
        run_fame_ranking(
            genre="仙侠",
            providers=[DivergentWikipediaProvider()],
            store=ArtifactStore(tmp_path / "objects"),
            catalog=Catalog(),
            repo_root=repo_root(),
            created_at=NOW,
            queries=["仙侠代表作"],
        )
    assert exc.value.code == "E-RANKING-REPLAY"


def test_ranking_validator_rejects_score_tampering(tmp_path):
    catalog = Catalog()
    store = ArtifactStore(tmp_path / "objects")
    run = run_fame_ranking(
        genre="玄幻",
        providers=[RankingProvider()],
        store=store,
        catalog=catalog,
        repo_root=repo_root(),
        created_at=NOW,
        queries=["玄幻小说代表作"],
    )
    run["candidates"][0]["score"] += 1

    with pytest.raises(ValidationError) as exc:
        validate_fame_ranking(catalog, store)
    assert exc.value.code == "E-RANKING-BIND"


def test_ranking_validator_rebuilds_signals_from_saved_provider_response(tmp_path):
    catalog = Catalog()
    store = ArtifactStore(tmp_path / "objects")
    run = run_fame_ranking(
        genre="玄幻",
        providers=[RankingProvider()],
        store=store,
        catalog=catalog,
        repo_root=repo_root(),
        created_at=NOW,
        queries=["玄幻小说代表作"],
    )
    forged_signals = [
        copy.deepcopy(signal)
        for candidate in run["candidates"]
        for signal in candidate["signals"]
    ]
    forged_signals[0]["title"] = "伪造作品"
    forged_signals[0]["url"] = "https://attacker.invalid/forged"
    run["candidates"] = rank_candidates(
        forged_signals,
        rrf_k=run["rrf_k"],
        score_scale=run["score_scale"],
    )
    identity = {key: value for key, value in run.items() if key != "ranking_run_id"}
    run["ranking_run_id"] = derived_id("NovelRankingRun", identity)

    with pytest.raises(ValidationError) as exc:
        validate_fame_ranking(catalog, store)
    assert exc.value.code == "E-RANKING-REPLAY"


def test_ranking_rejects_explicit_raw_response_that_cannot_replay_hits(tmp_path):
    class InvalidRawProvider:
        provider_id = "invalid-raw-fixture"
        provider_build_id = "invalid-raw-fixture-v1"

        def search(self, query_text, parameters):
            return {
                "hits": [
                    {
                        "rank": 1,
                        "title": "规范化结果",
                        "url": "https://example.test/normalized",
                    }
                ],
                "_raw_response_bytes": b"not-json",
            }

    with pytest.raises(ValidationError) as exc:
        run_fame_ranking(
            genre="玄幻",
            providers=[InvalidRawProvider()],
            store=ArtifactStore(tmp_path / "objects"),
            catalog=Catalog(),
            repo_root=repo_root(),
            created_at=NOW,
            queries=["玄幻小说代表作"],
        )
    assert exc.value.code == "E-RANKING-REPLAY"


def test_ranking_rejects_provider_page_larger_than_declared_window(tmp_path):
    class OversizedPageProvider:
        provider_id = "oversized-page-fixture"
        provider_build_id = "oversized-page-fixture-v1"

        def search(self, query_text, parameters):
            return {
                "hits": [
                    {
                        "rank": index,
                        "title": f"作品{index}",
                        "url": f"https://example.test/work/{index}",
                    }
                    for index in range(1, parameters["limit"] + 2)
                ]
            }

    with pytest.raises(ValidationError) as exc:
        run_fame_ranking(
            genre="玄幻",
            providers=[OversizedPageProvider()],
            store=ArtifactStore(tmp_path / "objects"),
            catalog=Catalog(),
            repo_root=repo_root(),
            created_at=NOW,
            queries=["玄幻小说代表作"],
            limit=2,
        )
    assert exc.value.code == "E-RANKING-WINDOW"


@pytest.mark.parametrize(
    "queries",
    [[""], ["同一查询", "同一查询"], ["同一查询", " 同一查询 "], 42],
)
def test_ranking_rejects_empty_or_duplicate_query_window(tmp_path, queries):
    with pytest.raises(ValidationError) as exc:
        run_fame_ranking(
            genre="玄幻",
            providers=[RankingProvider()],
            store=ArtifactStore(tmp_path / "objects"),
            catalog=Catalog(),
            repo_root=repo_root(),
            created_at=NOW,
            queries=queries,
        )
    assert exc.value.code == "E-RANKING-INPUT"


@pytest.mark.parametrize(
    ("pages_per_query", "limit"),
    [("oops", 10), (True, 10), (1, "oops"), (1, True)],
)
def test_ranking_rejects_non_integer_search_window(
    tmp_path, pages_per_query, limit
):
    with pytest.raises(ValidationError) as exc:
        run_fame_ranking(
            genre="玄幻",
            providers=[RankingProvider()],
            store=ArtifactStore(tmp_path / "objects"),
            catalog=Catalog(),
            repo_root=repo_root(),
            created_at=NOW,
            queries=["玄幻小说代表作"],
            pages_per_query=pages_per_query,
            limit=limit,
        )
    assert exc.value.code == "E-RANKING-INPUT"


def test_ranking_preserves_partial_provider_failure(tmp_path):
    class BrokenProvider:
        provider_id = "broken"
        provider_build_id = "broken-v1"

        def search(self, query_text, parameters):
            raise ValidationError("E-RETRYABLE", "rate limited")

    catalog = Catalog()
    store = ArtifactStore(tmp_path / "objects")
    run = run_fame_ranking(
        genre="玄幻",
        providers=[RankingProvider(), BrokenProvider()],
        store=store,
        catalog=catalog,
        repo_root=repo_root(),
        created_at=NOW,
        queries=["玄幻小说代表作"],
    )

    assert run["status"] == "PARTIAL"
    assert any(window["error_code"] == "E-RETRYABLE" for window in run["provider_windows"])


def test_ranking_validator_rejects_missing_window_page(tmp_path):
    catalog = Catalog()
    store = ArtifactStore(tmp_path / "objects")
    run = run_fame_ranking(
        genre="玄幻",
        providers=[RankingProvider()],
        store=store,
        catalog=catalog,
        repo_root=repo_root(),
        created_at=NOW,
        queries=["玄幻小说代表作"],
        pages_per_query=2,
    )
    run["provider_windows"] = [
        window for window in run["provider_windows"] if window["page"] != 1
    ]

    with pytest.raises(ValidationError) as exc:
        validate_fame_ranking(catalog, store)
    assert exc.value.code == "E-RANKING-WINDOW"
