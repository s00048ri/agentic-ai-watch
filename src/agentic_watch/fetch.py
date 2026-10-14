"""Change detection without an LLM (SPEC.md §4.1): RSS, sitemaps and page hashes.

Polite access: robots.txt is honoured, requests to one host are spaced by at least
`min_interval_seconds_per_domain`, and every request carries the configured User-Agent.
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import html
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import feedparser
import httpx
import trafilatura
from dateutil import parser as dateparser

from .config import Crawl, Organisation, Project, WatchPoint
from .models import Item, SeenRecord, item_id
from .store import Store
from .urls import normalise_url, on_domains

FREQUENCY_DAYS = {"weekly": 0, "monthly": 28, "annual": 360}
SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
NEWS_NS = "{http://www.google.com/schemas/sitemap-news/0.9}"
MAX_CHILD_SITEMAPS = 20


class RobotsDisallowed(Exception):
    pass


def describe_error(e: Exception) -> str:
    """A short description for reports, with the HTTP status when there is one."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code} for {e.request.url}"
    if isinstance(e, RobotsDisallowed):
        return f"robots.txt disallows {e} (or robots.txt could not be read)"
    return f"{type(e).__name__}: {e}" if str(e) else type(e).__name__


class Fetcher:
    """HTTP access with robots.txt checks and per-host spacing."""

    def __init__(
        self,
        crawl: Crawl,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.crawl = crawl
        self.client = client or httpx.Client(
            headers={"User-Agent": crawl.user_agent},
            timeout=crawl.timeout_seconds,
            follow_redirects=True,
        )
        self._sleep = sleep
        self._clock = clock
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser] = {}

    def _wait_for_host(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            gap = self.crawl.min_interval_seconds_per_domain - (self._clock() - last)
            if gap > 0:
                self._sleep(gap)
        self._last_request[host] = self._clock()

    def _raw_get(self, url: str) -> httpx.Response:
        host = urlsplit(url).hostname or ""
        self._wait_for_host(host)
        return self.client.get(url, headers={"User-Agent": self.crawl.user_agent})

    def robots(self, url: str) -> RobotFileParser:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser = RobotFileParser()
            try:
                resp = self._raw_get(origin + "/robots.txt")
                if resp.status_code >= 500:
                    parser.disallow_all = True  # type: ignore[attr-defined]  # server error: stay away (RFC 9309 §2.3.1.4)
                elif resp.status_code >= 400:
                    parser.allow_all = True  # type: ignore[attr-defined]  # no robots.txt: everything allowed
                else:
                    parser.parse(resp.text.splitlines())
            except httpx.HTTPError:
                parser.disallow_all = True  # type: ignore[attr-defined]
            self._robots[origin] = parser
        return self._robots[origin]

    def allowed(self, url: str) -> bool:
        return self.robots(url).can_fetch(self.crawl.user_agent, url)

    def get(self, url: str) -> httpx.Response:
        if not self.allowed(url):
            raise RobotsDisallowed(url)
        resp = self._raw_get(url)
        resp.raise_for_status()
        return resp

    def sitemaps_from_robots(self, url: str) -> list[str]:
        return list(self.robots(url).site_maps() or [])


# ---------------------------------------------------------------- text helpers


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def page_title(html_text: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.S | re.I)
    return strip_html(m.group(1)) if m else ""


def extract_text(html_text: str, url: str | None = None) -> str:
    """Main text of a page via trafilatura. Held in memory only."""
    return trafilatura.extract(html_text, url=url, include_comments=False, include_tables=False) or ""


def content_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode("utf-8")).hexdigest()


def _parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dateparser.parse(value).date()
    except (ValueError, OverflowError):
        return None


# ---------------------------------------------------------------- watch point readers


@dataclass
class Found:
    url: str
    title: str = ""
    summary: str | None = None
    published: dt.date | None = None
    lastmod: str | None = None
    content_hash: str | None = None


def read_rss(fetcher: Fetcher, wp: WatchPoint) -> list[Found]:
    assert wp.feed_url
    resp = fetcher.get(wp.feed_url)
    feed = feedparser.parse(resp.content)
    out: list[Found] = []
    for e in feed.entries:
        link = e.get("link")
        if not link:
            continue
        published = None
        for key in ("published_parsed", "updated_parsed"):
            t = e.get(key)
            if t:
                published = dt.date(t.tm_year, t.tm_mon, t.tm_mday)
                break
        summary = e.get("summary")
        out.append(
            Found(
                url=urljoin(wp.feed_url, link),
                title=strip_html(e.get("title", "")),
                summary=strip_html(summary) if summary else None,
                published=published,
            )
        )
    return out


def _xml_root(resp: httpx.Response) -> ET.Element:
    body = resp.content
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    return ET.fromstring(body)


