# Tasks: read-only 予測配信 API

**Input**: Design documents from `specs/014-prediction-serving-api/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/openapi_endpoints.md, contracts/response_schemas.md, quickstart.md

**Tests**: 含む（憲法 II リーク/書込禁止・V 監査・契約安定性は必須。TestClient ユニット + testcontainers 統合）

**Organization**: User story 単位（P1 US1 レース → P1 US2 予測 → P1 US3 オッズ → P1 US4 推奨 → P2 US5 契約/版）。MVP=US1。

## パス規約

新規 `api/` パッケージ（`horseracing_api`）。src=`api/src/horseracing_api/`、tests=`api/tests/{unit,integration}/`。全パスはルート相対。
**read-only**: 全ハンドラ SELECT のみ、commit しない。`api/` は db + probability のみ依存（**betting 非依存**）。

---

## Phase 1: Setup（新規 api パッケージ・ASGI 基盤）

- [x] T001 `api/pyproject.toml` を作成する: deps に `fastapi`/`uvicorn[standard]`/`pydantic>=2` + `horseracing-db`/`horseracing-probability`（`[tool.uv.sources]` path, editable）。**betting は入れない**。dev に pytest/httpx/testcontainers/ruff。ruff line-length=100
- [x] T002 [P] `api/src/horseracing_api/__init__.py`（`API_VERSION="v1"`, `SCHEMA_VERSION`）と `api/tests/__init__.py`/`unit`/`integration` 雛形を作成する
- [x] T003 `api/src/horseracing_api/deps.py` に **per-request 読み取り専用セッション** 依存 `get_session()` を実装する: app スコープ sessionmaker（`db.session.create_session_factory`）から Session を yield。**トランザクションを DB レベルで READ ONLY 化**（リクエスト開始時に `SET TRANSACTION READ ONLY` を実行 = 偶発書込は Postgres が拒否）。finally で **rollback + close**（commit しない）。これにより rollback 任せでなく DB が書込を物理的に拒否する(research.md R8)
- [x] T004 `api/src/horseracing_api/app.py` に FastAPI app を作成する: **lifespan** で app スコープ engine/sessionmaker（`db.session.create_db_engine`）、`/api/v1` ルータ束ね、**型付き例外ハンドラ**（ErrorBody）、`/docs`/`/openapi.json` 有効化（FR-010/FR-011）
- [x] T005 `cd api && uv sync` を実行し、`from horseracing_api.app import app` と FastAPI TestClient 起動・`horseracing_betting` が **import されないこと**（依存に無い）を確認する

**Checkpoint**: ASGI app が起動し /docs が出る。読み取り専用セッションと版付けルータが存在。

---

## Phase 2: Foundational（共有スキーマ・選択・クエリ・全 US 前提）

**⚠️ 全 US が schemas/selection/queries を共有。先に確定。**

- [x] T006 [P] `api/src/horseracing_api/schemas.py` に pydantic v2 レスポンス型を定義する: `ErrorBody`/`Page[T]`/`RaceSummary`/`HorseEntry`/`RaceDetail`/`RunAudit`/`HorsePrediction`/`JointEntry`/`PredictionResponse`/`WinOddsRow`/`EstimatedOddsRow`/`RealExoticOddsRow`/`OddsResponse`/`RecommendationRow`/`RecommendationResponse`（contracts/response_schemas.md 準拠、Decimal→float、frozen）。**ソースが nullable な値（win/top2/top3、odds、pseudo_odds/pseudo_roi、market/estimated odds 等）は `float | None`**（None で検証エラーを出さない）。**全 odds 行に `odds_source`+`is_estimated`**（win/real_exotic=false、estimated=true）、estimated 行に `as_of`（現時点再計算時刻）、real 行に `updated_at`、real_exotic に `coverage_scope`
- [x] T007 `api/src/horseracing_api/selection.py` に予測 run の**決定論選択** `select_prediction_run(session, race_id)` を実装する: `PredictionRun` を **`model_versions` に JOIN**（`PredictionRun.model_version == ModelVersion.model_version`）し、`adoption_status='active'` を優先（active を先頭に並べる sort key）→ `computed_at DESC` → `prediction_run_id DESC` タイブレーク（PredictionRun 自体に adoption_status 列は無いため JOIN 必須）。無ければ None。**canonical 母集団** `canonical_started(session, race_id, run_id)`（取消・除外を除外、win_prob>0、再正規化用）も実装(research.md R2/R3)
- [x] T008 [P] `api/src/horseracing_api/queries.py` に ORM 読み取りクエリを実装する: races 一覧（**安定全順序** `ORDER BY race_date DESC NULLS LAST, venue_code NULLS LAST, race_number NULLS LAST, race_id` + ページング + **フィルタ適用後の COUNT で total/has_next**）、race 詳細 + 出走馬、run 予測（win/top2/top3）、race_horses 単勝オッズ、`exotic_odds`（実）、`recommendations`（**SELECT のみ・bet_type が exotic 6 券種のもの**＝win の dict selection を除外）。すべて commit しない(research.md R1)

### Foundational テスト

- [x] T009 [P] `api/tests/unit/test_selection.py` を作成: `select_prediction_run` が active 優先 → computed_at → run_id で決定論選択、複数 run/同時刻タイブレーク、無予測で None、canonical が取消・除外を除外することを検証（SC-002）

**Checkpoint**: スキーマ・決定論選択・読み取りクエリが単体で検証済み。

---

## Phase 3: User Story 1 - レース一覧・詳細・ヘルス (Priority: P1) 🎯 MVP

**Goal**: `/health`・`/races`（絞込・安定順序・ページング）・`/races/{id}`（出走表）を read-only 提供。

**Independent Test**: TestClient で `/api/v1/health` 200、`/api/v1/races?date=&venue=&page=` が安定順序ページング、`/races/{id}` が
出走表、存在しない id は 404・不正形式は 422。

### 実装

- [x] T010 [US1] `api/src/horseracing_api/routers/races.py` に `GET /api/v1/health`（DB 接続 SELECT 1 + schema/api version）、`GET /api/v1/races`（date/venue 絞込・page/page_size・最大上限・安定順序・`Page[RaceSummary]`）、`GET /api/v1/races/{race_id}`（`RaceDetail`、404 if 無し・422 形式不正 `^[0-9]{12}$`）を実装し app に登録する（FR-003/FR-009）

### US1 テスト

- [x] T011 [P] [US1] `api/tests/integration/test_races_api.py` を作成: 実 DB(testcontainers)で /health 200、/races の絞込・ページング（安定順序・total・has_next・最大 page_size）、/races/{id} 出走表、404（無し）・422（不正形式）を検証（SC-001/SC-006）

**Checkpoint**: US1 単独で動作・テスト緑。レースを引ける（MVP）。

---

## Phase 4: User Story 2 - 予測取得 (Priority: P1)

**Goal**: 決定論選択 run の win/top2/top3 + 監査、結合確率は bet_type+上位 K 限定（canonical 母集団）。

**Independent Test**: `/races/{id}/predictions` が決定論 run の per-horse 確率 + run 監査を返し、`?bet_type=exacta&top=K` で上位 K の
joint（canonical）を返す。無指定では joint を返さない。予測無し=200 空、確率欠損 joint=409/422。

### 実装

- [x] T012 [US2] `api/src/horseracing_api/routers/predictions.py` に `GET /api/v1/races/{race_id}/predictions` を実装する: `select_prediction_run` で run 選択 → per-horse win/top2/top3 + `RunAudit`、`?bet_type=&top=K` 指定時のみ **canonical 母集団に 009 `joint_probabilities` を適用**し**確率降順 `(-prob, selection_key)` の決定論順で上位 K** の `joint` + `joint_logic_version`（無指定で大グリッド返さない）。`JointEntry.selection` は **`db.canonical_selection(bet_type, numbers)`** で 011/012 と同一正準配列に直列化（009 の tuple/frozenset キー → 馬番配列）。予測無し=200 空、使用可能確率無し=型付き 409/422（009 例外を捕捉）、race 無し=404（FR-004/FR-012/FR-009）

### US2 テスト

- [x] T013 [P] [US2] `api/tests/integration/test_predictions_api.py` を作成: 実 DB で決定論 run 選択 + 監査情報、bet_type 指定で上位 K joint（canonical で取消・除外除外）、bet_type 無指定で joint 無し、予測無し 200 空、確率欠損 joint 409/422、race 無し 404 を検証（SC-002/SC-003）

**Checkpoint**: US2 単独で動作・テスト緑。

---

## Phase 5: User Story 3 - オッズ取得（実/推定 判別） (Priority: P1)

**Goal**: win(real)/estimated(010,疑似)/real_exotic(012) を別フィールドで区別配信。

**Independent Test**: `/races/{id}/odds` が win/estimated/real_exotic を別フィールド・判別ラベル（source/is_estimated/coverage/
updated_at）で返し混在しない。オッズ欠損=200 空。

### 実装

- [x] T014 [US3] `api/src/horseracing_api/routers/odds.py` に `GET /api/v1/races/{race_id}/odds` を実装する: race_horses 単勝（`WinOddsRow` real/updated_at）、010 `estimate_market_odds` を **canonical 母集団の win オッズ + canonical field_size** に適用した推定（`EstimatedOddsRow` estimated/is_estimated/pseudo、selection は `db.canonical_selection`、bet_type+上位 K で抑制）、`exotic_odds` 実配当（`RealExoticOddsRow` real/coverage_scope/updated_at）を**別フィールド**で返す。**注記**: estimated は「現時点の推定（再計算）」、推奨行の `estimated_market_odds_used` は「推奨時スナップショット」で別物（front は混同しない）。欠損=200 空（`market_implied_win_probs` の MarketOddsError を捕捉、500 にしない）、race 無し=404（FR-005/FR-007/FR-008/FR-009）

### US3 テスト

- [x] T015 [P] [US3] `api/tests/integration/test_odds_api.py` を作成: 実 DB で win/estimated/real_exotic が別フィールド・判別ラベルで返り混在しない、推定=疑似ラベル、実 exotic=coverage/updated_at、オッズ欠損 200 空（500 でない）、race 無し 404 を検証（SC-004）

**Checkpoint**: US3 単独で動作・テスト緑。

---

## Phase 6: User Story 4 - 推奨取得（永続 SELECT のみ） (Priority: P1)

**Goal**: 永続済み exotic 推奨を SELECT で配信、二重疑似ラベル付き、**書込なし**。

**Independent Test**: `/races/{id}/recommendations` が永続 `recommendations` 行を返し、is_estimated_odds/pseudo_odds/pseudo_roi/
double_pseudo/監査付き。**GET 後に recommendations 行数が不変**（書込なし）。

### 実装

- [x] T016 [US4] `api/src/horseracing_api/routers/recommendations.py` に `GET /api/v1/races/{race_id}/recommendations` を実装する: 永続 `recommendations` を **SELECT のみ・exotic 6 券種に限定**（win の `selection` は dict なので除外、`selection: list[int]` 契約を満たす）で返す（`RecommendationRow`: bet_type/selection/market_odds_used/estimated_market_odds_used/is_estimated_odds/pseudo_odds/pseudo_roi/`double_pseudo`=is_estimated_odds/logic_version/computed_at/prediction_run_id）。**`generate_exotic_recommendations` を import/呼出しない**。推奨無し=200 空、race 無し=404（FR-006/FR-002）

### US4 テスト

- [x] T017 [P] [US4] `api/tests/integration/test_recommendations_api.py` を作成: 実 DB で永続推奨が二重疑似ラベル付きで返り、**GET 前後で `recommendations` 行数が不変（書込なし）**、推奨無し 200 空、race 無し 404 を検証（SC-005）
- [x] T018 [P] [US4] `api/tests/unit/test_no_write_boundary.py` を作成: **書込境界の静的ガード（AST + import-graph）** — `horseracing_api` の全モジュールを **AST 解析**し、`Session.commit/flush/add/add_all/delete/merge/bulk_*` 呼出、INSERT/UPDATE/DELETE を含む `execute`、生 SQL の書込を**使用しない**ことを検証。さらに **import 閉包**に `horseracing_betting`/`horseracing_training`/書込ジェネレータ・Kelly・auth・deploy 系モジュールが**現れない**ことを検証（FR-013 含む）。**DB レベル READ ONLY**（T003）の二重防御として(SC-005/SC-007/FR-002/FR-013/憲法 II)

**Checkpoint**: US4 単独で動作・テスト緑。書込が構造的に不可能。

---

## Phase 7: User Story 5 - 版付き OpenAPI 契約 + /docs (Priority: P2)

**Goal**: `/api/v1` 版付け・OpenAPI/docs 自動生成・型付きエラーモデル一貫。

**Independent Test**: 全エンドポイントが `/api/v1/` 前置、`/openapi.json`/`/docs` 生成、エラー本体が `{status,code,detail}` で一貫。

### 実装

- [x] T019 [US5] `api/src/horseracing_api/app.py` の例外ハンドラ/ルータ前置を仕上げる: 404/422/409 を `ErrorBody` に統一、全ルータ `/api/v1`、OpenAPI メタ（title/version=v1/description）を設定（FR-010）

### US5 テスト

- [x] T020 [P] [US5] `api/tests/integration/test_openapi_contract.py` を作成: `/openapi.json` が全 6 エンドポイントと全スキーマを含む、全パスが `/api/v1/` 前置、404/422/409 が ErrorBody 形、`/docs` 200 を検証（SC-006）

**Checkpoint**: 全 US 完了。OpenAPI 契約が front(015) 向けに固定。

---

## Phase 8: Polish & Cross-Cutting

- [x] T021 [P] `api/src/horseracing_api/__init__.py` に公開 API（app）を整理し、README 相当の docstring を整える
- [x] T022 [P] lint 解消: `cd api && uv run ruff check .`
- [x] T023 全テスト緑を確認: `cd api && uv run pytest tests/unit && uv run pytest -m integration`
- [x] T024 [P] [quickstart 検証] `specs/014-prediction-serving-api/quickstart.md` を実 DB（ローカル horseracing、2008 データ）で実行: uvicorn 起動 → 各エンドポイント curl → /docs 確認（SC-001〜SC-007）

---

## Dependencies & Execution Order

- **Phase 1 (Setup)**: 先頭。T001→T002[P]→T003→T004→T005（パッケージ/ASGI 基盤、順次）。
- **Phase 2 (Foundational)**: Setup 後。T006[P]/T007/T008[P]→T009。**全 US をブロック**（schemas/selection/queries）。
- **Phase 3 (US1, MVP)**: Foundational 後。T010→T011[P]。
- **Phase 4 (US2)**: Foundational 後（selection/queries 共有）。T012→T013[P]。
- **Phase 5 (US3)**: Foundational 後（queries + 010 純粋ヘルパ）。T014→T015[P]。
- **Phase 6 (US4)**: Foundational 後（queries の recommendations SELECT）。T016→T017[P]/T018[P]。
- **Phase 7 (US5)**: US1–US4 のルータ存在後。T019→T020[P]。
- **Phase 8 (Polish)**: 全実装後。

### User Story 独立性

- US1–US4 は Foundational（schemas/selection/queries）共有後、各ルータは独立に実装・テスト可能。US5 は全ルータの版/契約を束ねる。

## Parallel 実行例

- Foundational: T006/T008 を並走。US テストは各 [P]。Polish: T021/T022/T024 を並走。

## 実装戦略

1. **MVP first**: Phase 1→2→3（US1）で「レース一覧/詳細/health」を最短達成、/docs で契約の芽を提示。
2. **配信拡充**: US2（予測）→ US3（オッズ）→ US4（推奨, SELECT のみ）。
3. **契約固定**: US5 で /api/v1 + OpenAPI + エラーモデルを front(015) 向けに確定。
4. 各 Checkpoint で独立テストを緑に。憲法 II（read-only・書込禁止・応答非還流）/ V（監査・疑似ラベル）/ VI（契約先行）を全タスクで維持。
