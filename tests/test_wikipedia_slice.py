from __future__ import annotations

from xhnovel_pipeline.engine import run_local_slice
from xhnovel_pipeline.paths import repo_root


def test_wikipedia_recorded_slice(tmp_path):
    root = repo_root()
    result = run_local_slice(
        root / "fixtures/positive/wikipedia-recorded", tmp_path, repo_root=root
    )
    catalog = result["catalog"]
    assert catalog.all("SearchRun")
    assert catalog.all("DiscoveryHit")
    assert result["export"]["scene_facts"]["campaign_report"] in {
        "CLAIMS_PRODUCED",
        "NO_QUALIFYING_CASE_FOUND",
    }
