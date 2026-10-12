"""Keyword filter without an LLM (SPEC.md §4.2)."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import unquote, urlsplit

from .config import FILTER_BYPASS_TYPES, Keywords
from .models import Item


def normalise_text(text: str) -> str:
    """NFKC (full-width → half-width etc.), case-folded, separators collapsed to single spaces."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"[\s_\-/.]+", " ", text)
    return f" {text.strip()} "


def _norm_term(term: str) -> str:
    return normalise_text(term).strip()


def _contains(haystack: str, term: str) -> bool:
    """Whole-word match for terms in Latin script; plain substring otherwise (e.g. Japanese)."""
    t = _norm_term(term)
    if not t:
        return False
    if t.isascii():
        return f" {t} " in haystack
    return t in haystack


def match_text(item: Item) -> str:
    path = unquote(urlsplit(item.url).path)
    parts = [item.title, path, item.feed_summary or ""]
    return normalise_text(" ".join(parts))


def passes(item: Item, keywords: Keywords) -> bool:
    if item.doc_type in FILTER_BYPASS_TYPES:
        return True
    text = match_text(item)
    if any(_contains(text, t) for t in keywords.exclude.all()):
        return False
    return any(_contains(text, t) for t in keywords.include.all())
