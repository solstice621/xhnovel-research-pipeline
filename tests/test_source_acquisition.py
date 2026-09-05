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
