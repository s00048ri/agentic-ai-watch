"""Add organisations named in records to ledger/candidates.yaml (SPEC.md §4.5)."""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from pathlib import Path

import yaml

from .config import Candidate, Organisation, load_candidates
from .models import Entry

_SUFFIXES = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|llc|plc|gmbh|ag|sa|nv|bv|kk|holdings?|group)\b\.?"
)
_JA_SUFFIXES = re.compile(r"(株式会社|有限会社|合同会社|ホールディングス|グループ)")


def normalise_name(name: str) -> str:
    text = unicodedata.normalize("NFKC", name).casefold()
    text = _JA_SUFFIXES.sub("", text)
    text = _SUFFIXES.sub("", text)
    return re.sub(r"[\s\W_]+", "", text)


def _dump(candidates: list[Candidate]) -> str:
    header = (
        '# Candidate organisations (SPEC.md §3.3). "official-mention" rows are added automatically;\n'
        "# add other lead routes by hand.\n"
    )
    rows = [c.model_dump(mode="json") for c in candidates]
    return header + yaml.safe_dump(rows, sort_keys=False, allow_unicode=True) if rows else header + "[]\n"


def update_candidates(
    path: Path, entries: list[Entry], organisations: list[Organisation], today: dt.date
) -> list[str]:
    """Add or count up `official-mention` candidates. Returns a line per change for the digest."""
    known = {normalise_name(o.name) for o in organisations}
    known |= {normalise_name(o.name_local) for o in organisations if o.name_local}
    candidates = load_candidates(path)
    by_key = {normalise_name(c.name): c for c in candidates}
    changes: list[str] = []
    for entry in entries:
        for name in dict.fromkeys(entry.other_organisations_named):
            key = normalise_name(name)
            if not key or key in known:
                continue
            detail = f"named in {entry.org_id} page {entry.published or entry.detected}: {entry.url}"
            cand = by_key.get(key)
            if cand is None:
                cand = Candidate(
                    name=name.strip(), lead_route="official-mention", lead_detail=detail, first_seen=today
                )
                candidates.append(cand)
                by_key[key] = cand
                changes.append(f"{cand.name}: new ({entry.org_id})")
            else:
                cand.mentions += 1
                changes.append(f"{cand.name}: {cand.mentions} mentions")
    if changes:
        path.write_text(_dump(candidates), encoding="utf-8")
    return changes
