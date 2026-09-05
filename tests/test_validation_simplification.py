from __future__ import annotations

import json
from collections import Counter

import pytest

from xhnovel_pipeline import novel_ingest, schema, validate
from xhnovel_pipeline.errors import SchemaError
from test_novel_workflow import _candidate, _response, _run


def test_schema_assets_are_reused_only_within_one_operation(tmp_path, monkeypatch):
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    path = contracts / "artifact.schema.json"
    path.write_text(json.dumps({"type": "object", "properties": {"value": {"const": 1}}}))
    loads = []
    registry = schema._registry

    def counted(root):
        loads.append(root)
        return registry(root)

    monkeypatch.setattr(schema, "_registry", counted)
    with schema.schema_validation_session():
        for _ in range(4):
            with schema.schema_validation_session():
                schema.validate_schema("Artifact", {"value": 1}, root=tmp_path)
    assert len(loads) == 1
    path.write_text(json.dumps({"type": "object", "properties": {"value": {"const": 2}}}))
    with pytest.raises(SchemaError):
        schema.validate_schema("Artifact", {"value": 1}, root=tmp_path)
    assert len(loads) == 2


def test_legitimate_source_words_are_exportable_and_core_validation_runs_once(tmp_path, monkeypatch):
    marker = "林舟触发编号 M-1 的天门机关"

    def transport(url, headers, body, timeout):
        spans = json.loads(json.loads(body)["input"])["window"]["source_spans"]
        span = next(item for item in spans if marker in item["untrusted_text"])
        start = span["start"] + span["untrusted_text"].index(marker)
        candidate = _candidate({"segment_id": span["segment_id"], "start": start, "end": start + len(marker)})
        candidate["summary"] = marker
        return 200, {}, _response({"candidates": [candidate]})

    result = _run(tmp_path, text="第一章 天门\n" + marker + "。", transport=transport)
    assert result["export"]["scene_candidates"][0]["summary"] == marker
    calls = Counter()
    for module, name in (
        (novel_ingest, "validate_novel_ingestion"),
        (validate, "validate_collection"),
        (validate, "validate_evidence"),
    ):
        original = getattr(module, name)

        def counted(*args, _name=name, _original=original, **kwargs):
            calls[_name] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, name, counted)
    validate.validate_all(result["catalog"], result["store"])
    assert calls == {
        "validate_novel_ingestion": 1,
        "validate_collection": 1,
        "validate_evidence": 1,
    }


def test_scene_programming_errors_keep_the_original_exception(tmp_path):
    def broken_transport(url, headers, body, timeout):
        raise TypeError("executor programming error")

    with pytest.raises(TypeError, match="executor programming error"):
        _run(tmp_path, transport=broken_transport)
