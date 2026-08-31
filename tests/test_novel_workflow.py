from __future__ import annotations

import copy
import json

import pytest

from xhnovel_pipeline.canonical import canonical_dumps
from xhnovel_pipeline.runtime import TEST_NOW as NOW
from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.hashing import object_hash
from xhnovel_pipeline.artifact_closure import live_artifact_ids
from xhnovel_pipeline.ids import derived_id
from xhnovel_pipeline.model_api import OpenAIResponsesClient
from xhnovel_pipeline.model_collection import OpenAICollectionAssessor
from xhnovel_pipeline.novel_workflow import (
    _make_unqualified_export,
    _write_immutable_outputs,
    run_famous_novel_research,
    run_novel_research,
)
from xhnovel_pipeline.paths import repo_root
from xhnovel_pipeline.plot_analysis import run_plot_analysis, validate_plot_analysis
from xhnovel_pipeline.plot_extraction import (
    run_model_plot_extraction,
    validate_model_plot_extractions,
)
from xhnovel_pipeline.validate import validate_all, validate_evidence, validate_export


def _response(value, response_id):
    return json.dumps(
        {
            "id": response_id,
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(value, ensure_ascii=False)}],
                }
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")


def _store_json_artifact(result, value):
    data = canonical_dumps(value)
    artifact_id = result["store"].put(data)
    if artifact_id not in result["catalog"].ids("Artifact"):
        result["catalog"].add(
            "Artifact",
            {
                "schema_version": result["bundle"]["schema_version"],
                "artifact_id": artifact_id,
                "media_type": "application/json",
                "byte_length": len(data),
                "retention_policy": "retention-v1",
                "durability_status": "LOCAL",
                "created_at": NOW,
            },
        )
    return artifact_id


def _collection_transport(role):
    def transport(url, headers, body, timeout):
        request = json.loads(body)
        model_input = json.loads(request["input"])
        artifact = json.loads(model_input["artifacts"][0]["untrusted_text"])
        if model_input["task"] == "TRIAGE":
            assert "evidence" not in artifact
            value = {
                "outcome": {
                    "disposition": "SELECTED",
                    "tier": "B",
                    "access_legitimacy": "AUTHORIZED",
                },
                "confidence": "HIGH",
                "basis": ["chapter text and source metadata support collection"],
            }
            return 200, {}, _response(value, f"{role}-triage-{artifact['chapter']['ordinal']}")
        assert "chapter_key" not in artifact
        assert "document_title" not in artifact
        observation = artifact["body_heading_observation"]
        observed_title = artifact["segments"][0]["text"] if artifact["segments"] else ""
        disposition = "MATCH" if observation["text"] == observed_title else "MISMATCH"
        value = {
            "outcome": {"identity_status": disposition},
            "confidence": "HIGH",
            "basis": ["the body-heading observation agrees with the frozen segment"],
        }
        return 200, {}, _response(value, f"{role}-{observation['declared_number']}")

    return transport


def _extraction_transport(url, headers, body, timeout):
    request = json.loads(body)
    model_input = json.loads(request["input"])
    first_by_document = {}
    for segment in model_input["segments"]:
        first_by_document.setdefault(segment["document_id"], segment)
    events = []
    for index, segment in enumerate(first_by_document.values(), start=1):
        events.append(
            {
                "statement": f"林舟完成阶段事件{index}",
                "segment_ids": [segment["segment_id"]],
                "actors": ["林舟", "少年"],
                "action": "进入山门" if index == 1 else "拜师",
                "target": "修行道路",
                "precondition": "此前尚未入门" if index == 1 else "已经进入山门",
                "state_transition": "成为弟子" if index == 2 else "进入宗门",
                "timeline": [f"阶段{index}"],
                "conflicts": [],
                "immediate_feedback": "身份发生变化",
                "new_affordances": ["可以修行"],
                "persistence": "持续到后续章节",
            }
        )
    return 200, {}, _response({"events": events}, "extract-response")


