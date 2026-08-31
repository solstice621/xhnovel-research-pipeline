from __future__ import annotations

import copy
import json
from collections import Counter
from threading import Lock

import pytest

from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.hashing import object_hash
from xhnovel_pipeline.ids import derived_id
from xhnovel_pipeline.model_api import OpenAIResponsesClient
from xhnovel_pipeline.novel_ingest import run_novel_ingestion
from xhnovel_pipeline.novel_workflow import run_novel_research
from xhnovel_pipeline.paths import repo_root
from xhnovel_pipeline.runtime import TEST_NOW as NOW
from xhnovel_pipeline.validate import validate_all, validate_collection, validate_export


RIGHTS = {
    "basis": "USER_AUTHORIZED_LOCAL_COPY",
    "may_store_full_text": True,
    "may_send_to_external_model": True,
    "may_export_excerpts": False,
}
SOURCE_QUALITY = {
    "edition_status": "USER_VERIFIED_COPY",
    "textual_completeness": "COMPLETE",
}


def _response(value, response_id="scene-response", *, usage=None):
    payload = {
        "id": response_id,
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": json.dumps(value, ensure_ascii=False),
                    }
                ],
            }
        ],
    }
    if usage is not None:
        payload["usage"] = usage
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _unknown():
    return {"status": "UNKNOWN", "values": [], "support_spans": []}


def _candidate(span, *, conflicting=False):
    source_span = {
        "segment_id": span["segment_id"],
        "start": span["start"],
        "end": span["end"],
    }
    action = {
        "status": "CONFLICTING" if conflicting else "KNOWN",
        "values": ["触发", "解除"] if conflicting else ["触发"],
        "support_spans": [source_span],
    }
    return {
        "summary": "林舟触发天门机关",
        "source_spans": [source_span],
        "actors": {
            "status": "KNOWN",
            "values": ["林舟"],
            "support_spans": [source_span],
        },
        "action": action,
        "target": {
            "status": "KNOWN",
            "values": ["天门机关"],
            "support_spans": [source_span],
        },
        "precondition": _unknown(),
        "state_transition": _unknown(),
        "external_response": _unknown(),
        "immediate_feedback": _unknown(),
        "new_affordances": _unknown(),
        "persistence": _unknown(),
        "mechanic_pressure_point": _unknown(),
    }


def _empty_transport(url, headers, body, timeout):
    model_input = json.loads(json.loads(body)["input"])
    ordinal = model_input["window"]["ordinal"]
    return 200, {}, _response({"candidates": []}, f"empty-{ordinal}")


def _marker_transport(marker="林舟触发天门机关", *, conflicting=False):
    def transport(url, headers, body, timeout):
        model_input = json.loads(json.loads(body)["input"])
        candidates = []
        for span in model_input["window"]["source_spans"]:
            offset = span["untrusted_text"].find(marker)
            if offset < 0:
                continue
            exact = {
                "segment_id": span["segment_id"],
                "start": span["start"] + offset,
                "end": span["start"] + offset + len(marker),
            }
            candidates.append(_candidate(exact, conflicting=conflicting))
        ordinal = model_input["window"]["ordinal"]
        return (
            200,
            {},
            _response(
                {"candidates": candidates},
                f"scene-{ordinal}",
                usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            ),
        )

    return transport


def _spec(source_path, *, brief="寻找改变角色可行动作空间的场景", quality=None, scout=None):
    return {
        "source": {"kind": "txt", "path": str(source_path), "title": "测试仙途"},
        "rights": dict(RIGHTS),
        "source_quality": copy.deepcopy(quality or SOURCE_QUALITY),
        "request": {"discovery_brief": brief},
        "limits": {"max_chapters": 10, "max_bytes": 2_000_000},
        "scene_scout": scout or {"window_chars": 10_000, "overlap_chars": 1_800},
        "strict_order": False,
    }


def _client(transport, *, max_attempts=1):
    return OpenAIResponsesClient(
        model="scene-scout-model-snapshot",
        api_key="test-key",
        max_attempts=max_attempts,
        transport=transport,
    )


def _run(tmp_path, *, text="第一章 天门\n林舟触发天门机关，山路随之开启。", transport=None, spec_mutator=None):
    source = tmp_path / "book.txt"
    source.write_text(text, encoding="utf-8")
    spec = _spec(source)
    if spec_mutator:
        spec_mutator(spec)
    return run_novel_research(
        spec,
        tmp_path / "run",
        extractor_client=_client(transport or _marker_transport()),
        repo_root=repo_root(),
        now=NOW,
    )