def read_sitemap(fetcher: Fetcher, wp: WatchPoint, max_urls: int) -> list[Found]:
    """Read the sitemap (feed_url, else robots.txt Sitemap: lines, else /sitemap.xml).

    Only URLs under the watch point URL's path are kept, so a site-wide sitemap can serve
    a single section such as /news.
    """
    scope = urlsplit(wp.url)
    if wp.feed_url:
        locations = [wp.feed_url]
    else:
        locations = fetcher.sitemaps_from_robots(wp.url) or [f"{scope.scheme}://{scope.netloc}/sitemap.xml"]
    prefix = scope.path.rstrip("/")

    out: list[Found] = []
    queue = list(locations)
    visited = read = 0
    first_error: Exception | None = None
    while queue and visited < MAX_CHILD_SITEMAPS and len(out) < max_urls:
        loc = queue.pop(0)
        visited += 1
        try:
            root = _xml_root(fetcher.get(loc))
        except (httpx.HTTPError, RobotsDisallowed, ET.ParseError, OSError) as e:
            first_error = first_error or e  # one broken sitemap among several should not stop the rest
            continue
        read += 1
        if root.tag == f"{SITEMAP_NS}sitemapindex":
            queue.extend(
                (s.findtext(f"{SITEMAP_NS}loc") or "").strip() for s in root.findall(f"{SITEMAP_NS}sitemap")
            )
            continue
        for u in root.findall(f"{SITEMAP_NS}url"):
            url = (u.findtext(f"{SITEMAP_NS}loc") or "").strip()
            if not url or not urlsplit(url).path.startswith(prefix):
                continue
            lastmod = (u.findtext(f"{SITEMAP_NS}lastmod") or "").strip() or None
            news = u.find(f"{NEWS_NS}news")
            title = (news.findtext(f"{NEWS_NS}title") or "").strip() if news is not None else ""
            pub = news.findtext(f"{NEWS_NS}publication_date") if news is not None else None
            out.append(Found(url=url, title=title, lastmod=lastmod, published=_parse_date(pub or lastmod)))
            if len(out) >= max_urls:
                break
    if not read and first_error is not None:
        raise first_error
    return out


def sitemap_paths(fetcher: Fetcher, url: str, depth: int = 2, max_urls: int = 50000) -> list[tuple[str, int]]:
    """How many sitemap URLs fall under each path prefix (to choose a sitemap watch point's scope)."""
    parts = urlsplit(url)
    site = WatchPoint(type="newsroom", url=f"{parts.scheme}://{parts.netloc}/", method="sitemap")
    if parts.path.endswith(".xml"):
        site.feed_url = url
    counts: dict[str, int] = {}
    for f in read_sitemap(fetcher, site, max_urls):
        segments = [s for s in urlsplit(f.url).path.split("/") if s][:depth]
        key = "/" + "/".join(segments)
        counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


_HREF = re.compile(r"""<a\s[^>]*?href\s*=\s*["']([^"'#]+)""", re.I)


def read_links(fetcher: Fetcher, wp: WatchPoint) -> list[Found]:
    """Links on a listing page (for sites with neither a feed nor a usable sitemap).

    Links on the same host whose path starts with `wp.scope` (default: the page's own path) count;
    the page itself does not. Links carry no date, so a watch point's first check only records them.
    """
    page = urlsplit(wp.url)
    prefix = (wp.scope or page.path).rstrip("/") or "/"
    resp = fetcher.get(wp.url)
    out: dict[str, Found] = {}
    for href in _HREF.findall(resp.text):
        url = urljoin(wp.url, html.unescape(href.strip()))
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or parts.netloc != page.netloc:
            continue
        if not parts.path.startswith(prefix) or parts.path.rstrip("/") == page.path.rstrip("/"):
            continue
        out.setdefault(url, Found(url=url))
    return list(out.values())


def read_page_hash(fetcher: Fetcher, wp: WatchPoint) -> list[Found]:
    resp = fetcher.get(wp.url)
    text = extract_text(resp.text, wp.url)
    return [Found(url=wp.url, title=page_title(resp.text), content_hash=content_hash(text))]


# ---------------------------------------------------------------- detection


@dataclass
class Detection:
    items: list[Item] = field(default_factory=list)
    seen_updates: list[SeenRecord] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    checked: dict[str, dt.date] = field(default_factory=dict)
    per_point: list[str] = field(default_factory=list)  # one line per watch point checked, for the dry run


def is_due(wp: WatchPoint, last: dt.date | None, today: dt.date) -> bool:
    return last is None or (today - last).days >= FREQUENCY_DAYS[wp.frequency]


