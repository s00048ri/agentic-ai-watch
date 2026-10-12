"""The app's pages. Each function renders one page."""

from __future__ import annotations

import datetime as dt
import io
from collections.abc import Sequence
from typing import Any, get_args

import pandas as pd
import streamlit as st

from .. import ledger_ops
from ..config import (
    EcosystemRole,
    Frequency,
    InclusionReason,
    LeadRoute,
    Method,
    OrgType,
    Size,
    WatchPointType,
)
from ..digest import cost_usd
from ..discover import discover
from ..fetch import Fetcher
from ..models import Entry, EntryStatus, RelevanceLabel
from ..repo import Edit
from . import data
from .auth import User
from .i18n import t

STATUSES: tuple[EntryStatus, ...] = ("new", "needs_review", "kept", "blog_candidate", "discarded")
DECISIONS: tuple[EntryStatus, ...] = ("kept", "blog_candidate", "discarded")
PAGE_SIZE = 25


def _lang() -> str:
    return str(st.session_state.get("lang", "en"))


def _status_label(status: str) -> str:
    return t(f"status_{status}", _lang())


def _org_names() -> dict[str, str]:
    return {o.id: o.name for o in data.project().organisations}


def _tax_names() -> tuple[dict[str, str], dict[str, str]]:
    tax = data.project().taxonomy
    return {c.id: c.name for c in tax.challenges}, {r.id: r.name for r in tax.responses}


# ---------------------------------------------------------------- review


