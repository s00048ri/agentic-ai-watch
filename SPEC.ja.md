# SPEC — Agentic AI Watch: 公式発信モニタリングの試験導入（パイプライン）

[English](SPEC.md) | 日本語

Version: 0.1 (2026-10-12) / Owner: Rentaro Iida (GPAI Tokyo Centre)
対象：Claude Code。リポジトリ直下に置き、これを前提に実装する。

---

## 0. 目的と範囲

- 決めた組織リスト（ウォッチリスト）について、**各組織自身の公式サイトの発信だけ**を週1回確認し、agentic AI の導入・方針に関する新しい発信を見つけて、構造化した記録として蓄積する。
- 用途は**内部利用のみ**。収集結果は公開しない。チームが週次で確認し、定期ブログの材料にする。
- 4週間の試験導入として、20〜30組織から始める。
- **APIトークン・費用を最小にすること**が最重要の設計条件。

### やらないこと（Out of scope）
- 第三者サイト（ニュース、他のトラッカー、ベンダーの顧客事例）の内容を記録に使うこと。第三者情報は「候補組織」の手がかりとして手動で台帳に記録するだけ
- Web 検索ツールや自律ブラウジングでの探索（トークン消費が予測できないため）
- 収集結果の公開、組織の評価・スコア・ランキング
- 本文全文の保存
- ウォッチリストに登録したページ以外のクロール

## 1. 基本原則（Guardrails）

1. **一次情報のみ**：記録の出典 URL は、その組織自身のドメイン（台帳に登録したドメイン）に限る。他ドメインの URL は記録しない
2. **LLM の前に減らす**：新着検出とキーワード絞り込みは LLM なしで行い、LLM に渡す件数を最小にする
3. **推測しない**：出典に書かれていない項目は空欄（null）。モデルに推測させない
4. **根拠を検証する**：抽出時に得た「根拠の一文」が、取得した本文に逐語で含まれることをコードで確認する。含まれなければ記録は `needs_review` にする
5. **節度あるアクセス**：robots.txt を守る。同一ドメインへは1秒以上の間隔。週1回の頻度。User-Agent に連絡先を明記
6. **上限を必ず守る**：1回の実行あたりの件数上限と、月間トークン上限（§6）を超えたら処理を止め、Slack に通知する

## 2. 技術構成

- Python 3.12、依存管理は `uv`（または pip + `requirements.txt`）
- 主なライブラリ：`httpx`、`feedparser`、`pyyaml`、`pydantic`、`trafilatura`（本文抽出）、`anthropic`、`python-dateutil`
- 実行環境：**GitHub Actions（private リポジトリ）**。手元の Mac でも同じコマンドで動くこと
- データ保存：リポジトリ内のファイル（YAML / JSONL）。データベースは使わない
- 通知：Slack Incoming Webhook（#agentic_ai）
- Secrets：`ANTHROPIC_API_KEY`、`SLACK_WEBHOOK_URL`
- 前提：外部 API の利用と API キーの管理について、機構の規程上の確認が済んでいること（Ren 側で対応）

### ディレクトリ構成
```
/SPEC.md
/README.md
/ledger/
  organisations.yaml      # ウォッチリスト（§3.1）
  candidates.yaml         # 追加候補（§3.3）
/config/
  settings.yaml           # モデル名、上限、頻度など（§6）
  keywords.yaml           # 含める語・除外語（§4.2）
  taxonomy/
    challenges.yaml       # リビングリソースの分類をコピー
    responses.yaml
/prompts/
  relevance.md            # 段階1の指示文（固定・キャッシュ対象）
  extraction.md           # 段階2の指示文（固定・キャッシュ対象）
  synthesis.md            # 月次まとめの指示文
/state/
  seen.jsonl              # 検出済み URL と内容ハッシュ
  usage.jsonl             # 実行ごとのトークン使用量
/data/
  entries/YYYY-MM.jsonl   # 記録（§3.2）
  gold.yaml               # 精度確認用の正解データ（§8）
/reports/
  weekly/YYYY-MM-DD.md    # 週次ダイジェストの控え
  monthly/YYYY-MM.md      # 月次まとめ
/src/agentic_watch/
  cli.py  fetch.py  filter.py  llm.py  verify.py  store.py  digest.py  leads.py  evaluate.py  discover.py
/tests/
/.github/workflows/
  weekly.yml  collect.yml  monthly.yml  ci.yml
```

## 3. データモデル

