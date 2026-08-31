from __future__ import annotations

import json
import os
import pathlib
import zipfile

import pytest

from xhnovel_pipeline.runtime import TEST_NOW as NOW
from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.hashing import object_hash
from xhnovel_pipeline.artifact_closure import live_artifact_ids
from xhnovel_pipeline.ids import derived_id
from xhnovel_pipeline import novel_ingest
from xhnovel_pipeline.novel_adapters import DirectoryNovelAdapter
from xhnovel_pipeline.novel_adapters import StaticNovelSiteAdapter
from xhnovel_pipeline.novel_ingest import (
    novel_ingestion_artifact_ids,
    run_novel_ingestion,
    validate_novel_ingestion,
)
from xhnovel_pipeline.paths import repo_root


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _adapter(fetcher, **overrides):
    spec = {
        "kind": "site",
        "index_url": "https://novel.example/index",
        "chapter_url_pattern": r"/chapter/\d+$",
        "title": "测试小说",
        **overrides,
    }
    return StaticNovelSiteAdapter(spec, fetcher=fetcher)


def _successful_site_ingestion(tmp_path):
    spec = {
        "source": {
            "kind": "site",
            "index_url": "https://novel.example/index",
            "chapter_url_pattern": r"/chapter/\d+$",
            "title": "测试小说",
        },
        "strict_order": False,
    }

    class Fetcher:
        def fetch(self, url):
            if url.endswith("/index"):
                return '<a href="/chapter/1">第一章</a>'.encode(), "text/html", 200, url
            if url.endswith("/chapter/1"):
                return (
                    "<html><body><article><p>章节正文。</p></article></body></html>".encode(),
                    "text/html; charset=utf-8",
                    200,
                    "https://novel.example/chapter/9",
                )
            raise AssertionError(f"unexpected URL {url}")

    return spec, run_novel_ingestion(
        spec,
        tmp_path / "run",
        repo_root=repo_root(),
        fetcher=Fetcher(),
        now=NOW,
    )


def _only_failure_manifest(work_dir):
    manifests = list((work_dir / "failures").glob("*/failure-manifest.json"))
    assert len(manifests) == 1
    return json.loads(manifests[0].read_text(encoding="utf-8"))


def _attempt_receipt(store, receipt_artifact_id):
    return json.loads(store.get(receipt_artifact_id).decode("utf-8"))


def test_first_index_http_failure_persists_structured_attempt_and_raw_body(tmp_path):
    body = b"not found from index"
    work_dir = tmp_path / "failed-index"

    class Fetcher:
        def fetch(self, url):
            return body, "text/html; charset=utf-8", 404, "https://novel.example/missing"

    with pytest.raises(ValidationError):
        run_novel_ingestion(
            {
                "source": {
                    "kind": "site",
                    "index_url": "https://novel.example/index",
                    "chapter_url_pattern": r"/chapter/\d+$",
                }
            },
            work_dir,
            repo_root=repo_root(),
            fetcher=Fetcher(),
            now=NOW,
        )

    store = novel_ingest.ArtifactStore(work_dir / "objects")
    manifest = _only_failure_manifest(work_dir)
    receipt = _attempt_receipt(store, manifest["attempt_receipt_artifact_id"])
    assert receipt == manifest["attempt"]
    assert receipt["stage"] == "INDEX"
    assert receipt["requested_url"] == "https://novel.example/index"
    assert receipt["final_url"] == "https://novel.example/missing"
    assert receipt["http_status"] == 404
    assert receipt["content_type"] == "text/html; charset=utf-8"
    assert receipt["status"] == "FAILED"
    assert receipt["error_code"] == "E-NOVEL-HTTP"
    assert store.get(receipt["raw_artifact_id"]) == body
    assert manifest["retrieval"]["status"] == "FAILED"
    assert manifest["retrieval"]["http_status"] == 404


