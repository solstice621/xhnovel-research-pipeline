from __future__ import annotations

import html
import pathlib
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Callable, Protocol
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

from .canonical import canonical_dumps
from .errors import ValidationError
from .hashing import artifact_id_for, object_hash
from .http_fetch import MAX_BYTES as HTTP_FETCH_MAX_BYTES
from .http_fetch import HttpFetcher
from .parse import decode_html, decode_text, normalize_text
from .urls import canonicalize_url

DEFAULT_CHAPTER_PATTERN = (
    r"(?m)^[^\S\r\n]*(第\s*[0-9零〇一二两三四五六七八九十百千万]+"
    r"\s*[章节回][^\r\n]*)\r?$"
)
SUPPORTED_DIRECTORY_SUFFIXES = {".txt", ".html", ".htm", ".xhtml"}
MAX_EPUB_MEMBER_BYTES = 10_000_000
MAX_EPUB_TOTAL_BYTES = 200_000_000
MAX_SITE_INDEX_BYTES = 50_000_000
MAX_SITE_LINK_CHARS = 8_192
_ADAPTER_BUILD_SOURCE_NAMES = (
    "novel_adapters.py",
    "novel_ingest.py",
    "parse.py",
    "http_fetch.py",
    "urls.py",
    "ssrf.py",
    "user_agent.py",
)


def _adapter_build_id(adapter_name: str) -> str:
    source_root = pathlib.Path(__file__).resolve().parent
    try:
        sources = [
            {
                "path": name,
                "artifact_id": artifact_id_for((source_root / name).read_bytes()),
            }
            for name in _ADAPTER_BUILD_SOURCE_NAMES
        ]
    except OSError as exc:
        raise ValidationError(
            "E-ADAPTER-BUILD",
            "cannot fingerprint novel adapter implementation closure",
        ) from exc
    return f"{adapter_name}+{object_hash({'sources': sources}, omit=())}"


def _ingestion_byte_limit(spec: dict[str, Any]) -> int | None:
    value = spec.get("_ingestion_max_bytes")
    if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
        raise ValidationError("E-NOVEL-LIMIT", "_ingestion_max_bytes must be a non-negative integer")
    return value


def _integer_limit(
    spec: dict[str, Any],
    field: str,
    default: int,
    *,
    minimum: int = 1,
    maximum: int | None = None,
) -> int:
    value = spec.get(field, default)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        raise ValidationError("E-NOVEL-LIMIT", f"{field} must be an integer in the allowed range")
    return value


def _boolean_option(spec: dict[str, Any], field: str, default: bool = False) -> bool:
    value = spec.get(field, default)
    if not isinstance(value, bool):
        raise ValidationError("E-NOVEL-SPEC", f"{field} must be a boolean")
    return value


def _source_path(spec: dict[str, Any]) -> pathlib.Path:
    value = spec.get("path")
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("E-NOVEL-SPEC", "local novel source requires a non-empty path")
    try:
        return pathlib.Path(value).expanduser().resolve()
    except (OSError, TypeError, ValueError) as exc:
        raise ValidationError("E-NOVEL-SPEC", "local novel source path is invalid") from exc


def _effective_chapter_limit(spec: dict[str, Any], default: int) -> int:
    source_limit = _integer_limit(spec, "max_chapters", default, maximum=100_000)
    ingestion_limit = spec.get("_ingestion_max_chapters")
    if ingestion_limit is None:
        return source_limit
    if (
        not isinstance(ingestion_limit, int)
        or isinstance(ingestion_limit, bool)
        or not 1 <= ingestion_limit <= 100_000
    ):
        raise ValidationError(
            "E-NOVEL-LIMIT",
            "_ingestion_max_chapters must be an integer in the allowed range",
        )
    return min(source_limit, ingestion_limit)


def _reject_oversized_payload(spec: dict[str, Any], byte_length: int, label: str) -> None:
    limit = _ingestion_byte_limit(spec)
    if limit is not None and byte_length > limit:
        raise ValidationError("E-NOVEL-LIMIT", f"{label} exceeds {limit} bytes")


def _read_bounded_local_source(
    path: pathlib.Path,
    spec: dict[str, Any],
    label: str,
) -> bytes:
    limit = _ingestion_byte_limit(spec)
    if limit is None:
        return path.read_bytes()
    _reject_oversized_payload(spec, path.stat().st_size, label)
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValidationError("E-NOVEL-LIMIT", f"{label} exceeds {limit} bytes")
    return data


@dataclass(frozen=True)
class WorkMetadata:
    title: str
    author: str | None
    language: str
    source_kind: str
    source_locator: str


@dataclass(frozen=True)
class ProvenanceBlob:
    locator: str
    media_type: str
    data: bytes


