import json

import yaml

from agentic_watch.models import Entry
from agentic_watch.pipeline import collect, dry_run, start_run

from .conftest import STAGE2_OK, FakeLLM, make_ctx, message


def all_written_text(root) -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in root.rglob("*") if p.is_file())


def test_dry_run_writes_nothing_and_calls_no_api(project_root):
    before = {p: p.read_bytes() for p in project_root.rglob("*") if p.is_file()}
    ctx, _, _ = make_ctx(project_root, llm=None)
    result = dry_run(ctx)
    assert [i.url for i in result.passed] == ["https://example.com/news/agent-pilot"]
    after = {p: p.read_bytes() for p in project_root.rglob("*") if p.is_file()}
    assert before == after


def test_sync_run_records_entry_with_verified_evidence(project_root):
    llm = FakeLLM()
    ctx, _, notes = make_ctx(project_root, llm=llm)
    run = start_run(ctx)
    assert run.counts.detected == 3 and run.counts.keyword_passed == 1
    assert run.counts.stage1_passed == 1 and run.counts.recorded == 1
    [entry] = ctx.store.entries()
    assert entry.evidence_verified and entry.status == "new"
    assert entry.challenges == ["human-oversight-over-reliance"]  # unknown ids dropped
    assert entry.model.stage1 == "claude-haiku-5-5"
    # stage 1 only saw a short input; stage 2 at most the configured characters
    s1 = [c for c in llm.calls if "screen pages" in c["system"][0]["text"]]
    assert all(len(c["messages"][0]["content"]) <= 1500 for c in s1)
    assert s1[0]["thinking"] == {"type": "disabled"} and s1[0]["max_tokens"] == 100
    assert s1[0]["system"][0]["cache_control"] == {"type": "ephemeral"}
    # digest posted and saved
    assert notes and "weekly digest" in notes[-1]
    assert (project_root / f"reports/weekly/{run.run_id}.md").exists()


def test_unverified_evidence_needs_review(project_root):
    bad = dict(STAGE2_OK, evidence="Staff approve replies at all times.")
    ctx, _, _ = make_ctx(project_root, llm=FakeLLM(stage2=lambda p: message(bad)))
    start_run(ctx)
    [entry] = ctx.store.entries()
    assert not entry.evidence_verified and entry.status == "needs_review"


def test_page_text_is_never_written(project_root):
    ctx, _, _ = make_ctx(project_root, llm=FakeLLM())
    start_run(ctx)
    text = all_written_text(project_root)
    assert "UNIQUE-BODY-MARKER" not in text
    assert "Partner Co." in (project_root / "ledger/candidates.yaml").read_text()


def test_candidates_updated_and_watch_list_excluded(project_root):
    ctx, _, _ = make_ctx(project_root, llm=FakeLLM())
    start_run(ctx)
    cands = yaml.safe_load((project_root / "ledger/candidates.yaml").read_text())
    assert [c["name"] for c in cands] == ["Partner Co."]  # "Example Org" is on the watch list
    assert cands[0]["lead_route"] == "official-mention" and cands[0]["mentions"] == 1


def test_usage_is_recorded_and_shown_in_digest(project_root):
    ctx, _, notes = make_ctx(project_root, llm=FakeLLM())
    run = start_run(ctx)
    usage = ctx.store.usage()
    assert {u.stage for u in usage} == {"stage1", "stage2"}
    assert all(u.run_id == run.run_id for u in usage)
    report = (project_root / f"reports/weekly/{run.run_id}.md").read_text()
    assert "This run: 680 tokens" in report  # two calls × (300 + 40)
    assert "Tokens:" in notes[-1]


def test_parse_failure_is_retried_once(project_root):
    calls = {"n": 0}

    def flaky(params):
        calls["n"] += 1
        return (
            message({"label": "deployment", "confidence": 0.9}, stop="max_tokens")
            if calls["n"] == 1
            else message({"label": "deployment", "confidence": 0.9})
        )

    ctx, _, _ = make_ctx(project_root, llm=FakeLLM(stage1=flaky))
    run = start_run(ctx)
    assert calls["n"] == 2 and run.counts.stage1_passed == 1


def test_budget_stop_notifies_and_carries_over(project_root):
    s = yaml.safe_load((project_root / "config/settings.yaml").read_text())
    s["limits"]["monthly_token_budget"] = 500  # less than one estimated call
    (project_root / "config/settings.yaml").write_text(yaml.safe_dump(s))
    llm = FakeLLM()
    ctx, _, notes = make_ctx(project_root, llm=llm)
    run = start_run(ctx)
    assert llm.calls == []
    assert run.budget_stopped and run.counts.carried_over == 1
    assert any("budget" in n for n in notes)
    assert len(ctx.store.queue()) == 1  # carried to the next run


def test_stage1_limit_carries_oldest_first(project_root):
    s = yaml.safe_load((project_root / "config/settings.yaml").read_text())
    s["limits"]["stage1_max_items_per_run"] = 0
    (project_root / "config/settings.yaml").write_text(yaml.safe_dump(s))
    ctx, _, _ = make_ctx(project_root, llm=FakeLLM())
    run = start_run(ctx)
    assert run.counts.stage1_sent == 0 and run.counts.carried_over == 1


def test_low_confidence_is_not_sent_to_stage2(project_root):
    llm = FakeLLM(stage1=lambda p: message({"label": "deployment", "confidence": 0.3}))
    ctx, _, _ = make_ctx(project_root, llm=llm)
    run = start_run(ctx)
    assert run.counts.stage1_passed == 0 and ctx.store.entries() == []
    assert all("screen pages" in c["system"][0]["text"] for c in llm.calls)


def test_batch_flow_run_then_collect(project_root):
    llm = FakeLLM()
    ctx, _, notes = make_ctx(project_root, llm=llm, use_batch=True)
    run = start_run(ctx)
    assert run.counts.stage1_sent == 1 and not run.digest_posted
    batches = json.loads((project_root / "state/batches.json").read_text())
    assert len(batches) == 1

    assert collect(ctx) == "nothing to collect"  # still in progress
    for bid in llm.batch_state:
        llm.batch_state[bid] = "ended"
    assert collect(ctx) == "stage 2 submitted"
    for bid in llm.batch_state:
        llm.batch_state[bid] = "ended"
    assert collect(ctx) == "digest posted"
    [entry] = ctx.store.entries()
    assert isinstance(entry, Entry) and entry.evidence_verified
    assert all(u.batch for u in ctx.store.usage())
    assert notes and "weekly digest" in notes[-1]
    assert collect(ctx) == "nothing to collect"  # idempotent


def test_unreadable_relevant_page_is_listed_for_hand_check(project_root):
    from .conftest import default_site

    site = default_site()
    site.pages["https://example.com/news/agent-pilot"] = (
        403,
        "forbidden",
    )  # the RSS summary is enough for stage 1
    ctx, _, _ = make_ctx(project_root, site=site, llm=FakeLLM())
    run = start_run(ctx)
    assert run.counts.stage1_passed == 1 and run.counts.recorded == 0
    [line] = run.unreadable_pages
    assert "https://example.com/news/agent-pilot" in line and "HTTP 403" in line
    report = (project_root / f"reports/weekly/{run.run_id}.md").read_text()
    assert "could not be read" in report and "HTTP 403" in report
