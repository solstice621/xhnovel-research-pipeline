from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from .errors import ValidationError

BLOCKED = tuple(
    ipaddress.ip_network(net)
    for net in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "224.0.0.0/4",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
        "::ffff:127.0.0.0/104",
        "::ffff:10.0.0.0/104",
        "::ffff:192.168.0.0/120",
    )
)


def assert_public_http_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValidationError("E-SSRF-SCHEME", f"refusing scheme {parsed.scheme!r} for {url}")
    if parsed.username or parsed.password:
        raise ValidationError("E-SSRF-USERINFO", "credentials in URL are forbidden")
    host = parsed.hostname
    if not host:
        raise ValidationError("E-SSRF-HOST", f"missing host in {url}")
    if host.lower() in {"localhost", "metadata.google.internal"}:
        raise ValidationError("E-SSRF-HOST", f"blocked host {host}")
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ValidationError("E-SSRF-DNS", f"cannot resolve {host}: {exc}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if any(ip in net for net in BLOCKED):
            raise ValidationError("E-SSRF-IP", f"blocked IP {ip} for {host}")
