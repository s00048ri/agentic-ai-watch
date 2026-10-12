import httpx
import yaml

from agentic_watch.config import Crawl
from agentic_watch.discover import discover
from agentic_watch.evaluate import evaluate, pilot_summary
from agentic_watch.fetch import Fetcher
from agentic_watch.llm import stage2_schema, system_prompt
from agentic_watch.pipeline import start_run
from agentic_watch.synthesis import synthesize

from .conftest import FakeLLM, FakeSite, make_ctx, message


def test_extraction_prompt_lists_taxonomy_from_config(project_root):
    ctx, _, _ = make_ctx(project_root)
    text = system_prompt(ctx.project, "stage2")
    assert "{{TAXONOMY}}" not in text
    for c in ctx.project.taxonomy.challenges:
        assert c.id in text
    schema = stage2_schema(ctx.project)
    assert set(schema["properties"]["challenges"]["items"]["enum"]) == ctx.project.taxonomy.challenge_ids()


def test_discover_suggests_feeds_and_sitemaps():
    home = '<html lang="ja"><head><link rel="alternate" type="application/rss+xml" title="News" href="/news/rss.xml"></head></html>'
    site = FakeSite(
        pages={
            "https://example.com/robots.txt": (
                200,
                "User-agent: *\nAllow: /\nSitemap: https://example.com/sitemap_index.xml\n",
            ),
            "https://example.com/": (200, home),
        }
    )
    f = Fetcher(
        Crawl(user_agent="t"),
        client=httpx.Client(transport=httpx.MockTransport(site.handler)),
        sleep=lambda s: None,
    )
    out = discover(f, "https://example.com/")
    assert {"method": "rss", "feed_url": "https://example.com/news/rss.xml", "language": "ja"}.items() <= out[
        0
    ].items()
    assert out[1]["method"] == "sitemap" and out[1]["feed_url"] == "https://example.com/sitemap_index.xml"


def test_evaluate_reports_precision_and_recall(project_root):
    gold = [
        {"url": "https://example.com/news/agent-pilot", "label": "deployment", "relevant": True},
        {"url": "https://example.com/news/results", "label": "not-relevant", "relevant": False},
    ]
    (project_root / "data/gold.yaml").write_text(yaml.safe_dump(gold))

    def judge(params):
        rel = "agent" in params["messages"][0]["content"].lower()
        return message({"label": "deployment" if rel else "not-relevant", "confidence": 0.9})

    ctx, _, _ = make_ctx(project_root, llm=FakeLLM(stage1=judge))
    result = evaluate(ctx)
    assert result.precision == 1.0 and result.recall == 1.0
    assert "| deployment | 1 |" in result.markdown()


def test_synthesis_sends_structured_records_only(project_root):
    llm = FakeLLM()
    ctx, _, _ = make_ctx(project_root, llm=llm)
    start_run(ctx)
    path = project_root / "data/entries/2026-10.jsonl"
    path.write_text(path.read_text().replace('"status":"new"', '"status":"kept"'))
    llm.stage2 = lambda p: message({"x": 1}, model="claude-sonnet-5-5")
    ctx.client = llm
    out = synthesize(ctx, "2026-10")
    sent = llm.calls[-1]
    assert sent["model"] == "claude-sonnet-5-5" and sent["thinking"] == {"type": "between_tools"}
    assert "UNIQUE-BODY-MARKER" not in sent["messages"][0]["content"]
    assert out.endswith("reports/monthly/2026-10.md")


def test_pilot_summary_counts_by_watch_point_type(project_root):
    ctx, _, _ = make_ctx(project_root, llm=FakeLLM())
    start_run(ctx)
    text = pilot_summary(ctx)
    assert "| newsroom | 3 | 1 | 100% | 0 | — |" in text
