# Agentic AI Watch（試験導入）

[English](README.md) | 日本語

ウォッチリストに登録した組織について、**各組織自身の公式サイトの発信だけ**を週1回確認し、agentic AI の導入・方針に関する新しい発信を構造化した記録として蓄積する内部ツールです。収集結果はウェブサイト等では公表しません（リポジトリ上のファイルは除きます）。仕様は [SPEC.ja.md](SPEC.ja.md) を参照してください。

## 処理の流れ

```
検出（RSS・サイトマップ・ページハッシュ、LLMなし）
  → 自組織ドメインの URL だけ残す・既出を除く
  → キーワード絞り込み（LLMなし。AIガバナンス系ページは素通し。サイトマップの新着は先にページのタイトルと冒頭を読む）
  → 段階1：関連性の判定（Haiku、入力1,500字・出力100トークン以内）
  → 段階2：項目の抽出（Haiku、本文先頭6,000字・出力800トークン以内）
  → 根拠の一文が本文に逐語で含まれるかコードで確認（含まれなければ needs_review）
  → data/entries/YYYY-MM.jsonl に保存、候補組織を更新、週次ダイジェストを Slack に投稿
```

- 本文はメモリ上でのみ扱い、どのファイルにも保存しません（テストで確認しています）。
- 段階1・2は既定で Message Batches API（半額）を使います。`weekly.yml` が段階1を送り、`collect.yml`（2時間おき）が結果を回収して段階2を送り、すべて終わったらダイジェストを投稿します。
- 費用を抑えるため、段階1・2は thinking を無効にし（effort は low）、JSON スキーマで出力させます。固定の指示文にはプロンプトキャッシュを指定しています。
- 月間トークン上限（`limits.monthly_token_budget`）を超える見込みになると LLM 呼び出しを止めて Slack に通知し、残りは翌週に繰り越します。

## セットアップ

### 1. 手元（Mac）

```bash
# uv が無ければ: curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
uv run agentic-watch validate      # 設定・分類・台帳の検証
uv run pytest -q                   # テスト（API は使いません）
```

### 2. 設定で必ず変えるもの

- `config/settings.yaml` の `crawl.user_agent`：`<email>` を連絡先に置き換えてください。置き換えるまで、クロールするコマンドは停止します。
- `config/settings.yaml` の `pricing`：ダイジェストの推定費用に使う単価です。現行の料金を確認して更新してください。

### 3. GitHub

1. **Settings → Secrets and variables → Actions** に次の2つを登録します。
   - `ANTHROPIC_API_KEY`：Claude API のキー（API コンソール側でも月額上限を設定してください）
   - `SLACK_WEBHOOK_URL`：#agentic_ai の Incoming Webhook の URL
2. **Slack の Incoming Webhook**：Slack の「Incoming Webhooks」アプリで #agentic_ai を投稿先に選び、発行された URL を上の secret に入れます。未設定の場合、ダイジェストは `reports/weekly/` に保存されるだけで投稿はされません。
3. **Settings → Actions → General → Workflow permissions** を「Read and write permissions」にします（状態ファイルを bot がコミットするため）。

| workflow | 契機 | 内容 |
|---|---|---|
| `weekly.yml` | 毎週月曜 06:00 JST ＋手動 | 検出 → 絞り込み → 段階1の送信 |
| `collect.yml` | 2時間おき ＋手動 | バッチ結果の回収 → 段階2 → 保存 → ダイジェスト投稿（処理待ちが無ければ数秒で終了） |
| `monthly.yml` | 手動（月を指定） | 月次まとめ |
| `ci.yml` | push / PR | lint、型チェック、テスト（API キーは使いません） |

## Web アプリ

チーム向けの、ログインが必要な Web アプリです（英語が基本。サイドバーで日本語に切り替えられます）。