def test_failed_chapter_attempt_is_materialized_and_success_retries_it(tmp_path):
    work_dir = tmp_path / "retry-chapter"
    spec = {
        "source": {
            "kind": "site",
            "index_url": "https://novel.example/index",
            "chapter_url_pattern": r"/chapter/\d+$",
        }
    }

    class FirstFetcher:
        def fetch(self, url):
            if url.endswith("/index"):
                return b'<a href="/chapter/1">one</a>', "text/html", 200, url
            return b"rate limited", "text/plain", 429, url

    with pytest.raises(ValidationError):
        run_novel_ingestion(
            spec,
            work_dir,
            repo_root=repo_root(),
            fetcher=FirstFetcher(),
            now=NOW,
        )

    checkpoint = json.loads((work_dir / "ingestion-checkpoint.json").read_text(encoding="utf-8"))
    assert len(checkpoint["site_attempt_receipt_ids"]) == 2

    class ResumeFetcher:
        def fetch(self, url):
            assert url.endswith("/chapter/1")
            return b"<p>chapter body</p>", "text/html", 200, url

    result = run_novel_ingestion(
        spec,
        work_dir,
        repo_root=repo_root(),
        fetcher=ResumeFetcher(),
        now="2026-08-31T00:00:01Z",
    )
    attempts = [
        retrieval
        for retrieval in result["catalog"].all("Retrieval")
        if retrieval["requested_url"] == "https://novel.example/chapter/1"
    ]
    assert [attempt["status"] for attempt in attempts] == ["FAILED", "FETCHED"]
    assert attempts[1]["retry_of"] == attempts[0]["retrieval_id"]
    failed_edges = [
        edge
        for edge in result["catalog"].all("RetrievalArtifact")
        if edge["retrieval_id"] == attempts[0]["retrieval_id"]
    ]
    assert len(failed_edges) == 1
    assert result["store"].get(failed_edges[0]["artifact_id"]) == b"rate limited"
    assert result["chapters"][0]["retrieval_id"] == attempts[1]["retrieval_id"]
    validate_novel_ingestion(result["catalog"], result["store"])


@pytest.mark.parametrize(
    "error_code,http_status,raw",
    [
        ("E-DECOMPRESSION", 200, b"invalid compressed wire bytes"),
        ("E-UNREACHABLE", None, None),
    ],
)
def test_index_transport_failure_preserves_available_evidence(tmp_path, error_code, http_status, raw):
    work_dir = tmp_path / error_code

    class Fetcher:
        def fetch(self, url):
            if error_code == "E-UNREACHABLE":
                raise TimeoutError("timed out")
            exc = ValidationError(error_code, "decode failed")
            exc.raw_response_bytes = raw
            exc.requested_url = url
            exc.final_url = url
            exc.http_status = http_status
            exc.content_type = "text/html"
            raise exc

    with pytest.raises((ValidationError, TimeoutError)):
        run_novel_ingestion(
            {
                "source": {
                    "kind": "site",
                    "index_url": "https://novel.example/index",
                    "chapter_url_pattern": r"/chapter/\d+$",
                }
            },
            work_dir,
            repo_root=repo_root(),
            fetcher=Fetcher(),
            now=NOW,
        )

    store = novel_ingest.ArtifactStore(work_dir / "objects")
    receipt = _attempt_receipt(store, _only_failure_manifest(work_dir)["attempt_receipt_artifact_id"])
    assert receipt["error_code"] == error_code
    assert receipt["http_status"] == http_status
    if raw is None:
        assert receipt["raw_artifact_id"] is None
    else:
        assert store.get(receipt["raw_artifact_id"]) == raw


