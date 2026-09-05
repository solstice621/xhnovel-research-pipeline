from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
import shutil

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_library as lib
from test_source_acquisition import acq, prepared_fixture, write_json, fixture_config, reviewed
from test_phase0_execution import _agent_factory, _answer_all
from test_source_acquisition_observation import sealed_attempt
from test_generic_handoff_execution import _answers
from test_observation_campaign import record
from test_observation_planning import NOW
from xhnovel_pipeline.agent_files import AgentResponsesPending
from xhnovel_pipeline.phase0_execution import execute_evidence_handoff
from xhnovel_pipeline.observation_campaign import execute_campaign_handoff, report_observation_research
from xhnovel_pipeline.errors import ValidationError


@pytest.fixture
def setup(tmp_path):
    library = lib.Library.initialize(tmp_path / "library")
    request = write_json(tmp_path / "request.json", {"goal": "测试研究", "scope": "FULL_WORK"})
    research = library.new_research(request, key="first", name="场景研究")
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    sealed, prepared, planning = prepared_fixture(source_dir)
    source = library.register_source(sealed, protocol="SCENE", handoff=prepared["handoff_path"],
                                     native_root=source_dir / "phase0")
    return library, research, source, sealed, prepared, planning


def allocate(setup, **kwargs):
    library, research, source, sealed, prepared, planning = setup
    return library.allocate_execution(research["record_id"], source["record_id"],
                                      handoff=prepared["handoff_path"], native_root=planning.parent / "phase0",
                                      key="first", **kwargs)


def test_register_and_fixed_multilevel_layout_are_idempotent(setup):
    library, research, source, sealed, prepared, planning = setup
    assert library.register_source(sealed, protocol="SCENE", handoff=prepared["handoff_path"], native_root=planning.parent / "phase0") == source
    execution = allocate(setup)
    assert execution == allocate(setup)
    record = execution["record"]
    expected = library.root / "research" / research["record"]["research_id"] / "works" / source["record"]["work_ref_id"] / sealed.name / record["execution_id"] / "native"
    assert record["work_dir"] == str(expected)
    assert expected.is_dir()
    assert expected.parent.joinpath("reports").is_dir()
    assert library.validate(execution["record_id"])["status"] == "HANDOFF_READY"
    assert not list(library.root.rglob("fulltext*.txt"))
    assert source["record"]["mode"] == "EXTERNAL_REFERENCE"


def test_search_is_bounded_chapter_offsets_not_evidence(setup):
    library, _, source, sealed, *_ = setup
    result = library.search_text(source["record_id"], "第", limit=1)
    assert result["truncated"] and result["next_offset"] == 1
    match = result["matches"][0]
    raw = Path(match["chapter_path"]).read_bytes()
    assert raw[match["byte_start"]:match["byte_end"]].decode() == "第"
    assert raw.decode()[match["codepoint_start"]:match["codepoint_end"]] == "第"
    assert "text" not in match and match["kind"] == "TEXT_MATCH"
    assert library.search_text(source["record_id"], "no matching text")["matches"] == []
    assert library.search_text(source["record_id"], "第", offset=1)["matches"]
    with pytest.raises(ValidationError, match="permission"):
        library.search_text(source["record_id"], "第", include_text=True)


@pytest.mark.parametrize("change", ["chapter", "extra-file", "attestation", "directory"])
def test_source_tampering_never_uses_old_pass_or_index(setup, change):
    library, _, source, sealed, *_ = setup
    assert library.verify(source["record_id"])["outcome"] == "PASS"
    library.reindex()
    if change == "chapter":
        path = next((sealed / "chapters").glob("*.txt"))
        path.write_bytes(path.read_bytes() + b"changed")
    elif change == "extra-file":
        (sealed / "extra.txt").write_text("extra")
    elif change == "attestation":
        (sealed / "provenance/run/operator-attestation.json").write_text("{}")
    else:
        sealed.rename(sealed.parent / ("0" * 64))
    with pytest.raises((ValidationError, acq.AcquisitionError, OSError)):
        library.search_text(source["record_id"], "第")
    indexed = library.reindex()
    assert indexed["issues"]
    assert library.verify(source["record_id"])["outcome"] == "FAIL"