def _analysis_transport(url, headers, body, timeout):
    request = json.loads(body)
    model_input = json.loads(request["input"])
    claim_ids = [claim["claim_id"] for claim in model_input["claims"]]
    value = {
        "alias_groups": [
            {"canonical_name": "林舟", "aliases": ["林舟", "少年"], "claim_ids": claim_ids}
        ],
        "event_groups": [
            {"group_key": "入门事件", "summary": "林舟入门并拜师", "claim_ids": claim_ids}
        ],
        "importance": [
            {
                "claim_id": claim_id,
                "causal_impact": 5,
                "character_change": 4,
                "world_state_change": 3,
                "setup_payoff": 4,
                "rationale": "改变后续修行路径",
            }
            for claim_id in claim_ids
        ],
    }
    return 200, {}, _response(value, "analysis-response")


def _run_direct_workflow(tmp_path):
    chapters = tmp_path / "chapters"
    chapters.mkdir()
    (chapters / "第1章 入山.txt").write_text(
        "第1章 入山\n\n林舟以少年身份进入山门。",
        encoding="utf-8",
    )
    (chapters / "第2章 拜师.txt").write_text(
        "第2章 拜师\n\n林舟拜长老为师，成为正式弟子。",
        encoding="utf-8",
    )
    spec = {
        "source": {"kind": "directory", "path": str(chapters), "title": "测试仙途"},
        "evidence": {"tier": "A", "access_legitimacy": "AUTHORIZED"},
        "limits": {"max_chapters": 10, "max_bytes": 100_000},
        "strict_order": True,
    }
    collector_client = OpenAIResponsesClient(
        model="small-model-snapshot",
        api_key="test-key",
        transport=_collection_transport("collector"),
    )
    reviewer_client = OpenAIResponsesClient(
        model="large-model-snapshot",
        api_key="test-key",
        transport=_collection_transport("reviewer"),
    )
    extractor_client = OpenAIResponsesClient(
        model="extractor-model-snapshot",
        api_key="test-key",
        transport=_extraction_transport,
    )
    analyst_client = OpenAIResponsesClient(
        model="analyst-model-snapshot",
        api_key="test-key",
        transport=_analysis_transport,
    )

    return run_novel_research(
        spec,
        tmp_path / "run",
        collector=OpenAICollectionAssessor(collector_client, role="COLLECTOR"),
        reviewer=OpenAICollectionAssessor(reviewer_client, role="REVIEWER"),
        extractor_client=extractor_client,
        analyst_client=analyst_client,
        repo_root=repo_root(),
        now=NOW,
    )


def test_one_click_novel_research_produces_reviewed_bundle_claims_and_analysis(tmp_path):
    result = _run_direct_workflow(tmp_path)

    assert result["snapshot"]["quality_gate"]["result"] == "PASS"
    assert result["bundle"]["selection_manifest"]["quality_gate_result"] == "PASS"
    assert len(result["extraction"]["claims"]) == 2
    assert result["analysis"]["event_groups"][0]["cross_chapter"] is True
    assert [item["sequence"] for item in result["analysis"]["timeline"]] == [1, 2]
    claims_by_id = {claim["claim_id"]: claim for claim in result["extraction"]["claims"]}
    assert [
        claims_by_id[item["claim_id"]]["profile_payload"]["action"]
        for item in result["analysis"]["timeline"]
    ] == ["进入山门", "拜师"]
    assert result["analysis"]["alias_groups"][0]["canonical_name"] == "林舟"
    assert result["analysis"]["key_events"][0]["score"] > 0
    assert result["export"]["assurance"]["level"] == "UNQUALIFIED"
    assert result["export"]["assurance"]["auditability"] == "DEGRADED"
    assert (result["work_dir"] / "run-summary.json").is_file()
    summary = json.loads((result["work_dir"] / "run-summary.json").read_text(encoding="utf-8"))
    assert summary["auditability"] == "DEGRADED"
    assert (result["work_dir"] / "plot-analysis.json").is_file()
    assert (result["work_dir"] / "evidence-export.json").is_file()


