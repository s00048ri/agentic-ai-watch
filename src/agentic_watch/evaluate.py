"""Accuracy check of stage 1 against data/gold.yaml, and the end-of-pilot summary (SPEC.md §8)."""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass

import httpx
from pydantic import BaseModel

from .config import load_yaml
from .fetch import RobotsDisallowed, extract_text, page_title
from .llm import (
    RELEVANCE_LABELS,
    BudgetExceeded,
    CallOutcome,
    build_params,
    call_with_retry,
    parse_stage1,
    stage1_input,
)
from .models import Item, Relevance, RelevanceLabel, item_id
from .pipeline import Context, _budget

USEFUL = {"kept", "blog_candidate"}


class GoldItem(BaseModel):
    url: str
    title: str = ""
    label: RelevanceLabel
    relevant: bool


@dataclass
class EvalResult:
    rows: list[tuple[GoldItem, str | None, float | None]]
    precision: float | None
    recall: float | None
    confusion: dict[str, Counter[str]]

    def markdown(self) -> str:
        def fmt(v: float | None) -> str:
            return "n/a" if v is None else f"{v:.2f}"

        out = [
            "# Stage 1 evaluation",
            "",
            f"Items: {len(self.rows)} · precision {fmt(self.precision)} · recall {fmt(self.recall)}",
            "",
            "Relevant = label other than not-relevant with confidence at or above the threshold.",
            "",
            "## Confusion matrix (rows: gold, columns: predicted)",
            "",
            "| gold \\ predicted | " + " | ".join(RELEVANCE_LABELS) + " | error |",
            "|---" * (len(RELEVANCE_LABELS) + 2) + "|",
        ]
        for gold in RELEVANCE_LABELS:
            row = self.confusion.get(gold, Counter())
            out.append(
                f"| {gold} | " + " | ".join(str(row[p]) for p in RELEVANCE_LABELS) + f" | {row['error']} |"
            )
        return "\n".join(out) + "\n"


def evaluate(ctx: Context) -> EvalResult:
    assert ctx.client is not None
    gold = [GoldItem.model_validate(g) for g in (load_yaml(ctx.project.path("data/gold.yaml")) or [])]
    threshold = ctx.settings.stage1.threshold
    budget = _budget(ctx)
    rows: list[tuple[GoldItem, str | None, float | None]] = []
    usage = []
    try:
        for g in gold:
            item = Item(
                id=item_id(g.url),
                org_id="gold",
                url=g.url,
                title=g.title,
                detected=ctx.today,
                doc_type="gold",
            )
            try:
                resp = ctx.fetcher.get(g.url)
                item.title = item.title or page_title(resp.text)
                text = extract_text(resp.text, g.url)
            except (RobotsDisallowed, httpx.HTTPError):
                rows.append((g, None, None))
                continue
            params = build_params(
                ctx.project, "stage1", stage1_input(item, text, ctx.settings.limits.stage1_max_input_chars)
            )
            out: CallOutcome[Relevance] = call_with_retry(
                ctx.client, params, parse_stage1, budget, f"evaluate-{ctx.today}", "evaluate", item
            )
            usage += out.usage
            rows.append(
                (g, out.value.label if out.value else None, out.value.confidence if out.value else None)
            )
    except BudgetExceeded as e:
        ctx.notify(f"Agentic AI Watch evaluate stopped: {e}")
    finally:
        ctx.store.record_usage(usage)

    tp = fp = fn = 0
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    for g, label, conf in rows:
        confusion[g.label][label or "error"] += 1
        predicted = label is not None and label != "not-relevant" and (conf or 0) >= threshold
        tp += predicted and g.relevant
        fp += predicted and not g.relevant
        fn += (not predicted) and g.relevant
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return EvalResult(rows, precision, recall, dict(confusion))


def pilot_summary(ctx: Context) -> str:
    """Per watch point type: new detections, records, share with challenges/responses, tokens per useful record."""
    store = ctx.store
    seen = store.seen().values()
    entries = store.entries()
    usage = store.usage()
    types = sorted({s.doc_type or "unknown" for s in seen} | {e.doc_type for e in entries})
    out = [
        f"# Pilot summary ({dt.date.today().isoformat()})",
        "",
        "| Watch point type | New detections | Records | With challenges or responses | Useful records | Tokens per useful record |",
        "|---|---|---|---|---|---|",
    ]
    for t in types:
        detections = sum(1 for s in seen if (s.doc_type or "unknown") == t and s.outcome != "skipped")
        recs = [e for e in entries if e.doc_type == t]
        with_tax = sum(1 for e in recs if e.findings())
        useful = sum(1 for e in recs if e.status in USEFUL)
        tokens = sum(u.total() for u in usage if u.doc_type == t)
        share = f"{100 * with_tax / len(recs):.0f}%" if recs else "—"
        per = f"{tokens / useful:,.0f}" if useful else "—"
        out.append(f"| {t} | {detections} | {len(recs)} | {share} | {useful} | {per} |")
    out += [
        "",
        "Useful records are those reviewed as `kept` or `blog_candidate`. "
        "New detections exclude items recorded as a baseline on a watch point's first check.",
    ]
    return "\n".join(out) + "\n"