def test_incomplete_acquisition_is_not_a_source_registration(tmp_path):
    library = lib.Library.initialize(tmp_path / "library")
    (tmp_path / "source").mkdir()
    cfg, inputs = fixture_config(tmp_path / "source", count=2)
    (inputs / "0002.txt").unlink()
    run = acq.Run.initialize(cfg)
    run.import_local(inputs)
    with pytest.raises(acq.AcquisitionError):
        acq.seal(run, tmp_path / "sealed", reviewed(run, tmp_path))
    assert library.list_records("source")["records"] == []


def test_records_and_sqlite_are_not_trusted_by_filename(setup):
    library, _, source, *_ = setup
    library.reindex()
    index = library.root / "index/library.sqlite"
    with sqlite3.connect(index) as db:
        db.execute("DELETE FROM entries")
    assert len(library.list_records("source")["records"]) == 1
    index.write_bytes(b"corrupt database")
    assert len(library.list_records("source")["records"]) == 1
    record_file = Path(source["record_path"])
    data = json.loads(record_file.read_bytes())
    data["extra"] = "unrecognized"
    record_file.write_bytes(lib.encoded(data))
    with pytest.raises(ValidationError):
        library.load(source["record_id"])
    assert library.reindex()["issues"]


def test_request_and_library_root_cannot_be_changed(setup, tmp_path):
    library, research, *_ = setup
    request = Path(research["paths"]["research_dir"]) / "request.json"
    request.write_text('{"goal":"changed"}')
    with pytest.raises(ValidationError, match="changed"):
        library.validate(research["record_id"])
    destination = tmp_path / "moved-library"
    library.root.rename(destination)
    with pytest.raises(ValidationError, match="root moved"):
        lib.Library(destination)


def test_symlink_and_path_escape_are_rejected(setup, tmp_path):
    library, *_ = setup
    with pytest.raises(ValidationError):
        library.load("../../outside")
    with pytest.raises(acq.AcquisitionError):
        library.path("../outside")
    symlink = tmp_path / "alias"
    symlink.symlink_to(library.root, target_is_directory=True)
    with pytest.raises(acq.AcquisitionError):
        lib.Library(symlink)
    with pytest.raises(ValidationError):
        allocate(setup, work_dir=library.root / "wrong-path")


def test_different_research_reuses_bytes_but_gets_separate_execution(setup, tmp_path):
    from test_phase0_builder import _input
    from xhnovel_pipeline.phase0_builder import _seal_brief
    library, research, source, sealed, prepared, planning = setup
    before = acq.tree_manifest(sealed)
    first = allocate(setup)
    second_research = library.new_research(write_json(tmp_path / "second.json", {"goal": "另一个需求"}), key="second", name="第二项研究")
    draft = _input(next((sealed / "chapters").glob("*.txt")))
    draft["brief"]["research_question"] = "研究交互失败后的恢复场景。"
    draft["brief"]["evidence_discovery_brief"] = "寻找交互失败后重新获得行动能力的场景。"
    second_brief = write_json(tmp_path / "second-brief.json", _seal_brief(draft["brief"]))
    options = lib.read_json(planning)
    options["brief"] = acq.ref(second_brief)
    second_planning = write_json(tmp_path / "second-planning.json", options)
    second_prepared = acq.prepare_source(sealed, second_planning, tmp_path / "second-phase0")
    second = library.allocate_execution(second_research["record_id"], source["record_id"],
                                        handoff=second_prepared["handoff_path"], native_root=tmp_path / "second-phase0", key="second")
    assert first["record"]["work_dir"] != second["record"]["work_dir"]
    assert first["record"]["source_revision"] == second["record"]["source_revision"]
    assert first["record"]["input_spec_hash"] != second["record"]["input_spec_hash"]
    assert acq.tree_manifest(sealed) == before
    assert library.list_records("product")["records"] == []


def test_unrelated_handoff_cannot_register_as_same_source(setup, tmp_path):
    library, _, source, sealed, *_ = setup
    other = tmp_path / "other"
    other.mkdir()
    _, prepared, _ = prepared_fixture(other)
    with pytest.raises(ValidationError, match="does not bind"):
        library.register_source(sealed, protocol="SCENE", handoff=prepared["handoff_path"], native_root=other / "phase0")