def test_model_backed_novel_export_cannot_claim_full_auditability(tmp_path):
    result = _run_direct_workflow(tmp_path)
    export = result["export"]
    export["assurance"]["auditability"] = "FULL"
    export_identity = {
        key: value for key, value in export.items() if key not in {"export_id", "export_hash"}
    }
    export["export_id"] = derived_id("EvidenceExport", export_identity)
    export["export_hash"] = object_hash(export, omit=("export_hash",))

    with pytest.raises(ValidationError) as exc:
        validate_export(result["catalog"], result["store"])

    assert exc.value.code == "E-AUDITABILITY"


def test_degraded_novel_export_rejects_missing_local_manifest_artifact(tmp_path):
    result = _run_direct_workflow(tmp_path)
    artifact_id = result["export"]["artifact_manifest"][0]["artifact_id"]
    result["store"].delete_for_test(artifact_id)

    with pytest.raises(ValidationError) as exc:
        validate_export(result["catalog"], result["store"])

    assert exc.value.code == "E-ARTIFACT-MISSING"


def test_degraded_novel_export_rejects_corrupt_manifest_artifact(tmp_path):
    result = _run_direct_workflow(tmp_path)
    artifact_id = result["export"]["artifact_manifest"][0]["artifact_id"]
    result["store"]._path(artifact_id).write_bytes(b"corrupt-cas-bytes")

    with pytest.raises(ValidationError) as exc:
        validate_export(result["catalog"], result["store"])

    assert exc.value.code == "E-ARTIFACT-CORRUPT"


def test_degraded_novel_export_rejects_manifest_length_not_matching_cas(tmp_path):
    result = _run_direct_workflow(tmp_path)
    item = result["export"]["artifact_manifest"][0]
    artifact = result["catalog"].get("Artifact", item["artifact_id"])
    item["byte_length"] += 1
    artifact["byte_length"] += 1
    export_identity = {
        key: value
        for key, value in result["export"].items()
        if key not in {"export_id", "export_hash"}
    }
    result["export"]["export_id"] = derived_id("EvidenceExport", export_identity)
    result["export"]["export_hash"] = object_hash(
        result["export"], omit=("export_hash",)
    )

    with pytest.raises(ValidationError) as exc:
        validate_export(result["catalog"], result["store"])

    assert exc.value.code == "E-HASH-MISMATCH"


def test_full_export_and_gc_live_set_cover_ingestion_checkpoint_cas_closure(tmp_path):
    result = _run_direct_workflow(tmp_path)
    ingestion = result["ingestion"]
    checkpoint = json.loads(
        result["store"].get(ingestion["checkpoint_artifact_id"]).decode("utf-8")
    )
    closure = {
        checkpoint["discovery_artifact_id"],
        *(completion["receipt_artifact_id"] for completion in checkpoint["completed"].values()),
        *(checkpoint.get("site_attempt_receipt_ids") or []),
    }
    for receipt_id in checkpoint.get("site_attempt_receipt_ids") or []:
        receipt = json.loads(result["store"].get(receipt_id).decode("utf-8"))
        if receipt["raw_artifact_id"]:
            closure.add(receipt["raw_artifact_id"])

    manifest_ids = {item["artifact_id"] for item in result["export"]["artifact_manifest"]}
    catalog_data = {
        kind: records for kind, records in result["catalog"].by_type.items() if records
    }
    assert closure <= manifest_ids
    assert closure <= live_artifact_ids(catalog_data)


