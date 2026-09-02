from __future__ import annotations

import copy
from dataclasses import asdict

import pytest

from xhnovel_pipeline.canonical import canonical_dumps
from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.hashing import object_hash
from xhnovel_pipeline.ids import derived_id
from xhnovel_pipeline.novel_spec import SpecValidationPurpose, validate_direct_research_spec
from xhnovel_pipeline.phase0_handoff import (
    group_leads_for_source,
    make_exploration_brief,
    make_research_lead,
    make_source_declaration,
    normalize_author,
    source_ref_from_validated,
    validate_exploration_brief,
    validate_research_lead,
    validate_source_declaration,
    work_ref_from_declaration,
)
from xhnovel_pipeline.phase0_planning import normalize_seed_value

NOW = "2026-09-01T00:00:00Z"
LATER = "2026-09-02T00:00:00Z"
RIGHTS = {
    "basis": "USER_AUTHORIZED_LOCAL_COPY",
    "may_store_full_text": True,
    "may_send_to_external_model": True,
    "may_export_excerpts": False,
}
QUALITY = {"edition_status": "USER_VERIFIED_COPY", "textual_completeness": "COMPLETE"}


def test_phase_minus1_work_seed_identity_reuses_frozen_title_normalization():
    decorated = normalize_seed_value("works", "《测试仙途》（网络小说）")
    plain = normalize_seed_value("works", "测试仙途")
    assert decorated == plain == "测试仙途"
    first = derived_id("Seed", {"bucket": "works", "normalized_value": decorated})
    second = derived_id("Seed", {"bucket": "works", "normalized_value": plain})
    assert first == second


def _brief(*, text="寻找对象控制变化。", frozen_at=NOW):
    return make_exploration_brief(
        research_question="寻找玄幻作品中的对象控制桥段。",
        evidence_discovery_brief=text,
        scope={"genres": ["仙侠", "玄幻"], "target_leads": 6, "max_leads_per_work": 3},
        frozen_at=frozen_at,
    )


def _lead(brief, *, summary, author="测试作者", title="测试仙途", frozen_at=NOW):
    source = {
        "source_kind": "ENCYCLOPEDIA",
        "locator": f"https://example.invalid/{summary}",
        "title": None,
        "publisher": None,
        "supports": ["LOCATION_HINT", "WORK_IDENTITY", "SCENE_EXISTENCE_HINT"],
    }
    provisional = make_research_lead(
        brief_id=brief["brief_id"],
        work_claim={"title": title, "author": author, "language": "zh", "aliases": []},
        scene_hint={
            "summary": summary,
            "why_relevant": "压力测试对象控制与使用权限。",
            "interaction_tags": ["object_control"],
            "location_hints": [],
        },
        lead_sources=[source],
        frozen_at=frozen_at,
    )
    source_id = provisional["lead_sources"][0]["lead_source_id"]
    return make_research_lead(
        brief_id=brief["brief_id"],
        work_claim={"title": title, "author": author, "language": "zh", "aliases": []},
        scene_hint={
            "summary": summary,
            "why_relevant": "压力测试对象控制与使用权限。",
            "interaction_tags": ["object_control"],
            "location_hints": [
                {
                    "kind": "CHARACTER",
                    "value": summary,
                    "basis": "SOURCE_STATED",
                    "lead_source_ids": [source_id],
                }
            ],
        },
        lead_sources=[source],
        frozen_at=frozen_at,
    )


def _work(*, title="测试仙途", author="测试作者", identity=None, external_ids=None):
    normalized_author = normalize_author(author)
    return {
        "identity": identity
        or {
            "basis": "TITLE_AUTHOR",
            "normalized_title": "测试仙途",
            "normalized_author": normalized_author,
            "language": "zh",
        },
        "canonical_title": title,
        "author": author,
        "language": "zh",
        "aliases": [],
        "external_ids": external_ids or [],
    }


def _declaration(path, *, work=None, rights=None, quality=None, declared_at=NOW, source_extra=None):
    source = {"kind": "txt", "path": str(path)}
    source.update(source_extra or {})
    return make_source_declaration(
        work=work or _work(),
        source=source,
        rights=rights or RIGHTS,
        source_quality=quality or QUALITY,
        edition_label="用户授权副本",
        declared_at=declared_at,
    )