### 3.1 `ledger/organisations.yaml`（ウォッチリスト）
```yaml
- id: example-org                  # 英小文字・ハイフン
  name: Example Organisation
  name_local: 例示組織              # 任意
  domains: [example.com]           # 出典として認めるドメイン
  org_type: private-sector         # private-sector | public-sector | academic-institution | industry-association | non-profit
  ecosystem_role: deployer         # developer | deployer | developer-deployer | regulator
  sector: telecommunications       # リビングリソースの語彙
  region: asia-pacific
  size: large                      # large | medium | small | unknown
  inclusion_reason: phase2-interviewee
    # phase2-interviewee | phase2-contacted | haip-reporter | public-body | commitment-signatory | added-from-candidates
  lead_source: null                # 候補から追加した場合の手がかりの出どころ
  status: active                   # active | dormant | removed
  added: 2026-10-13
  last_reviewed: 2026-10-13
  watch_points:
    - type: newsroom               # newsroom | tech-blog | ai-governance-page | responsible-ai-report | ir-annual-report | haip-report | transparency-record
      url: https://example.com/news
      method: rss                  # rss | sitemap | page-hash | manual
      feed_url: https://example.com/news/rss.xml
      language: en
      frequency: weekly            # weekly | monthly | annual
      verified: false              # URL を人が確認したら true
```
- **Claude Code は組織名や URL を作らない。** 雛形と、ドメインから RSS・サイトマップを探す補助スクリプト（§4.6）だけを用意し、中身は Ren が入れる。

### 3.2 `data/entries/YYYY-MM.jsonl`（記録）
1行1件。pydantic モデルで検証する。
```json
{
  "id": "sha1(url)",
  "org_id": "example-org",
  "url": "https://example.com/news/2026/agent-pilot",
  "title": "...",
  "published": "2026-10-10",
  "detected": "2026-10-13",
  "doc_type": "newsroom",
  "language": "en",
  "relevance": {"label": "deployment", "confidence": 0.82},
    // label: deployment | policy-governance | research | product-offering | not-relevant
  "summary": "自分たちの言葉で2文以内（英語）",
  "evidence": "出典からの逐語引用（300字以内）",
  "evidence_verified": true,
  "fields": {
    "sector": null, "application_area": null, "deployment_stage": null,
    "autonomy_cues": null, "human_oversight": null
  },
  "challenges": ["challenge-id"],
  "responses": ["response-id"],
  "other_organisations_named": ["Partner Co."],
  "status": "new",
    // new | needs_review | kept | discarded | blog_candidate
  "reviewer": null,
  "model": {"stage1": "claude-haiku-5-5", "stage2": "claude-haiku-5-5"}
}
```
- `fields`、`challenges`、`responses` は出典に明記がなければ null／空配列
- 本文は保存しない（処理中のメモリ上でのみ扱う）

### 3.3 `ledger/candidates.yaml`（追加候補）
```yaml
- name: Candidate Org
  lead_route: official-mention     # official-filing | official-mention | self-reporting-scheme | third-party | people
  lead_detail: "named in example-org release 2026-10-10"
  first_seen: 2026-10-13
  mentions: 1
  official_confirmation_url: null  # 組織自身のサイトで確認できたページ
  decision: pending                # pending | add | reject
  decided: null
```
- `official-mention` は記録の `other_organisations_named` から自動で追加・件数加算する（§4.5）
- それ以外の経路（第三者の手がかり、自薦、紹介など）は人が手で追記する

## 4. 処理の流れ

### 4.1 変化の検出（LLMなし）— `fetch.py`
- `status: active` の組織の各 watch point を `frequency` に従って確認
- `rss`：フィードを取得し、未検出の URL を新着とする
- `sitemap`：sitemap.xml を取得し、未検出 URL または `lastmod` が更新されたものを新着とする
- `page-hash`：ページ本文のハッシュを前回と比較し、変化があれば新着とする
- `manual`：処理しない（週次ダイジェストに「手動確認」として一覧のみ表示）
- 新着の URL のドメインが組織の `domains` に含まれなければ除外
- `state/seen.jsonl` で重複を除く（URL の正規化：クエリの追跡パラメータ削除、末尾スラッシュ統一）

