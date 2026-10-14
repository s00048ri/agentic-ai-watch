import base64
import datetime as dt
import json
import re
from pathlib import Path

import httpx
import pytest
import yaml

from agentic_watch import ledger_ops
from agentic_watch.config import load_project
from agentic_watch.issues import (
    IssueError,
    apply_lead_issue,
    apply_organisation_issue,
    lead_fields,
    org_fields,
    parse_issue_body,
    render_templates,
)
from agentic_watch.repo import ConflictError, GitHubRepo, LocalRepo
from agentic_watch.webapp.i18n import TEXT

from .conftest import ORG, FakeLLM, make_ctx

TODAY = dt.date(2026, 10, 20)


# ---------------------------------------------------------------- ledger operations
def test_set_entry_status_changes_only_that_line(project_root):
    from agentic_watch.pipeline import start_run

    ctx, _, _ = make_ctx(project_root, llm=FakeLLM())
    start_run(ctx)
    path = project_root / "data/entries/2026-10.jsonl"
    original = path.read_text()
    other = '{"not": "an entry", "id": "other"}'
    text = original + other + "\n"
    [entry] = ctx.store.entries()
    new = ledger_ops.set_entry_status(text, entry.id, "kept", "Ren", " good ", TODAY)
    lines = new.splitlines()
    assert lines[-1] == other  # untouched
    updated = json.loads(lines[0])
    assert updated["status"] == "kept" and updated["reviewer"] == "Ren"
    assert updated["reviewed"] == "2026-10-20" and updated["review_note"] == "good"
    with pytest.raises(ledger_ops.LedgerError):
        ledger_ops.set_entry_status(text, "missing", "kept", "Ren", None, TODAY)


def test_add_and_update_organisation_validates(project_root):
    tax = load_project(project_root).taxonomy
    text = yaml.safe_dump([ORG])
    new_org = {**ORG, "id": "second-org", "name": "Second Org", "domains": ["second.example"]}
    out = ledger_ops.add_organisation(text, new_org, tax)
    assert [o["id"] for o in yaml.safe_load(out)] == ["example-org", "second-org"]
    with pytest.raises(ledger_ops.LedgerError, match="already exists"):
        ledger_ops.add_organisation(out, new_org, tax)
    with pytest.raises(ledger_ops.LedgerError):
        ledger_ops.add_organisation(text, {**new_org, "id": "third", "sector": "not-a-sector"}, tax)
    upd = ledger_ops.update_organisation(out, "second-org", {"status": "dormant"}, tax, TODAY)
    row = yaml.safe_load(upd)[1]
    assert row["status"] == "dormant" and str(row["last_reviewed"]) == "2026-10-20"


def test_candidates_add_and_decide():
    text = ledger_ops.add_candidate(
        None, {"name": "Lead Co. Ltd", "lead_route": "people", "lead_detail": "a"}, TODAY
    )
    text = ledger_ops.add_candidate(
        text, {"name": "lead co", "lead_route": "people", "lead_detail": "b"}, TODAY
    )
    [c] = yaml.safe_load(text)
    assert c["mentions"] == 2 and c["lead_detail"] == "a; b"
    decided = yaml.safe_load(ledger_ops.decide_candidate(text, "LEAD CO.", "reject", TODAY))[0]
    assert decided["decision"] == "reject" and decided["decided"] == "2026-10-20"


def test_slugify():
    assert ledger_ops.slugify("Société Générale & Co.") == "societe-generale-co"
    assert ledger_ops.slugify("株式会社") == "organisation"


# ---------------------------------------------------------------- repositories
def test_local_repo_apply(tmp_path: Path):
    repo = LocalRepo(tmp_path)
    repo.apply(["a/b.txt"], lambda files: {"a/b.txt": (files["a/b.txt"] or "") + "x"}, "msg")
    repo.apply(["a/b.txt"], lambda files: {"a/b.txt": (files["a/b.txt"] or "") + "y"}, "msg")
    assert repo.read("a/b.txt") == "xy" and repo.list_dir("a") == ["a/b.txt"]


