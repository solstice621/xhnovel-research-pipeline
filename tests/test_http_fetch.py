from __future__ import annotations

import gzip

import pytest

from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.http_fetch import _maybe_decompress


def test_gzip_accepts_highly_compressible_content_within_size_limit():
    content = b"<p>chapter text</p>" * 1000
    compressed = gzip.compress(content)
    assert len(content) > len(compressed) * 50
    assert _maybe_decompress(compressed, "gzip", max_bytes=len(content)) == content


def test_gzip_still_rejects_content_over_absolute_size_limit():
    compressed = gzip.compress(b"x" * 100_000)
    with pytest.raises(ValidationError, match="E-TOO-LARGE"):
        _maybe_decompress(compressed, "gzip", max_bytes=99_999)


def test_gzip_rejects_invalid_stream():
    with pytest.raises(ValidationError, match="E-DECOMPRESSION"):
        _maybe_decompress(b"not gzip", "gzip", max_bytes=100_000)
