"""Deterministic Phase 0 acceptance slice through the installed CLI.

The script copies the repository's explicitly licensed complete fixture into an
isolated directory, then exercises the public product boundary:

    prepare-handoff -> execute-handoff (WAITING) -> host answers -> resume
    -> validate all -> SUCCEEDED receipt

It uses a fixed fixture oracle only to fill native agent-file answers.  It is not a
second Scene Scout implementation and is never imported by production runtime code.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


PYTHON = sys.executable
FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "positive"
    / "phase0-vertical-slice"
)

EXPECTED_SCENES = (
    {
        # Source normalization canonicalizes Chinese commas/semicolons before
        # building exact character offsets in SceneWindow spans.
        "phrase": "林舟把青铜令交给苏澜携带,苏澜因此实际持有令牌,但令牌仍属青岚门,她也无权催动。",
        "summary": "苏澜取得青铜令的物理持有，但所有权和使用权没有转移",
        "known": {
            "actors": ["林舟", "苏澜"],
            "action": ["交付携带"],
            "target": ["青铜令"],
            "state_transition": ["苏澜取得物理持有，所有权和使用权未转移"],
            "new_affordances": ["苏澜可携带令牌但不可催动"],
            "mechanic_pressure_point": ["物理持有与所有权、使用权分离"],
        },
    },
    {
        "phrase": "长老确认她守约后解除第一重禁制,只授予开启山门的权限,令牌的所有权仍未转移。",
        "summary": "长老解除部分禁制并授予有限使用权，但未转移令牌所有权",
        "known": {
            "actors": ["长老", "苏澜"],
            "action": ["解除第一重禁制并授权开启山门"],
            "target": ["青铜令"],
            "precondition": ["苏澜守约"],
            "state_transition": ["苏澜获得有限使用权，所有权仍未转移"],
            "new_affordances": ["苏澜可以开启山门"],
            "mechanic_pressure_point": ["部分授权与所有权分离"],
        },
    },
    {
        "phrase": (
            "谷主把阵钥暂交林舟保管,同时以誓印禁止他离开雾谷;"
            "林舟能开启库门,却不能借阵钥越过谷界。"
            "任务结束时,谷主收回阵钥并解除誓印,林舟才恢复离谷的行动空间。"
        ),
        "summary": "林舟保管阵钥并获得开库能力，同时受誓印限制不能离谷",
        "known": {
            "actors": ["谷主", "林舟"],
            "action": ["暂交阵钥并施加誓印"],
            "target": ["阵钥"],
            "state_transition": ["林舟获得开库能力并受到离谷禁制"],
            "new_affordances": ["林舟可以开启库门但不能越过谷界"],
            "persistence": ["任务结束前持续"],
            "mechanic_pressure_point": ["对象能力与角色移动约束并存"],
        },
    },
)

OBSERVATION_FIELDS = (
    "actors",
    "action",
    "target",
    "precondition",
    "state_transition",
    "external_response",
    "immediate_feedback",
    "new_affordances",
    "persistence",
    "mechanic_pressure_point",
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON object: {path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _run(
    output_root: Path,
    label: str,
    args: list[str],
    *,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    env["PYTHONUTF8"] = "1"
    command = [PYTHON, *args]
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    logs = output_root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    _write_json(
        logs / f"{label}.command.json",
        {
            "argv": command,
            "cwd": str(cwd.resolve()),
            "returncode": completed.returncode,
        },
    )
    (logs / f"{label}.stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (logs / f"{label}.stderr.txt").write_text(completed.stderr, encoding="utf-8")
    return completed


def _cli(
    output_root: Path,
    label: str,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    return _run(
        output_root,
        label,
        ["-m", "xhnovel_pipeline.cli", *args],
        cwd=output_root,
    )


def _require_returncode(
    completed: subprocess.CompletedProcess[str],
    expected: int,
    label: str,
) -> None:
    if completed.returncode != expected:
        raise AssertionError(
            f"{label} returned {completed.returncode}, expected {expected}: "
            f"{completed.stderr}"
        )


def _unknown() -> dict[str, Any]:
    return {"status": "UNKNOWN", "values": [], "support_spans": []}


def _known(values: list[str], support: dict[str, Any]) -> dict[str, Any]:
    return {"status": "KNOWN", "values": values, "support_spans": [support]}


def _candidate(scene: dict[str, Any], support: dict[str, Any]) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "summary": scene["summary"],
        "source_spans": [support],
    }
    known = scene["known"]
    for field in OBSERVATION_FIELDS:
        candidate[field] = _known(known[field], support) if field in known else _unknown()
    return candidate


def _answer_tasks(tasks: list[Path]) -> tuple[int, int]:
    found_phrases: set[str] = set()
    candidate_count = 0
    for task_path in tasks:
        task = _read_json(task_path)
        candidates = []
        spans = task["input"]["window"]["source_spans"]
        for scene in EXPECTED_SCENES:
            for span in spans:
                untrusted_text = span["untrusted_text"]
                offset = untrusted_text.find(scene["phrase"])
                if offset < 0:
                    continue
                support = {
                    "segment_id": span["segment_id"],
                    "start": span["start"] + offset,
                    "end": span["start"] + offset + len(scene["phrase"]),
                }
                candidates.append(_candidate(scene, support))
                found_phrases.add(scene["phrase"])
                candidate_count += 1
                break
        answer_path = task_path.parents[1] / task["answer_file"]
        _write_json(answer_path, {"candidates": candidates})
    missing = {scene["phrase"] for scene in EXPECTED_SCENES} - found_phrases
    if missing:
        raise AssertionError(f"fixture oracle could not locate source phrases: {sorted(missing)}")
    return len(tasks), candidate_count


def _tainted_lead_values(preparation: dict[str, Any]) -> list[str]:
    values = []
    for lead in preparation["leads"]:
        hint = lead["scene_hint"]
        values.extend([hint["summary"], hint["why_relevant"]])
        values.extend(item["value"] for item in hint["location_hints"])
        for source in lead["lead_sources"]:
            values.append(source["locator"])
            for optional in ("title", "publisher"):
                if source.get(optional):
                    values.append(source[optional])
    return values


def _assert_no_taint(paths: list[Path], tainted_values: list[str]) -> None:
    for path in paths:
        raw = path.read_bytes()
        for value in tainted_values:
            if value.encode("utf-8") in raw:
                raise AssertionError(f"Lead-only metadata leaked into {path}: {value!r}")


def _history_snapshot(output_root: Path, handoff_path: Path, label: str) -> list[dict[str, Any]]:
    program = """
