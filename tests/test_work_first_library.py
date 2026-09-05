"""Selected-work composition uses the ordinary planning/source/execution boundary."""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_library as lib
from test_research_library import setup
from test_phase_minus1_planning import _stage_planning_run


@pytest.mark.parametrize("with_leads", [False, True])
def test_preparation_registers_a_managed_execution(setup, tmp_path, with_leads):
    library, research, _, sealed, _, source_options = setup
    _, _, compiled = _stage_planning_run(tmp_path)
    options = {"key": "selected", "planning_root": compiled.planning_root}
    if with_leads:
        options["leads"] = lib.read_json(source_options)["leads"]["path"]
    result = library.prepare_scene_work(research["record_id"], sealed, **options)
    execution = result["execution"]
    assert execution["record"]["mode"] == "MANAGED"
    assert Path(execution["paths"]["work_dir"]).is_dir()
    handoff = lib.read_json(Path(execution["record"]["handoff"]["path"]))
    assert bool(handoff["motivating_lead_ids"]) == with_leads
    assert handoff["localization"]["execution_scope"] == "FULL_WORK"
    assert result["native_state"] == "HANDOFF_READY"
    assert library.prepare_scene_work(research["record_id"], sealed, **options) == result
    view = library.research_status(research["record_id"], planning_root=compiled.planning_root)
    item = next(i for i in view["items"] if i["kind"] == "execution")
    assert item["planning_match"] == "VALIDATED"
    assert item["next_action"] == "FREEZE_AND_EXECUTE_NATIVE"
    source_item = next(i for i in view["items"] if i["kind"] == "source")
    assert source_item["next_action"] == "CHECK_LINKED_EXECUTIONS"
    assert result["commands"]["execute"][-1] == execution["record"]["work_dir"]
    assert "execute-handoff" in result["commands"]["execute"]
    assert not list(Path(execution["record"]["work_dir"]).rglob("pending.json"))


def test_preparation_resumes_after_source_registration_before_allocation(setup, tmp_path, monkeypatch):
    library, research, _, sealed, *_ = setup
    _, _, compiled = _stage_planning_run(tmp_path)
    allocate = library.allocate_execution
    def interruption(*args, **kwargs):
        raise OSError("interrupted after source registration")
    monkeypatch.setattr(library, "allocate_execution", interruption)
    with pytest.raises(OSError, match="interrupted"):
        library.prepare_scene_work(research["record_id"], sealed, key="selected",
                                   planning_root=compiled.planning_root)
    before, _ = library._inventory()
    assert not any(r["kind"] == "execution" for r in before.values())
    sources_before = {rid for rid, r in before.items() if r["kind"] == "source"}
    monkeypatch.setattr(library, "allocate_execution", allocate)
    result = library.prepare_scene_work(research["record_id"], sealed, key="selected",
                                       planning_root=compiled.planning_root)
    after, _ = library._inventory()
    assert {rid for rid, r in after.items() if r["kind"] == "source"} == sources_before
    assert result["execution"]["record_id"] in after


def test_invalid_planning_or_source_never_allocates_execution(setup, tmp_path):
    library, research, _, sealed, *_ = setup
    with pytest.raises((lib.PipelineError, OSError)):
        library.prepare_scene_work(research["record_id"], sealed, key="selected",
                                   planning_root=tmp_path / "missing")
    _, _, compiled = _stage_planning_run(tmp_path)
    chapter = next((sealed / "chapters").glob("*.txt"))
    chapter.write_text(chapter.read_text() + "changed")
    with pytest.raises(lib.PipelineError):
        library.prepare_scene_work(research["record_id"], sealed, key="selected",
                                   planning_root=compiled.planning_root)
    records, _ = library._inventory()
    assert not any(r["kind"] == "execution" for r in records.values())
