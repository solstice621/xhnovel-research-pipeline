from __future__ import annotations

import json
import pathlib
import re
import zipfile

import pytest

from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.hashing import artifact_id_for
from xhnovel_pipeline.novel_adapters import (
    DirectoryNovelAdapter,
    EpubNovelAdapter,
    StaticNovelSiteAdapter,
    TextNovelAdapter,
    chapter_number,
)
from xhnovel_pipeline.parse import decode_text


def _write_epub(path):
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
        archive.writestr("OEBPS/c2.xhtml", "<html><body><h1>第二章 拜师</h1><p>长老收他为徒。</p></body></html>")


def test_text_adapter_splits_chapters_and_parses_chinese_numbers(tmp_path):
    path = tmp_path / "novel.txt"
    path.write_text("序言\n\n第一章 入山\n少年入山。\n\n第二章 拜师\n长老收徒。\n", encoding="utf-8")
    adapter = TextNovelAdapter({"kind": "txt", "path": str(path), "title": "测试仙途"})

    discovery = adapter.discover()

    assert [chapter.title for chapter in discovery.chapters] == [
        "前置内容",
        "第一章 入山",
        "第二章 拜师",
    ]
    assert [chapter.chapter_kind for chapter in discovery.chapters] == [
        "FRONTMATTER",
        "MAIN",
        "MAIN",
    ]
    assert [chapter.declared_number for chapter in discovery.chapters] == [None, 1, 2]
    assert adapter.fetch_chapter(discovery.chapters[0])[0] == "序言".encode()
    assert adapter.fetch_chapter(discovery.chapters[1])[0].startswith("第一章".encode())
    assert chapter_number("第一百零二章 风起") == 102
    assert chapter_number("第万章 非法章号") is None


@pytest.mark.parametrize(
    ("encoding", "source"),
    [
        ("big5", "第一章 風起雲湧\n少年來到山門，長老收他為徒。"),
        ("gb18030", "第一章 风起云涌\n少年来到山门，长老收他为徒。"),
    ],
)
def test_auto_encoding_distinguishes_big5_and_gb18030(encoding, source):
    text, detected = decode_text(source.encode(encoding), "auto")

    assert text == source
    assert detected == encoding


def test_auto_encoding_fails_closed_when_legacy_bytes_are_ambiguous():
    with pytest.raises(ValidationError) as exc:
        decode_text(bytes.fromhex("aa40"), "auto")

    assert exc.value.code == "E-TEXT-ENCODING-AMBIGUOUS"


@pytest.mark.parametrize("changed_dependency", ["parse.py", "novel_ingest.py"])
def test_adapter_build_identity_changes_with_transitive_implementation_bytes(
    tmp_path, monkeypatch, changed_dependency
):
    source = tmp_path / "novel.txt"
    source.write_text("第一章\n正文。", encoding="utf-8")
    adapter = TextNovelAdapter({"kind": "txt", "path": str(source)})
    original_build_id = adapter.adapter_id

    original_read_bytes = pathlib.Path.read_bytes

    def changed_read_bytes(path):
        data = original_read_bytes(path)
        return data + b"\nchanged dependency" if path.name == changed_dependency else data

    monkeypatch.setattr(pathlib.Path, "read_bytes", changed_read_bytes)

    changed_build_id = TextNovelAdapter({"kind": "txt", "path": str(source)}).adapter_id

    assert re.fullmatch(r"novel-text-v1\+sha256:[0-9a-f]{64}", original_build_id)
    assert changed_build_id != original_build_id


def test_directory_adapter_naturally_orders_chapter_files(tmp_path):
    (tmp_path / "chapter-10.txt").write_text("第十章", encoding="utf-8")
    (tmp_path / "chapter-2.txt").write_text("第二章", encoding="utf-8")
    adapter = DirectoryNovelAdapter({"kind": "directory", "path": str(tmp_path)})

    discovery = adapter.discover()

    assert [chapter.title for chapter in discovery.chapters] == ["chapter-2", "chapter-10"]
    assert [chapter.declared_number for chapter in discovery.chapters] == [2, 10]
    assert not any(chapter.derived_from_provenance for chapter in discovery.chapters)
    manifest = json.loads(discovery.provenance[0].data)
    assert manifest["format"] == "xhnovel-directory-manifest-v1"
    assert [item["relative_path"] for item in manifest["files"]] == [
        "chapter-2.txt",
        "chapter-10.txt",
    ]
    assert [item["artifact_id"] for item in manifest["files"]] == [
        artifact_id_for((tmp_path / "chapter-2.txt").read_bytes()),
        artifact_id_for((tmp_path / "chapter-10.txt").read_bytes()),
    ]


