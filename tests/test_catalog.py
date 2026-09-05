from __future__ import annotations

import pytest

from xhnovel_pipeline.catalog import Catalog, indexed_catalog
from xhnovel_pipeline.errors import ValidationError


def test_scoped_index_supports_add_lookup_and_duplicate_detection():
    catalog = Catalog()
    first = catalog.add("Source", {"source_id": "first"})
    with catalog.indexed():
        second = catalog.add("Source", {"source_id": "second"})
        with catalog.indexed():
            assert catalog.get("Source", "first") is first
            assert catalog.get("Source", "second") is second
            assert catalog.contains("Source", "second")
            assert not catalog.contains("Source", "missing")
        with pytest.raises(ValidationError, match="E-DUP-ID"):
            catalog.add("Source", {"source_id": "second"})


def test_scoped_index_observes_external_mutations_between_calls():
    catalog = Catalog()
    record = catalog.add("Source", {"source_id": "first"})

    @indexed_catalog
    def lookup(catalog, ident):
        return catalog.get("Source", ident)

    assert lookup(catalog, "first") is record
    record["source_id"] = "changed"
    assert lookup(catalog, "changed") is record
    with pytest.raises(ValidationError, match="E-DANGLING-REF"):
        lookup(catalog, "first")
    with pytest.raises(ValidationError, match="E-DUP-ID"):
        catalog.add("Source", {"source_id": "changed"})
    replacement = {"source_id": "replacement"}
    catalog.by_type["Source"] = [replacement]
    assert lookup(catalog, "replacement") is replacement
    catalog.by_type["Source"].clear()
    assert not catalog.contains("Source", "replacement")


def test_scoped_index_is_discarded_after_error():
    catalog = Catalog()
    record = catalog.add("Source", {"source_id": "first"})
    with pytest.raises(RuntimeError):
        with catalog.indexed():
            raise RuntimeError("interrupted")
    record["source_id"] = "changed"
    assert catalog.get("Source", "changed") is record


def test_catalog_mapping_preserves_strict_duplicate_and_kind_errors():
    with pytest.raises(ValidationError, match="E-DUP-ID"):
        Catalog.from_mapping({"Source": [{"source_id": "same"}, {"source_id": "same"}]})
    with pytest.raises(ValidationError, match="E-CATALOG-KIND"):
        Catalog.from_mapping({"Unknown": []})


def test_index_does_not_replace_schema_validation_of_identifier_shape():
    catalog = Catalog.from_mapping({"Source": [{"source_id": ["invalid"]}]})
    with catalog.indexed():
        assert catalog.get("Source", ["invalid"])["source_id"] == ["invalid"]
        with pytest.raises(ValidationError, match="E-DUP-ID"):
            catalog.add("Source", {"source_id": ["invalid"]})