def test_scene_native_lifecycle_product_replay_and_evidence(setup):
    library, research, source, sealed, prepared, planning = setup
    execution = allocate(setup)
    work = Path(execution["record"]["work_dir"])
    acq.freeze_source(sealed, Path(prepared["handoff_path"]), work, phase0_root=planning.parent / "phase0")
    arguments = dict(executor="agent-files", extractor_factory=_agent_factory(work), repo_root=lib.ROOT, now=NOW)
    with pytest.raises(AgentResponsesPending):
        execute_evidence_handoff(Path(prepared["handoff_path"]), work, **arguments)
    assert library.validate(execution["record_id"])["status"] == "WAITING_FOR_AGENT"
    assert not library.list_records("product")["records"]
    _answer_all(work)
    completed = execute_evidence_handoff(Path(prepared["handoff_path"]), work, **arguments)
    product = library.register_product(execution["record_id"], completed.receipt_path)
    assert product == library.register_product(execution["record_id"], completed.receipt_path)
    assert library.validate(product["record_id"])["status"] == "VALIDATED"
    page = library.read_product(product["record_id"], limit=1)
    assert page["records"] and "content" not in page["records"][0]
    assert library.list_records("product", query="场景研究")["records"]
    rebuilt, records, *_ = library._product_context(execution["record_id"], product["record"]["receipt"])
    assert records
    evidence = library.show_evidence(product["record_id"], records[0]["scene_candidate_id"])
    assert evidence["evidence"] and evidence["assurance"] == "DRAFT_UNVERIFIED"
    assert all("text" not in span for span in evidence["evidence"])
    report_file = work.parent / "reports/summary.md"
    report_file.write_text("Synthetic source research result; see native product.")
    report = library.register_report(research["record_id"], report_file,
                                     executions=[execution["record_id"]], products=[product["record_id"]])
    assert library.validate(report["record_id"])["status"] == "VALIDATED"
    assert not library.reindex()["issues"]
    chapter = next((sealed / "chapters").glob("*.txt"))
    artifact = lib.ArtifactStore(work / "ingestion/objects")._path(lib.artifact_id_for(chapter.read_bytes()))
    artifact.write_bytes(b"bad native bytes")
    with pytest.raises(ValidationError):
        library.show_evidence(product["record_id"], records[0]["scene_candidate_id"])


@pytest.mark.parametrize("nonempty", [False, True])
def test_generic_campaign_lifecycle_keeps_native_budget_and_zero_results(tmp_path, nonempty):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    sealed, planning, root, campaign, start = sealed_attempt(source_dir)
    library = lib.Library.initialize(tmp_path / "library")
    research = library.new_research(write_json(tmp_path / "request.json", {"goal": "种族观察"}), key="race", name="种族观察")
    prepared = acq.prepare_generic_source(sealed, planning, root)
    source = library.register_source(sealed, protocol="GENERIC", handoff=prepared["handoff_path"], native_root=root)
    execution = library.allocate_execution(research["record_id"], source["record_id"], handoff=prepared["handoff_path"], native_root=root, key="race")
    work = Path(execution["record"]["work_dir"])
    acq.freeze_generic_source(sealed, Path(prepared["handoff_path"]), root, work)
    record(campaign, root, "source:finish", "SOURCE_FINISHED", {"start_event_artifact_id": start.artifact_id,
        "status": "ELIGIBLE", "handoff_artifact_id": prepared["handoff_artifact_id"], "reason": "Synthetic verified source."})
    waiting = execute_campaign_handoff(campaign.record, prepared["handoff_path"], root, work, now=NOW)
    assert library.validate(execution["record_id"])["status"] == "WAITING_FOR_AGENT"
    _answers(waiting, nonempty=nonempty)
    completed = execute_campaign_handoff(campaign.record, prepared["handoff_path"], root, work, now=NOW)
    assert completed["status"] == "SUCCEEDED"
    report = report_observation_research(campaign.record, root)
    assert report["budget_used"]["source_attempts"] == 1
    assert report["counts"]["successful_works"] == 1
    receipts = list((root / "records/GenericExtractionExecutionReceipt").glob("*.json"))
    receipt = next(p for p in receipts if lib.read_json(p)["status"] == "SUCCEEDED")
    product = library.register_product(execution["record_id"], receipt)
    assert product["record"]["record_count"] == int(nonempty)
    assert library.validate(product["record_id"])["status"] == "VALIDATED"
    assert len(library.read_product(product["record_id"])["records"]) == int(nonempty)
    assert library.list_records("product", query="race-mention-v1")["records"]
    _, records, *_ = library._product_context(execution["record_id"], product["record"]["receipt"])
    if nonempty:
        evidence = library.show_evidence(product["record_id"], records[0]["record_id"])
        assert evidence["evidence"] and evidence["assurance"] == "UNQUALIFIED"
    else:
        with pytest.raises(ValidationError, match="not a member"):
            library.show_evidence(product["record_id"], "invented")
    assert not library.reindex()["issues"]