class FakeGitHub:
    """Minimal Git Data API. `race` makes the first ref update fail as if someone pushed."""

    def __init__(self, files: dict[str, str], race: int = 0) -> None:
        self.files = dict(files)
        self.head = "c0"
        self.race = race
        self.commits: list[dict] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path.removeprefix("/repos/o/r")
        if req.method == "GET" and path == "/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": self.head}})
        if req.method == "GET" and path.startswith("/contents/"):
            name = path.removeprefix("/contents/")
            if name not in self.files:
                return httpx.Response(404, json={})
            content = base64.b64encode(self.files[name].encode()).decode()
            return httpx.Response(
                200, json={"encoding": "base64", "content": content, "size": 10, "sha": "b"}
            )
        if req.method == "GET" and path.startswith("/git/commits/"):
            return httpx.Response(200, json={"tree": {"sha": "t0"}})
        if req.method == "POST" and path == "/git/trees":
            self.pending = json.loads(req.content)["tree"]
            return httpx.Response(201, json={"sha": "t1"})
        if req.method == "POST" and path == "/git/commits":
            return httpx.Response(201, json={"sha": f"c{len(self.commits) + 1}"})
        if req.method == "PATCH" and path == "/git/refs/heads/main":
            if self.race:
                self.race -= 1
                self.head = "c-other"  # someone else pushed
                self.files["data.txt"] = self.files.get("data.txt", "") + "bot\n"
                return httpx.Response(422, json={"message": "Update is not a fast forward"})
            for item in self.pending:
                self.files[item["path"]] = item["content"]
            self.commits.append(json.loads(req.content))
            self.head = json.loads(req.content)["sha"]
            return httpx.Response(200, json={})
        return httpx.Response(500, json={"unexpected": f"{req.method} {path}"})


def gh_repo(fake: FakeGitHub) -> GitHubRepo:
    client = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(fake.handler))
    return GitHubRepo(owner="o", name="r", token="t", client=client)


def test_github_repo_reapplies_edit_after_conflict():
    fake = FakeGitHub({"data.txt": "start\n"}, race=1)
    repo = gh_repo(fake)
    repo.apply(["data.txt"], lambda f: {"data.txt": (f["data.txt"] or "") + "review\n"}, "chore: review")
    assert fake.files["data.txt"] == "start\nbot\nreview\n"  # the bot's line is kept
    assert len(fake.commits) == 1


def test_github_repo_gives_up_after_retries():
    fake = FakeGitHub({"data.txt": ""}, race=5)
    with pytest.raises(ConflictError):
        gh_repo(fake).apply(["data.txt"], lambda f: {"data.txt": "x"}, "m")


def test_github_repo_read_missing_returns_none():
    assert gh_repo(FakeGitHub({})).read("nope.txt") is None


# ---------------------------------------------------------------- issue forms
def _body(fields, values: dict[str, str]) -> str:
    return "\n\n".join(f"### {f.label}\n\n{values.get(f.id, '_No response_')}" for f in fields)


def test_issue_templates_match_vocabularies(project_root):
    project = load_project(project_root)
    forms = render_templates(project)
    org_form = yaml.safe_load(forms[".github/ISSUE_TEMPLATE/add-organisation.yml"].split("\n", 1)[1])
    sector = next(b for b in org_form["body"] if b.get("id") == "sector")
    assert sector["attributes"]["options"] == [t.id for t in project.taxonomy.vocabularies["sectors"]]


def test_committed_issue_templates_are_current():
    repo_root = Path(__file__).resolve().parents[1]
    project = load_project(repo_root)
    for rel, text in render_templates(project).items():
        assert (repo_root / rel).read_text() == text, f"{rel} is stale: run agentic-watch issue-templates"