@pytest.mark.parametrize(
    "raw,media,final_url,error_code",
    [
        (b"", "text/html", "https://novel.example/index", "E-NOVEL-EMPTY"),
        (b"{}", "application/json", "https://novel.example/index", "E-NOVEL-MIME"),
        (
            b'<a href="/chapter/1">one</a>',
            "text/html",
            "https://other.example/index",
            "E-NOVEL-SCOPE",
        ),
    ],
)
def test_index_processing_failure_has_one_failed_attempt_and_retry_chain(
    tmp_path, raw, media, final_url, error_code
):
    work_dir = tmp_path / error_code
    spec = {
        "source": {
            "kind": "site",
            "index_url": "https://novel.example/index",
            "chapter_url_pattern": r"/chapter/\d+$",
        }
    }

    class InvalidFetcher:
        def fetch(self, url):
            return raw, media, 200, final_url

    with pytest.raises(ValidationError) as exc:
        run_novel_ingestion(
            spec,
            work_dir,
            repo_root=repo_root(),
            fetcher=InvalidFetcher(),
            now=NOW,
        )
    assert exc.value.code == error_code
    failed_manifest = _only_failure_manifest(work_dir)
    assert failed_manifest["attempt"]["error_code"] == error_code
    assert failed_manifest["attempt"]["status"] == "FAILED"

    class ValidFetcher:
        def fetch(self, url):
            if url.endswith("/index"):
                return b'<a href="/chapter/1">one</a>', "text/html", 200, url
            return b"<p>chapter</p>", "text/html", 200, url

    result = run_novel_ingestion(
        spec,
        work_dir,
        repo_root=repo_root(),
        fetcher=ValidFetcher(),
        now="2026-08-31T00:00:01Z",
    )
    index_attempts = [
        retrieval
        for retrieval in result["catalog"].all("Retrieval")
        if retrieval["access_kind"] == "index_page"
    ]
    assert [attempt["status"] for attempt in index_attempts] == ["FAILED", "FETCHED"]
    assert index_attempts[1]["retry_of"] == index_attempts[0]["retrieval_id"]


def test_site_discovery_rejects_non_200_index_response():
    class Fetcher:
        def fetch(self, url):
            return b'<a href="/chapter/1">one</a>', "text/html", 204, url

    with pytest.raises(ValidationError) as exc:
        _adapter(Fetcher()).discover()

    assert exc.value.code == "E-NOVEL-HTTP"


def test_site_fetch_rejects_non_200_or_empty_chapter():
    class Fetcher:
        def __init__(self, chapter_response):
            self.chapter_response = chapter_response

        def fetch(self, url):
            if url.endswith("/index"):
                return b'<a href="/chapter/1">one</a>', "text/html", 200, url
            data, status = self.chapter_response
            return data, "text/html", status, url

    for response, expected_code in [
        ((b"", 200), "E-NOVEL-EMPTY"),
        ((b"<html><body><script>no chapter prose</script></body></html>", 200), "E-NOVEL-EMPTY"),
        ((b"<p>not returned</p>", 204), "E-NOVEL-HTTP"),
    ]:
        adapter = _adapter(Fetcher(response))
        chapter = adapter.discover().chapters[0]
        with pytest.raises(ValidationError) as exc:
            adapter.fetch_chapter(chapter)
        assert exc.value.code == expected_code


def test_site_fetch_revalidates_final_redirect_url_against_chapter_pattern():
    class Fetcher:
        def fetch(self, url):
            if url.endswith("/index"):
                return b'<a href="/chapter/1">one</a>', "text/html", 200, url
            return b"<p>login page</p>", "text/html", 200, "https://novel.example/login"

    adapter = _adapter(Fetcher())
    chapter = adapter.discover().chapters[0]

    with pytest.raises(ValidationError) as exc:
        adapter.fetch_chapter(chapter)

    assert exc.value.code == "E-NOVEL-SCOPE"


def test_external_chapters_do_not_allow_external_index_pagination():
    calls: list[str] = []

    class Fetcher:
        def fetch(self, url):
            calls.append(url)
            if url == "https://novel.example/index":
                return (
                    b'<a href="https://chapters.example/chapter/1">one</a>'
                    b'<a rel="next" href="https://other.example/index?page=2">next</a>',
                    "text/html",
                    200,
                    url,
                )
            raise AssertionError(f"unexpected index traversal: {url}")

    adapter = _adapter(Fetcher(), allow_external_chapters=True)

    with pytest.raises(ValidationError) as exc:
        adapter.discover()

    assert exc.value.code == "E-NOVEL-SCOPE"
    assert calls == ["https://novel.example/index"]


