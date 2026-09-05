from __future__ import annotations

import json
import pathlib
import threading
import time
from typing import Any, Callable

import pytest

from xhnovel_pipeline.canonical import canonical_dumps
from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.generic_agent_files import (
    GenericAgentFileExecutor,
    GenericAgentResponsesPending,
)
from xhnovel_pipeline.generic_extraction import (
    GenericExtractionPartial,
    run_generic_corpus_workflow,
    validate_generic_work_dir,
)
from xhnovel_pipeline.model_api import (
    API_EXECUTOR_KIND,
    OPENAI_RESPONSES_FORMAT,
    ModelAttemptTrace,
    ModelCallResult,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOW = "2026-09-03T12:00:00Z"


class CountingApiExecutor:
    executor_kind = API_EXECUTOR_KIND
    response_format = OPENAI_RESPONSES_FORMAT
    executor_build_id = "test-openai-responses-v1"
    endpoint = "https://api.openai.com/v1/responses"
    timeout = 1.0
    max_attempts = 1

    def __init__(
        self,
        answer: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        model: str = "test-model-snapshot",
        reverse_delay: bool = False,
    ) -> None:
        self.model = model
        self.answer = answer
        self.reverse_delay = reverse_delay
        self.calls = 0
        self._lock = threading.Lock()

    def json_request_bytes(
        self,
        *,
        instructions: str,
        input_value: dict[str, Any],
        schema_name: str,
        schema: dict[str, Any],
    ) -> bytes:
        return canonical_dumps(
            {
                "model": self.model,
                "instructions": instructions,
                "input": canonical_dumps(input_value).decode("utf-8"),
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": schema,
                    }
                },
                "store": False,
            }
        )

    def generate_json(
        self,
        *,
        instructions: str,
        input_value: dict[str, Any],
        schema_name: str,
        schema: dict[str, Any],
    ) -> ModelCallResult:
        with self._lock:
            self.calls += 1
        ordinal = int(input_value["unit"]["ordinal"])
        if self.reverse_delay:
            time.sleep(max(0, 6 - ordinal) * 0.002)
        else:
            time.sleep(ordinal * 0.001)
        value = self.answer(input_value)
        request_bytes = self.json_request_bytes(
            instructions=instructions,
            input_value=input_value,
            schema_name=schema_name,
            schema=schema,
        )
        response_bytes = canonical_dumps(
            {"id": f"response-{input_value['unit']['unit_id']}", "output": value}
        )
        return ModelCallResult(
            value=value,
            request_bytes=request_bytes,
            response_bytes=response_bytes,
            response_id=f"response-{input_value['unit']['unit_id']}",
            attempts=(
                ModelAttemptTrace(
                    ordinal=1,
                    status="SUCCEEDED",
                    http_status=200,
                    response_bytes=response_bytes,
                    error_code=None,
                    error_message=None,
                    response_id=f"response-{input_value['unit']['unit_id']}",
                    usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                ),
            ),
        )


def _write_novel(tmp_path: pathlib.Path) -> dict[str, Any]:
    chapters = tmp_path / "chapters"
    chapters.mkdir(parents=True)
    chapters.joinpath("0001.txt").write_text(
        "第一章 起点\n"
        + "甲" * 60
        + "乌坦城位于加玛帝国"
        + "乙" * 180
        + "\n",
        encoding="utf-8",
    )
    chapters.joinpath("0002.txt").write_text(
        "第二章 沙海\n"
        + "丙" * 55
        + "蛇人族世代生活在塔戈尔大沙漠"
        + "丁" * 180
        + "\n",
        encoding="utf-8",
    )
    return {
        "source": {
            "kind": "directory",
            "path": str(chapters.resolve()),
            "title": "通用抽取测试小说",
            "author": "fixture",
            "language": "zh",
        },
        "rights": {
            "basis": "USER_AUTHORIZED_LOCAL_COPY",
            "may_store_full_text": True,
            "may_send_to_external_model": True,
            "may_export_excerpts": False,
        },
        "source_quality": {
            "edition_status": "USER_VERIFIED_COPY",
            "textual_completeness": "COMPLETE",
        },
        "limits": {"max_chapters": 10, "max_bytes": 1_000_000},
        "strict_order": False,
    }


