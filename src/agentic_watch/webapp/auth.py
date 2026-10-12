"""Sign-in with an allow-list of email addresses. Fails closed when sign-in is not configured."""

from __future__ import annotations

import os
from dataclasses import dataclass

import streamlit as st

from .data import _secrets
from .i18n import t


@dataclass
class User:
    email: str
    name: str


def allowed_emails() -> set[str]:
    app = _secrets().get("app") or {}
    return {e.strip().lower() for e in app.get("allowed_emails", []) if e.strip()}


def require_user(lang: str) -> User:
    """Return the signed-in user, or stop the script after showing the sign-in screen."""
    secrets = _secrets()
    if "auth" in secrets:
        if not st.user.is_logged_in:
            st.title(t("app_title", lang))
            st.write(t("sign_in_prompt", lang))
            st.button(t("sign_in", lang), on_click=st.login, type="primary")
            st.stop()
        email = str(st.user.get("email") or "").lower()
        allowed = allowed_emails()
        if not allowed:
            st.error(t("allowlist_missing", lang))
            st.stop()
        if email not in allowed:
            st.error(t("not_allowed", lang))
            st.button(t("sign_out", lang), on_click=st.logout)
            st.stop()
        return User(email=email, name=str(st.user.get("name") or email))
    if os.environ.get("AGENTIC_WATCH_DEV") == "1":
        return User(email="dev@localhost", name="Local developer")
    st.error(t("auth_missing", lang))
    st.stop()
    raise AssertionError  # unreachable; st.stop() raises
