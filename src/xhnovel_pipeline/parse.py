from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from html.parser import HTMLParser
from typing import Any

from .hashing import digest_prefix, object_hash, sha256_bytes
from .constants import PARSER_BUILD_ID, SCHEMA_VERSION

SKIP_TAGS = {"script", "style", "nav", "noscript", "svg", "header", "footer", "aside"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._capture_title = False
        self._skip = 0
        self.blocks: list[str] = []
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP_TAGS:
            self._skip += 1
            return
        if tag == "title":
            self._capture_title = True
        if tag in {"p", "h1", "h2", "h3", "li", "div", "article"}:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
            return
        if tag == "title":
            self._capture_title = False
        if tag in {"p", "h1", "h2", "h3", "li", "article"}:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._capture_title:
            self.title += data
            return
        self._buf.append(data)

    def _flush(self) -> None:
        text = normalize_text("".join(self._buf))
        self._buf = []
        if text:
            self.blocks.append(text)


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def text_hash(text: str) -> str:
    return digest_prefix(sha256_bytes(text.encode("utf-8")))


def parse_html(artifact_id: str, html: bytes, *, document_id: str) -> dict[str, Any]:
    extractor = _TextExtractor()
    extractor.feed(html.decode("utf-8", errors="replace"))
    extractor._flush()
    title = normalize_text(extractor.title)
    segments = []
    for i, block in enumerate(extractor.blocks):
        segments.append(
            {
                "schema_version": SCHEMA_VERSION,
                "segment_id": f"SEG-{document_id[4:]}-{i:03d}",
                "document_id": document_id,
                "parent_segment_id": None,
                "ordinal": i,
                "segment_type": "paragraph",
                "normalized_text": block,
                "normalized_text_hash": text_hash(block),
                "source_locator": {"kind": "html", "selector": f"p:nth-of-type({i+1})", "start": 0, "end": len(block)},
            }
        )
    document = {
        "schema_version": SCHEMA_VERSION,
        "document_id": document_id,
        "input_artifact_id": artifact_id,
        "parser_build_id": PARSER_BUILD_ID,
        "title": title,
        "language": "zh",
        "structure_hash": "sha256:" + "0" * 64,
    }
    document["structure_hash"] = object_hash(document, omit=("structure_hash",))
    return {"document": document, "segments": segments}


def parse_pdf(artifact_id: str, data: bytes, *, document_id: str) -> dict[str, Any]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    blocks: list[tuple[int, str]] = []
    for i, page in enumerate(reader.pages, start=1):
        raw = page.extract_text() or ""
        for para in re.split(r"\n\s*\n", raw):
            text = normalize_text(para)
            if text:
                blocks.append((i, text))
    segments = []
    for i, (page, text) in enumerate(blocks):
        segments.append(
            {
                "schema_version": SCHEMA_VERSION,
                "segment_id": f"SEG-{document_id[4:]}-{i:03d}",
                "document_id": document_id,
                "parent_segment_id": None,
                "ordinal": i,
                "segment_type": "paragraph",
                "normalized_text": text,
                "normalized_text_hash": text_hash(text),
                "source_locator": {"kind": "pdf", "page": page, "start": 0, "end": len(text)},
            }
        )
    document = {
        "schema_version": SCHEMA_VERSION,
        "document_id": document_id,
        "input_artifact_id": artifact_id,
        "parser_build_id": PARSER_BUILD_ID,
        "title": segments[0]["normalized_text"][:80] if segments else "",
        "language": "zh",
        "structure_hash": "sha256:" + "0" * 64,
    }
    document["structure_hash"] = object_hash(document, omit=("structure_hash",))
    return {"document": document, "segments": segments}


def parse_artifact(artifact_id: str, data: bytes, media_type: str, document_id: str) -> dict[str, Any]:
    if media_type == "application/pdf" or data.startswith(b"%PDF"):
        return parse_pdf(artifact_id, data, document_id=document_id)
    return parse_html(artifact_id, data, document_id=document_id)


def make_minimal_pdf(text: str) -> bytes:
    """Tiny uncompressed PDF containing `text` as a single page."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1", errors="replace")
    objects = []
    objects.append(b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj")
    objects.append(b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj")
    objects.append(
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj"
    )
    objects.append(b"4 0 obj << /Length " + str(len(stream)).encode() + b" >> stream\n" + stream + b"\nendstream endobj")
    objects.append(b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj")
    body = b"\n".join(objects)
    # xref is optional for many parsers; pypdf accepts this simple file if we wrap it.
    header = b"%PDF-1.1\n"
    eof = b"\nstartxref\n0\n%%EOF\n"
    return header + body + eof