import json
import pathlib
import sys
from xhnovel_pipeline.phase0_execution import validate_handoff_execution_history

history = validate_handoff_execution_history(pathlib.Path(sys.argv[1]))
print(json.dumps([
    {
        "attempt_id": item.attempt_id,
        "attempt_ordinal": item.attempt_ordinal,
        "state": item.state,
        "event_states": [event["state"] for event in item.events],
        "receipt_status": item.receipt["status"] if item.receipt else None,
    }
    for item in history
], sort_keys=True))
""".strip()
    completed = _run(
        output_root,
        label,
        ["-c", program, str(handoff_path)],
        cwd=output_root,
    )
    _require_returncode(completed, 0, label)
    value = json.loads(completed.stdout)
    if not isinstance(value, list):
        raise AssertionError("execution history probe did not return a list")
    return value


def _validate_fixture(fixture_root: Path) -> dict[str, Any]:
    preparation = _read_json(fixture_root / "preparation-input.json")
    rights = preparation["source_declaration"]["rights"]
    quality = preparation["source_declaration"]["source_quality"]
    assert len(preparation["leads"]) == 3
    assert rights == {
        "basis": "LICENSED",
        "may_store_full_text": True,
        "may_send_to_external_model": True,
        "may_export_excerpts": True,
    }
    assert quality == {
        "edition_status": "USER_VERIFIED_COPY",
        "textual_completeness": "COMPLETE",
    }
    assert preparation["source_declaration"]["source"]["path"] == "novel.txt"
    assert "CC0" in (fixture_root / "RIGHTS.md").read_text(encoding="utf-8")
    assert (fixture_root / "novel.txt").read_text(encoding="utf-8").count("第") >= 3
    attestation_path = fixture_root / "operator-attestation.json"
    assert attestation_path.is_file()
    attestation = _read_json(attestation_path)
    assert attestation["record_kind"] == "OPERATOR_ATTESTATION"
    assert attestation["basis"] == rights["basis"]
    assert attestation["may_store_full_text"] is True
    assert attestation["may_send_to_external_model"] is True
    assert attestation["may_export_excerpts"] == rights["may_export_excerpts"]
    return preparation


def _probe_installation(
    output_root: Path,
    *,
    fixture_root: Path,
    require_wheel: bool,
) -> dict[str, str]:
    program = """