def review_page(user: User) -> None:
    lang = _lang()
    st.header(t("page_review", lang))
    st.caption(t("review_intro", lang))
    entries = data.entries()
    orgs = _org_names()

    c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
    statuses = c1.multiselect(
        t("filter_status", lang), STATUSES, default=["new", "needs_review"], format_func=_status_label
    )
    org = c2.selectbox(
        t("filter_org", lang),
        ["", *sorted({e.org_id for e in entries})],
        format_func=lambda o: orgs.get(o, o) if o else t("all", lang),
    )
    labels = c3.multiselect(t("filter_label", lang), get_args(RelevanceLabel), placeholder=t("all", lang))
    text = c4.text_input(t("filter_text", lang))

    shown = filter_entries(entries, statuses, org, labels, text)
    if not shown:
        st.info(t("no_records", lang))
        return
    pages = (len(shown) - 1) // PAGE_SIZE + 1
    page = st.number_input(t("page", lang), 1, pages, 1) if pages > 1 else 1
    chunk = shown[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    st.caption(t("showing_n", lang, shown=len(chunk), total=len(shown)))
    for entry in chunk:
        _entry_card(entry, user, orgs)


def filter_entries(
    entries: list[Entry], statuses: Sequence[str], org: str, labels: Sequence[str], text: str
) -> list[Entry]:
    q = text.strip().casefold()
    out = []
    for e in entries:
        if statuses and e.status not in statuses:
            continue
        if org and e.org_id != org:
            continue
        if labels and e.relevance.label not in labels:
            continue
        if q and q not in " ".join([e.title, e.summary, e.evidence, e.url]).casefold():
            continue
        out.append(e)
    return out


def _entry_card(entry: Entry, user: User, orgs: dict[str, str]) -> None:
    lang = _lang()
    challenges, responses = _tax_names()
    with st.container(border=True):
        head, badge = st.columns([5, 1])
        head.markdown(
            f"**{orgs.get(entry.org_id, entry.org_id)}** — [{entry.title or entry.url}]({entry.url})"
        )
        badge.markdown(f"`{_status_label(entry.status)}`")
        meta = [f"{t('detected', lang)}: {entry.detected}"]
        if entry.published:
            meta.append(f"{t('published', lang)}: {entry.published}")
        meta.append(f"{entry.relevance.label} ({entry.relevance.confidence:.2f})")
        st.caption(" · ".join(meta))
        st.write(entry.summary)
        mark = "✅ " + t("evidence_ok", lang) if entry.evidence_verified else "⚠️ " + t("evidence_ng", lang)
        st.markdown(f"**{t('evidence', lang)}** — {mark}")
        st.markdown(f"> {entry.evidence}")
        details = []
        if entry.challenges:
            details.append(
                f"**{t('challenges', lang)}:** " + ", ".join(challenges.get(c, c) for c in entry.challenges)
            )
        if entry.responses:
            details.append(
                f"**{t('responses', lang)}:** " + ", ".join(responses.get(r, r) for r in entry.responses)
            )
        fields = {k: v for k, v in entry.fields.model_dump().items() if v}
        if fields:
            details.append(f"**{t('fields', lang)}:** " + "; ".join(f"{k}: {v}" for k, v in fields.items()))
        if entry.other_organisations_named:
            details.append(f"**{t('partners', lang)}:** " + ", ".join(entry.other_organisations_named))
        for line in details:
            st.markdown(line)
        if entry.reviewer and entry.reviewed:
            st.caption(t("reviewed_by", lang, who=entry.reviewer, date=entry.reviewed))
        with st.form(f"review-{entry.id}", border=False):
            cols = st.columns([3, 3, 1])
            current = entry.status if entry.status in DECISIONS else "kept"
            decision = cols[0].radio(
                t("decision", lang),
                DECISIONS,
                index=DECISIONS.index(current),
                format_func=_status_label,
                horizontal=True,
            )
            note = cols[1].text_input(t("note", lang), value=entry.review_note or "")
            if cols[2].form_submit_button(t("save", lang), type="primary"):
                path = data.entry_path(entry)
                today = dt.date.today()
                ok = data.write(
                    [path],
                    lambda files: {
                        path: ledger_ops.set_entry_status(
                            files[path], entry.id, decision, user.name, note, today
                        )
                    },
                    f"chore: review record {entry.id[:8]}",
                    lang,
                )
                if ok:
                    st.rerun()


# ---------------------------------------------------------------- organisations


def organisations_page(user: User) -> None:
    lang = _lang()
    st.header(t("page_orgs", lang))
    prefill = st.session_state.get("prefill")
    tab_list, tab_add = st.tabs([t("orgs_tab_list", lang), t("orgs_tab_add", lang)])
    with tab_list:
        _org_list(user)
    with tab_add:
        if prefill:
            st.info(t("from_candidate", lang, name=prefill["name"]))
        _org_add(user, prefill)


def _org_list(user: User) -> None:
    lang = _lang()
    project = data.project()
    if not project.organisations:
        st.info(t("orgs_empty", lang))
        return
    checks = data.checks()
    counts: dict[str, int] = {}
    for e in data.entries():
        counts[e.org_id] = counts.get(e.org_id, 0) + 1
    rows = []
    for o in project.organisations:
        last = [checks.get(w.feed_url or w.url) for w in o.watch_points]
        dates = [d for d in last if d]
        rows.append(
            {
                "id": o.id,
                t("col_name", lang): o.name,
                t("col_type", lang): o.org_type,
                t("col_sector", lang): o.sector,
                t("col_region", lang): o.region,
                t("col_status", lang): o.status,
                t("col_watch_points", lang): len(o.watch_points),
                t("col_records", lang): counts.get(o.id, 0),
                t("col_last_check", lang): max(dates).isoformat() if dates else "—",
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    st.subheader(t("edit_org", lang))
    org_id = st.selectbox(
        t("filter_org", lang),
        [o.id for o in project.organisations],
        format_func=lambda i: _org_names().get(i, i),
    )
    org = project.organisation(org_id)
    with st.form(f"edit-{org_id}"):
        status = st.selectbox(
            t("col_status", lang),
            ["active", "dormant", "removed"],
            index=["active", "dormant", "removed"].index(org.status),
        )
        st.caption(t("watch_points_help", lang))
        wp_df = _watch_points_editor([w.model_dump() for w in org.watch_points], key=f"wp-{org_id}")
        if st.form_submit_button(t("save", lang), type="primary"):
            changes: dict[str, Any] = {"status": status, "watch_points": _clean_watch_points(wp_df)}
            tax = project.taxonomy
            today = dt.date.today()
            if data.write(
                ["ledger/organisations.yaml"],
                lambda files: {
                    "ledger/organisations.yaml": ledger_ops.update_organisation(
                        files["ledger/organisations.yaml"], org_id, changes, tax, today
                    )
                },
                f"chore: update organisation {org_id}",
                lang,
            ):
                st.rerun()


WP_COLUMNS = ["type", "url", "method", "feed_url", "language", "frequency", "verified"]


def _watch_points_editor(rows: list[dict[str, Any]], key: str) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=WP_COLUMNS) if rows else pd.DataFrame(columns=WP_COLUMNS)
    df["verified"] = df["verified"].fillna(False).astype(bool)
    return st.data_editor(
        df,
        key=key,
        num_rows="dynamic",
        hide_index=True,
        use_container_width=True,
        column_config={
            "type": st.column_config.SelectboxColumn(options=list(get_args(WatchPointType)), required=True),
            "url": st.column_config.TextColumn(required=True),
            "method": st.column_config.SelectboxColumn(options=list(get_args(Method)), required=True),
            "feed_url": st.column_config.TextColumn(),
            "language": st.column_config.TextColumn(default="en"),
            "frequency": st.column_config.SelectboxColumn(
                options=list(get_args(Frequency)), default="weekly"
            ),
            "verified": st.column_config.CheckboxColumn(default=False),
        },
    )


def _clean_watch_points(df: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for row in df.to_dict("records"):
        if not row.get("url"):
            continue
        clean = {k: (None if pd.isna(v) or v == "" else v) for k, v in row.items() if k in WP_COLUMNS}
        clean["language"] = clean.get("language") or "en"
        clean["frequency"] = clean.get("frequency") or "weekly"
        clean["verified"] = bool(clean.get("verified"))
        out.append(clean)
    return out


def _org_add(user: User, prefill: dict[str, str] | None) -> None:
    lang = _lang()
    project = data.project()
    tax = project.taxonomy
    sectors = [s.id for s in tax.vocabularies["sectors"]]
    regions = [r.id for r in tax.vocabularies["regions"]]

    st.subheader(t("discover_title", lang))
    disabled = project.settings.crawl.contact_missing()
    if disabled:
        st.warning(t("discover_disabled", lang))
    home = st.text_input(t("discover_url", lang), key="discover_home")
    if st.button(t("discover_button", lang), disabled=disabled or not home):
        fetcher = Fetcher(project.settings.crawl)
        found = [s for s in discover(fetcher, home) if "error" not in s]
        st.session_state["discovered"] = [{k: s.get(k) for k in WP_COLUMNS} for s in found]
        if not found:
            st.info(t("discover_none", lang))

    with st.form("add-org"):
        name = st.text_input(t("org_name", lang), value=(prefill or {}).get("name", ""))
        name_local = st.text_input(t("org_name_local", lang))
        org_id = st.text_input(
            t("org_id", lang), value=ledger_ops.slugify(name) if name else "", help=t("org_id_help", lang)
        )
        domains = st.text_input(t("org_domains", lang))
        c1, c2, c3 = st.columns(3)
        org_type = c1.selectbox(t("org_type", lang), get_args(OrgType))
        role = c2.selectbox(t("ecosystem_role", lang), get_args(EcosystemRole))
        size = c3.selectbox(t("size", lang), get_args(Size), index=list(get_args(Size)).index("unknown"))
        c4, c5, c6 = st.columns(3)
        sector = c4.selectbox(t("col_sector", lang), sectors)
        region = c5.selectbox(t("col_region", lang), regions)
        reasons = list(get_args(InclusionReason))
        reason = c6.selectbox(
            t("inclusion_reason", lang),
            reasons,
            index=reasons.index("added-from-candidates") if prefill else 0,
        )
        lead_source = st.text_input(t("lead_source", lang), value=(prefill or {}).get("lead_source", ""))
        st.caption(t("watch_points_help", lang))
        wp_df = _watch_points_editor(st.session_state.get("discovered", []), key="wp-new")
        submitted = st.form_submit_button(t("add_org_button", lang), type="primary")

    if not submitted:
        return
    org_id = org_id.strip() or ledger_ops.slugify(name)
    missing = [
        label
        for label, v in ((t("org_name", lang), name), (t("org_domains", lang), domains))
        if not v.strip()
    ]
    if missing:
        st.error(t("required", lang, fields=", ".join(missing)))
        return
    today = dt.date.today()
    org = {
        "id": org_id.strip(),
        "name": name.strip(),
        "name_local": name_local.strip() or None,
        "domains": [d.strip() for d in domains.split(",") if d.strip()],
        "org_type": org_type,
        "ecosystem_role": role,
        "sector": sector,
        "region": region,
        "size": size,
        "inclusion_reason": reason,
        "lead_source": lead_source.strip() or None,
        "status": "active",
        "added": today.isoformat(),
        "last_reviewed": today.isoformat(),
        "watch_points": _clean_watch_points(wp_df),
    }
    paths = ["ledger/organisations.yaml"]
    if prefill:
        paths.append("ledger/candidates.yaml")

    def edit(files: dict[str, str | None]) -> dict[str, str]:
        out = {
            "ledger/organisations.yaml": ledger_ops.add_organisation(
                files["ledger/organisations.yaml"], org, tax
            )
        }
        if prefill:
            out["ledger/candidates.yaml"] = ledger_ops.decide_candidate(
                files["ledger/candidates.yaml"], prefill["name"], "add", today
            )
        return out

    if data.write(paths, edit, f"chore: add organisation {org['id']}", lang):
        st.session_state.pop("prefill", None)
        st.session_state.pop("discovered", None)
        st.success(t("added_org", lang, name=org["name"]))


# ---------------------------------------------------------------- candidates


def candidates_page(user: User) -> None:
    lang = _lang()
    st.header(t("page_candidates", lang))
    st.caption(t("cand_intro", lang))
    show_all = st.toggle(t("cand_show_all", lang))
    cands = sorted(data.candidates(), key=lambda c: (-c.mentions, c.name.casefold()))
    if not show_all:
        cands = [c for c in cands if c.decision == "pending"]
    if not cands:
        st.info(t("cand_none", lang))
    today = dt.date.today()
    for c in cands:
        with st.container(border=True):
            st.markdown(f"**{c.name}** · {t('mentions', lang)}: {c.mentions} · {c.decision}")
            st.caption(f"{t('lead_route', lang)}: {c.lead_route} · {t('first_seen', lang)}: {c.first_seen}")
            if c.lead_detail:
                st.write(c.lead_detail)
            if c.decision != "pending":
                continue
            b1, b2, _ = st.columns([2, 1, 4])
            if b1.button(t("cand_add", lang), key=f"add-{c.name}"):
                st.session_state["prefill"] = {"name": c.name, "lead_source": c.lead_detail or c.lead_route}
                st.switch_page(st.session_state["pages"]["orgs"])
            if b2.button(t("cand_reject", lang), key=f"rej-{c.name}") and data.write(
                ["ledger/candidates.yaml"],
                _reject_edit(c.name, today),
                "chore: update candidates",
                lang,
            ):
                st.rerun()

    st.subheader(t("cand_new_title", lang))
    with st.form("new-lead", clear_on_submit=True):
        name = st.text_input(t("col_name", lang))
        routes = [r for r in get_args(LeadRoute) if r != "official-mention"]
        route = st.selectbox(t("lead_route", lang), routes)
        detail = st.text_input(t("lead_detail", lang))
        if st.form_submit_button(t("cand_new_button", lang)) and name.strip():
            cand = {"name": name.strip(), "lead_route": route, "lead_detail": detail.strip() or None}
            if data.write(
                ["ledger/candidates.yaml"],
                lambda files: {
                    "ledger/candidates.yaml": ledger_ops.add_candidate(
                        files["ledger/candidates.yaml"], cand, today
                    )
                },
                "chore: add candidate lead",
                lang,
            ):
                st.rerun()


def _reject_edit(name: str, today: dt.date) -> Edit:
    def edit(files: dict[str, str | None]) -> dict[str, str]:
        path = "ledger/candidates.yaml"
        return {path: ledger_ops.decide_candidate(files[path], name, "reject", today)}

    return edit


# ---------------------------------------------------------------- run status


def status_page(user: User) -> None:
    lang = _lang()
    st.header(t("page_status", lang))
    project = data.project()
    usage = data.usage()
    month = dt.date.today().strftime("%Y-%m")
    month_usage = [u for u in usage if u.ts.strftime("%Y-%m") == month]
    tokens = sum(u.total() for u in month_usage)
    budget = project.settings.limits.monthly_token_budget
    pending = [b for b in data.batches().values() if not b.collected]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric(t("month_tokens", lang), f"{tokens:,}")
    m1.caption(t("of_budget", lang, pct=round(100 * tokens / budget) if budget else 0))
    m2.metric(t("est_cost", lang), f"${cost_usd(month_usage, project.settings.pricing):.2f}")
    m3.metric(t("pending_batches", lang), len(pending))
    m4.metric(t("queue", lang), len(data.queue()))

    st.subheader(t("current_run", lang))
    run = data.run_state()
    if run is None:
        st.info(t("run_none", lang))
    else:
        c = run.counts
        st.write(
            f"**{run.run_id}** — "
            + t(
                "counts_flow",
                lang,
                d=c.detected,
                k=c.keyword_passed,
                s1=c.stage1_sent,
                p=c.stage1_passed,
                r=c.recorded,
            )
        )
        if run.budget_stopped:
            st.warning(t("budget_stopped", lang))
        with st.expander(f"{t('errors', lang)} ({len(run.fetch_errors)})"):
            st.write("\n".join(f"- {e}" for e in run.fetch_errors) or t("none", lang))
        with st.expander(f"{t('manual_points', lang)} ({len(run.manual_watch_points)})"):
            st.write("\n".join(f"- {m}" for m in run.manual_watch_points) or t("none", lang))

    if usage:
        st.subheader(t("tokens_by_month", lang))
        df = pd.DataFrame(
            [{"month": u.ts.strftime("%Y-%m"), "stage": u.stage, "tokens": u.total()} for u in usage]
        )
        st.bar_chart(df.pivot_table(index="month", columns="stage", values="tokens", aggfunc="sum").fillna(0))

    st.subheader(t("weekly_reports", lang))
    reports = data.weekly_reports()
    if not reports:
        st.info(t("none", lang))
    else:
        chosen = st.selectbox(
            t("weekly_reports", lang),
            reports,
            format_func=lambda p: p.rsplit("/", 1)[-1],
            label_visibility="collapsed",
        )
        st.markdown(data.read(chosen) or "")


# ---------------------------------------------------------------- search and export


def search_page(user: User) -> None:
    lang = _lang()
    st.header(t("page_search", lang))
    st.caption(t("search_intro", lang))
    entries = data.entries()
    orgs = _org_names()
    challenges, _responses = _tax_names()
    c1, c2, c3 = st.columns(3)
    text = c1.text_input(t("filter_text", lang), key="search_text")
    statuses = c2.multiselect(
        t("filter_status", lang),
        STATUSES,
        format_func=_status_label,
        key="search_status",
        placeholder=t("all", lang),
    )
    labels = c3.multiselect(
        t("filter_label", lang), get_args(RelevanceLabel), key="search_label", placeholder=t("all", lang)
    )
    c4, c5, c6 = st.columns(3)
    org = c4.selectbox(
        t("filter_org", lang),
        ["", *sorted({e.org_id for e in entries})],
        format_func=lambda o: orgs.get(o, o) if o else t("all", lang),
        key="search_org",
    )
    start = c5.date_input(t("date_from", lang), value=None, key="search_from")
    end = c6.date_input(t("date_to", lang), value=None, key="search_to")
    rows = filter_entries(entries, statuses, org, labels, text)
    if start:
        rows = [e for e in rows if e.detected >= start]
    if end:
        rows = [e for e in rows if e.detected <= end]
    df = entries_frame(rows, orgs, challenges)
    st.caption(t("showing_n", lang, shown=len(df), total=len(entries)))
    st.dataframe(
        df, hide_index=True, use_container_width=True, column_config={"url": st.column_config.LinkColumn()}
    )
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    st.download_button(
        t("download_csv", lang), buf.getvalue().encode("utf-8-sig"), "agentic-watch-records.csv", "text/csv"
    )


def entries_frame(entries: list[Entry], orgs: dict[str, str], challenges: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "detected": e.detected.isoformat(),
                "published": e.published.isoformat() if e.published else "",
                "organisation": orgs.get(e.org_id, e.org_id),
                "title": e.title,
                "url": e.url,
                "label": e.relevance.label,
                "confidence": round(e.relevance.confidence, 2),
                "summary": e.summary,
                "evidence": e.evidence,
                "evidence_verified": e.evidence_verified,
                "challenges": "; ".join(challenges.get(c, c) for c in e.challenges),
                "responses": "; ".join(e.responses),
                "status": e.status,
                "reviewer": e.reviewer or "",
                "review_note": e.review_note or "",
            }
            for e in entries
        ],
        columns=[
            "detected",
            "published",
            "organisation",
            "title",
            "url",
            "label",
            "confidence",
            "summary",
            "evidence",
            "evidence_verified",
            "challenges",
            "responses",
            "status",
            "reviewer",
            "review_note",
        ],
    )
