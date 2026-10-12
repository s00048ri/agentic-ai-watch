"""Suggest watch points for an organisation's home page (SPEC.md §4.6). No LLM; nothing is written."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
import yaml

from .fetch import Fetcher, RobotsDisallowed

FEED_TYPES = {"application/rss+xml", "application/atom+xml"}


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.feeds: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        if "alternate" in a.get("rel", "").lower().split() and a.get("type", "").lower() in FEED_TYPES:
            self.feeds.append((a.get("href", ""), a.get("title", "")))


def discover(fetcher: Fetcher, home_url: str) -> list[dict[str, object]]:
    """Return watch point candidates (feeds and sitemaps) for a person to check and paste."""
    suggestions: list[dict[str, object]] = []
    parts = urlsplit(home_url)
    origin = f"{parts.scheme}://{parts.netloc}"

    try:
        resp = fetcher.get(home_url)
        parser = _LinkParser()
        parser.feed(resp.text)
        for href, title in parser.feeds:
            suggestions.append(
                {
                    "type": "newsroom",
                    "url": home_url,
                    "method": "rss",
                    "feed_url": urljoin(home_url, href),
                    "language": _lang(resp.text),
                    "frequency": "weekly",
                    "verified": False,
                    "note": f"<link rel=alternate> {title}".strip(),
                }
            )
    except (RobotsDisallowed, httpx.HTTPError) as e:
        suggestions.append({"error": f"home page: {type(e).__name__}: {e}"})

    sitemaps = fetcher.sitemaps_from_robots(home_url)
    note = "robots.txt Sitemap:"
    if not sitemaps:
        candidate = origin + "/sitemap.xml"
        try:
            fetcher.get(candidate)
            sitemaps, note = [candidate], "/sitemap.xml exists"
        except (RobotsDisallowed, httpx.HTTPError):
            pass
    for sm in sitemaps:
        suggestions.append(
            {
                "type": "newsroom",
                "url": home_url,
                "method": "sitemap",
                "feed_url": sm,
                "language": "en",
                "frequency": "weekly",
                "verified": False,
                "note": f"{note} — set url to the section to watch (e.g. the news path)",
            }
        )
    return suggestions


def _lang(html_text: str) -> str:
    m = re.search(r"<html[^>]*\blang=[\"']?([a-zA-Z]{2})", html_text)
    return m.group(1).lower() if m else "en"


def as_yaml(suggestions: list[dict[str, object]]) -> str:
    return yaml.safe_dump({"watch_points": suggestions}, sort_keys=False, allow_unicode=True)