def _spec_from(brief, declaration):
    work = declaration["work"]
    source = copy.deepcopy(declaration["source"])
    source.update(
        {"title": work["canonical_title"], "author": work["author"], "language": work["language"]}
    )
    return {
        "source": source,
        "rights": copy.deepcopy(declaration["rights"]),
        "source_quality": copy.deepcopy(declaration["source_quality"]),
        "request": {"discovery_brief": brief["evidence_discovery_brief"]},
        "limits": {"max_chapters": 100_000, "max_bytes": 500_000_000},
        "scene_scout": {
            "window_chars": 10_000,
            "overlap_chars": 1_800,
            "max_input_chars": 20_000,
            "max_request_bytes": 2_000_000,
            "max_workers": 8,
        },
        "strict_order": False,
    }


def _validated(brief, declaration):
    return validate_direct_research_spec(
        _spec_from(brief, declaration),
        purpose=SpecValidationPurpose.EVIDENCE_HANDOFF,
    )


def test_semantic_brief_and_lead_identity_ignore_record_time():
    first = _brief(frozen_at=NOW)
    second = _brief(frozen_at=LATER)
    assert first["brief_id"] == second["brief_id"]
    assert first["brief_hash"] == second["brief_hash"]
    first_lead = _lead(first, summary="拍卖会", frozen_at=NOW)
    second_lead = _lead(first, summary="拍卖会", frozen_at=LATER)
    assert first_lead["lead_id"] == second_lead["lead_id"]
    assert first_lead["lead_hash"] == second_lead["lead_hash"]


def test_records_reject_identity_tampering():
    brief = _brief()
    brief["research_question"] = "tampered"
    with pytest.raises(ValidationError, match="E-PHASE0-BRIEF-BIND"):
        validate_exploration_brief(brief)
    lead = _lead(_brief(), summary="拍卖会")
    lead["scene_hint"]["summary"] = "tampered"
    with pytest.raises(ValidationError, match="E-PHASE0-LEAD-BIND"):
        validate_research_lead(lead)


@pytest.mark.parametrize(
    ("first_title", "second_title", "normalized_title"),
    [
        (" 测试仙途 ", "测试仙途", "测试仙途"),
        ("《测试仙途》", "测试仙途", "测试仙途"),
        ("测试仙途 — Wikipedia", "测试仙途", "测试仙途"),
        ("测试仙途（网络小说）", "测试仙途", "测试仙途"),
        ("测试   仙途", "测试 仙途", "测试 仙途"),
    ],
)
def test_title_author_normalization_uses_existing_title_rules(
    tmp_path,
    first_title,
    second_title,
    normalized_title,
):
    identity = {
        "basis": "TITLE_AUTHOR",
        "normalized_title": normalized_title,
        "normalized_author": "测试作者",
        "language": "zh",
    }
    first = _declaration(
        tmp_path / "book.txt",
        work=_work(title=first_title, identity=identity),
    )
    second = _declaration(
        tmp_path / "book.txt",
        work=_work(title=second_title, identity=identity),
    )
    assert work_ref_from_declaration(first)["work_ref_id"] == work_ref_from_declaration(second)["work_ref_id"]


def test_author_normalization_only_strips_and_collapses_whitespace(tmp_path):
    normalized = _declaration(
        tmp_path / "book.txt",
        work=_work(
            author="  测试\t作者  ",
            identity={
                "basis": "TITLE_AUTHOR",
                "normalized_title": "测试仙途",
                "normalized_author": "测试 作者",
                "language": "zh",
            },
        ),
    )
    canonical = _declaration(
        tmp_path / "book.txt",
        work=_work(
            author="测试 作者",
            identity={
                "basis": "TITLE_AUTHOR",
                "normalized_title": "测试仙途",
                "normalized_author": "测试 作者",
                "language": "zh",
            },
        ),
    )
    punctuated = _declaration(
        tmp_path / "book.txt",
        work=_work(
            author="测试·作者",
            identity={
                "basis": "TITLE_AUTHOR",
                "normalized_title": "测试仙途",
                "normalized_author": "测试·作者",
                "language": "zh",
            },
        ),
    )
    assert work_ref_from_declaration(normalized)["work_ref_id"] == work_ref_from_declaration(
        canonical
    )["work_ref_id"]
    assert work_ref_from_declaration(normalized)["work_ref_id"] != work_ref_from_declaration(
        punctuated
    )["work_ref_id"]


