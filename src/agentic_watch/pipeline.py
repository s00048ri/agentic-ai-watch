"""Weekly run and batch collection (SPEC.md §4, §5).

Sync mode (`use_batch: false`): detection, both stages and the digest in one process.
Batch mode: `run` detects and submits stage 1; `collect` (every two hours) collects results,
submits stage 2, collects it, and posts the digest when nothing is pending.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import digest as digest_mod
from .config import Project
from .fetch import Detection, Fetcher, RobotsDisallowed, detect, extract_text, page_title
from .filter import passes
from .leads import update_candidates
from .llm import (
    Budget,
    BudgetExceeded,
    CallOutcome,
    LLMClient,
    ParseError,
    build_params,
    call_with_retry,
    estimate_tokens,
    parse_stage1,
    parse_stage2,
    stage1_input,
    stage2_input,
    usage_record,
)
from .models import (
    BatchRecord,
    Entry,
    EntryModels,
    Extraction,
    Item,
    QueuedItem,
    Relevance,
    RunState,
    SeenRecord,
)
from .store import Store
from .verify import evidence_in_text

log = logging.getLogger(__name__)


class RunInProgress(Exception):
    pass


@dataclass
class Context:
    project: Project
    store: Store
    fetcher: Fetcher
    client: LLMClient | None
    today: dt.date
    notify: Callable[[str], None] = field(default=lambda text: None)

    @property
    def settings(self):  # type: ignore[no-untyped-def]
        return self.project.settings


def slack_notifier() -> Callable[[str], None]:
    webhook = os.environ.get("SLACK_WEBHOOK_URL")

    def notify(text: str) -> None:
        try:
            digest_mod.post_slack(webhook, text)
        except httpx.HTTPError as e:
            log.error("Slack post failed: %s", e)

    return notify


def _seen(item: Item, outcome: str) -> SeenRecord:
    return SeenRecord.model_validate(
        {
            "url": item.url,
            "org_id": item.org_id,
            "doc_type": item.doc_type,
            "detected": item.detected,
            "lastmod": item.lastmod,
            "content_hash": item.content_hash,
            "outcome": outcome,
        }
    )


def _queued(item: Item, stage: str) -> QueuedItem:
    q = QueuedItem.model_validate({**item.model_dump(), "stage": stage})
    q.feed_summary = item.feed_summary  # excluded from model_dump; keep it in memory for stage 1
    return q


def _page(ctx: Context, item: Item) -> tuple[str, str]:
    """(title, main text) of the item's page. The text is never written to disk."""
    resp = ctx.fetcher.get(item.url)
    return page_title(resp.text), extract_text(resp.text, item.url)


def _budget(ctx: Context) -> Budget:
    budget = Budget(ctx.project, ctx.store, ctx.today)
    budget.reserved = sum(b.reserved_tokens for b in ctx.store.batches().values() if not b.collected)
    return budget


def _stop_for_budget(ctx: Context, run: RunState, err: BudgetExceeded) -> None:
    if not run.budget_stopped:
        run.budget_stopped = True
        ctx.notify(
            f":octagonal_sign: Agentic AI Watch: LLM calls stopped. {err}. Remaining items carry over."
        )


def _carry_count(queue: list[QueuedItem]) -> int:
    return len(queue)


# ---------------------------------------------------------------- dry run


@dataclass
class DryRun:
    detection: Detection
    passed: list[Item]
    rejected: list[Item]


def dry_run(ctx: Context) -> DryRun:
    det = detect(ctx.project, ctx.store, ctx.fetcher, ctx.today, ctx.settings.crawl.first_run_lookback_days)
    passed = [i for i in det.items if passes(i, ctx.project.keywords)]
    rejected = [i for i in det.items if i not in passed]
    return DryRun(det, passed, rejected)


# ---------------------------------------------------------------- weekly run


