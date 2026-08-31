from __future__ import annotations

import json
import subprocess
import sys
import zipfile

import pytest

from xhnovel_pipeline import cli
from xhnovel_pipeline.runtime import TEST_NOW as NOW
from xhnovel_pipeline.http_fetch import HttpFetcher
from xhnovel_pipeline.paths import repo_root


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "xhnovel_pipeline.cli", *args],
        cwd=repo_root(),
        check=False,
        capture_output=True,
        text=True,
    )


def _write_epub(path) -> None:
    container = """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles>
</container>"""
    opf = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>CLI 测试仙途</dc:title><dc:language>zh</dc:language>
  </metadata>
  <manifest>
    <item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="c2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="c1"/><itemref idref="c2"/></spine>
</package>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr(
            "OEBPS/c1.xhtml",
            "<html><body><h1>第一章 入山</h1><p>少年进入山门。</p></body></html>",
        )
        archive.writestr(
            "OEBPS/c2.xhtml",
            "<html><body><h1>第二章 拜师</h1><p>长老收他为徒。</p></body></html>",
        )


@pytest.mark.parametrize("kind", ["directory", "txt", "epub"])
def test_cli_ingests_each_local_source_kind_with_relative_path_and_validates(tmp_path, kind):
    spec_dir = tmp_path / "input"
    spec_dir.mkdir()
    if kind == "directory":
        source = spec_dir / "chapters"
        source.mkdir()
        (source / "001.txt").write_text("第一章 入山\n\n少年进入山门。", encoding="utf-8")
        (source / "002.txt").write_text("第二章 拜师\n\n长老收他为徒。", encoding="utf-8")
        relative_path = "chapters"
    elif kind == "txt":
        source = spec_dir / "book.txt"
        source.write_text(
            "第一章 入山\n\n少年进入山门。\n\n第二章 拜师\n\n长老收他为徒。",
            encoding="utf-8",
        )
        relative_path = "book.txt"
    else:
        source = spec_dir / "book.epub"
        _write_epub(source)
        relative_path = "book.epub"

    spec_path = spec_dir / "spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "source": {"kind": kind, "path": relative_path, "title": "CLI 测试仙途"},
                "limits": {"max_chapters": 10, "max_bytes": 1_000_000},
                "strict_order": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    work_dir = tmp_path / "work"

    completed = _run_cli("ingest-novel", str(spec_path), "--work-dir", str(work_dir))

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert completed.stdout.startswith("OK: novel ingestion NING-")
    output_dirs = list((work_dir / "ingestions").iterdir())
    assert len(output_dirs) == 1
    catalog_path = output_dirs[0] / "catalog.json"
    ingestion = json.loads((output_dirs[0] / "novel-ingestion.json").read_text(encoding="utf-8"))
    assert ingestion["status"] == "SUCCEEDED"

    validated = _run_cli(
        "validate",
        "novel",
        str(catalog_path),
        "--store",
        str(work_dir / "objects"),
    )
    assert validated.returncode == 0, validated.stderr
    assert validated.stdout == "OK: validate novel\n"


def test_cli_strict_order_failure_uses_negative_exit_without_success_banner(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("第一章 开始\n正文。\n\n第三章 跳跃\n正文二。", encoding="utf-8")
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "source": {"kind": "txt", "path": "book.txt", "title": "缺章小说"},
                "strict_order": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    completed = _run_cli(
        "ingest-novel",
        str(spec_path),
        "--work-dir",
        str(tmp_path / "work"),
    )

    assert completed.returncode == 2
    assert "OK:" not in completed.stdout
    assert completed.stderr.startswith("FAIL: novel ingestion NING-")
    assert "status=FAILED" in completed.stderr


def test_cli_partial_ingestion_returns_zero_with_warning(tmp_path):
    chapters = tmp_path / "chapters"
    chapters.mkdir()
    (chapters / "001.txt").write_text("相同正文。", encoding="utf-8")
    (chapters / "002.txt").write_text("相同正文。", encoding="utf-8")
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "source": {"kind": "directory", "path": "chapters"},
                "strict_order": False,
            }
        ),
        encoding="utf-8",
    )

    completed = _run_cli(
        "ingest-novel",
        str(spec_path),
        "--work-dir",
        str(tmp_path / "work"),
    )

    assert completed.returncode == 0
    assert completed.stdout.strip().endswith("novel-ingestion.json")
    assert "OK:" not in completed.stdout
    assert completed.stderr.startswith("WARNING: novel ingestion NING-")
    assert "status=PARTIAL" in completed.stderr