def test_title_author_identity_distinguishes_authors_and_rejects_title_only(tmp_path):
    first = _declaration(tmp_path / "book.txt", work=_work(author="甲"))
    second = _declaration(
        tmp_path / "book.txt",
        work=_work(
            author="乙",
            identity={
                "basis": "TITLE_AUTHOR",
                "normalized_title": "测试仙途",
                "normalized_author": "乙",
                "language": "zh",
            },
        ),
    )
    assert work_ref_from_declaration(first)["work_ref_id"] != work_ref_from_declaration(second)[
        "work_ref_id"
    ]

    with pytest.raises(ValidationError, match="requires an author"):
        _declaration(
            tmp_path / "book.txt",
            work=_work(
                author=None,
                identity={
                    "basis": "TITLE_AUTHOR",
                    "normalized_title": "测试仙途",
                    "normalized_author": "未知",
                    "language": "zh",
                },
            ),
        )


def test_general_punctuation_is_not_folded(tmp_path):
    punctuated = _work(
        title="测试·仙途",
        identity={
            "basis": "TITLE_AUTHOR",
            "normalized_title": "测试·仙途",
            "normalized_author": "测试作者",
            "language": "zh",
        },
    )
    first = _declaration(tmp_path / "book.txt", work=punctuated)
    second = _declaration(tmp_path / "book.txt")
    assert work_ref_from_declaration(first)["work_ref_id"] != work_ref_from_declaration(second)["work_ref_id"]


def test_external_id_basis_prevents_same_title_collision(tmp_path):
    def declaration(external_id):
        return _declaration(
            tmp_path / "book.txt",
            work=_work(
                author=None,
                identity={"basis": "STABLE_EXTERNAL_ID", "namespace": "qidian", "external_id": external_id},
                external_ids=[{"namespace": "QIDIAN", "value": external_id}],
            ),
        )

    assert work_ref_from_declaration(declaration("1001"))["work_ref_id"] != work_ref_from_declaration(
        declaration("2002")
    )["work_ref_id"]


def test_external_id_namespace_is_casefolded_but_value_case_is_preserved(tmp_path):
    def declaration(namespace, external_id):
        return _declaration(
            tmp_path / "book.txt",
            work=_work(
                author=None,
                identity={
                    "basis": "STABLE_EXTERNAL_ID",
                    "namespace": namespace,
                    "external_id": external_id,
                },
                external_ids=[{"namespace": namespace, "value": external_id}],
            ),
        )

    upper_namespace = work_ref_from_declaration(declaration(" QIDIAN ", "Book-A"))
    lower_namespace = work_ref_from_declaration(declaration("qidian", "Book-A"))
    lower_value = work_ref_from_declaration(declaration("qidian", "book-a"))
    assert upper_namespace["work_ref_id"] == lower_namespace["work_ref_id"]
    assert upper_namespace["work_ref_id"] != lower_value["work_ref_id"]
    assert upper_namespace["identity"]["namespace"] == "qidian"


def test_user_confirmed_basis_depends_on_confirmation_artifact(tmp_path):
    def declaration(digest):
        return _declaration(
            tmp_path / "book.txt",
            work=_work(
                author=None,
                identity={
                    "basis": "USER_CONFIRMED",
                    "confirmation_artifact_id": "sha256:" + digest * 64,
                },
            ),
        )

    assert work_ref_from_declaration(declaration("a"))["work_ref_id"] != work_ref_from_declaration(
        declaration("b")
    )["work_ref_id"]