def test_site_ingestion_binds_actual_status_and_redirect_across_checkpoint_receipt_and_retrieval(tmp_path):
    _, result = _successful_site_ingestion(tmp_path)
    run = result["ingestion"]
    checkpoint = json.loads(result["store"].get(run["checkpoint_artifact_id"]).decode("utf-8"))
    chapter_key = checkpoint["chapter_refs"][0]["chapter_key"]
    completion = checkpoint["completed"][chapter_key]
    receipt = json.loads(result["store"].get(completion["receipt_artifact_id"]).decode("utf-8"))
    retrieval = result["catalog"].get("Retrieval", result["chapters"][0]["retrieval_id"])

    assert checkpoint["chapter_refs"][0]["source_locator"] == "https://novel.example/chapter/1"
    assert completion["http_status"] == receipt["http_status"] == retrieval["http_status"] == 200
    assert (
        completion["final_locator"]
        == receipt["final_locator"]
        == retrieval["final_url"]
        == "https://novel.example/chapter/9"
    )
    validate_novel_ingestion(result["catalog"], result["store"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("requested_url", "https://novel.example/chapter/7"),
        ("final_url", "https://novel.example/chapter/8"),
        ("http_status", 201),
        ("content_type", "text/plain"),
    ],
)
def test_validator_rejects_retrieval_fields_diverging_from_frozen_site_receipt(tmp_path, field, value):
    _, result = _successful_site_ingestion(tmp_path)
    result["catalog"].get("Retrieval", result["chapters"][0]["retrieval_id"])[field] = value

    with pytest.raises(ValidationError) as exc:
        validate_novel_ingestion(result["catalog"], result["store"])
    assert exc.value.code == "E-NOVEL-RETRIEVAL-BIND"


def test_validator_rejects_retrieval_artifact_diverging_from_completed_chapter(tmp_path):
    _, result = _successful_site_ingestion(tmp_path)
    retrieval_id = result["chapters"][0]["retrieval_id"]
    edge = next(
        edge
        for edge in result["catalog"].all("RetrievalArtifact")
        if edge["retrieval_id"] == retrieval_id
    )
    edge["artifact_id"] = result["ingestion"]["checkpoint_artifact_id"]

    with pytest.raises(ValidationError) as exc:
        validate_novel_ingestion(result["catalog"], result["store"])
    assert exc.value.code == "E-NOVEL-RETRIEVAL-BIND"


def test_validator_rejects_empty_chapter_segment_partition_even_if_hash_is_resealed(tmp_path):
    _, result = _successful_site_ingestion(tmp_path)
    chapter = result["chapters"][0]
    chapter["segment_ids"] = []
    chapter["normalized_content_hash"] = object_hash({"segment_hashes": []}, omit=())

    with pytest.raises(ValidationError) as exc:
        validate_novel_ingestion(result["catalog"], result["store"])
    assert exc.value.code == "E-NOVEL-EMPTY"


def test_validator_rejects_receipt_diverging_from_resealed_checkpoint(tmp_path):
    _, result = _successful_site_ingestion(tmp_path)
    store = result["store"]
    run = result["ingestion"]
    checkpoint = json.loads(store.get(run["checkpoint_artifact_id"]).decode("utf-8"))
    completion = next(iter(checkpoint["completed"].values()))
    receipt = json.loads(store.get(completion["receipt_artifact_id"]).decode("utf-8"))
    receipt["final_locator"] = "https://novel.example/chapter/999"
    completion["receipt_artifact_id"] = store.put(_json_bytes(receipt))
    checkpoint["integrity_hash"] = object_hash(checkpoint, omit=("integrity_hash",))
    checkpoint_bytes = _json_bytes(checkpoint)
    run["checkpoint_artifact_id"] = store.put(checkpoint_bytes)
    run["checkpoint_hash"] = object_hash(checkpoint, omit=())
    run_identity = {
        "work_id": run["work_id"],
        "input_spec_hash": run["input_spec_hash"],
        "adapter_build_id": run["adapter_build_id"],
        "chapter_ids": run["chapter_ids"],
        "checkpoint_hash": run["checkpoint_hash"],
        "strict_order": run["strict_order"],
    }
    run["ingestion_run_id"] = derived_id("NovelIngestionRun", run_identity)

    with pytest.raises(ValidationError) as exc:
        validate_novel_ingestion(result["catalog"], store)
    assert exc.value.code == "E-CHECKPOINT-INTEGRITY"


