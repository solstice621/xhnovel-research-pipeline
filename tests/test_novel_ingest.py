from __future__ import annotations

import json
import builtins
import zipfile

import pytest

from xhnovel_pipeline import novel_ingest
from xhnovel_pipeline.runtime import TEST_NOW as NOW
from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.hashing import object_hash
from xhnovel_pipeline.novel_adapters import DirectoryNovelAdapter, EpubNovelAdapter, TextNovelAdapter
from xhnovel_pipeline.novel_ingest import run_novel_ingestion, validate_novel_ingestion
from xhnovel_pipeline.paths import repo_root


def _site_spec():
    return {
        "source": {
            "kind": "site",
            "index_url": "https://novel.example/index",
            "chapter_url_pattern": r"/chapter/\d+$",
            "title": "测试小说",
            "author": "作者甲",
        },
        "evidence": {"tier": "A", "access_legitimacy": "AUTHORIZED"},
        "limits": {"max_chapters": 10, "max_bytes": 10000},
        "strict_order": True,
    }


def _write_epub(path, *, second_body="长老收他为徒。"):
    container = """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles>
</container>"""
    opf = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>测试仙途</dc:title><dc:creator>测试作者</dc:creator><dc:language>zh</dc:language>
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
        archive.writestr("OEBPS/c1.xhtml", "<html><body><h1>第一章 入山</h1><p>少年进入山门。</p></body></html>")
        archive.writestr(
            "OEBPS/c2.xhtml",
            f"<html><body><h1>第二章 拜师</h1><p>{second_body}</p></body></html>",
        )


def _interrupt_local_ingestion(monkeypatch, adapter_type, spec, work_dir):
    original_fetch = adapter_type.fetch_chapter
    calls = 0

    def interrupt_second(self, chapter):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValidationError("E-UNREACHABLE", "interrupted")
        return original_fetch(self, chapter)

    monkeypatch.setattr(adapter_type, "fetch_chapter", interrupt_second)
    with pytest.raises(ValidationError, match="interrupted"):
        run_novel_ingestion(spec, work_dir, repo_root=repo_root(), now=NOW)
    monkeypatch.setattr(adapter_type, "fetch_chapter", original_fetch)


def test_ingest_text_materializes_auditable_chapters_and_detects_duplicate(tmp_path):
    source = tmp_path / "chapters"
    source.mkdir()
    (source / "1.txt").write_text("第一章 入山\n\n少年进入山门。", encoding="utf-8")
    (source / "2.txt").write_text("第二章 拜师\n\n长老收徒。", encoding="utf-8")
    (source / "3.txt").write_text("第二章 拜师\n\n长老收徒。", encoding="utf-8")
    spec = {
        "source": {"kind": "directory", "path": str(source), "title": "测试小说"},
        "evidence": {"tier": "A", "access_legitimacy": "AUTHORIZED"},
        "strict_order": False,
    }

    result = run_novel_ingestion(spec, tmp_path / "run", repo_root=repo_root(), now=NOW)

    assert [chapter["status"] for chapter in result["chapters"]] == ["READY", "READY", "DUPLICATE"]
    assert result["chapters"][2]["duplicate_of"] == result["chapters"][1]["chapter_id"]
    assert result["ingestion"]["status"] == "PARTIAL"
    assert result["ingestion"]["order_validation"]["duplicate_chapter_ids"]
    validate_novel_ingestion(result["catalog"], result["store"])
    assert (result["work_dir"] / "catalog.json").is_file()


def test_ingest_strict_order_reports_missing_chapter(tmp_path):
    source = tmp_path / "book.txt"
    source.write_text("第一章 开始\n正文。\n\n第三章 跳跃\n正文二。", encoding="utf-8")
    spec = {
        "source": {"kind": "txt", "path": str(source), "title": "缺章小说"},
        "evidence": {"tier": "A", "access_legitimacy": "AUTHORIZED"},
        "strict_order": True,
    }

    result = run_novel_ingestion(spec, tmp_path / "run", repo_root=repo_root(), now=NOW)

    assert result["ingestion"]["status"] == "FAILED"
    assert result["ingestion"]["order_validation"]["missing_declared_numbers"] == [2]


def test_strict_order_does_not_pass_when_a_chapter_number_is_unknown():
    chapters = [
        {"chapter_id": "CHP-ONE", "status": "READY", "declared_number": 1},
        {"chapter_id": "CHP-UNKNOWN", "status": "READY", "declared_number": None},
    ]

    result = novel_ingest._order_validation(chapters, strict=True)

    assert result["status"] == "FAIL"


def test_strict_order_does_not_claim_order_when_all_numbers_are_unknown():
    chapters = [
        {"chapter_id": "CHP-ONE", "status": "READY", "declared_number": None},
        {"chapter_id": "CHP-TWO", "status": "READY", "declared_number": None},
    ]

    result = novel_ingest._order_validation(chapters, strict=True)

    assert result["status"] == "FAIL"


