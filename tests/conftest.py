"""Shared fixtures: a temporary project, a fake website and a fake LLM client. No network, no API key."""

from __future__ import annotations

import datetime as dt
import json
import shutil
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from anthropic.types import Message

from agentic_watch.config import load_project
from agentic_watch.fetch import Fetcher
from agentic_watch.pipeline import Context
from agentic_watch.store import Store

REPO = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 10, 19)

ARTICLE_BODY = (
    "Example Org today announced that its customer service team now works with an AI agent. "
    "The agent drafts replies to customer enquiries and a member of staff approves every reply before it is sent. "
    "The pilot runs with Partner Co. and will be reviewed after three months. "
    "UNIQUE-BODY-MARKER this sentence must never be written to disk by the pipeline."
)
EVIDENCE = "a member of staff approves every reply before it is sent"

ORG = {
    "id": "example-org",
    "name": "Example Org",
    "domains": ["example.com"],
    "org_type": "private-sector",
    "ecosystem_role": "deployer",
    "sector": "telecommunications",
    "region": "asia-pacific",
    "size": "large",
    "inclusion_reason": "phase2-interviewee",
    "status": "active",
    "added": "2026-10-13",
    "last_reviewed": "2026-10-13",
    "watch_points": [
        {
            "type": "newsroom",
            "url": "https://example.com/news",
            "method": "rss",
            "feed_url": "https://example.com/news/rss.xml",
            "language": "en",
            "frequency": "weekly",
            "verified": True,
        }
    ],
}


def rss(items: list[tuple[str, str, str]]) -> str:
    entries = "".join(
        f"<item><title>{t}</title><link>{link}</link><pubDate>{d}</pubDate>"
        f"<description>{t} — short teaser.</description></item>"
        for t, link, d in items
    )
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>News</title>{entries}</channel></rss>'


def article(title: str, body: str = ARTICLE_BODY) -> str:
    return f"<html><head><title>{title}</title></head><body><article><h1>{title}</h1><p>{body}</p></article></body></html>"


DEFAULT_ITEMS = [
    (
        "Example Org pilots an AI agent for customer service",
        "https://example.com/news/agent-pilot",
        "Mon, 12 Oct 2026 09:00:00 GMT",
    ),
    ("Quarterly results", "https://example.com/news/results?utm_source=rss", "Mon, 12 Oct 2026 09:00:00 GMT"),
    ("Our travel agent partnership", "https://example.com/news/travel", "Mon, 12 Oct 2026 09:00:00 GMT"),
    ("AI agents at a partner", "https://other.example.net/agents", "Mon, 12 Oct 2026 09:00:00 GMT"),
]


@dataclass
class FakeSite:
    """Routes for httpx.MockTransport. `log` records every requested URL."""

    pages: dict[str, tuple[int, str]] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.log.append(url)
        status, body = self.pages.get(url, (404, "not found"))
        return httpx.Response(status, text=body, request=request)


def default_site(items: list[tuple[str, str, str]] | None = None) -> FakeSite:
    site = FakeSite()
    site.pages["https://example.com/robots.txt"] = (200, "User-agent: *\nDisallow: /private\n")
    site.pages["https://example.com/news/rss.xml"] = (200, rss(items or DEFAULT_ITEMS))
    site.pages["https://example.com/news/agent-pilot"] = (200, article("Example Org pilots an AI agent"))
    site.pages["https://example.com/news/results"] = (200, article("Quarterly results", "Revenue rose."))
    return site


def message(payload: dict[str, Any], model: str = "claude-haiku-5-5", stop: str = "end_turn") -> Message:
    return Message.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": json.dumps(payload)}],
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 300,
                "output_tokens": 40,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        }
    )


STAGE1_OK = {"label": "deployment", "confidence": 0.9}
STAGE2_OK = {
    "summary": "Example Org pilots an AI agent that drafts customer service replies for staff approval.",
    "evidence": EVIDENCE,
    "fields": {
        "sector": "telecommunications",
        "application_area": "customer-support",
        "deployment_stage": "pilot",
        "autonomy_cues": "drafts replies",
        "human_oversight": "staff approve every reply",
    },
    "challenges": ["human-oversight-over-reliance", "not-a-real-id"],
    "responses": ["calibrated-human-oversight"],
    "other_organisations_named": ["Partner Co.", "Example Org"],
}


class FakeLLM:
    """Implements the LLMClient protocol. Stage is recognised from the system prompt."""

    def __init__(
        self,
        stage1: Callable[[dict[str, Any]], Message] | None = None,
        stage2: Callable[[dict[str, Any]], Message] | None = None,
    ) -> None:
        self.stage1 = stage1 or (lambda p: message(STAGE1_OK))
        self.stage2 = stage2 or (lambda p: message(STAGE2_OK))
        self.calls: list[dict[str, Any]] = []
        self.batches: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        self.batch_state: dict[str, str] = {}

    def _respond(self, params: dict[str, Any]) -> Message:
        self.calls.append(params)
        system = params["system"][0]["text"]
        return self.stage1(params) if "screen pages" in system else self.stage2(params)

    def create(self, params: dict[str, Any]) -> Message:
        return self._respond(params)

    def create_batch(self, requests: list[tuple[str, dict[str, Any]]]) -> str:
        bid = f"msgbatch_{len(self.batches) + 1}"
        self.batches[bid] = requests
        self.batch_state[bid] = "in_progress"
        return bid

    def batch_status(self, batch_id: str) -> str:
        return self.batch_state[batch_id]

    def batch_results(self, batch_id: str) -> Iterator[Any]:
        for cid, params in self.batches[batch_id]:
            msg = self._respond(params)
            yield type(
                "R", (), {"custom_id": cid, "result": type("S", (), {"type": "succeeded", "message": msg})()}
            )()


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    for d in ("config", "prompts"):
        shutil.copytree(REPO / d, tmp_path / d)
    for d in ("ledger", "state", "data/entries", "reports/weekly", "reports/monthly"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / "ledger/organisations.yaml").write_text(yaml.safe_dump([ORG]))
    (tmp_path / "ledger/candidates.yaml").write_text("[]\n")
    (tmp_path / "data/gold.yaml").write_text("[]\n")
    settings = yaml.safe_load((tmp_path / "config/settings.yaml").read_text())
    settings["crawl"]["user_agent"] = "test-agent/0.1 (+contact: test@example.org)"
    (tmp_path / "config/settings.yaml").write_text(yaml.safe_dump(settings))
    return tmp_path


def make_ctx(
    root: Path,
    site: FakeSite | None = None,
    llm: FakeLLM | None = None,
    use_batch: bool = False,
    today: dt.date = TODAY,
) -> tuple[Context, FakeSite, list[str]]:
    site = site or default_site()
    project = load_project(root)
    project.settings.use_batch = use_batch
    client = httpx.Client(transport=httpx.MockTransport(site.handler))
    fetcher = Fetcher(project.settings.crawl, client=client, sleep=lambda s: None)
    notes: list[str] = []
    ctx = Context(
        project=project,
        store=Store(root),
        fetcher=fetcher,
        client=llm,
        today=today,
        notify=notes.append,
    )
    return ctx, site, notes
