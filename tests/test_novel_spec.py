"""Contract lock for the P0-C1 Novel Spec validation primitives.

These freeze the pure-function behavior of ``xhnovel_pipeline.novel_spec`` directly
(input → return value / exact ValidationError code + message), rather than inferring
it through the production call sites. P0-C2 depends on these primitives, so their
contract must be pinned here. Also includes two order/side-effect sentinels that
prove the extraction did not move validation across the ingestion boundary, and an
import-cycle smoke test.
"""

from __future__ import annotations

import copy
import subprocess
import sys

import pytest

from xhnovel_pipeline import novel_spec
from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.novel_ingest import load_novel_spec, run_novel_ingestion
from xhnovel_pipeline.novel_workflow import run_novel_research
from xhnovel_pipeline.model_api import OpenAIResponsesClient
from xhnovel_pipeline.paths import repo_root
from xhnovel_pipeline.runtime import TEST_NOW as NOW


# ---------------------------------------------------------------------------
# 1. Per-primitive contract: valid input passes; invalid input raises the exact
#    same (code, message) the original inline check raised. bool is never a valid int.
# ---------------------------------------------------------------------------

VALID_CASES = [
    (novel_spec.check_spec_object, ({},), None),
    (novel_spec.check_source_object, ({"kind": "txt"},), None),
    (novel_spec.check_source_catalog, (None,), None),  # None is allowed
    (novel_spec.check_source_catalog, ([],), None),
    (novel_spec.check_limits_object, ({},), None),
    (novel_spec.check_strict_order, (True,), None),
    (novel_spec.check_strict_order, (False,), None),
]


@pytest.mark.parametrize("fn, args, _unused", VALID_CASES)
def test_primitive_accepts_valid_input(fn, args, _unused):
    assert fn(*args) is None


# (callable, args, kwargs, expected_code, expected_message)
INVALID_CASES = [
    (novel_spec.check_spec_object, ([],), {}, "E-NOVEL-SPEC", "novel spec must be an object"),
    (novel_spec.check_spec_object, ("x",), {}, "E-NOVEL-SPEC", "novel spec must be an object"),
    (novel_spec.check_source_object, (None,), {}, "E-NOVEL-SPEC", "source must be an object"),
    (novel_spec.check_source_object, ("x",), {}, "E-NOVEL-SPEC", "source must be an object"),
    (novel_spec.check_source_catalog, ({},), {}, "E-NOVEL-SOURCE-CATALOG", "source_catalog must be an array"),
    (novel_spec.check_source_catalog, ("x",), {}, "E-NOVEL-SOURCE-CATALOG", "source_catalog must be an array"),
    (novel_spec.check_limits_object, ([],), {}, "E-NOVEL-LIMIT", "limits must be an object"),
    (novel_spec.check_strict_order, ("true",), {}, "E-NOVEL-SPEC", "strict_order must be a boolean"),
    (novel_spec.check_strict_order, (1,), {}, "E-NOVEL-SPEC", "strict_order must be a boolean"),
]


@pytest.mark.parametrize("fn, args, kwargs, code, message", INVALID_CASES)
def test_primitive_rejects_with_exact_contract(fn, args, kwargs, code, message):
    with pytest.raises(ValidationError) as exc:
        fn(*args, **kwargs)
    assert exc.value.code == code
    assert str(exc.value) == f"{code}: {message}"


# ---- input_limit: default, minimum, bool rejection, exact message ----------

def test_input_limit_returns_default_when_absent():
    assert novel_spec.input_limit({}, "max_chapters", 100_000, minimum=1) == 100_000


def test_input_limit_returns_present_value():
    assert novel_spec.input_limit({"max_chapters": 7}, "max_chapters", 100_000, minimum=1) == 7


def test_input_limit_allows_zero_when_minimum_zero():
    # max_bytes uses minimum=0 in production; 0 must be accepted.
    assert novel_spec.input_limit({"max_bytes": 0}, "max_bytes", 500_000_000, minimum=0) == 0