def test_one_click_workflow_exports_only_draft_unverified_scene_candidates(tmp_path):
    result = _run(tmp_path)

    assert len(result["scout"]["candidates"]) == 1
    candidate = result["scout"]["candidates"][0]
    assert candidate["status"] == "DRAFT"
    assert candidate["verification"] == "UNVERIFIED"
    assert candidate["adjudication_status"] == "NOT_REQUIRED"
    assert result["export"]["scene_candidates"] == [candidate]
    assert result["export"]["scene_discovery"]["candidate_count"] == 1
    assert result["catalog"].all("Claim") == []
    assert result["catalog"].all("ExtractionRun") == []
    assert result["catalog"].all("PlotAnalysis") == []
    assert result["catalog"].all("CollectionDecision") == []
    assert result["catalog"].all("CollectionReview") == []
    assert (result["work_dir"] / "scene-scout-run.json").is_file()
    assert (result["work_dir"] / "scene-merge-run.json").is_file()
    assert (result["work_dir"] / "scene-candidates.json").is_file()
    assert not (result["work_dir"] / "plot-analysis.json").exists()
    validate_all(result["catalog"], result["store"])


def test_discovery_brief_is_bound_into_every_model_request(tmp_path):
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = _run(first_dir, transport=_empty_transport)

    def change_brief(spec):
        spec["request"]["discovery_brief"] = "只寻找制度压力造成的能力变化"

    second = _run(second_dir, transport=_empty_transport, spec_mutator=change_brief)
    first_ids = first["scout"]["run"]["model_request_artifact_ids"]
    second_ids = second["scout"]["run"]["model_request_artifact_ids"]
    assert first_ids != second_ids
    for artifact_id in second_ids:
        request = json.loads(second["store"].get(artifact_id).decode("utf-8"))
        model_input = json.loads(request["input"])
        assert model_input["discovery_brief"] == "只寻找制度压力造成的能力变化"


def test_snapshot_rejects_a_different_valid_ingestion_run(tmp_path):
    first_dir = tmp_path / "first"
    first_dir.mkdir()
    result = _run(first_dir, transport=_empty_transport)
    other_source = tmp_path / "other.txt"
    other_source.write_text("第一章 异本\n完全不同的正文。", encoding="utf-8")
    other = run_novel_ingestion(
        {"source": {"kind": "txt", "path": str(other_source)}, "strict_order": False},
        tmp_path / "other-ingestion",
        repo_root=repo_root(),
        now=NOW,
        catalog=result["catalog"],
        store=result["store"],
    )
    result["snapshot"]["ingestion_run_id"] = other["ingestion"]["ingestion_run_id"]

    with pytest.raises(ValidationError, match="E-SNAPSHOT-INGESTION-LINEAGE"):
        validate_collection(result["catalog"], result["store"])


def test_overlap_windows_merge_the_same_exact_source_span_once(tmp_path):
    marker = "林舟触发天门机关"
    body = "甲" * 9_000 + marker + "乙" * 10_000
    result = _run(
        tmp_path,
        text=f"第一章 天门\n{body}",
        transport=_marker_transport(marker),
    )

    assert len(result["scout"]["windows"]) == 3
    assert result["scout"]["merge_run"]["input_candidate_count"] == 2
    assert len(result["scout"]["candidates"]) == 1
    candidate = result["scout"]["candidates"][0]
    assert len(candidate["window_ids"]) == 2
    span = candidate["source_spans"][0]
    segment = result["catalog"].get("Segment", span["segment_id"])
    assert segment["normalized_text"][span["start"] : span["end"]] == marker


