from __future__ import annotations

import re

from .parse import normalize_text

LOGIN_MARKERS = ("请登录", "请先登录", "sign in", "log in", "login required")
SCRIPT_RE = re.compile(rb"<script", re.I)


def looks_like_login_wall(data: bytes, http_status: int) -> bool:
    if http_status in {401, 403}:
        return True
    text = data.decode("utf-8", errors="replace").casefold()
    return any(marker in text for marker in LOGIN_MARKERS)


def looks_like_js_shell(data: bytes) -> bool:
    if SCRIPT_RE.search(data) is None:
        return False
    text = data.decode("utf-8", errors="replace")
    body = normalize_text(re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I))
    body = re.sub(r"<[^>]+>", " ", body)
    body = normalize_text(body)
    return len(body) < 40 and data.lower().count(b"<script") >= 3
