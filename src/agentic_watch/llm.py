"""Stage 1 (relevance) and stage 2 (extraction) calls, synchronous or via Message Batches.

Cost controls: fixed system prompts marked for prompt caching, structured JSON output,
thinking off with low effort (thinking tokens would count toward the small max_tokens),
and a monthly token budget checked before every call or batch.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import anthropic
from anthropic.types import Message
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from pydantic import ValidationError

from .config import Project
from .models import Extraction, Item, Relevance, UsageRecord
from .store import Store

log = logging.getLogger(__name__)

Stage = Literal["stage1", "stage2", "synthesis"]
RELEVANCE_LABELS = ["deployment", "policy-governance", "research", "product-offering", "not-relevant"]


class BudgetExceeded(Exception):
    pass


# ---------------------------------------------------------------- prompts and schemas


def taxonomy_block(project: Project) -> str:
    """Deterministic rendering of vocabularies and the challenge/response taxonomy for the prompt."""
    tax = project.taxonomy
    lines = ["## Vocabularies (use the id)"]
    for name in ("sectors", "application_areas", "deployment_stages"):
        lines.append(f"### {name}")
        lines.extend(f"- {t.id}: {t.label}" for t in tax.vocabularies.get(name, []))
    lines.append("## Challenges (use the id)")
    lines.extend(f"- {c.id}: {c.name} — {' '.join(c.summary.split())}" for c in tax.challenges)
    lines.append("## Governance responses (use the id)")
    lines.extend(f"- {r.id}: {r.name} — {' '.join(r.summary.split())}" for r in tax.responses)
    return "\n".join(lines)


def system_prompt(project: Project, stage: Stage) -> str:
    name = {"stage1": "relevance", "stage2": "extraction", "synthesis": "synthesis"}[stage]
    text = project.prompt(name)
    return text.replace("{{TAXONOMY}}", taxonomy_block(project))


def _nullable_enum(values: Iterable[str]) -> dict[str, Any]:
    return {"anyOf": [{"type": "string", "enum": sorted(values)}, {"type": "null"}]}


def stage1_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "label": {"type": "string", "enum": RELEVANCE_LABELS},
            "confidence": {"type": "number"},
        },
        "required": ["label", "confidence"],
        "additionalProperties": False,
    }


def stage2_schema(project: Project) -> dict[str, Any]:
    tax = project.taxonomy
    nullable_str = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "evidence": {"type": "string"},
            "fields": {
                "type": "object",
                "properties": {
                    "sector": _nullable_enum(tax.vocab_ids("sectors")),
                    "application_area": _nullable_enum(tax.vocab_ids("application_areas")),
                    "deployment_stage": _nullable_enum(tax.vocab_ids("deployment_stages")),
                    "autonomy_cues": nullable_str,
                    "human_oversight": nullable_str,
                },
                "required": [
                    "sector",
                    "application_area",
                    "deployment_stage",
                    "autonomy_cues",
                    "human_oversight",
                ],
                "additionalProperties": False,
            },
            "challenges": {"type": "array", "items": {"type": "string", "enum": sorted(tax.challenge_ids())}},
            "responses": {"type": "array", "items": {"type": "string", "enum": sorted(tax.response_ids())}},
            "other_organisations_named": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary", "evidence", "fields", "challenges", "responses", "other_organisations_named"],
        "additionalProperties": False,
    }


def _thinking(mode: str) -> dict[str, Any]:
    return {"type": mode}


def stage1_input(item: Item, text: str, max_chars: int) -> str:
    """Title plus the opening of the RSS summary or page text, at most `max_chars` in total."""
    head = f"Title: {item.title}\nURL: {item.url}\n\n"
    body = " ".join(text.split())
    return (head + body)[:max_chars]


def build_params(project: Project, stage: Literal["stage1", "stage2"], user_text: str) -> dict[str, Any]:
    s = project.settings
    max_tokens = s.limits.stage1_max_output_tokens if stage == "stage1" else s.limits.stage2_max_output_tokens
    schema = stage1_schema() if stage == "stage1" else stage2_schema(project)
    return {
        "model": getattr(s.models, stage),
        "max_tokens": max_tokens,
        "system": [
            {"type": "text", "text": system_prompt(project, stage), "cache_control": {"type": "ephemeral"}}
        ],
        "messages": [{"role": "user", "content": user_text}],
        "thinking": _thinking(getattr(s.thinking, stage)),
        "output_config": {
            "effort": getattr(s.effort, stage),
            "format": {"type": "json_schema", "schema": schema},
        },
    }


def stage2_input(item: Item, text: str, max_chars: int) -> str:
    head = f"Organisation id: {item.org_id}\nTitle: {item.title}\nURL: {item.url}\n\nPage text:\n"
    return head + text[:max_chars]


# ---------------------------------------------------------------- parsing


class ParseError(Exception):
    pass


def message_json(message: Message) -> dict[str, Any]:
    if message.stop_reason not in ("end_turn", "stop_sequence"):
        raise ParseError(f"stop_reason={message.stop_reason}")
    text = next((b.text for b in message.content if b.type == "text"), None)
    if text is None:
        raise ParseError("no text block")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ParseError(str(e)) from e
    if not isinstance(data, dict):
        raise ParseError("not an object")
    return data


def parse_stage1(message: Message) -> Relevance:
    data = message_json(message)
    try:
        conf = min(max(float(data.get("confidence", 0)), 0.0), 1.0)
        return Relevance(label=data["label"], confidence=conf)
    except (KeyError, TypeError, ValueError, ValidationError) as e:
        raise ParseError(str(e)) from e


def parse_stage2(project: Project, message: Message) -> Extraction:
    data = message_json(message)
    try:
        ext = Extraction.model_validate(data)
    except ValidationError as e:
        raise ParseError(str(e)) from e
    tax = project.taxonomy
    # Keep only ids that exist in the copied taxonomy and vocabularies.
    ext.challenges = [c for c in dict.fromkeys(ext.challenges) if c in tax.challenge_ids()]
    ext.responses = [r for r in dict.fromkeys(ext.responses) if r in tax.response_ids()]
    f = ext.fields
    if f.sector not in tax.vocab_ids("sectors"):
        f.sector = None
    if f.application_area not in tax.vocab_ids("application_areas"):
        f.application_area = None
    if f.deployment_stage not in tax.vocab_ids("deployment_stages"):
        f.deployment_stage = None
    ext.evidence = ext.evidence.strip()[:300]
    return ext


# ---------------------------------------------------------------- usage and budget


def usage_record(
    message: Message,
    run_id: str,
    stage: Literal["stage1", "stage2", "synthesis", "evaluate"],
    batch: bool,
    item: Item | None = None,
) -> UsageRecord:
    u = message.usage
    return UsageRecord(
        ts=dt.datetime.now(dt.UTC),
        run_id=run_id,
        stage=stage,
        model=message.model,
        batch=batch,
        item_id=item.id if item else None,
        doc_type=item.doc_type if item else None,
        input_tokens=u.input_tokens or 0,
        output_tokens=u.output_tokens or 0,
        cache_creation_input_tokens=u.cache_creation_input_tokens or 0,
        cache_read_input_tokens=u.cache_read_input_tokens or 0,
    )


def estimate_tokens(params: dict[str, Any]) -> int:
    """Conservative upper estimate: about two characters per token (Japanese is near one) plus max_tokens."""
    chars = sum(len(b["text"]) for b in params["system"]) + sum(len(m["content"]) for m in params["messages"])
    return chars // 2 + int(params["max_tokens"])


class Budget:
    """Monthly token budget (SPEC.md §6). Usage already recorded counts; reservations cover pending work."""

    def __init__(self, project: Project, store: Store, today: dt.date) -> None:
        self.limit = project.settings.limits.monthly_token_budget
        self.month = today.strftime("%Y-%m")
        self.used = store.month_tokens(self.month)
        self.reserved = 0

    @property
    def remaining(self) -> int:
        return self.limit - self.used - self.reserved

    def reserve(self, tokens: int) -> None:
        if tokens > self.remaining:
            raise BudgetExceeded(
                f"monthly token budget would be exceeded: used {self.used:,} + reserved {self.reserved:,}"
                f" + {tokens:,} > {self.limit:,}"
            )
        self.reserved += tokens

    def settle(self, reserved: int, actual: int) -> None:
        self.reserved -= reserved
        self.used += actual


# ---------------------------------------------------------------- client


class BatchResult(Protocol):
    custom_id: str
    result: Any


class LLMClient(Protocol):
    def create(self, params: dict[str, Any]) -> Message: ...
    def create_batch(self, requests: list[tuple[str, dict[str, Any]]]) -> str: ...
    def batch_status(self, batch_id: str) -> str: ...
    def batch_results(self, batch_id: str) -> Iterator[BatchResult]: ...


class AnthropicClient:
    """Thin wrapper over the Anthropic SDK so the pipeline can be tested with a fake."""

    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        self.client = client or anthropic.Anthropic()

    def create(self, params: dict[str, Any]) -> Message:
        message: Message = self.client.messages.create(**params)
        return message

    def create_batch(self, requests: list[tuple[str, dict[str, Any]]]) -> str:
        batch = self.client.messages.batches.create(
            requests=[
                Request(custom_id=cid, params=MessageCreateParamsNonStreaming(**params))  # type: ignore[typeddict-item]
                for cid, params in requests
            ]
        )
        return batch.id

    def batch_status(self, batch_id: str) -> str:
        return self.client.messages.batches.retrieve(batch_id).processing_status

    def batch_results(self, batch_id: str) -> Iterator[BatchResult]:
        yield from self.client.messages.batches.results(batch_id)


@dataclass
class CallOutcome[T]:
    value: T | None
    usage: list[UsageRecord]
    error: str | None = None


def call_with_retry[T](
    client: LLMClient,
    params: dict[str, Any],
    parse: Any,
    budget: Budget,
    run_id: str,
    stage: Literal["stage1", "stage2", "synthesis", "evaluate"],
    item: Item | None,
) -> CallOutcome[T]:
    """Synchronous call; one retry if the output cannot be parsed (SPEC.md §4.3)."""
    usage: list[UsageRecord] = []
    error = None
    for _attempt in range(2):
        est = estimate_tokens(params)
        budget.reserve(est)
        message = client.create(params)
        rec = usage_record(message, run_id, stage, batch=False, item=item)
        usage.append(rec)
        budget.settle(est, rec.total())
        try:
            return CallOutcome(parse(message), usage)
        except ParseError as e:
            error = str(e)
            log.warning("parse failed for %s (%s); %s", item.url if item else stage, stage, e)
    return CallOutcome(None, usage, error)
