# Agentic AI Watch (pilot)

English | [日本語](README.ja.md)

An internal tool that checks, once a week, **only the organisations' own websites** for the organisations on a watch list, finds new publications on their adoption of and policies for agentic AI, and keeps them as structured records. See [SPEC.md](SPEC.md) for the specification ([Japanese original](SPEC.ja.md)).

## How it works

```
Detect new pages (RSS, sitemaps, page hashes; no LLM)
  → keep only URLs on the organisation's own domains, drop ones already seen
  → keyword filter (no LLM; AI governance pages skip the filter)
  → stage 1: relevance (Haiku; input ≤ 1,500 characters, output ≤ 100 tokens)
  → stage 2: field extraction (Haiku; first 6,000 characters of the page, output ≤ 800 tokens)
  → code checks that the evidence quote appears verbatim in the page (if not: needs_review)
  → save to data/entries/YYYY-MM.jsonl, update candidate organisations, post a weekly digest to Slack
```

- Page text is handled in memory only and is never written to any file (covered by the tests).
- By default, stages 1 and 2 use the Message Batches API (half price). `weekly.yml` submits stage 1; `collect.yml` (every two hours) collects the results, submits stage 2, and posts the digest when everything is done.
- To keep costs down, stages 1 and 2 run with thinking disabled (effort `low`) and return JSON through a schema. The fixed system prompts are marked for prompt caching.
- When the monthly token budget (`limits.monthly_token_budget`) would be exceeded, LLM calls stop, Slack is notified, and the remaining items carry over to the next week.

## Setup

### 1. Local (Mac)

```bash
# If uv is missing: curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
uv run agentic-watch validate      # check settings, taxonomy and the ledger
uv run pytest -q                   # tests (no API calls)
```

### 2. Settings you must change

- `crawl.user_agent` in `config/settings.yaml`: replace `<email>` with a contact address. Commands that crawl stop until you do.
- `pricing` in `config/settings.yaml`: unit prices used only for the cost estimate in the digest. Check current prices and update them.

### 3. GitHub

1. Under **Settings → Secrets and variables → Actions**, add:
   - `ANTHROPIC_API_KEY`: a Claude API key (also set a monthly spend limit in the API console)
   - `SLACK_WEBHOOK_URL`: the Incoming Webhook URL for #agentic_ai
2. **Slack Incoming Webhook**: in Slack's "Incoming Webhooks" app, choose #agentic_ai as the channel and put the issued URL in the secret above. Without it, the digest is saved to `reports/weekly/` but not posted.
3. Under **Settings → Actions → General → Workflow permissions**, select "Read and write permissions" (the bot commits state files).

| Workflow | Trigger | What it does |
|---|---|---|
| `weekly.yml` | Mondays 06:00 JST, or manual | Detect → filter → submit stage 1 |
| `collect.yml` | Every two hours, or manual | Collect batch results → stage 2 → save → post the digest (ends in seconds when nothing is pending) |
| `monthly.yml` | Manual (choose a month) | Monthly note |
| `ci.yml` | Push / PR | Lint, type check, tests (no API key used) |

## The ledger

### Watch list: `ledger/organisations.yaml`

People enter and check organisation names and URLs; the tool never creates them. Fields follow SPEC.md §3.1. `sector` and `region` use ids from `config/taxonomy/vocabularies.yaml`.

How to write each watch point `method`:

| method | `url` | `feed_url` | What counts as new |
|---|---|---|---|
| `rss` | The listing page | RSS/Atom URL (required) | A URL not seen before |
| `sitemap` | The section to watch (e.g. `https://example.com/news`); only URLs under this path are used | The sitemap URL (if omitted: robots.txt `Sitemap:`, else `/sitemap.xml`) | A URL not seen before, or a newer `lastmod` |
| `page-hash` | The page to monitor | Not needed | A change in the hash of the page text |
| `manual` | A page checked by hand | Not needed | Not processed; listed in the digest under "check by hand" |

- **First check of a watch point**: items older than `crawl.first_run_lookback_days` (default 30) and undated sitemap entries are recorded as seen but not processed, so a watch point's back catalogue does not flood stage 1. `page-hash` pages are processed on the first check too.
- Finding pages to watch: `uv run agentic-watch discover https://example.com/` prints RSS and sitemap candidates as YAML. Nothing is written to the ledger; check them and paste them in.

### Candidate organisations: `ledger/candidates.yaml`

Organisations named in a record's `other_organisations_named` are added automatically as `official-mention` (or their `mentions` count goes up; organisations already on the watch list are skipped). Add other leads (third-party, self-nominated, introductions) by hand. Decide on additions once a quarter by reviewing this file.

## Weekly review

From the digest, set `status` on the matching line of `data/entries/YYYY-MM.jsonl` to `kept`, `discarded` or `blog_candidate`, put your name in `reviewer`, and commit. `needs_review` means the evidence quote was not found in the page text.

## Running by hand

```bash
uv run agentic-watch run --no-llm          # dry run: detection and keyword filter only (no API, nothing written)
uv run agentic-watch run --sync            # all stages with the synchronous API (few items, or trying things out)
uv run agentic-watch run                   # batch API (submits stage 1)
uv run agentic-watch collect               # collect batches, stage 2, digest
uv run agentic-watch digest [--post]       # re-render the latest run's digest
uv run agentic-watch evaluate              # stage 1 accuracy against data/gold.yaml
uv run agentic-watch synthesize --month 2026-11
uv run agentic-watch pilot-summary         # end-of-pilot summary → reports/pilot-summary.md
```

On GitHub, use **Actions → the workflow → Run workflow**.

## Files

| Path | Contents |
|---|---|
| `config/taxonomy/` | Copies of the living resource's taxonomy (challenges / responses) and vocabularies. Update by copying; category names are never written into the code |
| `prompts/` | System prompts for stage 1, stage 2 and the monthly note |
| `state/seen.jsonl` | URLs already detected (the last line per URL wins) |
| `state/queue.jsonl` | Items carried over to the next run by the limits (no page text) |
| `state/batches.json` | Submitted batches and their status |
| `state/run.json` | Counts for the current week's run |
| `state/checks.json` | Last check date per watch point (for the frequency setting) |
| `state/usage.jsonl` | Token usage per API response |
| `data/entries/` | Records |
| `reports/` | Weekly digest copies, monthly notes, summaries |
