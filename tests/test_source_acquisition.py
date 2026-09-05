from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import source_acquisition as acq
from xhnovel_pipeline.phase0_handoff import make_operator_attestation
from xhnovel_pipeline.novel_ingest import _exclusive_work_dir


class FakeClock:
    def __init__(self):
        self.wall = 1_780_000_000_000
        self.mono = 0
        self.sleeps = []

    def now_ms(self):
        return self.wall

    def monotonic_ms(self):
        return self.mono

    def advance(self, milliseconds):
        self.wall += milliseconds
        self.mono += milliseconds

    def sleep_ms(self, milliseconds):
        self.sleeps.append(milliseconds)
        self.advance(milliseconds)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def fixture_config(tmp_path, *, channel="C4", count=3, assessment="PASS", extractor=None, limits=None):
    evidence = tmp_path / "fixture-basis.txt"
    evidence.write_text("Synthetic, intentionally complete fixture created for this test.", encoding="utf-8")
    att = make_operator_attestation(
        basis="USER_AUTHORIZED_LOCAL_COPY",
        may_export_excerpts=False,
        attested_by="synthetic-test-operator",
        scope="synthetic fixture only",
        attested_at="2026-09-05T00:00:00Z",
    )
    att_path = write_json(tmp_path / "attestation.json", att)
    inputs = tmp_path / "input"
    inputs.mkdir()
    entries, chapters = [], []
    for i in range(1, count + 1):
        k = f"p{i}"
        title = f"第{i}章 合成{i}"
        (inputs / f"{i:04d}.txt").write_text(f"{title}\n\n角色{i}带着第{i}件物品走向地点{i}。\n", encoding="utf-8")
        entries.append({"key": k, "url": f"https://example.org/book/test/{i}.html", "import_path": f"{i:04d}.txt", "expected_title": title})
        chapters.append({"key": f"c{i}", "title": title, "entry_keys": [k], "role": "MAIN"})
    verdict = {"status": assessment, "reason": "Synthetic fixture construction is explicit.", "evidence": [acq.ref(evidence)]}
    cat = {
        "format_version": acq.FORMAT, "entries": entries, "chapters": chapters,
        "assessments": {name: dict(verdict) for name in acq.ASSESSMENTS},
    }
    cat_path = write_json(tmp_path / "catalog.json", cat)
    cfg = {
        "format_version": acq.FORMAT, "run_dir": str(tmp_path / "run"),
        "work": {"title": "测试仙途", "author": "测试作者", "language": "zh"},
        "source": {
            "id": "fixture", "channel": channel, "scope_url": "https://example.org/book/test/",
            "edition_status": "USER_VERIFIED_COPY", "edition_label": "Synthetic test source",
            "extractor": extractor or {
                "kind": "TXT", "title_selector": None, "body_selector": None,
                "exclude_selectors": [], "strip_leading_title": False,
            },
            "browser_authorization": None,
        },
        "attestation": acq.ref(att_path), "catalog": acq.ref(cat_path),
        "limits": limits or {},
    }
    return write_json(tmp_path / "config.json", cfg), inputs


def mutate_config(config_path, **updates):
    cfg = acq.read_json(config_path)
    cfg.update(updates)
    write_json(config_path, cfg)


def mutate_catalog(config_path, edit):
    cfg = acq.read_json(config_path)
    path = Path(cfg["catalog"]["path"])
    cat = acq.read_json(path)
    edit(cat)
    write_json(path, cat)
    cfg["catalog"] = acq.ref(path)
    write_json(config_path, cfg)


def sender(clock, responses, calls):
    iterator = iter(responses)
    def send(url, timeout, limit):
        calls.append((url, clock.now_ms(), clock.monotonic_ms()))
        response = next(iterator)
        if isinstance(response, tuple):
            duration, response = response
            clock.advance(duration)
        return response
    return send


def chapter(i):
    return acq.Response(200, f"第{i}章 合成{i}\n\n第{i}个角色完成不同的动作{i}。\n".encode())


def test_inspect_has_no_side_effects(tmp_path, capsys):
    config, _ = fixture_config(tmp_path)
    assert acq.main(["inspect", str(config)]) == 0
    assert not (tmp_path / "run").exists()


