# SPEC — Agentic AI Watch: pilot monitoring of official publications (pipeline)

English | [日本語](SPEC.ja.md)

Version: 0.1 (2026-10-12) / Owner: Rentaro Iida (GPAI Tokyo Centre)
Audience: Claude Code. Placed at the repository root; the implementation is based on it.

> This is a translation of the Japanese original ([SPEC.ja.md](SPEC.ja.md)). If the two differ, the Japanese version prevails.

---

## 0. Purpose and scope

- For a fixed list of organisations (the watch list), check **only what each organisation publishes on its own official website**, once a week; find new publications on the adoption of and policies for agentic AI; and accumulate them as structured records.
- **Internal use only.** Results are not published on websites or elsewhere (files in this repository aside). The team reviews them weekly and uses them as material for a regular blog.
- A 4-week pilot, starting with 20–30 organisations.
- **Keeping API tokens and cost to a minimum** is the most important design constraint.

### Out of scope
- Using the content of third-party sites (news, other trackers, vendors' customer stories) in records. Third-party information is only noted by hand in the ledger as a lead to a "candidate organisation"
- Exploration with web search tools or autonomous browsing (token use is unpredictable)
- Publishing the results on websites or elsewhere; assessing, scoring or ranking organisations
- Storing full page text
- Crawling pages other than those registered on the watch list

## 1. Principles (guardrails)

1. **Primary sources only**: a record's source URL must be on the organisation's own domain (a domain registered in the ledger). URLs on other domains are not recorded
2. **Reduce before the LLM**: new-item detection and keyword filtering are done without an LLM, to minimise the number of items sent to the LLM
3. **No guessing**: fields not stated in the source are left empty (null). The model is not asked to infer
4. **Verify the evidence**: code checks that the "evidence sentence" obtained at extraction appears verbatim in the fetched page text. If it does not, the record is set to `needs_review`
5. **Polite access**: respect robots.txt. At least 1 second between requests to the same domain. Weekly frequency. A contact address in the User-Agent
6. **Always respect the limits**: when the per-run item limits or the monthly token limit (§6) are exceeded, stop processing and notify Slack

## 2. Technical stack

- Python 3.12, dependencies managed with `uv` (or pip + `requirements.txt`)
- Main libraries: `httpx`, `feedparser`, `pyyaml`, `pydantic`, `trafilatura` (main-text extraction), `anthropic`, `python-dateutil`
- Runtime: **GitHub Actions (public repository)**. The same commands must work on a local Mac
- Storage: files in the repository (YAML / JSONL). No database
- Notifications: Slack Incoming Webhook (#agentic_ai)
- Secrets: `ANTHROPIC_API_KEY`, `SLACK_WEBHOOK_URL`
- Prerequisite: use of external APIs and management of API keys have been cleared under the institute's rules (handled by Ren)

### Directory layout
```
/SPEC.md
/README.md
/ledger/
  organisations.yaml      # watch list (§3.1)
  candidates.yaml         # candidates for addition (§3.3)
/config/
  settings.yaml           # model names, limits, frequency, etc. (§6)
  keywords.yaml           # include and exclude terms (§4.2)
  taxonomy/
    challenges.yaml       # copied from the living resource's taxonomy
    responses.yaml
/prompts/
  relevance.md            # stage 1 instructions (fixed, cached)
  extraction.md           # stage 2 instructions (fixed, cached)
  synthesis.md            # monthly summary instructions
/state/
  seen.jsonl              # detected URLs and content hashes
  usage.jsonl             # token use per run
/data/
  entries/YYYY-MM.jsonl   # records (§3.2)
  gold.yaml               # reference data for accuracy checks (§8)
/reports/
  weekly/YYYY-MM-DD.md    # copies of weekly digests
  monthly/YYYY-MM.md      # monthly summaries
/src/agentic_watch/
  cli.py  fetch.py  filter.py  llm.py  verify.py  store.py  digest.py  leads.py  evaluate.py  discover.py
/tests/
/.github/workflows/
  weekly.yml  collect.yml  monthly.yml  ci.yml
```

## 3. Data model

### 3.1 `ledger/organisations.yaml` (watch list)
```yaml
- id: example-org                  # lower-case letters and hyphens
  name: Example Organisation
  name_local: 例示組織              # optional
  domains: [example.com]           # domains accepted as sources
  org_type: private-sector         # private-sector | public-sector | academic-institution | industry-association | non-profit
  ecosystem_role: deployer         # developer | deployer | developer-deployer | regulator
  sector: telecommunications       # living resource vocabulary
  region: asia-pacific
  size: large                      # large | medium | small | unknown
  inclusion_reason: phase2-interviewee
    # phase2-interviewee | phase2-contacted | haip-reporter | public-body | commitment-signatory | added-from-candidates
  lead_source: null                # where the lead came from, if added from candidates
  status: active                   # active | dormant | removed
  added: 2026-10-13
  last_reviewed: 2026-10-13
  watch_points:
    - type: newsroom               # newsroom | tech-blog | ai-governance-page | responsible-ai-report | ir-annual-report | haip-report | transparency-record
      url: https://example.com/news
      method: rss                  # rss | sitemap | page-hash | manual
      feed_url: https://example.com/news/rss.xml
      language: en
      frequency: weekly            # weekly | monthly | annual
      verified: false              # true once a person has checked the URL
```
- **Claude Code does not create organisation names or URLs.** It provides only the template and a helper script that finds RSS feeds and sitemaps from a domain (§4.6); Ren fills in the content.

### 3.2 `data/entries/YYYY-MM.jsonl` (records)
One record per line, validated with a pydantic model.
```json
{
  "id": "sha1(url)",
  "org_id": "example-org",
  "url": "https://example.com/news/2026/agent-pilot",
  "title": "...",
  "published": "2026-10-10",
  "detected": "2026-10-13",
  "doc_type": "newsroom",
  "language": "en",
  "relevance": {"label": "deployment", "confidence": 0.82},
    // label: deployment | policy-governance | research | product-offering | not-relevant
  "summary": "In our own words, at most two sentences (English)",
  "evidence": "Verbatim quote from the source (at most 300 characters)",
  "evidence_verified": true,
  "fields": {
    "sector": null, "application_area": null, "deployment_stage": null,
    "autonomy_cues": null, "human_oversight": null
  },
  "challenges": ["challenge-id"],
  "responses": ["response-id"],
  "other_organisations_named": ["Partner Co."],
  "status": "new",
    // new | needs_review | kept | discarded | blog_candidate
  "reviewer": null,
  "model": {"stage1": "claude-haiku-5-5", "stage2": "claude-haiku-5-5"}
}
```
- `fields`, `challenges` and `responses` are null / empty arrays unless the source states them explicitly
- Page text is not stored (it is handled only in memory during processing)

### 3.3 `ledger/candidates.yaml` (candidates for addition)
```yaml
- name: Candidate Org
  lead_route: official-mention     # official-filing | official-mention | self-reporting-scheme | third-party | people
  lead_detail: "named in example-org release 2026-10-10"
  first_seen: 2026-10-13
  mentions: 1
  official_confirmation_url: null  # a page on the organisation's own site that confirms it
  decision: pending                # pending | add | reject
  decided: null
```
- `official-mention` rows are added, and their counts increased, automatically from records' `other_organisations_named` (§4.5)
- Other routes (third-party leads, self-nomination, introductions, etc.) are added by hand

## 4. Processing flow

### 4.1 Change detection (no LLM) — `fetch.py`
- Check each watch point of organisations with `status: active` according to its `frequency`
- `rss`: fetch the feed; URLs not yet detected are new
- `sitemap`: fetch sitemap.xml; URLs not yet detected, or whose `lastmod` has been updated, are new
- `page-hash`: compare a hash of the page text with the previous one; a change counts as new
- `manual`: not processed (only listed as "check by hand" in the weekly digest)
- Exclude new URLs whose domain is not in the organisation's `domains`
- Remove duplicates using `state/seen.jsonl` (URL normalisation: remove tracking query parameters, unify the trailing slash)

### 4.2 Keyword filter (no LLM) — `filter.py`
- Applied to: title, URL path, RSS summary (if any)
- Pass items that match at least one include term and no exclude term in `config/keywords.yaml`
- Match after normalising case and full-width/half-width forms
- Initial values:
```yaml
include:
  en: ["agentic", "ai agent", "ai agents", "multi-agent", "multiagent", "autonomous agent", "agentic workflow", "agent platform"]
  ja: ["AIエージェント", "エージェント型AI", "エージェンティックAI", "自律型AI", "マルチエージェント", "AI agent"]
exclude:
  en: ["travel agent", "insurance agent", "real estate agent", "user agent", "secret agent", "free agent"]
  ja: ["代理店", "エージェント契約", "人材エージェント", "転職エージェント"]
```
- Watch points of type `ai-governance-page`, `responsible-ai-report`, `haip-report` and `transparency-record` go to stage 1 without the keyword filter (they are few, and likely to describe challenges and responses)

### 4.3 Stage 1: relevance (small model) — `llm.py`
- Input: title plus the opening text (the RSS summary, or else the start of the extracted page text), **at most 1,500 characters in total**
- Output: only `{"label": ..., "confidence": 0-1}` (small `max_tokens`)
- The instructions (`prompts/relevance.md`) are fixed and use **prompt caching**
- Items with `confidence < settings.stage1.threshold` (initially 0.5), or labelled `not-relevant`, do not go to stage 2
- Return structured output (JSON schema / tool use); if parsing fails, retry once

### 4.4 Stage 2: field extraction (small model)
- Only for items that passed stage 1: fetch the page, extract the main text with `trafilatura`, and truncate it to **the first 6,000 characters**
- The instructions (`prompts/extraction.md`) include the living resource's vocabularies and the `config/taxonomy/` classification (ids and short definitions), and are cached
- Output: `summary`, `evidence`, `fields`, `challenges`, `responses` and `other_organisations_named` from §3.2
- Rules the instructions must include:
  - Anything not stated explicitly in the source is null / an empty array. No guessing
  - `evidence` is a verbatim quote from the page text (at most 300 characters)
  - `challenges` / `responses` are tagged with taxonomy ids only where the page text states them explicitly
  - `summary` is in English, at most two sentences, without evaluative adjectives
- `verify.py`: check that `evidence` appears verbatim in the extracted text (substring match after normalising whitespace and line breaks). If not, `evidence_verified: false` and `status: needs_review`

### 4.5 Storage and candidate updates — `store.py`, `leads.py`
- Append records to `data/entries/YYYY-MM.jsonl` and update `state/seen.jsonl`
- Add the organisation names in `other_organisations_named` to `candidates.yaml` (or increase `mentions` if already there). Skip organisations already on the watch list (matched by normalised name)

### 4.6 Helper: finding pages to watch — `discover.py` (no LLM)
- Input: the organisation's home page URL
- Processing: check the HTML for `<link rel="alternate" type="application/rss+xml">`, the `Sitemap:` lines in robots.txt, and whether `/sitemap.xml` exists
- Output: print watch point candidates as YAML to standard output (nothing is written to the ledger automatically; a person checks and pastes them)

### 4.7 Weekly digest — `digest.py`
Posted to #agentic_ai, with a copy saved in `reports/weekly/`.
- This week's counts (new detections → passed keyword filter → passed stage 1 → recorded)
- List of new records: organisation name, title (link), one-line summary, label, confidence, challenges and responses mentioned
- List of `needs_review` records
- Candidate organisations whose `mentions` increased
- List of watch points that need checking by hand
- This week's token use and estimated cost, as a share of the monthly limit
- Reviewers update a record's `status` to `kept` / `discarded` / `blog_candidate` by hand (during the pilot, editing the YAML/JSONL directly is fine)

### 4.8 Monthly summary — `cli.py synthesize --month YYYY-MM`
- Run manually (`workflow_dispatch`)
- Input: **only the structured data** of that month's `kept` and `blog_candidate` records (no page text)
- Call a larger model once and write trend notes (blog material) to `reports/monthly/YYYY-MM.md`
- The instructions require using only facts with source links, and writing with a qualifier such as "as far as has been published"

## 5. Execution (GitHub Actions)

| workflow | Trigger | What it does |
|---|---|---|
| `weekly.yml` | Every Monday 06:00 JST (cron) + manual | Detection → filter → submit stage 1 |
| `collect.yml` | Every two hours (cron) + manual | Collect pending batch results → submit/collect stage 2 → save → post the weekly digest when all stages are complete |
| `monthly.yml` | Manual | Monthly summary |
| `ci.yml` | push / PR | Lint, type check, tests |

- Stages 1 and 2 use the **Message Batches API** (half price). Batch IDs are saved in `state/batches.json`, and `collect.yml` collects them idempotently
- `use_batch: false` in `settings.yaml` switches to the synchronous API (with prompt caching), for small volumes and local trials
- State file updates are committed by a bot at the end of the workflow (with a neutral commit message: `chore: weekly run 2026-10-19`)

## 6. Settings and limits (`config/settings.yaml`)
```yaml
models:
  stage1: claude-haiku-5-5
  stage2: claude-haiku-5-5
  synthesis: claude-sonnet-5-5
use_batch: true
limits:
  stage1_max_items_per_run: 200
  stage2_max_items_per_run: 40
  stage1_max_input_chars: 1500
  stage2_max_input_chars: 6000
  stage1_max_output_tokens: 100
  stage2_max_output_tokens: 800
  synthesis_max_output_tokens: 3000
  monthly_token_budget: 3000000     # input + output combined (cache reads are also recorded)
stage1:
  threshold: 0.5
crawl:
  user_agent: "GPAI-Tokyo-Centre-research-pilot/0.1 (+contact: <email>)"
  min_interval_seconds_per_domain: 1.0
  timeout_seconds: 20
```
- On every run, record the API response's `usage` (input, output, cache creation, cache read) in `state/usage.jsonl`
- When the month's running total is expected to exceed `monthly_token_budget`, stop LLM calls at that point, carry the rest over to the next week, and notify Slack
- Items beyond the count limits are carried over to the next week in order of detection date (oldest first), not by confidence
- Ren sets the monthly spend limit separately in the API console

## 7. Operating rules
- People add and remove organisations by editing `organisations.yaml`. Once a quarter, review `candidates.yaml` and decide
- Organisations with no relevant record for 12 months are changed to `dormant` by a person (not automatically)
- The taxonomy (`config/taxonomy/`) is a copy of the latest version from the living resource. Category names are not hard-coded

## 8. Accuracy checks — `evaluate.py`
- `data/gold.yaml` holds about 30 items judged by people (URL, correct label, relevant or not) (created by Ren)
- `cli.py evaluate`: run stage 1 on the gold items and output precision, recall and a confusion matrix
- At the end of the pilot, aggregate by source (watch point type) "new detections, records, share of records with challenges or responses, tokens per useful record" into `reports/pilot-summary.md`

## 9. Tests and acceptance criteria
- `--no-llm` (dry run) runs detection and filtering without the API
- Tests: URL normalisation and de-duplication, keyword filter (include terms, exclude terms, full-width/half-width), exclusion of other domains, robots.txt compliance, verification of the evidence sentence, stopping when limits are exceeded
- Tests that would use the real API use mocks; CI does not use an API key
- No file stores full page text
- The token use of each weekly run is recorded in `usage.jsonl` and shown in the digest
- The README covers setup (secrets, hooks), how to write the ledger, and how to run things manually

## 10. Priorities
| Priority | Scope |
|---|---|
| P0 | Ledger template and schema, detection (RSS, sitemaps), keyword filter, stages 1 and 2 (start with the synchronous API), evidence verification, storage, weekly digest, limits and usage records, dry run |
| P1 | Batch API and `collect.yml`, automatic candidate updates, `discover.py`, `evaluate.py` |
| P2 | `page-hash` method, monthly summary, end-of-pilot summary report |

Where something is unclear, do not decide unilaterally; collect the points and return them as questions.
