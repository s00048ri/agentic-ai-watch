import datetime as dt

import httpx
import pytest

from agentic_watch.config import Crawl
from agentic_watch.fetch import Fetcher, RobotsDisallowed, detect
from agentic_watch.models import SeenRecord

from .conftest import TODAY, FakeSite, default_site, make_ctx


def test_detect_keeps_only_own_domain_and_normalises(project_root):
    ctx, _site, _ = make_ctx(project_root)
    det = detect(ctx.project, ctx.store, ctx.fetcher, TODAY, lookback_days=30)
    urls = [i.url for i in det.items]
    assert "https://example.com/news/agent-pilot" in urls
    assert "https://example.com/news/results" in urls  # tracking parameter removed
    assert not any("other.example.net" in u for u in urls)  # another domain is never recorded


def test_seen_urls_are_not_detected_again(project_root):
    ctx, _site, _ = make_ctx(project_root)
    ctx.store.mark_seen(
        [SeenRecord(url="https://example.com/news/agent-pilot", org_id="example-org", detected=TODAY)]
    )
    det = detect(ctx.project, ctx.store, ctx.fetcher, TODAY, lookback_days=30)
    assert "https://example.com/news/agent-pilot" not in [i.url for i in det.items]


def test_first_check_records_old_items_as_baseline(project_root):
    old = [("AI agents in 2025", "https://example.com/news/old", "Mon, 06 Jan 2025 09:00:00 GMT")]
    ctx, _site, _ = make_ctx(project_root, site=default_site(old))
    det = detect(ctx.project, ctx.store, ctx.fetcher, TODAY, lookback_days=30)
    assert det.items == []
    assert [r.outcome for r in det.seen_updates] == ["skipped"]


def test_robots_disallow_is_respected(project_root):
    site = default_site()
    site.pages["https://example.com/robots.txt"] = (200, "User-agent: *\nDisallow: /news\n")
    ctx, site, _ = make_ctx(project_root, site=site)
    det = detect(ctx.project, ctx.store, ctx.fetcher, TODAY, lookback_days=30)
    assert det.items == []
    assert any("robots.txt" in e for e in det.errors)
    assert "https://example.com/news/rss.xml" not in site.log  # never requested


def test_robots_server_error_means_stay_away():
    site = FakeSite(pages={"https://example.com/robots.txt": (503, "down")})
    f = Fetcher(
        Crawl(user_agent="t"),
        client=httpx.Client(transport=httpx.MockTransport(site.handler)),
        sleep=lambda s: None,
    )
    with pytest.raises(RobotsDisallowed):
        f.get("https://example.com/page")


def test_requests_to_one_host_are_spaced():
    site = FakeSite(
        pages={
            "https://example.com/robots.txt": (404, ""),
            "https://example.com/a": (200, "a"),
            "https://example.com/b": (200, "b"),
        }
    )
    now = [0.0]
    sleeps: list[float] = []

    def sleep(s: float) -> None:
        sleeps.append(s)
        now[0] += s

    f = Fetcher(
        Crawl(user_agent="t", min_interval_seconds_per_domain=1.0),
        client=httpx.Client(transport=httpx.MockTransport(site.handler)),
        sleep=sleep,
        clock=lambda: now[0],
    )
    f.get("https://example.com/a")
    f.get("https://example.com/b")
    assert len(sleeps) == 2 and all(s == pytest.approx(1.0) for s in sleeps)  # robots → a → b


