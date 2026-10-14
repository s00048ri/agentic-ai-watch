"""Interface text. English is the default; Japanese is an option in the sidebar."""

from __future__ import annotations

LANGUAGES = {"en": "English", "ja": "日本語"}

TEXT: dict[str, dict[str, str]] = {
    # ---- app shell
    "app_title": {"en": "Agentic AI Watch", "ja": "Agentic AI Watch"},
    "app_caption": {
        "en": "Internal use only. Records come from organisations' own websites.",
        "ja": "内部利用のみ。記録は各組織自身のウェブサイトの発信に基づきます。",
    },
    "language": {"en": "Language", "ja": "言語"},
    "signed_in_as": {"en": "Signed in as", "ja": "ログイン中"},
    "sign_in": {"en": "Sign in", "ja": "ログイン"},
    "sign_out": {"en": "Sign out", "ja": "ログアウト"},
    "sign_in_prompt": {
        "en": "Sign in with your work account to continue.",
        "ja": "続けるには、業務用アカウントでログインしてください。",
    },
    "not_allowed": {
        "en": "This account is not on the list of allowed users. Ask the administrator to add it.",
        "ja": "このアカウントは利用者リストにありません。管理者に追加を依頼してください。",
    },
    "auth_missing": {
        "en": "Sign-in is not configured. Add the [auth] and [app] sections to the app's secrets.",
        "ja": "ログインが設定されていません。アプリの secrets に [auth] と [app] を追加してください。",
    },
    "allowlist_missing": {
        "en": "No allowed users are configured ([app] allowed_emails).",
        "ja": "利用者リスト（[app] allowed_emails）が設定されていません。",
    },
    "local_mode": {
        "en": "Local development mode: changes are written to this checkout only.",
        "ja": "ローカル開発モード：変更はこのチェックアウトにだけ書き込まれます。",
    },
    "reload": {"en": "Reload data", "ja": "データを再読み込み"},
    "saved": {"en": "Saved.", "ja": "保存しました。"},
    "conflict": {
        "en": "The data changed while saving. Reload and try again.",
        "ja": "保存中にデータが更新されました。再読み込みしてからもう一度お試しください。",
    },
    "save_failed": {"en": "Could not save", "ja": "保存できませんでした"},
    # ---- page titles
    "page_review": {"en": "Review records", "ja": "記録の確認"},
    "page_orgs": {"en": "Organisations", "ja": "対象機関"},
    "page_candidates": {"en": "Candidates", "ja": "候補組織"},
    "page_status": {"en": "Run status", "ja": "実行状況"},
    "page_search": {"en": "Search and export", "ja": "検索・書き出し"},
    "page_categories": {"en": "Possible new categories", "ja": "新分類の候補"},
    # ---- review
    "review_intro": {
        "en": "Mark each record as kept, blog candidate or discarded. Records needing review have a quote that was not found in the page. Each challenge and response comes with a suggested category: confirm it, change it, or mark it as a possible new category.",
        "ja": "各記録を「採用」「ブログ候補」「不採用」に分類してください。「要確認」は、引用がページ本文に見つからなかった記録です。課題・対応には分類が提案されているので、確定・変更するか「新分類の候補」としてください。",
    },
    "filter_status": {"en": "Status", "ja": "状態"},
    "filter_org": {"en": "Organisation", "ja": "組織"},
    "filter_label": {"en": "Label", "ja": "ラベル"},
    "filter_text": {"en": "Text contains", "ja": "含む語"},
    "all": {"en": "All", "ja": "すべて"},
    "no_records": {"en": "No records match.", "ja": "該当する記録はありません。"},
    "showing_n": {"en": "Showing {shown} of {total} records", "ja": "{total} 件中 {shown} 件を表示"},
    "page": {"en": "Page", "ja": "ページ"},
    "detected": {"en": "Detected", "ja": "検出日"},
    "published": {"en": "Published", "ja": "公開日"},
    "summary": {"en": "Summary", "ja": "要約"},
    "evidence": {"en": "Evidence (verbatim from the page)", "ja": "根拠（ページからの引用）"},
    "evidence_ok": {"en": "Found in the page text", "ja": "本文で確認済み"},
    "evidence_ng": {"en": "Not found in the page text", "ja": "本文に見つからず"},
    "challenges": {"en": "Challenges described on the page", "ja": "ページが述べている課題"},
    "responses": {"en": "Governance responses described on the page", "ja": "ページが述べている対応"},
    "suggested": {"en": "Suggested", "ja": "提案された分類"},
    "category": {"en": "Category", "ja": "分類"},
    "no_category": {"en": "No existing category", "ja": "既存の分類なし"},
    "cat_new": {"en": "Possible new category", "ja": "新分類の候補"},
    "cat_na": {"en": "Not applicable", "ja": "該当なし"},
    "fields": {"en": "Fields", "ja": "項目"},
    "partners": {"en": "Other organisations named", "ja": "言及された他の組織"},
    "decision": {"en": "Decision", "ja": "判断"},
    "note": {"en": "Note (optional)", "ja": "メモ（任意）"},
    "save": {"en": "Save", "ja": "保存"},
    "reviewed_by": {"en": "Reviewed by {who} on {date}", "ja": "{date} {who} が確認"},
    "status_new": {"en": "New", "ja": "新着"},
    "status_needs_review": {"en": "Needs review", "ja": "要確認"},
    "status_kept": {"en": "Kept", "ja": "採用"},
    "status_blog_candidate": {"en": "Blog candidate", "ja": "ブログ候補"},
    "status_discarded": {"en": "Discarded", "ja": "不採用"},
    # ---- organisations
    "orgs_tab_list": {"en": "Watch list", "ja": "ウォッチリスト"},
    "orgs_tab_add": {"en": "Add an organisation", "ja": "機関を追加"},
    "orgs_empty": {"en": "The watch list is empty.", "ja": "ウォッチリストは空です。"},
    "col_name": {"en": "Name", "ja": "名称"},
    "col_type": {"en": "Type", "ja": "種別"},
    "col_sector": {"en": "Sector", "ja": "分野"},
    "col_region": {"en": "Region", "ja": "地域"},
    "col_status": {"en": "Status", "ja": "状態"},
    "col_watch_points": {"en": "Watch points", "ja": "監視ページ"},
    "col_records": {"en": "Records", "ja": "記録数"},
    "col_last_check": {"en": "Last checked", "ja": "最終確認日"},
    "edit_org": {"en": "Edit an organisation", "ja": "機関を編集"},
    "watch_points_help": {
        "en": "One row per page to watch. Tick 'verified' only after opening the URL yourself.",
        "ja": "監視するページを1行ずつ入力します。「確認済み」は、自分で URL を開いて確かめた場合にのみチェックしてください。",
    },
    "org_name": {"en": "Name (English)", "ja": "名称（英語）"},
    "org_name_local": {"en": "Name (local language, optional)", "ja": "名称（現地語、任意）"},
    "org_id": {"en": "ID (lower case and hyphens)", "ja": "ID（英小文字とハイフン）"},
    "org_id_help": {
        "en": "Leave blank to create one from the name.",
        "ja": "空欄の場合は名称から自動で作成します。",
    },
    "org_domains": {
        "en": "Domains accepted as sources (comma-separated, e.g. example.com)",
        "ja": "出典として認めるドメイン（カンマ区切り。例：example.com）",
    },
    "org_type": {"en": "Organisation type", "ja": "組織の種別"},
    "ecosystem_role": {"en": "Role in the AI ecosystem", "ja": "AI エコシステムでの役割"},
    "size": {"en": "Size", "ja": "規模"},
    "inclusion_reason": {"en": "Reason for inclusion", "ja": "対象とした理由"},
    "lead_source": {
        "en": "Lead source (if from a candidate)",
        "ja": "手がかりの出どころ（候補から追加した場合）",
    },
    "discover_title": {"en": "Find pages to watch", "ja": "監視するページを探す"},
    "discover_url": {"en": "Home page URL", "ja": "トップページの URL"},
    "discover_button": {"en": "Find RSS feeds and sitemaps", "ja": "RSS とサイトマップを探す"},
    "discover_none": {
        "en": "No feeds or sitemaps found. Add watch points by hand below.",
        "ja": "RSS もサイトマップも見つかりませんでした。下の表に手で追加してください。",
    },
    "discover_disabled": {
        "en": "Set a contact address in crawl.user_agent (config/settings.yaml) before fetching pages.",
        "ja": "ページを取得する前に、config/settings.yaml の crawl.user_agent に連絡先を設定してください。",
    },
    "add_org_button": {"en": "Add to the watch list", "ja": "ウォッチリストに追加"},
    "added_org": {"en": "Added {name}.", "ja": "{name} を追加しました。"},
    "from_candidate": {"en": "Adding candidate: {name}", "ja": "候補から追加：{name}"},
    "required": {"en": "Please fill in: {fields}", "ja": "次の項目を入力してください：{fields}"},
    # ---- candidates
    "cand_intro": {
        "en": "Organisations named in records, and leads added by hand. Review them once a quarter.",
        "ja": "記録で言及された組織と、手で追加した手がかりです。四半期に1回確認します。",
    },
    "cand_show_all": {"en": "Show decided candidates too", "ja": "判断済みの候補も表示"},
    "cand_none": {"en": "No candidates.", "ja": "候補はありません。"},
    "mentions": {"en": "Mentions", "ja": "言及回数"},
    "lead_route": {"en": "Lead route", "ja": "手がかりの経路"},
    "lead_detail": {"en": "Lead detail", "ja": "手がかりの詳細"},
    "first_seen": {"en": "First seen", "ja": "初出"},
    "cand_add": {"en": "Add to watch list…", "ja": "ウォッチリストに追加…"},
    "cand_reject": {"en": "Reject", "ja": "見送り"},
    "cand_new_title": {"en": "Add a lead by hand", "ja": "手がかりを手で追加"},
    "cand_new_button": {"en": "Add lead", "ja": "手がかりを追加"},
    # ---- status
    "month_tokens": {"en": "Tokens this month", "ja": "今月のトークン"},
    "of_budget": {"en": "{pct}% of the monthly budget", "ja": "月間上限の {pct}%"},
    "est_cost": {"en": "Estimated cost this month", "ja": "今月の推定費用"},
    "pending_batches": {"en": "Batches pending", "ja": "処理中のバッチ"},
    "queue": {"en": "Items carried over", "ja": "繰り越し中の項目"},
    "current_run": {"en": "Latest run", "ja": "直近の実行"},
    "run_none": {"en": "No run yet.", "ja": "まだ実行されていません。"},
    "counts_flow": {
        "en": "Detected {d} → keywords {k} → stage 1 sent {s1} → passed {p} → recorded {r}",
        "ja": "検出 {d} → キーワード {k} → 段階1送信 {s1} → 通過 {p} → 記録 {r}",
    },
    "budget_stopped": {
        "en": "LLM calls were stopped this run because the monthly budget was reached.",
        "ja": "月間上限に達したため、この実行では LLM 呼び出しを停止しました。",
    },
    "errors": {"en": "Errors", "ja": "エラー"},
    "unreadable_pages": {
        "en": "Relevant pages that could not be read (check by hand)",
        "ja": "関連ありと判定されたが読み込めなかったページ（手動で確認）",
    },
    "manual_points": {"en": "Watch points to check by hand", "ja": "手動確認が必要な監視ページ"},
    "tokens_by_month": {"en": "Tokens by month and stage", "ja": "月・段階ごとのトークン"},
    "weekly_reports": {"en": "Weekly digests", "ja": "週次ダイジェスト"},
    "none": {"en": "None.", "ja": "なし。"},
    # ---- search
    "search_intro": {
        "en": "Search all records and download the result as CSV.",
        "ja": "すべての記録を検索し、結果を CSV で書き出せます。",
    },
    "date_from": {"en": "Detected from", "ja": "検出日（から）"},
    "date_to": {"en": "Detected to", "ja": "検出日（まで）"},
    "download_csv": {"en": "Download CSV", "ja": "CSV をダウンロード"},
    # ---- categories
    "categories_intro": {
        "en": "Challenges and responses the pages describe that fit no existing category (as suggested by "
        "the model, or as marked in review), and how often each existing category is used. "
        "Discarded records are left out.",
        "ja": "ページが述べている課題・対応のうち、既存の分類に当てはまらないもの（モデルの提案、またはレビューでの判断）と、"
        "既存の各分類の使用回数です。破棄した記録は除きます。",
    },
    "cat_candidates": {"en": "Findings without an existing category", "ja": "既存の分類に当てはまらない項目"},
    "cat_usage": {"en": "Use of existing categories", "ja": "既存の分類の使用回数"},
}


def t(key: str, lang: str, **kwargs: object) -> str:
    entry = TEXT.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry["en"]
    return text.format(**kwargs) if kwargs else text