def test_order_validation_rejects_unbounded_declared_number_gap(monkeypatch):
    chapters = [
        {"chapter_id": "CHP-ONE", "status": "READY", "declared_number": 1},
        {"chapter_id": "CHP-HUGE", "status": "READY", "declared_number": 1_000_000_000},
    ]

    def guarded_range(*args):
        if len(args) >= 2 and int(args[1]) - int(args[0]) > 10_000:
            raise AssertionError("order validation attempted to expand an unbounded range")
        return builtins.range(*args)

    monkeypatch.setattr(novel_ingest, "range", guarded_range, raising=False)

    with pytest.raises(ValidationError) as exc:
        novel_ingest._order_validation(chapters, strict=True)
    assert exc.value.code == "E-CHAPTER-ORDER"


def test_text_resume_rejects_changed_full_source_bytes(tmp_path, monkeypatch):
    source = tmp_path / "book.txt"
    source.write_text("第一章 开始\n正文。\n\n第二章 继续\n原文。", encoding="utf-8")
    spec = {
        "source": {"kind": "txt", "path": str(source), "title": "测试小说"},
        "evidence": {"tier": "A", "access_legitimacy": "AUTHORIZED"},
    }
    work_dir = tmp_path / "run"
    _interrupt_local_ingestion(monkeypatch, TextNovelAdapter, spec, work_dir)
    source.write_text("第一章 开始\n正文。\n\n第二章 继续\n被篡改的正文。", encoding="utf-8")

    with pytest.raises(ValidationError) as exc:
        run_novel_ingestion(spec, work_dir, repo_root=repo_root(), now=NOW)
    assert exc.value.code == "E-NOVEL-SOURCE-CHANGED"


def test_directory_resume_rejects_changed_unfinished_chapter(tmp_path, monkeypatch):
    source = tmp_path / "chapters"
    source.mkdir()
    (source / "001.txt").write_text("第一章 开始\n正文。", encoding="utf-8")
    second = source / "002.txt"
    second.write_text("第二章 继续\n原文。", encoding="utf-8")
    spec = {
        "source": {"kind": "directory", "path": str(source), "title": "测试小说"},
        "evidence": {"tier": "A", "access_legitimacy": "AUTHORIZED"},
    }
    work_dir = tmp_path / "run"
    _interrupt_local_ingestion(monkeypatch, DirectoryNovelAdapter, spec, work_dir)
    second.write_text("第二章 继续\n被篡改的正文。", encoding="utf-8")

    with pytest.raises(ValidationError) as exc:
        run_novel_ingestion(spec, work_dir, repo_root=repo_root(), now=NOW)
    assert exc.value.code == "E-NOVEL-SOURCE-CHANGED"


def test_directory_fetch_rejects_file_changed_after_discovery(tmp_path):
    source = tmp_path / "chapters"
    source.mkdir()
    chapter = source / "001.txt"
    chapter.write_text("第一章 开始\n原文。", encoding="utf-8")
    adapter = DirectoryNovelAdapter({"path": str(source)})
    discovery = adapter.discover()
    chapter.write_text("第一章 开始\n被篡改的正文。", encoding="utf-8")

    with pytest.raises(ValidationError) as exc:
        adapter.fetch_chapter(discovery.chapters[0])
    assert exc.value.code == "E-NOVEL-SOURCE-CHANGED"


def test_epub_resume_rejects_changed_full_source_bytes(tmp_path, monkeypatch):
    source = tmp_path / "book.epub"
    _write_epub(source)
    spec = {
        "source": {"kind": "epub", "path": str(source)},
        "evidence": {"tier": "A", "access_legitimacy": "AUTHORIZED"},
    }
    work_dir = tmp_path / "run"
    _interrupt_local_ingestion(monkeypatch, EpubNovelAdapter, spec, work_dir)
    _write_epub(source, second_body="被篡改的章节。")

    with pytest.raises(ValidationError) as exc:
        run_novel_ingestion(spec, work_dir, repo_root=repo_root(), now=NOW)
    assert exc.value.code == "E-NOVEL-SOURCE-CHANGED"


def test_epub_fetch_rejects_member_changed_after_discovery(tmp_path):
    source = tmp_path / "book.epub"
    _write_epub(source)
    adapter = EpubNovelAdapter({"path": str(source)})
    discovery = adapter.discover()
    _write_epub(source, second_body="被篡改的章节。")

    with pytest.raises(ValidationError) as exc:
        adapter.fetch_chapter(discovery.chapters[1])
    assert exc.value.code == "E-NOVEL-SOURCE-CHANGED"