def test_cli_real_commands_and_unknown_options_fail_closed(tmp_path, capsys):
    root = tmp_path / "library"
    prefix = ["--library-root", str(root)]
    assert lib.main([*prefix, "init"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["root"] == str(root)
    assert lib.main([*prefix, "list-sources"]) == 0
    assert json.loads(capsys.readouterr().out)["result"]["records"] == []
    assert lib.main([*prefix, "verify", "0" * 64]) == 2
    with pytest.raises(SystemExit):
        lib.main([*prefix, "build-view", "0" * 64])


def test_managed_source_is_explicitly_readmitted_at_stable_path(setup):
    library, _, source, sealed, _, planning = setup
    target = library.root / "sources/sealed" / sealed.name
    shutil.copytree(sealed, target)
    prepared = acq.prepare_source(target, planning, library.root / "admission")
    admitted = library.register_source(target, protocol="SCENE", handoff=prepared["handoff_path"], native_root=library.root / "admission")
    assert admitted["record"]["mode"] == "MANAGED"
    assert admitted["record"]["source_revision"] == source["record"]["source_revision"]
    assert admitted["record"]["input_spec_hash"] != source["record"]["input_spec_hash"]
    assert library.validate(admitted["record_id"])["status"] == "VALIDATED"


def test_concurrent_same_source_registration_is_immutable(setup):
    library, _, source, sealed, prepared, planning = setup
    def register(_):
        return library.register_source(sealed, protocol="SCENE", handoff=prepared["handoff_path"], native_root=planning.parent / "phase0")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(register, range(2)))
    assert all(r == source for r in results)


def test_concurrent_handoff_allocation_cannot_choose_two_directories(setup):
    library, research, source, _, prepared, planning = setup
    def allocate_key(key):
        try:
            return library.allocate_execution(research["record_id"], source["record_id"], handoff=prepared["handoff_path"],
                native_root=planning.parent / "phase0", key=key)
        except ValidationError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(allocate_key, ("one", "two")))
    assert sum(r is not None for r in results) == 1
    assert len(library.list_records("execution")["records"]) == 1


def test_allocation_registration_interruption_can_resume_same_inputs(setup, monkeypatch):
    library, *_ = setup
    original = library._publish
    def interrupt(record):
        if record["kind"] == "execution":
            raise OSError("simulated disk failure after fixed directory binding")
        return original(record)
    monkeypatch.setattr(library, "_publish", interrupt)
    with pytest.raises(OSError):
        allocate(setup)
    assert library.list_records("execution")["records"] == []
    monkeypatch.setattr(library, "_publish", original)
    execution = allocate(setup)
    assert library.validate(execution["record_id"])["status"] == "HANDOFF_READY"


def test_old_external_execution_is_registered_without_moving(setup, tmp_path):
    library, *_ = setup
    external = tmp_path / "existing-work"
    external.mkdir()
    marker = external / "keep.txt"
    marker.write_text("original files remain")
    execution = allocate(setup, work_dir=external)
    assert execution["record"]["mode"] == "EXTERNAL_REFERENCE"
    assert marker.read_text() == "original files remain"
    external.rename(tmp_path / "moved")
    with pytest.raises(ValidationError):
        library.validate(execution["record_id"])


def test_forged_source_record_and_version_claim_cannot_override_replay(setup):
    library, _, source, *_ = setup
    altered = dict(source["record"])
    altered["acquisition_script_sha256"] = "0" * 64
    forged = library._publish(altered)
    with pytest.raises(ValidationError, match="differs from replay"):
        library.validate(forged["record_id"])