@pytest.mark.parametrize("bad", [True, False, 1.5, "3", None, [1]])
def test_input_limit_rejects_non_int_and_bool(bad):
    with pytest.raises(ValidationError) as exc:
        novel_spec.input_limit({"max_chapters": bad}, "max_chapters", 100_000, minimum=1)
    assert exc.value.code == "E-NOVEL-LIMIT"
    assert str(exc.value) == "E-NOVEL-LIMIT: limits.max_chapters must be an integer of at least 1"


def test_input_limit_rejects_below_minimum():
    with pytest.raises(ValidationError) as exc:
        novel_spec.input_limit({"max_chapters": 0}, "max_chapters", 100_000, minimum=1)
    assert exc.value.code == "E-NOVEL-LIMIT"
    assert "at least 1" in str(exc.value)


# ---- scene window params: inclusive bounds, window_chars checked first ------

@pytest.mark.parametrize("wc", [8_000, 10_000, 12_000])
def test_scene_window_params_accepts_in_range(wc):
    assert novel_spec.check_scene_window_params(window_chars=wc, overlap_chars=int(wc * 0.17)) is None


@pytest.mark.parametrize("wc", [7_999, 12_001, 0])
def test_scene_window_params_rejects_window_chars_out_of_range(wc):
    with pytest.raises(ValidationError) as exc:
        # overlap chosen so the (unreached) overlap check would pass for wc>0
        novel_spec.check_scene_window_params(window_chars=wc, overlap_chars=1_800)
    assert exc.value.code == "E-SCENE-WINDOW"
    assert str(exc.value) == "E-SCENE-WINDOW: window_chars must be between 8000 and 12000"


@pytest.mark.parametrize("overlap", [1_000, 2_500])  # 10% and 25% of 10000
def test_scene_window_params_rejects_overlap_ratio(overlap):
    with pytest.raises(ValidationError) as exc:
        novel_spec.check_scene_window_params(window_chars=10_000, overlap_chars=overlap)
    assert exc.value.code == "E-SCENE-WINDOW"
    assert str(exc.value) == "E-SCENE-WINDOW: overlap must be between 15% and 20%"


@pytest.mark.parametrize("overlap", [1_500, 2_000])  # 15% and 20% inclusive
def test_scene_window_params_accepts_overlap_bounds(overlap):
    assert novel_spec.check_scene_window_params(window_chars=10_000, overlap_chars=overlap) is None


# ---- scene concurrency + config values -------------------------------------

@pytest.mark.parametrize("n", [1, 32, 64])
def test_scene_concurrency_accepts(n):
    assert novel_spec.check_scene_concurrency(n) is None


@pytest.mark.parametrize("bad", [0, 65, True, False, 1.0, "8", None])
def test_scene_concurrency_rejects(bad):
    with pytest.raises(ValidationError) as exc:
        novel_spec.check_scene_concurrency(bad)
    assert exc.value.code == "E-SCENE-CONCURRENCY"
    assert str(exc.value) == "E-SCENE-CONCURRENCY: max_workers must be between 1 and 64"


def test_scene_config_values_accepts_positive_ints():
    assert novel_spec.check_scene_config_values(max_input_chars=1, max_request_bytes=1_000) is None


def test_scene_config_values_reports_max_input_chars_first():
    # Tuple order must surface max_input_chars before max_request_bytes.
    with pytest.raises(ValidationError) as exc:
        novel_spec.check_scene_config_values(max_input_chars=0, max_request_bytes=0)
    assert str(exc.value) == "E-SCENE-CONFIG: max_input_chars must be a positive integer"


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "5", None])
def test_scene_config_values_rejects_max_request_bytes(bad):
    with pytest.raises(ValidationError) as exc:
        novel_spec.check_scene_config_values(max_input_chars=10, max_request_bytes=bad)
    assert exc.value.code == "E-SCENE-CONFIG"
    assert str(exc.value) == "E-SCENE-CONFIG: max_request_bytes must be a positive integer"


