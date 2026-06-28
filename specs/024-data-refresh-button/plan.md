# Implementation Plan: netkeiba データ更新ボタン

**Branch**: `024-data-refresh-button` | **Date**: 2026-06-28 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/024-data-refresh-button/spec.md`

## Summary

レース一覧／詳細ページに「データ更新」ボタンを追加し、押すと netkeiba から最新データ（結果未確定=出馬表+オッズ／確定後=結果）を取り込んで表示を最新化する。中核は **014 read-only API を一切変更せず、書き込み経路を物理分離する**こと。新しい **`ops/` write サービス（owner ロール）** が更新要求を受け、既存 `ingestion_jobs` を耐久キューとして QUEUED 行を INSERT して即 202 を返す。常駐 **worker** が `FOR UPDATE SKIP LOCKED` で取り出し、既存 `scrape/`（008/022 実パーサ・rate-limit 済み）で取得して DB 反映する。front はジョブを polling し、完了後に 014 を再取得して表示更新。**スキーマ変更なし**（`ingestion_jobs` の既存カラム `trace_id`/`retry_count`/`max_retry`/`summary` で親子バッチ・リトライ・監査を表現）。

## Technical Context

**Language/Version**: Python 3.12（ops API + worker）、TypeScript / React 18（front）

**Primary Dependencies**: FastAPI + uvicorn + pydantic v2（ops API、014 とは別アプリ）、SQLAlchemy 2.0 + psycopg3、既存 `horseracing_scrape`（httpx + selectolax、robots/rate-limit/backoff/cache）、`horseracing_live`（guards / list_pending）；front は React + Vite + openapi-typescript

**Storage**: PostgreSQL 16。**スキーマ変更なし**。既存 `ingestion_jobs`（`trace_id`・`retry_count`・`max_retry`・`checkpoint`・`summary` JSONB を流用）＋コアテーブル（race_horses / race_results）。`pg_advisory_xact_lock` で race 単位の重複起動を排他。

**Testing**: pytest + testcontainers（ops API 契約・worker 統合・dedup/leak/readonly 不変条件）、Vitest + React Testing Library + MSW（front のボタン／polling／状態遷移）、openapi-typescript drift-check（ops 契約の型同期）

**Target Platform**: Linux コンテナ。単一インスタンスのローカル／staging 運用（018 の docker compose に `ops` と `worker` サービスを追加）

**Project Type**: web（新 `ops/` write サービス＋ `worker` 常駐プロセス）＋ 既存 `front/` SPA への加筆。read 経路（014/app_ro）は不変。

**Performance Goals**: ボタン押下→「受付済み」表示は体感即時（取得完了を待たせない、202）。実取得は netkeiba の rate-limit（ドメイン約 1 req/s）に律速。日次バッチは worker の並列度上限内（rate-limit と二重で抑制）。

**Constraints**: 014 read-only を壊さない（write を足さない／app_ro を維持）。ops/worker のみ owner ロール（`DATABASE_URL_OWNER`）。robots/rate-limit を尊重。確定結果は追記のみ（保護）、事前オッズ上書きは result-pending のみ。取り込んだ odds/results をモデル特徴量に流さない（II）。

**Scale/Scope**: 日次バッチ＝1 日あたり数開催 × 各 12R 程度（数十レース）。利用はオペレータのオンデマンド操作（単一ユーザー想定、認証は対象外）。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] **I. データ契約**: 更新対象は 12 桁 `race_id` のみ（`is_valid_race_id` で検証、不正/非存在は起動せず拒否＝FR-019）。netkeiba race_id は JRA-VAN と同一桁で、URL 構築は entity guess-join ではない（`urls.py` の既述どおり）。馬/騎手/調教師の ID は既存 `id_mappings` 経由でのみ結合（scrape 既存挙動を流用）。→ **PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 本機能は表示用データの取り込みのみで、新しい予測特徴量を作らない。取り込んだ odds/results は**モデル特徴量に流入させない**（FR-020）。leak-guard テストで「ops/worker は training/eval/feature 経路に触れない」「odds/results が特徴量化されない」を固定。→ **PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: モデル/特徴量の変更を含まない（予測ロジック不変）。→ **N/A**
- [x] **IV. 確率整合性**: 確率の生成・正規化に触れない（表示は既存 014 のまま）。→ **N/A**
- [x] **V. 再現性・監査**: 各更新は `ingestion_jobs` に対象・種別・状態・件数・時刻・`summary`（parser_version 等）を記録（FR-017）。事前オッズは上書き、確定結果は追記のみ（008 既存規則）。推定/疑似値の表示ラベル（PseudoBadge）は不変（FR-022）。→ **PASS**
- [x] **VI. feature 分割規律**: UI 着手前に ops API の OpenAPI 契約を確定（contracts/ops-api.yaml）。read（014/app_ro）と write（ops/owner）を**経路として物理分離**（FR-021）。**新テーブルなし**＝初期 DB 契約（`ingestion_jobs`）の範囲内で完結。→ **PASS**
- [x] **品質ゲート**: 設計の非自明点（ジョブ実行形態・dedup 排他・親子バッチ表現・スキーマ要否）について `codex:codex-rescue` の second opinion を 2 回取得（spec 前のアーキ方針、plan 前の実装論点）。両案差分と採用根拠を [research.md](./research.md) に記録。→ **PASS**

**結論: 全ゲート PASS（III/IV は N/A）。スキーマ変更なしのため Complexity Tracking の正当化記載は不要。**

**技術制約への適合（手動実行原則）**: 憲法の「初期はすべて手動実行・自動運用（スケジューラ/検知/定期再学習）は将来」に対し、本機能の常駐 worker は **operator がボタンで起動したジョブ（`ingestion_jobs` の queued 行）を実行する executor に限定**する。cron/スケジューラ・自動検知・定期再取得は実装しない（deferred）。よって「ボタン＝手動トリガ」の範囲に収まり、自動運用には踏み込まない。同じく憲法の「スクレイピングは当面 CLI/API でよい」に対し、UI ボタン要件と 014 read-only 維持の両立のために write 経路を別サービス化する（read-only を汚さないための分離であり、強制的なサービス分割ではない）。

## Project Structure

### Documentation (this feature)

```text
specs/024-data-refresh-button/
├── plan.md              # This file
├── research.md          # Phase 0: 設計判断と second opinion の記録
├── data-model.md        # Phase 1: ingestion_jobs の利用形（refresh ジョブ/バッチの状態モデル）
├── quickstart.md        # Phase 1: end-to-end 検証手順
├── contracts/
│   └── ops-api.yaml     # Phase 1: 新 ops write API の OpenAPI 契約
└── tasks.md             # Phase 2 (/speckit-tasks) — NOT created here
```

### Source Code (repository root)

```text
ops/                                  # 新規: write/ingestion サービス（owner ロール）
├── pyproject.toml
├── src/horseracing_ops/
│   ├── app.py                        # FastAPI（/ops/v1/*）— 014 とは別アプリ・別ポート
│   ├── deps.py                       # owner ロールの engine/session（read+write）
│   ├── schemas.py                    # 202 受付・ジョブ状態の pydantic 契約
│   ├── enqueue.py                    # advisory-lock dedup + ingestion_jobs INSERT(QUEUED)
│   ├── routers/refresh.py            # POST /races/{id}/refresh, POST /days/{date}/refresh
│   ├── routers/jobs.py              # GET /jobs/{id}, GET /batches/{trace_id}
│   ├── worker.py                     # 常駐 worker: FOR UPDATE SKIP LOCKED → run_one()
│   └── runner.py                     # 1 ジョブ実行: 種別再判定 → scrape_* → 状態更新
└── tests/
    ├── integration/                  # enqueue/dedup/worker/leak/readonly 不変条件
    └── unit/