def test_chapter_identity_review_hides_internal_key_and_compares_content(tmp_path):
    result = _run_direct_workflow(tmp_path)

    for decision in result["catalog"].all("CollectionDecision"):
        if decision["task"] != "CHAPTER_IDENTITY":
            continue
        chapter = result["catalog"].get("NovelChapter", decision["subject_ids"][0])
        review_input = json.loads(
            result["store"].get(decision["input_artifact_ids"][0]).decode("utf-8")
        )

        assert "chapter_key" not in review_input
        assert "document_title" not in review_input
        assert "discovered_title" not in review_input
        assert "discovered_ordinal" not in review_input
        assert review_input["identity_scope"] == "DISCOVERY_ORDER_VS_BODY_HEADING_V1"
        assert review_input["body_heading_observation"] == {
            "segment_id": chapter["segment_ids"][0],
            "text": review_input["segments"][0]["text"],
            "declared_number": chapter["declared_number"],
        }
        assert decision["outcome"]["identity_status"] == "MATCH"


def test_chapter_identity_review_rejects_wrong_chapter_content(tmp_path):
    chapters = tmp_path / "chapters"
    chapters.mkdir()
    (chapters / "第1章 入山.txt").write_text(
        "第2章 拜师\n\n这不是目录所声称的章节。",
        encoding="utf-8",
    )
    spec = {
        "source": {"kind": "directory", "path": str(chapters), "title": "错章夹具"},
        "evidence": {"tier": "A", "access_legitimacy": "AUTHORIZED"},
        "limits": {"max_chapters": 10, "max_bytes": 100_000},
        "strict_order": True,
    }

    with pytest.raises(ValidationError, match="E-CHAPTER-IDENTITY"):
        run_novel_research(
            spec,
            tmp_path / "run",
            collector=OpenAICollectionAssessor(
                OpenAIResponsesClient(
                    model="small-model-snapshot",
                    api_key="test-key",
                    transport=_collection_transport("collector"),
                ),
                role="COLLECTOR",
            ),
            reviewer=OpenAICollectionAssessor(
                OpenAIResponsesClient(
                    model="large-model-snapshot",
                    api_key="test-key",
                    transport=_collection_transport("reviewer"),
                ),
                role="REVIEWER",
            ),
            extractor_client=OpenAIResponsesClient(
                model="unused-extractor", api_key="test-key", transport=_extraction_transport
            ),
            analyst_client=OpenAIResponsesClient(
                model="unused-analyst", api_key="test-key", transport=_analysis_transport
            ),
            repo_root=repo_root(),
            now=NOW,
        )


def test_plot_claims_cannot_diverge_from_stored_model_response(tmp_path):
    result = _run_direct_workflow(tmp_path)
    claim = result["catalog"].all("Claim")[0]
    claim["statement"] = "伪造的情节"
    claim["claim_id"] = derived_id("Claim", {key: value for key, value in claim.items() if key != "claim_id"})

    with pytest.raises(ValidationError, match="E-MODEL-REPLAY"):
        validate_model_plot_extractions(result["catalog"], result["store"])


def test_plot_analysis_cannot_diverge_from_stored_model_response(tmp_path):
    result = _run_direct_workflow(tmp_path)
    analysis = result["analysis"]
    analysis["event_groups"][0]["summary"] = "伪造的跨章总结"
    analysis["analysis_id"] = derived_id(
        "PlotAnalysis", {key: value for key, value in analysis.items() if key != "analysis_id"}
    )

    with pytest.raises(ValidationError, match="E-PLOT-REPLAY"):
        validate_plot_analysis(result["catalog"], result["store"])


def test_export_scene_facts_are_bound_to_plot_analysis(tmp_path):
    result = _run_direct_workflow(tmp_path)
    export = result["export"]
    export["scene_facts"]["event_groups"][0]["summary"] = "伪造的导出总结"
    identity = {key: value for key, value in export.items() if key not in {"export_id", "export_hash"}}
    export["export_id"] = derived_id("EvidenceExport", identity)
    export["export_hash"] = object_hash(export, omit=("export_hash",))

    with pytest.raises(ValidationError, match="E-PLOT-BIND"):
        validate_export(result["catalog"], result["store"])