def test_t01_t02_actual_request_gap_includes_blocked_call_and_redirect(tmp_path):
    config, _ = fixture_config(tmp_path, channel="C1", count=2)
    clock, calls = FakeClock(), []
    run = acq.Run.initialize(config, clock)
    response = run.acquire(send=sender(clock, [
        (20_000, acq.Response(302, b"", location="/book/test/redirect.html")),
        chapter(1), chapter(2),
    ], calls))
    assert response["accepted_entries"] == 2
    assert calls[1][2] - calls[0][2] == 30_000
    assert calls[2][2] - calls[1][2] == 10_000
    assert len(list((run.root / "attempts/p1").glob("*.json"))) == 2


@pytest.mark.parametrize("header", ["120", "garbage", "Sat, 30 May 2026 00:10:00 GMT"])
def test_t03_retry_after_persists_across_restart(tmp_path, header):
    config, _ = fixture_config(tmp_path, channel="C1", count=1)
    clock, calls = FakeClock(), []
    run = acq.Run.initialize(config, clock)
    result = run.acquire(send=sender(clock, [acq.Response(429, b"slow", retry_after=header)], calls))
    expected = acq.retry_after_ms(header, clock.now_ms())
    assert result["retry_not_before_ms"] == expected
    clock.advance(1)
    if expected > clock.now_ms():
        acq.Run.initialize(config, clock).acquire(send=lambda *_: pytest.fail("retried during cooldown"))


@pytest.mark.parametrize("response", [acq.Response(403, b"denied"), acq.Response(200, b"Just a moment...", "text/html")])
def test_t04_access_denial_is_not_text_and_does_not_retry(tmp_path, response):
    config, _ = fixture_config(tmp_path, channel="C1", count=1)
    clock, calls = FakeClock(), []
    run = acq.Run.initialize(config, clock)
    assert run.acquire(send=sender(clock, [response], calls))["acquisition"] == "NEEDS_ACCESS"
    assert not run.accepted()
    run.acquire(send=lambda *_: pytest.fail("retried access denial"))


def test_t05_finite_retries_and_missing(tmp_path):
    config, _ = fixture_config(tmp_path, channel="C1", count=1)
    clock, calls = FakeClock(), []
    run = acq.Run.initialize(config, clock)
    run.acquire(send=sender(clock, [acq.Response(500, b"bad")] * 3, calls))
    assert len(calls) == 3
    clock.advance(1_000_000)
    result = run.acquire(send=lambda *_: pytest.fail("retry budget reset"))
    assert result["acquisition"] == "ATTEMPTS_EXHAUSTED"


def test_t05_404_remains_a_gap(tmp_path):
    config, _ = fixture_config(tmp_path, channel="C1", count=1)
    run = acq.Run.initialize(config, FakeClock())
    assert run.acquire(send=lambda *_: acq.Response(404, b"missing"))["acquisition"] == "MISSING"
    assert not run.accepted()


def test_t06_total_budget_does_not_launch_unbounded_work(tmp_path):
    config, _ = fixture_config(tmp_path, channel="C1", count=1, limits={"max_run_seconds": 10})
    run = acq.Run.initialize(config, FakeClock())
    assert run.acquire(send=lambda *_: pytest.fail("request cannot fit budget"))["acquisition"] == "BUDGET_EXHAUSTED"


@pytest.mark.parametrize("point", ["raw", "attempt", "derived", "accepted"])
def test_t07_t08_t09_crash_boundaries_recover_without_false_progress(tmp_path, point):
    config, inputs = fixture_config(tmp_path, count=1)
    run = acq.Run.initialize(config, FakeClock())
    def crash(stage):
        if stage == point:
            raise RuntimeError("injected crash")
    with pytest.raises(RuntimeError, match="injected"):
        run.import_local(inputs, crash=crash)
    assert len(run.accepted()) == (1 if point == "accepted" else 0)
    resumed = acq.Run.initialize(config, FakeClock())
    assert resumed.import_local(inputs)["accepted_entries"] == 1


def test_t08_fetch_receipt_recovers_without_network(tmp_path):
    config, _ = fixture_config(tmp_path, channel="C1", count=1)
    run = acq.Run.initialize(config, FakeClock())
    def crash(stage):
        if stage == "attempt":
            raise RuntimeError("injected")
    with pytest.raises(RuntimeError):
        run.acquire(send=lambda *_: chapter(1), crash=crash)
    assert acq.Run.initialize(config, FakeClock()).acquire(
        send=lambda *_: pytest.fail("valid raw was refetched")
    )["accepted_entries"] == 1


