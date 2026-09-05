from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from xhnovel_pipeline.errors import PipelineError, ValidationError
from xhnovel_pipeline.generic_agent_files import GenericAgentFileExecutor, GenericAgentResponsesPending
from xhnovel_pipeline.generic_cli import _agent_root, make_generic_executor
from xhnovel_pipeline.generic_extraction import (
    GenericExtractionPartial,
    executor_descriptor,
    generic_work_dir_lock,
    run_generic_corpus_workflow,
    run_generic_extraction,
    run_generic_reduction,
    validate_generic_work_dir,
    validate_selected_generic_corpus,
)

from test_generic_extraction import (
    NOW, ROOT, CountingApiExecutor, _combined_answer, _write_novel, _write_profile,
)


def _selected(spec, work_dir, result, *, profiles_root=None):
    return validate_selected_generic_corpus(
        spec, work_dir, profile_ref=result.extraction.profile.slug,
        extraction_run_id=result.extraction.run["extraction_run_id"],
        reduction_run_id=result.reduction_run["reduction_run_id"],
        corpus_snapshot_id=result.corpus_snapshot["corpus_snapshot_id"],
        profiles_root=profiles_root, root=ROOT,
    )


@pytest.mark.parametrize("entry", ["workflow", "extraction", "reduction"])
def test_public_mutations_contend_across_processes(tmp_path: pathlib.Path, entry: str) -> None:
    # The caller owns the same physical directory regardless of Handoff identity.
    script = """
import pathlib, sys
from types import SimpleNamespace
from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.generic_extraction import (
    run_generic_corpus_workflow, run_generic_extraction, run_generic_reduction,
)
work = pathlib.Path(sys.argv[1])
try:
    if sys.argv[2] == 'reduction':
        run_generic_reduction(SimpleNamespace(paths=SimpleNamespace(
            shared_root=work / 'generic-extraction')), now='2026-09-05T00:00:00Z')
    else:
        function = run_generic_corpus_workflow if sys.argv[2] == 'workflow' else run_generic_extraction
        function({}, work, profile_ref='geography-v1', executor=None, now='2026-09-05T00:00:00Z')
except ValidationError as exc:
    if exc.code == 'E-GENERIC-WORKDIR-LOCKED':
        sys.exit(0)
    raise
sys.exit(7)
"""
    with generic_work_dir_lock(tmp_path):
        process = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path), entry],
            capture_output=True, text=True, timeout=30,
        )
    assert process.returncode == 0, process.stderr
    assert not (tmp_path / "ingestion").exists()


def test_lock_token_requires_live_same_thread_same_directory_owner(tmp_path: pathlib.Path) -> None:
    work_dir = tmp_path / "work"
    with generic_work_dir_lock(work_dir) as token:
        with generic_work_dir_lock(work_dir / ".." / "work", lock_token=token):
            pass
        with pytest.raises(ValidationError, match="E-GENERIC-WORKDIR-LOCKED"):
            with generic_work_dir_lock(work_dir):
                pass
        with pytest.raises(ValidationError, match="E-GENERIC-LOCK-TOKEN"):
            with generic_work_dir_lock(tmp_path / "other", lock_token=token):
                pass
        with ThreadPoolExecutor(max_workers=1) as pool:
            def use_token():
                with generic_work_dir_lock(work_dir, lock_token=token):
                    pass
            with pytest.raises(ValidationError, match="E-GENERIC-LOCK-TOKEN"):
                pool.submit(use_token).result()
        with generic_work_dir_lock(tmp_path / "other"):
            pass
    for invalid in (token, object(), True):
        with pytest.raises(ValidationError, match="E-GENERIC-LOCK-TOKEN"):
            with generic_work_dir_lock(work_dir, lock_token=invalid):
                pass
    with generic_work_dir_lock(work_dir):
        pass


def test_pending_releases_lock_and_outer_owner_can_publish_after_validation(tmp_path: pathlib.Path) -> None:
    spec = _write_novel(tmp_path / "novel")
    work_dir = tmp_path / "work"
    executor = make_generic_executor("agent-files", work_dir, "geography-v1", root=ROOT)
    with pytest.raises(GenericAgentResponsesPending) as waiting:
        run_generic_corpus_workflow(
            spec, work_dir, profile_ref="geography-v1", executor=executor, root=ROOT, now=NOW,
        )
    for item in waiting.value.pending:
        item.answer_path.write_text('{"records": []}\n', encoding="utf-8")
    # The outer integration retains ownership through workflow + validation + receipt.
    with generic_work_dir_lock(work_dir) as token:
        extraction = run_generic_extraction(
            spec, work_dir, profile_ref="geography-v1", executor=executor,
            root=ROOT, now=NOW, lock_token=token,
        )
        result = run_generic_reduction(extraction, root=ROOT, now=NOW, lock_token=token)
        cached = run_generic_corpus_workflow(
            spec, work_dir, profile_ref="geography-v1", executor=executor,
            root=ROOT, now=NOW, lock_token=token,
        )
        assert cached.extraction.reused_extraction
        assert _selected(spec, work_dir, result).corpus_snapshot == result.corpus_snapshot
        with pytest.raises(ValidationError, match="E-GENERIC-WORKDIR-LOCKED"):
            run_generic_reduction(extraction, root=ROOT, now=NOW)
        (tmp_path / "simulated-receipt.json").write_text(
            json.dumps({"corpus_snapshot_id": result.corpus_snapshot["corpus_snapshot_id"]}),
            encoding="utf-8",
        )
    with generic_work_dir_lock(work_dir):
        pass