import json
import xhnovel_pipeline
from xhnovel_pipeline.paths import repo_root

print(json.dumps({
    "package_file": xhnovel_pipeline.__file__,
    "data_root": str(repo_root()),
}, sort_keys=True))
""".strip()
    completed = _run(output_root, "00-installation-probe", ["-c", program], cwd=output_root)
    _require_returncode(completed, 0, "installation probe")
    probe = json.loads(completed.stdout)
    if require_wheel:
        checkout = fixture_root.resolve().parents[2]
        package_file = Path(probe["package_file"]).resolve()
        data_root = Path(probe["data_root"]).resolve()
        if checkout == package_file or checkout in package_file.parents:
            raise AssertionError(f"package was imported from source checkout: {package_file}")
        if checkout == data_root or checkout in data_root.parents:
            raise AssertionError(f"package data resolved into source checkout: {data_root}")
        if "site-packages" not in {part.casefold() for part in package_file.parts}:
            raise AssertionError(f"package is not installed under site-packages: {package_file}")
    return probe


def run_acceptance(
    fixture_root: Path,
    output_root: Path,
    *,
    require_wheel: bool,
    without_leads: bool = False,
) -> Path:
    fixture_root = fixture_root.resolve()
    if output_root.exists():
        raise AssertionError(f"refusing to overwrite acceptance output: {output_root}")
    output_root.mkdir(parents=True)
    preparation = _validate_fixture(fixture_root)
    input_root = output_root / "input"
    shutil.copytree(fixture_root, input_root)
    probe = _probe_installation(
        output_root,
        fixture_root=fixture_root,
        require_wheel=require_wheel,
    )

    preparation_path = input_root / "preparation-input.json"
    phase0_root = output_root / "phase0"
    phase0_root.mkdir(parents=True)
    attestation_src = fixture_root / "operator-attestation.json"
    shutil.copy2(attestation_src, phase0_root / "operator-attestation.json")
    attested_preparation = _read_json(preparation_path)
    attested_preparation["source_declaration"].pop("rights", None)
    if without_leads:
        attested_preparation.pop("leads")
    _write_json(preparation_path, attested_preparation)
    standing_attestation_id = _read_json(attestation_src)["attestation_id"]
    prepare_args = (
        "prepare-handoff",
        str(preparation_path),
        "--work-dir",
        str(phase0_root),
    )
    first_prepare = _cli(output_root, "01-prepare-handoff", *prepare_args)
    _require_returncode(first_prepare, 0, "prepare-handoff pass 1")
    handoff_paths = sorted(phase0_root.glob("handoffs/EHO-*/handoff.json"))
    assert len(handoff_paths) == 1
    handoff_path = handoff_paths[0]
    handoff_bytes = handoff_path.read_bytes()

    second_prepare = _cli(output_root, "02-prepare-handoff-replay", *prepare_args)
    _require_returncode(second_prepare, 0, "prepare-handoff replay")
    assert handoff_path.read_bytes() == handoff_bytes
    assert len(list(phase0_root.glob("handoffs/EHO-*/handoff.json"))) == 1

    validate_handoff = _cli(
        output_root,
        "03-validate-handoff",
        "validate-handoff",
        str(handoff_path),
    )
    _require_returncode(validate_handoff, 0, "validate-handoff")

    handoff = _read_json(handoff_path)
    lead_paths = sorted((phase0_root / "leads").glob("RLD-*.json"))
    declaration_paths = sorted(
        (phase0_root / "source-declarations").glob("SDL-*.json")
    )
    build_request_paths = sorted((phase0_root / "build-requests").glob("HBR-*.json"))
    assert len(lead_paths) == (0 if without_leads else 3)
    assert len(declaration_paths) == 1
    assert len(build_request_paths) == 1
    declaration = _read_json(declaration_paths[0])
    attestation = _read_json(phase0_root / "operator-attestation.json")
    assert declaration["operator_attestation_id"] == standing_attestation_id
    assert declaration["rights"] == {
        "basis": attestation["basis"],
        "may_store_full_text": attestation["may_store_full_text"],
        "may_send_to_external_model": attestation["may_send_to_external_model"],
        "may_export_excerpts": attestation["may_export_excerpts"],
    }
    assert len(handoff["motivating_lead_ids"]) == (0 if without_leads else 3)
    assert handoff["motivating_lead_ids"] == sorted(handoff["motivating_lead_ids"])
    assert all(
        set(item) == {"lead_id", "hint_indexes"}
        for item in handoff["localization"]["hint_refs"]
    )
    assert {
        lead["lead_id"] for lead in map(_read_json, lead_paths)
    } == set(handoff["motivating_lead_ids"])
    assert all(
        source["role"] == "LEAD_ONLY"
        for lead in map(_read_json, lead_paths)
        for source in lead["lead_sources"]
    )

    novel_spec_path = handoff_path.parent / handoff["novel_spec"]["path"]
    validation_receipt_path = handoff_path.parent / "validation-receipt.json"
    novel_spec = _read_json(novel_spec_path)
    assert novel_spec["request"]["discovery_brief"] == preparation["brief"][
        "evidence_discovery_brief"
    ]
    tainted_values = _tainted_lead_values(preparation)
    _assert_no_taint(
        [novel_spec_path, handoff_path, validation_receipt_path],
        tainted_values,
    )

    research_root = output_root / "research"
    execute_args = (
        "execute-handoff",
        str(handoff_path),
        "--executor",
        "agent-files",
        "--work-dir",
        str(research_root),
    )
    first_execute = _cli(output_root, "04-execute-waiting", *execute_args)
    _require_returncode(first_execute, 3, "execute-handoff pass 1")
    assert "WAITING_FOR_AGENT" in first_execute.stderr
    pending_manifest = json.loads(first_execute.stdout)
    assert pending_manifest["status"] == "WAITING_FOR_AGENT"
    pending_history = _history_snapshot(
        output_root,
        handoff_path,
        "05-validate-waiting-history",
    )
    assert [item["state"] for item in pending_history] == ["WAITING_FOR_AGENT"]
    assert pending_history[0]["event_states"] == ["STARTED", "WAITING_FOR_AGENT"]

    execution_root = phase0_root / "executions" / handoff["handoff_id"]
    assert len(list((execution_root / "started-markers").glob("*.json"))) == 1
    assert len(list((execution_root / "waiting-events").rglob("*.json"))) == 1
    assert not list((execution_root / "receipts").glob("*.json"))

    task_paths = sorted(
        (research_root / "scene-scout" / "agent-files" / "tasks").glob("*.json")
    )
    assert task_paths
    _assert_no_taint(task_paths, tainted_values)
    for task_path in task_paths:
        task = _read_json(task_path)
        assert task["input"]["discovery_brief"] == preparation["brief"][
            "evidence_discovery_brief"
        ]
    task_count, oracle_candidate_count = _answer_tasks(task_paths)

    second_execute = _cli(output_root, "06-execute-succeeded", *execute_args)
    _require_returncode(second_execute, 0, "execute-handoff pass 2")
    assert "status=SUCCEEDED" in second_execute.stdout
    receipt_paths = sorted((execution_root / "receipts").glob("*.json"))
    assert len(receipt_paths) == 1
    receipt_path = receipt_paths[0]
    receipt_bytes = receipt_path.read_bytes()

    third_execute = _cli(output_root, "07-execute-idempotent", *execute_args)
    _require_returncode(third_execute, 0, "execute-handoff idempotent replay")
    assert receipt_path.read_bytes() == receipt_bytes
    assert len(list((execution_root / "started-markers").glob("*.json"))) == 1
    assert len(list((execution_root / "receipts").glob("*.json"))) == 1

    receipt = _read_json(receipt_path)
    assert receipt["status"] == "SUCCEEDED"
    assert receipt["validate_all"] == "PASS"
    assert receipt["expected_input_spec_hash"] == receipt["actual_input_spec_hash"]
    assert receipt["expected_input_spec_hash"] == handoff["novel_spec"][
        "expected_input_spec_hash"
    ]

    catalog_path = research_root / "research" / receipt["scene_scout_run_id"] / "catalog.json"
    store_root = research_root / "ingestion" / "objects"
    fresh_validate = _cli(
        output_root,
        "08-fresh-validate-all",
        "validate",
        "all",
        str(catalog_path),
        "--store",
        str(store_root),
    )
    _require_returncode(fresh_validate, 0, "fresh-process validate all")
    assert "OK: validate all" in fresh_validate.stdout

    final_history = _history_snapshot(
        output_root,
        handoff_path,
        "09-fresh-validate-execution-history",
    )
    assert [item["state"] for item in final_history] == ["SUCCEEDED"]
    assert final_history[0]["event_states"] == ["STARTED", "WAITING_FOR_AGENT"]
    assert final_history[0]["receipt_status"] == "SUCCEEDED"

    catalog = _read_json(catalog_path)
    candidates = catalog["SceneCandidate"]
    assert len(candidates) == len(EXPECTED_SCENES) == 3
    assert {candidate["summary"] for candidate in candidates} == {
        scene["summary"] for scene in EXPECTED_SCENES
    }
    ingestion_runs = catalog["NovelIngestionRun"]
    assert len(ingestion_runs) == 1
    assert ingestion_runs[0]["input_spec_hash"] == receipt["expected_input_spec_hash"]
    _assert_no_taint(
        [path for path in research_root.rglob("*") if path.is_file()],
        tainted_values,
    )

    report = {
        "report_kind": "NON_AUTHORITATIVE_ACCEPTANCE_REPORT",
        "report_version": 1,
        "acceptance_gate": "PHASE0_DETERMINISTIC_VERTICAL_SLICE",
        "status": "PASS",
        "fixture": {
            "title": "青岚令",
            "completeness": "COMPLETE",
            "rights_basis": "LICENSED",
            "rights_file": _relative(input_root / "RIGHTS.md", output_root),
            "source_file": _relative(input_root / "novel.txt", output_root),
        },
        "installation": {
            "require_wheel": require_wheel,
            "package_file": probe["package_file"],
            "data_root": probe["data_root"],
        },
        "phase0": {
            "brief_count": 1,
            "lead_count": len(lead_paths),
            "source_declaration_count": len(declaration_paths),
            "build_request_count": len(build_request_paths),
            "handoff_count": 1,
            "motivating_lead_count": len(handoff["motivating_lead_ids"]),
            "lead_source_roles": ["LEAD_ONLY"] if lead_paths else [],
            "location_hint_leak_count": 0,
            "cas_object_count": sum(
                1 for path in (phase0_root / "objects").rglob("*") if path.is_file()
            ),
            "handoff_id": handoff["handoff_id"],
            "handoff_path": _relative(handoff_path, output_root),
            "standing_attestation_id": standing_attestation_id,
            "replay_status": "PASS",
        },
        "execution": {
            "executor": "agent-files",
            "pass1_status": "WAITING_FOR_AGENT",
            "pass2_status": receipt["status"],
            "attempt_count": len(final_history),
            "attempt_id": final_history[0]["attempt_id"],
            "event_states": final_history[0]["event_states"],
            "task_count": task_count,
            "oracle_candidate_count": oracle_candidate_count,
            "scene_candidate_count": len(candidates),
            "receipt_path": _relative(receipt_path, output_root),
        },
        "closure": {
            "expected_input_spec_hash": receipt["expected_input_spec_hash"],
            "actual_input_spec_hash": receipt["actual_input_spec_hash"],
            "fresh_validate_all": "PASS",
            "fresh_execution_history_replay": "PASS",
            "catalog_path": _relative(catalog_path, output_root),
            "store_path": _relative(store_root, output_root),
        },
    }
    report_path = output_root / "acceptance-report.json"
    _write_json(report_path, report)
    return report_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-root", type=Path, default=FIXTURE_ROOT)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--require-wheel", action="store_true")
    parser.add_argument("--without-leads", action="store_true",
                        help="research the selected fixture work without any scene Leads")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.output_root is not None:
        report = run_acceptance(
            args.fixture_root,
            args.output_root.resolve(),
            require_wheel=args.require_wheel,
            without_leads=args.without_leads,
        )
        print("OK: Phase 0 deterministic vertical slice")
        print(report)
        return 0
    with tempfile.TemporaryDirectory(prefix="xhnovel-phase0-vertical-") as raw:
        report = run_acceptance(
            args.fixture_root,
            Path(raw) / "acceptance",
            require_wheel=args.require_wheel,
            without_leads=args.without_leads,
        )
        print("OK: Phase 0 deterministic vertical slice")
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
