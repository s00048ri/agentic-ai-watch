"""Entry point of the web app (run with `streamlit run streamlit_app.py`)."""

from __future__ import annotations

from functools import partial

import streamlit as st

from . import data, views
from .auth import require_user
from .i18n import LANGUAGES, t


def main() -> None:
    st.set_page_config(page_title="Agentic AI Watch", page_icon="🔎", layout="wide")
    if "lang" not in st.session_state:
        st.session_state["lang"] = "en"
    lang = st.session_state["lang"]

    user = require_user(lang)

    with st.sidebar:
        st.radio(
            t("language", lang),
            list(LANGUAGES),
            format_func=LANGUAGES.__getitem__,
            key="lang",
            horizontal=True,
        )
        lang = st.session_state["lang"]
        st.caption(f"{t('signed_in_as', lang)}: {user.name}")
        if data.is_local():
            st.info(t("local_mode", lang))
        if st.button(t("reload", lang)):
            data.clear()
            st.rerun()
        if st.user.get("is_logged_in"):
            st.button(t("sign_out", lang), on_click=st.logout)
        st.caption(t("app_caption", lang))

    pages = {
        "review": st.Page(
            partial(views.review_page, user), title=t("page_review", lang), icon="📝", default=True
        ),
        "orgs": st.Page(
            partial(views.organisations_page, user),
            title=t("page_orgs", lang),
            icon="🏢",
            url_path="organisations",
        ),
        "candidates": st.Page(
            partial(views.candidates_page, user),
            title=t("page_candidates", lang),
            icon="🧭",
            url_path="candidates",
        ),
        "categories": st.Page(
            partial(views.categories_page, user),
            title=t("page_categories", lang),
            icon="🗂️",
            url_path="categories",
        ),
        "status": st.Page(
            partial(views.status_page, user), title=t("page_status", lang), icon="📊", url_path="status"
        ),
        "search": st.Page(
            partial(views.search_page, user), title=t("page_search", lang), icon="🔍", url_path="search"
        ),
    }
    st.session_state["pages"] = pages
    st.navigation(list(pages.values())).run()
