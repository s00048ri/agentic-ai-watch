"""Loading and validating settings, keywords, taxonomy and the ledger."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[2]

OrgType = Literal[
    "private-sector", "public-sector", "academic-institution", "industry-association", "non-profit"
]
EcosystemRole = Literal["developer", "deployer", "developer-deployer", "regulator"]
Size = Literal["large", "medium", "small", "unknown"]
InclusionReason = Literal[
    "phase2-interviewee",
    "phase2-contacted",
    "haip-reporter",
    "public-body",
    "commitment-signatory",
    "added-from-candidates",
]
OrgStatus = Literal["active", "dormant", "removed"]
WatchPointType = Literal[
    "newsroom",
    "tech-blog",
    "ai-governance-page",
    "responsible-ai-report",
    "ir-annual-report",
    "haip-report",
    "transparency-record",
]
Method = Literal["rss", "sitemap", "page-hash", "manual"]
Frequency = Literal["weekly", "monthly", "annual"]
Thinking = Literal["disabled", "adaptive", "between_tools"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]

# Watch point types sent to stage 1 without the keyword filter (SPEC.md §4.2).
FILTER_BYPASS_TYPES: frozenset[str] = frozenset(
    {"ai-governance-page", "responsible-ai-report", "haip-report", "transparency-record"}
)


def load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------- settings


class Models(BaseModel):
    stage1: str
    stage2: str
    synthesis: str


class PerStage[T](BaseModel):
    stage1: T
    stage2: T
    synthesis: T


class Limits(BaseModel):
    stage1_max_items_per_run: int
    stage2_max_items_per_run: int
    stage1_max_input_chars: int
    stage2_max_input_chars: int
    stage1_max_output_tokens: int
    stage2_max_output_tokens: int
    synthesis_max_output_tokens: int
    monthly_token_budget: int


class Stage1(BaseModel):
    threshold: float = 0.5


class Crawl(BaseModel):
    user_agent: str
    min_interval_seconds_per_domain: float = 1.0
    timeout_seconds: float = 20
    max_sitemap_urls: int = 5000
    first_run_lookback_days: int = 30

    def contact_missing(self) -> bool:
        return "<email>" in self.user_agent


class Price(BaseModel):
    input: float
    output: float


class Pricing(BaseModel):
    models: dict[str, Price]
    cache_write_multiplier: float = 1.25
    cache_read_multiplier: float = 0.10
    batch_multiplier: float = 0.50

    @model_validator(mode="before")
    @classmethod
    def _split(cls, data: Any) -> Any:
        if isinstance(data, dict) and "models" not in data:
            multipliers = {k: v for k, v in data.items() if k.endswith("_multiplier")}
            models = {k: v for k, v in data.items() if not k.endswith("_multiplier")}
            return {"models": models, **multipliers}
        return data


class Settings(BaseModel):
    models: Models
    thinking: PerStage[Thinking]
    effort: PerStage[Effort]
    use_batch: bool = True
    limits: Limits
    stage1: Stage1 = Stage1()
    crawl: Crawl
    pricing: Pricing


# ---------------------------------------------------------------- keywords


class KeywordSet(BaseModel):
    en: list[str] = Field(default_factory=list)
    ja: list[str] = Field(default_factory=list)

    def all(self) -> list[str]:
        return [*self.en, *self.ja]


class Keywords(BaseModel):
    include: KeywordSet
    exclude: KeywordSet


# ---------------------------------------------------------------- taxonomy


class Term(BaseModel):
    id: str
    label: str


class Challenge(BaseModel):
    id: str
    name: str
    summary: str


class Response(BaseModel):
    id: str
    name: str
    summary: str


class Taxonomy(BaseModel):
    challenges: list[Challenge]
    responses: list[Response]
    vocabularies: dict[str, list[Term]]

    def challenge_ids(self) -> set[str]:
        return {c.id for c in self.challenges}

    def response_ids(self) -> set[str]:
        return {r.id for r in self.responses}

    def vocab_ids(self, name: str) -> set[str]:
        return {t.id for t in self.vocabularies.get(name, [])}


# ---------------------------------------------------------------- ledger


class WatchPoint(BaseModel):
    type: WatchPointType
    url: str
    method: Method
    feed_url: str | None = None
    language: str = "en"
    frequency: Frequency = "weekly"
    verified: bool = False

    @model_validator(mode="after")
    def _feed_required(self) -> WatchPoint:
        if self.method == "rss" and not self.feed_url:
            raise ValueError(f"watch point {self.url}: method 'rss' needs feed_url")
        return self


class Organisation(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    name: str
    name_local: str | None = None
    domains: list[str] = Field(min_length=1)
    org_type: OrgType
    ecosystem_role: EcosystemRole
    sector: str
    region: str
    size: Size = "unknown"
    inclusion_reason: InclusionReason
    lead_source: str | None = None
    status: OrgStatus = "active"
    added: dt.date
    last_reviewed: dt.date
    watch_points: list[WatchPoint] = Field(default_factory=list)

    @field_validator("domains")
    @classmethod
    def _lower(cls, v: list[str]) -> list[str]:
        return [d.lower().removeprefix("www.") for d in v]


LeadRoute = Literal["official-filing", "official-mention", "self-reporting-scheme", "third-party", "people"]
Decision = Literal["pending", "add", "reject"]


class Candidate(BaseModel):
    name: str
    lead_route: LeadRoute
    lead_detail: str | None = None
    first_seen: dt.date
    mentions: int = 1
    official_confirmation_url: str | None = None
    decision: Decision = "pending"
    decided: dt.date | None = None


# ---------------------------------------------------------------- project


class Project(BaseModel):
    """All configuration of one checkout, rooted at `root`."""

    root: Path
    settings: Settings
    keywords: Keywords
    taxonomy: Taxonomy
    organisations: list[Organisation]

    model_config = {"arbitrary_types_allowed": True}

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def prompt(self, name: str) -> str:
        return self.path("prompts", f"{name}.md").read_text(encoding="utf-8")

    def organisation(self, org_id: str) -> Organisation:
        for org in self.organisations:
            if org.id == org_id:
                return org
        raise KeyError(org_id)


def load_organisations(path: Path, taxonomy: Taxonomy) -> list[Organisation]:
    orgs = [Organisation.model_validate(o) for o in (load_yaml(path) or [])]
    ids = [o.id for o in orgs]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate organisation ids: {sorted(dupes)}")
    for org in orgs:
        if org.sector not in taxonomy.vocab_ids("sectors"):
            raise ValueError(f"{org.id}: unknown sector '{org.sector}'")
        if org.region not in taxonomy.vocab_ids("regions"):
            raise ValueError(f"{org.id}: unknown region '{org.region}'")
    return orgs


def load_project(root: Path | None = None) -> Project:
    root = root or ROOT
    taxonomy = Taxonomy(
        challenges=[Challenge.model_validate(c) for c in load_yaml(root / "config/taxonomy/challenges.yaml")],
        responses=[Response.model_validate(r) for r in load_yaml(root / "config/taxonomy/responses.yaml")],
        vocabularies={
            k: [Term.model_validate(t) for t in v]
            for k, v in load_yaml(root / "config/taxonomy/vocabularies.yaml").items()
        },
    )
    return Project(
        root=root,
        settings=Settings.model_validate(load_yaml(root / "config/settings.yaml")),
        keywords=Keywords.model_validate(load_yaml(root / "config/keywords.yaml")),
        taxonomy=taxonomy,
        organisations=load_organisations(root / "ledger/organisations.yaml", taxonomy),
    )


def load_candidates(path: Path) -> list[Candidate]:
    return [Candidate.model_validate(c) for c in (load_yaml(path) or [])]
