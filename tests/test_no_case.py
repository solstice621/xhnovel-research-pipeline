from __future__ import annotations

from xhnovel_pipeline.engine import run_local_slice
from xhnovel_pipeline.ids import derived_id
from xhnovel_pipeline.paths import repo_root


def test_no_qualifying_case_records_hits_without_fake_claims(tmp_path):
    root = repo_root()
    result = run_local_slice(root / "fixtures/positive/no-qualifying-case", tmp_path, repo_root=root)
    catalog = result["catalog"]
    export = result["export"]
    assert catalog.all("DiscoveryHit")
    assert catalog.all("SearchCampaign")[0]["stop_reason"]
    assert export["claims"] == []
    assert export["scene_facts"]["campaign_report"] == "NO_QUALIFYING_CASE_FOUND"
    assert catalog.all("SearchCampaign")[0]["stop_reason"] == "budget_exhausted"
    qualification = catalog.all("QualificationRun")[0]
    qualification_payload = {key: value for key, value in qualification.items() if key != "qualification_run_id"}
    assert qualification["qualification_run_id"] == derived_id("QualificationRun", qualification_payload)
    assert qualification["result"] == "PASS"
    assert catalog.all("ExtractorBuild")[0]["status"] == "QUALIFIED"