@pytest.mark.parametrize("damage", ["change", "delete", "escape"])
def test_t10_damaged_committed_file_never_counts_as_complete(tmp_path, damage):
    config, inputs = fixture_config(tmp_path, count=1)
    run = acq.Run.initialize(config)
    run.import_local(inputs)
    path = run.root / "chapters/p1.txt"
    if damage == "change":
        path.write_text("other")
    elif damage == "delete":
        path.unlink()
    else:
        accepted = acq.read_json(run.root / "accepted/p1.json")
        accepted["derived"] = acq.ref(inputs / "0001.txt")
        write_json(run.root / "accepted/p1.json", accepted)
    with pytest.raises((acq.AcquisitionError, OSError)):
        run.status()


def test_t15_second_writer_rejected(tmp_path):
    config, inputs = fixture_config(tmp_path)
    run = acq.Run.initialize(config)
    with _exclusive_work_dir(run.root):
        with pytest.raises(acq.ValidationError, match="LOCKED"):
            run.import_local(inputs)


def test_t18_changed_import_preserves_both_raw_versions(tmp_path):
    config, inputs = fixture_config(tmp_path, count=1)
    run = acq.Run.initialize(config)
    run.import_local(inputs)
    path = inputs / "0001.txt"
    path.write_text(path.read_text() + "changed body")
    with pytest.raises(acq.AcquisitionError, match="SOURCE-CHANGED"):
        run.import_local(inputs)
    assert len(list((run.root / "raw/p1").glob("*.bin"))) == 2
    assert run.status()["accepted_entries"] == 1
    assert run.status()["acquisition"] == "SOURCE_CHANGED"
    assert acq.verify(run)["checks"]["source_consistency"] == "FAIL"


def test_t19_journal_tail_is_audited_and_rebuilt(tmp_path):
    config, inputs = fixture_config(tmp_path, count=2)
    second = (inputs / "0002.txt").read_bytes()
    (inputs / "0002.txt").unlink()
    run = acq.Run.initialize(config)
    run.import_local(inputs)
    with (run.root / "journal.jsonl").open("ab") as stream:
        stream.write(b'{"broken')
    (inputs / "0002.txt").write_bytes(second)
    assert run.import_local(inputs)["accepted_entries"] == 2
    assert list((run.root / "journal-recovery").glob("*.bin"))


def test_t20_reconfigured_run_does_not_overwrite_frozen_inputs(tmp_path):
    config, _ = fixture_config(tmp_path)
    run = acq.Run.initialize(config)
    mutate_config(config, limits={"min_gap_seconds": 8})
    with pytest.raises(acq.ValidationError, match="IMMUTABLE"):
        acq.Run.initialize(config)
    assert acq.Run(run.root).limits["min_gap_seconds"] == 5


@pytest.mark.parametrize("location", ["https://other.example/book/2", "/other-book/1", "/book/test/%2e%2e/other"])
def test_t21_redirect_scope_cannot_escape_work(tmp_path, location):
    config, _ = fixture_config(tmp_path, channel="C1", count=1)
    run = acq.Run.initialize(config, FakeClock())
    with pytest.raises(acq.AcquisitionError, match="SCOPE"):
        run.acquire(send=lambda *_: acq.Response(302, b"", location=location))
    assert not run.accepted()


def test_t21_body_size_bound(tmp_path):
    config, _ = fixture_config(tmp_path, channel="C1", count=1, limits={"max_response_bytes": 40})
    run = acq.Run.initialize(config, FakeClock())
    run.acquire(send=lambda *_: acq.Response(200, b"x" * 100))
    assert not run.accepted()
    assert (run.root / "raw/p1/000001.bin").stat().st_size == 40


def test_html_extraction_is_scoped_and_rejects_ambiguous_or_truncated_dom():
    ex = {"kind": "HTML", "title_selector": "h1", "body_selector": "#content", "exclude_selectors": [".ad"], "strip_leading_title": False}
    data = b"<html><body><h1>Chapter 1</h1><div id='content'><p>A &amp; B</p><div class='ad'>AD</div><p>C</p></div><footer>NO</footer></body></html>"
    assert acq.extract(data, ex, "Chapter 1") == ("Chapter 1", "A & B\nC")
    with pytest.raises(acq.AcquisitionError):
        acq.extract(data.replace(b"</body>", b"<h1>Other</h1></body>"), ex, None)
    with pytest.raises(acq.AcquisitionError):
        acq.extract(data[:-10], ex, None)


