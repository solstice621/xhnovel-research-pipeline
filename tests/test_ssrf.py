from __future__ import annotations

import pytest

from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.ssrf import assert_public_http_url


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
