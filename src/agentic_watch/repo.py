"""Read and write repository files, locally or through the GitHub API.

The web app has no checkout of its own: on Streamlit Community Cloud it reads files from GitHub
and commits changes back. Edits are expressed as functions applied to the *current* file
contents, so when the weekly bot (or another reviewer) has pushed in between, the edit is simply
re-applied to the fresh contents and committed again.
"""

from __future__ import annotations

import base64
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

# An edit receives {path: current text or None if missing} and returns {path: new text}.
Edit = Callable[[dict[str, str | None]], dict[str, str]]


class ConflictError(Exception):
    """The branch moved while committing, and retries did not resolve it."""


class Repo(Protocol):
    def read(self, path: str) -> str | None: ...
    def list_dir(self, path: str) -> list[str]: ...
    def apply(self, paths: list[str], edit: Edit, message: str) -> None: ...


# ---------------------------------------------------------------- local checkout


@dataclass
class LocalRepo:
    """Files in a local checkout. `commit=True` also commits (and pushes if `push=True`)."""

    root: Path
    commit: bool = False
    push: bool = False

    def read(self, path: str) -> str | None:
        p = self.root / path
        return p.read_text(encoding="utf-8") if p.exists() else None

    def list_dir(self, path: str) -> list[str]:
        d = self.root / path
        return (
            sorted(f"{path.rstrip('/')}/{p.name}" for p in d.iterdir() if p.is_file()) if d.exists() else []
        )

    def apply(self, paths: list[str], edit: Edit, message: str) -> None:
        changed = edit({p: self.read(p) for p in paths})
        for path, text in changed.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        if self.commit and changed:
            subprocess.run(["git", "-C", str(self.root), "add", *changed], check=True)
            subprocess.run(["git", "-C", str(self.root), "commit", "-q", "-m", message], check=True)
            if self.push:
                subprocess.run(["git", "-C", str(self.root), "push", "-q"], check=True)


# ---------------------------------------------------------------- GitHub


@dataclass
class GitHubRepo:
    """Files on a GitHub branch, written with one commit per edit (Git Data API)."""

    owner: str
    name: str
    token: str
    branch: str = "main"
    client: httpx.Client | None = None
    retries: int = 3

    def __post_init__(self) -> None:
        self.client = self.client or httpx.Client(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
        )
        self._head_cache: str | None = None

    @property
    def _api(self) -> str:
        return f"/repos/{self.owner}/{self.name}"

    def _http(self) -> httpx.Client:
        assert self.client is not None
        return self.client

    def _head(self) -> str:
        r = self._http().get(f"{self._api}/git/ref/heads/{self.branch}")
        r.raise_for_status()
        sha: str = r.json()["object"]["sha"]
        return sha

    def _read_at(self, path: str, ref: str) -> str | None:
        r = self._http().get(f"{self._api}/contents/{path}", params={"ref": ref})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        if (
            data.get("encoding") == "base64"
            and data.get("content") is not None
            and data.get("size", 0) < 1_000_000
        ):
            return base64.b64decode(data["content"]).decode("utf-8")
        blob = self._http().get(f"{self._api}/git/blobs/{data['sha']}")  # files over 1 MB
        blob.raise_for_status()
        return base64.b64decode(blob.json()["content"]).decode("utf-8")

    def read(self, path: str) -> str | None:
        return self._read_at(path, self.branch)

    def list_dir(self, path: str) -> list[str]:
        r = self._http().get(f"{self._api}/contents/{path}", params={"ref": self.branch})
        if r.status_code == 404:
            return []
        r.raise_for_status()
        return sorted(item["path"] for item in r.json() if item["type"] == "file")

    def apply(self, paths: list[str], edit: Edit, message: str) -> None:
        for _attempt in range(self.retries):
            head = self._head()
            current = {p: self._read_at(p, head) for p in paths}
            changed = edit(current)
            if not changed:
                return
            if self._commit(head, changed, message):
                return
        raise ConflictError(f"{self.branch} kept changing; please reload and try again")

    def _commit(self, head: str, files: dict[str, str], message: str) -> bool:
        http = self._http()
        commit = http.get(f"{self._api}/git/commits/{head}")
        commit.raise_for_status()
        base_tree = commit.json()["tree"]["sha"]
        tree = http.post(
            f"{self._api}/git/trees",
            json={
                "base_tree": base_tree,
                "tree": [
                    {"path": p, "mode": "100644", "type": "blob", "content": t} for p, t in files.items()
                ],
            },
        )
        tree.raise_for_status()
        new = http.post(
            f"{self._api}/git/commits",
            json={"message": message, "tree": tree.json()["sha"], "parents": [head]},
        )
        new.raise_for_status()
        ref = http.patch(
            f"{self._api}/git/refs/heads/{self.branch}", json={"sha": new.json()["sha"], "force": False}
        )
        if ref.status_code == 422:  # not a fast-forward: someone pushed in between
            return False
        ref.raise_for_status()
        return True