def _write_profile(
    profiles_root: pathlib.Path,
    slug: str,
    *,
    profile_id: str,
    kind: str,
    record_version: int = 1,
) -> pathlib.Path:
    profile_dir = profiles_root / slug
    profile_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "profile_manifest_version": "extraction-profile/v1",
        "profile_id": profile_id,
        "profile_version": "1.0.0",
        "prompt": "prompt.md",
        "payload_schema": "payload.schema.json",
        "schema_name": slug.replace("-", "_") + "_v1",
        "unit_policy": {"id": "sliding-text/v1", "target_chars": 100, "overlap_chars": 50},
        "limits": {
            "max_records_per_unit": 16,
            "max_payload_bytes_per_record": 4096,
            "max_workers": 4,
        },
        "evidence_policy": {
            "by_kind": {
                kind: {
                    "required_groups": [["/name"]],
                    "exempt_paths": ["/kind"],
                }
            }
        },
        "reduction": {
            "reducer_id": "exact-payload-dedup/v1",
            "config": {"record_version": record_version},
        },
    }
    profile_dir.joinpath("profile.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    profile_dir.joinpath("prompt.md").write_text(
        f"Extract explicit {kind} records only.\n", encoding="utf-8"
    )
    profile_dir.joinpath("payload.schema.json").write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "name"],
                "properties": {
                    "kind": {"const": kind},
                    "name": {"type": "string", "minLength": 1},
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return profile_dir


def _mention_answer(
    *,
    profile_id: str,
    kind: str,
    phrase: str,
    name: str,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def answer(input_value: dict[str, Any]) -> dict[str, Any]:
        if input_value["profile"]["profile_id"] != profile_id:
            return {"records": []}
        for span in input_value["unit"]["source_spans"]:
            index = span["untrusted_text"].find(phrase)
            if index >= 0:
                return {
                    "records": [
                        {
                            "payload": {"kind": kind, "name": name},
                            "evidence_bindings": [
                                {
                                    "paths": ["/name"],
                                    "source_spans": [
                                        {
                                            "segment_id": span["segment_id"],
                                            "start": span["start"] + index,
                                            "end": span["start"] + index + len(phrase),
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                }
        return {"records": []}

    return answer


def _combined_answer(input_value: dict[str, Any]) -> dict[str, Any]:
    profile_id = input_value["profile"]["profile_id"]
    if profile_id == "test.geography":
        return _mention_answer(
            profile_id=profile_id,
            kind="PLACE",
            phrase="乌坦城位于加玛帝国",
            name="乌坦城",
        )(input_value)
    if profile_id == "test.race":
        return _mention_answer(
            profile_id=profile_id,
            kind="RACE",
            phrase="蛇人族",
            name="蛇人族",
        )(input_value)
    return {"records": []}


def test_whole_book_generic_pipeline_reuses_ingestion_and_cached_extraction(
    tmp_path: pathlib.Path,
) -> None:
    spec = _write_novel(tmp_path / "novel")
    profiles_root = tmp_path / "profiles"
    _write_profile(
        profiles_root,
        "geography",
        profile_id="test.geography",
        kind="PLACE",
    )
    _write_profile(profiles_root, "race", profile_id="test.race", kind="RACE")
    work_dir = tmp_path / "work"

    first_executor = CountingApiExecutor(_combined_answer)
    first = run_generic_corpus_workflow(
        spec,
        work_dir,
        profile_ref="geography",
        profiles_root=profiles_root,
        executor=first_executor,
        root=ROOT,
        now=NOW,
    )
    assert first_executor.calls == first.extraction.run["unit_count"]
    assert first.extraction.run["coverage"]["text_coverage"] == "FULL"
    assert first.extraction.run["coverage"]["semantic_coverage"] == "UNMEASURED"
    assert first.extraction.run["observation_count"] >= 2  # overlap produces duplicate observations
    assert first.corpus_snapshot["corpus_record_count"] == 1
    assert first.corpus_snapshot["semantic_assurance"] == "UNQUALIFIED"

    cached_executor = CountingApiExecutor(_combined_answer)
    cached = run_generic_corpus_workflow(
        spec,
        work_dir,
        profile_ref="geography",
        profiles_root=profiles_root,
        executor=cached_executor,
        root=ROOT,
        now="2026-09-03T13:00:00Z",
    )
    assert cached_executor.calls == 0
    assert cached.extraction.reused_extraction is True
    assert cached.extraction.run == first.extraction.run
    assert cached.corpus_snapshot == first.corpus_snapshot

    race_executor = CountingApiExecutor(_combined_answer)
    race = run_generic_corpus_workflow(
        spec,
        work_dir,
        profile_ref="race",
        profiles_root=profiles_root,
        executor=race_executor,
        root=ROOT,
        now=NOW,
    )
    assert race_executor.calls == race.extraction.run["unit_count"]
    assert race.extraction.snapshot["text_snapshot_id"] == first.extraction.snapshot["text_snapshot_id"]
    assert race.extraction.ingestion["ingestion_run_id"] == first.extraction.ingestion["ingestion_run_id"]
    assert race.corpus_snapshot["corpus_record_count"] == 1

    assert validate_generic_work_dir(
        spec,
        work_dir,
        profile_ref="geography",
        profiles_root=profiles_root,
        root=ROOT,
        now=NOW,
    )
    assert validate_generic_work_dir(
        spec,
        work_dir,
        profile_ref="race",
        profiles_root=profiles_root,
        root=ROOT,
        now=NOW,
    )


def test_reducer_only_profile_change_does_not_call_model(tmp_path: pathlib.Path) -> None:
    spec = _write_novel(tmp_path / "novel")
    profiles_root = tmp_path / "profiles"
    profile_dir = _write_profile(
        profiles_root,
        "geography",
        profile_id="test.geography",
        kind="PLACE",
        record_version=1,
    )
    work_dir = tmp_path / "work"
    first_executor = CountingApiExecutor(_combined_answer)
    first = run_generic_corpus_workflow(
        spec,
        work_dir,
        profile_ref="geography",
        profiles_root=profiles_root,
        executor=first_executor,
        root=ROOT,
        now=NOW,
    )

    manifest_path = profile_dir / "profile.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["reduction"]["config"]["record_version"] = 2
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    second_executor = CountingApiExecutor(_combined_answer)
    second = run_generic_corpus_workflow(
        spec,
        work_dir,
        profile_ref="geography",
        profiles_root=profiles_root,
        executor=second_executor,
        root=ROOT,
        now="2026-09-03T14:00:00Z",
    )
    assert second_executor.calls == 0
    assert second.extraction.reused_extraction is True
    assert second.extraction.run["extraction_run_id"] == first.extraction.run["extraction_run_id"]
    assert second.reduction_run["reduction_run_id"] != first.reduction_run["reduction_run_id"]
    assert second.corpus_records[0]["schema_version"] == "corpus-record/v2"
    validated = validate_generic_work_dir(
        spec,
        work_dir,
        profile_ref="geography",
        profiles_root=profiles_root,
        root=ROOT,
        now=NOW,
    )
    assert len(validated) == 2


def test_worker_completion_order_does_not_change_bytes(tmp_path: pathlib.Path) -> None:
    spec = _write_novel(tmp_path / "novel")
    profiles_root = tmp_path / "profiles"
    _write_profile(
        profiles_root,
        "geography",
        profile_id="test.geography",
        kind="PLACE",
    )
    first = run_generic_corpus_workflow(
        spec,
        tmp_path / "work-a",
        profile_ref="geography",
        profiles_root=profiles_root,
        executor=CountingApiExecutor(_combined_answer, reverse_delay=False),
        root=ROOT,
        now=NOW,
    )
    second = run_generic_corpus_workflow(
        spec,
        tmp_path / "work-b",
        profile_ref="geography",
        profiles_root=profiles_root,
        executor=CountingApiExecutor(_combined_answer, reverse_delay=True),
        root=ROOT,
        now=NOW,
    )
    assert first.extraction.paths.unit_results_path.read_bytes() == second.extraction.paths.unit_results_path.read_bytes()
    assert first.extraction.paths.attempts_path.read_bytes() == second.extraction.paths.attempts_path.read_bytes()
    assert first.extraction.paths.observations_path.read_bytes() == second.extraction.paths.observations_path.read_bytes()
    assert first.extraction.run["extraction_run_hash"] == second.extraction.run["extraction_run_hash"]
    assert first.corpus_path.read_bytes() == second.corpus_path.read_bytes()


def test_evidence_missing_and_out_of_unit_are_rejected(tmp_path: pathlib.Path) -> None:
    spec = _write_novel(tmp_path / "novel")
    profiles_root = tmp_path / "profiles"
    _write_profile(
        profiles_root,
        "geography",
        profile_id="test.geography",
        kind="PLACE",
    )

    def missing_evidence(input_value: dict[str, Any]) -> dict[str, Any]:
        return {
            "records": [
                {
                    "payload": {"kind": "PLACE", "name": "乌坦城"},
                    "evidence_bindings": [
                        {
                            "paths": ["/kind"],
                            "source_spans": [
                                {
                                    "segment_id": input_value["unit"]["source_spans"][0]["segment_id"],
                                    "start": input_value["unit"]["source_spans"][0]["start"],
                                    "end": input_value["unit"]["source_spans"][0]["start"] + 1,
                                }
                            ],
                        }
                    ],
                }
            ]
        }

    with pytest.raises(GenericExtractionPartial) as caught:
        run_generic_corpus_workflow(
            spec,
            tmp_path / "bad-evidence",
            profile_ref="geography",
            profiles_root=profiles_root,
            executor=CountingApiExecutor(missing_evidence),
            root=ROOT,
            now=NOW,
        )
    assert {entry["error_code"] for entry in caught.value.failed.values()} == {
        "E-GENERIC-EVIDENCE-MISSING"
    }


def test_agent_files_materialize_all_tasks_resume_and_detect_tampering(
    tmp_path: pathlib.Path,
) -> None:
    spec = _write_novel(tmp_path / "novel")
    profiles_root = tmp_path / "profiles"
    _write_profile(
        profiles_root,
        "geography",
        profile_id="test.geography",
        kind="PLACE",
    )
    work_dir = tmp_path / "agent-work"
    agent_root = tmp_path / "agent-files"
    executor = GenericAgentFileExecutor(agent_root, model_label="test-agent")
    with pytest.raises(GenericAgentResponsesPending) as pending:
        run_generic_corpus_workflow(
            spec,
            work_dir,
            profile_ref="geography",
            profiles_root=profiles_root,
            executor=executor,
            root=ROOT,
            now=NOW,
        )
    assert pending.value.pending_count >= 2
    first_task = json.loads(pending.value.pending[0].task_path.read_text(encoding="utf-8"))
    assert first_task["input"]["profile"]["evidence_policy"] == {
        "by_kind": {
            "PLACE": {
                "required_groups": [["/name"]],
                "exempt_paths": ["/kind"],
            }
        }
    }
    assert "RFC 6901 JSON Pointer" in first_task["instructions"]
    for item in pending.value.pending:
        item.answer_path.write_text('{"records": []}\n', encoding="utf-8")

    completed = run_generic_corpus_workflow(
        spec,
        work_dir,
        profile_ref="geography",
        profiles_root=profiles_root,
        executor=GenericAgentFileExecutor(agent_root, model_label="test-agent"),
        root=ROOT,
        now=NOW,
    )
    assert completed.extraction.run["unit_count"] == pending.value.pending_count
    assert completed.extraction.run["observation_count"] == 0

    tamper_work = tmp_path / "tamper-work"
    tamper_root = tmp_path / "tamper-agent"
    with pytest.raises(GenericAgentResponsesPending) as tamper_pending:
        run_generic_corpus_workflow(
            spec,
            tamper_work,
            profile_ref="geography",
            profiles_root=profiles_root,
            executor=GenericAgentFileExecutor(tamper_root, model_label="test-agent"),
            root=ROOT,
            now=NOW,
        )
    for item in tamper_pending.value.pending:
        item.answer_path.write_text('{"records": []}\n', encoding="utf-8")
    first_task = tamper_pending.value.pending[0].task_path
    first_task.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValidationError, match="E-GENERIC-AGENT-TAMPER"):
        run_generic_corpus_workflow(
            spec,
            tamper_work,
            profile_ref="geography",
            profiles_root=profiles_root,
            executor=GenericAgentFileExecutor(tamper_root, model_label="test-agent"),
            root=ROOT,
            now=NOW,
        )


def test_completed_corpus_validates_offline_alongside_pending_extraction(
    tmp_path: pathlib.Path,
) -> None:
    spec = _write_novel(tmp_path / "novel")
    profiles_root = tmp_path / "profiles"
    _write_profile(profiles_root, "geography", profile_id="test.geography", kind="PLACE")
    work_dir = tmp_path / "work"
    completed = run_generic_corpus_workflow(
        spec, work_dir, profile_ref="geography", profiles_root=profiles_root,
        executor=CountingApiExecutor(_combined_answer), root=ROOT, now=NOW,
    )
    with pytest.raises(GenericAgentResponsesPending):
        run_generic_corpus_workflow(
            spec, work_dir, profile_ref="geography", profiles_root=profiles_root,
            executor=GenericAgentFileExecutor(tmp_path / "agent-files", model_label="pending"),
            root=ROOT, now=NOW,
        )
    (completed.extraction.paths.extraction_root.parent / "XBLD-unpublished").mkdir()
    pathlib.Path(spec["source"]["path"]).rename(tmp_path / "archived-chapters")
    validated = validate_generic_work_dir(
        spec, work_dir, profile_ref="geography", profiles_root=profiles_root,
        root=ROOT, now=NOW,
    )
    assert [result.corpus_snapshot for result in validated] == [completed.corpus_snapshot]

    # A completed reduction cannot be hidden by deleting its extraction marker.
    completed.extraction.paths.extraction_run_path.unlink()
    with pytest.raises(ValidationError, match="E-GENERIC-VALIDATE: missing"):
        validate_generic_work_dir(
            spec, work_dir, profile_ref="geography", profiles_root=profiles_root,
            root=ROOT, now=NOW,
        )


def test_offline_validation_rejects_corrupt_ingestion_cas(tmp_path: pathlib.Path) -> None:
    spec = _write_novel(tmp_path / "novel")
    completed = run_generic_corpus_workflow(
        spec, tmp_path / "work", profile_ref="geography-v1",
        executor=CountingApiExecutor(lambda _: {"records": []}), root=ROOT, now=NOW,
    )
    artifact_id = completed.extraction.ingestion["input_spec_artifact_id"]
    completed.extraction.store._path(artifact_id).write_bytes(b"corrupt")
    with pytest.raises(ValidationError, match="E-ARTIFACT-CORRUPT"):
        validate_generic_work_dir(
            spec, tmp_path / "work", profile_ref="geography-v1", root=ROOT, now=NOW,
        )


@pytest.mark.parametrize("failure", [TypeError("executor bug"), ValidationError("E-ARTIFACT-CORRUPT", "corrupt")])
def test_executor_integrity_and_programming_failures_propagate(
    tmp_path: pathlib.Path, failure: Exception,
) -> None:
    spec = _write_novel(tmp_path / "novel")

    def fail(_: dict[str, Any]) -> dict[str, Any]:
        raise failure

    with pytest.raises(type(failure), match=str(failure)) as caught:
        run_generic_corpus_workflow(
            spec, tmp_path / "work", profile_ref="geography-v1",
            executor=CountingApiExecutor(fail), root=ROOT, now=NOW,
        )
    assert caught.value is failure


def test_domain_payload_state_cannot_promote_observation(tmp_path: pathlib.Path) -> None:
    spec = _write_novel(tmp_path / "novel")
    profiles_root = tmp_path / "profiles"
    profile_dir = _write_profile(
        profiles_root, "geography", profile_id="test.geography", kind="PLACE",
    )
    schema_path = profile_dir / "payload.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["properties"].update({
        "status": {"type": "string"},
        "verification": {"type": "string"},
        "observation_id": {"type": "string"},
    })
    schema_path.write_text(json.dumps(schema), encoding="utf-8")

    def answer(input_value: dict[str, Any]) -> dict[str, Any]:
        value = _combined_answer(input_value)
        for record in value["records"]:
            record["payload"].update({
                "status": "ACTIVE", "verification": "CHECKED", "observation_id": "domain-id",
            })
            record["evidence_bindings"][0]["paths"] += [
                "/status", "/verification", "/observation_id",
            ]
        return value

    completed = run_generic_corpus_workflow(
        spec, tmp_path / "work", profile_ref="geography", profiles_root=profiles_root,
        executor=CountingApiExecutor(answer), root=ROOT, now=NOW,
    )
    assert completed.extraction.observations
    for observation in completed.extraction.observations:
        assert observation["payload"]["status"] == "ACTIVE"
        assert observation["status"] == "DRAFT"
        assert observation["verification"] == "UNVERIFIED"
        assert observation["observation_id"] != "domain-id"