def test_partial_releases_native_work_dir_lock(tmp_path: pathlib.Path) -> None:
    spec = _write_novel(tmp_path / "novel")
    work_dir = tmp_path / "work"
    with pytest.raises(GenericExtractionPartial):
        run_generic_corpus_workflow(
            spec, work_dir, profile_ref="geography-v1",
            executor=CountingApiExecutor(lambda _: {"unexpected": []}), root=ROOT, now=NOW,
        )
    with generic_work_dir_lock(work_dir):
        assert list(work_dir.glob("generic-extraction/profiles/*/extractions/*/checkpoint.json"))


def test_exact_corpus_offline_with_multiple_reductions_and_new_pending(tmp_path: pathlib.Path) -> None:
    spec = _write_novel(tmp_path / "novel")
    profiles_root = tmp_path / "profiles"
    profile_dir = _write_profile(
        profiles_root, "geography", profile_id="test.geography", kind="PLACE",
    )
    work_dir = tmp_path / "work"
    first = run_generic_corpus_workflow(
        spec, work_dir, profile_ref="geography", profiles_root=profiles_root,
        executor=CountingApiExecutor(_combined_answer), root=ROOT, now=NOW,
    )
    manifest_path = profile_dir / "profile.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["reduction"]["config"]["record_version"] = 2
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    executor = CountingApiExecutor(_combined_answer)
    second = run_generic_corpus_workflow(
        spec, work_dir, profile_ref="geography", profiles_root=profiles_root,
        executor=executor, root=ROOT, now=NOW,
    )
    assert executor.calls == 0
    assert second.extraction.build == first.extraction.build
    with pytest.raises(GenericAgentResponsesPending):
        run_generic_corpus_workflow(
            spec, work_dir, profile_ref="geography", profiles_root=profiles_root,
            executor=GenericAgentFileExecutor(tmp_path / "pending", model_label="new"),
            root=ROOT, now=NOW,
        )
    pathlib.Path(spec["source"]["path"]).rename(tmp_path / "archived-source")
    for result in (first, second):
        assert _selected(spec, work_dir, result, profiles_root=profiles_root).corpus_snapshot == result.corpus_snapshot
    assert len(validate_generic_work_dir(
        spec, work_dir, profile_ref="geography", profiles_root=profiles_root, root=ROOT, now=NOW,
    )) == 2
    # Exact selection does not accidentally validate all reductions in the directory.
    second.corpus_path.write_bytes(b"corrupt")
    assert _selected(spec, work_dir, first, profiles_root=profiles_root).corpus_snapshot == first.corpus_snapshot
    with pytest.raises(ValidationError):
        _selected(spec, work_dir, second, profiles_root=profiles_root)
    with pytest.raises(ValidationError):
        validate_generic_work_dir(
            spec, work_dir, profile_ref="geography", profiles_root=profiles_root, root=ROOT, now=NOW,
        )


@pytest.mark.parametrize("field", ["extraction_run_id", "reduction_run_id", "corpus_snapshot_id"])
def test_exact_selector_rejects_cross_binding_and_path_escape(tmp_path: pathlib.Path, field: str) -> None:
    spec = _write_novel(tmp_path / "novel")
    work_dir = tmp_path / "work"
    result = run_generic_corpus_workflow(
        spec, work_dir, profile_ref="geography-v1",
        executor=CountingApiExecutor(lambda _: {"records": []}), root=ROOT, now=NOW,
    )
    selections = {
        "extraction_run_id": result.extraction.run["extraction_run_id"],
        "reduction_run_id": result.reduction_run["reduction_run_id"],
        "corpus_snapshot_id": result.corpus_snapshot["corpus_snapshot_id"],
    }
    for value in (selections[field].split("-")[0] + "-" + "0" * 20, "../../elsewhere"):
        with pytest.raises(ValidationError, match="E-GENERIC-SELECTED"):
            validate_selected_generic_corpus(
                spec, work_dir, profile_ref="geography-v1", root=ROOT,
                **{**selections, field: value},
            )


def test_exact_selector_rejects_corrupt_selected_cas(tmp_path: pathlib.Path) -> None:
    spec = _write_novel(tmp_path / "novel")
    work_dir = tmp_path / "work"
    result = run_generic_corpus_workflow(
        spec, work_dir, profile_ref="geography-v1",
        executor=CountingApiExecutor(lambda _: {"records": []}), root=ROOT, now=NOW,
    )
    artifact_id = result.extraction.run["observations_artifact_id"]
    result.extraction.store._path(artifact_id).write_bytes(b"corrupt")
    with pytest.raises(ValidationError, match="E-ARTIFACT-CORRUPT"):
        _selected(spec, work_dir, result)


def test_factory_preserves_agent_root_and_model_configuration(tmp_path: pathlib.Path) -> None:
    executor = make_generic_executor(
        "agent-files", tmp_path, "geography-v1", agent_model_label="test-host", root=ROOT,
    )
    assert executor.root == _agent_root(tmp_path, "geography-v1", "test-host", root=ROOT)
    assert executor_descriptor(executor)["model"] == "test-host"
    for kind, model in (("api", None), ("agent-files", "api-model"), ("unknown", None)):
        with pytest.raises(PipelineError, match="E-MODEL-CONFIG"):
            make_generic_executor(kind, tmp_path, "geography-v1", model=model, root=ROOT)
