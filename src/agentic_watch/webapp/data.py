"""Data access for the web app. Reads are cached for a minute; every write clears the cache."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import streamlit as st
import yaml

from ..config import ROOT, Candidate, Project, load_project
from ..models import BatchRecord, Entry, QueuedItem, RunState, UsageRecord
from ..repo import Edit, GitHubRepo, LocalRepo, Repo
from .i18n import t


def _secrets() -> dict[str, Any]:
    try:
        return {k: dict(v) if hasattr(v, "items") else v for k, v in st.secrets.items()}
    except Exception:  # no secrets file (local development)
        return {}


def _repo_config() -> tuple[str, ...]:
    gh = _secrets().get("github")
    if gh:
        return ("github", gh["owner"], gh["repo"], gh["token"], gh.get("branch", "main"))
    return ("local", os.environ.get("AGENTIC_WATCH_ROOT") or str(ROOT))


@st.cache_resource
def _make_repo(config: tuple[str, ...]) -> Repo:
    if config[0] == "github":
        _kind, owner, name, token, branch = config
        return GitHubRepo(owner=owner, name=name, token=token, branch=branch)
    return LocalRepo(root=Path(config[1]))


def repo() -> Repo:
    return _make_repo(_repo_config())


def is_local() -> bool:
    return isinstance(repo(), LocalRepo)


@st.cache_data(ttl=60, show_spinner=False)
def _read(config: tuple[str, ...], path: str) -> str | None:
    return _make_repo(config).read(path)


@st.cache_data(ttl=60, show_spinner=False)
def _list_dir(config: tuple[str, ...], path: str) -> list[str]:
    return _make_repo(config).list_dir(path)


def read(path: str) -> str | None:
    return _read(_repo_config(), path)


def list_dir(path: str) -> list[str]:
    return _list_dir(_repo_config(), path)


def write(paths: list[str], edit: Edit, message: str, lang: str) -> bool:
    """Apply an edit and commit it. Shows the outcome; returns True on success."""
    from ..ledger_ops import LedgerError
    from ..repo import ConflictError

    try:
        repo().apply(paths, edit, message)
    except LedgerError as e:
        st.error(f"{t('save_failed', lang)}: {e}")
        return False
    except ConflictError:
        st.error(t("conflict", lang))
        return False
    finally:
        st.cache_data.clear()
    st.toast(t("saved", lang))
    return True


def clear() -> None:
    st.cache_data.clear()


# ---------------------------------------------------------------- typed views of the files


def project() -> Project:
    return load_project(ROOT, organisations_text=read("ledger/organisations.yaml") or "[]")


def entries() -> list[Entry]:
    from ..ledger_ops import parse_entries

    out: list[Entry] = []
    for path in list_dir("data/entries"):
        if path.endswith(".jsonl"):
            out.extend(parse_entries(read(path)))
    return sorted(out, key=lambda e: (e.detected, e.id), reverse=True)


def entry_path(entry: Entry) -> str:
    return f"data/entries/{entry.detected.strftime('%Y-%m')}.jsonl"


def candidates() -> list[Candidate]:
    rows = yaml.safe_load(read("ledger/candidates.yaml") or "") or []
    return [Candidate.model_validate(r) for r in rows]


def checks() -> dict[str, dt.date]:
    raw = json.loads(read("state/checks.json") or "{}")
    return {k: dt.date.fromisoformat(v) for k, v in raw.items()}


def run_state() -> RunState | None:
    text = read("state/run.json")
    return RunState.model_validate_json(text) if text else None


def usage() -> list[UsageRecord]:
    text = read("state/usage.jsonl") or ""
    return [UsageRecord.model_validate_json(line) for line in text.splitlines() if line.strip()]


def batches() -> dict[str, BatchRecord]:
    raw = json.loads(read("state/batches.json") or "{}")
    return {k: BatchRecord.model_validate(v) for k, v in raw.items()}


def queue() -> list[QueuedItem]:
    text = read("state/queue.jsonl") or ""
    return [QueuedItem.model_validate_json(line) for line in text.splitlines() if line.strip()]


def weekly_reports() -> list[str]:
    return sorted((p for p in list_dir("reports/weekly") if p.endswith(".md")), reverse=True)