def test_plot_extraction_rejects_duplicate_bundle_segment_before_model_call(tmp_path):
    result = _run_direct_workflow(tmp_path)
    bundle = copy.deepcopy(result["bundle"])
    bundle["segment_ids"].append(bundle["segment_ids"][0])
    calls = 0

    def transport(url, headers, body, timeout):
        nonlocal calls
        calls += 1
        return _extraction_transport(url, headers, body, timeout)

    with pytest.raises(ValidationError, match="E-PLOT-LINEAGE"):
        run_model_plot_extraction(
            result["catalog"],
            result["store"],
            bundle,
            client=OpenAIResponsesClient(
                model="duplicate-segment-extractor",
                api_key="test-key",
                transport=transport,
            ),
            repo_root=repo_root(),
            now=NOW,
        )
    assert calls == 0


def test_plot_extraction_rejects_empty_model_event_set(tmp_path):
    result = _run_direct_workflow(tmp_path)

    def transport(url, headers, body, timeout):
        return 200, {}, _response({"events": []}, "empty-extraction-response")

    with pytest.raises(ValidationError, match="E-PLOT-EMPTY"):
        run_model_plot_extraction(
            result["catalog"],
            result["store"],
            result["bundle"],
            client=OpenAIResponsesClient(
                model="empty-event-extractor",
                api_key="test-key",
                transport=transport,
            ),
            repo_root=repo_root(),
            now=NOW,
        )


def test_plot_extraction_rejects_duplicate_model_events(tmp_path):
    result = _run_direct_workflow(tmp_path)

    def transport(url, headers, body, timeout):
        status, response_headers, response_bytes = _extraction_transport(
            url, headers, body, timeout
        )
        response = json.loads(response_bytes)
        value = json.loads(response["output"][0]["content"][0]["text"])
        value["events"].append(copy.deepcopy(value["events"][0]))
        return status, response_headers, _response(value, "duplicate-extraction-response")

    with pytest.raises(ValidationError, match="E-PLOT-DUPLICATE"):
        run_model_plot_extraction(
            result["catalog"],
            result["store"],
            result["bundle"],
            client=OpenAIResponsesClient(
                model="duplicate-event-extractor",
                api_key="test-key",
                transport=transport,
            ),
            repo_root=repo_root(),
            now=NOW,
        )


def test_plot_extraction_replay_rejects_unrecorded_request_capability(tmp_path):
    result = _run_direct_workflow(tmp_path)
    run = result["extraction"]["run"]
    old_id = run["model_request_artifact_ids"][0]
    request = json.loads(result["store"].get(old_id).decode("utf-8"))
    request["tools"] = []
    new_id = _store_json_artifact(result, request)
    run["model_request_artifact_ids"][0] = new_id
    run["input_manifest"]["tool_input_hashes"][0] = new_id

    with pytest.raises(ValidationError) as exc:
        validate_model_plot_extractions(result["catalog"], result["store"])
    assert exc.value.code == "E-MODEL-REPLAY"


def test_plot_analysis_rejects_cross_work_before_model_call(tmp_path):
    result = _run_direct_workflow(tmp_path)
    original_work = result["catalog"].all("NovelWork")[0]
    foreign_work = copy.deepcopy(original_work)
    foreign_work["work_id"] = "NWK-FOREIGN-WORK"
    result["catalog"].add("NovelWork", foreign_work)
    called = False

    def transport(url, headers, body, timeout):
        nonlocal called
        called = True
        return _analysis_transport(url, headers, body, timeout)

    with pytest.raises(ValidationError, match="E-PLOT-LINEAGE"):
        run_plot_analysis(
            result["catalog"],
            result["store"],
            work_id=foreign_work["work_id"],
            extraction_run_id=result["extraction"]["run"]["extraction_run_id"],
            client=OpenAIResponsesClient(
                model="cross-work-analyst",
                api_key="test-key",
                transport=transport,
            ),
            repo_root=repo_root(),
            created_at=NOW,
        )
    assert called is False