def test_overlapping_scout_disagreement_is_persisted_for_adjudication(tmp_path):
    marker = "林舟触发天门机关"

    def transport(url, headers, body, timeout):
        model_input = json.loads(json.loads(body)["input"])
        ordinal = model_input["window"]["ordinal"]
        candidates = []
        for span in model_input["window"]["source_spans"]:
            offset = span["untrusted_text"].find(marker)
            if offset < 0:
                continue
            exact = {
                "segment_id": span["segment_id"],
                "start": span["start"] + offset,
                "end": span["start"] + offset + len(marker),
            }
            candidate = _candidate(exact)
            candidate["action"]["values"] = ["触发" if ordinal == 1 else "解除"]
            candidates.append(candidate)
        return 200, {}, _response({"candidates": candidates}, f"disagree-{ordinal}")

    result = _run(
        tmp_path,
        text=f"第一章 天门\n{'甲' * 9_000}{marker}{'乙' * 10_000}",
        transport=transport,
    )

    candidate = result["scout"]["candidates"][0]
    assert candidate["action"]["status"] == "CONFLICTING"
    assert candidate["action"]["values"] == ["解除", "触发"]
    assert candidate["adjudication_status"] == "NEEDS_ADJUDICATION"


def test_zero_candidates_is_a_successful_legal_result(tmp_path):
    result = _run(tmp_path, transport=_empty_transport)

    assert result["scout"]["run"]["status"] == "SUCCEEDED"
    assert result["scout"]["merge_run"]["input_candidate_count"] == 0
    assert result["scout"]["candidates"] == []
    assert result["export"]["scene_candidates"] == []


