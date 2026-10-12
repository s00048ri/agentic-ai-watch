"""Check that the evidence quote appears verbatim in the extracted page text (SPEC.md §4.4)."""

from __future__ import annotations

import re
import unicodedata

# Typographic variants that page text and model output often disagree on.
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES)
    return re.sub(r"\s+", " ", text).strip()


def evidence_in_text(evidence: str, text: str) -> bool:
    """True if `evidence` (whitespace-normalised) is a substring of `text`. Empty evidence never passes."""
    ev = _norm(evidence)
    return bool(ev) and ev in _norm(text)
