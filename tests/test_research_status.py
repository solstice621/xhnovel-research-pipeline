"""Behavioral regressions for host continuation over actual native artifacts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_library as lib
from test_research_library import setup, allocate
from test_phase_minus1_planning import _stage_planning_run, _preparation_input, _write_json
from test_phase0_execution import _agent_factory, _answer_all, NOW
from test_source_acquisition import fixture_config, acq
from xhnovel_pipeline.cli import _emit_pending_manifest
from xhnovel_pipeline.agent_files import AgentResponsesPending
from xhnovel_pipeline.phase0_builder import prepare_handoff_from_input
from xhnovel_pipeline.phase0_execution import execute_evidence_handoff
from xhnovel_pipeline.phase0_planning import validate_planning_compilation, make_research_intake


def _research(tmp_path):
    library = lib.Library.initialize(tmp_path / "library")
    request = _write_json(tmp_path / "request.json", {"goal": "研究物品控制的变化"})
    research = library.new_research(request, key="research", name="研究")
    return library, research["record_id"]


def _files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _waiting(tmp_path, capsys):
    library, rid = _research(tmp_path)
    _, _, compiled = _stage_planning_run(tmp_path)
    source = tmp_path / "book.txt"
    source.write_text("第一章 天门\n林舟触发天门机关，山路随之开启。")
    prepared = prepare_handoff_from_input(
        _write_json(tmp_path / "prepare.json", _preparation_input(compiled.brief, source)),
        tmp_path / "phase0")
    work = tmp_path / "native"
    args = dict(executor="agent-files", extractor_factory=_agent_factory(work), repo_root=lib.ROOT, now=NOW)
    with pytest.raises(AgentResponsesPending) as waiting:
        execute_evidence_handoff(prepared.handoff_path, work, **args)
    _emit_pending_manifest(waiting.value, work)
    capsys.readouterr()
    execution = library.register_external_execution(
        rid, protocol="SCENE", handoff=prepared.handoff_path,
        native_root=tmp_path / "phase0", work_dir=work)
    return library, rid, compiled, prepared, work, execution, args


def test_planning_before_handoff_replays_without_creating_execution(tmp_path):
    library, rid = _research(tmp_path)
    _, _, compiled = _stage_planning_run(tmp_path)
    before = _files(tmp_path)
    assert validate_planning_compilation(compiled.receipt_path, planning_root=compiled.planning_root,
                                         repo_root=lib.ROOT) == compiled.receipt
    view = library.research_status(rid, planning_root=compiled.planning_root)
    assert view["planning"]["validation"] == "VALIDATED"
    assert view["planning"]["scope"]["target_leads"] == 12
    assert view["rights"]["rights"]["basis"] == "FAIR_USE_RESEARCH"
    assert view["items"] == []
    assert _files(tmp_path) == before


def test_verbatim_goal_does_not_require_confirmed_summary():
    goal = "帮我研究玄幻小说中的艳遇情节"
    result = make_research_intake(
        user_goal_verbatim=goal, neutral_goal_text=goal, neutral_goal_origin="USER_VERBATIM_NO_SEEDS",
        explicit_scope={"genres": {"include": ["玄幻"], "exclude": []}, "scope_origin": "USER_EXPLICIT"},
        seeds=[], frozen_at=NOW)
    assert result["neutral_goal_origin"] == "USER_VERBATIM_NO_SEEDS"


def test_stale_report_does_not_override_waiting_and_answer_presence(tmp_path, capsys):
    library, rid, compiled, prepared, work, execution, args = _waiting(tmp_path, capsys)
    reports = tmp_path / "phase0/reports"
    reports.mkdir()
    (reports / "old.md").write_text("136/1646 章，所有执行尚未开始。")
    view = library.research_status(rid, planning_root=compiled.planning_root)
    item = next(i for i in view["items"] if i["kind"] == "external-execution")
    assert item["native_state"] == "WAITING_FOR_AGENT"
    assert item["next_action"] == "CONTINUE_NATIVE_TASKS"
    assert item["planning_match"] == "VALIDATED"
    assert item["tasks"]["answer_files_present"] == 0
    assert item["tasks"]["current_pending_count"] is None
    _answer_all(work)
    item = next(i for i in library.research_status(rid, planning_root=compiled.planning_root)["items"]
                if i["kind"] == "external-execution")
    assert item["native_state"] == "WAITING_FOR_AGENT"  # answers have not been consumed
    assert item["tasks"]["answer_files_present"] > 0
    result = execute_evidence_handoff(prepared.handoff_path, work, **args)
    assert result.status == "SUCCEEDED"
    view = library.research_status(rid, planning_root=compiled.planning_root)
    item = next(i for i in view["items"] if i["kind"] == "external-execution")
    assert item["native_state"] == "SUCCEEDED"
    assert item["next_action"] == "REGISTER_PRODUCT"
    assert item["registered_product_ids"] == []
    product = library.register_product(execution["record_id"], result.receipt_path)
    def current():
        return next(i for i in library.research_status(rid, planning_root=compiled.planning_root)["items"]
                    if i["kind"] == "external-execution")
    assert current()["next_action"] == "WRITE_AND_REGISTER_REPORT"
    report = _write_json(tmp_path / "report.json", {"findings": "host report"})
    library.register_report(rid, report, executions=[execution["record_id"]])
    assert current()["next_action"] == "WRITE_AND_REGISTER_REPORT"  # missing product binding
    library.register_report(rid, report, executions=[execution["record_id"]], products=[product["record_id"]])
    assert current()["next_action"] == "REVIEW_RESEARCH_COVERAGE"


def test_new_brief_cannot_resume_old_tasks(tmp_path, capsys):
    library, rid, compiled, prepared, work, execution, _ = _waiting(tmp_path, capsys)
    # A separately compiled and valid research frame (not a tampered visible file).
    from test_phase_minus1_planning import _stack
    from xhnovel_pipeline.phase0_planning import make_planning_receipt, make_neutral_frame, make_neutral_execution, make_exploration_plan, compile_exploration_brief
    other = tmp_path / "other"
    stack = _stack(other)
    frame = make_neutral_frame(
        intake=stack["intake"], neutral_input=stack["neutral_input"],
        research_question="角色如何从冲突中恢复？", evidence_discovery_brief="寻找冲突后恢复行动的场景。",
        selection_budget={"target_leads": 12, "max_leads_per_work": 3}, frozen_at=NOW)
    execution = make_neutral_execution(
        neutral_input=stack["neutral_input"], neutral_frame=frame, host="pytest",
        isolation_claim="HOST_ISOLATION_UNAVAILABLE", assurance="NOT_PROVEN",
        recorded_at=NOW, store=stack["store"])
    plan = make_exploration_plan(
        intake=stack["intake"], neutral_frame=frame, neutral_execution=execution,
        exploration_seeds=stack["intake"]["seeds"], diversity=stack["plan"]["diversity"], frozen_at=NOW)
    brief = compile_exploration_brief(plan)
    receipt = make_planning_receipt(
        intake=stack["intake"], neutral_input=stack["neutral_input"], neutral_frame=frame,
        neutral_execution=execution, plan=plan, brief=brief, compiled_at=NOW,
        store=stack["store"], repo_root=lib.ROOT)
    other_root = other / "planning"
    _write_json(other_root / "planning-compilation-receipt.json", receipt)
    _write_json(other_root / "exploration-plan.json", plan)
    _write_json(other_root / "exploration-brief.json", brief)
    view = library.research_status(rid, planning_root=other_root)
    assert view["planning"]["validation"] == "VALIDATED"
    item = next(i for i in view["items"] if i["kind"] == "external-execution")
    assert item["native_state"] == "WAITING_FOR_AGENT"
    assert item["planning_match"] == "MISMATCH"
    assert item["next_action"] == "PREPARE_CURRENT_RESEARCH_HANDOFF"


def test_source_integrity_failure_preserves_other_observations(setup):
    library, research, source, sealed, prepared, planning = setup
    allocate(setup)
    library.verify(source["record_id"])
    chapter = next((sealed / "chapters").glob("*.txt"))
    chapter.unlink()
    view = library.research_status(research["record_id"])
    assert view["rights"]["validation"] == "VALIDATED"
    assert any(i["kind"] == "source" and i["validation"] == "UNAVAILABLE" for i in view["items"])
    assert view["issues"]


def test_deleted_acquisition_is_not_resumable_and_book_is_only_material(tmp_path):
    library, rid = _research(tmp_path)
    (tmp_path / "input").mkdir()
    cfg, inputs = fixture_config(tmp_path / "input")
    run = acq.Run.initialize(cfg)
    run.import_local(inputs)
    next((run.root / "chapters").glob("*.txt")).unlink()
    legacy = tmp_path / "legacy"
    book = legacy / "sources/book/book.txt"
    book.parent.mkdir(parents=True)
    book.write_text("第一章\n有保留文本，但本测试没有完整性证明。")
    view = library.research_status(rid, acquisition_root=[run.root], legacy_root=[legacy])
    acquisition = next(i for i in view["items"] if i["kind"] == "acquisition")
    assert acquisition["validation"] == "UNAVAILABLE"
    material = next(i for i in view["items"] if i["kind"] == "legacy-material")
    assert material["validation"] == "NOT_CHECKED"
    assert material["next_action"] == "IMPORT_AND_VERIFY_MATERIAL"


def test_tampered_pending_paths_do_not_escape_workdir(tmp_path, capsys):
    library, rid, compiled, _, work, _, _ = _waiting(tmp_path, capsys)
    path = work / "scene-scout/agent-files/pending.json"
    value = json.loads(path.read_text())
    value["pending"][0]["task"] = "../outside.json"
    _write_json(path, value)
    view = library.research_status(rid, planning_root=compiled.planning_root)
    item = next(i for i in view["items"] if i["kind"] == "external-execution")
    assert item["tasks"]["validation"] == "UNAVAILABLE"
    assert item["next_action"] == "REVIEW_NATIVE_TASK_LOCATORS"


def test_legacy_discovery_does_not_register_or_resume(tmp_path, capsys):
    library, rid, compiled, prepared, _, _, _ = _waiting(tmp_path, capsys)
    before = _files(library.root)
    view = library.research_status(rid, planning_root=compiled.planning_root,
                                   legacy_root=[tmp_path / "phase0"])
    item = next(i for i in view["items"] if i["kind"] == "legacy-handoff")
    assert item["research_binding"] == "UNREGISTERED"
    assert item["attempts"][0]["state"] == "WAITING_FOR_AGENT"
    assert _files(library.root) == before


def test_candidate_lookup_requires_neutral_planning(setup):
    library, research, source, *_ = setup
    view = library.research_status(research["record_id"], work_ref_id=[source["record"]["work_ref_id"]])
    assert view["items"] == []
    assert view["issues"][0]["code"] == "E-HOST-PLANNING-REQUIRED"


def test_changed_visible_planning_copy_is_not_passed_to_preparation(tmp_path):
    library, rid = _research(tmp_path)
    _, _, compiled = _stage_planning_run(tmp_path)
    data = json.loads(compiled.brief_path.read_text())
    data["evidence_discovery_brief"] = "unrelated text"
    _write_json(compiled.brief_path, data)
    view = library.research_status(rid, planning_root=compiled.planning_root)
    assert view["planning"]["validation"] == "UNAVAILABLE"
    assert view["planning"]["error"]["code"] == "E-HOST-PLANNING-COPY"


def test_checkout_launcher_ignores_stale_install_and_returns_native_exit_codes(tmp_path):
    launcher = lib.ROOT / "scripts/xhnovel.py"
    impostor = tmp_path / "xhnovel_pipeline"
    impostor.mkdir()
    (impostor / "__init__.py").write_text("raise RuntimeError('wrong checkout')")
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    doctor = subprocess.run([sys.executable, str(launcher), "doctor"], cwd=tmp_path,
                            env=env, capture_output=True, text=True)
    assert doctor.returncode == 0, doctor.stderr
    assert json.loads(doctor.stdout)["checkout"] == str(lib.ROOT)
    bad = subprocess.run([sys.executable, str(launcher), "nonexistent-command"], cwd=tmp_path,
                         env=env, capture_output=True)
    assert bad.returncode == 2
    no_site = subprocess.run([sys.executable, "-S", str(launcher), "doctor"], cwd=tmp_path,
                             env=env, capture_output=True, text=True)
    assert no_site.returncode == 2
    assert json.loads(no_site.stdout)["issues"]


def test_prepared_source_is_discovered_before_allocation(setup, tmp_path):
    library, research, _, sealed, _, options_path = setup
    _, _, compiled = _stage_planning_run(tmp_path)
    p = compiled.planning_root
    options = lib.read_json(options_path)
    options["brief"] = acq.ref(compiled.brief_path)
    options["planning"] = {"root": str(p), "receipt": acq.ref(compiled.receipt_path)}
    prepared = acq.prepare_source(sealed, _write_json(tmp_path / "options.json", options), p)
    source = library.register_source(sealed, protocol="SCENE", handoff=prepared["handoff_path"], native_root=p)
    before = _files(library.root)
    view = library.research_status(research["record_id"], planning_root=p)
    item = next(i for i in view["items"] if i["kind"] == "source")
    assert item["record_id"] == source["record_id"]
    assert item["research_binding"] == "PREPARED_FOR_SUPPLIED_PLANNING"
    assert item["next_action"] == "ALLOCATE_EXECUTION"
    assert item["handoff"]["path"] == prepared["handoff_path"]
    assert _files(library.root) == before
    library.allocate_execution(research["record_id"], source["record_id"],
                               handoff=prepared["handoff_path"], native_root=p, key="new")
    item = next(i for i in library.research_status(research["record_id"], planning_root=p)["items"]
                if i["kind"] == "source")
    assert item["next_action"] == "CHECK_LINKED_EXECUTIONS"
    assert len(item["execution_record_ids"]) == 1


def test_unlinked_source_in_other_root_is_not_implicitly_selected(setup, tmp_path):
    library, research, source, sealed, prepared, planning = setup
    _, _, compiled = _stage_planning_run(tmp_path)
    view = library.research_status(research["record_id"], planning_root=compiled.planning_root)
    assert view["planning"]["validation"] == "VALIDATED"
    assert view["items"] == []