def start_run(ctx: Context) -> RunState:
    store = ctx.store
    existing = store.run()
    if existing and not existing.digest_posted:
        pending = [b for b in store.batches().values() if not b.collected and b.run_id == existing.run_id]
        if pending:
            raise RunInProgress(f"run {existing.run_id} still has {len(pending)} batch(es) pending")
        finish_run(ctx, existing)

    det = detect(ctx.project, store, ctx.fetcher, ctx.today, ctx.settings.crawl.first_run_lookback_days)
    passed = [i for i in det.items if passes(i, ctx.project.keywords)]
    rejected_ids = {i.id for i in det.items} - {i.id for i in passed}

    run = RunState(run_id=ctx.today.isoformat(), started=ctx.today)
    run.counts.detected = len(det.items)
    run.counts.keyword_passed = len(passed)
    run.manual_watch_points = det.manual
    run.fetch_errors = list(det.errors)

    seen_updates = [r for r in det.seen_updates if r.outcome == "skipped"]
    seen_updates += [_seen(i, "keyword_rejected") for i in det.items if i.id in rejected_ids]
    seen_updates += [_seen(i, "queued") for i in passed]
    store.mark_seen(seen_updates)
    store.save_checks({**store.checks(), **det.checked})

    queue = store.queue() + [_queued(i, "stage1") for i in passed]
    stage1(ctx, run, queue)
    if not ctx.settings.use_batch:
        stage2(ctx, run, queue)
    run.counts.carried_over = _carry_count(queue)
    store.save_queue(queue)
    store.save_run(run)
    if not ctx.settings.use_batch or not _pending_batches(ctx, run):
        if ctx.settings.use_batch and any(q.stage == "stage2" for q in queue):
            return run  # collect will submit stage 2 for items carried over
        finish_run(ctx, run)
    return run


def _take(queue: list[QueuedItem], stage: str, limit: int) -> list[QueuedItem]:
    pending = sorted((q for q in queue if q.stage == stage), key=lambda q: (q.detected, q.id))
    return pending[: max(limit, 0)]


def stage1(ctx: Context, run: RunState, queue: list[QueuedItem]) -> None:
    if ctx.client is None:
        return
    limits = ctx.settings.limits
    take = _take(queue, "stage1", limits.stage1_max_items_per_run - run.counts.stage1_sent)
    if not take:
        return
    budget = _budget(ctx)
    prepared: list[tuple[QueuedItem, dict[str, object]]] = []
    for q in take:
        text = q.feed_summary or ""
        if not text:
            try:
                title, text = _page(ctx, q)
                q.title = q.title or title
            except (RobotsDisallowed, httpx.HTTPError) as e:
                run.fetch_errors.append(f"{q.org_id}: {q.url}: {type(e).__name__}")
                queue.remove(q)
                ctx.store.mark_seen([_seen(q, "fetch_failed")])
                continue
        prepared.append(
            (q, build_params(ctx.project, "stage1", stage1_input(q, text, limits.stage1_max_input_chars)))
        )

    if ctx.settings.use_batch:
        _submit_batch(ctx, run, queue, "stage1", prepared, budget)
        return
    usage = []
    try:
        for q, params in prepared:
            out: CallOutcome[Relevance] = call_with_retry(
                ctx.client, params, parse_stage1, budget, run.run_id, "stage1", q
            )
            usage += out.usage
            run.counts.stage1_sent += 1
            _apply_stage1(ctx, run, queue, q, out.value, out.error)
    except BudgetExceeded as e:
        _stop_for_budget(ctx, run, e)
    finally:
        ctx.store.record_usage(usage)


def _apply_stage1(
    ctx: Context,
    run: RunState,
    queue: list[QueuedItem],
    q: QueuedItem,
    rel: Relevance | None,
    error: str | None,
) -> None:
    if rel is None:
        run.fetch_errors.append(f"{q.org_id}: {q.url}: stage 1 output unreadable ({error})")
        if q in queue:
            queue.remove(q)
        ctx.store.mark_seen([_seen(q, "stage1_rejected")])
        return
    if rel.label != "not-relevant" and rel.confidence >= ctx.settings.stage1.threshold:
        q.stage = "stage2"
        q.relevance = rel
        q.attempts = 0
        run.counts.stage1_passed += 1
        if q not in queue:
            queue.append(q)
    else:
        if q in queue:
            queue.remove(q)
        ctx.store.mark_seen([_seen(q, "stage1_rejected")])