def test_completed_research_rerun_is_idempotent_and_makes_no_model_call(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("第一章 天门\n正文。", encoding="utf-8")
    spec = _spec(source)
    calls = 0

    def transport(url, headers, body, timeout):
        nonlocal calls
        calls += 1
        return 200, {}, _response({"candidates": []}, "stable-response")

    work_dir = tmp_path / "run"
    first = run_novel_research(
        spec,
        work_dir,
        extractor_client=_client(transport),
        repo_root=repo_root(),
        now=NOW,
    )
    second = run_novel_research(
        spec,
        work_dir,
        extractor_client=_client(transport),
        repo_root=repo_root(),
        now="2026-08-30T00:00:00Z",
    )

    assert calls == 1
    assert second["scout"]["run"] == first["scout"]["run"]
    assert second["export"] == first["export"]
    assert second["work_dir"] == first["work_dir"]


def test_lead_only_tier_creates_no_windows_or_model_calls(tmp_path):
    calls = 0

    def forbidden_transport(url, headers, body, timeout):
        nonlocal calls
        calls += 1
        raise AssertionError("lead-only text must not be sent to the model")

    def downgrade(spec):
        spec["source_quality"] = {
            "edition_status": "UNOFFICIAL_COPY",
            "textual_completeness": "PARTIAL",
        }

    result = _run(tmp_path, transport=forbidden_transport, spec_mutator=downgrade)

    assert calls == 0
    assert result["scout"]["windows"] == []
    assert result["scout"]["run"]["model_attempt_ids"] == []
    assert result["scout"]["candidates"] == []


def test_rights_gate_blocks_external_model_before_ingestion_or_transport(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("第一章 天门\n正文。", encoding="utf-8")
    spec = _spec(source)
    spec["rights"]["may_send_to_external_model"] = False
    calls = 0

    def transport(url, headers, body, timeout):
        nonlocal calls
        calls += 1
        return 200, {}, _response({"candidates": []})

    with pytest.raises(ValidationError, match="E-RIGHTS-EXTERNAL-MODEL"):
        run_novel_research(
            spec,
            tmp_path / "run",
            extractor_client=_client(transport),
            repo_root=repo_root(),
            now=NOW,
        )
    assert calls == 0
    assert not (tmp_path / "run" / "ingestion").exists()


def test_retry_attempts_and_usage_are_immutable_and_replayable(tmp_path, monkeypatch):
    monkeypatch.setattr("xhnovel_pipeline.model_api.time.sleep", lambda _: None)
    calls = 0

    def transport(url, headers, body, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            return 429, {}, b'{"error":"busy"}'
        return 200, {}, _response(
            {"candidates": []},
            "retry-success",
            usage={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
        )

    source = tmp_path / "book.txt"
    source.write_text("第一章 天门\n正文。", encoding="utf-8")
    result = run_novel_research(
        _spec(source),
        tmp_path / "run",
        extractor_client=_client(transport, max_attempts=2),
        repo_root=repo_root(),
        now=NOW,
    )

    attempts = result["catalog"].all("ModelAttempt")
    assert [item["status"] for item in attempts] == ["RETRYABLE", "SUCCEEDED"]
    assert attempts[1]["retry_of"] == attempts[0]["attempt_id"]
    assert attempts[0]["response_artifact_id"] is not None
    assert result["scout"]["run"]["usage_ledger"] == {
        "input_tokens": 12,
        "output_tokens": 3,
        "total_tokens": 15,
        "attempts_with_unknown_usage": 1,
        "estimated_cost_microusd": None,
    }
    validate_all(result["catalog"], result["store"])


def test_conflicting_observation_requires_adjudication(tmp_path):
    result = _run(tmp_path, transport=_marker_transport(conflicting=True))
    assert result["scout"]["candidates"][0]["adjudication_status"] == "NEEDS_ADJUDICATION"


def test_resume_1000_windows_calls_only_the_one_incomplete_window(
    tmp_path, monkeypatch
):
    import xhnovel_pipeline.scene_scout as scene_scout

    original_builder = scene_scout.build_scene_windows

    def thousand_windows(
        catalog,
        bundle,
        *,
        request_id,
        window_chars=10_000,
        overlap_chars=1_800,
    ):
        base = original_builder(
            catalog,
            bundle,
            request_id=request_id,
            window_chars=window_chars,
            overlap_chars=overlap_chars,
        )[0]
        windows = []
        for ordinal in range(1, 1_001):
            identity = {
                "request_id": request_id,
                "bundle_id": bundle["bundle_id"],
                "ordinal": ordinal,
                "source_spans": copy.deepcopy(base["source_spans"]),
                "window_chars": window_chars,
                "overlap_chars": overlap_chars,
            }
            window_hash = object_hash(identity, omit=())
            windows.append(
                {
                    "schema_version": base["schema_version"],
                    "window_id": derived_id("SceneWindow", {"window_hash": window_hash}),
                    **identity,
                    "text_length": base["text_length"],
                    "window_hash": window_hash,
                }
            )
        return windows

    monkeypatch.setattr(scene_scout, "build_scene_windows", thousand_windows)
    counts = Counter()
    lock = Lock()

    def transport(url, headers, body, timeout):
        model_input = json.loads(json.loads(body)["input"])
        ordinal = model_input["window"]["ordinal"]
        with lock:
            counts[ordinal] += 1
            call_number = counts[ordinal]
        if ordinal == 900 and call_number == 1:
            return 500, {}, b'{"error":"fixture failure"}'
        return 200, {}, _response({"candidates": []}, f"window-{ordinal}-{call_number}")

    source = tmp_path / "book.txt"
    source.write_text("第一章 天门\n正文。", encoding="utf-8")
    spec = _spec(source, scout={"window_chars": 10_000, "overlap_chars": 1_800, "max_workers": 8})
    work_dir = tmp_path / "run"
    with pytest.raises(ValidationError, match="E-SCENE-PARTIAL"):
        run_novel_research(
            spec,
            work_dir,
            extractor_client=_client(transport),
            repo_root=repo_root(),
            now=NOW,
        )

    assert len(counts) == 1_000
    assert counts[900] == 1
    result = run_novel_research(
        spec,
        work_dir,
        extractor_client=_client(transport),
        repo_root=repo_root(),
        now=NOW,
    )

    assert all(counts[index] == 1 for index in range(1, 900))
    assert counts[900] == 2
    assert all(counts[index] == 1 for index in range(901, 1_001))
    assert result["scout"]["run"]["resumed_from_checkpoint"] is True
    attempts_900 = [
        item
        for item in result["catalog"].all("ModelAttempt")
        if item["subject_id"] == result["scout"]["windows"][899]["window_id"]
    ]
    assert [item["status"] for item in attempts_900] == ["FAILED", "SUCCEEDED"]


def test_tampered_scene_candidate_fails_replay_validation(tmp_path):
    result = _run(tmp_path)
    result["scout"]["candidates"][0]["summary"] = "伪造场景"

    with pytest.raises(ValidationError, match="E-(SCENE-REPLAY|ID-BIND)"):
        validate_all(result["catalog"], result["store"])


def test_export_cannot_upgrade_model_backed_auditability_to_full(tmp_path):
    result = _run(tmp_path)
    export = result["export"]
    export["assurance"]["auditability"] = "FULL"
    identity = {key: value for key, value in export.items() if key not in {"export_id", "export_hash"}}
    export["export_id"] = derived_id("EvidenceExport", identity)
    export["export_hash"] = object_hash(export, omit=("export_hash",))

    with pytest.raises(ValidationError, match="E-AUDITABILITY"):
        validate_export(result["catalog"], result["store"])
