#!/usr/bin/env python3
"""Bounded host acquisition and audited local-source preparation.

This script is deliberately outside the installed Evidence Compiler. It never
constructs semantic tasks, candidates, or replacement native pipeline objects.
"""
from __future__ import annotations

import argparse
import contextlib
import email.utils
import hashlib
import json
import math
import os
import re
import shutil
import signal
import sys
import tempfile
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xhnovel_pipeline.canonical import canonical_dumps
from xhnovel_pipeline.errors import PipelineError, ValidationError
from xhnovel_pipeline.file_io import atomic_write, write_immutable
from xhnovel_pipeline.hashing import artifact_id_for
from xhnovel_pipeline.http_fetch import _request_once
from xhnovel_pipeline.novel_adapters import DirectoryNovelAdapter, adapter_from_spec, chapter_number
from xhnovel_pipeline.novel_ingest import _exclusive_work_dir
from xhnovel_pipeline.phase0_common import require_fields
from xhnovel_pipeline.phase0_handoff import validate_operator_attestation

FORMAT = "source-acquisition-v1"
ASSESSMENTS = ("identity", "whole_work", "catalog_coverage", "chapter_relationships", "text_integrity", "dom_order")
VERDICTS = {"PASS", "FAIL", "UNRESOLVED"}
DEFAULT_LIMITS = {
    "min_gap_seconds": 5,
    "slow_start_gap_seconds": 10,
    "slow_start_requests": 20,
    "request_timeout_seconds": 30,
    "max_response_bytes": 2_000_000,
    "max_input_bytes": 500_000_000,
    "max_attempts_per_entry": 3,
    "max_redirects": 5,
    "max_run_seconds": 1800,
    "consecutive_transport_failures": 3,
    "no_commit_seconds": 300,
}
MAX_METADATA_BYTES = 20_000_000
CHALLENGES = ("just a moment...", "cf-chl-", "verify you are human", "checking your browser")
BLOCK_TAGS = {"p", "div", "article", "section", "li", "h1", "h2", "h3"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class AcquisitionError(ValidationError):
    def __init__(self, code: str, message: str, *, pending: bool = False):
        super().__init__(code, message)
        self.exit_code = 4 if pending else 2


def fail(message: str, code: str = "E-ACQUISITION-INPUT", *, pending: bool = False):
    raise AcquisitionError(code, message, pending=pending)


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def object_digest(value: Any) -> str:
    return digest(canonical_dumps(value))


def fields(value: Any, required: set[str], optional: set[str] = frozenset(), *, label: str):
    if not isinstance(value, dict):
        fail(f"{label} must be an object")
    require_fields(value, required=required, optional=optional, code="E-ACQUISITION-INPUT", label=label)


def string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        fail(f"{label} must be a nonempty string")
    return value


def key(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", value):
        fail("entry/chapter/source keys must be safe ASCII identifiers")
    return value


def integer(value: Any, label: str, *, minimum: int = 1, maximum: int = 2_000_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        fail(f"{label} must be an integer in {minimum}..{maximum}")
    return value


def no_symlinks(path: Path) -> Path:
    path = path.absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        fail(f"symlink path is not permitted: {path}", "E-ACQUISITION-PATH")
    return path


def child(root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or not candidate.parts or any(p in {".", ".."} for p in candidate.parts):
        fail("artifact path must be relative and contained", "E-ACQUISITION-PATH")
    if "\\" in relative or "\x00" in relative:
        fail("nonportable artifact path", "E-ACQUISITION-PATH")
    return no_symlinks(root / candidate)


def read_bytes(path: Path, limit: int = MAX_METADATA_BYTES) -> bytes:
    no_symlinks(path)
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        fail(f"file exceeds configured bound: {path}", "E-ACQUISITION-SIZE")
    return data


def read_json(path: Path) -> dict:
    try:
        value = json.loads(read_bytes(path).decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        fail(f"invalid JSON in {path}: {exc}")
    if not isinstance(value, dict):
        fail(f"{path} must contain an object")
    return value


def _unique_object(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            fail(f"duplicate JSON key {k}")
        result[k] = v
    return result


def sync_directory(path: Path):
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def put(path: Path, data: bytes, *, mutable: bool = False):
    no_symlinks(path)
    (atomic_write if mutable else write_immutable)(path, data)
    sync_directory(path.parent)


def put_json(path: Path, value: dict, *, mutable: bool = False):
    put(path, canonical_dumps(value) + b"\n", mutable=mutable)


def ref(path: Path, data: bytes | None = None) -> dict:
    return {"path": str(path), "sha256": digest(read_bytes(path) if data is None else data)}


def checked_ref(value: Any, base: Path, limit: int = MAX_METADATA_BYTES) -> tuple[Path, bytes]:
    fields(value, {"path", "sha256"}, label="file reference")
    path = Path(string(value["path"], "reference path"))
    path = path if path.is_absolute() else base / path
    data = read_bytes(path, limit)
    if digest(data) != value["sha256"]:
        fail(f"reference hash mismatch: {path}", "E-ACQUISITION-INTEGRITY")
    return path, data


def verdict(value: Any, base: Path):
    fields(value, {"status", "reason", "evidence"}, label="assessment")
    if value["status"] not in VERDICTS or not isinstance(value["evidence"], list):
        fail("invalid assessment")
    string(value["reason"], "assessment reason")
    if value["status"] == "PASS" and not value["evidence"]:
        fail("PASS assessment requires evidence references")
    for item in value["evidence"]:
        checked_ref(item, base)


def scoped_url(url: str, scope: str):
    parsed, allowed = urlsplit(string(url, "URL")), urlsplit(string(scope, "scope URL"))
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.fragment:
        fail("unsupported or credential-bearing URL", "E-ACQUISITION-SCOPE")
    if allowed.scheme not in {"http", "https"} or allowed.username or allowed.password or not allowed.hostname:
        fail("invalid source scope", "E-ACQUISITION-SCOPE")
    try:
        origin = (parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        allowed_origin = (allowed.scheme, allowed.hostname, allowed.port or (443 if allowed.scheme == "https" else 80))
    except ValueError:
        fail("invalid URL port", "E-ACQUISITION-SCOPE")
    decoded = unquote(parsed.path)
    prefix = unquote(allowed.path)
    if not prefix.endswith("/") or origin != allowed_origin or not decoded.startswith(prefix):
        fail("URL leaves the configured work/source scope", "E-ACQUISITION-SCOPE")
    if any(p in {".", ".."} for p in decoded.split("/")) or "\\" in decoded:
        fail("ambiguous URL path", "E-ACQUISITION-SCOPE")


def validate_catalog(cat: dict, base: Path, source: dict):
    fields(cat, {"format_version", "entries", "chapters", "assessments"}, label="catalog")
    if cat["format_version"] != FORMAT:
        fail("unsupported catalog version")
    if not isinstance(cat["entries"], list) or not 1 <= len(cat["entries"]) <= 100_000:
        fail("catalog requires a bounded nonempty entry list")
    seen, import_paths = set(), set()
    for item in cat["entries"]:
        fields(item, {"key", "url", "import_path", "expected_title"}, label="entry")
        k = key(item["key"])
        if k.casefold() in seen:
            fail("duplicate entry key")
        seen.add(k.casefold())
        if item["url"] is not None:
            scoped_url(item["url"], source["scope_url"])
        if source["channel"] == "C1" and item["url"] is None:
            fail("C1 entries require URLs")
        if item["import_path"] is not None:
            p = item["import_path"]
            child(base, string(p, "import path"))
            if p.casefold() in import_paths:
                fail("duplicate import path")
            import_paths.add(p.casefold())
        if item["expected_title"] is not None:
            string(item["expected_title"], "expected title")
    if not isinstance(cat["chapters"], list) or not cat["chapters"]:
        fail("catalog requires logical chapters")
    ordered_entries, chapter_keys = [], set()
    for ch in cat["chapters"]:
        fields(ch, {"key", "title", "entry_keys", "role"}, label="logical chapter")
        k = key(ch["key"])
        if k.casefold() in chapter_keys:
            fail("duplicate logical chapter key")
        chapter_keys.add(k.casefold())
        if ch["title"] is not None:
            string(ch["title"], "chapter title")
        if ch["role"] not in {"MAIN", "SUPPLEMENT"}:
            fail("chapter role must be MAIN or SUPPLEMENT")
        if not isinstance(ch["entry_keys"], list) or not ch["entry_keys"]:
            fail("each logical chapter requires ordered entries")
        ordered_entries.extend(ch["entry_keys"])
    if ordered_entries != [e["key"] for e in cat["entries"]]:
        fail("logical chapter pages must partition the entire catalog in reading order")
    fields(cat["assessments"], set(ASSESSMENTS), label="catalog assessments")
    for assessment in cat["assessments"].values():
        verdict(assessment, base)


def validate_config(cfg: dict, base: Path) -> tuple[dict, bytes, bytes]:
    fields(cfg, {"format_version", "run_dir", "work", "source", "attestation", "catalog"}, {"limits"}, label="config")
    if cfg["format_version"] != FORMAT:
        fail("unsupported config version")
    string(cfg["run_dir"], "run directory")
    fields(cfg["work"], {"title", "author", "language"}, label="work")
    for name, value in cfg["work"].items():
        string(value, name)
    source = cfg["source"]
    fields(source, {"id", "channel", "scope_url", "edition_status", "edition_label", "extractor", "browser_authorization"}, label="source")
    key(source["id"])
    if source["channel"] not in {"C1", "C2", "C4"}:
        fail("unsupported acquisition channel")
    if source["edition_status"] not in {"OFFICIAL", "PUBLISHED_EDITION", "USER_VERIFIED_COPY", "UNOFFICIAL_COPY", "UNKNOWN"}:
        fail("invalid edition status")
    string(source["edition_label"], "edition label")
    fields(source["extractor"], {"kind", "title_selector", "body_selector", "exclude_selectors", "strip_leading_title"}, label="extractor")
    ex = source["extractor"]
    if ex["kind"] not in {"TXT", "HTML"} or not isinstance(ex["strip_leading_title"], bool):
        fail("unsupported extractor")
    if not isinstance(ex["exclude_selectors"], list):
        fail("exclude selectors must be a list")
    for selector in ex["exclude_selectors"] + ([ex["title_selector"], ex["body_selector"]] if ex["kind"] == "HTML" else []):
        validate_selector(selector)
    if ex["kind"] == "TXT" and (ex["title_selector"] is not None or ex["body_selector"] is not None or ex["exclude_selectors"]):
        fail("TXT does not accept HTML selectors")
    if source["browser_authorization"] is not None:
        checked_ref(source["browser_authorization"], base)
    limits = cfg.get("limits", {})
    fields(limits, set(), set(DEFAULT_LIMITS), label="limits")
    effective = {**DEFAULT_LIMITS, **limits}
    for name, value in effective.items():
        integer(value, name, minimum=0 if name == "slow_start_requests" else 1)
    if effective["slow_start_gap_seconds"] < effective["min_gap_seconds"]:
        fail("slow-start gap cannot be shorter than the base gap")
    _, att_bytes = checked_ref(cfg["attestation"], base)
    att = validate_operator_attestation(json.loads(att_bytes))
    if att["basis"] == "UNKNOWN" or not att["may_store_full_text"]:
        fail("standing attestation does not permit full-text storage", "E-ACQUISITION-RIGHTS")
    _, cat_bytes = checked_ref(cfg["catalog"], base)
    cat = json.loads(cat_bytes, object_pairs_hook=_unique_object)
    validate_catalog(cat, base, source)
    return cat, cat_bytes, att_bytes


def validate_selector(selector):
    if not isinstance(selector, str) or not re.fullmatch(r"(?:[a-z][a-z0-9-]*)?(?:[.#][A-Za-z_][A-Za-z0-9_-]*)?", selector) or not selector:
        fail("selectors support one tag, #id, .class or tag#id/tag.class")


@dataclass
class Node:
    tag: str
    attrs: dict
    children: list = field(default_factory=list)

    def matches(self, selector: str) -> bool:
        m = re.fullmatch(r"([a-z][a-z0-9-]*)?(?:([.#])([A-Za-z_][A-Za-z0-9_-]*))?", selector)
        tag, marker, name = m.groups()
        return (not tag or tag == self.tag) and (
            not marker or (self.attrs.get("id") == name if marker == "#" else name in self.attrs.get("class", "").split())
        )

    def nodes(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.nodes()

    def text(self, excluded: list[str]) -> str:
        if self.tag in {"script", "style", "noscript", "nav", "header", "footer", "aside", "svg"} or any(self.matches(s) for s in excluded):
            return ""
        if self.tag == "br":
            return "\n"
        body = "".join(c.text(excluded) if isinstance(c, Node) else c for c in self.children)
        return "\n" + body + "\n" if self.tag in BLOCK_TAGS else body


class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in VOID_TAGS:
            return
        if len(self.stack) == 1 or self.stack[-1].tag != tag:
            fail("unbalanced HTML; extraction requires a verified DOM fixture", "E-ACQUISITION-EXTRACT")
        self.stack.pop()

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def extract(data: bytes, extractor: dict, expected_title: str | None) -> tuple[str, str]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        fail("source is not valid UTF-8", "E-ACQUISITION-EXTRACT")
    if any(marker in text.casefold() for marker in CHALLENGES):
        fail("challenge page received", "E-ACQUISITION-ACCESS")
    if "\ufffd" in text or "\x00" in text:
        fail("replacement or NUL character in source", "E-ACQUISITION-EXTRACT")
    if extractor["kind"] == "TXT":
        title, _, body = text.partition("\n")
        title, body = title.strip(), body.strip()
    else:
        document = Document()
        document.feed(text)
        document.close()
        if len(document.stack) != 1:
            fail("truncated HTML document", "E-ACQUISITION-EXTRACT")
        def select(selector):
            nodes = [n for n in document.root.nodes() if n.matches(selector)]
            if len(nodes) != 1:
                fail(f"selector {selector!r} has {len(nodes)} matches", "E-ACQUISITION-EXTRACT")
            return nodes[0]
        title = select(extractor["title_selector"]).text([]).strip()
        body = select(extractor["body_selector"]).text(extractor["exclude_selectors"]).strip()
        if extractor["strip_leading_title"] and body.startswith(title):
            body = body[len(title):].strip()
        body = "\n".join(line.strip() for line in body.splitlines() if line.strip())
    if not title or not body:
        fail("empty title or chapter body", "E-ACQUISITION-EXTRACT")
    if expected_title is not None and title != expected_title:
        fail("chapter title differs from the fixed catalog", "E-ACQUISITION-IDENTITY")
    return title, body


class Clock:
    def now_ms(self) -> int:
        return time.time_ns() // 1_000_000

    def monotonic_ms(self) -> int:
        return time.monotonic_ns() // 1_000_000

    def sleep_ms(self, milliseconds: int):
        time.sleep(milliseconds / 1000)


def timestamp(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Response:
    status: int | None
    body: bytes
    content_type: str = "text/plain"
    location: str | None = None
    retry_after: str | None = None
    error: str | None = None


@contextlib.contextmanager
def wall_timeout(seconds: int):
    """macOS/POSIX main-thread deadline includes DNS, TLS and trickle reads."""
    if not hasattr(signal, "setitimer"):
        fail("bounded C1 transport currently requires POSIX", "E-ACQUISITION-PLATFORM")
    if signal.getitimer(signal.ITIMER_REAL)[0]:
        fail("another alarm is active", "E-ACQUISITION-PLATFORM")
    previous = signal.getsignal(signal.SIGALRM)
    def expired(*_):
        raise TimeoutError("acquisition request wall deadline exceeded")
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def transport(url: str, timeout: int, max_bytes: int) -> Response:
    # Reuse the native *single-hop* pinned-public-address transport; no implicit
    # redirect, retry, decompression, browser fallback or proxy reconfiguration.
    try:
        with wall_timeout(timeout):
            raw, headers, status = _request_once(url, timeout=timeout, max_bytes=max_bytes)
        encoding = headers.get("Content-Encoding", "identity").casefold()
        if encoding not in {"", "identity"}:
            return Response(status, raw, error="unsupported content encoding")
        return Response(status, raw, headers.get("Content-Type", ""), headers.get("Location"), headers.get("Retry-After"))
    except (OSError, TimeoutError, ValidationError) as exc:
        if isinstance(exc, AcquisitionError):
            raise
        return Response(
            getattr(exc, "http_status", None),
            getattr(exc, "raw_response_bytes", b"")[:max_bytes],
            error=getattr(exc, "code", type(exc).__name__),
        )


def retry_after_ms(value: str | None, now: int) -> int:
    try:
        if value and re.fullmatch(r"[0-9]+", value.strip()):
            return now + int(value.strip()) * 1000
        parsed = email.utils.parsedate_to_datetime(value or "")
        if parsed.tzinfo is None:
            raise ValueError("missing timezone")
        return max(now, math.ceil(parsed.timestamp() * 1000))
    except (TypeError, ValueError, OverflowError):
        return now + 900_000


class Run:
    def __init__(self, root: Path, clock: Clock | None = None):
        self.root = no_symlinks(root)
        self.clock = clock or Clock()
        self.cfg = read_json(self.root / "config.json")
        self.cat = read_json(self.root / "expected-catalog.json")
        self.binding = read_json(self.root / "binding.json")
        expected = {
            "format_version": FORMAT,
            "config_sha256": digest(read_bytes(self.root / "config.json")),
            "catalog_sha256": digest(read_bytes(self.root / "expected-catalog.json")),
            "attestation_sha256": digest(read_bytes(self.root / "operator-attestation.json")),
            "tool_sha256": digest(Path(__file__).read_bytes()),
        }
        if expected != self.binding:
            fail("run configuration, tool or frozen inputs changed", "E-ACQUISITION-INTEGRITY")
        # Revalidate snapshotted inputs; original external files are not trusted
        # as the continuing source of an already initialized run.
        validate_config(self.cfg, self.root)
        self.limits = {**DEFAULT_LIMITS, **self.cfg.get("limits", {})}
        self.entries = {e["key"]: e for e in self.cat["entries"]}

    @classmethod
    def initialize(cls, config_path: Path, clock: Clock | None = None):
        cfg = read_json(config_path)
        cat, _, att = validate_config(cfg, config_path.parent)
        run_path = Path(cfg["run_dir"])
        root = no_symlinks(run_path if run_path.is_absolute() else config_path.parent / run_path)
        # Resolve once; cwd never changes the identity or frozen paths.
        root = root.resolve()
        cfg["run_dir"] = str(root)
        evidence = {}
        def snapshot_reference(item):
            _, data = checked_ref(item, config_path.parent)
            name = digest(data).split(":")[1]
            evidence[name] = data
            return {"path": f"catalog-evidence/{name}.bin", "sha256": digest(data)}
        for assessment in cat["assessments"].values():
            assessment["evidence"] = [snapshot_reference(r) for r in assessment["evidence"]]
        if cfg["source"]["browser_authorization"] is not None:
            cfg["source"]["browser_authorization"] = snapshot_reference(cfg["source"]["browser_authorization"])
        cfg["attestation"] = {"path": "operator-attestation.json", "sha256": digest(att)}
        cat_data = canonical_dumps(cat) + b"\n"
        cfg["catalog"] = {"path": "expected-catalog.json", "sha256": digest(cat_data)}
        with _exclusive_work_dir(root):
            for name, data in evidence.items():
                put(root / "catalog-evidence" / (name + ".bin"), data)
            put(root / "operator-attestation.json", att)
            put(root / "expected-catalog.json", cat_data)
            put_json(root / "config.json", cfg)
            put_json(root / "binding.json", {
                "format_version": FORMAT,
                "config_sha256": digest(read_bytes(root / "config.json")),
                "catalog_sha256": digest(cat_data),
                "attestation_sha256": digest(att),
                "tool_sha256": digest(Path(__file__).read_bytes()),
            })
        return cls(root, clock)

    def _bound(self, value: dict):
        if value.get("binding_sha256") != object_digest(self.binding):
            fail("attempt belongs to different frozen inputs", "E-ACQUISITION-INTEGRITY")

    def starts(self) -> list[dict]:
        values = []
        for directory in sorted((self.root / "started").iterdir()) if (self.root / "started").exists() else []:
            if directory.name not in self.entries or not directory.is_dir():
                fail("STARTED entry outside the catalog", "E-ACQUISITION-INTEGRITY")
            for i, p in enumerate(sorted(directory.glob("*.json")), 1):
                value = read_json(p)
                fields(value, {
                    "format_version", "binding_sha256", "entry", "ordinal", "requested_url", "channel", "started_at_ms",
                }, {"source_archive", "archive_ordinal"}, label="STARTED")
                self._bound(value)
                if p.name != f"{i:06d}.json" or value["ordinal"] != i or value["entry"] != directory.name:
                    fail("STARTED sequence differs", "E-ACQUISITION-INTEGRITY")
                integer(value["started_at_ms"], "started time", minimum=0, maximum=10**16)
                values.append(value)
        return values

    def attempts(self, entry_key: str) -> list[dict]:
        records = []
        directory = child(self.root, f"attempts/{entry_key}")
        for p in sorted(directory.glob("*.json")):
            value = read_json(p)
            fields(value, {
                "format_version", "binding_sha256", "entry", "ordinal", "channel", "requested_url",
                "started_at_ms", "finished_at_ms", "status", "content_type", "location",
                "retry_after", "retry_not_before_ms", "raw", "result", "error",
            }, {"source_archive", "archive_ordinal"}, label="attempt")
            self._bound(value)
            if value["entry"] != entry_key or p.name != f"{value['ordinal']:06d}.json":
                fail("attempt identity mismatch", "E-ACQUISITION-INTEGRITY")
            expected_raw = f"raw/{entry_key}/{value['ordinal']:06d}.bin"
            if value["raw"].get("path") != expected_raw:
                fail("attempt raw path mismatch", "E-ACQUISITION-INTEGRITY")
            start = read_json(self.root / f"started/{entry_key}/{value['ordinal']:06d}.json")
            self._bound(start)
            if any(value.get(k) != v for k, v in start.items()):
                fail("attempt does not match its durable STARTED record", "E-ACQUISITION-INTEGRITY")
            checked_ref(value["raw"], self.root, self.limits["max_response_bytes"])
            records.append(value)
        return records

    def accepted(self) -> dict[str, dict]:
        result = {}
        archives = {}
        self.starts()
        for p in sorted((self.root / "accepted").glob("*.json")):
            k = p.stem
            if k not in self.entries:
                fail("accepted entry outside fixed catalog", "E-ACQUISITION-INTEGRITY")
            value = read_json(p)
            fields(value, {"format_version", "binding_sha256", "entry", "attempt", "title", "body_sha256", "derived", "committed_at_ms"}, label="accepted")
            self._bound(value)
            if value["entry"] != k:
                fail("accepted identity mismatch", "E-ACQUISITION-INTEGRITY")
            if value["derived"].get("path") != f"chapters/{k}.txt":
                fail("accepted derived path mismatch", "E-ACQUISITION-INTEGRITY")
            if not isinstance(value["attempt"].get("path"), str) or not re.fullmatch(rf"attempts/{re.escape(k)}/[0-9]{{6}}\.json", value["attempt"]["path"]):
                fail("accepted attempt path mismatch", "E-ACQUISITION-INTEGRITY")
            attempt_path, _ = checked_ref(value["attempt"], self.root)
            if attempt_path != self.root / f"attempts/{k}/{read_json(attempt_path)['ordinal']:06d}.json":
                fail("accepted attempt path mismatch", "E-ACQUISITION-INTEGRITY")
            matches = [a for a in self.attempts(k) if a["ordinal"] == read_json(attempt_path)["ordinal"]]
            if len(matches) != 1 or matches[0]["result"] != "FETCHED":
                fail("accepted requires exactly one successful fetch/import", "E-ACQUISITION-INTEGRITY")
            attempt = matches[0]
            _, raw = checked_ref(attempt["raw"], self.root, self.limits["max_response_bytes"])
            if attempt["channel"] == "LOCAL_ORIGINAL_IMPORT":
                if "source_archive" not in attempt or "archive_ordinal" not in attempt:
                    fail("archive import is missing provenance", "E-ACQUISITION-INTEGRITY")
                archive_ref = attempt["source_archive"]
                archive_path = child(self.root, archive_ref["path"])
                archive_key = archive_ref["sha256"]
                if archive_key not in archives:
                    _, original = checked_ref(archive_ref, self.root, self.limits["max_input_bytes"])
                    kind = archive_path.suffix.lstrip(".")
                    adapter = self.local_adapter(archive_path, kind)
                    archives[archive_key] = (adapter, adapter.discover().chapters)
                adapter, chapters = archives[archive_key]
                index = integer(attempt["archive_ordinal"], "archive ordinal") - 1
                if index >= len(chapters) or adapter.fetch_chapter(chapters[index])[0] != raw:
                    fail("imported chapter differs from original local source", "E-ACQUISITION-INTEGRITY")
            title, body = extract(raw, self.cfg["source"]["extractor"], self.entries[k]["expected_title"])
            _, actual = checked_ref(value["derived"], self.root, self.limits["max_response_bytes"] * 2)
            if actual != self.derived_bytes(title, body) or value["title"] != title or value["body_sha256"] != digest(body.encode()):
                fail("accepted derivation cannot be replayed", "E-ACQUISITION-INTEGRITY")
            result[k] = value
        return result

    @staticmethod
    def derived_bytes(title: str, body: str) -> bytes:
        return (title + "\n\n" + body + "\n").encode("utf-8")

    def _commit(self, entry: dict, attempt: dict, crash: Callable[[str], None] = lambda _: None, *, accepted=None):
        k = entry["key"]
        _, raw = checked_ref(attempt["raw"], self.root, self.limits["max_response_bytes"])
        title, body = extract(raw, self.cfg["source"]["extractor"], entry["expected_title"])
        data = self.derived_bytes(title, body)
        accepted = self.accepted() if accepted is None else accepted
        existing = accepted.get(k)
        if existing:
            accepted_attempt = read_json(child(self.root, existing["attempt"]["path"]))
            if accepted_attempt["raw"]["sha256"] != attempt["raw"]["sha256"] or accepted_attempt.get("source_archive") != attempt.get("source_archive"):
                self._state("SOURCE_CHANGED", entry=k)
                fail("new response differs from accepted source", "E-ACQUISITION-SOURCE-CHANGED")
            return
        target = self.root / f"chapters/{k}.txt"
        put(target, data)
        crash("derived")
        attempt_path = self.root / f"attempts/{k}/{attempt['ordinal']:06d}.json"
        record = {
            "format_version": FORMAT, "binding_sha256": object_digest(self.binding), "entry": k,
            "attempt": ref(attempt_path), "title": title, "body_sha256": digest(body.encode()),
            "derived": ref(target, data), "committed_at_ms": self.clock.now_ms(),
        }
        # Store portable relative paths.
        record["attempt"]["path"] = attempt_path.relative_to(self.root).as_posix()
        record["derived"]["path"] = target.relative_to(self.root).as_posix()
        put_json(self.root / f"accepted/{k}.json", record)
        accepted[k] = record
        crash("accepted")
        self._journal({"event": "COMMITTED", "entry": k, "at_ms": self.clock.now_ms()})

    def _journal(self, value: dict):
        path = child(self.root, "journal.jsonl")
        previous = read_bytes(path) if path.exists() else b""
        valid = []
        for line in previous.splitlines():
            try:
                json.loads(line)
            except (ValueError, UnicodeError):
                if previous:
                    put(self.root / f"journal-recovery/{digest(previous).split(':')[1]}.bin", previous)
                break
            valid.append(line)
        put(path, b"\n".join(valid + [canonical_dumps(value)]) + b"\n", mutable=True)

    def status(self) -> dict:
        accepted = self.accepted()
        latest = max((a["committed_at_ms"] for a in accepted.values()), default=None)
        state = read_json(self.root / "state.json") if (self.root / "state.json").exists() else {}
        result = {
            "format_version": FORMAT, "run_dir": str(self.root), "expected_entries": len(self.entries),
            "accepted_entries": len(accepted), "missing_entries": [k for k in self.entries if k not in accepted],
            "last_accepted_at": timestamp(latest) if latest is not None else None,
            "acquisition": state["reason"] if state.get("reason") == "SOURCE_CHANGED" else (
                "ENTRIES_ACQUIRED" if len(accepted) == len(self.entries) else state.get("reason", "PARTIAL")
            ),
            "retry_not_before_ms": state.get("retry_not_before_ms"),
            "eta_seconds": None,
            "coverage": "NOT_CHECKED", "native_freeze": "NOT_RUN", "research": "NOT_RUN",
        }
        if result["acquisition"] == "ENTRIES_ACQUIRED":
            result["eta_seconds"] = 0
        elif self.cfg["source"]["channel"] == "C1" and len(accepted) >= 5 and latest is not None and self.clock.now_ms() - latest <= 300_000:
            recent = sorted(accepted.values(), key=lambda a: a["committed_at_ms"])[-50:]
            started = min(read_json(child(self.root, a["attempt"]["path"]))["started_at_ms"] for a in recent)
            elapsed = latest - started
            if elapsed > 0:
                result["observed_throughput"] = {"accepted_entries": len(recent), "elapsed_ms": elapsed}
                if result["acquisition"] not in {"COOLDOWN", "NEEDS_ACCESS", "SOURCE_CHANGED", "MISSING"}:
                    result["eta_seconds"] = math.ceil(len(result["missing_entries"]) * elapsed / len(recent) / 1000)
        return result

    def _state(self, reason: str, **kwargs):
        put_json(self.root / "state.json", {"reason": reason, "at_ms": self.clock.now_ms(), **kwargs}, mutable=True)

    def _start(self, entry: dict, url: str, channel: str, **provenance) -> dict:
        directory = self.root / f"started/{entry['key']}"
        starts = list(directory.glob("*.json"))
        ordinal = len(starts) + 1
        for i, path in enumerate(sorted(starts), 1):
            old = read_json(path)
            self._bound(old)
            if path.name != f"{i:06d}.json" or old["entry"] != entry["key"]:
                fail("started attempt sequence is corrupt", "E-ACQUISITION-INTEGRITY")
        start = {
            "format_version": FORMAT, "binding_sha256": object_digest(self.binding),
            "entry": entry["key"], "ordinal": ordinal, "requested_url": url, "channel": channel,
            "started_at_ms": self.clock.now_ms(),
            **provenance,
        }
        put_json(directory / f"{ordinal:06d}.json", start)
        return start

    def _record(self, start: dict, response: Response, *, result: str, crash=lambda _: None) -> dict:
        raw_path = self.root / f"raw/{start['entry']}/{start['ordinal']:06d}.bin"
        put(raw_path, response.body)
        crash("raw")
        end = self.clock.now_ms()
        attempt = {
            **start, "finished_at_ms": end, "status": response.status,
            "content_type": response.content_type, "location": response.location,
            "retry_after": response.retry_after,
            "retry_not_before_ms": retry_after_ms(response.retry_after, end) if response.status == 429 else None,
            "raw": {"path": raw_path.relative_to(self.root).as_posix(), "sha256": digest(response.body)},
            "result": result, "error": response.error,
        }
        put_json(self.root / f"attempts/{start['entry']}/{start['ordinal']:06d}.json", attempt)
        crash("attempt")
        return attempt

    def import_local(self, directory: Path, *, crash=lambda _: None) -> dict:
        directory = no_symlinks(directory).resolve()
        with _exclusive_work_dir(self.root):
            if directory.is_file():
                return self._import_book(directory, crash)
            if not directory.is_dir():
                fail("local input must be a chapter directory, TXT or EPUB file")
            accepted = self.accepted()
            for entry in self.cat["entries"]:
                if entry["import_path"] is None:
                    continue
                source = child(directory, entry["import_path"])
                if not source.exists():
                    continue
                data = read_bytes(source, self.limits["max_response_bytes"])
                previous = self.attempts(entry["key"])
                reusable = next((a for a in previous if a["result"] == "FETCHED" and a["raw"]["sha256"] == digest(data)), None)
                if reusable is None:
                    start = self._start(entry, source.as_uri(), "LOCAL_DERIVED_IMPORT")
                    reusable = self._record(start, Response(None, data), result="FETCHED", crash=crash)
                self._commit(entry, reusable, crash, accepted=accepted)
            status = self.status()
            put_json(self.root / "status.json", status, mutable=True)
            return status

    def local_adapter(self, path: Path, kind: str):
        if kind not in {"txt", "epub"}:
            fail("single-file import supports native TXT/EPUB adapters")
        return adapter_from_spec({
            "kind": kind, "path": str(path), "title": self.cfg["work"]["title"],
            "author": self.cfg["work"]["author"], "language": self.cfg["work"]["language"],
            "_ingestion_max_bytes": self.limits["max_input_bytes"],
        })

    def _import_book(self, source: Path, crash) -> dict:
        kind = source.suffix.casefold().lstrip(".")
        adapter = self.local_adapter(source, kind)
        original = read_bytes(source, self.limits["max_input_bytes"])
        archive_path = self.root / f"imports/{digest(original).split(':')[1]}.{kind}"
        put(archive_path, original)
        # Always discover from the preserved copy so concurrent changes to the
        # operator's original file cannot change an import in flight.
        adapter = self.local_adapter(archive_path, kind)
        chapters = adapter.discover().chapters
        if len(chapters) != len(self.cat["entries"]):
            fail("native local-book chapter count differs from the fixed catalog", "E-ACQUISITION-ORDER")
        accepted = self.accepted()
        for entry, chapter in zip(self.cat["entries"], chapters):
            if entry["expected_title"] is not None and chapter.title != entry["expected_title"]:
                fail("native local-book chapter title differs from fixed catalog", "E-ACQUISITION-IDENTITY")
            raw, media_type, _, _ = adapter.fetch_chapter(chapter)
            if len(raw) > self.limits["max_response_bytes"]:
                fail("local chapter exceeds configured response limit", "E-ACQUISITION-SIZE")
            previous = self.attempts(entry["key"])
            reusable = next((a for a in previous if a["result"] == "FETCHED" and a["raw"]["sha256"] == digest(raw) and a.get("source_archive", {}).get("sha256") == digest(original)), None)
            if reusable is None:
                start = self._start(
                    entry, source.as_uri() + f"#chapter={chapter.ordinal}", "LOCAL_ORIGINAL_IMPORT",
                    source_archive={"path": archive_path.relative_to(self.root).as_posix(), "sha256": digest(original)},
                    archive_ordinal=chapter.ordinal,
                )
                reusable = self._record(start, Response(None, raw, content_type=media_type), result="FETCHED", crash=crash)
            self._commit(entry, reusable, crash, accepted=accepted)
        result = self.status()
        put_json(self.root / "status.json", result, mutable=True)
        return result

    def acquire(self, *, send=transport, crash=lambda _: None) -> dict:
        channel = self.cfg["source"]["channel"]
        if channel == "C2":
            fail("C2 requires an independently authorized host browser session; no automatic browser runtime is installed", "E-ACQUISITION-BROWSER", pending=True)
        if channel != "C1":
            fail("acquire is only available for a C1 source")
        with _exclusive_work_dir(self.root):
            return self._acquire(send=send, crash=crash)

    def _acquire(self, *, send, crash) -> dict:
        limits, clock = self.limits, self.clock
        accepted = self.accepted()
        all_attempts = [a for k in self.entries for a in self.attempts(k)]
        starts = self.starts()
        latest = max(all_attempts, key=lambda a: a["finished_at_ms"], default=None)
        persisted = read_json(self.root / "state.json") if (self.root / "state.json").exists() else {}
        permanent = {"NEEDS_ACCESS", "MISSING", "EXTRACTION_FAILED", "SOURCE_CHANGED", "ATTEMPTS_EXHAUSTED"}
        if persisted.get("reason") in permanent:
            return self.status()
        resume_at = max(
            persisted.get("retry_not_before_ms") or 0,
            max((a["retry_not_before_ms"] or 0 for a in all_attempts), default=0),
        )
        if clock.now_ms() < resume_at:
            self._state("COOLDOWN", retry_not_before_ms=resume_at)
            return self.status()
        # Do not replay an access denial after a crash before state.json.
        if latest and latest["result"] == "NEEDS_ACCESS":
            self._state("NEEDS_ACCESS")
            return self.status()
        deadline = clock.monotonic_ms() + limits["max_run_seconds"] * 1000
        last_commit = clock.monotonic_ms()
        terminal_keys = {(a["entry"], a["ordinal"]) for a in all_attempts}
        interrupted = [s for s in starts if (s["entry"], s["ordinal"]) not in terminal_keys]
        interrupted_end = max(
            (s["started_at_ms"] + limits["request_timeout_seconds"] * 1000 for s in interrupted),
            default=0,
        )
        last_end = max(latest["finished_at_ms"] if latest else 0, interrupted_end) or None
        last_end_monotonic = None
        request_count = len(starts)
        failures = 0
        for a in sorted(all_attempts, key=lambda a: a["finished_at_ms"], reverse=True):
            if a["result"] != "RETRYABLE":
                break
            failures += 1
        for entry in self.cat["entries"]:
            if entry["key"] in accepted:
                continue
            history = self.attempts(entry["key"])
            fetched = [a for a in history if a["result"] == "FETCHED"]
            if fetched:
                try:
                    self._commit(entry, fetched[-1], crash, accepted=accepted)
                except AcquisitionError as exc:
                    self._state("EXTRACTION_FAILED", error=str(exc))
                    return self.status()
                last_commit = clock.monotonic_ms()
                continue
            url = entry["url"]
            redirects = 0
            while True:
                used = len(list((self.root / f"started/{entry['key']}").glob("*.json")))
                if used >= limits["max_attempts_per_entry"]:
                    self._state("ATTEMPTS_EXHAUSTED", entry=entry["key"])
                    return self.status()
                gap = limits["slow_start_gap_seconds"] if request_count < limits["slow_start_requests"] else limits["min_gap_seconds"]
                backoff = min(30 * 2 ** min(failures - 1, 2), 120) if failures else 0
                wait_ms = max(0, (last_end or clock.now_ms()) + max(gap, backoff) * 1000 - clock.now_ms()) if last_end is not None else 0
                if last_end_monotonic is not None:
                    wait_ms = max(wait_ms, last_end_monotonic + max(gap, backoff) * 1000 - clock.monotonic_ms())
                remaining = min(
                    deadline - clock.monotonic_ms(),
                    last_commit + limits["no_commit_seconds"] * 1000 - clock.monotonic_ms(),
                )
                if wait_ms + limits["request_timeout_seconds"] * 1000 > remaining:
                    self._state("STALLED" if clock.monotonic_ms() + wait_ms >= last_commit + limits["no_commit_seconds"] * 1000 else "BUDGET_EXHAUSTED")
                    return self.status()
                if wait_ms:
                    clock.sleep_ms(wait_ms)
                scoped_url(url, self.cfg["source"]["scope_url"])
                start = self._start(entry, url, "C1")
                try:
                    response = send(url, limits["request_timeout_seconds"], limits["max_response_bytes"])
                except (OSError, TimeoutError) as exc:
                    response = Response(None, b"", error=type(exc).__name__)
                if len(response.body) > limits["max_response_bytes"]:
                    response = Response(response.status, response.body[:limits["max_response_bytes"]], error="response too large")
                if response.status in {401, 403} or any(m in response.body[:200_000].decode("utf-8", errors="ignore").casefold() for m in CHALLENGES):
                    result = "NEEDS_ACCESS"
                elif response.status == 429:
                    result = "COOLDOWN"
                elif response.status in {404, 410}:
                    result = "MISSING"
                elif response.error and response.error.startswith("E-SSRF") and response.error != "E-SSRF-DNS":
                    result = "EXTRACTION_FAILED"
                elif response.status is None or response.status in {500, 502, 503, 504}:
                    result = "RETRYABLE"
                elif response.error:
                    result = "EXTRACTION_FAILED"
                elif response.status in {301, 302, 303, 307, 308}:
                    result = "REDIRECT"
                elif response.status == 200 and response.content_type.split(";")[0].strip().casefold() in {"text/plain", "text/html", "application/xhtml+xml"}:
                    result = "FETCHED"
                else:
                    result = "EXTRACTION_FAILED"
                attempt = self._record(start, response, result=result, crash=crash)
                last_end, request_count = clock.now_ms(), request_count + 1
                last_end_monotonic = clock.monotonic_ms()
                if result == "REDIRECT":
                    redirects += 1
                    if not response.location or redirects > limits["max_redirects"]:
                        self._state("EXTRACTION_FAILED", error="invalid redirect chain")
                        return self.status()
                    url = urljoin(url, response.location)
                    scoped_url(url, self.cfg["source"]["scope_url"])
                    continue
                if result == "RETRYABLE":
                    failures += 1
                    if failures >= limits["consecutive_transport_failures"]:
                        self._state("COOLDOWN", retry_not_before_ms=clock.now_ms() + 900_000)
                        return self.status()
                    continue
                failures = 0
                if result != "FETCHED":
                    self._state(result, retry_not_before_ms=attempt["retry_not_before_ms"])
                    return self.status()
                try:
                    self._commit(entry, attempt, crash, accepted=accepted)
                except AcquisitionError as exc:
                    self._state("EXTRACTION_FAILED", error=str(exc))
                    return self.status()
                last_commit = clock.monotonic_ms()
                break
        self._state("ENTRIES_ACQUIRED")
        status = self.status()
        put_json(self.root / "status.json", status, mutable=True)
        return status


def safe_filename(ordinal: int, title: str, chapter_key: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", unicodedata.normalize("NFC", title))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    # Preserve original title in the manifest; bound filename bytes, not chars.
    while len(cleaned.encode("utf-8")) > 160:
        cleaned = cleaned[:-1]
    suffix = hashlib.sha256(chapter_key.encode()).hexdigest()[:10]
    return f"{ordinal:06d}__{cleaned or 'untitled'}__{suffix}.txt"


def sample_plan(cat: dict) -> dict:
    chapters = [c["key"] for c in cat["chapters"] if c["role"] == "MAIN"]
    if not chapters:
        chapters = [c["key"] for c in cat["chapters"]]
    selected = set(chapters if len(chapters) <= 13 else (chapters[0], chapters[len(chapters) // 2], chapters[-1]))
    catalog_hash = object_digest(cat)
    if len(chapters) > 13:
        for i in range(5):
            group = chapters[len(chapters) * i // 5:len(chapters) * (i + 1) // 5]
            candidates = sorted(group, key=lambda k: object_digest([catalog_hash, i, k]))
            selected.update([k for k in candidates if k not in selected][:2])
    return {
        "format_version": FORMAT, "catalog_sha256": catalog_hash,
        "method": "endpoints-plus-five-strata-v1",
        "chapter_keys": [k for k in chapters if k in selected],
        "lead_metadata_consumed": False,
    }


def chapter_view(run: Run) -> tuple[dict, dict[str, bytes]]:
    accepted = run.accepted()
    rows, payloads = [], {}
    for ordinal, ch in enumerate(run.cat["chapters"], 1):
        if any(k not in accepted for k in ch["entry_keys"]):
            continue
        title = ch["title"] or accepted[ch["entry_keys"][0]]["title"]
        pages, spans = [], []
        cursor = len(title) + 2
        for k in ch["entry_keys"]:
            data = checked_ref(accepted[k]["derived"], run.root, run.limits["max_response_bytes"] * 2)[1]
            body = data.decode("utf-8").partition("\n\n")[2].rstrip("\n")
            pages.append(body)
            spans.append({"entry": k, "body_start_char": cursor, "body_end_char": cursor + len(body)})
            cursor += len(body) + 2
        body = "\n\n".join(pages)
        data = Run.derived_bytes(title, body)
        name = safe_filename(ordinal, title, ch["key"])
        rows.append({
            "key": ch["key"], "ordinal": ordinal, "title": title, "role": ch["role"],
            "entry_keys": ch["entry_keys"], "page_spans": spans, "file_name": name,
            "sha256": digest(data), "byte_length": len(data), "body_sha256": digest(body.encode()),
        })
        payloads[ch["key"]] = data
    view = {
        "format_version": FORMAT, "binding_sha256": object_digest(run.binding),
        "accepted": [
            {"entry": k, "record_sha256": digest(read_bytes(run.root / f"accepted/{k}.json"))}
            for k in run.entries if k in accepted
        ],
        "chapters": rows,
    }
    return view, payloads


def anomaly_scan(view: dict, payloads: dict[str, bytes]) -> tuple[list[dict], list[list[str]]]:
    signals = []
    duplicates = defaultdict(list)
    paragraph_owners = defaultdict(set)
    def signal(code, chapter, details):
        item = {"code": code, "chapter": chapter, "details": details}
        signals.append({"signal_id": object_digest(item), **item})
    sizes = []
    for row in view["chapters"]:
        body = payloads[row["key"]].decode().partition("\n\n")[2].strip()
        sizes.append(len(body))
        normalized_body = "".join(unicodedata.normalize("NFC", body).split())
        duplicates[digest(normalized_body.encode())].append(row["key"])
        if len(body) < 200:
            signal("SHORT_CHAPTER", row["key"], {"chars": len(body)})
        for token in ("\ufffd", "**", "本章未完", "下一页继续", "请关闭广告", "验证码"):
            if token in body:
                signal("TEXT_ANOMALY", row["key"], {"token": token, "occurrences": body.count(token)})
        # Long repeated paragraphs flag near-duplicate chapters without inventing
        # an accuracy rate. This is a diagnostic, not a completeness proof.
        for p in body.splitlines():
            normalized = "".join(p.split())
            if len(normalized) >= 128:
                paragraph_owners[digest(normalized.encode())].add(row["key"])
    if sizes:
        median = sorted(sizes)[len(sizes) // 2]
        for row, size in zip(view["chapters"], sizes):
            if size > max(10_000, median * 5) or size * 5 < median:
                signal("LENGTH_OUTLIER", row["key"], {"chars": size, "median_chars": median})
    for paragraph_hash, owners in sorted(paragraph_owners.items()):
        if len(owners) > 1:
            for owner in sorted(owners):
                signal("REPEATED_LONG_PARAGRAPH", owner, {"paragraph_sha256": paragraph_hash, "chapters": sorted(owners)})
    return signals, [keys for keys in duplicates.values() if len(keys) > 1]


def review_template(run: Run) -> dict:
    view, payloads = chapter_view(run)
    plan = sample_plan(run.cat)
    anomalies, _ = anomaly_scan(view, payloads)
    blank = lambda: {"status": "UNRESOLVED", "reason": "Not reviewed.", "evidence": []}
    return {
        "format_version": FORMAT, "view_sha256": object_digest(view),
        "sample_plan_sha256": object_digest(plan),
        "reviewer": "UNASSIGNED", "reviewed_at": "NOT_REVIEWED",
        "samples": {k: blank() for k in plan["chapter_keys"]},
        "anomalies": {s["signal_id"]: blank() for s in anomalies},
        "limitations": "No independent fidelity review has been completed.",
    }


def validate_review(review: dict, run: Run, view: dict, anomalies: list[dict], base: Path):
    fields(review, {
        "format_version", "view_sha256", "sample_plan_sha256", "reviewer", "reviewed_at",
        "samples", "anomalies", "limitations",
    }, label="quality review")
    if review["format_version"] != FORMAT or review["view_sha256"] != object_digest(view) or review["sample_plan_sha256"] != object_digest(sample_plan(run.cat)):
        fail("quality review belongs to different source bytes or sample plan", "E-ACQUISITION-REVIEW-BIND")
    string(review["reviewer"], "reviewer")
    string(review["limitations"], "quality limitations")
    try:
        dt = datetime.fromisoformat(review["reviewed_at"].replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("timezone missing")
    except (AttributeError, ValueError):
        fail("quality review requires an actual timestamp")
    fields(review["samples"], set(sample_plan(run.cat)["chapter_keys"]), label="sample judgments")
    fields(review["anomalies"], {s["signal_id"] for s in anomalies}, label="anomaly judgments")
    for item in [*review["samples"].values(), *review["anomalies"].values()]:
        verdict(item, base)
    if review["reviewer"] == "UNASSIGNED":
        fail("quality review must name the host reviewer")


def verify(run: Run, review_path: Path | None = None, *, persist: bool = True) -> dict:
    """Recompute physical checks; semantic judgments stay explicit host claims."""
    view, payloads = chapter_view(run)
    plan = sample_plan(run.cat)
    anomalies, duplicates = anomaly_scan(view, payloads)
    missing = [k for k in run.entries if k not in {r["entry"] for r in view["accepted"]}]
    checks = {name: a["status"] for name, a in run.cat["assessments"].items()}
    checks["entry_coverage"] = "UNRESOLVED" if missing else "PASS"
    checks["duplicate_bodies"] = "FAIL" if duplicates else "PASS"
    checks["explicit_chapter_titles"] = "PASS" if all(c["title"] is not None for c in run.cat["chapters"]) else "UNRESOLVED"
    checks["logical_chapter_coverage"] = "PASS" if len(view["chapters"]) == len(run.cat["chapters"]) else "UNRESOLVED"
    changed_entries = [
        k for k in run.entries
        if len({(a["raw"]["sha256"], a.get("source_archive", {}).get("sha256")) for a in run.attempts(k) if a["result"] == "FETCHED"}) > 1
    ]
    checks["source_consistency"] = "FAIL" if changed_entries else "PASS"
    quality = "UNRESOLVED"
    review = None
    if review_path is not None:
        review = read_json(review_path)
        validate_review(review, run, view, anomalies, review_path.parent)
        judgments = [*review["samples"].values(), *review["anomalies"].values()]
        quality = "FAIL" if any(v["status"] == "FAIL" for v in judgments) else (
            "PASS" if all(v["status"] == "PASS" for v in judgments) else "UNRESOLVED"
        )
    checks["fidelity"] = quality
    overall = "FAIL" if "FAIL" in checks.values() else "PASS" if set(checks.values()) == {"PASS"} else "UNRESOLVED"
    result = {
        "format_version": FORMAT, "view_sha256": object_digest(view),
        "sample_plan_sha256": object_digest(plan), "checks": checks, "result": overall,
        "missing_entries": missing, "changed_entries": changed_entries, "duplicate_bodies": duplicates, "anomalies": anomalies,
        "sample_denominator": len(plan["chapter_keys"]),
        "quality_review_sha256": object_digest(review) if review is not None else None,
        "quality_claim": "HOST_REVIEWED" if quality == "PASS" else "NOT_ESTABLISHED",
        "limits": "Structural replay and sampled host review do not prove semantic accuracy or whole-work completeness independently.",
    }
    if persist:
        put_json(run.root / "quality/sample-plan.json", plan)
        put_json(run.root / f"quality/verification-history/{object_digest(result).split(':')[1]}.json", result)
        put_json(run.root / "coverage-report.json", result, mutable=True)
    return result


def comparison_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def edit_distance(a: str, b: str, *, max_cells: int = 20_000_000) -> int | None:
    if a == b:
        return 0
    prefix = 0
    for x, y in zip(a, b):
        if x != y:
            break
        prefix += 1
    a, b = a[prefix:], b[prefix:]
    suffix = 0
    for x, y in zip(reversed(a), reversed(b)):
        if x != y:
            break
        suffix += 1
    if suffix:
        a, b = a[:-suffix], b[:-suffix]
    if len(a) * len(b) > max_cells:
        return None
    if len(b) > len(a):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def compare_sources(left: Run, right: Run, alignment_path: Path | None = None) -> dict:
    lv, lp = chapter_view(left)
    rv, rp = chapter_view(right)
    if left.cfg["work"] != right.cfg["work"]:
        fail("source comparison requires the same work identity")
    titles = defaultdict(list)
    for row in rv["chapters"]:
        titles[comparison_text(row["title"])].append(row["key"])
    proposals = [
        {"left": row["key"], "right_candidates": titles[comparison_text(row["title"])]}
        for row in lv["chapters"]
    ]
    rows, used_left, used_right = [], set(), set()
    if alignment_path:
        alignment = read_json(alignment_path)
        fields(alignment, {"format_version", "left_view_sha256", "right_view_sha256", "groups"}, label="alignment")
        if alignment["format_version"] != FORMAT or alignment["left_view_sha256"] != object_digest(lv) or alignment["right_view_sha256"] != object_digest(rv):
            fail("alignment source hashes differ", "E-ACQUISITION-REVIEW-BIND")
        if not isinstance(alignment["groups"], list):
            fail("alignment groups must be a list")
        left_order = {r["key"]: i for i, r in enumerate(lv["chapters"])}
        right_order = {r["key"]: i for i, r in enumerate(rv["chapters"])}
        for group in alignment["groups"]:
            fields(group, {"left", "right", "assessment"}, label="alignment group")
            verdict(group["assessment"], alignment_path.parent)
            if group["assessment"]["status"] != "PASS":
                continue
            for side, payloads, used, order in ((group["left"], lp, used_left, left_order), (group["right"], rp, used_right, right_order)):
                if not isinstance(side, list) or not side or any(k not in payloads for k in side):
                    fail("alignment contains unknown or unavailable chapters")
                if len(set(side)) != len(side) or used.intersection(side) or sorted(side, key=order.get) != side:
                    fail("alignment overlaps or changes chapter order")
                used.update(side)
            a = comparison_text("\n\n".join(lp[k].decode().partition("\n\n")[2] for k in group["left"]))
            b = comparison_text("\n\n".join(rp[k].decode().partition("\n\n")[2] for k in group["right"]))
            distance = edit_distance(a, b)
            rows.append({
                "left": group["left"], "right": group["right"], "left_chars": len(a), "right_chars": len(b),
                "edit_distance": distance, "difference_rate": None if distance is None else {"numerator": distance, "denominator": max(len(a), len(b), 1)},
                "status": "COMPUTED" if distance is not None else "COMPARISON_BUDGET_EXCEEDED",
            })
    return {
        "format_version": FORMAT, "left_view_sha256": object_digest(lv), "right_view_sha256": object_digest(rv),
        "metric": "NFC-whitespace-Levenshtein-difference-v1", "is_error_rate": False,
        "title_proposals": proposals, "aligned_groups": rows,
        "unaligned_left": [k for k in lp if k not in used_left],
        "unaligned_right": [k for k in rp if k not in used_right],
        "limitations": "Matching mirrors may share a damaged source; alignment and fidelity require independent review.",
    }


def copy_review(review: dict, base: Path, destination: Path) -> dict:
    copied = json.loads(json.dumps(review))
    for item in [*copied["samples"].values(), *copied["anomalies"].values()]:
        for i, reference in enumerate(item["evidence"]):
            _, data = checked_ref(reference, base)
            name = digest(data).split(":")[1] + ".bin"
            put(destination / "evidence" / name, data)
            item["evidence"][i] = {"path": "evidence/" + name, "sha256": digest(data)}
    return copied


def tree_manifest(root: Path, *, omit: set[str] = frozenset(), limit: int = 500_000_000) -> list[dict]:
    files = []
    for p in sorted(root.rglob("*")):
        no_symlinks(p)
        if p.is_file() and p.relative_to(root).as_posix() not in omit:
            data = read_bytes(p, limit)
            files.append({"path": p.relative_to(root).as_posix(), "sha256": digest(data), "byte_length": len(data)})
    return files


def seal(run: Run, output: Path, review_path: Path) -> Path:
    output = no_symlinks(output).resolve()
    if output == run.root or run.root in output.parents:
        fail("sealed output must be outside the acquisition run")
    with _exclusive_work_dir(run.root):
        report = verify(run, review_path)
        if report["result"] != "PASS":
            fail("coverage or fidelity has unresolved/failed checks", "E-ACQUISITION-NOT-READY", pending=True)
        view, payloads = chapter_view(run)
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".source-staging-", dir=output) as temporary:
            staging = Path(temporary)
            for row in view["chapters"]:
                put(staging / "chapters" / row["file_name"], payloads[row["key"]])
            # Snapshot only normal files. The host run and original full text
            # remain untouched; no hard links to active acquisition files.
            for item in tree_manifest(run.root):
                if item["path"] == ".novel-ingest.lock":
                    continue
                data = read_bytes(child(run.root, item["path"]), run.limits["max_input_bytes"])
                put(child(staging / "provenance/run", item["path"]), data)
            copied = copy_review(read_json(review_path), review_path.parent, staging / "provenance/quality")
            copied_review_path = staging / "provenance/quality/review.json"
            put_json(copied_review_path, copied)
            # Recompute against the frozen copy, with no dependence on the
            # original review/evidence or mutable status files.
            frozen_run = Run(staging / "provenance/run")
            frozen_report = verify(frozen_run, copied_review_path, persist=False)
            if frozen_report["result"] != "PASS" or frozen_report["view_sha256"] != report["view_sha256"]:
                fail("source changed during seal", "E-ACQUISITION-INTEGRITY")
            native = DirectoryNovelAdapter({"path": str(staging / "chapters"), "title": run.cfg["work"]["title"]}).discover()
            if [c.title for c in native.chapters] != [Path(row["file_name"]).stem for row in view["chapters"]]:
                fail("native adapter reading order differs", "E-ACQUISITION-ORDER")
            put_json(staging / "coverage-report.json", frozen_report)
            put_json(staging / "chapter-view.json", view)
            manifest = {
                "format_version": FORMAT, "work": run.cfg["work"], "source": run.cfg["source"],
                "view_sha256": object_digest(view),
                "sealed_at": timestamp(run.clock.now_ms()),
                "files": tree_manifest(staging),
            }
            data = canonical_dumps(manifest) + b"\n"
            name = digest(data).split(":")[1]
            put(staging / "source-manifest.json", data)
            target = output / name
            if target.exists():
                validate_sealed(target)
            else:
                os.rename(staging, target)
                sync_directory(output)
            validate_sealed(target)
            return target


def validate_sealed(path: Path) -> tuple[dict, Run]:
    path = no_symlinks(path).resolve()
    data = read_bytes(path / "source-manifest.json")
    manifest = read_json(path / "source-manifest.json")
    fields(manifest, {"format_version", "work", "source", "view_sha256", "sealed_at", "files"}, label="source manifest")
    if manifest["format_version"] != FORMAT or path.name != digest(data).split(":")[1]:
        fail("sealed source identity mismatch", "E-ACQUISITION-INTEGRITY")
    observed = tree_manifest(path, omit={"source-manifest.json"})
    if observed != manifest["files"]:
        fail("sealed files differ from manifest", "E-ACQUISITION-INTEGRITY")
    run = Run(path / "provenance/run")
    if manifest["work"] != run.cfg["work"] or manifest["source"] != run.cfg["source"]:
        fail("sealed source identity differs from acquisition", "E-ACQUISITION-INTEGRITY")
    report = verify(run, path / "provenance/quality/review.json", persist=False)
    if report["result"] != "PASS" or report != read_json(path / "coverage-report.json"):
        fail("sealed quality/coverage cannot be replayed", "E-ACQUISITION-INTEGRITY")
    view, payloads = chapter_view(run)
    if view != read_json(path / "chapter-view.json") or object_digest(view) != manifest["view_sha256"]:
        fail("sealed chapter manifest differs", "E-ACQUISITION-INTEGRITY")
    for row in view["chapters"]:
        if read_bytes(path / "chapters" / row["file_name"]) != payloads[row["key"]]:
            fail("sealed chapter differs from replayed derivation", "E-ACQUISITION-INTEGRITY")
    return manifest, run


def source_declaration(run: Run, sealed: Path, declared_at: str) -> dict:
    """Project the same reviewed local source into either native builder."""
    work = run.cfg["work"]
    return {
        "work": {
            "canonical_title": work["title"], "author": work["author"], "language": work["language"],
            "aliases": [], "external_ids": [],
        },
        "source": {"kind": "directory", "path": str(sealed / "chapters")},
        "source_quality": {
            "edition_status": run.cfg["source"]["edition_status"], "textual_completeness": "COMPLETE",
        },
        "edition_label": run.cfg["source"]["edition_label"] + "; host source manifest " + sealed.name,
        "declared_at": declared_at,
    }


def prepare_source(sealed: Path, planning_input: Path, phase0_root: Path) -> dict:
    from xhnovel_pipeline.phase0_builder import prepare_handoff_from_input, resolve_validated_handoff_input
    from xhnovel_pipeline.phase0_handoff import validate_exploration_brief
    from xhnovel_pipeline.phase0_planning import validate_planning_handoff

    sealed = no_symlinks(sealed).resolve()
    manifest, run = validate_sealed(sealed)
    phase0_root = no_symlinks(phase0_root).resolve()
    if sealed == phase0_root or sealed in phase0_root.parents:
        fail("Phase 0 output must be outside the sealed source")
    options = read_json(planning_input)
    fields(options, {"format_version", "brief", "planning"}, {"leads"}, label="planning input")
    if options["format_version"] != FORMAT:
        fail("unsupported planning input version")
    brief = validate_exploration_brief(json.loads(checked_ref(options["brief"], planning_input.parent)[1]))
    # A selected, sealed work can enter FULL_WORK research without a web scene hypothesis.
    leads = (json.loads(checked_ref(options["leads"], planning_input.parent)[1])
             if "leads" in options else [])
    if not isinstance(leads, list):
        fail("planning input Lead list must be an array")
    planning = options["planning"]
    planning_root, receipt_path = None, None
    if planning is not None:
        fields(planning, {"root", "receipt"}, label="planning lineage")
        planning_root = Path(string(planning["root"], "planning root"))
        if not planning_root.is_absolute():
            planning_root = planning_input.parent / planning_root
        planning_root = no_symlinks(planning_root).resolve()
        receipt_path, _ = checked_ref(planning["receipt"], planning_input.parent)
    now = timestamp(run.clock.now_ms())
    payload = {
        "brief": brief, "leads": leads,
        "source_declaration": source_declaration(run, sealed, now),
        "requested_at": now,
    }
    phase0_root.mkdir(parents=True, exist_ok=True)
    att_bytes = read_bytes(run.root / "operator-attestation.json")
    put(phase0_root / "operator-attestation.json", att_bytes)
    input_path = phase0_root / "preparation-input.json"
    if input_path.exists():
        old = read_json(input_path)
        # Resume the same declaration timestamps; never overwrite a conflicting
        # source/brief/Lead set under an existing preparation path.
        payload["requested_at"] = old.get("requested_at")
        payload["source_declaration"]["declared_at"] = old.get("source_declaration", {}).get("declared_at")
        if payload != old:
            fail("existing preparation has different frozen inputs", "E-ACQUISITION-PREPARE-BIND")
    put_json(input_path, payload)
    prepared = prepare_handoff_from_input(input_path, phase0_root)
    resolve_validated_handoff_input(prepared.handoff_path, phase0_root=phase0_root)
    planning_validation = None
    if planning is not None:
        planning_validation = validate_planning_handoff(
            receipt_path, prepared.handoff_path,
            planning_root=planning_root, phase0_root=phase0_root, repo_root=ROOT,
        )
    result = {
        "format_version": FORMAT, "status": "READY_FOR_XHNOVEL",
        "sealed_source": str(sealed),
        "sealed_manifest_sha256": digest(read_bytes(sealed / "source-manifest.json")),
        "planning_input_sha256": digest(read_bytes(planning_input)),
        "handoff_path": str(prepared.handoff_path), "novel_spec_path": str(prepared.novel_spec_path),
        "expected_input_spec_hash": prepared.handoff["novel_spec"]["expected_input_spec_hash"],
        "planning_validation": planning_validation,
        "native_freeze": "NOT_RUN", "research": "NOT_RUN",
    }
    put_json(phase0_root / "acquisition-prepared.json", result)
    return result


def prepare_generic_source(sealed: Path, planning_input: Path, research_root: Path) -> dict:
    """Bridge a sealed host source to the existing observation Handoff."""
    from xhnovel_pipeline.generic_handoff import prepare_generic_handoff_from_input, resolve_generic_handoff

    sealed = no_symlinks(sealed).resolve()
    _, run = validate_sealed(sealed)
    research_root = no_symlinks(research_root).resolve()
    if research_root == sealed or sealed in research_root.parents:
        fail("observation outputs must be outside the sealed source")
    options = read_json(planning_input)
    fields(options, {
        "format_version", "definition_artifact_id", "resolution_artifact_id",
        "work_lead_artifact_ids", "requested_at",
    }, label="generic planning input")
    if options["format_version"] != FORMAT:
        fail("unsupported planning input version")
    # A caller-frozen time makes preparation idempotent. All semantic records
    # and their CAS lineage are checked by the native builder, never recreated.
    payload = {k: v for k, v in options.items() if k != "format_version"}
    payload["source_declaration"] = source_declaration(run, sealed, options["requested_at"])
    put(research_root / "operator-attestation.json", read_bytes(run.root / "operator-attestation.json"))
    prepared = prepare_generic_handoff_from_input(payload, research_root, root=ROOT)
    resolved = resolve_generic_handoff(Path(prepared["handoff_path"]), research_root, root=ROOT)
    result = {
        "format_version": FORMAT, "status": "READY_FOR_XHNOVEL", "protocol": "GENERIC",
        "sealed_source": str(sealed),
        "sealed_manifest_sha256": digest(read_bytes(sealed / "source-manifest.json")),
        "planning_input_sha256": digest(read_bytes(planning_input)),
        "handoff_path": prepared["handoff_path"], "novel_spec_path": prepared["novel_spec_path"],
        "handoff_artifact_id": prepared["handoff_artifact_id"],
        "expected_input_spec_hash": resolved.handoff["novel_spec"]["expected_input_spec_hash"],
        "native_freeze": "NOT_RUN", "research": "NOT_RUN",
    }
    put_json(research_root / "source-acquisition" / (resolved.handoff["handoff_id"] + ".json"), result)
    return result


def freeze_source(sealed: Path, handoff_path: Path, research_root: Path, *, phase0_root: Path | None = None) -> dict:
    from xhnovel_pipeline.phase0_builder import resolve_validated_handoff_input

    sealed = no_symlinks(sealed).resolve()
    _, run = validate_sealed(sealed)
    handoff_path = no_symlinks(handoff_path).resolve()
    research_root = no_symlinks(research_root).resolve()
    if research_root == sealed or sealed in research_root.parents:
        fail("native outputs must be outside the sealed source")
    phase0_root = phase0_root or handoff_path.parents[2]
    prepared = read_json(phase0_root / "acquisition-prepared.json")
    if prepared.get("sealed_manifest_sha256") != digest(read_bytes(sealed / "source-manifest.json")) or prepared.get("handoff_path") != str(handoff_path):
        fail("prepared Handoff is not bound to this sealed source", "E-ACQUISITION-PREPARE-BIND")
    resolved = resolve_validated_handoff_input(handoff_path, phase0_root=phase0_root)
    return freeze_native_source(sealed, run, resolved, prepared, research_root)


def freeze_generic_source(sealed: Path, handoff_path: Path, research_root: Path, work_dir: Path) -> dict:
    from xhnovel_pipeline.generic_handoff import resolve_generic_handoff

    sealed = no_symlinks(sealed).resolve()
    _, run = validate_sealed(sealed)
    handoff_path = no_symlinks(handoff_path).resolve()
    research_root = no_symlinks(research_root).resolve()
    work_dir = no_symlinks(work_dir).resolve()
    if work_dir == sealed or sealed in work_dir.parents:
        fail("native outputs must be outside the sealed source")
    resolved = resolve_generic_handoff(handoff_path, research_root, root=ROOT)
    prepared = read_json(research_root / "source-acquisition" / (resolved.handoff["handoff_id"] + ".json"))
    if prepared.get("sealed_manifest_sha256") != digest(read_bytes(sealed / "source-manifest.json")) or prepared.get("handoff_path") != str(handoff_path):
        fail("prepared Handoff is not bound to this sealed source", "E-ACQUISITION-PREPARE-BIND")
    return freeze_native_source(sealed, run, resolved, prepared, work_dir)


def freeze_native_source(sealed: Path, run: Run, resolved, prepared: dict, research_root: Path) -> dict:
    """Share native ingestion and exact source replay across both Handoff kinds."""
    from xhnovel_pipeline.novel_ingest import run_novel_ingestion
    from xhnovel_pipeline.phase0_handoff import attestation_rights

    spec = resolved.execution_spec
    expected_source = {
        "kind": "directory", "path": str(sealed / "chapters"),
        "title": run.cfg["work"]["title"], "author": run.cfg["work"]["author"],
        "language": run.cfg["work"]["language"],
    }
    if spec["source"] != expected_source or spec["source_quality"] != {
        "edition_status": run.cfg["source"]["edition_status"], "textual_completeness": "COMPLETE",
    }:
        fail("native spec source differs from sealed source", "E-ACQUISITION-PREPARE-BIND")
    att = validate_operator_attestation(read_json(run.root / "operator-attestation.json"))
    if spec["rights"] != attestation_rights(att):
        fail("native rights differ from standing attestation", "E-ACQUISITION-RIGHTS")
    result = run_novel_ingestion(
        spec, research_root / "ingestion", repo_root=ROOT, now=timestamp(run.clock.now_ms()),
    )
    ingestion = result["ingestion"]
    if ingestion["input_spec_hash"] != resolved.handoff["novel_spec"]["expected_input_spec_hash"]:
        fail("native whole-spec hash differs", "E-ACQUISITION-PREPARE-BIND")
    view, payloads = chapter_view(run)
    actual = sorted(result["chapters"], key=lambda c: c["ordinal"])
    if len(actual) != len(view["chapters"]):
        fail("native chapter set differs from sealed source", "E-ACQUISITION-INTEGRITY")
    for chapter, row in zip(actual, view["chapters"]):
        expected_data = payloads[row["key"]]
        if (
            chapter["ordinal"] != row["ordinal"]
            or chapter["title"] != Path(row["file_name"]).stem
            or chapter["artifact_id"] != artifact_id_for(expected_data)
            or result["store"].get(chapter["artifact_id"]) != expected_data
        ):
            fail("native CAS source bytes differ from sealed chapters", "E-ACQUISITION-INTEGRITY")
    order = ingestion["order_validation"]
    if ingestion["status"] == "FAILED" or any(order[k] for k in (
        "out_of_order_chapter_ids", "missing_declared_numbers", "duplicate_chapter_ids",
    )):
        fail("native ingestion reports hard coverage/order problems", "E-ACQUISITION-ORDER")
    # A PARTIAL run with unknown-number-only warnings is kept PARTIAL. It is
    # never relabelled SUCCEEDED or confused with a Handoff execution receipt.
    output = {
        "format_version": FORMAT, "status": "NATIVE_SOURCE_FROZEN",
        "sealed_manifest_sha256": prepared["sealed_manifest_sha256"],
        "handoff_id": resolved.handoff["handoff_id"],
        "expected_input_spec_hash": resolved.handoff["novel_spec"]["expected_input_spec_hash"],
        "ingestion_run_id": ingestion["ingestion_run_id"], "ingestion_status": ingestion["status"],
        "order_validation": order, "unknown_number_explanation": (
            run.cat["assessments"]["chapter_relationships"] if ingestion["status"] == "PARTIAL" else None
        ),
        "catalog_path": str(result["work_dir"] / "catalog.json"),
        "store_path": str(research_root / "ingestion/objects"),
        "research": "NOT_RUN", "semantic_assurance": "UNVERIFIED",
    }
    put_json(research_root / "source-freeze-receipt.json", output)
    return output


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    for name in ("inspect", "acquire", "import-local"):
        cmd = sub.add_parser(name)
        cmd.add_argument("config", type=Path)
        if name == "import-local":
            cmd.add_argument("input", type=Path)
    cmd = sub.add_parser("status")
    cmd.add_argument("run", type=Path)
    for name in ("verify", "review-template", "seal"):
        cmd = sub.add_parser(name)
        cmd.add_argument("run", type=Path)
        if name in {"verify", "seal"}:
            cmd.add_argument("--review", type=Path, required=name == "seal")
        if name in {"review-template", "seal"}:
            cmd.add_argument("--output", type=Path, required=True)
    cmd = sub.add_parser("compare")
    cmd.add_argument("left", type=Path)
    cmd.add_argument("right", type=Path)
    cmd.add_argument("--alignment", type=Path)
    cmd.add_argument("--output", type=Path, required=True)
    cmd = sub.add_parser("prepare")
    cmd.add_argument("sealed", type=Path)
    cmd.add_argument("planning_input", type=Path)
    cmd.add_argument("--phase0-root", type=Path, required=True)
    cmd = sub.add_parser("freeze")
    cmd.add_argument("sealed", type=Path)
    cmd.add_argument("handoff", type=Path)
    cmd.add_argument("--research-root", type=Path, required=True)
    cmd.add_argument("--phase0-root", type=Path)
    cmd = sub.add_parser("prepare-generic")
    cmd.add_argument("sealed", type=Path)
    cmd.add_argument("planning_input", type=Path)
    cmd.add_argument("--research-root", type=Path, required=True)
    cmd = sub.add_parser("freeze-generic")
    cmd.add_argument("sealed", type=Path)
    cmd.add_argument("handoff", type=Path)
    cmd.add_argument("--research-root", type=Path, required=True)
    cmd.add_argument("--work-dir", type=Path, required=True)
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "inspect":
            cfg = read_json(args.config)
            cat, _, _ = validate_config(cfg, args.config.parent)
            result = {"status": "CONFIG_VALID", "expected_entries": len(cat["entries"]), "network_requested": False}
        elif args.command == "status":
            result = Run(args.run).status()
        elif args.command == "verify":
            run = Run(args.run)
            with _exclusive_work_dir(run.root):
                result = verify(run, args.review)
        elif args.command == "review-template":
            run = Run(args.run)
            with _exclusive_work_dir(run.root):
                result = review_template(run)
                put_json(args.output, result)
        elif args.command == "compare":
            result = compare_sources(Run(args.left), Run(args.right), args.alignment)
            put_json(args.output, result)
        elif args.command == "seal":
            result = {"status": "LOCAL_SOURCE_SEALED", "path": str(seal(Run(args.run), args.output, args.review))}
        elif args.command == "prepare":
            result = prepare_source(args.sealed, args.planning_input, args.phase0_root)
        elif args.command == "freeze":
            result = freeze_source(args.sealed, args.handoff, args.research_root, phase0_root=args.phase0_root)
        elif args.command == "prepare-generic":
            result = prepare_generic_source(args.sealed, args.planning_input, args.research_root)
        elif args.command == "freeze-generic":
            result = freeze_generic_source(args.sealed, args.handoff, args.research_root, args.work_dir)
        else:
            run = Run.initialize(args.config)
            result = run.import_local(args.input) if args.command == "import-local" else run.acquire()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command in {"acquire", "import-local"} and result["missing_entries"]:
            return 4
        if args.command == "verify" and result["result"] != "PASS":
            return 4
        return 0
    except (PipelineError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return getattr(exc, "exit_code", 2)


if __name__ == "__main__":
    raise SystemExit(main())
