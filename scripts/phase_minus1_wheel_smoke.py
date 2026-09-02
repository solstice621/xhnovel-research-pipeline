"""Installed-wheel smoke for Phase -1 planning through Handoff closure.

The script is run by CI with an interpreter whose wheel is installed outside the
checkout.  It performs the three staged planning commands, prepares a Phase 0
Handoff from the compiled Brief, and fixed-build replays the planning lineage.
No semantic model or network access is used.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


PYTHON = sys.executable


def _write_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"expected JSON object: {path}")
    return value


def _cli(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [PYTHON, "-m", "xhnovel_pipeline.cli", *args],
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _require_ok(result: subprocess.CompletedProcess[str], label: str) -> None:
    if result.returncode != 0:
        raise AssertionError(
            f"{label} returned {result.returncode}: {result.stderr}\n{result.stdout}"
        )


def _preparation(brief: dict[str, Any]) -> dict[str, Any]:
    return {
        "brief": brief,
        "leads": [
            {
                "work_claim": {
                    "title": "Wheel Smoke 仙途",
                    "author": "测试作者",
                    "language": "zh",
                    "aliases": [],
                },
                "scene_hint": {
                    "summary": "可能存在物品控制变化。",
                    "why_relevant": "用于测试规划到证据编译器的边界。",
                    "interaction_tags": ["object_control"],
                    "location_hints": [],
                },
                "lead_sources": [
                    {
                        "source_kind": "REFERENCE",
                        "locator": "https://example.invalid/phase-minus1-smoke",
                        "supports": ["WORK_IDENTITY", "SCENE_EXISTENCE_HINT"],
                    }
                ],
                "frozen_at": "2026-09-02T00:00:00Z",
            }
        ],
        "source_declaration": {
            "work": {
                "canonical_title": "Wheel Smoke 仙途",
                "author": "测试作者",
                "language": "zh",
                "aliases": [],
                "external_ids": [],
            },
            "source": {"kind": "txt", "path": "book.txt"},
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
            "edition_label": "Phase -1 wheel smoke copy",
            "declared_at": "2026-09-02T00:00:00Z",
        },
        "requested_at": "2026-09-02T00:00:00Z",
    }


def run(fixture_root: Path, *, require_wheel: bool) -> None:
    fixture_root = fixture_root.resolve()
    required = {
        "intake-draft.json",
        "neutral-frame-draft.json",
        "attestation.json",
        "strategy-draft.json",
    }
    missing = [name for name in sorted(required) if not (fixture_root / name).is_file()]
    if missing:
        raise AssertionError(f"Phase -1 fixture is incomplete: {missing}")

    probe = subprocess.run(
        [
            PYTHON,
            "-c",
            "import json, xhnovel_pipeline; from xhnovel_pipeline.paths import repo_root; "
            "print(json.dumps({'package': xhnovel_pipeline.__file__, 'root': str(repo_root())}))",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    installation = json.loads(probe.stdout)
    data_root = Path(installation["root"]).resolve()
    package_file = Path(installation["package"]).resolve()
    assert (data_root / "contracts" / "research-intake.schema.json").is_file()
    assert (data_root / "contracts" / "planning-compilation-receipt.schema.json").is_file()
    if require_wheel:
        checkout = Path(__file__).resolve().parents[1]
        if checkout == package_file or checkout in package_file.parents:
            raise AssertionError(f"package was imported from checkout: {package_file}")
        if checkout == data_root or checkout in data_root.parents:
            raise AssertionError(f"package data resolved into checkout: {data_root}")

    with tempfile.TemporaryDirectory() as raw:
        temp = Path(raw)
        planning = temp / "planning"
        phase0 = temp / "phase0"
        (temp / "book.txt").write_text(
            "第一章\n林舟取得法器，但禁制仍阻止他使用。",
            encoding="utf-8",
        )

        sealed_intake = _cli(
            temp,
            "seal-intake",
            str(fixture_root / "intake-draft.json"),
            "--work-dir",
            str(planning),
        )
        _require_ok(sealed_intake, "seal-intake")
        neutral = _read_json(planning / "neutral-planning-input.json")
        assert set(neutral) == {
            "schema_version",
            "neutral_input_id",
            "neutral_goal_text",
            "explicit_scope",
        }

        sealed_frame = _cli(
            temp,
            "seal-neutral-frame",
            str(fixture_root / "neutral-frame-draft.json"),
            "--attestation",
            str(fixture_root / "attestation.json"),
            "--work-dir",
            str(planning),
        )
        _require_ok(sealed_frame, "seal-neutral-frame")
        manifest = _read_json(planning / "planning-manifest.json")
        request = {
            "schema_version": "0.2-draft",
            "intake_artifact_id": manifest["intake_artifact_id"],
            "neutral_frame_artifact_id": manifest["neutral_frame_artifact_id"],
            "neutral_execution_artifact_id": manifest[
                "neutral_execution_artifact_id"
            ],
            "strategy": _read_json(fixture_root / "strategy-draft.json"),
            "compiled_at": "2026-09-02T00:00:00Z",
        }
        request_path = _write_json(planning / "compile-request.json", request)
        compiled = _cli(
            temp,
            "compile-exploration-plan",
            str(request_path),
            "--work-dir",
            str(planning),
        )
        _require_ok(compiled, "compile-exploration-plan")
        receipt = _read_json(planning / "planning-compilation-receipt.json")
        assert receipt["compiler_build_id"].startswith("PCB-")
        if require_wheel:
            assert receipt["repository_commit"] == "unknown-dev"

        preparation_path = _write_json(
            temp / "preparation.json",
            _preparation(_read_json(planning / "exploration-brief.json")),
        )
        prepared = _cli(
            temp,
            "prepare-handoff",
            str(preparation_path),
            "--work-dir",
            str(phase0),
        )
        _require_ok(prepared, "prepare-handoff")
        handoff_path = Path(prepared.stdout.strip().splitlines()[-1])
        if not handoff_path.is_file():
            raise AssertionError(f"prepare-handoff did not emit a Handoff: {handoff_path}")

        validated = _cli(
            temp,
            "validate-planning-handoff",
            str(planning / "planning-compilation-receipt.json"),
            str(handoff_path),
            "--planning-root",
            str(planning),
            "--phase0-root",
            str(phase0),
        )
        _require_ok(validated, "validate-planning-handoff")
        assert "OK: validate planning handoff PCR-" in validated.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--require-wheel", action="store_true")
    args = parser.parse_args()
    run(args.fixture_root, require_wheel=args.require_wheel)
    print("OK: Phase -1 installed-wheel planning smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
