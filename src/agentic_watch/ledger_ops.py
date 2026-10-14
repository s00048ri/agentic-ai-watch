"""Edits used by the web app and the issue-form workflow.

Every function takes the current file text and returns the new text, so an edit can be
re-applied safely if the file changed in the meantime (see repo.py).
"""

from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata
from typing import Any

import yaml
from pydantic import ValidationError

from .config import Candidate, Organisation, Taxonomy, load_organisations_data
from .leads import normalise_name
from .models import Entry, EntryStatus

ORGANISATIONS_HEADER = (
    "# Watch list (SPEC.md §3.1). Edited by hand, through the web app, or from issue forms.\n"
)
CANDIDATES_HEADER = (
    '# Candidate organisations (SPEC.md §3.3). "official-mention" rows are added automatically;\n'
    "# other lead routes are added by hand or through the web app.\n"
)


class LedgerError(ValueError):
    """A change that would make the ledger invalid. The message is shown to the user."""


# ---------------------------------------------------------------- entries


def parse_entries(text: str | None) -> list[Entry]:
    if not text:
        return []
    return [Entry.model_validate_json(line) for line in text.splitlines() if line.strip()]


def set_entry_status(
    text: str | None,
    entry_id: str,
    status: EntryStatus,
    reviewer: str,
    note: str | None,
    today: dt.date,
    categories: dict[str, list[str]] | None = None,
) -> str:
    """Update one record's review fields; every other line is kept byte for byte.

    `categories` maps "challenges" / "responses" to the reviewer's category for each finding, in order.
    """
    if not text:
        raise LedgerError(f"record {entry_id} not found")
    out: list[str] = []
    found = False
    for line in text.splitlines():
        if line.strip() and json.loads(line).get("id") == entry_id:
            entry = Entry.model_validate_json(line)
            entry.status = status
            entry.reviewer = reviewer
            entry.reviewed = today
            entry.review_note = (note or "").strip() or None
            for kind, chosen in (categories or {}).items():
                findings = entry.challenges if kind == "challenges" else entry.responses
                if len(chosen) != len(findings):
                    raise LedgerError(f"record {entry_id} changed; please reload and try again")
                for finding, category in zip(findings, chosen, strict=True):
                    finding.category = category
            line = entry.model_dump_json()
            found = True
        out.append(line)
    if not found:
        raise LedgerError(f"record {entry_id} not found")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- organisations


def slugify(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "organisation"


def _orgs_dump(rows: list[dict[str, Any]]) -> str:
    body = yaml.safe_dump(rows, sort_keys=False, allow_unicode=True) if rows else "[]\n"
    return ORGANISATIONS_HEADER + body


def _orgs_rows(text: str | None) -> list[dict[str, Any]]:
    rows = yaml.safe_load(text or "") or []
    if not isinstance(rows, list):
        raise LedgerError("ledger/organisations.yaml is not a list")
    return rows


def _validate_orgs(rows: list[dict[str, Any]], taxonomy: Taxonomy) -> None:
    try:
        load_organisations_data(rows, taxonomy)
    except (ValidationError, ValueError) as e:
        raise LedgerError(str(e)) from e


def add_organisation(text: str | None, org: dict[str, Any], taxonomy: Taxonomy) -> str:
    rows = _orgs_rows(text)
    if any(r.get("id") == org.get("id") for r in rows):
        raise LedgerError(f"An organisation with id '{org.get('id')}' already exists")
    clean = Organisation.model_validate(org).model_dump(mode="json")
    for wp in clean["watch_points"]:
        if wp.get("scope") is None:
            wp.pop("scope", None)  # only links watch points use it
    rows.append(clean)
    _validate_orgs(rows, taxonomy)
    return _orgs_dump(rows)


def update_organisation(
    text: str | None, org_id: str, changes: dict[str, Any], taxonomy: Taxonomy, today: dt.date
) -> str:
    rows = _orgs_rows(text)
    for row in rows:
        if row.get("id") == org_id:
            row.update(changes)
            row["last_reviewed"] = today.isoformat()
            break
    else:
        raise LedgerError(f"organisation '{org_id}' not found")
    _validate_orgs(rows, taxonomy)
    return _orgs_dump(rows)


# ---------------------------------------------------------------- candidates


def _cands(text: str | None) -> list[Candidate]:
    rows = yaml.safe_load(text or "") or []
    return [Candidate.model_validate(r) for r in rows]


def _cands_dump(cands: list[Candidate]) -> str:
    rows = [c.model_dump(mode="json") for c in cands]
    return CANDIDATES_HEADER + (yaml.safe_dump(rows, sort_keys=False, allow_unicode=True) if rows else "[]\n")


def decide_candidate(text: str | None, name: str, decision: str, today: dt.date) -> str:
    cands = _cands(text)
    key = normalise_name(name)
    for c in cands:
        if normalise_name(c.name) == key:
            c.decision = decision  # type: ignore[assignment]
            c.decided = today
            return _cands_dump(cands)
    raise LedgerError(f"candidate '{name}' not found")


def add_candidate(text: str | None, cand: dict[str, Any], today: dt.date) -> str:
    cands = _cands(text)
    new = Candidate.model_validate({"first_seen": today.isoformat(), **cand})
    key = normalise_name(new.name)
    for c in cands:
        if normalise_name(c.name) == key:
            c.mentions += 1
            if new.lead_detail:
                c.lead_detail = f"{c.lead_detail}; {new.lead_detail}" if c.lead_detail else new.lead_detail
            return _cands_dump(cands)
    cands.append(new)
    return _cands_dump(cands)
