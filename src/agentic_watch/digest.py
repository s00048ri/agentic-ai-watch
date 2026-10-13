"""Weekly digest (SPEC.md §4.7): Markdown copy in reports/weekly/ and a Slack post."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from .config import Pricing, Project
from .models import Entry, RunState, UsageRecord

log = logging.getLogger(__name__)


def cost_usd(records: list[UsageRecord], pricing: Pricing) -> float:
    total = 0.0
    for r in records:
        price = pricing.models.get(r.model)
        if price is None:
            continue
        usd = (
            r.input_tokens * price.input
            + r.output_tokens * price.output
            + r.cache_creation_input_tokens * price.input * pricing.cache_write_multiplier
            + r.cache_read_input_tokens * price.input * pricing.cache_read_multiplier
        ) / 1_000_000
        total += usd * (pricing.batch_multiplier if r.batch else 1.0)
    return total


@dataclass
class DigestData:
    run: RunState
    new_entries: list[Entry]
    needs_review: list[Entry]
    run_usage: list[UsageRecord]
    month_tokens: int
    month_budget: int


def _org_name(project: Project, org_id: str) -> str:
    try:
        return project.organisation(org_id).name
    except KeyError:
        return org_id


def _tags(e: Entry) -> str:
    tags = [*e.challenges, *e.responses]
    return ", ".join(tags) if tags else "—"


def render_markdown(project: Project, d: DigestData) -> str:
    c = d.run.counts
    tokens = sum(u.total() for u in d.run_usage)
    cost = cost_usd(d.run_usage, project.settings.pricing)
    pct = 100 * d.month_tokens / d.month_budget if d.month_budget else 0
    out = [
        f"# Agentic AI Watch — weekly digest {d.run.run_id}",
        "",
        "Internal use only. Sources are the organisations' own websites.",
        "",
        "## Counts",
        f"Detected {c.detected} → keyword filter {c.keyword_passed} → stage 1 sent {c.stage1_sent}"
        f" → stage 1 passed {c.stage1_passed} → recorded {c.recorded}"
        f" (carried over to next run: {c.carried_over})",
        "",
        f"## New records ({len(d.new_entries)})",
    ]
    if not d.new_entries:
        out.append("None this week.")
    for e in d.new_entries:
        flag = "" if e.evidence_verified else " **[needs review: evidence not found in page]**"
        out += [
            f"- **{_org_name(project, e.org_id)}** — [{e.title or e.url}]({e.url}){flag}",
            f"  {e.summary}",
            f"  {e.relevance.label} ({e.relevance.confidence:.2f}) · challenges/responses: {_tags(e)}",
        ]
    out += ["", f"## Needs review ({len(d.needs_review)})"]
    out += [f"- {_org_name(project, e.org_id)} — [{e.title or e.url}]({e.url})" for e in d.needs_review] or [
        "None."
    ]
    if d.run.unreadable_pages:
        out += [
            "",
            f"## Relevant pages that could not be read — check by hand ({len(d.run.unreadable_pages)})",
        ]
        out += [f"- {line}" for line in d.run.unreadable_pages]
    out += ["", "## Candidate organisations with new mentions"]
    out += [f"- {line}" for line in d.run.candidate_changes] or ["None."]
    out += ["", "## Watch points to check by hand"]
    out += [f"- {line}" for line in d.run.manual_watch_points] or ["None."]
    if d.run.fetch_errors:
        out += ["", "## Fetch and processing errors"]
        out += [f"- {line}" for line in d.run.fetch_errors]
    out += [
        "",
        "## Token use",
        f"This run: {tokens:,} tokens, estimated ${cost:.2f}.",
        f"This month: {d.month_tokens:,} of {d.month_budget:,} tokens ({pct:.0f}% of the monthly budget).",
    ]
    if d.run.budget_stopped:
        out.append("**LLM calls were stopped this run because the monthly budget was reached.**")
    return "\n".join(out) + "\n"


def render_slack(project: Project, d: DigestData) -> str:
    c = d.run.counts
    tokens = sum(u.total() for u in d.run_usage)
    cost = cost_usd(d.run_usage, project.settings.pricing)
    pct = 100 * d.month_tokens / d.month_budget if d.month_budget else 0
    lines = [
        f"*Agentic AI Watch — weekly digest {d.run.run_id}*",
        f"Detected {c.detected} → keywords {c.keyword_passed} → stage 1 {c.stage1_passed}/{c.stage1_sent}"
        f" → recorded {c.recorded} (carried over {c.carried_over})",
        "",
        f"*New records ({len(d.new_entries)})*",
    ]
    for e in d.new_entries:
        flag = " :warning: needs review" if not e.evidence_verified else ""
        lines.append(
            f"• *{_org_name(project, e.org_id)}* <{e.url}|{(e.title or e.url)[:120]}>{flag}\n"
            f"   {e.summary}\n   _{e.relevance.label} ({e.relevance.confidence:.2f}) · {_tags(e)}_"
        )
    if not d.new_entries:
        lines.append("None this week.")
    lines += ["", f"*Needs review:* {len(d.needs_review)}"]
    if d.run.unreadable_pages:
        lines.append(
            f"*Pages to read by hand:* {len(d.run.unreadable_pages)} passed stage 1 but could not be read"
            " (see the weekly report)"
        )
    if d.run.candidate_changes:
        lines.append("*Candidates:* " + "; ".join(d.run.candidate_changes[:20]))
    if d.run.manual_watch_points:
        lines.append(
            f"*Check by hand:* {len(d.run.manual_watch_points)} watch points (see the weekly report)"
        )
    if d.run.fetch_errors:
        lines.append(f"*Errors:* {len(d.run.fetch_errors)} (see the weekly report)")
    lines.append(
        f"*Tokens:* {tokens:,} this run (≈${cost:.2f}); month {d.month_tokens:,} / {d.month_budget:,} ({pct:.0f}%)"
    )
    if d.run.budget_stopped:
        lines.append(":octagonal_sign: LLM calls stopped: monthly token budget reached.")
    return "\n".join(lines)


def post_slack(webhook_url: str | None, text: str) -> bool:
    """Post to the Slack Incoming Webhook. Returns False (and logs) when no webhook is configured."""
    if not webhook_url:
        log.info("SLACK_WEBHOOK_URL not set; Slack post skipped")
        return False
    resp = httpx.post(webhook_url, json={"text": text}, timeout=20)
    resp.raise_for_status()
    return True
