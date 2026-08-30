from __future__ import annotations

import json

import pytest

from xhnovel_pipeline.canonical import canonical_dumps
from xhnovel_pipeline.hashing import object_hash
from xhnovel_pipeline.schema import SCHEMA_BY_TYPE, validate_schema
from xhnovel_pipeline.errors import SchemaError
from xhnovel_pipeline.constants import SCHEMA_VERSION


def test_yaml_json_same_hash():
    a = {"b": 1, "a": "x"}
    b = {"a": "x", "b": 1}
    assert canonical_dumps(a) == canonical_dumps(b)
    assert object_hash(a) == object_hash(b)


def test_self_hash_omitted():
    obj = {"bundle_id": "BND-1", "bundle_hash": "sha256:" + "a" * 64, "x": 1}
    h1 = object_hash(obj)
    obj2 = dict(obj)
    obj2["bundle_hash"] = "sha256:" + "b" * 64
    assert object_hash(obj2) == h1
    obj3 = dict(obj)
    obj3["x"] = 2
    assert object_hash(obj3) != h1


def test_research_request_unknown_field_fails():
    req = {
        "schema_version": SCHEMA_VERSION,
        "request_id": "REQ-X",
        "origin": {"repository": "r", "commit": "1234567"},
        "mode": "EXPLORE",
        "discovery_brief": "d",
        "search_constraints": {},
        "extraction_profile": "xuanhuan-gameplay-scene/v1",
        "budget": {"max_queries": 1, "max_fetches": 1},
        "created_at": "2026-08-29T00:00:00Z",
        "surprise": True,
    }
    with pytest.raises(SchemaError):
        validate_schema("ResearchRequest", req)


def test_every_schema_file_exists():
    from xhnovel_pipeline.paths import repo_root

    root = repo_root()
    for rel in SCHEMA_BY_TYPE.values():
        assert (root / "contracts" / rel).is_file()
