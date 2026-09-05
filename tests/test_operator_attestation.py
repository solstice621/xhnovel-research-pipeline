"""Standing operator attestation: auto-prefilled, operator-attested rights basis.

The attestation is authored by the operator once and applied automatically by
``prepare-handoff`` to every SourceDeclaration prepared in that Phase 0 work
directory. Without the file, behavior is unchanged: explicit rights are required.
"""

from __future__ import annotations

import copy
import json

import pytest

from xhnovel_pipeline.errors import SchemaError, ValidationError
from xhnovel_pipeline.phase0_builder import (
    prepare_handoff_from_input,
    validate_evidence_handoff,
)
from xhnovel_pipeline.phase0_handoff import (
    attestation_rights,
    make_operator_attestation,
    make_source_declaration,
    validate_operator_attestation,
    validate_source_declaration,
)
from xhnovel_pipeline.paths import repo_root
from xhnovel_pipeline.schema import validate_schema

ROOT = repo_root()
FIXTURE = ROOT / "fixtures" / "positive" / "phase0-vertical-slice"
NOW = "2026-09-03T00:00:00Z"


def _preparation() -> dict:
    return copy.deepcopy(
        json.loads((FIXTURE / "preparation-input.json").read_text(encoding="utf-8"))
    )


def _attestation(**overrides) -> dict:
    record = make_operator_attestation(
        basis="FAIR_USE_RESEARCH",
        may_export_excerpts=False,
        attested_by="operator-test",
        scope="个人研究",
        attested_at=NOW,
    )
    return {**record, **overrides}


def test_attestation_roundtrip_and_determinism():
    one = make_operator_attestation(
        basis="FAIR_USE_RESEARCH",
        may_export_excerpts=False,
        attested_by="operator-test",
        scope="个人研究",
        attested_at=NOW,
    )
    two = make_operator_attestation(
        basis="FAIR_USE_RESEARCH",
        may_export_excerpts=False,
        attested_by="operator-test",
        scope="个人研究",
        attested_at=NOW,
    )
    assert one == two
    assert validate_operator_attestation(one) == one


def test_attestation_tamper_rejected():
    record = _attestation(scope="被篡改")
    with pytest.raises(ValidationError, match="E-PHASE0-ATTEST-BIND"):
        validate_operator_attestation(record)


def test_attestation_rejects_unknown_basis():
    with pytest.raises(ValidationError, match="E-PHASE0-ATTEST"):
        make_operator_attestation(
            basis="UNKNOWN",
            may_export_excerpts=False,
            attested_by="operator-test",
            scope="个人研究",
            attested_at=NOW,
        )
    record = _attestation(basis="UNKNOWN")
    with pytest.raises(SchemaError, match="E-SCHEMA"):
        validate_schema("OperatorAttestation", record)


def test_attestation_requires_storage_and_external_model_permission():
    record = _attestation(may_store_full_text=False)
    with pytest.raises(SchemaError, match="E-SCHEMA"):
        validate_schema("OperatorAttestation", record)
    record = _attestation(may_send_to_external_model=False)
    with pytest.raises(SchemaError, match="E-SCHEMA"):
        validate_schema("OperatorAttestation", record)


def _standalone(tmp_path, record: dict) -> None:
    (tmp_path / "operator-attestation.json").write_text(
        json.dumps(record, ensure_ascii=False),
        encoding="utf-8",
    )


def _preparation_on_disk(tmp_path) -> dict:
    (tmp_path / "novel.txt").write_bytes((FIXTURE / "novel.txt").read_bytes())
    prep = _preparation()
    prep_path = tmp_path / "preparation-input.json"
    prep_path.write_text(json.dumps(prep, ensure_ascii=False), encoding="utf-8")
    return prep


