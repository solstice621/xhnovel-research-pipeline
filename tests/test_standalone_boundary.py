from __future__ import annotations

import ast
from pathlib import Path

import yaml

from xhnovel_pipeline.paths import repo_root
from xhnovel_pipeline.schema import SCHEMA_BY_TYPE


FORBIDDEN_MODULES = {
    "audit",
    "collection",
    "engine",
    "extraction",
    "hardening",
    "importer",
    "origin",
    "page_kind",
    "qualification",
    "stop",
    "tools_legacy",
    "wikipedia",
}

FORBIDDEN_CONTRACTS = {
    "artifact-replica-status.schema.json",
    "assurance-record.schema.json",
    "discovery-hit.schema.json",
    "origin-assessment.schema.json",
    "qualification.schema.json",
    "query-spec.schema.json",
    "search-campaign.schema.json",
    "search-run.schema.json",
}


def test_retired_g0_g12_modules_and_assets_are_absent():
    root = repo_root()
    package = root / "src" / "xhnovel_pipeline"
    assert not (root / "AGENTS.md").exists()
    assert not (root / "IMPLEMENTATION_PLAN.md").exists()
    assert not (root / "SKILL.md").exists()
    assert not (root / "fixtures" / "legacy").exists()
    for name in FORBIDDEN_MODULES:
        assert not (package / f"{name}.py").exists(), name
    for name in FORBIDDEN_CONTRACTS:
        assert not (root / "contracts" / name).exists(), name


def test_runtime_import_graph_does_not_reference_retired_modules():
    root = repo_root()
    package = root / "src" / "xhnovel_pipeline"
    violations: list[str] = []
    missing: list[str] = []
    available = {path.stem for path in package.glob("*.py")}
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level != 1 or not node.module:
                continue
            module = node.module.split(".", 1)[0]
            if module in FORBIDDEN_MODULES:
                violations.append(f"{path.name}:{node.lineno}:{module}")
            if module not in available:
                missing.append(f"{path.name}:{node.lineno}:{module}")
    assert violations == []
    assert missing == []


def test_policy_manifest_contains_only_standalone_runtime_policies():
    root = repo_root()
    manifest = yaml.safe_load((root / "policies" / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["documents"] == [
        "policies/collection-quality-v1.yaml",
        "policies/novel-fame-ranking-v1.yaml",
        "policies/plot-analysis-v1.yaml",
    ]


def test_every_registered_contract_exists_in_the_standalone_tree():
    contracts = repo_root() / "contracts"
    missing = [relative for relative in SCHEMA_BY_TYPE.values() if not (contracts / relative).is_file()]
    assert missing == []