def test_t30_attestation_tampering_rejected(tmp_path):
    config, _ = fixture_config(tmp_path)
    cfg = acq.read_json(config)
    Path(cfg["attestation"]["path"]).write_text("{}")
    with pytest.raises(acq.AcquisitionError, match="hash mismatch"):
        acq.Run.initialize(config)


def test_t31_import_symlink_rejected(tmp_path):
    config, inputs = fixture_config(tmp_path, count=1)
    (inputs / "0001.txt").unlink()
    (inputs / "0001.txt").symlink_to(config)
    run = acq.Run.initialize(config)
    with pytest.raises(acq.AcquisitionError, match="symlink"):
        run.import_local(inputs)


def test_t37_c2_is_never_implicitly_activated(tmp_path):
    config, _ = fixture_config(tmp_path, channel="C2", count=1)
    run = acq.Run.initialize(config)
    with pytest.raises(acq.AcquisitionError, match="BROWSER"):
        run.acquire(send=lambda *_: pytest.fail("C2 must not silently use C1"))


def reviewed(run, tmp_path):
    value = acq.review_template(run)
    value["reviewer"] = "synthetic-fixture-reviewer"
    value["reviewed_at"] = "2026-09-05T12:00:00Z"
    value["limitations"] = "Constructed synthetic fixture; not a real novel evaluation."
    evidence = tmp_path / "review-basis.txt"
    evidence.write_text("The test fixture intentionally contains complete, short, distinct chapters.", encoding="utf-8")
    for assessment in [*value["samples"].values(), *value["anomalies"].values()]:
        assessment.update(status="PASS", reason="Explicit synthetic fixture construction.", evidence=[acq.ref(evidence)])
    return write_json(tmp_path / "review.json", value)


def test_t11_duplicate_body_cannot_be_approved_by_review(tmp_path):
    config, inputs = fixture_config(tmp_path, count=4)
    for p in inputs.iterdir():
        title = p.read_text().partition("\n")[0]
        p.write_text(title + "\n\nIdentical body copied to every chapter.\n")
    run = acq.Run.initialize(config)
    run.import_local(inputs)
    report = acq.verify(run, reviewed(run, tmp_path))
    assert report["result"] == "FAIL"
    assert report["checks"]["duplicate_bodies"] == "FAIL"
    with pytest.raises(acq.AcquisitionError, match="NOT-READY"):
        acq.seal(run, tmp_path / "sealed", tmp_path / "review.json")


@pytest.mark.parametrize("missing", ["0001.txt", "0002.txt", "0003.txt"])
def test_t12_t23_missing_endpoints_or_middle_block_seal(tmp_path, missing):
    config, inputs = fixture_config(tmp_path)
    (inputs / missing).unlink()
    run = acq.Run.initialize(config)
    run.import_local(inputs)
    report = acq.verify(run, reviewed(run, tmp_path))
    assert report["result"] == "UNRESOLVED"
    assert report["checks"]["entry_coverage"] == "UNRESOLVED"
    with pytest.raises(acq.AcquisitionError, match="NOT-READY"):
        acq.seal(run, tmp_path / "sealed", tmp_path / "review.json")


def test_t13_catalog_guessed_from_ids_cannot_self_promote(tmp_path):
    config, inputs = fixture_config(tmp_path, assessment="UNRESOLVED")
    run = acq.Run.initialize(config)
    run.import_local(inputs)
    assert acq.verify(run, reviewed(run, tmp_path))["result"] == "UNRESOLVED"


def test_t14_pages_are_assembled_only_when_all_are_present(tmp_path):
    config, inputs = fixture_config(tmp_path, count=2)
    def group(cat):
        cat["chapters"] = [{"key": "c1", "title": "第1章 合成1", "entry_keys": ["p1", "p2"], "role": "MAIN"}]
    mutate_catalog(config, group)
    second = (inputs / "0002.txt").read_bytes()
    (inputs / "0002.txt").unlink()
    run = acq.Run.initialize(config)
    run.import_local(inputs)
    assert acq.chapter_view(run)[0]["chapters"] == []
    (inputs / "0002.txt").write_bytes(second)
    run.import_local(inputs)
    view, payloads = acq.chapter_view(run)
    assert len(view["chapters"]) == 1
    text = payloads["c1"].decode()
    assert "第1件物品" in text and "第2件物品" in text
    for page in view["chapters"][0]["page_spans"]:
        assert text[page["body_start_char"]:page["body_end_char"]]