def test_site_ingest_resumes_after_interrupted_chapter_without_refetching_completed(tmp_path):
    spec = _site_spec()
    calls: list[str] = []

    class FirstFetcher:
        def fetch(self, url):
            calls.append(url)
            if url.endswith("/index"):
                return (
                    '<a href="/chapter/1">第一章</a><a href="/chapter/2">第二章</a>'.encode(),
                    "text/html",
                    200,
                    url,
                )
            if url.endswith("/chapter/1"):
                return b"<h1>First</h1><p>one</p>", "text/html", 200, url
            raise ValidationError("E-UNREACHABLE", "interrupted")

    with pytest.raises(ValidationError, match="interrupted"):
        run_novel_ingestion(
            spec,
            tmp_path / "run",
            repo_root=repo_root(),
            fetcher=FirstFetcher(),
            now=NOW,
        )

    checkpoint = json.loads((tmp_path / "run" / "ingestion-checkpoint.json").read_text())
    assert len(checkpoint["completed"]) == 1

    resumed_calls: list[str] = []

    class ResumeFetcher:
        def fetch(self, url):
            resumed_calls.append(url)
            if url.endswith("/chapter/2"):
                return b"<h1>Second</h1><p>two</p>", "text/html", 200, url
            raise AssertionError(f"resume unexpectedly fetched {url}")

    result = run_novel_ingestion(
        spec,
        tmp_path / "run",
        repo_root=repo_root(),
        fetcher=ResumeFetcher(),
        now=NOW,
    )

    assert resumed_calls == ["https://novel.example/chapter/2"]
    assert result["ingestion"]["resumed_from_checkpoint"] is True
    assert result["ingestion"]["status"] == "SUCCEEDED"
    assert len(result["ingestion"]["ready_chapter_ids"]) == 2


def test_checkpoint_rejects_changed_spec(tmp_path):
    spec = _site_spec()

    class InterruptingFetcher:
        def fetch(self, url):
            if url.endswith("/index"):
                return b'<a href="/chapter/1">First</a>', "text/html", 200, url
            raise ValidationError("E-UNREACHABLE", "stop")

    with pytest.raises(ValidationError):
        run_novel_ingestion(
            spec,
            tmp_path / "run",
            repo_root=repo_root(),
            fetcher=InterruptingFetcher(),
            now=NOW,
        )
    changed = _site_spec()
    changed["limits"]["max_bytes"] = 9999

    with pytest.raises(ValidationError) as exc:
        run_novel_ingestion(
            changed,
            tmp_path / "run",
            repo_root=repo_root(),
            fetcher=InterruptingFetcher(),
            now=NOW,
        )
    assert exc.value.code == "E-CHECKPOINT-INPUT"


def test_completed_same_second_rerun_has_distinct_identity_and_immutable_output(tmp_path):
    source = tmp_path / "chapters"
    source.mkdir()
    (source / "001.txt").write_text("第一章\n\n正文。", encoding="utf-8")
    spec = {"source": {"kind": "directory", "path": str(source)}, "strict_order": True}
    work_dir = tmp_path / "run"

    first = run_novel_ingestion(spec, work_dir, repo_root=repo_root(), now=NOW)
    second = run_novel_ingestion(spec, work_dir, repo_root=repo_root(), now=NOW)

    assert first["ingestion"]["resumed_from_checkpoint"] is False
    assert second["ingestion"]["resumed_from_checkpoint"] is True
    assert first["ingestion"]["ingestion_run_id"] != second["ingestion"]["ingestion_run_id"]
    assert first["work_dir"].is_dir()
    assert second["work_dir"].is_dir()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda checkpoint: checkpoint.__setitem__("discovery_complete", False),
        lambda checkpoint: checkpoint["work"].__setitem__("title", "被篡改的作品"),
        lambda checkpoint: checkpoint["chapter_refs"][0].__setitem__("title", "被篡改的章节"),
        lambda checkpoint: checkpoint["provenance"][0].__setitem__("locator", "https://evil.example/index"),
        lambda checkpoint: next(iter(checkpoint["completed"].values())).__setitem__(
            "retrieved_at", "2026-01-01T00:00:00Z"
        ),
    ],
    ids=["discovery", "work", "refs", "provenance", "completed"],
)
def test_checkpoint_tampering_cannot_be_washed_by_resealing(tmp_path, mutate):
    spec = _site_spec()
    work_dir = tmp_path / "run"

    class InterruptingFetcher:
        def fetch(self, url):
            if url.endswith("/index"):
                return (
                    b'<a href="/chapter/1">First</a><a href="/chapter/2">Second</a>',
                    "text/html",
                    200,
                    url,
                )
            if url.endswith("/chapter/1"):
                return b"<p>one</p>", "text/html", 200, url
            raise ValidationError("E-UNREACHABLE", "stop")

    with pytest.raises(ValidationError, match="stop"):
        run_novel_ingestion(
            spec,
            work_dir,
            repo_root=repo_root(),
            fetcher=InterruptingFetcher(),
            now=NOW,
        )

    checkpoint_path = work_dir / "ingestion-checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    mutate(checkpoint)
    checkpoint["integrity_hash"] = object_hash(checkpoint, omit=("integrity_hash",))
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    class ResumeFetcher:
        def fetch(self, url):
            if url.endswith("/chapter/2"):
                return b"<p>two</p>", "text/html", 200, url
            raise AssertionError(f"resume unexpectedly fetched {url}")

    with pytest.raises(ValidationError) as exc:
        run_novel_ingestion(
            spec,
            work_dir,
            repo_root=repo_root(),
            fetcher=ResumeFetcher(),
            now=NOW,
        )
    assert exc.value.code == "E-CHECKPOINT-INTEGRITY"
