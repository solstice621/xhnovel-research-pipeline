from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
from threading import Thread
from urllib.parse import urlparse

import pytest

from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.http_fetch import HttpFetcher, MAX_REDIRECTS
from xhnovel_pipeline.ssrf import assert_public_http_url


class RedirectHandler(BaseHTTPRequestHandler):
    private_hits = 0
    paths: list[str] = []

    def do_GET(self):
        type(self).paths.append(self.path)
        if self.path == "/to-private":
            self.send_response(302)
            self.send_header("Location", "/private")
            self.end_headers()
        elif self.path == "/missing-location":
            self.send_response(302)
            self.end_headers()
            self.wfile.write(b"redirect explanation")
        elif self.path == "/relative":
            self.send_response(302)
            self.send_header("Location", "/final")
            self.end_headers()
        elif self.path.startswith("/loop/"):
            step = int(self.path.rsplit("/", 1)[1])
            self.send_response(302)
            self.send_header("Location", f"/loop/{step + 1}")
            self.end_headers()
        elif self.path == "/private":
            type(self).private_hits += 1
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"secret")
        elif self.path == "/final":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_error(404)

    def log_message(self, format, *args):
        pass


@contextmanager
def redirect_server():
    RedirectHandler.private_hits = 0
    RedirectHandler.paths = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_file_scheme_blocked():
    with pytest.raises(ValidationError) as exc:
        assert_public_http_url("file:///etc/passwd")
    assert exc.value.code == "E-SSRF-SCHEME"


def test_localhost_blocked():
    with pytest.raises(ValidationError):
        assert_public_http_url("http://localhost/secret")


def test_private_ip_blocked():
    with pytest.raises(ValidationError):
        assert_public_http_url("http://127.0.0.1/")


@pytest.mark.parametrize(
    "url",
    [
        "http://100.100.100.200/",
        "http://[::ffff:169.254.169.254]/",
        "http://[::ffff:172.16.0.1]/",
        "http://[::ffff:0.0.0.0]/",
        "http://224.0.0.1/",
        "http://239.255.255.250/",
        "http://[ff02::1]/",
        "http://[fec0::1]/",
        "http://[64:ff9b::7f00:1]/",
        "http://[::ffff:0:7f00:1]/",
    ],
)
def test_all_non_global_and_ipv4_mapped_addresses_are_blocked(url):
    with pytest.raises(ValidationError) as exc:
        assert_public_http_url(url)
    assert exc.value.code == "E-SSRF-IP"


def _allow_test_server(checked):
    def resolve(url):
        checked.append(url)
        parsed = urlparse(url)
        infos = socket.getaddrinfo(parsed.hostname, parsed.port, type=socket.SOCK_STREAM)
        return parsed, infos

    return resolve


def test_redirect_target_is_ssrf_checked_before_request(monkeypatch):
    checked: list[str] = []

    def guard(url: str):
        checked.append(url)
        if urlparse(url).path == "/private":
            raise ValidationError("E-SSRF-IP", "blocked redirect target")
        parsed = urlparse(url)
        return parsed, socket.getaddrinfo(parsed.hostname, parsed.port, type=socket.SOCK_STREAM)

    monkeypatch.setattr("xhnovel_pipeline.http_fetch.resolve_public_http_url", guard)
    with redirect_server() as base_url:
        with pytest.raises(ValidationError) as exc:
            HttpFetcher().fetch(f"{base_url}/to-private")

    assert exc.value.code == "E-SSRF-IP"
    assert [urlparse(url).path for url in checked] == ["/to-private", "/private"]
    assert RedirectHandler.private_hits == 0


def test_relative_redirect_location_is_resolved(monkeypatch):
    checked: list[str] = []
    monkeypatch.setattr("xhnovel_pipeline.http_fetch.resolve_public_http_url", _allow_test_server(checked))

    with redirect_server() as base_url:
        data, _, status, final_url = HttpFetcher().fetch(f"{base_url}/relative")

    assert data == b"ok"
    assert status == 200
    assert final_url == f"{base_url}/final"
    assert checked == [f"{base_url}/relative", f"{base_url}/final"]


def test_redirect_limit_is_enforced_by_fetcher(monkeypatch):
    monkeypatch.setattr("xhnovel_pipeline.http_fetch.resolve_public_http_url", _allow_test_server([]))

    with redirect_server() as base_url:
        with pytest.raises(ValidationError) as exc:
            HttpFetcher().fetch(f"{base_url}/loop/0")

    assert exc.value.code == "E-REDIRECT"
    assert len(RedirectHandler.paths) == MAX_REDIRECTS + 1


def test_invalid_redirect_preserves_raw_response(monkeypatch):
    monkeypatch.setattr("xhnovel_pipeline.http_fetch.resolve_public_http_url", _allow_test_server([]))

    with redirect_server() as base_url:
        with pytest.raises(ValidationError) as exc:
            HttpFetcher().fetch(f"{base_url}/missing-location")

    assert exc.value.code == "E-REDIRECT"
    assert exc.value.raw_response_bytes == b"redirect explanation"


def test_environment_proxy_is_not_used(monkeypatch):
    checked: list[str] = []
    monkeypatch.setattr("xhnovel_pipeline.http_fetch.resolve_public_http_url", _allow_test_server(checked))
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")

    with redirect_server() as base_url:
        data, _, status, _ = HttpFetcher().fetch(f"{base_url}/final")

    assert data == b"ok"
    assert status == 200
    assert len(checked) == 1


def test_connection_uses_validated_sockaddr_without_second_dns(monkeypatch):
    with redirect_server() as base_url:
        url = f"{base_url}/final"
        parsed = urlparse(url)
        infos = socket.getaddrinfo(parsed.hostname, parsed.port, type=socket.SOCK_STREAM)
        monkeypatch.setattr(
            "xhnovel_pipeline.http_fetch.resolve_public_http_url",
            lambda candidate: (urlparse(candidate), infos),
        )
        monkeypatch.setattr(
            "xhnovel_pipeline.http_fetch.socket.getaddrinfo",
            lambda *args, **kwargs: pytest.fail("validated hostname was resolved a second time"),
        )

        data, _, status, _ = HttpFetcher().fetch(url)

    assert data == b"ok"
    assert status == 200