def test_t16_duplicate_titles_remain_multiple_alignment_proposals(tmp_path):
    left_root, right_root = tmp_path / "left", tmp_path / "right"
    left_root.mkdir()
    right_root.mkdir()
    lc, li = fixture_config(left_root, count=2)
    rc, ri = fixture_config(right_root, count=2)
    for config, inputs in ((lc, li), (rc, ri)):
        def rename(cat):
            for e in cat["entries"]:
                e["expected_title"] = "相同标题"
            for c in cat["chapters"]:
                c["title"] = "相同标题"
        mutate_catalog(config, rename)
        for p in inputs.iterdir():
            p.write_text("相同标题\n" + p.read_text().partition("\n")[2])
    left, right = acq.Run.initialize(lc), acq.Run.initialize(rc)
    left.import_local(li)
    right.import_local(ri)
    report = acq.compare_sources(left, right)
    assert report["title_proposals"][0]["right_candidates"] == ["c1", "c2"]
    assert report["aligned_groups"] == []
    assert report["unaligned_left"] == ["c1", "c2"]


def test_t17_t29_shared_bad_text_is_not_automatically_fidelity_pass(tmp_path):
    config, inputs = fixture_config(tmp_path, count=1)
    p = inputs / "0001.txt"
    p.write_text(p.read_text() + "\n本章未完 **")
    run = acq.Run.initialize(config)
    run.import_local(inputs)
    report = acq.verify(run)
    assert report["checks"]["fidelity"] == "UNRESOLVED"
    assert any(a["details"].get("token") == "**" for a in report["anomalies"])


def test_t22_t24_seal_replays_and_does_not_depend_on_original_run(tmp_path):
    config, inputs = fixture_config(tmp_path)
    run = acq.Run.initialize(config)
    run.import_local(inputs)
    review = reviewed(run, tmp_path)
    sealed = acq.seal(run, tmp_path / "sealed", review)
    manifest, _ = acq.validate_sealed(sealed)
    assert manifest["view_sha256"] == acq.object_digest(acq.chapter_view(run)[0])
    native = acq.DirectoryNovelAdapter({"path": str(sealed / "chapters")}).discover()
    assert [ch.declared_number for ch in native.chapters] == [1, 2, 3]
    (run.root / "chapters/p1.txt").write_text("changed original")
    acq.validate_sealed(sealed)
    target = next((sealed / "chapters").glob("*.txt"))
    target.write_text(target.read_text() + "changed sealed")
    with pytest.raises(acq.AcquisitionError, match="sealed files differ"):
        acq.validate_sealed(sealed)


def test_t24_review_binds_actual_accepted_bytes(tmp_path):
    config, inputs = fixture_config(tmp_path)
    run = acq.Run.initialize(config)
    second = (inputs / "0002.txt").read_bytes()
    (inputs / "0002.txt").unlink()
    run.import_local(inputs)
    stale = reviewed(run, tmp_path)
    (inputs / "0002.txt").write_bytes(second)
    run.import_local(inputs)
    with pytest.raises(acq.AcquisitionError, match="REVIEW-BIND"):
        acq.verify(run, stale)


def test_t27_sampling_cannot_consume_lead_metadata(tmp_path):
    config, _ = fixture_config(tmp_path, count=50)
    cat = acq.read_json(Path(acq.read_json(config)["catalog"]["path"]))
    plan = acq.sample_plan(cat)
    assert len(plan["chapter_keys"]) <= 13
    assert len(plan["chapter_keys"]) == len(set(plan["chapter_keys"]))
    assert {"c1", "c26", "c50"} <= set(plan["chapter_keys"])
    assert plan["lead_metadata_consumed"] is False
    cfg = acq.read_json(config)
    cfg["lead_hints"] = ["第1章", "evil instruction"]
    write_json(config, cfg)
    with pytest.raises(acq.ValidationError, match="invalid field set"):
        acq.Run.initialize(config)


