from __future__ import annotations

import gzip
from email.message import Message

from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.http_fetch import HttpFetcher, retry_fetch
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


def _response(data: bytes, *, encoding: str = "gzip", status: int = 200):
    headers = Message()
    headers["Content-Type"] = "text/plain"
    headers["Content-Encoding"] = encoding
    return data, headers, status


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


def test_gzip_decompressed_size_is_bounded(monkeypatch):
    payload = bytes(range(256)) * 8
    response = _response(gzip.compress(payload))
    monkeypatch.setattr("xhnovel_pipeline.http_fetch._request_once", lambda *args, **kwargs: response)

    with pytest.raises(ValidationError) as exc:
        HttpFetcher(max_bytes=1024).fetch("https://example.com/data")

    assert exc.value.code == "E-TOO-LARGE"


def test_gzip_decompression_ratio_is_bounded(monkeypatch):
    raw = gzip.compress(b"A" * 10_000)
    response = _response(raw)
    monkeypatch.setattr("xhnovel_pipeline.http_fetch._request_once", lambda *args, **kwargs: response)

    with pytest.raises(ValidationError) as exc:
        HttpFetcher(max_bytes=20_000).fetch("https://example.com/data")

    assert exc.value.code == "E-BOMB"
    assert exc.value.raw_response_bytes == raw


def test_gzip_is_not_fully_decompressed_before_limits_are_checked(monkeypatch):
    response = _response(gzip.compress(b"A" * 10_000))
    monkeypatch.setattr("xhnovel_pipeline.http_fetch._request_once", lambda *args, **kwargs: response)
    monkeypatch.setattr(
        gzip,
        "decompress",
        lambda raw: pytest.fail("gzip.decompress expands the entire body before checks"),
    )

    with pytest.raises(ValidationError) as exc:
        HttpFetcher(max_bytes=20_000).fetch("https://example.com/data")

    assert exc.value.code == "E-BOMB"


def test_malformed_gzip_is_normalized_and_preserves_raw_bytes(monkeypatch):
    raw = b"\x1f\x8b\x08\x00invalid-gzip"
    monkeypatch.setattr(
        "xhnovel_pipeline.http_fetch._request_once",
        lambda *args, **kwargs: _response(raw),
    )

    with pytest.raises(ValidationError) as exc:
        HttpFetcher().fetch("https://example.com/data")

    assert exc.value.code == "E-DECOMPRESSION"
    assert exc.value.raw_response_bytes == raw


@pytest.mark.parametrize("status,code", [(429, "E-RETRYABLE"), (404, "E-HTTP")])
def test_http_error_status_is_normalized(monkeypatch, status, code):
    monkeypatch.setattr(
        "xhnovel_pipeline.http_fetch._request_once",
        lambda *args, **kwargs: _response(b"body", encoding="", status=status),
    )

    with pytest.raises(ValidationError) as exc:
        HttpFetcher().fetch("https://example.com/data")

    assert exc.value.code == code
    assert exc.value.raw_response_bytes == b"body"


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