def detect(project: Project, store: Store, fetcher: Fetcher, today: dt.date, lookback_days: int) -> Detection:
    """Find new pages on active organisations' watch points.

    On the first check of a watch point, entries dated more than `lookback_days` ago (or
    undated sitemap entries) are recorded as a baseline instead of being processed, so a
    new watch point does not flood the pipeline with its back catalogue.
    """
    checks = store.checks()
    seen = store.seen()
    result = Detection()
    queued_urls: set[str] = set()

    for org in project.organisations:
        if org.status != "active":
            continue
        for wp in org.watch_points:
            key = wp.feed_url or wp.url
            if wp.method == "manual":
                result.manual.append(f"{org.name}: {wp.url}")
                continue
            last = checks.get(key)
            if not is_due(wp, last, today):
                continue
            first_check = last is None
            try:
                if wp.method == "rss":
                    found = read_rss(fetcher, wp)
                elif wp.method == "sitemap":
                    found = read_sitemap(fetcher, wp, project.settings.crawl.max_sitemap_urls)
                elif wp.method == "links":
                    found = read_links(fetcher, wp)
                else:
                    found = read_page_hash(fetcher, wp)
            except (RobotsDisallowed, httpx.HTTPError, ET.ParseError, OSError) as e:
                result.errors.append(f"{org.id}: {key}: {describe_error(e)}")
                continue
            result.checked[key] = today
            start, seen_before = len(result.items), len(result.seen_updates)
            if first_check:  # newest first, so the first-check cap keeps the most recent pages
                found.sort(key=lambda f: f.published or dt.date.min, reverse=True)
            _collect(
                org,
                wp,
                found,
                seen,
                queued_urls,
                first_check,
                today,
                lookback_days,
                result,
                project.settings.crawl.first_run_max_items,
            )
            baseline = sum(1 for r in result.seen_updates[seen_before:] if r.outcome == "skipped")
            result.per_point.append(
                f"{org.id}: {wp.url} ({wp.method}): {len(found)} listed, "
                f"{len(result.items) - start} new, {baseline} baseline"
            )
            if wp.method == "sitemap":
                _add_previews(fetcher, project, result.items[start:], result.errors)
    return result


def _add_previews(fetcher: Fetcher, project: Project, items: list[Item], errors: list[str]) -> None:
    """Sitemaps list URLs only: read each new page's title and opening text, so the keyword filter
    and stage 1 see more than the URL. Like an RSS summary, the text is held in memory only."""
    s = project.settings
    for item in [i for i in items if not i.title and not i.feed_summary][: s.crawl.max_page_previews]:
        try:
            resp = fetcher.get(item.url)
        except (RobotsDisallowed, httpx.HTTPError) as e:
            errors.append(f"{item.org_id}: {item.url}: preview failed: {describe_error(e)}")
            continue
        item.title = page_title(resp.text)
        text = " ".join(extract_text(resp.text, item.url).split())
        item.feed_summary = text[: s.limits.stage1_max_input_chars] or None


def _collect(
    org: Organisation,
    wp: WatchPoint,
    found: list[Found],
    seen: dict[str, SeenRecord],
    queued_urls: set[str],
    first_check: bool,
    today: dt.date,
    lookback_days: int,
    result: Detection,
    first_run_max_items: int,
) -> None:
    cutoff = today - dt.timedelta(days=lookback_days)
    taken = 0
    for f in found:
        url = normalise_url(f.url)
        if not on_domains(url, org.domains) or url in queued_urls:
            continue
        prev = seen.get(url)
        changed = prev is not None and (
            (f.lastmod is not None and prev.lastmod is not None and f.lastmod > prev.lastmod)
            or (f.content_hash is not None and prev.content_hash not in (None, f.content_hash))
        )
        if prev is not None and not changed:
            continue
        record = SeenRecord(
            url=url,
            org_id=org.id,
            doc_type=wp.type,
            detected=today,
            lastmod=f.lastmod,
            content_hash=f.content_hash,
        )
        baseline = first_check and wp.method != "page-hash" and (f.published is None or f.published < cutoff)
        # A site that refreshes every page's lastmod would otherwise flood the first run.
        baseline = baseline or (first_check and taken >= first_run_max_items)
        if baseline:
            record.outcome = "skipped"
            result.seen_updates.append(record)
            continue
        # A page seen before keeps sha1(url) for its first record; later versions get their own id.
        version = f.lastmod or f.content_hash
        iid = item_id(url) if prev is None or not version else item_id(f"{url}|{version}")
        result.items.append(
            Item(
                id=iid,
                org_id=org.id,
                url=url,
                title=f.title,
                published=f.published,
                detected=today,
                doc_type=wp.type,
                language=wp.language,
                lastmod=f.lastmod,
                content_hash=f.content_hash,
                feed_summary=f.summary,
            )
        )
        result.seen_updates.append(record)
        queued_urls.add(url)
        taken += 1