def test_t28_difference_metric_is_not_an_error_rate(tmp_path):
    lroot, rroot = tmp_path / "left", tmp_path / "right"
    lroot.mkdir()
    rroot.mkdir()
    lc, li = fixture_config(lroot, count=1)
    rc, ri = fixture_config(rroot, count=1)
    (ri / "0001.txt").write_text((ri / "0001.txt").read_text().replace("走向", "离开"))
    left, right = acq.Run.initialize(lc), acq.Run.initialize(rc)
    left.import_local(li)
    right.import_local(ri)
    evidence = lroot / "fixture-basis.txt"
    alignment = {
        "format_version": acq.FORMAT,
        "left_view_sha256": acq.object_digest(acq.chapter_view(left)[0]),
        "right_view_sha256": acq.object_digest(acq.chapter_view(right)[0]),
        "groups": [{
            "left": ["c1"], "right": ["c1"],
            "assessment": {"status": "PASS", "reason": "Same synthetic chapter.", "evidence": [acq.ref(evidence)]},
        }],
    }
    report = acq.compare_sources(left, right, write_json(tmp_path / "alignment.json", alignment))
    assert report["is_error_rate"] is False
    assert report["aligned_groups"][0]["edit_distance"] == 2
    assert report["unaligned_left"] == []


def test_edit_distance_handles_unicode_and_bounded_work():
    assert acq.edit_distance("甲乙丙", "甲丁丙") == 1
    assert acq.edit_distance("", "ab") == 2
    assert acq.edit_distance("abc", "abc") == 0
    assert acq.edit_distance("abcdef", "uvwxyz", max_cells=2) is None


def test_review_requires_evidence_not_just_a_pass_string(tmp_path):
    config, inputs = fixture_config(tmp_path, count=1)
    run = acq.Run.initialize(config)
    run.import_local(inputs)
    review_path = reviewed(run, tmp_path)
    value = acq.read_json(review_path)
    value["samples"]["c1"]["evidence"] = []
    write_json(review_path, value)
    with pytest.raises(acq.AcquisitionError, match="evidence"):
        acq.verify(run, review_path)


def prepared_fixture(tmp_path, *, unnumbered=False):
    from test_phase0_builder import _input
    from xhnovel_pipeline.phase0_builder import _seal_brief

    config, inputs = fixture_config(tmp_path, count=1)
    if unnumbered:
        def change(cat):
            cat["entries"][0]["expected_title"] = "结束感言"
            cat["chapters"][0]["title"] = "结束感言"
            cat["chapters"][0]["role"] = "SUPPLEMENT"
        mutate_catalog(config, change)
        (inputs / "0001.txt").write_text("结束感言\n\n这是合成测试的附属材料，无任何实际小说内容。\n")
    run = acq.Run.initialize(config)
    run.import_local(inputs)
    sealed = acq.seal(run, tmp_path / "sealed", reviewed(run, tmp_path))
    draft = _input(inputs / "0001.txt")
    brief = _seal_brief(draft["brief"])
    brief_path = write_json(tmp_path / "formal-brief.json", brief)
    leads_path = write_json(tmp_path / "leads.json", draft["leads"])
    planning_path = write_json(tmp_path / "planning-input.json", {
        "format_version": acq.FORMAT,
        "brief": acq.ref(brief_path), "leads": acq.ref(leads_path), "planning": None,
    })
    prepared = acq.prepare_source(sealed, planning_path, tmp_path / "phase0")
    return sealed, prepared, planning_path


def test_t25_t32_native_preparation_preserves_brief_and_spec_hash(tmp_path):
    sealed, prepared, options = prepared_fixture(tmp_path)
    from xhnovel_pipeline.novel_spec import load_validated_direct_research_spec, SpecValidationPurpose
    spec = acq.read_json(Path(prepared["novel_spec_path"]))
    expected_brief = acq.read_json(tmp_path / "formal-brief.json")["evidence_discovery_brief"]
    assert spec["request"]["discovery_brief"] == expected_brief
    assert spec["source"]["path"] == str(sealed / "chapters")
    assert spec["source_quality"]["textual_completeness"] == "COMPLETE"
    validated = load_validated_direct_research_spec(Path(prepared["novel_spec_path"]), purpose=SpecValidationPurpose.EVIDENCE_HANDOFF)
    assert validated.resolved_spec_hash == prepared["expected_input_spec_hash"]
    assert validated.resolved_spec_hash != prepared["sealed_manifest_sha256"]
    assert acq.prepare_source(sealed, options, tmp_path / "phase0") == prepared