def test_user_agent_is_sent(project_root):
    seen_headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers["user-agent"])
        return httpx.Response(404, request=request)

    f = Fetcher(
        Crawl(user_agent="ua-under-test"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
    )
    f.allowed("https://example.com/x")
    assert seen_headers == ["ua-under-test"]


def test_sitemap_scope_and_lastmod_updates(project_root):
    import yaml

    orgs = yaml.safe_load((project_root / "ledger/organisations.yaml").read_text())
    orgs[0]["watch_points"] = [
        {
            "type": "newsroom",
            "url": "https://example.com/news",
            "method": "sitemap",
            "feed_url": "https://example.com/sitemap.xml",
        }
    ]
    (project_root / "ledger/organisations.yaml").write_text(yaml.safe_dump(orgs))
    sitemap = (
        '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://example.com/news/agentic-launch</loc><lastmod>2026-10-15</lastmod></url>"
        "<url><loc>https://example.com/careers/jobs</loc><lastmod>2026-10-15</lastmod></url>"
        "</urlset>"
    )
    site = FakeSite(
        pages={"https://example.com/robots.txt": (404, ""), "https://example.com/sitemap.xml": (200, sitemap)}
    )
    ctx, _, _ = make_ctx(project_root, site=site)
    det = detect(ctx.project, ctx.store, ctx.fetcher, TODAY, lookback_days=30)
    assert [i.url for i in det.items] == [
        "https://example.com/news/agentic-launch"
    ]  # /careers is out of scope
    assert det.per_point == ["example-org: https://example.com/news (sitemap): 1 listed, 1 new, 0 baseline"]
    ctx.store.mark_seen(det.seen_updates)
    ctx.store.save_checks(det.checked)
    site.pages["https://example.com/sitemap.xml"] = (200, sitemap.replace("2026-10-15", "2026-10-25"))
    later = detect(ctx.project, ctx.store, ctx.fetcher, TODAY + dt.timedelta(days=7), lookback_days=30)
    assert [i.url for i in later.items] == ["https://example.com/news/agentic-launch"]
    assert later.items[0].id != det.items[0].id  # a new version gets its own record id


def test_frequency_skips_watch_points_not_due(project_root):
    ctx, site, _ = make_ctx(project_root)
    ctx.store.save_checks({"https://example.com/news/rss.xml": TODAY - dt.timedelta(days=3)})
    orgs = ctx.project.organisations
    orgs[0].watch_points[0].frequency = "monthly"
    det = detect(ctx.project, ctx.store, ctx.fetcher, TODAY, lookback_days=30)
    assert det.items == [] and "https://example.com/news/rss.xml" not in site.log


def test_sitemap_items_get_a_page_preview_for_the_filter(project_root):
    import yaml

    from agentic_watch.filter import passes

    from .conftest import article

    orgs = yaml.safe_load((project_root / "ledger/organisations.yaml").read_text())
    orgs[0]["watch_points"] = [
        {
            "type": "newsroom",
            "url": "https://example.com/news",
            "method": "sitemap",
            "feed_url": "https://example.com/sitemap.xml",
        }
    ]
    (project_root / "ledger/organisations.yaml").write_text(yaml.safe_dump(orgs))
    sitemap = (
        '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://example.com/news/bank-scales-assistant</loc><lastmod>2026-10-15</lastmod></url>"
        "<url><loc>https://example.com/news/gone</loc><lastmod>2026-10-15</lastmod></url>"
        "</urlset>"
    )
    site = FakeSite(
        pages={
            "https://example.com/robots.txt": (404, ""),
            "https://example.com/sitemap.xml": (200, sitemap),
            "https://example.com/news/bank-scales-assistant": (200, article("A bank scales its assistant")),
        }
    )
    ctx, _, _ = make_ctx(project_root, site=site)
    det = detect(ctx.project, ctx.store, ctx.fetcher, TODAY, lookback_days=30)
    item, gone = det.items
    assert item.title == "A bank scales its assistant"
    assert item.feed_summary and "AI agent" in item.feed_summary  # only the page text names the topic
    assert passes(item, ctx.project.keywords)
    assert "UNIQUE-BODY-MARKER" not in item.model_dump_json()  # the preview is never serialised
    assert gone.title == "" and any("preview failed" in e for e in det.errors)


def _sitemap_org(project_root, feed_url="https://example.com/sitemap.xml"):
    import yaml

    orgs = yaml.safe_load((project_root / "ledger/organisations.yaml").read_text())
    orgs[0]["watch_points"] = [
        {"type": "newsroom", "url": "https://example.com/news", "method": "sitemap", "feed_url": feed_url}
    ]
    (project_root / "ledger/organisations.yaml").write_text(yaml.safe_dump(orgs))


def _urlset(n: int, day: str = "2026-10-15") -> str:
    rows = "".join(
        f"<url><loc>https://example.com/news/item-{i}</loc><lastmod>{day}</lastmod></url>" for i in range(n)
    )
    return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{rows}</urlset>'


def test_a_broken_child_sitemap_does_not_stop_the_others(project_root):
    _sitemap_org(project_root)
    index = (
        '<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<sitemap><loc>https://example.com/broken.xml</loc></sitemap>"
        "<sitemap><loc>https://example.com/news.xml</loc></sitemap></sitemapindex>"
    )
    site = FakeSite(
        pages={
            "https://example.com/robots.txt": (404, ""),
            "https://example.com/sitemap.xml": (200, index),
            "https://example.com/broken.xml": (500, "oops"),
            "https://example.com/news.xml": (200, _urlset(2)),
        }
    )
    ctx, _, _ = make_ctx(project_root, site=site)
    det = detect(ctx.project, ctx.store, ctx.fetcher, TODAY, lookback_days=30)
    assert len(det.items) == 2
    assert not [
        e for e in det.errors if "preview" not in e
    ]  # the article pages themselves are not in this fake site


def test_first_check_is_capped(project_root):
    _sitemap_org(project_root)
    site = FakeSite(
        pages={
            "https://example.com/robots.txt": (404, ""),
            "https://example.com/sitemap.xml": (200, _urlset(25)),
        }
    )
    ctx, _, _ = make_ctx(project_root, site=site)
    det = detect(ctx.project, ctx.store, ctx.fetcher, TODAY, lookback_days=30)
    assert len(det.items) == 20
    assert sum(1 for r in det.seen_updates if r.outcome == "skipped") == 5


def test_sitemap_paths_counts_prefixes():
    from agentic_watch.fetch import sitemap_paths

    site = FakeSite(
        pages={
            "https://example.com/robots.txt": (404, ""),
            "https://example.com/sitemap.xml": (200, _urlset(3)),
        }
    )
    f = Fetcher(
        Crawl(user_agent="t"),
        client=httpx.Client(transport=httpx.MockTransport(site.handler)),
        sleep=lambda s: None,
    )
    assert sitemap_paths(f, "https://example.com/", depth=1) == [("/news", 3)]


def test_links_watch_point_finds_new_article_links(project_root):
    import yaml

    orgs = yaml.safe_load((project_root / "ledger/organisations.yaml").read_text())
    orgs[0]["watch_points"] = [
        {"type": "newsroom", "url": "https://example.com/blog/", "method": "links", "scope": "/posts/"}
    ]
    (project_root / "ledger/organisations.yaml").write_text(yaml.safe_dump(orgs))

    def listing(slugs):
        links = "".join(f'<a href="/posts/{s}">{s}</a>' for s in slugs)
        return f'<html><body><a href="/about">About</a><a href="https://other.example.net/posts/x">x</a>{links}</body></html>'

    site = FakeSite(
        pages={
            "https://example.com/robots.txt": (404, ""),
            "https://example.com/blog/": (200, listing(["old-one", "old-two"])),
        }
    )
    ctx, _, _ = make_ctx(project_root, site=site)
    first = detect(ctx.project, ctx.store, ctx.fetcher, TODAY, lookback_days=30)
    assert first.items == [] and len(first.seen_updates) == 2  # undated links: baseline only
    ctx.store.mark_seen(first.seen_updates)
    ctx.store.save_checks(first.checked)
    site.pages["https://example.com/blog/"] = (200, listing(["new-agents", "old-one", "old-two"]))
    later = detect(ctx.project, ctx.store, ctx.fetcher, TODAY + dt.timedelta(days=7), lookback_days=30)
    assert [i.url for i in later.items] == ["https://example.com/posts/new-agents"]