def test_organisation_issue_is_applied(project_root):
    project = load_project(project_root)
    values = {
        "name": "New Org",
        "home_url": "https://new.example/",
        "domains": "new.example",
        "pages": "- https://new.example/ai-policy\nnot a url",
        "org_type": "public-sector",
        "ecosystem_role": "deployer",
        "sector": "public-administration",
        "region": "europe",
        "size": "medium",
        "inclusion_reason": "public-body",
    }
    org_id = apply_organisation_issue(project, _body(org_fields(project), values), 12, TODAY, fetcher=None)
    assert org_id == "new-org"
    row = yaml.safe_load((project_root / "ledger/organisations.yaml").read_text())[-1]
    assert row["lead_source"] == "issue #12"
    assert row["watch_points"] == [
        {
            "type": "newsroom",
            "url": "https://new.example/ai-policy",
            "method": "manual",
            "feed_url": None,
            "language": "en",
            "frequency": "weekly",
            "verified": False,
        }
    ]


def test_issue_missing_fields_and_bad_values(project_root):
    project = load_project(project_root)
    with pytest.raises(IssueError, match="Missing"):
        apply_lead_issue(project, _body(lead_fields(), {"name": "X"}), 3, TODAY)
    bad = {
        "name": "Bad",
        "home_url": "https://b.example",
        "domains": "b.example",
        "org_type": "private-sector",
        "ecosystem_role": "deployer",
        "sector": "invented",
        "region": "europe",
        "size": "small",
        "inclusion_reason": "public-body",
    }
    with pytest.raises(ValueError):
        apply_organisation_issue(project, _body(org_fields(project), bad), 4, TODAY, fetcher=None)


def test_lead_issue_is_applied(project_root):
    project = load_project(project_root)
    body = _body(
        lead_fields(), {"name": "Lead Org", "lead_route": "third-party", "lead_detail": "Seen in a report"}
    )
    assert apply_lead_issue(project, body, 7, TODAY) == "Lead Org"
    [c] = yaml.safe_load((project_root / "ledger/candidates.yaml").read_text())
    assert c["lead_detail"] == "Seen in a report (issue #7)" and c["lead_route"] == "third-party"


def test_parse_issue_body_handles_no_response():
    fields = lead_fields()
    parsed = parse_issue_body(_body(fields, {"name": "A"}), fields)
    assert parsed["name"] == "A" and parsed["official_url"] is None


# ---------------------------------------------------------------- translations
def test_every_text_key_used_in_the_app_exists_in_both_languages():
    src = Path(__file__).resolve().parents[1] / "src/agentic_watch/webapp"
    used = set()
    for f in src.glob("*.py"):
        used |= set(re.findall(r'\bt\(\s*["\']([a-z0-9_]+)["\']', f.read_text()))
        used |= {f"status_{s}" for s in ("new", "needs_review", "kept", "blog_candidate", "discarded")}
    missing = sorted(k for k in used if k not in TEXT)
    assert not missing, missing
    no_ja = sorted(k for k, v in TEXT.items() if not v.get("ja") or not v.get("en"))
    assert not no_ja, no_ja


def test_review_sets_finding_categories_and_lists_candidates(project_root):
    from agentic_watch.models import NEW_CATEGORY
    from agentic_watch.pipeline import start_run
    from agentic_watch.webapp.views import category_candidates, category_usage

    ctx, _, _ = make_ctx(project_root, llm=FakeLLM())
    start_run(ctx)
    path = project_root / "data/entries/2026-10.jsonl"
    [entry] = ctx.store.entries()
    assert len(category_candidates([entry], {})) == 2  # two findings have no existing category
    chosen = {
        "challenges": ["human-oversight-over-reliance", "human-oversight-over-reliance", NEW_CATEGORY],
        "responses": ["calibrated-human-oversight"],
    }
    new = ledger_ops.set_entry_status(path.read_text(), entry.id, "kept", "Ren", None, TODAY, chosen)
    [updated] = ledger_ops.parse_entries(new)
    assert [f.category for f in updated.challenges] == chosen["challenges"]
    [candidate] = category_candidates([updated], {})
    assert candidate["reviewed"] == "yes"
    assert category_usage([updated])["challenges"] == {"human-oversight-over-reliance": 2}
    with pytest.raises(ledger_ops.LedgerError):
        ledger_ops.set_entry_status(new, entry.id, "kept", "Ren", None, TODAY, {"challenges": ["x"]})