def test_t26_partial_native_freeze_retains_true_status(tmp_path):
    sealed, prepared, _ = prepared_fixture(tmp_path, unnumbered=True)
    result = acq.freeze_source(sealed, Path(prepared["handoff_path"]), tmp_path / "research", phase0_root=tmp_path / "phase0")
    assert result["status"] == "NATIVE_SOURCE_FROZEN"
    assert result["ingestion_status"] == "PARTIAL"
    assert result["research"] == "NOT_RUN"
    assert result["unknown_number_explanation"]["status"] == "PASS"
    assert not list((tmp_path / "research").rglob("SWIN-*.json"))


def test_t32_changed_brief_and_extra_request_fields_are_rejected(tmp_path):
    sealed, prepared, planning = prepared_fixture(tmp_path)
    spec = acq.read_json(planning)
    spec["request"] = {"discovery_brief": "evil hint"}
    write_json(planning, spec)
    with pytest.raises(acq.ValidationError):
        acq.prepare_source(sealed, planning, tmp_path / "different-phase0")
    assert not (tmp_path / "different-phase0/preparation-input.json").exists()


def test_t33_t38_t39_t40_native_agent_files_lifecycle_after_prefreeze(tmp_path):
    from test_phase0_execution import _agent_factory, _answer_all
    from xhnovel_pipeline.agent_files import AgentResponsesPending
    from xhnovel_pipeline.phase0_execution import execute_evidence_handoff
    from xhnovel_pipeline.cli import main as native_main

    sealed, prepared, _ = prepared_fixture(tmp_path)
    research = tmp_path / "research"
    frozen = acq.freeze_source(sealed, Path(prepared["handoff_path"]), research, phase0_root=tmp_path / "phase0")
    with pytest.raises(AgentResponsesPending):
        execute_evidence_handoff(
            Path(prepared["handoff_path"]), research, executor="agent-files",
            extractor_factory=_agent_factory(research), repo_root=acq.ROOT, now="2026-09-05T13:00:00Z",
        )
    tasks = list((research / "scene-scout/agent-files/tasks").glob("*.json"))
    assert tasks
    native_ingestion = list((research / "ingestion/ingestions").glob("*/novel-ingestion.json"))
    assert len(native_ingestion) == 1
    assert acq.read_json(native_ingestion[0])["ingestion_run_id"] == frozen["ingestion_run_id"]
    task = acq.read_json(tasks[0])
    answer = tasks[0].parents[1] / task["answer_file"]
    answer.parent.mkdir(parents=True, exist_ok=True)
    answer.write_text("{broken json")
    with pytest.raises(acq.ValidationError):
        execute_evidence_handoff(
            Path(prepared["handoff_path"]), research, executor="agent-files",
            extractor_factory=_agent_factory(research), repo_root=acq.ROOT, now="2026-09-05T13:00:30Z",
        )
    _answer_all(research)
    result = execute_evidence_handoff(
        Path(prepared["handoff_path"]), research, executor="agent-files",
        extractor_factory=_agent_factory(research), repo_root=acq.ROOT, now="2026-09-05T13:01:00Z", retry=True,
    )
    assert result.status == "SUCCEEDED"
    catalogs = list((research / "research").glob("*/catalog.json"))
    assert len(catalogs) == 1
    assert native_main(["validate", "all", str(catalogs[0]), "--store", str(research / "ingestion/objects")]) == 0
    # Native CAS damage is rejected; a host receipt cannot override it.
    row = acq.read_json(sealed / "chapter-view.json")["chapters"][0]
    data = (sealed / "chapters" / row["file_name"]).read_bytes()
    store = result.native_result["store"]
    artifact_id = acq.artifact_id_for(data)
    assert any(a["status"] == "REJECTED" for a in result.native_result["catalog"].all("ModelAttempt"))
    stored = store._path(artifact_id)
    stored.write_bytes(b"damaged")
    assert native_main(["validate", "all", str(catalogs[0]), "--store", str(research / "ingestion/objects")]) != 0


def test_t33_sealed_change_before_freeze_cannot_egress(tmp_path):
    sealed, prepared, _ = prepared_fixture(tmp_path)
    chapter = next((sealed / "chapters").glob("*.txt"))
    chapter.write_text(chapter.read_text() + "changed")
    with pytest.raises(acq.AcquisitionError, match="sealed files differ"):
        acq.freeze_source(sealed, Path(prepared["handoff_path"]), tmp_path / "research")
    assert not (tmp_path / "research").exists()


