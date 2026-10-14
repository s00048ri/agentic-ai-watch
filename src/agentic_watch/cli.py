"""Command line: `agentic-watch <command>`. See README.md."""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
from pathlib import Path

from .config import load_project
from .discover import as_yaml, discover
from .fetch import Fetcher
from .llm import AnthropicClient, BudgetExceeded
from .pipeline import Context, RunInProgress, collect, dry_run, finish_run, slack_notifier, start_run
from .store import Store


def _context(root: Path | None, need_llm: bool, today: dt.date | None = None) -> Context:
    project = load_project(root)
    if project.settings.crawl.contact_missing():
        sys.exit(
            "config/settings.yaml: set a contact address in crawl.user_agent (replace <email>) before crawling."
        )
    client = None
    if need_llm:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("ANTHROPIC_API_KEY is not set. Use --no-llm for a dry run.")
        client = AnthropicClient()
    return Context(
        project=project,
        store=Store(project.root),
        fetcher=Fetcher(project.settings.crawl),
        client=client,
        today=today or dt.date.today(),
        notify=slack_notifier(),
    )


def cmd_run(args: argparse.Namespace) -> int:
    ctx = _context(args.root, need_llm=not args.no_llm)
    if args.sync:
        ctx.project.settings.use_batch = False
    if args.no_llm:
        result = dry_run(ctx)
        d = result.detection
        print(f"Detected {len(d.items)} new items; keyword filter passed {len(result.passed)}.")
        for item in result.passed:
            print(f"  PASS  {item.org_id}  {item.title or '-'}  {item.url}")
        for item in result.rejected:
            print(f"  skip  {item.org_id}  {item.title or '-'}  {item.url}")
        baseline = sum(1 for r in d.seen_updates if r.outcome == "skipped")
        if baseline:
            print(f"Baseline (first check, older items, not processed): {baseline}")
        print("Watch points checked:")
        for line in d.per_point:
            print(f"  point  {line}")
        for line in d.manual:
            print(f"  manual  {line}")
        for line in d.errors:
            print(f"  error  {line}")
        print("Dry run: nothing written, no API calls.")
        return 0
    try:
        run = start_run(ctx)
    except RunInProgress as e:
        print(f"Not started: {e}. Run `agentic-watch collect` first.", file=sys.stderr)
        return 1
    c = run.counts
    print(
        f"Run {run.run_id}: detected {c.detected}, keywords {c.keyword_passed}, stage 1 sent {c.stage1_sent},"
        f" passed {c.stage1_passed}, recorded {c.recorded}, carried over {c.carried_over}."
    )
    return 0


def cmd_collect(args: argparse.Namespace) -> int:
    ctx = _context(args.root, need_llm=True)
    print(collect(ctx))
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    ctx = _context(args.root, need_llm=False)
    run = ctx.store.run()
    if run is None:
        print("No run recorded yet.")
        return 1
    print(finish_run(ctx, run, post=args.post))
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    ctx = _context(args.root, need_llm=False)
    print(as_yaml(discover(ctx.fetcher, args.url)))
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    from .evaluate import evaluate

    ctx = _context(args.root, need_llm=True)
    print(evaluate(ctx).markdown())
    return 0


def cmd_synthesize(args: argparse.Namespace) -> int:
    from .synthesis import synthesize

    ctx = _context(args.root, need_llm=True)
    try:
        print(synthesize(ctx, args.month))
    except BudgetExceeded as e:
        print(e, file=sys.stderr)
        return 1
    return 0


def cmd_pilot_summary(args: argparse.Namespace) -> int:
    from .evaluate import pilot_summary

    ctx = _context(args.root, need_llm=False)
    text = pilot_summary(ctx)
    path = ctx.project.path("reports/pilot-summary.md")
    path.write_text(text, encoding="utf-8")
    print(text)
    return 0


def cmd_issue_templates(args: argparse.Namespace) -> int:
    from .issues import write_templates

    project = load_project(args.root)
    stale = write_templates(project, check=args.check)
    if args.check and stale:
        print("Issue forms are out of date; run `agentic-watch issue-templates`: " + ", ".join(stale))
        return 1
    print("\n".join(stale) if stale else "Issue forms are up to date.")
    return 0


def cmd_from_issue(args: argparse.Namespace) -> int:
    """Apply an issue form to the ledger. The issue body is read from the ISSUE_BODY environment variable."""
    from .issues import IssueError, apply_lead_issue, apply_organisation_issue

    project = load_project(args.root)
    body = os.environ.get("ISSUE_BODY", "")
    today = dt.date.today()
    try:
        if args.kind == "organisation":
            fetcher = None if project.settings.crawl.contact_missing() else Fetcher(project.settings.crawl)
            result = apply_organisation_issue(project, body, args.number, today, fetcher)
        else:
            result = apply_lead_issue(project, body, args.number, today)
    except (IssueError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(result)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    project = load_project(args.root)
    n_wp = sum(len(o.watch_points) for o in project.organisations)
    print(f"OK: {len(project.organisations)} organisations, {n_wp} watch points.")
    if project.settings.crawl.contact_missing():
        print("Warning: crawl.user_agent still has the <email> placeholder.")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"), format="%(levelname)s %(name)s: %(message)s"
    )
    p = argparse.ArgumentParser(prog="agentic-watch")
    p.add_argument("--root", type=Path, default=None, help="project root (default: this checkout)")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="weekly run: detect, filter, stage 1 (and stage 2 in sync mode)")
    r.add_argument("--no-llm", action="store_true", help="dry run: detection and keyword filter only")
    r.add_argument("--sync", action="store_true", help="use the synchronous API instead of batches")
    r.set_defaults(func=cmd_run)

    sub.add_parser("collect", help="collect batch results, submit stage 2, post the digest").set_defaults(
        func=cmd_collect
    )

    d = sub.add_parser("digest", help="re-render the latest run's digest")
    d.add_argument("--post", action="store_true", help="also post it to Slack")
    d.set_defaults(func=cmd_digest)

    dc = sub.add_parser("discover", help="suggest RSS feeds and sitemaps for a home page")
    dc.add_argument("url")
    dc.set_defaults(func=cmd_discover)

    sub.add_parser("evaluate", help="run stage 1 on data/gold.yaml").set_defaults(func=cmd_evaluate)

    s = sub.add_parser("synthesize", help="monthly note from reviewed records")
    s.add_argument("--month", required=True, help="YYYY-MM")
    s.set_defaults(func=cmd_synthesize)

    sub.add_parser("pilot-summary", help="end-of-pilot summary by watch point type").set_defaults(
        func=cmd_pilot_summary
    )
    sub.add_parser("validate", help="check settings, taxonomy and the ledger").set_defaults(func=cmd_validate)

    it = sub.add_parser("issue-templates", help="generate the GitHub issue forms from config/")
    it.add_argument("--check", action="store_true", help="only report forms that are out of date")
    it.set_defaults(func=cmd_issue_templates)

    fi = sub.add_parser("from-issue", help="apply an issue form (body in $ISSUE_BODY) to the ledger")
    fi.add_argument("--kind", choices=["organisation", "lead"], required=True)
    fi.add_argument("--number", type=int, required=True)
    fi.set_defaults(func=cmd_from_issue)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