| 画面 | できること |
|---|---|
| 記録の確認 | 新着・要確認の記録を絞り込み、要約・根拠の一文・出典を見て「採用」「ブログ候補」「不採用」をメモ付きで付ける |
| 対象機関 | ウォッチリストを最終確認日・記録数とともに表示。状態や監視ページを変更。機関を追加（トップページから RSS とサイトマップを提案。「verified」は各 URL を自分で開いて確認してからチェック） |
| 候補組織 | 言及回数の多い順に表示。ウォッチリストへの追加、見送り、手がかりの手入力 |
| 実行状況 | 今月のトークンと推定費用、処理中のバッチ、直近の実行の件数とエラー、過去の週次ダイジェスト |
| 検索・書き出し | すべての記録を検索し、CSV でダウンロード |

変更はすべて GitHub API 経由でこのリポジトリにコミットされます。週次の bot や他のメンバーが直前に更新していた場合は、最新の内容に対して自動でやり直します。

### Streamlit Community Cloud への公開

1. [share.streamlit.io](https://share.streamlit.io) に GitHub でログインし、このリポジトリからアプリを作成します（ブランチ `main`、ファイル `streamlit_app.py`、Advanced settings で Python 3.12）。
2. [Google Cloud Console](https://console.cloud.google.com/) で Google ログインを設定します（どの Google アカウントでもプロジェクトを作成できます）。
   1. プロジェクトを作成します（例：`agentic-ai-watch`）。
   2. **API とサービス → OAuth 同意画面**：ユーザーの種類は **外部**、アプリ名「Agentic AI Watch」、サポートと開発者の連絡先に自分のメールアドレス。スコープは `openid`・`email`・`profile` だけ（既定のまま。Google の審査は不要です）。公開ステータスは **テスト** のままにし、**テストユーザー** に事務局メンバーの Google アカウントを追加します。ここに登録した人しかログインできないので、`allowed_emails` に加えた二重のチェックになります。
   3. **API とサービス → 認証情報 → 認証情報を作成 → OAuth クライアント ID**：種類は **ウェブ アプリケーション**、承認済みのリダイレクト URI に `https://<アプリ名>.streamlit.app/oauth2callback` を登録し、クライアント ID とシークレットを控えます。
   4. テスト状態のままだと、1週間程度で再ログインが必要になることがあります。
3. GitHub で、このリポジトリだけを対象に「Contents: Read and write」を付けた fine-grained personal access token を作ります。
4. [`.streamlit/secrets.toml.example`](.streamlit/secrets.toml.example) に沿って、アプリの **Secrets** に設定を貼り付けます。`allowed_emails` が利用できる人のリストで、それ以外の人はログイン後に拒否されます。`[auth]` が無い場合、アプリは起動しません。
5. 任意：Streamlit Community Cloud のアプリ設定でも、閲覧者を同じ人に限定できます。

ログインなしで手元で試す場合：`AGENTIC_WATCH_DEV=1 uv run streamlit run streamlit_app.py`（変更は手元のチェックアウトにだけ書き込まれます）。

## Issue フォーム

GitHub アカウントを持つ人は **Issues → New issue** から次の2つを使えます。

- **Propose an organisation（機関の提案）**：`ledger/organisations.yaml` に機関を追加するプルリクエストを自動で作ります。トップページで見つかった RSS とサイトマップは `verified: false` で監視ページに入るので、マージ前に各 URL を確認してください。
- **Suggest a candidate lead（候補の手がかり）**：`ledger/candidates.yaml` に手がかりを追加するプルリクエストを作ります。

プルリクエストが自動で作られるのは、リポジトリの collaborator からの提案だけです。それ以外の提案には返信だけを付け、手で対応します。リポジトリは public なので Issue も公開されます。ラベル `add-organisation` と `candidate-lead` を一度だけ作成してください（Issues → Labels）。フォームの選択肢は `config/` から生成しています。語彙を変えたら `uv run agentic-watch issue-templates` を実行してください（CI が古いままでないか確認します）。

## 台帳の書き方

### ウォッチリスト `ledger/organisations.yaml`

組織名・URL は人が確認して入れます（ツールは作りません）。項目は SPEC.ja.md §3.1 のとおりです。`sector` と `region` は `config/taxonomy/vocabularies.yaml` の id を使います。

watch point の `method` ごとの書き方：

| method | `url` | `feed_url` | 新着の判定 |
|---|---|---|---|
| `rss` | 一覧ページ | RSS/Atom の URL（必須） | 未検出の URL |
| `sitemap` | 見たい範囲（例 `https://example.com/news`）。この path 以下の URL だけを見ます | サイトマップの URL（省略時は robots.txt の `Sitemap:`、なければ `/sitemap.xml`） | 未検出の URL、または `lastmod` の更新 |
| `page-hash` | 監視するページ | 不要 | 本文のハッシュの変化 |
| `manual` | 手で見るページ | 不要 | 処理せず、ダイジェストに「手動確認」として表示 |

- **最初の確認時の扱い**：watch point を初めて確認したときは、`crawl.first_run_lookback_days`（既定30日）より古い項目と日付の無いサイトマップ項目を「既出」として記録するだけにし、処理しません（過去記事が一度に段階1へ流れ込むのを防ぐため）。`page-hash` は初回も処理します。
- 見るページの候補探し：`uv run agentic-watch discover https://example.com/` で RSS とサイトマップの候補を YAML で表示します。台帳には書き込まないので、確認して貼り付けてください。

### 候補組織 `ledger/candidates.yaml`

記録の `other_organisations_named` に出た組織は、`official-mention` として自動で追加されます（既にあれば `mentions` を加算。ウォッチリストにある組織は除外）。第三者の手がかり・自薦・紹介などは手で追記してください。追加の判断は四半期に1回、ここを見て行います。

## 週次の確認

Web アプリの **記録の確認** 画面で、各記録を「採用」「ブログ候補」「不採用」に分類します（`data/entries/YYYY-MM.jsonl` の `status`・`reviewer`・`reviewed`・`review_note` が更新されます）。ファイルを直接編集しても構いません。`needs_review` は根拠の一文が本文に見つからなかった記録です。

## 手動実行

```bash
uv run agentic-watch run --no-llm          # ドライラン：検出と絞り込みだけ（API なし、何も書き込まない）
uv run agentic-watch run --sync            # 同期 API で全段階を実行（件数が少ないとき・試行用）
uv run agentic-watch run                   # バッチ API（段階1を送信）
uv run agentic-watch collect               # バッチ結果の回収・段階2・ダイジェスト
uv run agentic-watch digest [--post]       # 直近の実行のダイジェストを作り直す
uv run agentic-watch evaluate              # data/gold.yaml に対する段階1の精度
uv run agentic-watch synthesize --month 2026-11
uv run agentic-watch pilot-summary         # 試験終了時の集計 → reports/pilot-summary.md
```

GitHub 上では **Actions → 該当 workflow → Run workflow** で手動実行できます。

## ファイル

| パス | 内容 |
|---|---|
| `config/taxonomy/` | リビングリソースの分類（challenges / responses）と語彙のコピー。更新はコピーで行い、コードに分類名は書きません |
| `prompts/` | 段階1・段階2・月次まとめの指示文 |
| `state/seen.jsonl` | 検出済み URL（最後の行が有効） |
| `state/queue.jsonl` | 上限で翌週に繰り越した項目（本文は含みません） |
| `state/batches.json` | 送信したバッチと処理状況 |
| `state/run.json` | 実行中の週の件数など |
| `state/checks.json` | watch point ごとの最終確認日（頻度の判定用） |
| `state/usage.jsonl` | API 応答ごとのトークン使用量 |
| `data/entries/` | 記録 |
| `reports/` | 週次ダイジェストの控え、月次まとめ、集計 |