def test_whole_txt_import_uses_native_chapters_and_preserves_original(tmp_path):
    config, inputs = fixture_config(tmp_path, count=3)
    book = tmp_path / "book.txt"
    original = b"\n".join(p.read_bytes() for p in sorted(inputs.iterdir()))
    book.write_bytes(original)
    run = acq.Run.initialize(config)
    assert run.import_local(book)["accepted_entries"] == 3
    copies = list((run.root / "imports").glob("*.txt"))
    assert len(copies) == 1 and copies[0].read_bytes() == original
    assert all(a["channel"] == "LOCAL_ORIGINAL_IMPORT" for k in run.entries for a in run.attempts(k))
    sealed = acq.seal(run, tmp_path / "sealed", reviewed(run, tmp_path))
    book.write_text("original later changed")
    acq.validate_sealed(sealed)


def test_whole_local_book_requires_exact_catalog_instead_of_positional_guess(tmp_path):
    config, inputs = fixture_config(tmp_path, count=3)
    book = tmp_path / "partial.txt"
    book.write_bytes((inputs / "0002.txt").read_bytes())
    run = acq.Run.initialize(config)
    with pytest.raises(acq.AcquisitionError, match="chapter count"):
        run.import_local(book)
    assert not run.accepted()


def test_epub_import_uses_native_spine_and_preserves_archive(tmp_path):
    from test_novel_adapters import _write_epub
    extractor = {
        "kind": "HTML", "title_selector": "h1", "body_selector": "body",
        "exclude_selectors": [], "strip_leading_title": True,
    }
    config, _ = fixture_config(tmp_path, count=2, extractor=extractor)
    def titles(cat):
        for i, title in enumerate(["第一章 入山", "第二章 拜师"]):
            cat["entries"][i]["expected_title"] = title
            cat["chapters"][i]["title"] = title
    mutate_catalog(config, titles)
    book = tmp_path / "test.epub"
    _write_epub(book)
    run = acq.Run.initialize(config)
    assert run.import_local(book)["accepted_entries"] == 2
    assert next((run.root / "imports").glob("*.epub")).read_bytes() == book.read_bytes()
    acq.validate_sealed(acq.seal(run, tmp_path / "sealed", reviewed(run, tmp_path)))


def test_crash_before_attempt_receipt_still_delays_next_request(tmp_path):
    config, _ = fixture_config(tmp_path, channel="C1", count=1)
    clock = FakeClock()
    run = acq.Run.initialize(config, clock)
    def crash(point):
        if point == "raw":
            raise RuntimeError("before receipt")
    with pytest.raises(RuntimeError):
        run.acquire(send=lambda *_: chapter(1), crash=crash)
    calls = []
    resumed = acq.Run.initialize(config, clock)
    resumed.acquire(send=sender(clock, [chapter(1)], calls))
    assert calls[0][2] >= 40_000


def test_transport_rejects_nonpublic_target_without_retry_loop(tmp_path):
    config, _ = fixture_config(tmp_path, channel="C1", count=1)
    run = acq.Run.initialize(config, FakeClock())
    calls = []
    result = run.acquire(send=sender(run.clock, [acq.Response(None, b"", error="E-SSRF-IP")], calls))
    assert result["acquisition"] == "EXTRACTION_FAILED"
    assert len(calls) == 1


@pytest.mark.parametrize("explicit_empty", [False, True])
def test_prepare_selected_sealed_work_without_web_lead_file(tmp_path, explicit_empty):
    sealed, _, options_path = prepared_fixture(tmp_path)
    options = acq.read_json(options_path)
    if explicit_empty:
        options["leads"] = acq.ref(write_json(tmp_path / "empty-leads.json", []))
    else:
        del options["leads"]
    options_path = write_json(tmp_path / "selected-work.json", options)
    p = tmp_path / "selected-phase0"
    result = acq.prepare_source(sealed, options_path, p)
    handoff = acq.read_json(Path(result["handoff_path"]))
    assert handoff["motivating_lead_ids"] == []
    assert handoff["localization"]["execution_scope"] == "FULL_WORK"
    assert not (p / "leads").exists()
    assert acq.prepare_source(sealed, options_path, p) == result
    # An optional supplied list is still binding; it cannot be silently dropped.
    options["leads"] = acq.read_json(tmp_path / "planning-input.json")["leads"]
    write_json(options_path, options)
    with pytest.raises(acq.AcquisitionError, match="different frozen inputs"):
        acq.prepare_source(sealed, options_path, p)