def stage2(ctx: Context, run: RunState, queue: list[QueuedItem]) -> None:
    if ctx.client is None:
        return
    limits = ctx.settings.limits
    take = _take(queue, "stage2", limits.stage2_max_items_per_run - run.counts.stage2_sent)
    if not take:
        return
    budget = _budget(ctx)
    prepared: list[tuple[QueuedItem, dict[str, object]]] = []
    texts: dict[str, str] = {}
    for q in take:
        try:
            title, text = _page(ctx, q)
        except (RobotsDisallowed, httpx.HTTPError) as e:
            run.fetch_errors.append(f"{q.org_id}: {q.url}: {type(e).__name__}")
            queue.remove(q)
            ctx.store.mark_seen([_seen(q, "fetch_failed")])
            continue
        q.title = q.title or title
        texts[q.id] = text
        prepared.append(
            (q, build_params(ctx.project, "stage2", stage2_input(q, text, limits.stage2_max_input_chars)))
        )

    if ctx.settings.use_batch:
        _submit_batch(ctx, run, queue, "stage2", prepared, budget)
        return
    usage = []
    entries: list[Entry] = []
    try:
        for q, params in prepared:
            out: CallOutcome[Extraction] = call_with_retry(
                ctx.client, params, lambda m: parse_stage2(ctx.project, m), budget, run.run_id, "stage2", q
            )
            usage += out.usage
            run.counts.stage2_sent += 1
            queue.remove(q)
            entry = _make_entry(ctx, run, q, out.value, out.error, texts[q.id])
            if entry:
                entries.append(entry)
    except BudgetExceeded as e:
        _stop_for_budget(ctx, run, e)
    finally:
        ctx.store.record_usage(usage)
        _save_entries(ctx, run, entries)


def _make_entry(
    ctx: Context, run: RunState, q: QueuedItem, ext: Extraction | None, error: str | None, text: str
) -> Entry | None:
    if ext is None or q.relevance is None:
        run.fetch_errors.append(f"{q.org_id}: {q.url}: stage 2 output unreadable ({error})")
        ctx.store.mark_seen([_seen(q, "stage1_rejected")])
        return None
    verified = evidence_in_text(ext.evidence, text)
    s = ctx.settings
    return Entry(
        id=q.id,
        org_id=q.org_id,
        url=q.url,
        title=q.title,
        published=q.published,
        detected=q.detected,
        doc_type=q.doc_type,
        language=q.language,
        relevance=q.relevance,
        summary=ext.summary,
        evidence=ext.evidence,
        evidence_verified=verified,
        fields=ext.fields,
        challenges=ext.challenges,
        responses=ext.responses,
        other_organisations_named=ext.other_organisations_named,
        status="new" if verified else "needs_review",
        model=EntryModels(stage1=s.models.stage1, stage2=s.models.stage2),
    )


def _save_entries(ctx: Context, run: RunState, entries: list[Entry]) -> None:
    if not entries:
        return
    ctx.store.add_entries(entries)
    ctx.store.mark_seen(
        [
            SeenRecord(
                url=e.url, org_id=e.org_id, doc_type=e.doc_type, detected=e.detected, outcome="recorded"
            )
            for e in entries
        ]
    )
    run.new_entry_ids += [e.id for e in entries]
    run.counts.recorded += len(entries)
    run.candidate_changes += update_candidates(
        ctx.project.path("ledger/candidates.yaml"), entries, ctx.project.organisations, ctx.today
    )


# ---------------------------------------------------------------- batches


def _custom_id(stage: str, q: QueuedItem) -> str:
    return f"{stage[-1]}-{q.id}"  # "1-<sha1>" / "2-<sha1>": within the 64-character limit


def _submit_batch(
    ctx: Context,
    run: RunState,
    queue: list[QueuedItem],
    stage: str,
    prepared: list[tuple[QueuedItem, dict[str, object]]],
    budget: Budget,
) -> None:
    assert ctx.client is not None
    requests: list[tuple[str, dict[str, Any]]] = []
    items: list[QueuedItem] = []
    reserved = 0
    for q, params in prepared:
        est = estimate_tokens(params)
        try:
            budget.reserve(est)
        except BudgetExceeded as e:
            _stop_for_budget(ctx, run, e)
            break
        reserved += est
        requests.append((_custom_id(stage, q), params))
        items.append(q)
    if not requests:
        return
    batch_id = ctx.client.create_batch(requests)
    batches = ctx.store.batches()
    batches[batch_id] = BatchRecord(
        stage=stage,  # type: ignore[arg-type]
        run_id=run.run_id,
        model=str(requests[0][1]["model"]),
        submitted=dt.datetime.now(dt.UTC),
        items=items,
        reserved_tokens=reserved,
    )
    ctx.store.save_batches(batches)
    for q in items:
        queue.remove(q)
    if stage == "stage1":
        run.counts.stage1_sent += len(items)
    else:
        run.counts.stage2_sent += len(items)
    log.info("submitted %s batch %s with %d requests", stage, batch_id, len(items))