def test_validator_requires_local_retrieval_http_status_to_remain_none(tmp_path):
    source = tmp_path / "chapters"
    source.mkdir()
    (source / "001.txt").write_text("第一章\n\n本地正文。", encoding="utf-8")
    result = run_novel_ingestion(
        {"source": {"kind": "directory", "path": str(source)}, "strict_order": True},
        tmp_path / "local-run",
        repo_root=repo_root(),
        now=NOW,
    )
    retrieval = result["catalog"].all("Retrieval")[0]
    assert retrieval["http_status"] is None
    retrieval["http_status"] = 200

    with pytest.raises(ValidationError) as exc:
        validate_novel_ingestion(result["catalog"], result["store"])
    assert exc.value.code == "E-NOVEL-HTTP-BIND"


@pytest.mark.parametrize(
    "field,value",
    [
        ("final_locator", "file:///forged/chapter.txt"),
        ("media_type", "text/html"),
    ],
)
def test_validator_rejects_local_completion_diverging_from_frozen_ref(tmp_path, field, value):
    source = tmp_path / "chapters"
    source.mkdir()
    (source / "001.txt").write_text("第一章\n\n本地正文。", encoding="utf-8")
    result = run_novel_ingestion(
        {"source": {"kind": "directory", "path": str(source)}, "strict_order": True},
        tmp_path / "local-run",
        repo_root=repo_root(),
        now=NOW,
    )
    store = result["store"]
    run = result["ingestion"]
    checkpoint = json.loads(store.get(run["checkpoint_artifact_id"]).decode("utf-8"))
    completion = next(iter(checkpoint["completed"].values()))
    receipt = json.loads(store.get(completion["receipt_artifact_id"]).decode("utf-8"))
    completion[field] = value
    receipt[field] = value
    completion["receipt_artifact_id"] = store.put(_json_bytes(receipt))
    checkpoint["integrity_hash"] = object_hash(checkpoint, omit=("integrity_hash",))
    run["checkpoint_artifact_id"] = store.put(_json_bytes(checkpoint))
    run["checkpoint_hash"] = object_hash(checkpoint, omit=())

    chapter = result["chapters"][0]
    retrieval = result["catalog"].all("Retrieval")[0]
    source_record = result["catalog"].all("Source")[0]
    if field == "final_locator":
        chapter["source_locator"] = value
        retrieval["final_url"] = value
        source_record["canonical_url"] = value
    else:
        chapter["media_type"] = value
        retrieval["content_type"] = value
        result["catalog"].get("Artifact", chapter["artifact_id"])["media_type"] = value
    run_identity = {
        "work_id": run["work_id"],
        "input_spec_hash": run["input_spec_hash"],
        "adapter_build_id": run["adapter_build_id"],
        "chapter_ids": run["chapter_ids"],
        "checkpoint_hash": run["checkpoint_hash"],
        "strict_order": run["strict_order"],
    }
    run["ingestion_run_id"] = derived_id("NovelIngestionRun", run_identity)

    with pytest.raises(ValidationError) as exc:
        validate_novel_ingestion(result["catalog"], store)
    assert exc.value.code == "E-NOVEL-RETRIEVAL-BIND"


@pytest.mark.parametrize("kind", ["txt", "directory", "epub"])
def test_local_adapter_rejects_top_level_byte_limit_before_reading_source(tmp_path, monkeypatch, kind):
    if kind == "txt":
        source = tmp_path / "book.txt"
        source.write_bytes(b"x" * 200)
    elif kind == "directory":
        source = tmp_path / "chapters"
        source.mkdir()
        (source / "001.txt").write_bytes(b"x" * 60)
        (source / "002.txt").write_bytes(b"y" * 60)
    else:
        source = tmp_path / "book.epub"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("payload.bin", b"x" * 200)

    source_files = {source.resolve()} if source.is_file() else {
        path.resolve() for path in source.iterdir() if path.is_file()
    }
    original_read_bytes = pathlib.Path.read_bytes

    def guarded_read_bytes(path):
        if path.resolve() in source_files:
            raise AssertionError("source bytes were read before enforcing top-level max_bytes")
        return original_read_bytes(path)

    monkeypatch.setattr(pathlib.Path, "read_bytes", guarded_read_bytes)

    with pytest.raises(ValidationError) as exc:
        run_novel_ingestion(
            {"source": {"kind": kind, "path": str(source)}, "limits": {"max_bytes": 50}},
            tmp_path / "limited-run",
            repo_root=repo_root(),
            now=NOW,
        )
    assert exc.value.code == "E-NOVEL-LIMIT"