### 4.2 キーワード絞り込み（LLMなし）— `filter.py`
- 対象：タイトル、URL のパス、RSS の要約（あれば）
- `config/keywords.yaml` の含める語に1つ以上一致し、除外語に一致しないものを通す
- 大文字小文字・全角半角を正規化して照合
- 初期値：
```yaml
include:
  en: ["agentic", "ai agent", "ai agents", "multi-agent", "multiagent", "autonomous agent", "agentic workflow", "agent platform"]
  ja: ["AIエージェント", "エージェント型AI", "エージェンティックAI", "自律型AI", "マルチエージェント", "AI agent"]
exclude:
  en: ["travel agent", "insurance agent", "real estate agent", "user agent", "secret agent", "free agent"]
  ja: ["代理店", "エージェント契約", "人材エージェント", "転職エージェント"]
```
- `ai-governance-page`、`responsible-ai-report`、`haip-report`、`transparency-record` の watch point は、キーワード絞り込みを通さず段階1に回す（件数が少なく、課題・対応が書かれやすいため）

### 4.3 段階1：関連性の判定（小型モデル）— `llm.py`
- 入力：タイトル＋本文の冒頭（RSS 要約、なければ本文抽出の先頭）を**合計 1,500 文字以内**
- 出力：`{"label": ..., "confidence": 0-1}` のみ（`max_tokens` 小さく）
- 指示文（`prompts/relevance.md`）は固定し、**プロンプトキャッシュ**を使う
- `confidence < settings.stage1.threshold`（初期 0.5）または `not-relevant` は段階2に回さない
- 構造化出力（JSON スキーマ／ツール使用）で返させ、パースに失敗したら1回だけ再試行

### 4.4 段階2：項目の抽出（小型モデル）
- 段階1を通ったものだけ、ページを取得して `trafilatura` で本文を抽出し、**先頭 6,000 文字以内**に切り詰める
- 指示文（`prompts/extraction.md`）には、リビングリソースの語彙と `config/taxonomy/` の分類（id と短い定義）を含め、キャッシュする
- 出力：§3.2 の `summary`、`evidence`、`fields`、`challenges`、`responses`、`other_organisations_named`
- 指示文に必ず含める規則：
  - 出典に明記されていないことは null / 空配列にする。推測しない
  - `evidence` は本文からの逐語引用（300字以内）
  - `challenges` / `responses` は、本文が明示的に述べている場合のみ、分類の id で付ける
  - `summary` は英語で2文以内、評価的な形容をしない
- `verify.py`：`evidence` が抽出本文に逐語で含まれるか確認（空白・改行を正規化して部分一致）。含まれなければ `evidence_verified: false`、`status: needs_review`

### 4.5 保存と候補の更新 — `store.py`、`leads.py`
- 記録を `data/entries/YYYY-MM.jsonl` に追記、`state/seen.jsonl` を更新
- `other_organisations_named` の組織名を `candidates.yaml` に追加（既存なら `mentions` を加算）。ウォッチリストに既にある組織は除外（名前の正規化で照合）

### 4.6 補助：見るページの探索 — `discover.py`（LLMなし）
- 入力：組織のトップページ URL
- 処理：HTML の `<link rel="alternate" type="application/rss+xml">`、robots.txt の `Sitemap:` 行、`/sitemap.xml` の有無を確認
- 出力：watch point の候補を標準出力に YAML で表示（台帳には自動で書き込まない。人が確認して貼る）

### 4.7 週次ダイジェスト — `digest.py`
#agentic_ai に投稿し、`reports/weekly/` に控えを保存する。
- 今週の件数（新着検出 → キーワード通過 → 段階1通過 → 記録）
- 新しい記録の一覧：組織名、タイトル（リンク）、要約1行、ラベル、確信度、言及された課題・対応
- `needs_review` の一覧
- 候補組織のうち `mentions` が増えたもの
- 手動確認が必要な watch point の一覧
- 今週のトークン使用量と推定費用、月間上限に対する割合
- 確認者は、記録の `status` を `kept` / `discarded` / `blog_candidate` に手で更新する（試験期間中は YAML/JSONL の直接編集で可）

### 4.8 月次まとめ — `cli.py synthesize --month YYYY-MM`
- 手動実行（`workflow_dispatch`）
- 入力：その月の `kept` と `blog_candidate` の記録の**構造化データのみ**（本文は渡さない）
- 上位モデルを1回だけ呼び、傾向のメモ（ブログの材料）を `reports/monthly/YYYY-MM.md` に出力
- 指示文で、出典リンクの付いた事実だけを使い、「公表されている範囲では」という限定を付けて書くよう指定

## 5. 実行方式（GitHub Actions）

