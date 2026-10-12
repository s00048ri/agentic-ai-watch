import datetime as dt

from agentic_watch.config import Keywords, KeywordSet
from agentic_watch.filter import passes
from agentic_watch.models import Item
from agentic_watch.urls import normalise_url, on_domains
from agentic_watch.verify import evidence_in_text

KW = Keywords(
    include=KeywordSet(
        en=["agentic", "ai agent", "ai agents", "multi-agent"], ja=["AIエージェント", "AI agent"]
    ),
    exclude=KeywordSet(en=["travel agent", "user agent"], ja=["代理店"]),
)


def item(
    title: str,
    url: str = "https://example.com/news/x",
    doc_type: str = "newsroom",
    summary: str | None = None,
) -> Item:
    return Item(
        id="x",
        org_id="o",
        url=url,
        title=title,
        detected=dt.date(2026, 10, 19),
        doc_type=doc_type,
        feed_summary=summary,
    )


# ---------------------------------------------------------------- URL normalisation and de-duplication
def test_normalise_drops_tracking_fragment_and_trailing_slash() -> None:
    a = normalise_url("HTTPS://Example.com/News/Item/?utm_source=rss&utm_medium=x&id=2&fbclid=abc#top")
    assert a == "https://example.com/News/Item?id=2"
    assert normalise_url("https://example.com/news/item/") == normalise_url("https://example.com/news/item")
    assert normalise_url("https://example.com/") == "https://example.com/"


def test_normalise_sorts_query_and_drops_default_port() -> None:
    assert normalise_url("https://example.com:443/a?b=2&a=1") == "https://example.com/a?a=1&b=2"


def test_on_domains_accepts_subdomains_only() -> None:
    assert on_domains("https://www.example.com/a", ["example.com"])
    assert on_domains("https://news.example.com/a", ["example.com"])
    assert not on_domains("https://example.com.evil.net/a", ["example.com"])
    assert not on_domains("https://notexample.com/a", ["example.com"])


# ---------------------------------------------------------------- keyword filter
def test_include_terms_match_title_and_url_path() -> None:
    assert passes(item("We deploy AI agents in our call centre"), KW)
    assert passes(item("News", url="https://example.com/news/agentic-ai-roadmap"), KW)
    assert passes(item("Notes", summary="A multi-agent system for planning"), KW)
    assert not passes(item("Quarterly results"), KW)


def test_whole_words_for_latin_script() -> None:
    assert not passes(item("Agentics and logistics"), KW)  # "agentics" is not "agentic"
    assert passes(item("Our agentic future"), KW)


def test_exclude_terms_win() -> None:
    assert not passes(item("New AI agent for our travel agent network"), KW)
    assert not passes(item("AIエージェントを代理店向けに提供"), KW)


def test_full_width_and_case_are_normalised() -> None:
    assert passes(item("ＡＩエージェントを導入"), KW)
    assert passes(item("ＡＩ　ＡＧＥＮＴ pilot"), KW)


def test_governance_watch_points_bypass_the_filter() -> None:
    assert passes(item("Annual report 2026", doc_type="responsible-ai-report"), KW)
    assert passes(item("Policy", doc_type="ai-governance-page"), KW)


# ---------------------------------------------------------------- evidence verification
def test_evidence_found_with_whitespace_and_quote_differences() -> None:
    text = "The agent drafts replies.\n\nA member of staff  approves every reply — before it is “sent”."
    assert evidence_in_text('a member of staff approves every reply - before it is "sent"'.capitalize(), text)


def test_evidence_missing_or_paraphrased_fails() -> None:
    text = "The agent drafts replies and staff approve them."
    assert not evidence_in_text("Staff approve every reply before sending.", text)
    assert not evidence_in_text("", text)