def test_directory_adapter_rejects_tampered_checkpoint_path(tmp_path):
    chapter_dir = tmp_path / "chapters"
    chapter_dir.mkdir()
    (chapter_dir / "1.txt").write_text("第一章", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("not a chapter", encoding="utf-8")
    adapter = DirectoryNovelAdapter({"kind": "directory", "path": str(chapter_dir)})
    chapter = adapter.discover().chapters[0]
    object.__setattr__(chapter, "adapter_data", {"path": str(outside)})

    with pytest.raises(ValidationError, match="E-NOVEL-SCOPE"):
        adapter.fetch_chapter(chapter)


def test_epub_adapter_uses_spine_order_and_preserves_archive(tmp_path):
    path = tmp_path / "book.epub"
    _write_epub(path)
    adapter = EpubNovelAdapter({"kind": "epub", "path": str(path)})

    discovery = adapter.discover()

    assert discovery.work.title == "测试仙途"
    assert discovery.work.author == "测试作者"
    assert [chapter.title for chapter in discovery.chapters] == ["第一章 入山", "第二章 拜师"]
    assert discovery.provenance[0].data == path.read_bytes()
    assert b"\xe9\x95\xbf\xe8\x80\x81" in adapter.fetch_chapter(discovery.chapters[1])[0]


def test_epub_adapter_rejects_unsafe_members(tmp_path):
    path = tmp_path / "unsafe.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../escape", "bad")

    with pytest.raises(ValidationError) as exc:
        EpubNovelAdapter({"kind": "epub", "path": str(path)}).discover()
    assert exc.value.code == "E-EPUB-PATH"


def test_static_site_adapter_follows_index_pagination_and_deduplicates_links():
    pages = {
        "https://novel.example/index": (
            b'<a href="/chapter/1">\xe7\xac\xac\xe4\xb8\x80\xe7\xab\xa0</a>'
            b'<a href="mailto:editor@example.com">mail</a>'
            b'<a href="javascript:void(0)">script</a>'
            b'<a href="">empty</a>'
            b'<a rel="next" href="/index?page=2">next</a>',
            "text/html",
        ),
        "https://novel.example/index?page=2": (
            b'<a href="/chapter/1">duplicate</a><a href="/chapter/2">second</a>',
            "text/html",
        ),
        "https://novel.example/chapter/1": (b"<p>one</p>", "text/html"),
        "https://novel.example/chapter/2": (b"<p>two</p>", "text/html"),
    }

    class Fetcher:
        def fetch(self, url):
            data, media = pages[url]
            return data, media, 200, url

    adapter = StaticNovelSiteAdapter(
        {
            "kind": "site",
            "index_url": "https://novel.example/index",
            "chapter_url_pattern": r"/chapter/\d+$",
            "title": "站点小说",
        },
        fetcher=Fetcher(),
    )
    discovery = adapter.discover()

    assert [chapter.source_locator for chapter in discovery.chapters] == [
        "https://novel.example/chapter/1",
        "https://novel.example/chapter/2",
    ]
    assert len(discovery.provenance) == 2
    assert adapter.fetch_chapter(discovery.chapters[1])[0] == b"<p>two</p>"


def test_static_site_adapter_decodes_declared_gbk_index_labels():
    index = (
        '<html><head><meta charset="gbk"></head><body>'
        '<a href="/chapter/1">第一章 风起</a>'
        "</body></html>"
    ).encode("gb18030")

    class Fetcher:
        def fetch(self, url):
            return index, "text/html; charset=gbk", 200, url

    discovery = StaticNovelSiteAdapter(
        {
            "kind": "site",
            "index_url": "https://novel.example/index",
            "chapter_url_pattern": r"/chapter/\d+$",
        },
        fetcher=Fetcher(),
    ).discover()

    assert discovery.chapters[0].title == "第一章 风起"
    assert discovery.chapters[0].declared_number == 1


def test_static_site_adapter_refuses_cross_origin_chapters():
    class Fetcher:
        def fetch(self, url):
            return b'<a href="https://other.example/chapter/1">one</a>', "text/html", 200, url

    adapter = StaticNovelSiteAdapter(
        {
            "kind": "site",
            "index_url": "https://novel.example/index",
            "chapter_url_pattern": r"/chapter/\d+$",
        },
        fetcher=Fetcher(),
    )
    with pytest.raises(ValidationError) as exc:
        adapter.discover()
    assert exc.value.code == "E-NOVEL-SCOPE"


@pytest.mark.parametrize(
    "pattern",
    [r"(?:/chapter/)+$", r"(/chapter/+)+$", r"/chapter/(\\1)+$"],
)
def test_static_site_adapter_rejects_complex_user_regex(pattern):
    with pytest.raises(ValidationError) as exc:
        StaticNovelSiteAdapter(
            {
                "kind": "site",
                "index_url": "https://novel.example/index",
                "chapter_url_pattern": pattern,
            }
        )

    assert exc.value.code == "E-NOVEL-SPEC"