front/src/                            # 既存 SPA への加筆
├── api/opsClient.ts + ops-schema.d.ts# ops API 用クライアント + 生成型（drift-check）
├── components/RefreshButton.tsx      # 受付/取得中/成功/一部成功/失敗 の状態表示
├── components/DayRefreshButton.tsx   # 一覧の一括更新（レース別進捗・失敗分再実行）
├── pages/RaceDetailPage.tsx          # US1 ボタン設置 + 完了後 invalidate
└── pages/RaceListPage.tsx            # US2 ボタン設置

deploy/                               # 018 への追記
└── docker-compose.yml                # ops(owner) + worker(owner) サービス追加、nginx で /ops/ を ops へ
```

**Structure Decision**: 新規 `ops/` パッケージ（FastAPI write サービス＋常駐 worker）を追加し、既存 `scrape/`・`live/`・`db/` を依存として再利用する。読み取りは 014（`api/`、app_ro）に据え置き一切変更しない。front は既存 SPA にボタンと polling を加筆。`ingestion_jobs` を耐久キュー兼監査として流用するため **DB スキーマ変更なし**。

## Complexity Tracking

> スキーマ変更なし・憲法ゲート全 PASS のため、正当化を要する違反は無し。

設計上の「単純な代替を退けた」判断は research.md に記録（要約）:

| 判断 | 採用 | 退けた単純案と理由 |
|------|------|--------------------|
| ジョブ実行形態 | 別常駐 worker ＋ `ingestion_jobs` 耐久キュー | FastAPI BackgroundTasks（in-process）は API 再起動で取得中ジョブが宙吊り・無監査になる。netkeiba は遅く失敗もするため耐久キューが要る。`ingestion_jobs` が既に retry_count/max_retry を持ち追加コスト極小。 |
| 重複起動の排他 | `pg_advisory_xact_lock(refresh:race:{id})` | `ingestion_jobs` に一意制約が無く `SELECT FOR UPDATE` だけでは競合を防げない。新規 UNIQUE 制約（スキーマ変更）より advisory lock が無改変で確実。 |
| 親子バッチ表現 | `trace_id` で親(refresh_day)と子(refresh_race)を束ねる | 新テーブル/新カラムは憲法 VI で要正当化。既存 `trace_id` で十分表現でき変更不要。 |