# ---- scene_scout options key-set -------------------------------------------

def test_scene_scout_options_accepts_known_keys():
    assert novel_spec.check_scene_scout_options({"window_chars": 10_000, "max_workers": 8}) is None
    assert novel_spec.check_scene_scout_options({}) is None


@pytest.mark.parametrize("bad", [{"unknown": 1}, {"window_chars": 1, "extra": 2}, [], "x", None])
def test_scene_scout_options_rejects(bad):
    with pytest.raises(ValidationError) as exc:
        novel_spec.check_scene_scout_options(bad)
    assert exc.value.code == "E-SCENE-CONFIG"
    assert str(exc.value) == "E-SCENE-CONFIG: scene_scout options are invalid"


# ---------------------------------------------------------------------------
# 2. Purity: primitives that receive a dict must not mutate it. The re-exported
#    rights/quality validators return deepcopies (mutating the result is safe).
# ---------------------------------------------------------------------------

def test_input_limit_does_not_mutate_limits():
    limits = {"max_chapters": 5}
    before = copy.deepcopy(limits)
    novel_spec.input_limit(limits, "max_chapters", 100_000, minimum=1)
    novel_spec.input_limit(limits, "max_bytes", 500_000_000, minimum=0)  # absent -> default
    assert limits == before


def test_declared_rights_returns_independent_copy():
    spec = {
        "rights": {
            "basis": "USER_AUTHORIZED_LOCAL_COPY",
            "may_store_full_text": True,
            "may_send_to_external_model": True,
            "may_export_excerpts": False,
        }
    }
    before = copy.deepcopy(spec)
    result = novel_spec.declared_rights(spec)
    result["basis"] = "MUTATED"
    assert spec == before  # mutating the returned copy must not touch the input


def test_declared_source_quality_defaults_and_is_independent():
    assert novel_spec.declared_source_quality({}) == {
        "edition_status": "UNKNOWN",
        "textual_completeness": "UNKNOWN",
    }
    spec = {"source_quality": {"edition_status": "OFFICIAL", "textual_completeness": "COMPLETE"}}
    before = copy.deepcopy(spec)
    result = novel_spec.declared_source_quality(spec)
    result["edition_status"] = "MUTATED"
    assert spec == before


@pytest.mark.parametrize(
    ("source_quality", "expected"),
    [
        ({"edition_status": "OFFICIAL", "textual_completeness": "COMPLETE"}, "A"),
        (
            {"edition_status": "PUBLISHED_EDITION", "textual_completeness": "COMPLETE"},
            "B",
        ),
        (
            {"edition_status": "USER_VERIFIED_COPY", "textual_completeness": "COMPLETE"},
            "B",
        ),
        ({"edition_status": "UNKNOWN", "textual_completeness": "COMPLETE"}, "B"),
        ({"edition_status": "UNOFFICIAL_COPY", "textual_completeness": "COMPLETE"}, "B"),
        ({"edition_status": "OFFICIAL", "textual_completeness": "PARTIAL"}, "D"),
        ({"edition_status": "UNKNOWN", "textual_completeness": "UNKNOWN"}, "D"),
    ],
)
def test_source_quality_tier_is_shared_with_direct_preflight(source_quality, expected):
    assert novel_spec.source_quality_tier(source_quality) == expected


# ---------------------------------------------------------------------------
# 3. Path resolution stays spec-dir-relative (not cwd-relative). This is the
#    load-bearing precondition for the input-spec-hash / ingestion identity closure.
#    load_novel_spec did NOT move modules; it still lives in novel_ingest.
# ---------------------------------------------------------------------------

