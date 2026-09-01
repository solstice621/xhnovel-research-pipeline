from __future__ import annotations

import copy
import json

import pytest

from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.hashing import object_hash
from xhnovel_pipeline.novel_spec import (
    SpecValidationPurpose,
    load_validated_direct_research_spec,
    validate_direct_research_spec,
)


RIGHTS = {
    "basis": "USER_AUTHORIZED_LOCAL_COPY",
    "may_store_full_text": True,
    "may_send_to_external_model": True,
    "may_export_excerpts": False,
}
QUALITY_B = {
    "edition_status": "USER_VERIFIED_COPY",
    "textual_completeness": "COMPLETE",
}


def _spec(path, **updates):
    value = {
        "source": {
            "kind": "txt",
            "path": str(path),
            "title": "测试仙途",
            "author": "测试作者",
            "language": "zh",
        },
        "rights": copy.deepcopy(RIGHTS),
        "source_quality": copy.deepcopy(QUALITY_B),
        "request": {"discovery_brief": "寻找改变角色后续行动空间的场景。"},
        "limits": {"max_chapters": 10, "max_bytes": 2_000_000},
        "scene_scout": {"window_chars": 10_000, "overlap_chars": 1_800},
        "strict_order": False,
    }
    value.update(updates)
    return value


def test_handoff_preflight_returns_resolved_copied_view(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("第一章\n正文。", encoding="utf-8")
    spec = _spec(source)
    before = copy.deepcopy(spec)

    result = validate_direct_research_spec(
        spec,
        purpose=SpecValidationPurpose.EVIDENCE_HANDOFF,
    )

    assert spec == before
    assert result.effective_spec == spec
    assert result.effective_spec is not spec
    assert result.resolved_spec_hash == object_hash(spec, omit=())
    assert result.source_kind == "txt"
    assert result.normalized_source_spec["path"] == str(source.resolve())
    assert result.source_quality_tier == "B"
    assert result.discovery_brief == spec["request"]["discovery_brief"]
    assert result.execution_scope == "FULL_WORK"
    assert result.scene_scout_config == {
        "window_chars": 10_000,
        "overlap_chars": 1_800,
        "max_input_chars": 20_000,
        "max_request_bytes": 2_000_000,
        "max_workers": 8,
    }


def test_loader_hashes_path_resolved_spec(tmp_path, monkeypatch):
    spec_dir = tmp_path / "specs"
    spec_dir.mkdir()
    source = spec_dir / "book.txt"
    source.write_text("第一章\n正文。", encoding="utf-8")
    spec = _spec("book.txt")
    spec_path = spec_dir / "novel-spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = load_validated_direct_research_spec(
        spec_path,
        purpose=SpecValidationPurpose.EVIDENCE_HANDOFF,
    )

    assert result.effective_spec["source"]["path"] == str(source.resolve())
    assert result.resolved_spec_hash == object_hash(result.effective_spec, omit=())


def test_runtime_compat_keeps_default_brief_and_tier_d(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("正文。", encoding="utf-8")
    spec = _spec(source)
    spec.pop("request")
    spec["source_quality"] = {
        "edition_status": "UNKNOWN",
        "textual_completeness": "PARTIAL",
    }

    result = validate_direct_research_spec(
        spec,
        purpose=SpecValidationPurpose.RUNTIME_COMPAT,
    )

    assert result.discovery_brief == "提取小说中的关键情节、参与者、条件与状态变化。"
    assert result.source_quality_tier == "D"


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda spec: spec.pop("request"), "E-HANDOFF-BRIEF"),
        (
            lambda spec: spec.__setitem__(
                "source_quality",
                {"edition_status": "UNKNOWN", "textual_completeness": "PARTIAL"},
            ),
            "E-HANDOFF-QUALITY",
        ),
        (
            lambda spec: spec["rights"].__setitem__("may_send_to_external_model", False),
            "E-RIGHTS-EXTERNAL-MODEL",
        ),
        (
            lambda spec: spec["rights"].__setitem__("may_store_full_text", False),
            "E-RIGHTS-STORAGE",
        ),
        (lambda spec: spec["source"].pop("title"), "E-HANDOFF-SOURCE"),
        (lambda spec: spec["source"].pop("language"), "E-HANDOFF-SOURCE"),
        (
            lambda spec: spec.__setitem__("source_catalog", [{"source": {"kind": "txt"}}]),
            "E-HANDOFF-SOURCE",
        ),
        (
            lambda spec: spec.__setitem__("location_hints", ["第327章"]),
            "E-HANDOFF-SCOPE",
        ),
        (
            lambda spec: spec["scene_scout"].__setitem__("overlap_chars", 100),
            "E-SCENE-WINDOW",
        ),
    ],
)
def test_handoff_only_strictness(tmp_path, mutate, code):
    source = tmp_path / "book.txt"
    source.write_text("正文。", encoding="utf-8")
    spec = _spec(source)
    mutate(spec)
    with pytest.raises(ValidationError) as exc:
        validate_direct_research_spec(
            spec,
            purpose=SpecValidationPurpose.EVIDENCE_HANDOFF,
        )
    assert exc.value.code == code


@pytest.mark.parametrize(
    "purpose",
    [
        SpecValidationPurpose.RUNTIME_COMPAT,
        SpecValidationPurpose.EVIDENCE_HANDOFF,
    ],
)
def test_shared_rights_failure_under_both_purposes(tmp_path, purpose):
    source = tmp_path / "book.txt"
    source.write_text("正文。", encoding="utf-8")
    spec = _spec(source)
    spec["rights"]["basis"] = "UNKNOWN"

    with pytest.raises(ValidationError) as exc:
        validate_direct_research_spec(spec, purpose=purpose)
    assert exc.value.code == "E-RIGHTS-EXTERNAL-MODEL"


def test_missing_local_source_fails_preflight(tmp_path):
    missing = tmp_path / "missing.txt"
    spec = _spec(missing)
    with pytest.raises(ValidationError) as exc:
        validate_direct_research_spec(
            spec,
            purpose=SpecValidationPurpose.EVIDENCE_HANDOFF,
        )
    assert exc.value.code == "E-NOVEL-SOURCE"
    assert str(missing.resolve()) in str(exc.value)


def test_handoff_requires_loaded_absolute_local_path(tmp_path):
    spec = _spec("relative.txt")
    with pytest.raises(ValidationError) as exc:
        validate_direct_research_spec(
            spec,
            purpose=SpecValidationPurpose.EVIDENCE_HANDOFF,
        )
    assert exc.value.code == "E-HANDOFF-SOURCE"


def test_runtime_compat_does_not_reject_ignored_exploration_key(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("正文。", encoding="utf-8")
    spec = _spec(source)
    spec["location_hints"] = ["not consumed by the legacy runtime"]

    result = validate_direct_research_spec(
        spec,
        purpose=SpecValidationPurpose.RUNTIME_COMPAT,
    )
    assert result.source_kind == "txt"


def test_unknown_validation_purpose_fails(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("正文。", encoding="utf-8")
    with pytest.raises(ValidationError, match="E-NOVEL-SPEC"):
        validate_direct_research_spec(
            _spec(source),
            purpose="EVIDENCE_HANDOFF",  # type: ignore[arg-type]
        )