def test_text_adapter_bounded_read_rejects_growth_after_stale_stat(tmp_path, monkeypatch):
    source = (tmp_path / "book.txt").resolve()
    source.write_bytes(b"x" * 200)
    original_stat = pathlib.Path.stat
    original_open = pathlib.Path.open

    def stale_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == source:
            values = list(result)
            values[6] = 1
            return os.stat_result(values)
        return result

    monkeypatch.setattr(pathlib.Path, "stat", stale_stat)

    class GuardedReader:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def read(self, size=-1):
            if size < 0 or size > 51:
                raise AssertionError("source read was not bounded to max_bytes + 1")
            return self.handle.read(size)

        def __getattr__(self, name):
            return getattr(self.handle, name)

    def guarded_open(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        mode = args[0] if args else kwargs.get("mode", "r")
        if path == source and mode == "rb":
            return GuardedReader(handle)
        return handle

    monkeypatch.setattr(pathlib.Path, "open", guarded_open)

    with pytest.raises(ValidationError) as exc:
        run_novel_ingestion(
            {"source": {"kind": "txt", "path": str(source)}, "limits": {"max_bytes": 50}},
            tmp_path / "limited-run",
            repo_root=repo_root(),
            now=NOW,
        )
    assert exc.value.code == "E-NOVEL-LIMIT"


def test_site_adapter_rejects_oversized_index_before_parsing(tmp_path):
    class Fetcher:
        def fetch(self, url):
            return b" " * 100, "text/html", 200, url

    with pytest.raises(ValidationError) as exc:
        run_novel_ingestion(
            {
                "source": {
                    "kind": "site",
                    "index_url": "https://novel.example/index",
                    "chapter_url_pattern": r"/chapter/\d+$",
                },
                "limits": {"max_bytes": 50},
            },
            tmp_path / "limited-site-run",
            repo_root=repo_root(),
            fetcher=Fetcher(),
            now=NOW,
        )
    assert exc.value.code == "E-NOVEL-LIMIT"


def test_default_site_fetcher_enforces_top_level_limit_during_transport():
    adapter = StaticNovelSiteAdapter(
        {
            "kind": "site",
            "index_url": "https://novel.example/index",
            "chapter_url_pattern": r"/chapter/\d+$",
            "_ingestion_max_bytes": 50,
        }
    )

    assert adapter.fetcher.max_bytes == 50


def test_site_index_provenance_has_a_cumulative_memory_budget():
    first = b'<a rel="next" href="/index?page=2">next</a>' + b" " * 55
    second = b'<a href="/chapter/1">one</a>' + b" " * 65
    assert max(len(first), len(second)) < 120 < len(first) + len(second)

    class Fetcher:
        def fetch(self, url):
            return (
                first if url.endswith("/index") else second,
                "text/html",
                200,
                url,
            )

    adapter = StaticNovelSiteAdapter(
        {
            "kind": "site",
            "index_url": "https://novel.example/index",
            "chapter_url_pattern": r"/chapter/\d+$",
            "max_index_pages": 2,
            "max_index_bytes": 120,
        },
        fetcher=Fetcher(),
    )

    with pytest.raises(ValidationError) as exc:
        adapter.discover()

    assert exc.value.code == "E-NOVEL-LIMIT"


def test_top_level_chapter_limit_fails_inside_site_discovery_and_records_index_attempt(tmp_path):
    class Fetcher:
        def fetch(self, url):
            assert url == "https://novel.example/index"
            return (
                b'<a href="/chapter/1">one</a><a href="/chapter/2">two</a>',
                "text/html",
                200,
                url,
            )

    work_dir = tmp_path / "chapter-limited-site"
    with pytest.raises(ValidationError) as exc:
        run_novel_ingestion(
            {
                "source": {
                    "kind": "site",
                    "index_url": "https://novel.example/index",
                    "chapter_url_pattern": r"/chapter/\d+$",
                    "max_chapters": 10_000,
                },
                "limits": {"max_chapters": 1},
            },
            work_dir,
            repo_root=repo_root(),
            fetcher=Fetcher(),
            now=NOW,
        )

    assert exc.value.code == "E-NOVEL-LIMIT"
    manifest = _only_failure_manifest(work_dir)
    assert manifest["attempt"]["stage"] == "INDEX"
    assert manifest["attempt"]["status"] == "FAILED"
    assert manifest["attempt"]["error_code"] == "E-NOVEL-LIMIT"


def test_ingestion_refuses_a_second_writer_for_the_same_work_dir(tmp_path):
    source = tmp_path / "chapters"
    source.mkdir()
    (source / "001.txt").write_text("第一章\n\n正文。", encoding="utf-8")
    spec = {"source": {"kind": "directory", "path": str(source)}}
    work_dir = tmp_path / "shared-run"

    with novel_ingest._exclusive_work_dir(work_dir):
        with pytest.raises(ValidationError) as exc:
            run_novel_ingestion(spec, work_dir, repo_root=repo_root(), now=NOW)

    assert exc.value.code == "E-NOVEL-WORKDIR-LOCKED"
    assert not (work_dir / "ingestion-checkpoint.json").exists()
    result = run_novel_ingestion(spec, work_dir, repo_root=repo_root(), now=NOW)
    assert result["ingestion"]["status"] == "SUCCEEDED"


def test_epub_member_is_capped_by_top_level_ingestion_limit_before_read(tmp_path, monkeypatch):
    source = tmp_path / "book.epub"
    container = """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles>
</container>"""
    opf = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>测试</dc:title></metadata>
  <manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="c1"/></spine>
</package>"""
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/c1.xhtml", "<p>" + "正文" * 10_000 + "</p>")
    assert source.stat().st_size < 5_000
    original_read = zipfile.ZipFile.read

    def guarded_read(archive, name, *args, **kwargs):
        if name == "OEBPS/c1.xhtml":
            raise AssertionError("oversized EPUB member was decompressed before limit rejection")
        return original_read(archive, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "read", guarded_read)

    with pytest.raises(ValidationError) as exc:
        run_novel_ingestion(
            {"source": {"kind": "epub", "path": str(source)}, "limits": {"max_bytes": 5_000}},
            tmp_path / "limited-epub-run",
            repo_root=repo_root(),
            now=NOW,
        )
    assert exc.value.code == "E-NOVEL-LIMIT"


def test_validator_fails_when_site_attempt_receipt_cas_object_is_missing(tmp_path):
    _, result = _successful_site_ingestion(tmp_path)
    checkpoint = json.loads(
        result["store"].get(result["ingestion"]["checkpoint_artifact_id"]).decode("utf-8")
    )
    receipt_artifact_id = checkpoint["site_attempt_receipt_ids"][0]
    assert result["catalog"].get("Artifact", receipt_artifact_id)["media_type"] == "application/json"
    result["store"].delete_for_test(receipt_artifact_id)

    with pytest.raises(ValidationError) as exc:
        validate_novel_ingestion(result["catalog"], result["store"])
    assert exc.value.code == "E-ARTIFACT-MISSING"


def test_site_attempt_receipts_and_raw_bodies_are_in_ingestion_cas_live_closure(tmp_path):
    _, result = _successful_site_ingestion(tmp_path)
    checkpoint = json.loads(
        result["store"].get(result["ingestion"]["checkpoint_artifact_id"]).decode("utf-8")
    )
    expected = set(checkpoint["site_attempt_receipt_ids"])
    for receipt_artifact_id in checkpoint["site_attempt_receipt_ids"]:
        receipt = _attempt_receipt(result["store"], receipt_artifact_id)
        expected.add(receipt["raw_artifact_id"])

    closure = set(
        novel_ingestion_artifact_ids(
            result["catalog"],
            result["store"],
            result["ingestion"],
        )
    )
    catalog_artifacts = set(result["catalog"].ids("Artifact"))
    catalog_data = {
        kind: records for kind, records in result["catalog"].by_type.items() if records
    }

    assert expected <= closure
    assert expected <= catalog_artifacts
    assert expected <= live_artifact_ids(catalog_data)


def _crash_after_completion_marker(tmp_path, monkeypatch):
    source = tmp_path / "chapters"
    source.mkdir()
    (source / "001.txt").write_text("第一章\n\n可恢复正文。", encoding="utf-8")
    spec = {"source": {"kind": "directory", "path": str(source)}, "strict_order": True}
    work_dir = tmp_path / "crash-run"
    original_write_checkpoint = novel_ingest._write_checkpoint

    class SimulatedProcessCrash(BaseException):
        pass

    def crash_once_checkpoint_has_completion(path, state):
        if state.get("completed"):
            raise SimulatedProcessCrash()
        return original_write_checkpoint(path, state)

    monkeypatch.setattr(novel_ingest, "_write_checkpoint", crash_once_checkpoint_has_completion)
    with pytest.raises(SimulatedProcessCrash):
        run_novel_ingestion(spec, work_dir, repo_root=repo_root(), now=NOW)
    monkeypatch.setattr(novel_ingest, "_write_checkpoint", original_write_checkpoint)
    return spec, work_dir


def test_resume_reconciles_valid_orphan_completion_marker_after_process_crash(tmp_path, monkeypatch):
    spec, work_dir = _crash_after_completion_marker(tmp_path, monkeypatch)

    def unexpected_refetch(self, chapter):
        raise AssertionError(f"reconciled chapter was refetched: {chapter.chapter_key}")

    monkeypatch.setattr(DirectoryNovelAdapter, "fetch_chapter", unexpected_refetch)
    result = run_novel_ingestion(spec, work_dir, repo_root=repo_root(), now=NOW)

    checkpoint = json.loads((work_dir / "ingestion-checkpoint.json").read_text(encoding="utf-8"))
    assert result["ingestion"]["resumed_from_checkpoint"] is True
    assert len(checkpoint["completed"]) == 1


def test_resume_rejects_multiple_completion_markers_for_one_chapter(tmp_path, monkeypatch):
    spec, work_dir = _crash_after_completion_marker(tmp_path, monkeypatch)
    checkpoint = json.loads((work_dir / "ingestion-checkpoint.json").read_text(encoding="utf-8"))
    chapter_key = checkpoint["chapter_refs"][0]["chapter_key"]
    store = novel_ingest.ArtifactStore(work_dir / "objects")
    artifact_id = store.put(b"forged")
    forged_receipt = {
        "chapter_key": chapter_key,
        "artifact_id": artifact_id,
        "byte_length": len(b"forged"),
        "media_type": "text/plain",
        "final_locator": checkpoint["chapter_refs"][0]["source_locator"],
        "http_status": None,
        "retrieved_at": NOW,
    }
    forged_receipt_id = store.put(_json_bytes(forged_receipt))
    forged_marker = novel_ingest._completion_marker_path(work_dir, chapter_key, forged_receipt_id)
    forged_marker.parent.mkdir(parents=True, exist_ok=True)
    forged_marker.write_bytes(
        _json_bytes({"chapter_key": chapter_key, "receipt_artifact_id": forged_receipt_id})
    )

    with pytest.raises(ValidationError) as exc:
        run_novel_ingestion(spec, work_dir, repo_root=repo_root(), now=NOW)
    assert exc.value.code == "E-CHECKPOINT-INTEGRITY"


def test_resume_rejects_orphan_marker_outside_frozen_refs(tmp_path, monkeypatch):
    spec, work_dir = _crash_after_completion_marker(tmp_path, monkeypatch)
    store = novel_ingest.ArtifactStore(work_dir / "objects")
    unknown_key = "chapter-not-in-discovery"
    artifact_id = store.put(b"forged")
    forged_receipt = {
        "chapter_key": unknown_key,
        "artifact_id": artifact_id,
        "byte_length": len(b"forged"),
        "media_type": "text/plain",
        "final_locator": "file:///forged.txt",
        "http_status": None,
        "retrieved_at": NOW,
    }
    forged_receipt_id = store.put(_json_bytes(forged_receipt))
    forged_marker = novel_ingest._completion_marker_path(work_dir, unknown_key, forged_receipt_id)
    forged_marker.parent.mkdir(parents=True, exist_ok=True)
    forged_marker.write_bytes(
        _json_bytes({"chapter_key": unknown_key, "receipt_artifact_id": forged_receipt_id})
    )

    with pytest.raises(ValidationError) as exc:
        run_novel_ingestion(spec, work_dir, repo_root=repo_root(), now=NOW)
    assert exc.value.code == "E-CHECKPOINT-INTEGRITY"