def test_load_novel_spec_resolves_relative_path_against_spec_dir(tmp_path, monkeypatch):
    spec_dir = tmp_path / "specs"
    spec_dir.mkdir()
    (spec_dir / "book.txt").write_text("第一章\n正文。", encoding="utf-8")
    spec_path = spec_dir / "novel-spec.json"
    spec_path.write_text(
        '{"source": {"kind": "txt", "path": "book.txt"}, '
        '"source_catalog": [{"source": {"kind": "txt", "path": "book.txt"}}]}',
        encoding="utf-8",
    )
    # cwd is deliberately elsewhere; resolution must ignore it.
    monkeypatch.chdir(tmp_path)
    loaded = load_novel_spec(spec_path)
    expected = str((spec_dir / "book.txt").resolve())
    assert loaded["source"]["path"] == expected
    assert loaded["source_catalog"][0]["source"]["path"] == expected


def test_load_novel_spec_leaves_absolute_path_unchanged(tmp_path):
    abs_txt = tmp_path / "abs.txt"
    abs_txt.write_text("x", encoding="utf-8")
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        '{"source": {"kind": "txt", "path": "%s"}}' % str(abs_txt).replace("\\", "\\\\"),
        encoding="utf-8",
    )
    loaded = load_novel_spec(spec_path)
    assert loaded["source"]["path"] == str(abs_txt)


# ---------------------------------------------------------------------------
# 5. Import-cycle smoke: importing in several orders in a clean process must
#    not deadlock or raise (partially-initialized module) errors.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "sequence",
    [
        "import xhnovel_pipeline.novel_spec, xhnovel_pipeline.novel_ingest, xhnovel_pipeline.novel_workflow",
        "import xhnovel_pipeline.novel_workflow, xhnovel_pipeline.novel_spec",
        "import xhnovel_pipeline.scene_scout, xhnovel_pipeline.novel_spec",
        "import xhnovel_pipeline.novel_spec",
    ],
)
def test_no_import_cycle(sequence):
    result = subprocess.run(
        [sys.executable, "-c", sequence + "; print('ok')"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


# ---------------------------------------------------------------------------
# 6. Order/side-effect sentinel: a valid corpus with INVALID scene_scout options
#    must fail with the scene_scout config error only AFTER ingestion has run and
#    produced its work-dir artifacts — proving the extraction did not move that
#    check earlier across the ingestion boundary. (The rights-gate-before-ingestion
#    sentinel is already covered in test_novel_workflow.py.)
# ---------------------------------------------------------------------------

_RIGHTS = {
    "basis": "USER_AUTHORIZED_LOCAL_COPY",
    "may_store_full_text": True,
    "may_send_to_external_model": True,
    "may_export_excerpts": False,
}
_QUALITY = {"edition_status": "OFFICIAL", "textual_completeness": "COMPLETE"}


def test_invalid_scene_scout_options_fail_after_ingestion_side_effects(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("第一章 天门\n林舟触发天门机关，山路随之开启。", encoding="utf-8")
    spec = {
        "source": {"kind": "txt", "path": str(source), "title": "测试仙途"},
        "rights": dict(_RIGHTS),
        "source_quality": dict(_QUALITY),
        "request": {"discovery_brief": "寻找改变角色可行动作空间的场景"},
        "limits": {"max_chapters": 10, "max_bytes": 2_000_000},
        "scene_scout": {"window_chars": 10_000, "overlap_chars": 1_800, "bogus": 1},
        "strict_order": False,
    }
    run_dir = tmp_path / "run"
    client = OpenAIResponsesClient(
        model="scene-scout-model-snapshot",
        api_key="test-key",
        transport=lambda *a, **k: (200, {}, b"{}"),
    )
    with pytest.raises(ValidationError) as exc:
        run_novel_research(spec, run_dir, extractor_client=client, repo_root=repo_root(), now=NOW)
    assert exc.value.code == "E-SCENE-CONFIG"
    assert str(exc.value) == "E-SCENE-CONFIG: scene_scout options are invalid"
    # Ingestion ran to completion before the scene_scout options were checked.
    assert (run_dir / "ingestion").exists()