def test_failed_native_receipt_is_a_report_reference_not_a_product(setup):
    library, research, _, sealed, prepared, planning = setup
    execution = allocate(setup)
    work = Path(execution["record"]["work_dir"])
    acq.freeze_source(sealed, Path(prepared["handoff_path"]), work, phase0_root=planning.parent / "phase0")
    kwargs = dict(executor="agent-files", extractor_factory=_agent_factory(work), repo_root=lib.ROOT, now=NOW)
    with pytest.raises(AgentResponsesPending):
        execute_evidence_handoff(Path(prepared["handoff_path"]), work, **kwargs)
    task_path = next((work / "scene-scout/agent-files/tasks").glob("*.json"))
    task = lib.read_json(task_path)
    answer = task_path.parents[1] / task["answer_file"]
    answer.parent.mkdir(parents=True, exist_ok=True)
    answer.write_text("invalid JSON")
    with pytest.raises(ValidationError):
        execute_evidence_handoff(Path(prepared["handoff_path"]), work, **kwargs)
    assert library.validate(execution["record_id"])["status"] == "FAILED"
    history = lib.validate_handoff_execution_history(Path(prepared["handoff_path"]), phase0_root=planning.parent / "phase0")
    with pytest.raises(ValidationError, match="successful native receipt"):
        library.register_product(execution["record_id"], history[-1].receipt_path)
    report = work.parent / "reports/failed.md"
    report.write_text("Native answers rejected. No successful corpus.")
    registered = library.register_report(research["record_id"], report, executions=[execution["record_id"]])
    assert registered["record"]["product_record_ids"] == []
    assert library.validate(registered["record_id"])["status"] == "VALIDATED"


def test_same_title_with_different_author_has_distinct_native_identity(setup, tmp_path):
    from test_phase0_builder import _input
    from xhnovel_pipeline.phase0_builder import _seal_brief
    library, _, source, *_ = setup
    folder = tmp_path / "other-author"
    folder.mkdir()
    cfg, inputs = fixture_config(folder, count=1)
    config = lib.read_json(cfg)
    config["work"]["author"] = "另一位作者"
    write_json(cfg, config)
    run = acq.Run.initialize(cfg)
    run.import_local(inputs)
    sealed = acq.seal(run, folder / "sealed", reviewed(run, folder))
    draft = _input(inputs / "0001.txt")
    for lead in draft["leads"]:
        lead["work_claim"]["author"] = "另一位作者"
    brief = write_json(folder / "brief.json", _seal_brief(draft["brief"]))
    leads = write_json(folder / "leads.json", draft["leads"])
    plan = write_json(folder / "planning.json", {"format_version": acq.FORMAT, "brief": acq.ref(brief), "leads": acq.ref(leads), "planning": None})
    prepared = acq.prepare_source(sealed, plan, folder / "phase0")
    other = library.register_source(sealed, protocol="SCENE", handoff=prepared["handoff_path"], native_root=folder / "phase0")
    assert other["record"]["work_ref_id"] != source["record"]["work_ref_id"]
    assert len(library.list_records("source", query="测试仙途")["records"]) == 2


@pytest.mark.parametrize("protocol", ["SCENE", "GENERIC"])
def test_existing_txt_native_products_can_be_archived_without_claiming_reusable_source(tmp_path, protocol):
    library = lib.Library.initialize(tmp_path / "library")
    research = library.new_research(write_json(tmp_path / "request.json", {"goal": "历史研究归档"}), key="archive", name="历史研究")
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    work = legacy / "original-native"
    if protocol == "SCENE":
        from test_phase0_execution import _prepare
        prepared = _prepare(legacy)
        root = prepared.phase0_root
        handoff = prepared.handoff_path
        kwargs = dict(executor="agent-files", extractor_factory=_agent_factory(work), repo_root=lib.ROOT, now=NOW)
        with pytest.raises(AgentResponsesPending):
            execute_evidence_handoff(handoff, work, **kwargs)
        _answer_all(work)
        completed = execute_evidence_handoff(handoff, work, **kwargs)
        receipt = completed.receipt_path
    else:
        from test_generic_handoff import prepared_handoff
        from test_generic_handoff_execution import _run
        root, source, prepared = prepared_handoff(legacy, profile="race-mention-v1", kind="RACE_MENTION")
        handoff = prepared["handoff_path"]
        waiting = _run(root, prepared, work)
        _answers(waiting, nonempty=True)
        completed = _run(root, prepared, work)
        receipt = completed["receipt_path"]
    assert not (legacy / "sealed").exists()
    execution = library.register_external_execution(research["record_id"], protocol=protocol, handoff=handoff, native_root=root, work_dir=work)
    assert execution["record"]["kind"] == "external-execution"
    assert library.validate(execution["record_id"])["status"] == "SUCCEEDED"
    product = library.register_product(execution["record_id"], receipt)
    page = library.read_product(product["record_id"])
    assert page["records"]
    evidence = library.show_evidence(product["record_id"], page["records"][0]["native_record_id"])
    assert evidence["evidence"]
    assert evidence["evidence"][0]["chapter_path"] is None
    assert evidence["evidence"][0]["chapter_locator"]
    assert library.list_records("source")["records"] == []
    assert len(library.list_records("execution")["records"]) == 1
    assert library.list_records("product", query="历史研究")["records"]
    assert not library.reindex()["issues"]


