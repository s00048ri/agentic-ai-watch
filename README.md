# Agentic AI Watch (pilot)

English | [日本語](README.ja.md)

An internal tool that checks, once a week, **only the organisations' own websites** for the organisations on a watch list, finds new publications on their adoption of and policies for agentic AI, and keeps them as structured records. Results are not published on websites or elsewhere (files in this repository aside). See [SPEC.md](SPEC.md) for the specification ([Japanese original](SPEC.ja.md)).

## How it works

```
Detect new pages (RSS, sitemaps, page hashes; no LLM)
  → keep only URLs on the organisation's own domains, drop ones already seen
  → keyword filter (no LLM; AI governance pages skip the filter; for sitemap items the page title and opening are read first)
  → stage 1: relevance (Haiku; input ≤ 1,500 characters, output ≤ 100 tokens)
  → stage 2: field extraction (Haiku; first 6,000 characters of the page, output ≤ 1,500 tokens)
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

## Web app

A sign-in protected web app for the team (English, with a Japanese option in the sidebar):

| Page | What you can do |
|---|---|
| Review records | Filter new and needs-review records; read the summary, the evidence quote and the source; see every challenge and response the page describes, each with its quote and a suggested category, and confirm or change the category (or mark it as a possible new category); mark each record as kept, blog candidate or discarded, with a note |
| Possible new categories | Challenges and responses that fit no existing category, with their quotes and sources (CSV download), and how often each existing category is used |
| Organisations | See the watch list with last-check dates and record counts; change an organisation's status and watch points; add an organisation (RSS feeds and sitemaps are suggested from the home page; tick "verified" only after opening each URL) |
| Candidates | Candidates by number of mentions; add one to the watch list or reject it; add a lead by hand |
| Run status | Tokens and estimated cost this month, pending batches, the latest run's counts and errors, past weekly digests |
| Search and export | Search all records and download them as CSV |

Every change is saved as a commit to this repository through the GitHub API. If the weekly bot (or a colleague) pushed in between, the change is applied again to the latest version automatically.

### Deploying on Streamlit Community Cloud

1. On [share.streamlit.io](https://share.streamlit.io), sign in with GitHub and create an app from this repository: branch `main`, file `streamlit_app.py`, Python 3.12 (under Advanced settings).
2. Set up Google sign-in in [Google Cloud Console](https://console.cloud.google.com/) (any Google account can own the project):
   1. Create a project (e.g. `agentic-ai-watch`).
   2. **APIs & Services → OAuth consent screen**: user type **External**, app name "Agentic AI Watch", your email as support and developer contact. Scopes: only `openid`, `email` and `profile` (the defaults; no review by Google is needed). Leave the app in **Testing** and add each team member's Google account under **Test users**: only they can sign in, which adds a second check on top of `allowed_emails`.
   3. **APIs & Services → Credentials → Create credentials → OAuth client ID**: type **Web application**, authorised redirect URI `https://<your-app>.streamlit.app/oauth2callback`. Copy the client ID and secret.
   4. In Testing mode, people may need to sign in again about once a week.
3. Create a GitHub fine-grained personal access token limited to this repository with "Contents: Read and write".
4. Paste the settings into the app's **Secrets**, following [`.streamlit/secrets.toml.example`](.streamlit/secrets.toml.example). `allowed_emails` is the list of people who may use the app; anyone else is refused after signing in. Without the `[auth]` section the app refuses to start.
5. Optional second layer: in the app's settings on Streamlit Community Cloud, limit viewers to the same people.

To try it locally without sign-in: `AGENTIC_WATCH_DEV=1 uv run streamlit run streamlit_app.py` (changes go to your checkout only).

## Issue forms

People with a GitHub account can use **Issues → New issue**:

- **Propose an organisation**: creates a pull request that adds the organisation to `ledger/organisations.yaml`. RSS feeds and sitemaps found on the home page are added as watch points with `verified: false`; check each URL before merging.
- **Suggest a candidate lead**: creates a pull request that adds the lead to `ledger/candidates.yaml`.

Pull requests are only created for repository collaborators; other suggestions get a reply and are handled by hand. The repository is public, so issues are public too. Create the labels `add-organisation` and `candidate-lead` once (Issues → Labels). The form options are generated from `config/`; after changing the vocabularies, run `uv run agentic-watch issue-templates` (CI checks they are current).

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

Use the web app's **Review records** page to mark each record as kept, blog candidate or discarded (it fills in `status`, `reviewer`, `reviewed` and `review_note` in `data/entries/YYYY-MM.jsonl`). Editing the file directly also works. `needs_review` means the evidence quote, or a challenge or response quote, was not found in the page text.

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
