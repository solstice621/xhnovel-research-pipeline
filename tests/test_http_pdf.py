from __future__ import annotations

from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.http_fetch import retry_fetch
from xhnovel_pipeline.parse import parse_pdf, make_minimal_pdf
import pytest


class FakeFetcher:
    def __init__(self, sequence):
        self.sequence = list(sequence)

    def fetch(self, url: str):
        item = self.sequence.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_retry_429_then_ok():
    from xhnovel_pipeline.errors import ValidationError as E

    fetcher = FakeFetcher(
        [
            E("E-RETRYABLE", "HTTP 429"),
            (b"ok", "text/html", 200, "https://example.com"),
        ]
    )
    data, *_ = retry_fetch(fetcher, "https://example.com", attempts=3)
    assert data == b"ok"


def test_pdf_parse_roundtrip():
    from pypdf import PdfWriter
    import io

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    data = buf.getvalue()
    parsed = parse_pdf("sha256:" + "a" * 64, data, document_id="DOC-PDF-1")
    assert parsed["document"]["document_id"] == "DOC-PDF-1"
    assert parsed["document"]["structure_hash"].startswith("sha256:")