def test_cli_can_resume_a_completed_ingestion_without_immutable_id_collision(
    tmp_path, monkeypatch, capsys
):
    source = tmp_path / "book.txt"
    source.write_text("第一章 开始\n正文。", encoding="utf-8")
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        json.dumps({"source": {"kind": "txt", "path": "book.txt"}, "strict_order": True}),
        encoding="utf-8",
    )
    work_dir = tmp_path / "work"
    monkeypatch.setattr(cli, "utc_now", lambda: NOW)

    assert cli.main(["ingest-novel", str(spec_path), "--work-dir", str(work_dir)]) == 0
    first = capsys.readouterr()
    assert first.err == ""
    assert cli.main(["ingest-novel", str(spec_path), "--work-dir", str(work_dir)]) == 0
    second = capsys.readouterr()

    assert second.err == ""
    assert "E-IMMUTABLE-OUTPUT" not in second.out
    ingestions = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (work_dir / "ingestions").glob("*/novel-ingestion.json")
    ]
    assert len(ingestions) == 1
    assert ingestions[0]["resumed_from_checkpoint"] is False


def test_cli_site_configuration_fails_cleanly_before_network_for_invalid_pattern(tmp_path):
    spec_path = tmp_path / "site.json"
    spec_path.write_text(
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

    completed = _run_cli(
        "ingest-novel",
        str(spec_path),
        "--work-dir",
        str(tmp_path / "work"),
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert "FAIL: E-NOVEL-SPEC: invalid chapter_url_pattern" in completed.stderr
    assert "Traceback" not in completed.stderr


@pytest.mark.parametrize(
    "source,limits,expected_code",
    [
        ({"kind": "txt"}, {}, "E-NOVEL-SPEC"),
        (
            {"kind": "site", "chapter_url_pattern": "/chapter/[0-9]+$"},
            {},
            "E-NOVEL-SPEC",
        ),
        (
            {"kind": "txt", "path": "missing.txt"},
            {"max_chapters": True},
            "E-NOVEL-LIMIT",
        ),
        (
            {"kind": "txt", "path": "missing.txt"},
            {"max_bytes": True},
            "E-NOVEL-LIMIT",
        ),
        (
            {"kind": "txt", "path": "missing.txt", "max_chapters": True},
            {},
            "E-NOVEL-LIMIT",
        ),
        (
            {"kind": "directory", "path": "missing", "recursive": "false"},
            {},
            "E-NOVEL-SPEC",
        ),
        (
            {
                "kind": "site",
                "index_url": "https://novel.example/index",
                "chapter_url_pattern": "/chapter/[0-9]+$",
                "allow_external_chapters": "false",
            },
            {},
            "E-NOVEL-SPEC",
        ),
        (
            {
                "kind": "site",
                "index_url": "https://novel.example:bad/index",
                "chapter_url_pattern": "/chapter/[0-9]+$",
            },
            {},
            "E-NOVEL-SPEC",
        ),
    ],
)
def test_cli_rejects_malformed_novel_input_without_traceback(
    tmp_path, source, limits, expected_code
):
    spec_path = tmp_path / "invalid.json"
    spec_path.write_text(
        json.dumps({"source": source, "limits": limits, "strict_order": False}),
        encoding="utf-8",
    )

    completed = _run_cli(
        "ingest-novel",
        str(spec_path),
        "--work-dir",
        str(tmp_path / "work"),
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert f"FAIL: {expected_code}:" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_cli_rejects_string_false_strict_order_without_traceback(tmp_path):
    spec_path = tmp_path / "invalid.json"
    spec_path.write_text(
        json.dumps(
            {
                "source": {"kind": "txt", "path": "missing.txt"},
                "strict_order": "false",
            }
        ),
        encoding="utf-8",
    )

    completed = _run_cli(
        "ingest-novel",
        str(spec_path),
        "--work-dir",
        str(tmp_path / "work"),
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert "FAIL: E-NOVEL-SPEC: strict_order must be a boolean" in completed.stderr
    assert "Traceback" not in completed.stderr


@pytest.mark.parametrize("source_catalog", [{"not": "an array"}, 42])
def test_cli_rejects_non_array_source_catalog_before_ranking_without_traceback(
    tmp_path, source_catalog
):
    spec_path = tmp_path / "invalid-famous.json"
    spec_path.write_text(
        json.dumps({"genre": "玄幻", "source_catalog": source_catalog}),
        encoding="utf-8",
    )

    completed = _run_cli(
        "research-famous-novel",
        str(spec_path),
        "--scout-model",
        "scene-scout-model",
        "--work-dir",
        str(tmp_path / "work"),
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert "FAIL: E-NOVEL-SOURCE-CATALOG: source_catalog must be an array" in completed.stderr
    assert "Traceback" not in completed.stderr


@pytest.mark.parametrize(
    "ranking",
    [
        {"queries": 42},
        {"pages_per_query": "oops"},
        {"pages_per_query": True},
        {"limit": "oops"},
        {"limit": True},
        False,
        [],
    ],
)
def test_cli_rejects_malformed_famous_ranking_without_traceback(
    tmp_path, monkeypatch, ranking
):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    spec_path = tmp_path / "invalid-famous-ranking.json"
    spec_path.write_text(
        json.dumps(
            {
                "genre": "玄幻",
                "ranking": ranking,
                "source_catalog": [
                    {
                        "candidate_titles": ["测试作品"],
                        "source": {"kind": "txt", "path": "missing.txt"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    completed = _run_cli(
        "research-famous-novel",
        str(spec_path),
        "--scout-model",
        "scene-scout-model",
        "--work-dir",
        str(tmp_path / "work"),
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert "FAIL: E-RANKING-INPUT:" in completed.stderr
    assert "Traceback" not in completed.stderr


@pytest.mark.parametrize("defaults", [False, [], 0, ""])
def test_cli_rejects_falsey_non_object_famous_defaults_before_ranking(
    tmp_path, monkeypatch, defaults
):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    spec_path = tmp_path / "invalid-famous-defaults.json"
    spec_path.write_text(
        json.dumps(
            {
                "genre": "玄幻",
                "defaults": defaults,
                "source_catalog": [
                    {
                        "candidate_titles": ["测试作品"],
                        "source": {"kind": "txt", "path": "missing.txt"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    completed = _run_cli(
        "research-famous-novel",
        str(spec_path),
        "--scout-model",
        "scene-scout-model",
        "--work-dir",
        str(tmp_path / "work"),
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert "FAIL: E-NOVEL-SOURCE-CATALOG:" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_cli_ingests_static_site_configuration_with_bounded_offline_transport(
    tmp_path, monkeypatch, capsys
):
    pages = {
        "https://novel.example/index": (
            '<a href="/chapter/1">第一章 入山</a><a href="/chapter/2">第二章 拜师</a>'.encode(),
            "text/html",
        ),
        "https://novel.example/chapter/1": (
            "<html><body><h1>第一章 入山</h1><p>少年进入山门。</p></body></html>".encode(),
            "text/html",
        ),
        "https://novel.example/chapter/2": (
            "<html><body><h1>第二章 拜师</h1><p>长老收他为徒。</p></body></html>".encode(),
            "text/html",
        ),
    }

    def fetch(_self, url):
        data, media_type = pages[url]
        return data, media_type, 200, url

    monkeypatch.setattr(HttpFetcher, "fetch", fetch)
    monkeypatch.setattr(cli, "utc_now", lambda: NOW)
    spec_path = tmp_path / "site.json"
    spec_path.write_text(
        json.dumps(
            {
                "source": {
                    "kind": "site",
                    "index_url": "https://novel.example/index",
                    "chapter_url_pattern": "/chapter/[0-9]+$",
                    "max_index_pages": 1,
                    "max_chapters": 2,
                },
                "limits": {"max_chapters": 2, "max_bytes": 100_000},
                "strict_order": True,
            }
        ),
        encoding="utf-8",
    )
    work_dir = tmp_path / "work"

    assert cli.main(["ingest-novel", str(spec_path), "--work-dir", str(work_dir)]) == 0
    captured = capsys.readouterr()

    assert captured.err == ""
    output_path = next((work_dir / "ingestions").glob("*/novel-ingestion.json"))
    ingestion = json.loads(output_path.read_text(encoding="utf-8"))
    catalog = json.loads((output_path.parent / "catalog.json").read_text(encoding="utf-8"))
    assert ingestion["status"] == "SUCCEEDED"
    assert len(ingestion["ready_chapter_ids"]) == 2
    assert {record["http_status"] for record in catalog["Retrieval"]} == {200}


def test_cli_research_requires_scene_scout_model_before_api_or_output(tmp_path):
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps({"source": {"kind": "txt", "path": "missing.txt"}}))
    work_dir = tmp_path / "work"

    completed = _run_cli(
        "research-novel",
        str(spec_path),
        "--work-dir",
        str(work_dir),
    )

    assert completed.returncode == 1
    assert "E-MODEL-CONFIG" in completed.stderr
    assert "--scout-model is required" in completed.stderr
    assert not work_dir.exists()
