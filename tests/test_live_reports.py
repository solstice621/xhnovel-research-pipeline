from __future__ import annotations

import json

from xhnovel_pipeline.paths import repo_root


def test_live_reports_are_no_qualifying_case():
    root = repo_root() / "examples" / "live-runs"
    expected = {
        "rq-002-campaign-report.json": {
            "request_id": "REQ-LIVE-RQ002",
            "export_id": "EXP-LIVE-RQ002",
            "export_hash": "sha256:e8c4ca5fe0a7e65f942a38187d3255a0de57f78053cbf0c79c3e699606680ae3",
            "campaign_report": "NO_QUALIFYING_CASE_FOUND",
            "live_claim_count": 0,
        },
        "rq-003-campaign-report.json": {
            "request_id": "REQ-LIVE-RQ003",
            "export_id": "EXP-LIVE-RQ003",
            "export_hash": "sha256:11c3b3a0be328925d3e7a9e16779b190405cbdf9ca294cb88d1585f305cde065",
            "campaign_report": "NO_QUALIFYING_CASE_FOUND",
            "live_claim_count": 0,
        },
    }
    for name, fields in expected.items():
        report = json.loads((root / name).read_text(encoding="utf-8"))
        for key, value in fields.items():
            assert report[key] == value, name
        export = json.loads((root / f"{fields['export_id']}.json").read_text(encoding="utf-8"))
        assert export["export_hash"] == fields["export_hash"]
        assert export["claims"] == []
        assert "element_mapping" not in (export.get("scene_facts") or {})