def test_plot_analysis_must_cover_every_active_claim_from_its_extraction(tmp_path):
    result = _run_direct_workflow(tmp_path)
    foreign = copy.deepcopy(result["extraction"]["claims"][0])
    foreign["statement"] = "同一次抽取中被分析遗漏的事实"
    foreign["claim_id"] = derived_id(
        "Claim", {key: value for key, value in foreign.items() if key != "claim_id"}
    )
    result["catalog"].add("Claim", foreign)

    with pytest.raises(ValidationError) as exc:
        validate_plot_analysis(result["catalog"], result["store"])
    assert exc.value.code == "E-PLOT-LINEAGE"


def test_plot_analysis_replay_rejects_unrecorded_request_capability(tmp_path):
    result = _run_direct_workflow(tmp_path)
    analysis = result["analysis"]
    request = json.loads(
        result["store"].get(analysis["model_request_artifact_id"]).decode("utf-8")
    )
    request["tools"] = []
    analysis["model_request_artifact_id"] = _store_json_artifact(result, request)
    analysis["analysis_id"] = derived_id(
        "PlotAnalysis", {key: value for key, value in analysis.items() if key != "analysis_id"}
    )

    with pytest.raises(ValidationError) as exc:
        validate_plot_analysis(result["catalog"], result["store"])
    assert exc.value.code == "E-PLOT-REPLAY"


def test_novel_export_excludes_foreign_active_claims_in_reused_catalog(tmp_path):
    result = _run_direct_workflow(tmp_path)
    foreign = copy.deepcopy(result["extraction"]["claims"][0])
    foreign["extraction_run_id"] = "ERUN-FOREIGN-ACTIVE"
    foreign["statement"] = "来自其他批次的活跃事实"
    foreign["claim_id"] = derived_id(
        "Claim", {key: value for key, value in foreign.items() if key != "claim_id"}
    )
    result["catalog"].add("Claim", foreign)
    result["catalog"].by_type["EvidenceExport"].clear()

    export = _make_unqualified_export(
        result["catalog"],
        result["bundle"],
        result["extraction"],
        result["analysis"],
        repo_root=repo_root(),
        now=NOW,
    )

    assert [claim["claim_id"] for claim in export["claims"]] == sorted(
        result["analysis"]["claim_ids"]
    )
    validate_export(result["catalog"], result["store"])


def test_reused_catalog_keeps_each_novel_export_bound_to_its_own_run(tmp_path):
    first_root = tmp_path / "first"
    first_root.mkdir()
    first = _run_direct_workflow(first_root)
    chapters = tmp_path / "second-chapters"
    chapters.mkdir()
    (chapters / "第1章 下山.txt").write_text(
        "第1章 下山\n\n林舟离开山门。", encoding="utf-8"
    )
    spec = {
        "source": {"kind": "directory", "path": str(chapters), "title": "测试仙途续篇"},
        "limits": {"max_chapters": 10, "max_bytes": 100_000},
        "strict_order": True,
    }

    second = run_novel_research(
        spec,
        tmp_path / "second-run",
        collector=OpenAICollectionAssessor(
            OpenAIResponsesClient(
                model="small-model-snapshot",
                api_key="test-key",
                transport=_collection_transport("collector"),
            ),
            role="COLLECTOR",
        ),
        reviewer=OpenAICollectionAssessor(
            OpenAIResponsesClient(
                model="large-model-snapshot",
                api_key="test-key",
                transport=_collection_transport("reviewer"),
            ),
            role="REVIEWER",
        ),
        extractor_client=OpenAIResponsesClient(
            model="extractor-model-snapshot",
            api_key="test-key",
            transport=_extraction_transport,
        ),
        analyst_client=OpenAIResponsesClient(
            model="analyst-model-snapshot",
            api_key="test-key",
            transport=_analysis_transport,
        ),
        repo_root=repo_root(),
        now=NOW,
        catalog=first["catalog"],
        store=first["store"],
    )

    exports = second["catalog"].all("EvidenceExport")
    assert len(exports) == 2
    assert [
        {claim["extraction_run_id"] for claim in export["claims"]}
        for export in exports
    ] == [
        {first["extraction"]["run"]["extraction_run_id"]},
        {second["extraction"]["run"]["extraction_run_id"]},
    ]
    second_manifest_ids = {
        item["artifact_id"] for item in second["export"]["artifact_manifest"]
    }
    assert not set(first["bundle"]["artifact_ids"]) & second_manifest_ids
    validate_all(second["catalog"], second["store"])


