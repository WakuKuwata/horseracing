# Implementation Plan: read-only 予測配信 API

**Branch**: `014-prediction-serving-api` | **Date**: 2026-06-25 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/014-prediction-serving-api/spec.md`

## Summary

新規 `api/` パッケージ（FastAPI + uvicorn + pydantic）で、既存の永続データ（レース/予測/オッズ/推奨）を **read-only JSON 配信**。
憲法 VI（契約先行）に従い front(React/Vite, 015) が消費する **OpenAPI 契約**を確定。**書込なし・スキーマ変更なし**。`api/` は ORM
モデル（読み取り）+ 純粋確率ヘルパ（009 `joint_probabilities`・010 `estimate_market_odds`）のみ依存し、**書込ジェネレータ
（`generate_exotic_recommendations`）に依存しない**（推奨は永続行 SELECT のみ）。エンドポイント `/api/v1/`: health/races/race/
predictions/odds/recommendations。

codex の CRITICAL（書込禁止・パッケージ結合・prediction_run 選択の決定論）を機構解消（下表）。

## Technical Context

**Language/Version**: Python 3.12（`uv`）

**Primary Dependencies**: **FastAPI**・**uvicorn**・**pydantic v2**（新規）。`horseracing-db`（ORM 読み取り）・`horseracing-probability`
（純粋ヘルパ 009/010）。**`horseracing-betting` には依存しない**（書込経路を露出しないため。推奨は ORM の `Recommendation` を直接 SELECT）。

**Storage**: PostgreSQL 16（**読み取りのみ**）。スキーマ変更なし。書込なし。

**Testing**: pytest + **FastAPI TestClient**（httpx）+ testcontainers（実 DB）。ユニット（スキーマ/選択ロジック/エラー）+ 統合
（実 DB で各エンドポイント・404/空・ページング・決定論 run 選択・実/推定区別・書込非発生）。

**Target Platform**: ローカル/内部の ASGI（uvicorn）。認証なし。

**Project Type**: 新規 `api/` パッケージ（web 層、リポジトリ初）。

**Performance Goals**: 結合確率は bet_type + 上位 K に限定（大グリッド回避）。一覧はページング（最大 page_size）。

**Constraints**: 読み取り専用・書込禁止・betting 非依存・スキーマ変更なし。prediction_run 選択は決定論。実/推定オッズ判別。監査ラベル
全付与。canonical 母集団。応答値をモデル特徴に還流しない。`/api/v1` 版付け。型付きエラー（404/422/409、500 回避）。

**Scale/Scope**: read-only 6 エンドポイント。認証/書込/Kelly/デプロイ/front は将来。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.0 に基づくゲート:

- [x] **I. データ契約**: race_id 12 桁・2007+。既存 ID 契約を読むのみ。予測ラベルは内部 `win/top2/top3`（表示は 1着/2着以内/3着以内
  に対応可）。新 ID なし。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: **読み取り専用**で、応答値（オッズ・予測・q'）を**モデル特徴に一切還流しない**
  （`api/` は features/training に書かない・呼ばない）。配信は既存の永続モデル出力のみ。**PASS**
- [x] **III. 評価先行**: 本 feature はモデル/特徴量を変更しない（配信のみ）。評価ハーネスは 003/010/012/013 が保有。該当外だが、配信する
  評価値（疑似 ROI 等）には疑似ラベルを付す。**PASS（対象外・配信側の明示）**
- [x] **IV. 確率整合性**: 結合確率は canonical 母集団（取消・除外を除外+再正規化）で 009 を適用。Σ=1 等は 009 を継承。**PASS**
- [x] **V. 再現性と監査**: 応答に `prediction_run_id`/`model_version`/`logic_version`/`computed_at`、オッズ `odds_source`/`is_estimated`/
  `coverage_scope`/`updated_at`、推奨 `is_estimated_odds`/`pseudo_odds`/`pseudo_roi`/`double_pseudo`。推定=疑似明示。**PASS**
- [x] **VI. feature 分割規律**: **UI の前に API/DB 契約を確定**（本 feature の目的）。read-only を MVP 境界に。書込/生成/Kelly/認証/
  front は将来に明示分離。スキーマ変更なし。**PASS（本原則の実装）**
- [x] **品質ゲート**: `codex:codex-rescue` second opinion を取得・記録（下表）。CRITICAL/HIGH を機構解消。**PASS**

### Second Opinion 記録（codex:codex-rescue — spec/plan 段階）

| 重大度 | codex 助言 | 本 plan の対応 |
|---|---|---|
| **CRITICAL** | `generate_exotic_recommendations` は書込。GET API が呼んではならない | `/recommendations` は永続 `Recommendation` を **SELECT のみ**（R1） |
| **CRITICAL** | betting 広域依存は書込経路露出リスク | `api/` は ORM + 純粋確率ヘルパのみ依存、**betting 非依存**（R1） |
| **CRITICAL** | `PredictionRun` に current マーカ無し、複数 run 可 | 決定論選択（active 優先 → computed_at DESC → run_id）+ 応答に run_id（R2） |
| HIGH | `joint_probabilities` は全組み合わせ materialize | **bet_type + 上位 K 限定**、無指定で大グリッド返さない（R3） |
| HIGH | 実/推定オッズが 1 フィールドに潰れる | 判別スキーマ `odds_source`/`is_estimated`/`coverage_scope`/`updated_at`、混在禁止（R4） |
| HIGH | 監査列（run/model/logic/時刻）を全付与 | predictions/odds/recommendations に監査・疑似ラベル（R5/V） |
| HIGH | exotic_odds は最新値上書き → 特徴量化禁止注記 | `updated_at` 明示 + 「モデル特徴に還流しない」契約注記（R4/II） |
| HIGH | 欠損オッズで `market_implied_win_probs` 例外 → 500 | 欠損は **200 空/404/409** の型付き（R6） |
| HIGH | 二重疑似を front が実 ROI と誤認 | `is_estimated_odds`+`pseudo_odds`+`pseudo_roi`+`double_pseudo` を露出（R4/R5） |
| MED | 空/非正確率で 009 例外 | 使用可能確率無し=型付き 409/422（500 にしない）（R6） |
| MED | ページング順序・最大サイズ未定義 | 安定順序（date DESC, venue, race_number）+ 最大 page_size（R7） |
| MED | not-found 契約 | レース無し=404、予測/オッズ/推奨無し=200 型付き空（null 不可）（R6） |
| MED | ASGI セッション寿命未検証 | アプリスコープ engine/sessionmaker + per-request 読み取り専用セッション（rollback/close）（R8） |
| MED | 非出走馬の混入 | canonical 母集団（取消・除外を除外+再正規化）（R3/IV） |
| MED | API 版が観測不能 | 全 `/api/v1/` 前置 + OpenAPI（R7） |
| LOW | read-only 境界は VI と整合 | 014 を厳格 read-only に、書込は CLI のまま（R1） |
| LOW | selection は馬番配列 | bet_type + selection 配列をそのまま、馬メタは別フィールド（R4） |

最重要 TOP3: ①書込禁止（推奨は SELECT のみ・betting 非依存）②prediction_run 決定論選択 ③実/推定オッズ判別 + 監査/疑似ラベル。

## Project Structure

### Documentation (this feature)

```text
specs/014-prediction-serving-api/
├── plan.md
├── research.md          # R1 read-only/依存境界 / R2 run 選択 / R3 結合確率制限/canonical / R4 オッズ判別 / R5 監査 / R6 エラー / R7 ページング・版 / R8 ASGI セッション
├── data-model.md        # pydantic レスポンススキーマ・選択規則・エラー/ページングモデル・不変条件
├── quickstart.md        # 起動 → 各エンドポイント検証 → OpenAPI/docs 手順
├── contracts/
│   ├── openapi_endpoints.md  # 各 GET /api/v1/... の入出力・ステータスの契約
│   └── response_schemas.md   # pydantic スキーマ（予測/オッズ/推奨/エラー/ページング）の契約
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
api/                                          # 新規パッケージ（FastAPI）
├── pyproject.toml                            # fastapi/uvicorn/pydantic + horseracing-db/-probability(path)
├── src/horseracing_api/
│   ├── app.py            # FastAPI app・lifespan(engine/sessionmaker)・/api/v1 router・例外ハンドラ
│   ├── deps.py           # per-request 読み取り専用 Session 依存（rollback/close）
│   ├── schemas.py        # pydantic レスポンス（Race/Prediction/Odds/Recommendation/Error/Page）
│   ├── selection.py      # prediction_run 決定論選択（active→computed_at→run_id）・canonical 母集団
│   ├── queries.py        # ORM 読み取りクエリ（races/predictions/odds/exotic_odds/recommendations）
│   └── routers/
│       ├── races.py          # /health, /races, /races/{id}
│       ├── predictions.py    # /races/{id}/predictions (win/top2/top3 + joint by bet_type)
│       ├── odds.py           # /races/{id}/odds (real/estimated 判別)
│       └── recommendations.py# /races/{id}/recommendations (SELECT only)
└── tests/                # TestClient ユニット + testcontainers 統合
```

**Structure Decision**: 新規 `api/` パッケージは **db（ORM 読み取り）+ probability（純粋 009/010）のみ**に依存。推奨は ORM の
`Recommendation` を直接 SELECT し、**betting の書込関数に依存しない**（書込経路の非露出）。FastAPI の lifespan で app スコープ
engine/sessionmaker を作り、依存で per-request 読み取り専用セッションを供給。pydantic スキーマが OpenAPI（front 契約）を生成。

## Complexity Tracking

> Constitution Check 違反なし。スキーマ変更なし。新規 web 依存（FastAPI/uvicorn/pydantic）は本 feature の目的（API 層）に内在。記入不要。
