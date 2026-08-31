from __future__ import annotations

import ast
import json

import yaml

from xhnovel_pipeline.paths import repo_root
from xhnovel_pipeline.schema import SCHEMA_BY_TYPE


def _relative_imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module
    }


def test_primary_workflow_and_cli_reach_scene_scout_not_legacy_plot_runtime():
    package = repo_root() / "src" / "xhnovel_pipeline"
    workflow = package / "novel_workflow.py"
    cli = package / "cli.py"
    workflow_imports = _relative_imports(workflow)
    cli_imports = _relative_imports(cli)

    assert "scene_scout" in workflow_imports
    assert "scene_scout" in cli_imports
    assert not {
        "plot_extraction",
        "plot_analysis",
        "model_collection",
        "collection_quality",
    } & workflow_imports
    combined = workflow.read_text(encoding="utf-8") + cli.read_text(encoding="utf-8")
    assert "run_model_plot_extraction" not in combined
    assert "run_plot_analysis" not in combined
    assert "analyst_client" not in combined
    assert "collector_model" not in combined
    assert "reviewer_model" not in combined


def test_collection_decision_surface_has_only_explicit_rubric_bound_tasks():
    contract = json.loads(
        (repo_root() / "contracts" / "collection-decision.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert contract["properties"]["task"]["enum"] == ["TRIAGE", "CHAPTER_IDENTITY"]
    required = set(contract["required"])
    assert {"rubric_id", "rubric_artifact_id", "input_manifest_hash"} <= required
    parameters = contract["properties"]["assessor_parameters"]
    assert {"task", "rubric_id", "rubric_hash", "task_schema_hash"} <= set(
        parameters["required"]
    )


def test_active_policy_manifest_is_resolvable_and_scene_oriented():
    root = repo_root()
    manifest = yaml.safe_load((root / "policies" / "manifest.yaml").read_text(encoding="utf-8"))
    documents = manifest["documents"]

    assert len(documents) == len(set(documents))
    assert "policies/novel-source-rights-v1.yaml" in documents
    assert "policies/scene-discovery-v1.yaml" in documents
    assert "policies/plot-analysis-v1.yaml" not in documents
    assert "policies/collection-quality-v1.yaml" not in documents
    assert all((root / relative).is_file() for relative in documents)


def test_every_registered_contract_exists_in_the_distribution_tree():
    contracts = repo_root() / "contracts"
    missing = [relative for relative in SCHEMA_BY_TYPE.values() if not (contracts / relative).is_file()]
    assert missing == []


def test_runtime_relative_imports_resolve_to_package_modules():
    package = repo_root() / "src" / "xhnovel_pipeline"
    available = {path.stem for path in package.glob("*.py")}
    missing = []
    for path in sorted(package.glob("*.py")):
        for module in _relative_imports(path):
            if module not in available:
                missing.append(f"{path.name}:{module}")
    assert missing == []
