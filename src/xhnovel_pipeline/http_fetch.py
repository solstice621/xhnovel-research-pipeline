from __future__ import annotations

import gzip
import io
import ssl
import urllib.error
import urllib.request
from typing import Any

from .errors import ValidationError
from .ssrf import assert_public_http_url
from .user_agent import USER_AGENT

MAX_BYTES = 2_000_000
MAX_REDIRECTS = 5
MAX_RATIO = 50


class HttpFetcher:
    def __init__(self, *, timeout: float = 15.0, max_bytes: int = MAX_BYTES) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes

    def fetch(self, url: str) -> tuple[bytes, str, int, str]:
        current = url
        for _ in range(MAX_REDIRECTS):
            assert_public_http_url(current)
            req = urllib.request.Request(
                current, headers={"User-Agent": USER_AGENT}
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout, context=ssl.create_default_context()) as resp:
                    status = getattr(resp, "status", 200)
                    if status in {301, 302, 303, 307, 304, 308} or 300 <= status < 400:
                        loc = resp.headers.get("Location")
                        if not loc:
                            raise ValidationError("E-REDIRECT", f"redirect without Location from {current}")
                        current = loc
                        continue
                    raw = resp.read(self.max_bytes + 1)
                    if len(raw) > self.max_bytes:
                        raise ValidationError("E-TOO-LARGE", f"response over {self.max_bytes}")
                    enc = (resp.headers.get("Content-Encoding") or "").lower()
                    data = _maybe_decompress(raw, enc)
                    if len(data) > self.max_bytes * MAX_RATIO:
                        raise ValidationError("E-BOMB", "decompression ratio too high")
                    ctype = resp.headers.get("Content-Type") or "application/octet-stream"
                    return data, ctype, status, current
            except urllib.error.HTTPError as exc:
                if exc.code in {429, 500, 502, 503, 504}:
                    raise ValidationError("E-RETRYABLE", f"HTTP {exc.code} for {current}") from exc
                raise ValidationError("E-HTTP", f"HTTP {exc.code} for {current}") from exc
        raise ValidationError("E-REDIRECT", f"too many redirects for {url}")


def _maybe_decompress(raw: bytes, enc: str) -> bytes:
    if enc == "gzip":
        return gzip.decompress(raw)
    return raw


def retry_fetch(fetcher: HttpFetcher, url: str, *, attempts: int = 3) -> tuple[bytes, str, int, str]:
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return fetcher.fetch(url)
        except ValidationError as exc:
            last = exc
            if exc.code != "E-RETRYABLE":
                raise
    assert last is not None
    raise last