def test_external_execution_requires_actual_native_history(setup, tmp_path):
    library, research, _, _, prepared, planning = setup
    unused = tmp_path / "unused-native"
    unused.mkdir()
    with pytest.raises(ValidationError, match="no native execution history"):
        library.register_external_execution(research["record_id"], protocol="SCENE", handoff=prepared["handoff_path"], native_root=planning.parent / "phase0", work_dir=unused)


def test_unicode_query_offsets_stay_in_chapter_bytes(tmp_path):
    from test_phase0_builder import _input
    from xhnovel_pipeline.phase0_builder import _seal_brief
    from xhnovel_pipeline.parse import normalize_text
    cfg, inputs = fixture_config(tmp_path, count=1)
    (inputs / "0001.txt").write_text("第1章 合成1\n\nＡ   e\u0301😀 玉佩 玉佩。\n", encoding="utf-8")
    run = acq.Run.initialize(cfg)
    run.import_local(inputs)
    sealed = acq.seal(run, tmp_path / "sealed", reviewed(run, tmp_path))
    draft = _input(inputs / "0001.txt")
    brief = write_json(tmp_path / "brief.json", _seal_brief(draft["brief"]))
    leads = write_json(tmp_path / "leads.json", draft["leads"])
    plan = write_json(tmp_path / "plan.json", {"format_version": acq.FORMAT, "brief": acq.ref(brief), "leads": acq.ref(leads), "planning": None})
    prepared = acq.prepare_source(sealed, plan, tmp_path / "phase0")
    library = lib.Library.initialize(tmp_path / "library")
    source = library.register_source(sealed, protocol="SCENE", handoff=prepared["handoff_path"], native_root=tmp_path / "phase0")
    result = library.search_text(source["record_id"], "玉佩", limit=1)
    hit = result["matches"][0]
    raw = Path(hit["chapter_path"]).read_bytes()
    assert raw[hit["byte_start"]:hit["byte_end"]].decode() == "玉佩"
    assert raw.decode()[hit["codepoint_start"]:hit["codepoint_end"]] == "玉佩"
    assert hit["byte_start"] != hit["codepoint_start"]
    assert normalize_text(raw.decode()).index("玉佩") != hit["codepoint_start"]
    assert result["truncated"]


def test_displayed_catalog_is_validated_after_historical_receipt_check(setup, monkeypatch):
    library, _, _, _, prepared, _ = setup
    execution = allocate(setup)
    work = Path(execution["record"]["work_dir"])
    kwargs = dict(executor="agent-files", extractor_factory=_agent_factory(work), repo_root=lib.ROOT, now=NOW)
    with pytest.raises(AgentResponsesPending):
        execute_evidence_handoff(Path(prepared["handoff_path"]), work, **kwargs)
    _answer_all(work)
    completed = execute_evidence_handoff(Path(prepared["handoff_path"]), work, **kwargs)
    product = library.register_product(execution["record_id"], completed.receipt_path)
    read = lib.read_json
    def switched_catalog(path, *args, **kwargs):
        value = read(path, *args, **kwargs)
        if Path(path).name == "catalog.json" and "research" in Path(path).parts:
            value["Segment"][0]["normalized_text"] = "tampered between verification and display"
        return value
    monkeypatch.setattr(lib, "read_json", switched_catalog)
    with pytest.raises(ValidationError):
        library.read_product(product["record_id"])
