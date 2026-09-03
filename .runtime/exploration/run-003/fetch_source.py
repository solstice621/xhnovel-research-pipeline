"""Host fetch of one HTML catalog into a local chapter directory.

Uses the pipeline HttpFetcher (SSRF-safe, frozen User-Agent). Not a product crawler.
"""

from __future__ import annotations

import html as html_mod
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

from xhnovel_pipeline.errors import ValidationError
from xhnovel_pipeline.http_fetch import HttpFetcher
from xhnovel_pipeline.parse import normalize_text

INDEX = "https://www.shubaobiquge.com/169709/"
CHAPTER_RE = re.compile(r"/169709/[0-9]+(?:_[0-9]+)?\.html$")
CATALOG_RE = re.compile(r"/169709/[0-9]+\.html$")
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "input" / "chapters"
LOG = ROOT / "logs" / "fetch.log"
_LOG_LOCK = threading.Lock()

TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 6
MAX_WORKERS = 1
MIN_BYTES = 80


class Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((self._href, "".join(self._text).strip()))
            self._href = None


def log(msg: str) -> None:
    line = msg + "\n"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_LOCK:
        LOG.open("a", encoding="utf-8").write(line)
        print(msg, flush=True)


def fetch_with_retry(fetcher: HttpFetcher, url: str, attempts: int = MAX_ATTEMPTS) -> bytes:
    last: Exception | None = None
    for i in range(attempts):
        try:
            raw, _media, status, _final = fetcher.fetch(url)
            if status != 200:
                raise ValidationError("E-HTTP", f"HTTP {status} for {url}")
            return raw
        except (ValidationError, OSError, TimeoutError) as exc:
            last = exc
            time.sleep(min(8, 1.5 * (i + 1)))
    raise last  # type: ignore[misc]


def extract_content(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace")
    match = re.search(r'<div id="content">(.*?)</div>', text, flags=re.IGNORECASE | re.DOTALL)
    chunk = match.group(1) if match else text
    chunk = re.sub(
        r"<(?:script|style|noscript|svg)\b[^>]*>.*?</(?:script|style|noscript|svg)\s*>",
        " ",
        chunk,
        flags=re.IGNORECASE | re.DOTALL,
    )
    visible = normalize_text(html_mod.unescape(re.sub(r"<[^>]+>", " ", chunk)))
    return visible


def next_page_url(raw: bytes, current: str) -> str | None:
    parser = Links()
    parser.feed(raw.decode("utf-8", "replace"))
    for href, label in parser.links:
        if "下一页" not in (label or ""):
            continue
        joined = urljoin(current, href)
        if urlparse(joined).netloc != urlparse(INDEX).netloc:
            continue
        if CHAPTER_RE.search(urlparse(joined).path):
            return joined
    return None


def catalog_chapters(fetcher: HttpFetcher) -> list[tuple[str, str]]:
    raw = fetch_with_retry(fetcher, INDEX)
    parser = Links()
    parser.feed(raw.decode("utf-8", "replace"))
    seen: dict[str, str] = {}
    order: list[str] = []
    for href, label in parser.links:
        joined = urljoin(INDEX, href or "")
        if CATALOG_RE.search(urlparse(joined).path) and joined not in seen:
            seen[joined] = label or joined
            order.append(joined)
    return [(url, seen[url]) for url in order]


def fetch_chapter_text(fetcher: HttpFetcher, start_url: str) -> str:
    parts: list[str] = []
    url = start_url
    seen: set[str] = set()
    for _ in range(8):
        if url in seen:
            break
        seen.add(url)
        raw = fetch_with_retry(fetcher, url)
        parts.append(extract_content(raw))
        nxt = next_page_url(raw, url)
        if not nxt or nxt in seen:
            break
        url = nxt
    return "\n".join(p for p in parts if p)


def write_one(item: tuple[int, str, str]) -> tuple[int, str, int, str | None]:
    ordinal, url, title = item
    path = OUT / f"{ordinal:04d}.txt"
    heading = normalize_text(title) or f"Chapter {ordinal}"
    if path.is_file() and path.stat().st_size > MIN_BYTES:
        return ordinal, heading, path.stat().st_size, None
    fetcher = HttpFetcher(timeout=TIMEOUT_SECONDS, max_bytes=2_000_000)
    try:
        body = fetch_chapter_text(fetcher, url)
        if not body.strip():
            return ordinal, heading, 0, "empty body"
        path.write_text(f"{heading}\n\n{body}\n", encoding="utf-8")
        size = path.stat().st_size
        if size <= MIN_BYTES:
            return ordinal, heading, size, "too short"
        return ordinal, heading, size, None
    except Exception as exc:  # host fetch: record and continue
        return ordinal, heading, 0, f"{type(exc).__name__}: {exc}"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    fetcher = HttpFetcher(timeout=TIMEOUT_SECONDS, max_bytes=2_000_000)
    chapters = catalog_chapters(fetcher)
    log(f"catalog chapters={len(chapters)}")
    if len(chapters) < 1000:
        raise SystemExit(f"catalog too small: {len(chapters)}")
    jobs = []
    skipped = 0
    for ordinal, (url, title) in enumerate(chapters, start=1):
        path = OUT / f"{ordinal:04d}.txt"
        if path.is_file() and path.stat().st_size > MIN_BYTES:
            skipped += 1
            continue
        jobs.append((ordinal, url, title))
    log(f"resume skipped={skipped} remaining={len(jobs)}")
    if not jobs:
        log("done (nothing remaining)")
        return
    done = 0
    failed: list[tuple[int, str]] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(write_one, job) for job in jobs]
        for future in as_completed(futures):
            ordinal, heading, size, error = future.result()
            done += 1
            if error:
                failed.append((ordinal, error))
                log(f"FAIL {done}/{len(jobs)} {ordinal:04d} {heading} {error}")
            elif done == 1 or done % 25 == 0 or done == len(jobs):
                log(f"wrote {done}/{len(jobs)} last={ordinal:04d} {heading} bytes={size}")
    log(f"done remaining_failures={len(failed)}")
    if failed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
