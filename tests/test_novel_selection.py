from __future__ import annotations

import copy

import pytest

from xhnovel_pipeline.catalog import Catalog
from xhnovel_pipeline.runtime import TEST_NOW as NOW
from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.novel_selection import resolve_ranked_source, validate_source_resolutions
from xhnovel_pipeline.paths import repo_root
from xhnovel_pipeline.ranking import run_fame_ranking, validate_fame_ranking
from xhnovel_pipeline.store import ArtifactStore


class RankingProvider:
    provider_id = "selection-fixture"
    provider_build_id = "selection-fixture-v1"

    def search(self, query, params):
        hits = [
            {"rank": 1, "title": "无来源作品", "url": "https://example.test/unavailable"},
            {"rank": 2, "title": "测试仙途", "url": "https://example.test/available"},
        ]
        return {"hits": hits}


def _ranking(tmp_path):
    catalog = Catalog()
    store = ArtifactStore(tmp_path / "objects")
    ranking = run_fame_ranking(
        genre="仙侠",
        providers=[RankingProvider()],
        store=store,
        catalog=catalog,
        repo_root=repo_root(),
        created_at=NOW,
        queries=["仙侠代表作"],
        limit=10,
    )
    return catalog, store, ranking


def test_resolution_selects_highest_ranked_candidate_with_declared_source(tmp_path):
    catalog, store, ranking = _ranking(tmp_path)
    source_path = tmp_path / "book.txt"
    source_path.write_text("第一章 开始\n正文", encoding="utf-8")

    resolution, novel_spec = resolve_ranked_source(
        ranking,
        [
            {
                "candidate_titles": ["《测试仙途》"],
                "source": {"kind": "txt", "path": str(source_path)},
            }
        ],
        catalog,
        store,
        defaults={"evidence": {"tier": "A", "access_legitimacy": "AUTHORIZED"}},
        created_at=NOW,
    )

    assert resolution["candidate_rank"] == 2
    assert resolution["match_method"] == "NORMALIZED_TITLE"
    assert novel_spec["source"]["title"] == "测试仙途"
    validate_fame_ranking(catalog, store)
    validate_source_resolutions(catalog, store)


def test_resolution_rejects_ambiguous_source_entries(tmp_path):
    catalog, store, ranking = _ranking(tmp_path)
    entries = [
        {"candidate_titles": ["测试仙途"], "source": {"kind": "txt", "path": "/a"}},
        {"candidate_titles": ["测试仙途"], "source": {"kind": "txt", "path": "/b"}},
    ]

    with pytest.raises(ValidationError, match="E-NOVEL-SOURCE-AMBIGUOUS"):
        resolve_ranked_source(ranking, entries, catalog, store, created_at=NOW)


@pytest.mark.parametrize("defaults", [False, [], 0, ""])
def test_resolution_rejects_falsey_non_object_defaults(tmp_path, defaults):
    catalog, store, ranking = _ranking(tmp_path)

    with pytest.raises(ValidationError, match="E-NOVEL-SOURCE-CATALOG"):
        resolve_ranked_source(
            ranking,
            [{"candidate_titles": ["测试仙途"], "source": {"kind": "txt", "path": "/book"}}],
            catalog,
            store,
            defaults=defaults,
            created_at=NOW,
        )


def test_resolution_validator_rejects_tampered_candidate_binding(tmp_path):
    catalog, store, ranking = _ranking(tmp_path)
    resolution, _ = resolve_ranked_source(
        ranking,
        [{"candidate_titles": ["测试仙途"], "source": {"kind": "txt", "path": "/book"}}],
        catalog,
        store,
        created_at=NOW,
    )
    resolution["candidate_rank"] = 1

    with pytest.raises(ValidationError, match="E-NOVEL-SOURCE-BIND"):
        validate_source_resolutions(catalog, store)


def test_resolution_validator_rejects_repointed_spec_artifact(tmp_path):
    catalog, store, ranking = _ranking(tmp_path)
    resolution, _ = resolve_ranked_source(
        ranking,
        [{"candidate_titles": ["测试仙途"], "source": {"kind": "txt", "path": "/book"}}],
        catalog,
        store,
        created_at=NOW,
    )
    tampered = copy.deepcopy(resolution)
    tampered["source_spec_artifact_id"] = ranking["policy_artifact_id"]
    catalog.by_type["NovelSourceResolution"] = [tampered]

    with pytest.raises(ValidationError, match="E-NOVEL-SOURCE-BIND"):
        validate_source_resolutions(catalog, store)