def test_prepare_handoff_auto_prefills_attestation(tmp_path):
    attestation = _attestation()
    _standalone(tmp_path, attestation)
    prep = _preparation_on_disk(tmp_path)
    del prep["source_declaration"]["rights"]
    prep_path = tmp_path / "preparation-input.json"
    prep_path.write_text(json.dumps(prep, ensure_ascii=False), encoding="utf-8")

    prepared = prepare_handoff_from_input(prep_path, tmp_path)

    assert prepared.handoff["readiness"]["rights_basis"] == "FAIR_USE_RESEARCH"
    assert prepared.handoff["readiness"]["may_store_full_text"] is True
    assert prepared.handoff["readiness"]["may_send_to_external_model"] is True
    declaration_path = next((tmp_path / "source-declarations").glob("*.json"))
    declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
    assert declaration["operator_attestation_id"] == attestation["attestation_id"]
    assert declaration["rights"] == attestation_rights(attestation)
    assert (
        tmp_path
        / "operator-attestations"
        / f"{attestation['attestation_id']}.json"
    ).is_file()
    validated = validate_evidence_handoff(prepared.handoff_path, phase0_root=tmp_path)
    assert validated["handoff_id"] == prepared.handoff["handoff_id"]


def test_prepare_handoff_attestation_formalizes_explicit_rights(tmp_path):
    attestation = _attestation()
    _standalone(tmp_path, attestation)
    prep = _preparation_on_disk(tmp_path)
    prep["source_declaration"]["rights"] = attestation_rights(attestation)
    prep_path = tmp_path / "preparation-input.json"
    prep_path.write_text(json.dumps(prep, ensure_ascii=False), encoding="utf-8")

    prepared = prepare_handoff_from_input(prep_path, tmp_path)

    declaration_path = next((tmp_path / "source-declarations").glob("*.json"))
    declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
    assert declaration["operator_attestation_id"] == attestation["attestation_id"]