def _pending_batches(ctx: Context, run: RunState) -> list[str]:
    return [bid for bid, b in ctx.store.batches().items() if not b.collected and b.run_id == run.run_id]


def collect(ctx: Context) -> str:
    """Collect finished batches, submit stage 2 when stage 1 is done, post the digest at the end."""
    assert ctx.client is not None
    store = ctx.store
    run = store.run()
    batches = store.batches()
    queue = store.queue()
    collected_any = False

    for bid, rec in batches.items():
        if rec.collected:
            continue
        status = ctx.client.batch_status(bid)
        if status != "ended":
            log.info("batch %s: %s", bid, status)
            continue
        by_cid = {_custom_id(rec.stage, q): q for q in rec.items}
        usage = []
        entries: list[Entry] = []
        handled: set[str] = set()
        target_run = run if run and run.run_id == rec.run_id else None
        for res in ctx.client.batch_results(bid):
            q = by_cid.get(res.custom_id)
            if q is None:
                continue
            handled.add(res.custom_id)
            if res.result.type != "succeeded":
                _requeue(queue, q, rec.stage)
                continue
            message = res.result.message
            usage.append(usage_record(message, rec.run_id, rec.stage, batch=True, item=q))
            if rec.stage == "stage1":
                try:
                    rel = parse_stage1(message)
                except ParseError as e:
                    if _requeue(queue, q, "stage1"):
                        continue
                    if target_run:
                        _apply_stage1(ctx, target_run, queue, q, None, str(e))
                    continue
                if target_run:
                    _apply_stage1(ctx, target_run, queue, q, rel, None)
                elif rel.label != "not-relevant" and rel.confidence >= ctx.settings.stage1.threshold:
                    q.stage, q.relevance, q.attempts = "stage2", rel, 0
                    queue.append(q)
            else:
                try:
                    ext = parse_stage2(ctx.project, message)
                except ParseError:
                    _requeue(queue, q, "stage2")
                    continue
                try:
                    _title, text = _page(ctx, q)  # re-read the page to verify the quote; not stored
                except (RobotsDisallowed, httpx.HTTPError):
                    text = ""
                owner = target_run or RunState(run_id=rec.run_id, started=ctx.today)
                entry = _make_entry(ctx, owner, q, ext, None, text)
                if entry:
                    entries.append(entry)
        for cid, q in by_cid.items():  # requests missing from the results file
            if cid not in handled:
                _requeue(queue, q, rec.stage)
        store.record_usage(usage)
        if entries:
            _save_entries(ctx, target_run or RunState(run_id=rec.run_id, started=ctx.today), entries)
        rec.collected = True
        collected_any = True
        store.save_batches(batches)

    message = "nothing to collect"
    if run and not run.digest_posted and not _pending_batches(ctx, run):
        stage2(ctx, run, queue)
        if not _pending_batches(ctx, run):
            run.counts.carried_over = _carry_count(queue)
            store.save_queue(queue)
            finish_run(ctx, run)
            return "digest posted"
        message = "stage 2 submitted"
    elif collected_any:
        message = "collected"
    if run:
        run.counts.carried_over = _carry_count(queue)
        store.save_run(run)
    store.save_queue(queue)
    return message


def _requeue(queue: list[QueuedItem], q: QueuedItem, stage: str) -> bool:
    """Put a failed batch item back once. Returns False when it has already been retried."""
    if q.attempts >= 1:
        return False
    q.attempts += 1
    q.stage = stage  # type: ignore[assignment]
    queue.append(q)
    return True


# ---------------------------------------------------------------- digest


def finish_run(ctx: Context, run: RunState, post: bool = True) -> str:
    store = ctx.store
    entries = store.entries()
    new_ids = set(run.new_entry_ids)
    data = digest_mod.DigestData(
        run=run,
        new_entries=[e for e in entries if e.id in new_ids],
        needs_review=[e for e in entries if e.status == "needs_review"],
        run_usage=[u for u in store.usage() if u.run_id == run.run_id],
        month_tokens=store.month_tokens(ctx.today.strftime("%Y-%m")),
        month_budget=ctx.settings.limits.monthly_token_budget,
    )
    markdown = digest_mod.render_markdown(ctx.project, data)
    path = ctx.project.path("reports/weekly", f"{run.run_id}.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    if post:
        ctx.notify(digest_mod.render_slack(ctx.project, data))
    run.digest_posted = True
    store.save_run(run)
    return markdown