@dataclass(frozen=True)
class ChapterRef:
    chapter_key: str
    ordinal: int
    title: str
    source_locator: str
    media_type: str
    declared_number: int | None = None
    chapter_kind: str = "UNKNOWN"
    adapter_data: dict[str, Any] = field(default_factory=dict)
    derived_from_provenance: bool = False

    def to_checkpoint(self) -> dict[str, Any]:
        return {
            "chapter_key": self.chapter_key,
            "ordinal": self.ordinal,
            "title": self.title,
            "source_locator": self.source_locator,
            "media_type": self.media_type,
            "declared_number": self.declared_number,
            "chapter_kind": self.chapter_kind,
            "adapter_data": self.adapter_data,
            "derived_from_provenance": self.derived_from_provenance,
        }

    @classmethod
    def from_checkpoint(cls, value: dict[str, Any]) -> ChapterRef:
        return cls(
            chapter_key=value["chapter_key"],
            ordinal=int(value["ordinal"]),
            title=value["title"],
            source_locator=value["source_locator"],
            media_type=value["media_type"],
            declared_number=value.get("declared_number"),
            chapter_kind=value.get("chapter_kind", "UNKNOWN"),
            adapter_data=dict(value.get("adapter_data") or {}),
            derived_from_provenance=bool(value.get("derived_from_provenance", False)),
        )


@dataclass(frozen=True)
class NovelDiscovery:
    work: WorkMetadata
    chapters: list[ChapterRef]
    provenance: list[ProvenanceBlob]


class NovelAdapter(Protocol):
    adapter_id: str

    def discover(self) -> NovelDiscovery: ...

    def fetch_chapter(self, chapter: ChapterRef) -> tuple[bytes, str, str, int | None]: ...


def _natural_key(value: str) -> list[int | str]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", value)]