def test_output_conflict_is_detected_before_writing_any_missing_sibling(tmp_path):
    output_dir = tmp_path / "research-output"
    output_dir.mkdir()
    (output_dir / "plot-analysis.json").write_bytes(b"tampered")

    with pytest.raises(ValidationError, match="E-IMMUTABLE-OUTPUT"):
        _write_immutable_outputs(
            output_dir,
            {
                "catalog.json": b"new catalog",
                "plot-analysis.json": b"expected analysis",
            },
        )

    assert not (output_dir / "catalog.json").exists()
    assert (output_dir / "plot-analysis.json").read_bytes() == b"tampered"


class _RankingProvider:
    provider_id = "one-click-ranking-fixture"
    provider_build_id = "one-click-ranking-fixture-v1"

    def search(self, query, params):
        return {
            "hits": [
                {"rank": 1, "title": "无本地材料", "url": "https://example.test/missing"},
                {"rank": 2, "title": "测试仙途", "url": "https://example.test/test"},
            ],
        }


def test_famous_novel_workflow_ranks_resolves_and_binds_source_before_research(tmp_path):
    chapters = tmp_path / "chapters"
    chapters.mkdir()
    (chapters / "第1章 入山.txt").write_text("第1章 入山\n\n林舟进入山门。", encoding="utf-8")
    (chapters / "第2章 拜师.txt").write_text("第2章 拜师\n\n林舟拜长老为师。", encoding="utf-8")
    spec = {
        "genre": "仙侠",
        "ranking": {"queries": ["仙侠代表作"], "pages_per_query": 1, "limit": 10},
        "defaults": {
            "evidence": {"tier": "A", "access_legitimacy": "AUTHORIZED"},
            "limits": {"max_chapters": 10, "max_bytes": 100_000},
            "strict_order": True,
        },
        "source_catalog": [
            {
                "candidate_titles": ["《测试仙途》"],
                "source": {"kind": "directory", "path": str(chapters)},
            }
        ],
    }
    result = run_famous_novel_research(
        spec,
        tmp_path / "run",
        providers=[_RankingProvider()],
        collector=OpenAICollectionAssessor(
            OpenAIResponsesClient(
                model="small-model-snapshot",
                api_key="test-key",
                transport=_collection_transport("collector"),
            ),
            role="COLLECTOR",
        ),
        reviewer=OpenAICollectionAssessor(
            OpenAIResponsesClient(
                model="large-model-snapshot",
                api_key="test-key",
                transport=_collection_transport("reviewer"),
            ),
            role="REVIEWER",
        ),
        extractor_client=OpenAIResponsesClient(
            model="extractor-model-snapshot", api_key="test-key", transport=_extraction_transport
        ),
        analyst_client=OpenAIResponsesClient(
            model="analyst-model-snapshot", api_key="test-key", transport=_analysis_transport
        ),
        repo_root=repo_root(),
        now=NOW,
    )

    selection = result["export"]["origin_request"]["search_constraints"]["novel_selection"]
    assert result["source_resolution"]["candidate_rank"] == 2
    assert selection["resolution_id"] == result["source_resolution"]["resolution_id"]
    assert selection["source_spec_hash"] == result["ingestion"]["input_spec_hash"]
    validate_all(result["catalog"], result["store"])

    request = result["catalog"].get("ResearchRequest", result["bundle"]["request_id"])
    request["search_constraints"]["novel_selection"]["candidate_rank"] = 1
    with pytest.raises(ValidationError, match="E-NOVEL-SOURCE-BIND"):
        validate_evidence(result["catalog"], result["store"])