@pytest.mark.parametrize("explicit_rights", [False, True], ids=["omitted", "matching"])
def test_canonical_seed_prepares_and_replays_in_a_fresh_root(tmp_path, explicit_rights):
    canonical_bytes = (ROOT / "attestations" / "operator-attestation.json").read_bytes()
    standing_path = tmp_path / "operator-attestation.json"
    standing_path.write_bytes(canonical_bytes)
    attestation = validate_operator_attestation(json.loads(canonical_bytes))
    prep = _preparation_on_disk(tmp_path)
    if explicit_rights:
        prep["source_declaration"]["rights"] = attestation_rights(attestation)
    else:
        del prep["source_declaration"]["rights"]
    prep_path = tmp_path / "preparation-input.json"
    prep_path.write_text(json.dumps(prep, ensure_ascii=False), encoding="utf-8")

    prepared = prepare_handoff_from_input(prep_path, tmp_path)

    declaration_path = next((tmp_path / "source-declarations").glob("*.json"))
    declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
    assert declaration["rights"] == attestation_rights(attestation)
    assert declaration["operator_attestation_id"] == attestation["attestation_id"]
    validated = validate_evidence_handoff(prepared.handoff_path, phase0_root=tmp_path)
    assert validated["handoff_id"] == prepared.handoff["handoff_id"]
    assert standing_path.read_bytes() == canonical_bytes


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("basis", "PUBLIC_DOMAIN"),
        ("may_store_full_text", False),
        ("may_send_to_external_model", False),
        ("may_export_excerpts", False),
    ],
)
def test_canonical_seed_rejects_each_explicit_rights_conflict(tmp_path, field, value):
    canonical_bytes = (ROOT / "attestations" / "operator-attestation.json").read_bytes()
    standing_path = tmp_path / "operator-attestation.json"
    standing_path.write_bytes(canonical_bytes)
    attestation = validate_operator_attestation(json.loads(canonical_bytes))
    prep = _preparation_on_disk(tmp_path)
    rights = attestation_rights(attestation)
    assert rights[field] != value
    rights[field] = value
    prep["source_declaration"]["rights"] = rights
    prep_path = tmp_path / "preparation-input.json"
    prep_path.write_text(json.dumps(prep, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValidationError, match="E-PHASE0-ATTEST-MISMATCH"):
        prepare_handoff_from_input(prep_path, tmp_path)

    assert standing_path.read_bytes() == canonical_bytes
    assert json.loads(prep_path.read_text(encoding="utf-8")) == prep


def test_prepare_handoff_rejects_attestation_mismatch(tmp_path):
    attestation = _attestation()
    _standalone(tmp_path, attestation)
    prep = _preparation_on_disk(tmp_path)
    prep["source_declaration"]["rights"] = {
        "basis": "PUBLIC_DOMAIN",
        "may_store_full_text": True,
        "may_send_to_external_model": True,
        "may_export_excerpts": True,
    }
    prep_path = tmp_path / "preparation-input.json"
    prep_path.write_text(json.dumps(prep, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValidationError, match="E-PHASE0-ATTEST-MISMATCH"):
        prepare_handoff_from_input(prep_path, tmp_path)


def test_prepare_handoff_requires_rights_without_attestation(tmp_path):
    prep = _preparation_on_disk(tmp_path)
    del prep["source_declaration"]["rights"]
    prep_path = tmp_path / "preparation-input.json"
    prep_path.write_text(json.dumps(prep, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValidationError, match="E-PHASE0-PREPARE"):
        prepare_handoff_from_input(prep_path, tmp_path)


def test_handoff_replay_requires_standing_attestation(tmp_path):
    attestation = _attestation()
    _standalone(tmp_path, attestation)
    prep = _preparation_on_disk(tmp_path)
    del prep["source_declaration"]["rights"]
    prep_path = tmp_path / "preparation-input.json"
    prep_path.write_text(json.dumps(prep, ensure_ascii=False), encoding="utf-8")
    prepared = prepare_handoff_from_input(prep_path, tmp_path)

    (tmp_path / "operator-attestation.json").unlink()

    with pytest.raises(ValidationError, match="E-PHASE0-ATTEST-BIND"):
        validate_evidence_handoff(prepared.handoff_path, phase0_root=tmp_path)


def test_make_source_declaration_binds_attestation_id(tmp_path):
    attestation = _attestation()
    work = {
        "identity": {
            "basis": "TITLE_AUTHOR",
            "normalized_title": "测试仙途",
            "normalized_author": "测试作者",
            "language": "zh",
        },
        "canonical_title": "测试仙途",
        "author": "测试作者",
        "language": "zh",
        "aliases": [],
        "external_ids": [],
    }
    source = {"kind": "txt", "path": str(tmp_path / "book.txt")}
    declaration = make_source_declaration(
        work=work,
        source=source,
        rights=attestation_rights(attestation),
        source_quality={
            "edition_status": "USER_VERIFIED_COPY",
            "textual_completeness": "COMPLETE",
        },
        edition_label="用户授权副本",
        declared_at=NOW,
        operator_attestation_id=attestation["attestation_id"],
    )
    validate_source_declaration(declaration)
    assert declaration["operator_attestation_id"] == attestation["attestation_id"]
    plain = make_source_declaration(
        work=work,
        source=source,
        rights=attestation_rights(attestation),
        source_quality={
            "edition_status": "USER_VERIFIED_COPY",
            "textual_completeness": "COMPLETE",
        },
        edition_label="用户授权副本",
        declared_at=NOW,
    )
    assert plain["declaration_hash"] != declaration["declaration_hash"]
    assert "operator_attestation_id" not in plain


def test_operator_attestation_stays_out_of_core_catalog():
    from xhnovel_pipeline.catalog import Catalog, ID_FIELDS

    assert "OperatorAttestation" not in ID_FIELDS
    with pytest.raises(ValidationError, match="E-CATALOG-KIND"):
        Catalog().add("OperatorAttestation", _attestation())