| workflow | 契機 | 内容 |
|---|---|---|
| `weekly.yml` | 毎週月曜 06:00 JST（cron）＋手動 | 検出 → 絞り込み → 段階1の送信 |
| `collect.yml` | 2時間おき（cron）＋手動 | 未完了のバッチ結果を回収 → 段階2の送信／回収 → 保存 → 全段階完了で週次ダイジェスト投稿 |
| `monthly.yml` | 手動 | 月次まとめ |
| `ci.yml` | push / PR | lint、型チェック、テスト |

- 段階1・2は **Message Batches API**（半額）を使う。バッチ ID は `state/batches.json` に保存し、`collect.yml` が冪等に回収する
- `settings.yaml` の `use_batch: false` で、同期 API（プロンプトキャッシュ付き）に切り替えられること（件数が少ないとき・手元での試行用）
- 状態ファイルの更新は、workflow の最後に bot としてコミットする（コミットメッセージは中立的に：`chore: weekly run 2026-10-19`）

## 6. 設定と上限（`config/settings.yaml`）
```yaml
models:
  stage1: claude-haiku-5-5
  stage2: claude-haiku-5-5
  synthesis: claude-sonnet-5-5
use_batch: true
limits:
  stage1_max_items_per_run: 200
  stage2_max_items_per_run: 40
  stage1_max_input_chars: 1500
  stage2_max_input_chars: 6000
  stage1_max_output_tokens: 100
  stage2_max_output_tokens: 800
  synthesis_max_output_tokens: 3000
  monthly_token_budget: 3000000     # 入力＋出力の合計（キャッシュ読込も含めて記録）
stage1:
  threshold: 0.5
crawl:
  user_agent: "GPAI-Tokyo-Centre-research-pilot/0.1 (+contact: <email>)"
  min_interval_seconds_per_domain: 1.0
  timeout_seconds: 20
```
- 実行のたびに、API 応答の `usage`（入力、出力、キャッシュ作成、キャッシュ読込）を `state/usage.jsonl` に記録する
- 当月の累計が `monthly_token_budget` を超える見込みになったら、その時点で LLM 呼び出しを止め、残りは翌週に回し、Slack に通知する
- 上限件数を超えた分は、確信度ではなく検出日の古い順で翌週に繰り越す
- API コンソール側の月額上限は Ren が別途設定する

## 7. 運用ルール
- 組織の追加・除外は `organisations.yaml` を人が編集する。四半期に1回、`candidates.yaml` を見て判断する
- 12か月関連する記録のない組織は、人が `dormant` に変更する（自動では変更しない）
- 分類（`config/taxonomy/`）はリビングリソース側の最新版をコピーして使う。コード側に分類名を直書きしない

## 8. 精度の確認 — `evaluate.py`
- `data/gold.yaml` に、人が判定した約30件（URL、正しいラベル、関連の有無）を置く（Ren が作成）
- `cli.py evaluate`：段階1を gold に対して実行し、適合率・再現率・混同行列を出力
- 試験終了時に、情報源（watch point の種類）ごとの「新着件数、記録件数、課題・対応を含む割合、有用な1件あたりのトークン数」を集計して `reports/pilot-summary.md` に出す

## 9. テストと受け入れ基準
- `--no-llm`（ドライラン）で、検出と絞り込みまでを API なしで実行できる
- テスト：URL 正規化と重複除去、キーワード絞り込み（含める語・除外語・全角半角）、他ドメインの除外、robots.txt の遵守、根拠の一文の検証、上限超過時の停止
- 実 API を使うテストはモックで行い、CI では API キーを使わない
- 本文全文がどのファイルにも保存されていないこと
- 1回の週次実行のトークン使用量が `usage.jsonl` に記録され、ダイジェストに表示されること
- README に、セットアップ（secrets、hooks）、台帳の書き方、手動実行の方法を記載

## 10. 優先度
| 優先度 | 内容 |
|---|---|
| P0 | 台帳の雛形とスキーマ、検出（RSS・サイトマップ）、キーワード絞り込み、段階1・2（同期 API でまず動かす）、根拠の検証、保存、週次ダイジェスト、上限と使用量の記録、ドライラン |
| P1 | Batch API と `collect.yml`、候補の自動更新、`discover.py`、`evaluate.py` |
| P2 | `page-hash` 方式、月次まとめ、試験終了時の集計レポート |

判断に迷う点は決め打ちせず、質問としてまとめて返すこと。