def test_source_ref_depends_on_source_config_not_rights_or_brief(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("第一章\n正文。", encoding="utf-8")
    brief_a = _brief(text="寻找对象控制变化。")
    brief_b = _brief(text="寻找持续义务。")
    declaration_a = _declaration(source)
    declaration_rights = _declaration(source, rights={**RIGHTS, "may_export_excerpts": True})
    validated_a = _validated(brief_a, declaration_a)
    ref_a = source_ref_from_validated(
        declaration_a,
        validated_a,
        work_ref_from_declaration(declaration_a),
    )
    assert ref_a["source_config_hash"] == object_hash(
        validated_a.normalized_source_spec,
        omit=(),
    )
    validated_rights = _validated(brief_a, declaration_rights)
    ref_rights = source_ref_from_validated(
        declaration_rights,
        validated_rights,
        work_ref_from_declaration(declaration_rights),
    )
    validated_brief = _validated(brief_b, declaration_a)
    ref_brief = source_ref_from_validated(
        declaration_a,
        validated_brief,
        work_ref_from_declaration(declaration_a),
    )
    assert ref_a["source_ref_id"] == ref_rights["source_ref_id"] == ref_brief["source_ref_id"]
    assert validated_a.resolved_spec_hash != validated_rights.resolved_spec_hash
    assert validated_a.resolved_spec_hash != validated_brief.resolved_spec_hash
    declaration_c = _declaration(source, source_extra={"encoding": "utf-8"})
    ref_c = source_ref_from_validated(
        declaration_c,
        _validated(brief_a, declaration_c),
        work_ref_from_declaration(declaration_c),
    )
    assert ref_c["source_ref_id"] != ref_a["source_ref_id"]


def test_source_ref_rejects_a_work_ref_not_derived_from_the_declaration(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("第一章\n正文。", encoding="utf-8")
    brief = _brief()
    declaration = _declaration(source)
    work_ref = work_ref_from_declaration(declaration)
    work_ref["work_ref_id"] = "WREF-UNBOUND"
    with pytest.raises(ValidationError, match="work reference differs"):
        source_ref_from_validated(declaration, _validated(brief, declaration), work_ref)


def test_n_leads_group_to_one_work_source_brief(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("第一章\n正文。", encoding="utf-8")
    brief = _brief()
    declaration = _declaration(source)
    leads = [
        _lead(brief, summary="拍卖会"),
        _lead(brief, summary="法器认主"),
        _lead(brief, summary="战利品分配"),
    ]
    group = group_leads_for_source(
        brief=brief,
        leads=reversed(leads),
        declaration=declaration,
        validated_spec=_validated(brief, declaration),
    )
    assert group.motivating_lead_ids == tuple(sorted(lead["lead_id"] for lead in leads))
    assert len(group.hint_refs) == 3
    assert all("value" not in ref for ref in group.hint_refs)
    smaller = group_leads_for_source(
        brief=brief,
        leads=[leads[0]],
        declaration=declaration,
        validated_spec=_validated(brief, declaration),
    )
    assert smaller.group_key == group.group_key


def test_grouping_is_byte_stable_and_separates_briefs_and_sources(tmp_path):
    source_a = tmp_path / "book-a.txt"
    source_b = tmp_path / "book-b.txt"
    source_a.write_text("第一章\n正文。", encoding="utf-8")
    source_b.write_text("第一章\n另一版正文。", encoding="utf-8")
    brief_a = _brief(text="寻找对象控制变化。")
    brief_b = _brief(text="寻找持续义务。")
    declaration_a = _declaration(source_a)
    declaration_b = _declaration(source_b)
    leads_a = [
        _lead(brief_a, summary="拍卖会"),
        _lead(brief_a, summary="法器认主"),
        _lead(brief_a, summary="战利品分配"),
    ]

    ordered = group_leads_for_source(
        brief=brief_a,
        leads=leads_a,
        declaration=declaration_a,
        validated_spec=_validated(brief_a, declaration_a),
    )
    reversed_input = group_leads_for_source(
        brief=brief_a,
        leads=reversed(leads_a),
        declaration=declaration_a,
        validated_spec=_validated(brief_a, declaration_a),
    )
    ordered_payload = asdict(ordered)
    reversed_payload = asdict(reversed_input)
    for payload in (ordered_payload, reversed_payload):
        payload["motivating_lead_ids"] = list(payload["motivating_lead_ids"])
        payload["hint_refs"] = list(payload["hint_refs"])
    assert canonical_dumps(ordered_payload) == canonical_dumps(reversed_payload)

    other_brief = group_leads_for_source(
        brief=brief_b,
        leads=[_lead(brief_b, summary="拍卖会")],
        declaration=declaration_a,
        validated_spec=_validated(brief_b, declaration_a),
    )
    other_source = group_leads_for_source(
        brief=brief_a,
        leads=leads_a,
        declaration=declaration_b,
        validated_spec=_validated(brief_a, declaration_b),
    )
    assert ordered.source_ref["source_ref_id"] == other_brief.source_ref["source_ref_id"]
    assert ordered.group_key != other_brief.group_key
    assert ordered.work_ref["work_ref_id"] == other_source.work_ref["work_ref_id"]
    assert ordered.source_ref["source_ref_id"] != other_source.source_ref["source_ref_id"]
    assert ordered.group_key != other_source.group_key


def test_group_rejects_other_brief_and_other_work(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("正文。", encoding="utf-8")
    brief = _brief()
    declaration = _declaration(source)
    with pytest.raises(ValidationError, match="another exploration brief"):
        group_leads_for_source(
            brief=brief,
            leads=[_lead(_brief(text="其他问题"), summary="拍卖会")],
            declaration=declaration,
            validated_spec=_validated(brief, declaration),
        )
    with pytest.raises(ValidationError, match="resolved work"):
        group_leads_for_source(
            brief=brief,
            leads=[_lead(brief, summary="拍卖会", author="另一作者")],
            declaration=declaration,
            validated_spec=_validated(brief, declaration),
        )


def test_group_rejects_duplicate_lead_ids(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("正文。", encoding="utf-8")
    brief = _brief()
    declaration = _declaration(source)
    lead = _lead(brief, summary="拍卖会")
    with pytest.raises(ValidationError, match="duplicate leads"):
        group_leads_for_source(
            brief=brief,
            leads=[lead, copy.deepcopy(lead)],
            declaration=declaration,
            validated_spec=_validated(brief, declaration),
        )


def test_group_rejects_conflicting_claim_authors_when_resolved_author_is_unknown(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("正文。", encoding="utf-8")
    brief = _brief()
    declaration = _declaration(
        source,
        work=_work(
            author=None,
            identity={
                "basis": "STABLE_EXTERNAL_ID",
                "namespace": "qidian",
                "external_id": "1001",
            },
            external_ids=[{"namespace": "qidian", "value": "1001"}],
        ),
    )
    with pytest.raises(ValidationError, match="conflicting authors"):
        group_leads_for_source(
            brief=brief,
            leads=[
                _lead(brief, summary="拍卖会", author="甲"),
                _lead(brief, summary="法器认主", author="乙"),
            ],
            declaration=declaration,
            validated_spec=_validated(brief, declaration),
        )


def test_group_rejects_rights_projection_drift(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("正文。", encoding="utf-8")
    brief = _brief()
    declaration = _declaration(source)
    spec = _spec_from(brief, declaration)
    spec["rights"]["may_export_excerpts"] = True
    validated = validate_direct_research_spec(spec, purpose=SpecValidationPurpose.EVIDENCE_HANDOFF)
    with pytest.raises(ValidationError, match="rights differ"):
        group_leads_for_source(
            brief=brief,
            leads=[_lead(brief, summary="拍卖会")],
            declaration=declaration,
            validated_spec=validated,
        )


def test_group_rejects_source_and_quality_projection_drift(tmp_path):
    source_a = tmp_path / "book-a.txt"
    source_b = tmp_path / "book-b.txt"
    source_a.write_text("正文。", encoding="utf-8")
    source_b.write_text("另一版正文。", encoding="utf-8")
    brief = _brief()
    declaration_a = _declaration(source_a)
    declaration_b = _declaration(source_b)
    with pytest.raises(ValidationError, match="source declaration projection"):
        group_leads_for_source(
            brief=brief,
            leads=[_lead(brief, summary="拍卖会")],
            declaration=declaration_a,
            validated_spec=_validated(brief, declaration_b),
        )

    quality_drift = _declaration(
        source_a,
        quality={"edition_status": "PUBLISHED_EDITION", "textual_completeness": "COMPLETE"},
    )
    with pytest.raises(ValidationError, match="quality differs"):
        group_leads_for_source(
            brief=brief,
            leads=[_lead(brief, summary="拍卖会")],
            declaration=declaration_a,
            validated_spec=_validated(brief, quality_drift),
        )


def test_source_declaration_requires_absolute_local_path():
    with pytest.raises(ValidationError, match="must be absolute"):
        make_source_declaration(
            work=_work(),
            source={"kind": "txt", "path": "relative.txt"},
            rights=RIGHTS,
            source_quality=QUALITY,
            edition_label="副本",
            declared_at=NOW,
        )


def test_source_declaration_identity_ignores_declared_time(tmp_path):
    path = tmp_path / "book.txt"
    first = _declaration(path, declared_at=NOW)
    second = _declaration(path, declared_at=LATER)
    assert first["source_declaration_id"] == second["source_declaration_id"]
    assert first["declaration_hash"] == second["declaration_hash"]
    validate_source_declaration(first)
