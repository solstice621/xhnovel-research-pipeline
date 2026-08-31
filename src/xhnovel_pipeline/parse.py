from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from html.parser import HTMLParser
from typing import Any

from .constants import PARSER_BUILD_ID, SCHEMA_VERSION, TEXT_PARSER_BUILD_ID
from .errors import ValidationError
from .hashing import digest_prefix, object_hash, sha256_bytes

SKIP_TAGS = {"script", "style", "nav", "noscript", "svg", "header", "footer", "aside"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
AD_HINTS = ("ad-banner", "advertisement", "sponsored", "sponsor-slot", "广告位")
_MAIN_MARKERS = ('id="mw-content-text"', "id='mw-content-text'", 'id="bodyContent"', "id='bodyContent'")
_HTML_CHARSET_PATTERN = re.compile(
    br"charset\s*=\s*['\"]?\s*([A-Za-z0-9._-]+)",
    flags=re.IGNORECASE,
)
_HTML_CHARSET_ALIASES = {
    "gb2312": "gb18030",
    "gbk": "gb18030",
    "utf8": "utf-8",
}
_COMMON_CHINESE_CHARS = frozenset(
    "的一是在不了有和人這这中大為为上個个國国我以要他時时來来用們们生到作地於于出就分"
    "對对成會会可主發发年動动同工也能下過过子說说產产種种面而方後后多定行學学法所民"
    "得經经十三之進进著着等部度家電电力裡里如水化高自二理起小物現现实加量都兩两體体"
    "制機机當当使點点從从業业本去把性好應应開开它合還还因由其些然前外天政四日那社義"
    "义事平形相全表間间樣样與与關关各重新線线內内數数正心反你明看原又麼么利比或但質"
    "质氣气第向道命此變变條条只沒没結结解問问意建月公無无系軍军很情者最立代想已通並"
    "并提直題题黨党程展五果料象員员革位入常文總总次品式活設设及管特件長长求老頭头基"
    "資资邊边流路級级少圖图山統统接知較较將将組组見见計计別别她手角期根論论運运農农"
    "指幾几九區区強强放決决西被幹干做必戰战先回則则任取據据處处世風风雲云湧涌劍剑靈"
    "灵脈脉復复甦苏眾众驚惊訝讶夢梦轉转離离門门測测试試繁小說小"
)
_LEGACY_ENCODING_SCORE_MARGIN = 500


def _is_ad_attrs(attrs: list[tuple[str, str | None]]) -> bool:
    blob = " ".join((v or "") for _, v in attrs).casefold()
    return any(hint in blob for hint in AD_HINTS)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._capture_title = False
        self._skip = 0
        self.blocks: list[str] = []
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip or tag in SKIP_TAGS or _is_ad_attrs(attrs):
            if tag not in VOID_TAGS:
                self._skip += 1
            return
        if tag == "title":
            self._capture_title = True
        if tag in {"p", "h1", "h2", "h3", "li", "div", "article"}:
            self._flush()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip or tag in SKIP_TAGS or _is_ad_attrs(attrs):
            return
        if tag in {"p", "h1", "h2", "h3", "li", "div", "article"}:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if self._skip:
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


def decode_html(html: bytes) -> str:
    declared = _HTML_CHARSET_PATTERN.search(html[:4096])
    if declared:
        charset = declared.group(1).decode("ascii").casefold()
        charset = _HTML_CHARSET_ALIASES.get(charset, charset)
        try:
            return html.decode(charset)
        except (LookupError, UnicodeDecodeError):
            pass
    return decode_text(html, "auto")[0]


def _select_html_root(html: bytes) -> str:
    text = decode_html(html)
    for marker in _MAIN_MARKERS:
        idx = text.find(marker)
        if idx >= 0:
            lt = text.rfind("<", 0, idx)
            return text[lt:] if lt >= 0 else text[idx:]
    for pattern in (r"<main\b[^>]*>.*</main>", r"<article\b[^>]*>.*</article>"):
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(0)
    return text


def parse_html(artifact_id: str, html: bytes, *, document_id: str) -> dict[str, Any]:
    extractor = _TextExtractor()
    extractor.feed(_select_html_root(html))
    extractor._flush()
    title = normalize_text(extractor.title)
    if not title:
        raw = decode_html(html)
        found = re.search(r"<title\b[^>]*>(.*?)</title>", raw, flags=re.IGNORECASE | re.DOTALL)
        title = normalize_text(found.group(1) if found else "")
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
    page_lines: list[tuple[int, list[str]]] = []
    for i, page in enumerate(reader.pages, start=1):
        raw = page.extract_text() or ""
        lines = [normalize_text(x) for x in raw.splitlines()]
        lines = [x for x in lines if x]
        page_lines.append((i, lines))
    repeating: set[str] = set()
    if len(page_lines) >= 2:
        firsts = [lines[0] for _, lines in page_lines if lines]
        lasts = [lines[-1] for _, lines in page_lines if lines]
        if firsts and len(set(firsts)) == 1:
            repeating.add(firsts[0])
        if lasts and len(set(lasts)) == 1:
            repeating.add(lasts[0])
    blocks: list[tuple[int, str]] = []
    for i, lines in page_lines:
        body = [ln for ln in lines if ln not in repeating]
        text = normalize_text(" ".join(body))
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


def _legacy_chinese_quality(text: str) -> int:
    """Return a normalized plausibility score; it is a detector, not proof of language."""
    score = 0
    observed = 0
    for char in text:
        if char.isspace():
            continue
        observed += 1
        ordinal = ord(char)
        category = unicodedata.category(char)
        if category in {"Cc", "Cf", "Co", "Cs", "Cn"}:
            score -= 50
        elif (
            0x3040 <= ordinal <= 0x30FF
            or 0x3100 <= ordinal <= 0x312F
            or 0xAC00 <= ordinal <= 0xD7AF
        ):
            score -= 12
        elif 0x3400 <= ordinal <= 0x9FFF or 0xF900 <= ordinal <= 0xFAFF:
            score += 4 if char in _COMMON_CHINESE_CHARS else 1
        elif char.isascii() or category.startswith("P"):
            continue
        elif category.startswith("L"):
            score -= 3
        elif category.startswith("S"):
            score -= 15
    return score * 1000 // max(observed, 1)


def decode_text(data: bytes, encoding: str = "auto") -> tuple[str, str]:
    if encoding != "auto":
        try:
            return data.decode(encoding), encoding
        except (LookupError, UnicodeDecodeError) as exc:
            raise ValidationError(
                "E-TEXT-ENCODING",
                f"unable to decode text as {encoding}",
            ) from exc

    try:
        return data.decode("utf-8-sig"), "utf-8-sig"
    except UnicodeDecodeError:
        pass

    decoded: list[tuple[str, str]] = []
    for candidate in ("gb18030", "big5"):
        try:
            decoded.append((data.decode(candidate), candidate))
        except UnicodeDecodeError:
            pass
    if not decoded:
        raise ValidationError("E-TEXT-ENCODING", "unable to decode text with a supported encoding")
    if len(decoded) == 1:
        return decoded[0]

    ranked = sorted(
        ((_legacy_chinese_quality(text), candidate, text) for text, candidate in decoded),
        reverse=True,
    )
    if ranked[0][0] - ranked[1][0] < _LEGACY_ENCODING_SCORE_MARGIN:
        raise ValidationError(
            "E-TEXT-ENCODING-AMBIGUOUS",
            "legacy Chinese encoding is ambiguous; specify source.encoding explicitly",
        )
    _, candidate, text = ranked[0]
    return text, candidate


def parse_text(
    artifact_id: str,
    data: bytes,
    *,
    document_id: str,
    encoding: str = "auto",
) -> dict[str, Any]:
    raw, detected_encoding = decode_text(data, encoding)
    blocks = [normalize_text(block) for block in re.split(r"(?:\r?\n){2,}", raw)]
    blocks = [block for block in blocks if block]
    if not blocks and normalize_text(raw):
        blocks = [normalize_text(raw)]
    segments = [
        {
            "schema_version": SCHEMA_VERSION,
            "segment_id": f"SEG-{document_id[4:]}-{index:03d}",
            "document_id": document_id,
            "parent_segment_id": None,
            "ordinal": index,
            "segment_type": "paragraph",
            "normalized_text": block,
            "normalized_text_hash": text_hash(block),
            "source_locator": {
                "kind": "text",
                "selector": f"paragraph:{index + 1}",
                "start": 0,
                "end": len(block),
            },
        }
        for index, block in enumerate(blocks)
    ]
    document = {
        "schema_version": SCHEMA_VERSION,
        "document_id": document_id,
        "input_artifact_id": artifact_id,
        "parser_build_id": TEXT_PARSER_BUILD_ID,
        "title": blocks[0][:80] if blocks else "",
        "language": "zh",
        "structure_hash": "sha256:" + "0" * 64,
    }
    document["structure_hash"] = object_hash(document, omit=("structure_hash",))
    return {
        "document": document,
        "segments": segments,
        "detected_encoding": detected_encoding,
    }


def parser_build_id_for(media_type: str, data: bytes) -> str:
    if media_type.startswith("text/plain"):
        return TEXT_PARSER_BUILD_ID
    return PARSER_BUILD_ID


def parse_artifact(artifact_id: str, data: bytes, media_type: str, document_id: str) -> dict[str, Any]:
    try:
        if media_type == "application/pdf" or data.startswith(b"%PDF"):
            return parse_pdf(artifact_id, data, document_id=document_id)
        if media_type.startswith("text/plain"):
            return parse_text(artifact_id, data, document_id=document_id)
        return parse_html(artifact_id, data, document_id=document_id)
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError("E-PARSE", f"parser failed for {document_id}: {exc}") from exc


def diff_segments(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> dict[str, Any]:
    ha = {s["normalized_text_hash"] for s in a}
    hb = {s["normalized_text_hash"] for s in b}
    return {
        "removed_hashes": sorted(ha - hb),
        "added_hashes": sorted(hb - ha),
        "count_a": len(a),
        "count_b": len(b),
        "changed": ha != hb,
    }


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
