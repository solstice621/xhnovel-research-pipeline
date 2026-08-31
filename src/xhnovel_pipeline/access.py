from __future__ import annotations

import re

SNIPPET_ALIASES = {
    "search_snippet",
    "searchsnippet",
    "search-snippet",
    "search_excerpt",
    "searchexcerpt",
    "search excerpt",
    "搜索摘录",
    "搜索摘要",
}

KIND_ALIASES = {
    "full_page": "full_page",
    "fullpage": "full_page",
    "全文": "full_page",
    "search_snippet": "search_snippet",
    "licensed_teaser": "licensed_teaser",
    "licensedteaser": "licensed_teaser",
    "catalog_page": "catalog_page",
    "catalogpage": "catalog_page",
    "unauthorized_reprint": "unauthorized_reprint",
    "unauthorizedreprint": "unauthorized_reprint",
}

SNIPPET_KINDS = {"search_snippet", "search_excerpt"}


def normalize_access_kind(kind: object) -> str:
    text = str(kind or "").strip()
    if not text:
        return ""
    folded = text.casefold()
    if folded in SNIPPET_ALIASES or text in SNIPPET_ALIASES:
        return "search_snippet"
    underscored = re.sub(r"[\s.\-]+", "_", text)
    compact = underscored.replace("_", "")
    for key in (text, underscored, underscored.casefold(), compact.casefold(), folded):
        if key in KIND_ALIASES:
            return KIND_ALIASES[key]
        if key in SNIPPET_ALIASES:
            return "search_snippet"
    return underscored.casefold()


def is_snippet_kind(kind: object) -> bool:
    return normalize_access_kind(kind) in SNIPPET_KINDS


def looks_like_snippet_label(raw: object) -> bool:
    text = str(raw or "")
    if is_snippet_kind(text):
        return True
    return any(marker in text.casefold() for marker in ("搜索摘录", "搜索摘要", "search snippet", "search excerpt"))