def test_famous_site_workflow_exports_ranking_and_fetch_attempt_raw_closure(tmp_path):
    spec = {
        "genre": "仙侠",
        "ranking": {"queries": ["仙侠代表作"], "pages_per_query": 1, "limit": 10},
        "defaults": {
            "evidence": {"tier": "A", "access_legitimacy": "AUTHORIZED"},
            "limits": {"max_chapters": 10, "max_bytes": 100_000},
            "strict_order": True,
        },
        "source_catalog": [
            {
                "candidate_titles": ["《测试仙途》"],
                "source": {
                    "kind": "site",
                    "index_url": "https://novel.example/index",
                    "chapter_url_pattern": r"/chapter/\d+$",
                },
            }
        ],
    }

    class Fetcher:
        def fetch(self, url):
            pages = {
                "https://novel.example/index": (
                    '<a href="/chapter/1">第1章 入山</a>'
                    '<a href="/chapter/2">第2章 拜师</a>'
                ).encode(),
                "https://novel.example/chapter/1": (
                    "<html><body><h1>第1章 入山</h1><p>林舟进入山门。</p></body></html>"
                ).encode(),
                "https://novel.example/chapter/2": (
                    "<html><body><h1>第2章 拜师</h1><p>林舟拜长老为师。</p></body></html>"
                ).encode(),
            }
            return pages[url], "text/html; charset=utf-8", 200, url

    result = run_famous_novel_research(
        spec,
        tmp_path / "run",
        providers=[_RankingProvider()],
        collector=OpenAICollectionAssessor(
            OpenAIResponsesClient(
                model="small-model-snapshot",
                api_key="test-key",
                transport=_collection_transport("collector"),
            ),
            role="COLLECTOR",
        ),
        reviewer=OpenAICollectionAssessor(
            OpenAIResponsesClient(
                model="large-model-snapshot",
                api_key="test-key",
                transport=_collection_transport("reviewer"),
            ),
            role="REVIEWER",
        ),
        extractor_client=OpenAIResponsesClient(
            model="extractor-model-snapshot", api_key="test-key", transport=_extraction_transport
        ),
        analyst_client=OpenAIResponsesClient(
            model="analyst-model-snapshot", api_key="test-key", transport=_analysis_transport
        ),
        repo_root=repo_root(),
        now=NOW,
        fetcher=Fetcher(),
    )

    expected = {
        window["raw_response_artifact_id"]
        for window in result["ranking"]["provider_windows"]
        if window["raw_response_artifact_id"]
    }
    checkpoint = json.loads(
        result["store"].get(result["ingestion"]["checkpoint_artifact_id"]).decode("utf-8")
    )
    expected.update(checkpoint["site_attempt_receipt_ids"])
    for receipt_artifact_id in checkpoint["site_attempt_receipt_ids"]:
        receipt = json.loads(result["store"].get(receipt_artifact_id).decode("utf-8"))
        if receipt["raw_artifact_id"]:
            expected.add(receipt["raw_artifact_id"])

    manifest_ids = {
        item["artifact_id"] for item in result["export"]["artifact_manifest"]
    }
    catalog_data = {
        kind: records for kind, records in result["catalog"].by_type.items() if records
    }
    assert expected <= manifest_ids
    assert expected <= live_artifact_ids(catalog_data)
    validate_all(result["catalog"], result["store"])
