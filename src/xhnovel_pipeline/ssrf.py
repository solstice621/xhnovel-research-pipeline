from __future__ import annotations

import socket
import ipaddress
from urllib.parse import urlparse

from .errors import ValidationError

BLOCKED_TRANSITION_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "64:ff9b::/96",
        "64:ff9b:1::/48",
        "2001::/32",
        "2002::/16",
        "::ffff:0:0:0/96",
    )
)


def _is_public_unicast(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return _is_public_unicast(ip.ipv4_mapped)
        if ip.is_site_local or any(ip in network for network in BLOCKED_TRANSITION_NETWORKS):
            return False
    return (
        ip.is_global
        and not ip.is_multicast
        and not ip.is_unspecified
        and not ip.is_reserved
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_private
    )


def resolve_public_http_url(url: str) -> tuple[object, list[tuple]]:
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
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValidationError("E-SSRF-HOST", f"invalid port in {url}") from exc
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise ValidationError("E-SSRF-DNS", f"cannot resolve {host}: {exc}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not _is_public_unicast(ip):
            raise ValidationError("E-SSRF-IP", f"blocked IP {ip} for {host}")
    if not infos:
        raise ValidationError("E-SSRF-DNS", f"no addresses for {host}")
    return parsed, infos


def assert_public_http_url(url: str) -> None:
    resolve_public_http_url(url)