def _chinese_integer(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
    if (
        not value
        or value.startswith("万")
        or any(char not in digits and char not in units for char in value)
    ):
        return None
    total = 0
    section = 0
    number = 0
    for char in value:
        if char in digits:
            number = digits[char]
            continue
        unit = units[char]
        if unit == 10000:
            section = (section + number) * unit
            total += section
            section = 0
            number = 0
        else:
            section += (number or 1) * unit
            number = 0
    return total + section + number


def chapter_number(title: str) -> int | None:
    match = re.search(r"第\s*([0-9零〇一二两三四五六七八九十百千万]+)\s*[章节回]", title)
    return _chinese_integer(match.group(1)) if match else None


def classify_chapter_kind(
    title: str,
    declared_number: int | None,
    *,
    properties: set[str] | None = None,
) -> str:
    normalized = normalize_text(title).casefold()
    properties = properties or set()
    if "nav" in properties or re.search(r"(?:^|[-_])(nav|toc)(?:$|[-_])", normalized):
        return "NAVIGATION"
    if declared_number is not None:
        return "MAIN"
    if re.search(r"(?:序章|楔子|引子|prologue)", normalized, flags=re.IGNORECASE):
        return "PROLOGUE"
    if re.search(r"(?:尾声|epilogue)", normalized, flags=re.IGNORECASE):
        return "EPILOGUE"
    if re.search(r"(?:番外|后记|後記|附录|附錄|extra|appendix)", normalized, flags=re.IGNORECASE):
        return "EXTRA"
    if re.search(
        r"(?:封面|版权|版權|目录|目錄|书名页|書名頁|title.?page|copyright|cover)",
        normalized,
        flags=re.IGNORECASE,
    ):
        return "FRONTMATTER"
    return "UNKNOWN"


def _directory_chapter_number(title: str) -> int | None:
    declared = chapter_number(title)
    if declared is not None:
        return declared
    match = re.fullmatch(r"(?:chapter[\s._-]*)?(\d+)", title, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _media_type(path: pathlib.Path) -> str:
    if path.suffix.casefold() == ".txt":
        return "text/plain"
    return "text/html"


def _chapter_key(ordinal: int, title: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z\u3400-\u9fff]+", "-", title).strip("-")[:48]
    return f"chapter-{ordinal:06d}-{safe or 'untitled'}"


class TextNovelAdapter:
    adapter_name = "novel-text-v1"

    @property
    def adapter_id(self) -> str:
        return _adapter_build_id(self.adapter_name)

    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.path = _source_path(spec)
        self.max_chapters = _effective_chapter_limit(spec, 100_000)
        self._payloads: dict[str, bytes] = {}

    def discover(self) -> NovelDiscovery:
        if not self.path.is_file():
            raise ValidationError("E-NOVEL-SOURCE", f"missing text file {self.path}")
        raw = _read_bounded_local_source(self.path, self.spec, "text source")
        text, _ = decode_text(raw, str(self.spec.get("encoding", "auto")))
        pattern_text = str(self.spec.get("chapter_pattern") or DEFAULT_CHAPTER_PATTERN)
        if len(pattern_text) > 500:
            raise ValidationError("E-NOVEL-SPEC", "chapter_pattern is too long")
        try:
            pattern = re.compile(pattern_text)
        except re.error as exc:
            raise ValidationError("E-NOVEL-SPEC", f"invalid chapter_pattern: {exc}") from exc
        matches = []
        for match in pattern.finditer(text):
            matches.append(match)
            if len(matches) > self.max_chapters:
                raise ValidationError(
                    "E-NOVEL-LIMIT",
                    f"chapter count exceeds {self.max_chapters}",
                )
        sections: list[tuple[str, str, str]] = []
        if matches:
            preface = text[: matches[0].start()].strip()
            if preface:
                sections.append(("前置内容", preface, "FRONTMATTER"))
            for index, match in enumerate(matches):
                title = normalize_text(match.group(1) if match.lastindex else match.group(0))
                end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
                body = text[match.end():end].strip()
                declared = chapter_number(title)
                sections.append(
                    (title, f"{title}\n\n{body}".strip(), classify_chapter_kind(title, declared))
                )
        else:
            title = str(self.spec.get("title") or self.path.stem)
            sections.append((title, text.strip(), classify_chapter_kind(title, chapter_number(title))))
        if len(sections) > self.max_chapters:
            raise ValidationError("E-NOVEL-LIMIT", f"chapter count exceeds {self.max_chapters}")
        chapters = []
        for ordinal, (title, body, chapter_kind) in enumerate(sections, start=1):
            key = _chapter_key(ordinal, title)
            self._payloads[key] = body.encode("utf-8")
            chapters.append(
                ChapterRef(
                    chapter_key=key,
                    ordinal=ordinal,
                    title=title,
                    source_locator=f"{self.path.as_uri()}#chapter={ordinal}",
                    media_type="text/plain",
                    declared_number=chapter_number(title),
                    chapter_kind=chapter_kind,
                    adapter_data={"ordinal": ordinal},
                    derived_from_provenance=True,
                )
            )
        return NovelDiscovery(
            work=WorkMetadata(
                title=str(self.spec.get("title") or self.path.stem),
                author=self.spec.get("author"),
                language=str(self.spec.get("language", "zh")),
                source_kind="TXT",
                source_locator=self.path.as_uri(),
            ),
            chapters=chapters,
            provenance=[ProvenanceBlob(self.path.as_uri(), "text/plain", raw)],
        )

    def fetch_chapter(self, chapter: ChapterRef) -> tuple[bytes, str, str, int | None]:
        try:
            return self._payloads[chapter.chapter_key], "text/plain", chapter.source_locator, None
        except KeyError as exc:
            # A resumed run reconstructs refs without calling discover. Rediscover locally.
            self.discover()
            try:
                return self._payloads[chapter.chapter_key], "text/plain", chapter.source_locator, None
            except KeyError:
                raise ValidationError("E-NOVEL-CHAPTER", f"missing {chapter.chapter_key}") from exc


class DirectoryNovelAdapter:
    adapter_name = "novel-directory-v1"

    @property
    def adapter_id(self) -> str:
        return _adapter_build_id(self.adapter_name)

    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.path = _source_path(spec)
        self.recursive = _boolean_option(spec, "recursive")
        self.max_chapters = _effective_chapter_limit(spec, 100_000)

    def discover(self) -> NovelDiscovery:
        if not self.path.is_dir():
            raise ValidationError("E-NOVEL-SOURCE", f"missing chapter directory {self.path}")
        paths = self.path.rglob("*") if self.recursive else self.path.glob("*")
        files = []
        for path in paths:
            if path.is_file() and path.suffix.casefold() in SUPPORTED_DIRECTORY_SUFFIXES:
                files.append(path)
                if len(files) > self.max_chapters:
                    raise ValidationError(
                        "E-NOVEL-LIMIT",
                        f"chapter count exceeds {self.max_chapters}",
                    )
        files.sort(key=lambda path: _natural_key(str(path.relative_to(self.path))))
        if not files:
            raise ValidationError("E-NOVEL-SOURCE", f"no supported chapters in {self.path}")
        chapters = []
        manifest_entries: list[dict[str, Any]] = []
        total_bytes = 0
        for ordinal, path in enumerate(files, start=1):
            resolved_path = path.resolve()
            try:
                relative_path = resolved_path.relative_to(self.path).as_posix()
            except ValueError as exc:
                raise ValidationError(
                    "E-NOVEL-SCOPE",
                    f"chapter path leaves configured directory: {resolved_path}",
                ) from exc
            data = _read_bounded_local_source(resolved_path, self.spec, "chapter file")
            total_bytes += len(data)
            _reject_oversized_payload(self.spec, total_bytes, "chapter directory")
            media_type = _media_type(resolved_path)
            expected_artifact_id = artifact_id_for(data)
            manifest_entries.append(
                {
                    "artifact_id": expected_artifact_id,
                    "byte_length": len(data),
                    "media_type": media_type,
                    "relative_path": relative_path,
                }
            )
            title = normalize_text(path.stem)
            chapters.append(
                ChapterRef(
                    chapter_key=_chapter_key(ordinal, relative_path),
                    ordinal=ordinal,
                    title=title,
                    source_locator=resolved_path.as_uri(),
                    media_type=media_type,
                    declared_number=_directory_chapter_number(title),
                    chapter_kind=classify_chapter_kind(
                        title, _directory_chapter_number(title)
                    ),
                    adapter_data={
                        "path": str(resolved_path),
                        "expected_artifact_id": expected_artifact_id,
                        "expected_byte_length": len(data),
                    },
                )
            )
        manifest = canonical_dumps(
            {
                "files": manifest_entries,
                "format": "xhnovel-directory-manifest-v1",
            }
        )
        return NovelDiscovery(
            work=WorkMetadata(
                title=str(self.spec.get("title") or self.path.name),
                author=self.spec.get("author"),
                language=str(self.spec.get("language", "zh")),
                source_kind="DIRECTORY",
                source_locator=self.path.as_uri(),
            ),
            chapters=chapters,
            provenance=[
                ProvenanceBlob(
                    f"{self.path.as_uri()}#xhnovel-directory-manifest-v1",
                    "application/vnd.xhnovel.directory-manifest+json",
                    manifest,
                )
            ],
        )

    def fetch_chapter(self, chapter: ChapterRef) -> tuple[bytes, str, str, int | None]:
        path = pathlib.Path(chapter.adapter_data["path"]).resolve()
        try:
            path.relative_to(self.path)
        except ValueError as exc:
            raise ValidationError("E-NOVEL-SCOPE", f"chapter path leaves configured directory: {path}") from exc
        if path.suffix.casefold() not in SUPPORTED_DIRECTORY_SUFFIXES:
            raise ValidationError("E-NOVEL-SCOPE", f"unsupported chapter path: {path}")
        if not path.is_file():
            raise ValidationError("E-NOVEL-CHAPTER", f"missing chapter file {path}")
        data = _read_bounded_local_source(path, self.spec, "chapter file")
        expected_artifact_id = chapter.adapter_data.get("expected_artifact_id")
        expected_byte_length = chapter.adapter_data.get("expected_byte_length")
        if (
            not isinstance(expected_artifact_id, str)
            or not isinstance(expected_byte_length, int)
            or isinstance(expected_byte_length, bool)
            or len(data) != expected_byte_length
            or artifact_id_for(data) != expected_artifact_id
        ):
            raise ValidationError(
                "E-NOVEL-SOURCE-CHANGED",
                f"chapter file changed after discovery: {path}",
            )
        return (
            data,
            chapter.media_type,
            path.as_uri(),
            None,
        )


def _safe_epub_member(name: str) -> str:
    normalized = pathlib.PurePosixPath(name)
    if normalized.is_absolute() or ".." in normalized.parts:
        raise ValidationError("E-EPUB-PATH", f"unsafe EPUB member {name!r}")
    return normalized.as_posix()


def _xml_text(root: ElementTree.Element, local_name: str) -> str | None:
    node = root.find(f".//{{*}}{local_name}")
    return normalize_text(node.text or "") if node is not None and node.text else None


class EpubNovelAdapter:
    adapter_name = "novel-epub-v1"

    @property
    def adapter_id(self) -> str:
        return _adapter_build_id(self.adapter_name)

    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.path = _source_path(spec)
        self.max_chapters = _effective_chapter_limit(spec, 100_000)
        self.max_member_bytes = _integer_limit(
            spec,
            "max_member_bytes",
            MAX_EPUB_MEMBER_BYTES,
        )
        self.max_total_bytes = _integer_limit(
            spec,
            "max_total_bytes",
            MAX_EPUB_TOTAL_BYTES,
        )
        self._member_by_key: dict[str, str] = {}

    def _open(self) -> zipfile.ZipFile:
        if not self.path.is_file():
            raise ValidationError("E-NOVEL-SOURCE", f"missing EPUB {self.path}")
        _reject_oversized_payload(self.spec, self.path.stat().st_size, "EPUB container")
        try:
            archive = zipfile.ZipFile(self.path)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ValidationError("E-EPUB", f"invalid EPUB {self.path}") from exc
        ingestion_limit = _ingestion_byte_limit(self.spec)
        member_limit = self.max_member_bytes
        total_limit = self.max_total_bytes
        if ingestion_limit is not None:
            member_limit = min(member_limit, ingestion_limit)
            total_limit = min(total_limit, ingestion_limit)
        total = 0
        for info in archive.infolist():
            _safe_epub_member(info.filename)
            if info.file_size > member_limit:
                archive.close()
                raise ValidationError("E-NOVEL-LIMIT", f"EPUB member too large: {info.filename}")
            total += info.file_size
        if total > total_limit:
            archive.close()
            raise ValidationError("E-NOVEL-LIMIT", "EPUB uncompressed content exceeds limit")
        return archive

    def discover(self) -> NovelDiscovery:
        with self._open() as archive:
            raw_epub = _read_bounded_local_source(self.path, self.spec, "EPUB container")
            try:
                container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
                rootfile = container.find(".//{*}rootfile")
                if rootfile is None or not rootfile.attrib.get("full-path"):
                    raise ValueError("missing rootfile")
                opf_name = _safe_epub_member(rootfile.attrib["full-path"])
                opf = ElementTree.fromstring(archive.read(opf_name))
            except (KeyError, ElementTree.ParseError, ValueError) as exc:
                raise ValidationError("E-EPUB", "EPUB container/package metadata is invalid") from exc
            opf_dir = pathlib.PurePosixPath(opf_name).parent
            manifest = {
                item.attrib.get("id", ""): item
                for item in opf.findall(".//{*}manifest/{*}item")
                if item.attrib.get("id")
            }
            spine = [item.attrib.get("idref", "") for item in opf.findall(".//{*}spine/{*}itemref")]
            chapters: list[ChapterRef] = []
            for item_id in spine:
                item = manifest.get(item_id)
                if item is None:
                    raise ValidationError("E-EPUB", f"spine references missing item {item_id!r}")
                media = item.attrib.get("media-type", "")
                if media not in {"application/xhtml+xml", "text/html"}:
                    continue
                if len(chapters) >= self.max_chapters:
                    raise ValidationError(
                        "E-NOVEL-LIMIT",
                        f"chapter count exceeds {self.max_chapters}",
                    )
                href = item.attrib.get("href")
                if not href:
                    raise ValidationError("E-EPUB", f"manifest item {item_id!r} has no href")
                member = _safe_epub_member((opf_dir / href.split("#", 1)[0]).as_posix())
                try:
                    page = archive.read(member)
                except KeyError as exc:
                    raise ValidationError("E-EPUB", f"missing spine member {member!r}") from exc
                title = _extract_html_title(page) or pathlib.PurePosixPath(member).stem
                declared_number = chapter_number(title)
                item_properties = set(item.attrib.get("properties", "").split())
                ordinal = len(chapters) + 1
                key = _chapter_key(ordinal, item_id or title)
                self._member_by_key[key] = member
                chapters.append(
                    ChapterRef(
                        chapter_key=key,
                        ordinal=ordinal,
                        title=title,
                        source_locator=f"epub:{self.path.as_uri()}!/{member}",
                        media_type="application/xhtml+xml",
                        declared_number=declared_number,
                        chapter_kind=classify_chapter_kind(
                            title,
                            declared_number,
                            properties=item_properties,
                        ),
                        adapter_data={
                            "member": member,
                            "expected_artifact_id": artifact_id_for(page),
                            "expected_byte_length": len(page),
                        },
                        derived_from_provenance=True,
                    )
                )
            if not chapters:
                raise ValidationError("E-EPUB", "EPUB spine contains no HTML chapters")
            title = str(self.spec.get("title") or _xml_text(opf, "title") or self.path.stem)
            author = self.spec.get("author") or _xml_text(opf, "creator")
            language = str(self.spec.get("language") or _xml_text(opf, "language") or "zh")
        return NovelDiscovery(
            work=WorkMetadata(title, author, language, "EPUB", self.path.as_uri()),
            chapters=chapters,
            provenance=[ProvenanceBlob(self.path.as_uri(), "application/epub+zip", raw_epub)],
        )

    def fetch_chapter(self, chapter: ChapterRef) -> tuple[bytes, str, str, int | None]:
        member = chapter.adapter_data.get("member")
        if not isinstance(member, str):
            raise ValidationError("E-EPUB", f"missing member for {chapter.chapter_key}")
        with self._open() as archive:
            try:
                data = archive.read(_safe_epub_member(member))
                expected_artifact_id = chapter.adapter_data.get("expected_artifact_id")
                expected_byte_length = chapter.adapter_data.get("expected_byte_length")
                if (
                    not isinstance(expected_artifact_id, str)
                    or not isinstance(expected_byte_length, int)
                    or isinstance(expected_byte_length, bool)
                    or len(data) != expected_byte_length
                    or artifact_id_for(data) != expected_artifact_id
                ):
                    raise ValidationError(
                        "E-NOVEL-SOURCE-CHANGED",
                        f"EPUB chapter changed after discovery: {member}",
                    )
                return (
                    data,
                    chapter.media_type,
                    chapter.source_locator,
                    None,
                )
            except KeyError as exc:
                raise ValidationError("E-EPUB", f"missing chapter member {member!r}") from exc


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str, set[str]]] = []
        self._href: str | None = None
        self._rels: set[str] = set()
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        values = {key.casefold(): value or "" for key, value in attrs}
        self._href = values.get("href")
        self._rels = set(values.get("rel", "").casefold().split())
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a" and self._href is not None:
            self.links.append((self._href, normalize_text("".join(self._text)), self._rels))
            self._href = None
            self._rels = set()
            self._text = []


