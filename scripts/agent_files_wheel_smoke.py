"""Wheel smoke: exercise the agent-files two-pass CLI flow end to end.

Run from an installed wheel's interpreter with OPENAI_API_KEY unset. Proves the
distributed package can: materialize native tasks (exit 3), accept host-agent
answers, complete the run (exit 0), and validate the produced catalog in a fresh
process — without any model API access.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PY = sys.executable
CANDIDATE_SUMMARY = "林舟触发天门机关"


def _cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    return subprocess.run(
        [PY, "-m", "xhnovel_pipeline.cli", *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def _answer(task_path: Path) -> None:
    task = json.loads(task_path.read_text(encoding="utf-8"))
    span = task["input"]["window"]["source_spans"][0]
    support = {
        "segment_id": span["segment_id"],
        "start": span["start"],
        "end": min(span["end"], span["start"] + 4),
    }
    known = lambda value: {"status": "KNOWN", "values": [value], "support_spans": [support]}
    unknown = {"status": "UNKNOWN", "values": [], "support_spans": []}
    candidate = {
        "summary": CANDIDATE_SUMMARY,
        "source_spans": [support],
        "actors": known("林舟"),
        "action": known("触发机关"),
        "target": known("天门机关"),
        "precondition": unknown,
        "state_transition": unknown,
        "external_response": unknown,
        "immediate_feedback": unknown,
        "new_affordances": unknown,
        "persistence": unknown,
        "mechanic_pressure_point": unknown,
    }
    answer_dir = task_path.parents[1] / "answers"
    answer_dir.mkdir(parents=True, exist_ok=True)
    (answer_dir / task_path.name).write_text(
        json.dumps({"candidates": [candidate]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        # Run entirely outside the source checkout so profile/contract resolution
        # is proven to come from the installed package data, not the cwd. repo_root()
        # must resolve to the installed data dir, never a parent of the source tree.
        os.chdir(tmp)
        probe = subprocess.run(
            [
                PY,
                "-c",
                "import xhnovel_pipeline as p; from xhnovel_pipeline.paths import repo_root; "
                "print(repo_root()); print(p.__file__)",
            ],
            check=True,
            capture_output=True,
            text=True,
            cwd=tmp,
        )
        root_line, pkg_line = probe.stdout.strip().splitlines()[:2]
        resolved = Path(root_line).resolve()
        pkg_file = Path(pkg_line).resolve()
        assert (resolved / "contracts").is_dir(), f"no contracts under {resolved}"
        assert (resolved / "profiles").is_dir(), f"no profiles under {resolved}"
        # Only a real wheel/site-packages install proves cwd-independent data
        # resolution. An editable install legitimately points repo_root() back at
        # the source checkout, so the strict guard applies only when the imported
        # package does NOT live under this repo's src/ tree.
        source_pkg = Path(__file__).resolve().parents[1] / "src" / "xhnovel_pipeline"
        source_pkg = source_pkg.resolve()
        is_editable = source_pkg == pkg_file.parent or source_pkg in pkg_file.parents
        if not is_editable:
            checkout = source_pkg.parents[1]
            assert resolved != checkout and checkout not in resolved.parents, (
                f"repo_root() resolved into the source checkout ({resolved}) from a "
                "non-editable install; wheel install must resolve package data "
                "independently of cwd"
            )
        (tmp / "book.txt").write_text(
            "第一章 天门\n林舟触发天门机关，山路随之开启。", encoding="utf-8"
        )
        spec = {
            "source": {"kind": "txt", "path": "book.txt", "title": "Smoke 仙途"},
            "rights": {
                "basis": "USER_AUTHORIZED_LOCAL_COPY",
                "may_store_full_text": True,
                "may_send_to_external_model": True,
                "may_export_excerpts": False,
            },
            "source_quality": {
                "edition_status": "USER_VERIFIED_COPY",
                "textual_completeness": "COMPLETE",
            },
            "request": {"discovery_brief": "寻找对象控制变化及其后续行动空间"},
            "limits": {"max_chapters": 10, "max_bytes": 2_000_000},
            "scene_scout": {"window_chars": 10_000, "overlap_chars": 1_800, "max_workers": 4},
            "strict_order": False,
        }
        spec_path = tmp / "spec.json"
        spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        work_dir = tmp / "work"
        args = [
            "research-novel",
            str(spec_path),
            "--executor",
            "agent-files",
            "--work-dir",
            str(work_dir),
        ]

        first = _cli(*args)
        assert first.returncode == 3, f"pass1 expected exit 3, got {first.returncode}: {first.stderr}"

        tasks_dir = work_dir / "scene-scout" / "agent-files" / "tasks"
        tasks = list(tasks_dir.glob("*.json"))
        assert tasks, "no tasks materialized"
        for task_path in tasks:
            _answer(task_path)

        second = _cli(*args)
        assert second.returncode == 0, f"pass2 expected exit 0, got {second.returncode}: {second.stderr}"
        assert "Token usage: unknown for" in second.stdout, second.stdout

        candidates_path = second.stdout.strip().splitlines()[-1]
        catalog_path = os.path.join(os.path.dirname(candidates_path), "catalog.json")
        store_dir = next(work_dir.rglob("objects"))
        validate = _cli("validate", "all", catalog_path, "--store", str(store_dir))
        assert validate.returncode == 0, f"validate failed: {validate.stderr}"

    print("OK: agent-files two-pass wheel smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
