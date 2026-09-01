from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from xhnovel_pipeline.agent_files import AGENT_FILES_PROTOCOL, locate_quote_in_task
from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.paths import repo_root

from test_agent_files_integration import _candidate, _spec

AGENT_FILES_SUBDIR = ("scene-scout", "agent-files")


def _run_cli(*args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    run_env = dict(os.environ)
    run_env.pop("OPENAI_API_KEY", None)
    if env:
        run_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "xhnovel_pipeline.cli", *args],
        cwd=repo_root(),
        check=False,
        capture_output=True,
        text=True,
        env=run_env,
    )


def _write_spec(tmp_path, text: str = "第一章 天门\n林舟触发天门机关，山路随之开启。"):
    source = tmp_path / "book.txt"
    source.write_text(text, encoding="utf-8")
    spec = _spec(source)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return spec_path


def _tasks_dir(work_dir):
    return work_dir.joinpath(*AGENT_FILES_SUBDIR, "tasks")


def _answers_dir(work_dir):
    return work_dir.joinpath(*AGENT_FILES_SUBDIR, "answers")


def _answer_valid(task_path, mutate=None):
    task = json.loads(task_path.read_text(encoding="utf-8"))
    span = task["input"]["window"]["source_spans"][0]
    candidate = _candidate(span)
    if mutate is not None:
        mutate(candidate, span)
    answer_dir = task_path.parents[1] / "answers"
    answer_dir.mkdir(parents=True, exist_ok=True)
    (answer_dir / task_path.name).write_text(
        json.dumps({"candidates": [candidate]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Executor selection contract
# ---------------------------------------------------------------------------
def test_api_executor_is_default_and_requires_scout_model(tmp_path):
    spec_path = _write_spec(tmp_path)
    work_dir = tmp_path / "work"
    completed = _run_cli(
        "research-novel", str(spec_path), "--work-dir", str(work_dir)
    )
    assert completed.returncode == 1, completed.stderr
    assert "E-MODEL-CONFIG" in completed.stderr
    assert "--scout-model is required" in completed.stderr
    assert not work_dir.exists()


def test_agent_files_executor_rejects_scout_model(tmp_path):
    spec_path = _write_spec(tmp_path)
    work_dir = tmp_path / "work"
    completed = _run_cli(
        "research-novel",
        str(spec_path),
        "--executor",
        "agent-files",
        "--scout-model",
        "gpt-x",
        "--work-dir",
        str(work_dir),
    )
    assert completed.returncode == 1, completed.stderr
    assert "E-MODEL-CONFIG" in completed.stderr
    assert "not allowed for --executor agent-files" in completed.stderr


# ---------------------------------------------------------------------------
# WAITING_FOR_AGENT exit-3 contract
# ---------------------------------------------------------------------------
def test_agent_files_pending_returns_exit_3_with_manifest(tmp_path):
    spec_path = _write_spec(tmp_path)
    work_dir = tmp_path / "work"
    completed = _run_cli(
        "research-novel",
        str(spec_path),
        "--executor",
        "agent-files",
        "--work-dir",
        str(work_dir),
    )
    assert completed.returncode == 3, completed.stderr
    assert completed.stderr.startswith("WAITING_FOR_AGENT: ")
    assert "FAIL:" not in completed.stderr
    manifest = json.loads(completed.stdout)
    assert manifest["status"] == "WAITING_FOR_AGENT"
    assert manifest["exit_code"] == 3
    assert manifest["executor"] == "agent-files"
    assert manifest["pending_count"] >= 1
    # paths are relative to work_dir and carry no source text
    assert manifest["tasks_dir"] == "scene-scout/agent-files/tasks"
    window_ids = [item["window_id"] for item in manifest["pending"]]
    assert window_ids == sorted(window_ids)  # deterministic ordering
    for item in manifest["pending"]:
        assert item["task"].startswith("scene-scout/agent-files/tasks/")
        assert "untrusted_text" not in json.dumps(item)
    # regenerable operational view written to disk
    pending_json = work_dir.joinpath(*AGENT_FILES_SUBDIR, "pending.json")
    assert json.loads(pending_json.read_text(encoding="utf-8"))["status"] == "WAITING_FOR_AGENT"
    # tasks materialized, but no model attempt persisted yet
    assert list(_tasks_dir(work_dir).glob("*.json"))


# ---------------------------------------------------------------------------
# Two-pass end-to-end (no API key in the environment)
# ---------------------------------------------------------------------------
def test_agent_files_two_pass_e2e_completes_and_validates(tmp_path):
    spec_path = _write_spec(tmp_path)
    work_dir = tmp_path / "work"
    args = [
        "research-novel",
        str(spec_path),
        "--executor",
        "agent-files",
        "--work-dir",
        str(work_dir),
    ]

    first = _run_cli(*args)
    assert first.returncode == 3, first.stderr

    for task_path in _tasks_dir(work_dir).glob("*.json"):
        _answer_valid(task_path)

    second = _run_cli(*args)
    assert second.returncode == 0, second.stderr
    assert second.stdout.startswith("OK: discovered ")
    assert "Token usage: unknown for" in second.stdout
    assert "Total tokens: 0" not in second.stdout
    assert json.loads(
        work_dir.joinpath(*AGENT_FILES_SUBDIR, "pending.json").read_text(encoding="utf-8")
    )["status"] == "COMPLETE"

    # scene-candidates.json path is the last stdout line
    candidates_path = second.stdout.strip().splitlines()[-1]
    run_dir = os.path.dirname(candidates_path)
    catalog_path = os.path.join(run_dir, "catalog.json")
    store_dir = next(work_dir.rglob("objects"))
    validate = _run_cli("validate", "all", catalog_path, "--store", str(store_dir))
    assert validate.returncode == 0, validate.stderr


# ---------------------------------------------------------------------------
# Negative: task tamper is a hard abort, not a demoted partial
# ---------------------------------------------------------------------------
def test_agent_files_task_tamper_surfaces_native_code(tmp_path):
    spec_path = _write_spec(tmp_path)
    work_dir = tmp_path / "work"
    args = [
        "research-novel",
        str(spec_path),
        "--executor",
        "agent-files",
        "--work-dir",
        str(work_dir),
    ]
    assert _run_cli(*args).returncode == 3

    task_path = next(_tasks_dir(work_dir).glob("*.json"))
    _answer_valid(task_path)
    original = task_path.read_bytes()
    task_path.write_bytes(original.replace(b"instructions", b"XnstructionsX", 1))

    tampered = _run_cli(*args)
    assert tampered.returncode == 1, tampered.stderr
    assert "E-AGENT-TASK-TAMPER" in tampered.stderr
    assert "E-SCENE-PARTIAL" not in tampered.stderr
    assert "WAITING_FOR_AGENT" not in tampered.stderr


# ---------------------------------------------------------------------------
# agent-locate CLI + helper
# ---------------------------------------------------------------------------
def _prepare_tasks(tmp_path):
    spec_path = _write_spec(tmp_path)
    work_dir = tmp_path / "work"
    assert _run_cli(
        "research-novel",
        str(spec_path),
        "--executor",
        "agent-files",
        "--work-dir",
        str(work_dir),
    ).returncode == 3
    task_path = next(_tasks_dir(work_dir).glob("*.json"))
    window_id = json.loads(task_path.read_text(encoding="utf-8"))["window_id"]
    return work_dir, window_id


def test_agent_locate_exact_match_and_no_match(tmp_path):
    work_dir, window_id = _prepare_tasks(tmp_path)
    hit = _run_cli(
        "agent-locate", "--work-dir", str(work_dir), "--window", window_id, "--quote", "林舟"
    )
    assert hit.returncode == 0, hit.stderr
    payload = json.loads(hit.stdout)
    assert payload["match_count"] == 1
    assert payload["matches"][0]["start"] == 0
    assert payload["matches"][0]["end"] == 2

    miss = _run_cli(
        "agent-locate", "--work-dir", str(work_dir), "--window", window_id, "--quote", "缺失XYZ"
    )
    assert miss.returncode == 0, miss.stderr
    assert json.loads(miss.stdout) == {
        "window_id": window_id,
        "quote": "缺失XYZ",
        "match_count": 0,
        "matches": [],
    }


def test_agent_locate_unknown_window_and_empty_quote(tmp_path):
    work_dir, window_id = _prepare_tasks(tmp_path)
    unknown = _run_cli(
        "agent-locate", "--work-dir", str(work_dir), "--window", "SWIN-NOPE", "--quote", "x"
    )
    assert unknown.returncode == 1
    assert "E-AGENT-LOCATE" in unknown.stderr

    empty = _run_cli(
        "agent-locate", "--work-dir", str(work_dir), "--window", window_id, "--quote", ""
    )
    assert empty.returncode == 1
    assert "E-AGENT-LOCATE" in empty.stderr


def test_locate_quote_helper_offsets_and_boundaries():
    task = {
        "protocol": AGENT_FILES_PROTOCOL,
        "window_id": "SWIN-x",
        "input": {
            "window": {
                "source_spans": [
                    {"segment_id": "S", "start": 100, "untrusted_text": "林舟触发天门机关林舟"},
                    {"segment_id": "S", "start": 200, "untrusted_text": "B后半段"},
                ]
            }
        },
    }
    # segment-absolute offsets, all occurrences
    assert locate_quote_in_task(task, "林舟") == [
        {"segment_id": "S", "start": 100, "end": 102},
        {"segment_id": "S", "start": 108, "end": 110},
    ]
    # a quote straddling the span boundary is never stitched
    assert locate_quote_in_task(task, "机关B") == []
    with pytest.raises(ValidationError, match="E-AGENT-LOCATE"):
        locate_quote_in_task(task, "")
    with pytest.raises(ValidationError, match="E-AGENT-LOCATE"):
        locate_quote_in_task({"protocol": "other"}, "x")


# ---------------------------------------------------------------------------
# Negative: an out-of-window citation is a recoverable partial (exit 1), and a
# corrected answer completes on rerun — distinct from the tamper hard-abort.
# ---------------------------------------------------------------------------
def test_agent_files_invalid_citation_is_partial_then_recovers(tmp_path):
    spec_path = _write_spec(tmp_path)
    work_dir = tmp_path / "work"
    args = [
        "research-novel",
        str(spec_path),
        "--executor",
        "agent-files",
        "--work-dir",
        str(work_dir),
    ]
    assert _run_cli(*args).returncode == 3

    task_path = next(_tasks_dir(work_dir).glob("*.json"))

    def push_out_of_window(candidate, span):
        candidate["action"]["support_spans"] = [
            {"segment_id": span["segment_id"], "start": span["end"], "end": span["end"] + 1}
        ]

    _answer_valid(task_path, mutate=push_out_of_window)
    bad = _run_cli(*args)
    assert bad.returncode == 1, bad.stderr
    assert "E-SCENE-PARTIAL" in bad.stderr
    assert "E-AGENT-TASK-TAMPER" not in bad.stderr

    _answer_valid(task_path)  # overwrite with a valid answer
    good = _run_cli(*args)
    assert good.returncode == 0, good.stderr
    assert good.stdout.startswith("OK: discovered ")

