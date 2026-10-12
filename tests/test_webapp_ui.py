"""Smoke tests of every page with Streamlit's AppTest, against a local copy of a project."""

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from agentic_watch.pipeline import start_run  # noqa: E402

from .conftest import REPO, FakeLLM, make_ctx  # noqa: E402

SCRIPT = str(REPO / "streamlit_app.py")


@pytest.fixture
def app_root(project_root, monkeypatch):
    ctx, _, _ = make_ctx(project_root, llm=FakeLLM())
    start_run(ctx)
    monkeypatch.setenv("AGENTIC_WATCH_DEV", "1")
    monkeypatch.setenv("AGENTIC_WATCH_ROOT", str(project_root))
    return project_root


def run_page(path: str | None, lang: str = "en") -> AppTest:
    at = AppTest.from_file(SCRIPT, default_timeout=30)
    at.session_state["lang"] = lang
    if path:
        at.switch_page(path)
    at.run()
    return at


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_review_page_renders(app_root, lang):
    at = run_page(None, lang)
    assert not at.exception
    assert at.header[0].value == ("Review records" if lang == "en" else "記録の確認")
    assert any("Example Org" in m.value for m in at.markdown)


def test_review_save_updates_the_record(app_root):
    at = run_page(None)
    decision = next(r for r in at.radio if r.label == "Decision")
    decision.set_value("blog_candidate")
    note = next(i for i in at.text_input if i.label == "Note (optional)")
    note.set_value("useful")
    at.button(key="FormSubmitter:review-" + _entry_id(app_root) + "-Save").click().run()
    text = (app_root / "data/entries/2026-10.jsonl").read_text()
    assert '"status":"blog_candidate"' in text and '"review_note":"useful"' in text


def _entry_id(root) -> str:
    import json

    return json.loads((root / "data/entries/2026-10.jsonl").read_text().splitlines()[0])["id"]


def test_auth_is_required_without_dev_mode(project_root, monkeypatch):
    monkeypatch.delenv("AGENTIC_WATCH_DEV", raising=False)
    monkeypatch.setenv("AGENTIC_WATCH_ROOT", str(project_root))
    at = AppTest.from_file(SCRIPT, default_timeout=30).run()
    assert at.error and "Sign-in is not configured" in at.error[0].value
