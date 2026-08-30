from __future__ import annotations

import gzip
import http.client
import io
import socket
import ssl
import zlib
from urllib.parse import urljoin

from .errors import ValidationError
from .ssrf import resolve_public_http_url
from .user_agent import USER_AGENT

MAX_BYTES = 2_000_000
MAX_REDIRECTS = 5
MAX_RATIO = 50
DECOMPRESSION_CHUNK_SIZE = 64 * 1024


REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def _connect_validated(addresses: list[tuple], timeout: float) -> socket.socket:
    last_error: OSError | None = None
    for family, socktype, proto, _, sockaddr in addresses:
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(timeout)
        try:
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
    raise ValidationError("E-UNREACHABLE", f"connection failed: {last_error}") from last_error


def _request_target(parsed) -> str:
    target = parsed.path or "/"
    if parsed.params:
        target += ";" + parsed.params
    if parsed.query:
        target += "?" + parsed.query
    return target


def _request_once(url: str, *, timeout: float, max_bytes: int) -> tuple[bytes, object, int]:
    parsed, addresses = resolve_public_http_url(url)
    host = parsed.hostname or ""
    ascii_host = host.encode("idna").decode("ascii")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    sock = _connect_validated(addresses, timeout)
    if parsed.scheme == "https":
        try:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=ascii_host)
        except Exception:
            sock.close()
            raise
    connection = http.client.HTTPConnection(ascii_host, port=port, timeout=timeout)
    connection.sock = sock
    try:
        connection.request("GET", _request_target(parsed), headers={"User-Agent": USER_AGENT})
        response = connection.getresponse()
        status = response.status
        headers = response.headers
        raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValidationError("E-TOO-LARGE", f"response over {max_bytes}")
        return raw, headers, status
    except ValidationError:
        raise
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        raise ValidationError("E-UNREACHABLE", f"request failed for {url}: {exc}") from exc
    finally:
        connection.close()


class HttpFetcher:
    def __init__(self, *, timeout: float = 15.0, max_bytes: int = MAX_BYTES) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes

    def fetch(self, url: str) -> tuple[bytes, str, int, str]:
        current = url
        redirects = 0
        while True:
            try:
                raw, headers, status = _request_once(current, timeout=self.timeout, max_bytes=self.max_bytes)
            except (OSError, TimeoutError, ssl.SSLError) as exc:
                raise ValidationError("E-UNREACHABLE", f"request failed for {current}: {exc}") from exc
            if status in REDIRECT_STATUSES:
                try:
                    current, redirects = _next_redirect(current, headers, redirects, url)
                except ValidationError as exc:
                    exc.raw_response_bytes = raw
                    raise
                continue
            if status in {429, 500, 502, 503, 504}:
                error = ValidationError("E-RETRYABLE", f"HTTP {status} for {current}")
                error.raw_response_bytes = raw
                raise error
            if status >= 400:
                error = ValidationError("E-HTTP", f"HTTP {status} for {current}")
                error.raw_response_bytes = raw
                raise error
            enc = (headers.get("Content-Encoding") or "").lower()
            try:
                data = _maybe_decompress(raw, enc, max_bytes=self.max_bytes)
            except ValidationError as exc:
                exc.raw_response_bytes = raw
                raise
            ctype = headers.get("Content-Type") or "application/octet-stream"
            return data, ctype, status, current


def _next_redirect(current: str, headers, redirects: int, original: str) -> tuple[str, int]:
    location = headers.get("Location")
    if not location:
        raise ValidationError("E-REDIRECT", f"redirect without Location from {current}")
    if redirects >= MAX_REDIRECTS:
        raise ValidationError("E-REDIRECT", f"too many redirects for {original}")
    return urljoin(current, location), redirects + 1


def _maybe_decompress(raw: bytes, enc: str, *, max_bytes: int) -> bytes:
    if enc == "gzip":
        ratio_limit = len(raw) * MAX_RATIO
        output_limit = min(max_bytes, ratio_limit)
        output = bytearray()
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                while True:
                    remaining = output_limit - len(output)
                    chunk = stream.read(min(DECOMPRESSION_CHUNK_SIZE, remaining + 1))
                    if not chunk:
                        break
                    output.extend(chunk)
                    if len(output) > output_limit:
                        if ratio_limit < max_bytes:
                            raise ValidationError(
                                "E-BOMB", f"decompression ratio exceeds {MAX_RATIO}:1"
                            )
                        raise ValidationError(
                            "E-TOO-LARGE", f"decompressed response over {max_bytes}"
                        )
        except (gzip.BadGzipFile, EOFError, OSError, zlib.error) as exc:
            raise ValidationError("E-DECOMPRESSION", "invalid gzip response") from exc
        return bytes(output)
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
