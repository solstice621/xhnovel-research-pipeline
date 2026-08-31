from __future__ import annotations

import json
import subprocess
import sys

from xhnovel_pipeline.paths import repo_root


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "xhnovel_pipeline.cli", *args],
        cwd=repo_root(),
        check=False,
        capture_output=True,
        text=True,
    )


def test_cli_ingests_and_validates_text_source(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("第一章 入山\n少年进入山门。\n\n第二章 拜师\n长老收他为徒。", encoding="utf-8")
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "source": {"kind": "txt", "path": "book.txt", "title": "CLI 测试仙途"},
                "limits": {"max_chapters": 10, "max_bytes": 1_000_000},
                "strict_order": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    work = tmp_path / "work"

    completed = _run_cli("ingest-novel", str(spec), "--work-dir", str(work))

    assert completed.returncode == 0, completed.stderr
    output = next((work / "ingestions").glob("*/novel-ingestion.json"))
    validated = _run_cli(
        "validate",
        "novel",
        str(output.parent / "catalog.json"),
        "--store",
        str(work / "objects"),
    )
    assert validated.returncode == 0, validated.stderr
    assert validated.stdout == "OK: validate novel\n"


def test_cli_reports_strict_order_failure_without_success_banner(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("第一章 开始\n正文。\n\n第三章 跳跃\n正文二。", encoding="utf-8")
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps({"source": {"kind": "txt", "path": "book.txt"}, "strict_order": True}),
        encoding="utf-8",
    )

    completed = _run_cli("ingest-novel", str(spec), "--work-dir", str(tmp_path / "work"))

    assert completed.returncode == 2
    assert "OK:" not in completed.stdout
    assert "status=FAILED" in completed.stderr


def test_cli_rejects_invalid_site_pattern_without_traceback(tmp_path):
    spec = tmp_path / "site.json"
    spec.write_text(
        json.dumps(
            {
                "source": {
                    "kind": "site",
                    "index_url": "https://novel.example/index",
                    "chapter_url_pattern": "[",
                }
            }
        ),
        encoding="utf-8",
    )

    completed = _run_cli("ingest-novel", str(spec), "--work-dir", str(tmp_path / "work"))

    assert completed.returncode == 1
    assert "E-NOVEL-SPEC" in completed.stderr
    assert "Traceback" not in completed.stderr
