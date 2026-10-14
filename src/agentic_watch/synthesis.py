"""Monthly note from reviewed records (SPEC.md §4.8). Structured data only; no page text is sent."""

from __future__ import annotations

import json
from typing import Any

from .llm import Budget, BudgetExceeded, estimate_tokens, system_prompt, usage_record
from .models import Finding
from .pipeline import Context

INCLUDED = {"kept", "blog_candidate"}


def _finding(f: Finding) -> dict[str, Any]:
    return {"description": f.description, "category": f.current_category() or "no existing category"}


def synthesis_payload(ctx: Context, month: str) -> list[dict[str, Any]]:
    rows = []
    for e in ctx.store.entries(month):
        if e.status not in INCLUDED:
            continue
        rows.append(
            {
                "org_id": e.org_id,
                "url": e.url,
                "title": e.title,
                "published": e.published.isoformat() if e.published else None,
                "label": e.relevance.label,
                "summary": e.summary,
                "evidence": e.evidence,
                "fields": e.fields.model_dump(),
                "challenges": [_finding(f) for f in e.challenges],
                "responses": [_finding(f) for f in e.responses],
                "status": e.status,
            }
        )
    return rows


def synthesize(ctx: Context, month: str) -> str:
    assert ctx.client is not None
    rows = synthesis_payload(ctx, month)
    if not rows:
        return f"No `kept` or `blog_candidate` records for {month}; nothing sent."
    s = ctx.settings
    params: dict[str, Any] = {
        "model": s.models.synthesis,
        "max_tokens": s.limits.synthesis_max_output_tokens,
        "system": [{"type": "text", "text": system_prompt(ctx.project, "synthesis")}],
        "messages": [{"role": "user", "content": json.dumps(rows, ensure_ascii=False, sort_keys=True)}],
        "thinking": {"type": s.thinking.synthesis},
        "output_config": {"effort": s.effort.synthesis},
    }
    budget = Budget(ctx.project, ctx.store, ctx.today)
    est = estimate_tokens(params)
    try:
        budget.reserve(est)
    except BudgetExceeded as e:
        ctx.notify(f"Agentic AI Watch monthly synthesis not run: {e}")
        raise
    message = ctx.client.create(params)
    ctx.store.record_usage([usage_record(message, f"synthesis-{month}", "synthesis", batch=False)])
    text = "".join(b.text for b in message.content if b.type == "text")
    if message.stop_reason == "max_tokens":
        text += "\n\n_(Output reached the token limit and may be incomplete.)_\n"
    header = (
        f"# Monthly note {month}\n\nInternal use only. Based on {len(rows)} reviewed records; "
        "in what has been published by the organisations themselves.\n\n"
    )
    path = ctx.project.path("reports/monthly", f"{month}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + text.strip() + "\n", encoding="utf-8")
    return str(path)
