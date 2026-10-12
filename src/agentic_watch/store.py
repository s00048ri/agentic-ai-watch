"""File-based state: seen URLs, queue, batches, run, usage and entries. No database."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterable, Iterator
from pathlib import Path

from pydantic import BaseModel

from .models import BatchRecord, Entry, QueuedItem, RunState, SeenRecord, UsageRecord


def _read_jsonl(path: Path) -> Iterator[dict[str, object]]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def _append_jsonl(path: Path, rows: Iterable[BaseModel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(row.model_dump_json() + "\n")


def _write_jsonl(path: Path, rows: Iterable[BaseModel]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(row.model_dump_json() + "\n")


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root

    # ------------------------------------------------------------ paths
    @property
    def seen_path(self) -> Path:
        return self.root / "state/seen.jsonl"

    @property
    def queue_path(self) -> Path:
        return self.root / "state/queue.jsonl"

    @property
    def batches_path(self) -> Path:
        return self.root / "state/batches.json"

    @property
    def run_path(self) -> Path:
        return self.root / "state/run.json"

    @property
    def checks_path(self) -> Path:
        return self.root / "state/checks.json"

    @property
    def usage_path(self) -> Path:
        return self.root / "state/usage.jsonl"

    def entries_path(self, month: str) -> Path:
        return self.root / f"data/entries/{month}.jsonl"

    # ------------------------------------------------------------ seen
    def seen(self) -> dict[str, SeenRecord]:
        latest: dict[str, SeenRecord] = {}
        for row in _read_jsonl(self.seen_path):
            rec = SeenRecord.model_validate(row)
            latest[rec.url] = rec
        return latest

    def mark_seen(self, records: Iterable[SeenRecord]) -> None:
        _append_jsonl(self.seen_path, records)

    # ------------------------------------------------------------ queue
    def queue(self) -> list[QueuedItem]:
        return [QueuedItem.model_validate(r) for r in _read_jsonl(self.queue_path)]

    def save_queue(self, items: Iterable[QueuedItem]) -> None:
        _write_jsonl(self.queue_path, items)

    # ------------------------------------------------------------ batches
    def batches(self) -> dict[str, BatchRecord]:
        if not self.batches_path.exists():
            return {}
        raw = json.loads(self.batches_path.read_text(encoding="utf-8") or "{}")
        return {k: BatchRecord.model_validate(v) for k, v in raw.items()}

    def save_batches(self, batches: dict[str, BatchRecord]) -> None:
        data = {k: json.loads(v.model_dump_json()) for k, v in batches.items()}
        self.batches_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # ------------------------------------------------------------ run
    def run(self) -> RunState | None:
        if not self.run_path.exists():
            return None
        return RunState.model_validate_json(self.run_path.read_text(encoding="utf-8"))

    def save_run(self, run: RunState) -> None:
        self.run_path.write_text(run.model_dump_json(indent=2) + "\n", encoding="utf-8")

    def clear_run(self) -> None:
        self.run_path.unlink(missing_ok=True)

    # ------------------------------------------------------------ checks (last check per watch point)
    def checks(self) -> dict[str, dt.date]:
        if not self.checks_path.exists():
            return {}
        raw = json.loads(self.checks_path.read_text(encoding="utf-8") or "{}")
        return {k: dt.date.fromisoformat(v) for k, v in raw.items()}

    def save_checks(self, checks: dict[str, dt.date]) -> None:
        data = {k: v.isoformat() for k, v in sorted(checks.items())}
        self.checks_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    # ------------------------------------------------------------ usage
    def usage(self) -> list[UsageRecord]:
        return [UsageRecord.model_validate(r) for r in _read_jsonl(self.usage_path)]

    def record_usage(self, records: Iterable[UsageRecord]) -> None:
        _append_jsonl(self.usage_path, records)

    def month_tokens(self, month: str) -> int:
        return sum(u.total() for u in self.usage() if u.ts.strftime("%Y-%m") == month)

    # ------------------------------------------------------------ entries
    def add_entries(self, entries: Iterable[Entry]) -> None:
        by_month: dict[str, list[Entry]] = {}
        for e in entries:
            by_month.setdefault(e.detected.strftime("%Y-%m"), []).append(e)
        for month, rows in by_month.items():
            _append_jsonl(self.entries_path(month), rows)

    def entries(self, month: str | None = None) -> list[Entry]:
        paths = [self.entries_path(month)] if month else sorted((self.root / "data/entries").glob("*.jsonl"))
        out: list[Entry] = []
        for p in paths:
            out.extend(Entry.model_validate(r) for r in _read_jsonl(p))
        return out
