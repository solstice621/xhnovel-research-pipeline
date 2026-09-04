from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile


def _run(args: list[str], *, expected: int) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "xhnovel_pipeline.generic_cli", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != expected:
        raise AssertionError(
            f"command returned {result.returncode}, expected {expected}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"stdout was not JSON:\n{result.stdout}") from exc


def _complete_pending(manifest: dict, *, completion: bool = False) -> None:
    assert manifest["status"] == "WAITING_FOR_AGENT"
    assert manifest["pending_count"] >= 1
    body = (
        '{"records": [], "completion": {"status": "COMPLETE"}}\n'
        if completion
        else '{"records": []}\n'
    )
    for item in manifest["pending"]:
        path = pathlib.Path(item["answer"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="xhnovel-generic-wheel-") as raw:
        root = pathlib.Path(raw)
        chapters = root / "chapters"
        chapters.mkdir()
        chapters.joinpath("0001.txt").write_text(
            "第一章 起点\n乌坦城位于加玛帝国。\n", encoding="utf-8"
        )
        chapters.joinpath("0002.txt").write_text(
            "第二章 沙海\n蛇人族生活在沙漠。\n", encoding="utf-8"
        )
        spec = {
            "source": {
                "kind": "directory",
                "path": str(chapters.resolve()),
                "title": "wheel smoke novel",
                "author": "fixture",
                "language": "zh",
            },
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
            "limits": {"max_chapters": 10, "max_bytes": 100000},
            "strict_order": False,
        }
        spec_path = root / "spec.json"
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        work_dir = root / "work"

        listed = _run(["list-profiles", "--json"], expected=0)
        refs = {item["ref"] for item in listed}
        assert {"geography-v1", "geography-unique-v1", "race-mention-v1"} <= refs

        snapshot_ids: list[str] = []
        for profile in ("geography-v1", "geography-unique-v1", "race-mention-v1"):
            pending = _run(
                [
                    "run",
                    str(spec_path),
                    "--profile",
                    profile,
                    "--executor",
                    "agent-files",
                    "--work-dir",
                    str(work_dir),
                ],
                expected=3,
            )
            _complete_pending(pending, completion=profile == "geography-unique-v1")
            completed = _run(
                [
                    "run",
                    str(spec_path),
                    "--profile",
                    profile,
                    "--executor",
                    "agent-files",
                    "--work-dir",
                    str(work_dir),
                ],
                expected=0,
            )
            assert completed["status"] == "SUCCEEDED"
            assert completed["semantic_assurance"] == "UNQUALIFIED"
            snapshot_ids.append(completed["text_snapshot_id"])
            validated = _run(
                [
                    "validate",
                    str(spec_path),
                    "--profile",
                    profile,
                    "--work-dir",
                    str(work_dir),
                ],
                expected=0,
            )
            assert validated["status"] == "VALID"
        assert len(set(snapshot_ids)) == 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
