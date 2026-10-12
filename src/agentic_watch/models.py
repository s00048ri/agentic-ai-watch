"""Data models for pipeline items, records (entries) and run state."""

from __future__ import annotations

import datetime as dt
import hashlib
from typing import Literal

from pydantic import BaseModel, Field

RelevanceLabel = Literal["deployment", "policy-governance", "research", "product-offering", "not-relevant"]
EntryStatus = Literal["new", "needs_review", "kept", "discarded", "blog_candidate"]
QueueStage = Literal["stage1", "stage2"]


def item_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


class Item(BaseModel):
    """A newly detected page on an organisation's own site.

    `feed_summary` holds the RSS summary in memory for stage 1 only; it is never written to disk.
    """

    id: str
    org_id: str
    url: str
    title: str = ""
    published: dt.date | None = None
    detected: dt.date
    doc_type: str
    language: str = "en"
    lastmod: str | None = None
    content_hash: str | None = None
    feed_summary: str | None = Field(default=None, exclude=True)


class QueuedItem(Item):
    """An item waiting for stage 1 or 2. Carried over to the next run when limits are reached."""

    stage: QueueStage
    relevance: Relevance | None = None
    attempts: int = 0


class Relevance(BaseModel):
    label: RelevanceLabel
    confidence: float = Field(ge=0, le=1)


class Fields(BaseModel):
    sector: str | None = None
    application_area: str | None = None
    deployment_stage: str | None = None
    autonomy_cues: str | None = None
    human_oversight: str | None = None


class Extraction(BaseModel):
    """Stage 2 model output (validated before it becomes an Entry)."""

    summary: str
    evidence: str
    fields: Fields = Fields()
    challenges: list[str] = Field(default_factory=list)
    responses: list[str] = Field(default_factory=list)
    other_organisations_named: list[str] = Field(default_factory=list)


class EntryModels(BaseModel):
    stage1: str
    stage2: str


class Entry(BaseModel):
    """One record in data/entries/YYYY-MM.jsonl (SPEC.md §3.2). No page text is stored."""

    id: str
    org_id: str
    url: str
    title: str
    published: dt.date | None = None
    detected: dt.date
    doc_type: str
    language: str
    relevance: Relevance
    summary: str
    evidence: str = Field(max_length=300)
    evidence_verified: bool
    fields: Fields = Fields()
    challenges: list[str] = Field(default_factory=list)
    responses: list[str] = Field(default_factory=list)
    other_organisations_named: list[str] = Field(default_factory=list)
    status: EntryStatus = "new"
    reviewer: str | None = None
    reviewed: dt.date | None = None
    review_note: str | None = None
    model: EntryModels


SeenOutcome = Literal[
    "detected", "keyword_rejected", "stage1_rejected", "queued", "recorded", "fetch_failed", "skipped"
]


class SeenRecord(BaseModel):
    """One line of state/seen.jsonl. The latest line per URL wins."""

    url: str
    org_id: str
    doc_type: str | None = None
    detected: dt.date
    lastmod: str | None = None
    content_hash: str | None = None
    outcome: SeenOutcome = "detected"


class UsageRecord(BaseModel):
    """One API response's token usage (state/usage.jsonl)."""

    ts: dt.datetime
    run_id: str
    stage: Literal["stage1", "stage2", "synthesis", "evaluate"]
    model: str
    batch: bool
    item_id: str | None = None
    doc_type: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


class RunCounts(BaseModel):
    detected: int = 0
    keyword_passed: int = 0
    stage1_sent: int = 0
    stage1_passed: int = 0
    stage2_sent: int = 0
    recorded: int = 0
    carried_over: int = 0


class RunState(BaseModel):
    """state/run.json — the current weekly run, kept until its digest is posted."""

    run_id: str
    started: dt.date
    counts: RunCounts = RunCounts()
    new_entry_ids: list[str] = Field(default_factory=list)
    manual_watch_points: list[str] = Field(default_factory=list)
    fetch_errors: list[str] = Field(default_factory=list)
    budget_stopped: bool = False
    candidate_changes: list[str] = Field(default_factory=list)
    digest_posted: bool = False


class BatchRecord(BaseModel):
    """state/batches.json value, keyed by batch id."""

    stage: Literal["stage1", "stage2"]
    run_id: str
    model: str
    submitted: dt.datetime
    items: list[QueuedItem]
    reserved_tokens: int = 0
    collected: bool = False


QueuedItem.model_rebuild()