def _extract_html_title(data: bytes) -> str:
    text = decode_html(data)
    for pattern in (
        r"<h1\b[^>]*>(.*?)</h1>",
        r"<title\b[^>]*>(.*?)</title>",
        r"<h2\b[^>]*>(.*?)</h2>",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return normalize_text(re.sub(r"<[^>]+>", " ", html.unescape(match.group(1))))
    return ""


def _origin_tuple(url: str) -> tuple[str, str, int | None]:
    try:
        parsed = urlparse(url)
        return parsed.scheme.casefold(), (parsed.hostname or "").casefold(), parsed.port
    except ValueError as exc:
        raise ValidationError("E-NOVEL-SCOPE", f"invalid site URL {url!r}") from exc


def _canonical_site_url(url: str, *, spec_field: bool = False) -> str:
    code = "E-NOVEL-SPEC" if spec_field else "E-NOVEL-SCOPE"
    try:
        canonical = canonicalize_url(url)
        parsed = urlparse(canonical)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("site URL must be absolute HTTP(S)")
        parsed.port
        return canonical
    except (TypeError, ValueError) as exc:
        raise ValidationError(code, f"invalid site URL {url!r}") from exc


def _compile_site_url_pattern(value: Any, field: str) -> re.Pattern[str]:
    if not isinstance(value, str) or not value or len(value) > 500:
        raise ValidationError("E-NOVEL-SPEC", f"{field} must be a non-empty bounded string")
    # URL matching intentionally accepts a small regular-expression subset.
    # Disallow grouping, lookarounds and backreferences so nested-repeat ReDoS
    # shapes cannot enter the crawler's per-link hot path.
    if "(?" in value or re.search(r"(?<!\\)[()]|\\[1-9]", value):
        raise ValidationError("E-NOVEL-SPEC", f"unsafe {field}")
    try:
        return re.compile(value)
    except re.error as exc:
        raise ValidationError("E-NOVEL-SPEC", f"invalid {field}: {exc}") from exc


def _page_link_url(base_url: str, href: str) -> str | None:
    href = href.strip()
    if not href or len(href) > MAX_SITE_LINK_CHARS or href.startswith("#"):
        return None
    try:
        joined = urljoin(base_url, href)
        if urlparse(joined).scheme.casefold() not in {"http", "https"}:
            return None
        return _canonical_site_url(joined)
    except (ValidationError, ValueError):
        return None


class StaticNovelSiteAdapter:
    adapter_name = "novel-static-site-v1"

    @property
    def adapter_id(self) -> str:
        return _adapter_build_id(self.adapter_name)

    def __init__(
        self,
        spec: dict[str, Any],
        fetcher: HttpFetcher | Any | None = None,
        attempt_recorder: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.spec = spec
        ingestion_byte_limit = _ingestion_byte_limit(spec)
        default_fetch_limit = HTTP_FETCH_MAX_BYTES
        if ingestion_byte_limit is not None:
            default_fetch_limit = min(default_fetch_limit, ingestion_byte_limit)
        self.fetcher = fetcher or HttpFetcher(max_bytes=default_fetch_limit)
        self.attempt_recorder = attempt_recorder
        index_url = spec.get("index_url")
        if not isinstance(index_url, str) or not index_url.strip():
            raise ValidationError("E-NOVEL-SPEC", "site adapter requires a non-empty index_url")
        self.index_url = _canonical_site_url(index_url, spec_field=True)
        self.chapter_pattern = _compile_site_url_pattern(
            spec.get("chapter_url_pattern"), "chapter_url_pattern"
        )
        next_pattern = spec.get("next_index_url_pattern")
        if next_pattern is not None and not isinstance(next_pattern, str):
            raise ValidationError("E-NOVEL-SPEC", "next_index_url_pattern must be a string")
        self.next_pattern = (
            _compile_site_url_pattern(next_pattern, "next_index_url_pattern")
            if next_pattern
            else None
        )
        self.allow_external_chapters = _boolean_option(spec, "allow_external_chapters")
        self.max_index_pages = _integer_limit(
            spec,
            "max_index_pages",
            20,
            maximum=1_000,
        )
        configured_index_bytes = _integer_limit(
            spec,
            "max_index_bytes",
            MAX_SITE_INDEX_BYTES,
            maximum=MAX_SITE_INDEX_BYTES,
        )
        self.max_index_bytes = (
            min(configured_index_bytes, ingestion_byte_limit)
            if ingestion_byte_limit is not None
            else configured_index_bytes
        )
        self.max_chapters = _effective_chapter_limit(spec, 10_000)
        self._refs: dict[str, str] = {}

    def _record_attempt(
        self,
        *,
        stage: str,
        requested_url: str,
        final_url: str,
        http_status: int | None,
        content_type: str,
        raw: bytes | None,
        status: str,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        if self.attempt_recorder is not None:
            self.attempt_recorder(
                {
                    "stage": stage,
                    "requested_url": requested_url,
                    "final_url": final_url,
                    "http_status": http_status,
                    "content_type": content_type,
                    "raw_response_bytes": raw,
                    "status": status,
                    "error_code": error_code,
                    "error_message": error_message,
                }
            )

    def _fetch_site_response(self, requested_url: str, *, stage: str) -> tuple[bytes, str, int, str]:
        try:
            raw, media, status, final_url = self.fetcher.fetch(requested_url)
        except (ValidationError, OSError, TimeoutError) as exc:
            error = exc if isinstance(exc, ValidationError) else ValidationError(
                "E-UNREACHABLE", f"request failed for {requested_url}: {exc}"
            )
            self._record_attempt(
                stage=stage,
                requested_url=str(getattr(error, "requested_url", requested_url)),
                final_url=str(getattr(error, "final_url", requested_url)),
                http_status=getattr(error, "http_status", None),
                content_type=str(getattr(error, "content_type", "")),
                raw=getattr(error, "raw_response_bytes", None),
                status="FAILED",
                error_code=error.code,
                error_message=str(error),
            )
            error.site_attempt_recorded = True
            if error is exc:
                raise
            raise error from exc
        if status != 200:
            code = "E-RETRYABLE" if status in {429, 500, 502, 503, 504} else "E-NOVEL-HTTP"
            error = ValidationError(code, f"{stage.casefold()} returned HTTP {status}: {final_url}")
            self._record_attempt(
                stage=stage,
                requested_url=requested_url,
                final_url=final_url,
                http_status=status,
                content_type=media,
                raw=raw,
                status="FAILED",
                error_code=error.code,
                error_message=str(error),
            )
            error.site_attempt_recorded = True
            raise error
        return raw, media, status, final_url

    def _allowed_chapter_url(self, url: str) -> bool:
        return self.allow_external_chapters or _origin_tuple(url) == _origin_tuple(
            self.index_url
        )

    def _allowed_index_url(self, url: str) -> bool:
        return _origin_tuple(url) == _origin_tuple(self.index_url)

    def discover(self) -> NovelDiscovery:
        queue = [self.index_url]
        visited: set[str] = set()
        chapter_links: list[tuple[str, str]] = []
        seen_chapters: set[str] = set()
        provenance: list[ProvenanceBlob] = []
        index_bytes = 0
        while queue and len(visited) < self.max_index_pages:
            current = _canonical_site_url(queue.pop(0))
            if current in visited:
                continue
            if not self._allowed_index_url(current):
                raise ValidationError("E-NOVEL-SCOPE", f"index traversal left allowed origin: {current}")
            raw: bytes | None = None
            media = ""
            status: int | None = None
            final_url = current
            try:
                raw, media, status, final_url = self._fetch_site_response(current, stage="INDEX")
                final_url = _canonical_site_url(final_url)
                if not self._allowed_index_url(final_url):
                    raise ValidationError("E-NOVEL-SCOPE", f"index redirect left allowed origin: {final_url}")
                _reject_oversized_payload(self.spec, len(raw), "site index response")
                index_bytes += len(raw)
                if index_bytes > self.max_index_bytes:
                    raise ValidationError(
                        "E-NOVEL-LIMIT",
                        f"site index provenance exceeds {self.max_index_bytes} bytes",
                    )
                if not raw.strip():
                    raise ValidationError("E-NOVEL-EMPTY", f"index returned an empty body: {final_url}")
                if "html" not in media.casefold():
                    raise ValidationError("E-NOVEL-MIME", f"index is not HTML: {media}")
                parser = _LinkParser()
                parser.feed(decode_html(raw))
                for href, label, rels in parser.links:
                    candidate = _page_link_url(final_url, href)
                    if candidate is None:
                        continue
                    if self.chapter_pattern.search(candidate):
                        if not self._allowed_chapter_url(candidate):
                            raise ValidationError(
                                "E-NOVEL-SCOPE", f"chapter link left allowed origin: {candidate}"
                            )
                        if candidate not in seen_chapters:
                            chapter_links.append((candidate, label))
                            seen_chapters.add(candidate)
                        continue
                    is_next = "next" in rels or bool(self.next_pattern and self.next_pattern.search(candidate))
                    if is_next and candidate not in visited and candidate not in queue:
                        if not self._allowed_index_url(candidate):
                            raise ValidationError(
                                "E-NOVEL-SCOPE",
                                f"index pagination left allowed origin: {candidate}",
                            )
                        queue.append(candidate)
                if len(chapter_links) > self.max_chapters:
                    raise ValidationError(
                        "E-NOVEL-LIMIT",
                        f"chapter count exceeds {self.max_chapters}",
                    )
            except ValidationError as exc:
                if not getattr(exc, "site_attempt_recorded", False):
                    self._record_attempt(
                        stage="INDEX",
                        requested_url=current,
                        final_url=final_url,
                        http_status=status,
                        content_type=media,
                        raw=raw,
                        status="FAILED",
                        error_code=exc.code,
                        error_message=str(exc),
                    )
                    exc.site_attempt_recorded = True
                raise
            self._record_attempt(
                stage="INDEX",
                requested_url=current,
                final_url=final_url,
                http_status=status,
                content_type=media,
                raw=raw,
                status="FETCHED",
                error_code=None,
                error_message=None,
            )
            visited.add(current)
            provenance.append(ProvenanceBlob(final_url, media.split(";", 1)[0], raw))
        if queue:
            raise ValidationError(
                "E-NOVEL-LIMIT",
                f"index traversal exceeds {self.max_index_pages} pages",
            )
        if not chapter_links:
            raise ValidationError("E-NOVEL-SOURCE", "site index yielded no chapter links")
        chapters = []
        for ordinal, (url, label) in enumerate(chapter_links, start=1):
            title = label or f"Chapter {ordinal}"
            key = _chapter_key(ordinal, url)
            self._refs[key] = url
            chapters.append(
                ChapterRef(
                    chapter_key=key,
                    ordinal=ordinal,
                    title=title,
                    source_locator=url,
                    media_type="text/html",
                    declared_number=chapter_number(title),
                    chapter_kind=classify_chapter_kind(title, chapter_number(title)),
                    adapter_data={"url": url},
                )
            )
        return NovelDiscovery(
            work=WorkMetadata(
                title=str(self.spec.get("title") or "Untitled web novel"),
                author=self.spec.get("author"),
                language=str(self.spec.get("language", "zh")),
                source_kind="SITE",
                source_locator=self.index_url,
            ),
            chapters=chapters,
            provenance=provenance,
        )

    def fetch_chapter(self, chapter: ChapterRef) -> tuple[bytes, str, str, int | None]:
        url = str(chapter.adapter_data.get("url") or chapter.source_locator)
        if not self._allowed_chapter_url(url) or not self.chapter_pattern.search(url):
            raise ValidationError("E-NOVEL-SCOPE", f"chapter URL is outside configured scope: {url}")
        raw: bytes | None = None
        media = ""
        status: int | None = None
        final_url = url
        try:
            raw, media, status, final_url = self._fetch_site_response(url, stage="CHAPTER")
            final_url = _canonical_site_url(final_url)
            if not self._allowed_chapter_url(final_url) or not self.chapter_pattern.search(final_url):
                raise ValidationError("E-NOVEL-SCOPE", f"chapter redirect left allowed origin: {final_url}")
            _reject_oversized_payload(self.spec, len(raw), "site chapter response")
            if not raw.strip():
                raise ValidationError("E-NOVEL-EMPTY", f"chapter returned an empty body: {final_url}")
            normalized_media = media.split(";", 1)[0].casefold()
            if normalized_media not in {"text/html", "application/xhtml+xml", "text/plain"}:
                raise ValidationError("E-NOVEL-MIME", f"unsupported chapter content type {media}")
            if normalized_media in {"text/html", "application/xhtml+xml"}:
                visible = re.sub(
                    r"<(?:script|style|noscript|svg)\b[^>]*>.*?</(?:script|style|noscript|svg)\s*>",
                    " ",
                    decode_html(raw),
                    flags=re.IGNORECASE | re.DOTALL,
                )
                visible = normalize_text(html.unescape(re.sub(r"<[^>]+>", " ", visible)))
                if not visible:
                    raise ValidationError("E-NOVEL-EMPTY", f"chapter contains no visible text: {final_url}")
        except ValidationError as exc:
            if not getattr(exc, "site_attempt_recorded", False):
                self._record_attempt(
                    stage="CHAPTER",
                    requested_url=url,
                    final_url=final_url,
                    http_status=status,
                    content_type=media,
                    raw=raw,
                    status="FAILED",
                    error_code=exc.code,
                    error_message=str(exc),
                )
                exc.site_attempt_recorded = True
            raise
        self._record_attempt(
            stage="CHAPTER",
            requested_url=url,
            final_url=final_url,
            http_status=status,
            content_type=media,
            raw=raw,
            status="FETCHED",
            error_code=None,
            error_message=None,
        )
        return raw, normalized_media, final_url, status


def adapter_from_spec(
    spec: dict[str, Any],
    *,
    fetcher: Any | None = None,
    attempt_recorder: Callable[[dict[str, Any]], None] | None = None,
) -> NovelAdapter:
    kind_value = spec.get("kind")
    if not isinstance(kind_value, str) or not kind_value:
        raise ValidationError("E-NOVEL-SPEC", "novel adapter kind must be a non-empty string")
    kind = kind_value.casefold()
    if kind == "txt":
        return TextNovelAdapter(spec)
    if kind == "epub":
        return EpubNovelAdapter(spec)
    if kind in {"directory", "chapter-directory"}:
        return DirectoryNovelAdapter(spec)
    if kind in {"site", "static-site"}:
        return StaticNovelSiteAdapter(spec, fetcher=fetcher, attempt_recorder=attempt_recorder)
    raise ValidationError("E-NOVEL-SPEC", f"unsupported novel adapter kind {kind!r}")
