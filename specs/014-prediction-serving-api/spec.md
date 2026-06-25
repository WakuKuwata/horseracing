# Feature Specification: read-only 予測配信 API

**Feature Branch**: `014-prediction-serving-api`

**Created**: 2026-06-25

**Status**: Draft

**Input**: User description: "read-only 予測配信 API。新規 api/ パッケージ（FastAPI + uvicorn + pydantic）で既存データを JSON 配信。憲法 VI: front(React/Vite,015) が消費する API/DB 契約を確定。読み取り専用・書込なし・スキーマ変更なし。OpenAPI 自動生成。"

## 概要

新規 `api/` パッケージで、既存の永続データ（レース・予測・オッズ・推奨）を JSON で配信する**読み取り専用 API** を実装する。
憲法 VI（契約先行）に従い、本フィーチャーの目的は将来の front（React/Vite SPA, 015）が消費する **API/DB 契約（OpenAPI）の確定**で
ある。API は**既存の永続データとモデル出力を配信するのみ**で、学習・推論・賭けの実行や DB 書き込みを一切行わない。

**最重要（読み取り専用 / 書込禁止）**: API は**書き込み経路を一切呼ばない**。特に exotic 推奨は**永続済み行を SELECT で読むだけ**で、
`generate_exotic_recommendations`（行を INSERT/commit する書込）を**呼ばない**。`api/` は ORM モデル + 純粋な確率ヘルパ（009 結合確率・
010 推定オッズ）のみに依存し、書込ジェネレータには依存しない。

**監査・ラベル（憲法 V）**: 予測応答は `prediction_run_id`・`model_version`・`logic_version`・`computed_at` を含む。オッズは**実/推定を
明確に区別**（`odds_source`・`is_estimated`・`coverage_scope`・`updated_at`）し、推定は**疑似**、exotic 推奨は **二重疑似**を明示する
（front が疑似 ROI を実 ROI と誤認できない）。

**リーク境界（憲法 II）**: API は既存データを配信するのみで、応答値（オッズ・q'・予測）を**モデル特徴に一切還流しない**。スキーマ変更なし。

「利用者」は人間ではなく、API を呼ぶ front（015）と、レース/予測/オッズ/推奨を閲覧するオペレーター。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - レース一覧・詳細・ヘルスを取得できる (Priority: P1) 🎯 MVP

front/オペレーターが、レース一覧（日付・開催で絞込・ページング）とレース詳細（出走表・馬・状態）を取得でき、ヘルスチェックで
稼働を確認できる。

**Why this priority**: API の土台。レースを引けないと予測・オッズ・推奨も辿れない。契約の入口。

**Independent Test**: `GET /health` が稼働を返し、`GET /races?date=...&venue=...` が安定順序・ページングで一覧を返し、
`GET /races/{race_id}` が出走表（馬番・馬・entry_status）を返す。存在しない race_id は 404。

**Acceptance Scenarios**:

1. **Given** 稼働中の API, **When** `GET /health`, **Then** 200 と稼働ステータス（+ schema/contract バージョン）を返す。
2. **Given** レース群, **When** `GET /races?date=2008-06-01&venue=05&page=1&page_size=N`, **Then** **安定順序**（race_date DESC, venue,
   race_number）で**ページング**された一覧（最大 page_size 上限あり）と総件数/次ページ情報を返す。
3. **Given** 既存 race_id, **When** `GET /races/{race_id}`, **Then** レース属性 + 出走馬（馬番・horse_id・entry_status）を返す。
4. **Given** 存在しない race_id, **When** 取得, **Then** **404**（型付きエラー本体）。不正な race_id 形式は 422。

---

### User Story 2 - レースの予測を取得できる (Priority: P1)

front/オペレーターが、レースの最新予測（win/2着以内/3着以内 + 任意で結合確率）を、監査情報付きで取得できる。

**Why this priority**: 予測配信が本 API の中核価値。front の主要画面が消費する。

**Independent Test**: `GET /races/{race_id}/predictions` が**決定論的に選んだ最新 prediction_run** の per-horse 確率
（win/top2/top3）と監査情報（prediction_run_id/model_version/logic_version/computed_at）を返し、結合確率は `bet_type` 指定時のみ
（大グリッド抑制）返す。予測が無いレースは型付き空セクション。

**Acceptance Scenarios**:

1. **Given** 予測のあるレース, **When** `GET /races/{race_id}/predictions`, **Then** **決定論規則で選択した prediction_run**
   （採用モデル優先 → `computed_at DESC` → `prediction_run_id` タイブレーク）の per-horse `win`/`top2`/`top3` と、選択した
   `prediction_run_id`・`model_version`・`logic_version`・`computed_at` を返す。
2. **Given** 結合確率要求, **When** `?bet_type=exacta&top=K`, **Then** 009 を**取消・除外を母集団から除外+再正規化**した上で適用し、
   指定券種の上位 K のみ返す（**bet_type 無指定では大グリッド（三連単等）を返さない**＝性能保護）。結合確率の `logic_version` を含める。
3. **Given** 予測の無いレース, **When** 取得, **Then** **200** で空の予測セクション（null ではなく型付き空）+ レース自体は存在。
4. **Given** 予測はあるが使用可能な確率が無い（全頭欠損等）, **When** 結合確率算出, **Then** 型付き 409/422（500 にしない）。

---

### User Story 3 - レースのオッズ（実/推定）を取得できる (Priority: P1)

front/オペレーターが、単勝オッズ・推定市場オッズ（010）・実 exotic オッズ（012）を、**実/推定を明確に区別**して取得できる。

**Why this priority**: front がオッズと推定を**誤認なく**表示するための区別ラベルが契約の要。

**Independent Test**: `GET /races/{race_id}/odds` が、各行に `odds_source`（real/estimated）・`is_estimated`・`coverage_scope`・
`updated_at` を持つ判別可能なスキーマで返し、推定は疑似ラベル、実 exotic は最新値（上書き）を明示。オッズ欠損は 200 空。

**Acceptance Scenarios**:

1. **Given** 単勝オッズのあるレース, **When** `GET /races/{race_id}/odds`, **Then** 馬番別単勝オッズ（`odds_source=real`,
   `updated_at`）を返す。
2. **Given** 推定市場オッズ要求（010）, **When** 取得, **Then** 券種別推定オッズを `odds_source=estimated`・`is_estimated=true`・
   **疑似ラベル**付きで返す（実オッズと**同一フィールドに混ぜない**）。
3. **Given** 実 exotic オッズ（012）, **When** 取得, **Then** 券種別実配当を `odds_source=real`・`coverage_scope`（full/partial）・
   `updated_at`（最新値・上書き）付きで返す。**この値はモデル特徴に使われない**ことを契約注記。
4. **Given** オッズ欠損のレース, **When** 取得, **Then** **200** で空セクション（500 にしない）。

---

### User Story 4 - レースの exotic 推奨を取得できる (Priority: P1)

front/オペレーターが、永続済みの exotic EV 推奨（011/012）を、疑似/二重疑似ラベル付きで取得できる。

**Why this priority**: 推奨配信の価値。ただし**生成は書込なので API はしない**——永続行を読むだけ。

**Independent Test**: `GET /races/{race_id}/recommendations` が**永続済み `recommendations` 行を SELECT で**返し、
`bet_type`・`selection`（馬番配列）・`market_odds_used`/`estimated_market_odds_used`・`is_estimated_odds`・`pseudo_odds`・
`pseudo_roi`・`double_pseudo`・`logic_version`・`computed_at` を含む。**書込・生成は一切しない**。

**Acceptance Scenarios**:

1. **Given** 永続済み推奨のあるレース, **When** `GET /races/{race_id}/recommendations`, **Then** 各行に bet_type・selection・
   `is_estimated_odds`・`pseudo_odds`・`pseudo_roi`・`double_pseudo`（推定オッズ由来かを示す bool）・監査情報を返す。
2. **Given** 推奨要求, **When** 内部処理, **Then** API は `generate_exotic_recommendations`（書込）を**呼ばず**、SELECT のみ。書込
   経路は一切露出しない。
3. **Given** 推奨の無いレース, **When** 取得, **Then** 200 で空セクション。
4. **Given** front が表示, **When** 推奨を見る, **Then** 疑似 ROI を実 ROI と誤認しないラベル（二重疑似/推定オッズ使用）が付く。

---

### User Story 5 - 版付き OpenAPI 契約と /docs (Priority: P2)

front 開発者が、**版付き**の OpenAPI 契約（自動生成）と対話ドキュメント `/docs` を参照して 015 を実装できる。

**Why this priority**: 契約の安定性。React 015 がスキーマに強結合する前に版とエラーモデルを固定する。

**Independent Test**: 全エンドポイントが `/api/v1/` で版付けされ、`/docs`（OpenAPI UI）と `/openapi.json` が生成され、型付きエラー
モデル（404/422/409、`detail`/`code`）が一貫している。

**Acceptance Scenarios**:

1. **Given** API, **When** `/openapi.json` / `/docs` 取得, **Then** 全エンドポイントの pydantic スキーマから OpenAPI が生成される。
2. **Given** 任意のエラー, **When** 発生, **Then** 一貫した型付きエラー本体（status/code/detail）。全エンドポイントは `/api/v1/` 前置。

---

### Edge Cases

- **書込禁止（最重要）**: 推奨生成は書込のため API はしない。`/recommendations` は永続行を SELECT のみ。`api/` は書込ジェネレータに
  依存しない（ORM + 純粋確率ヘルパのみ）。
- **prediction_run 選択の決定論**: レースに複数 run/モデル版がありうる。採用モデル優先 → `computed_at DESC` → `prediction_run_id`
  タイブレークで一意選択し、選んだ `prediction_run_id` を応答に含める（監査再現）。
- **結合確率の性能**: 三連単 ~ N(N−1)(N−2)。`bet_type` 指定 + 上位 K に限定し、無指定で大グリッドを返さない。
- **実/推定の区別**: 実 exotic オッズと推定市場オッズを**別フィールド/別 source**にし、`is_estimated`/疑似ラベルで判別可能に。混在禁止。
- **欠損の扱い**: レース無し=404。レースはあるが予測/オッズ/推奨が無い=200 + 型付き空セクション（null ではない）。使用可能確率が
  無い結合確率算出=型付き 409/422（500 にしない）。
- **取消・除外**: 予測/結合確率は canonical 母集団（取消・除外を除外+再正規化、011/009 と同規律）で算出。
- **オッズの最新値**: exotic/単勝オッズは単一最新値（上書き、履歴なし、憲法 V）。`updated_at` を明示。**モデル特徴に還流しない**注記。
- **セッション寿命**: 長寿命 ASGI でのアプリスコープ engine/sessionmaker + リクエスト毎の読み取り専用セッション（確実な rollback/close）。
- **ページング安定性**: 安定順序（date DESC, venue, race_number）+ 最大 page_size。版付け（/api/v1）で front 結合前に固定。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: システムは**読み取り専用 API** を提供する MUST。DB 書き込み・学習・推論実行・賭け実行を一切行わない。スキーマ変更なし。
- **FR-002**: `api/` は ORM モデル（読み取り）+ 純粋確率ヘルパ（009 `joint_probabilities`・010 `estimate_market_odds`）のみに依存し、
  **書込ジェネレータ（`generate_exotic_recommendations` 等）を import/呼出しない** MUST（書込経路の非露出）。
- **FR-003**: `GET /health`・`GET /races`（date/venue 絞込・**安定順序・ページング・最大 page_size**）・`GET /races/{race_id}`
  （出走表・entry_status）を提供する MUST。存在しない race_id は 404、不正形式は 422。
- **FR-004**: `GET /races/{race_id}/predictions` は**決定論規則**（採用モデル優先 → `computed_at DESC` → `prediction_run_id`
  タイブレーク）で選んだ run の per-horse win/top2/top3 と、`prediction_run_id`/`model_version`/`logic_version`/`computed_at` を返す
  MUST。結合確率は `bet_type` 指定時のみ上位 K（無指定で大グリッドを返さない）、canonical 母集団で算出、その `logic_version` を含める。
- **FR-005**: `GET /races/{race_id}/odds` は単勝（real）・推定市場オッズ（010, estimated）・実 exotic（012, real）を、各行
  `odds_source`/`is_estimated`/`coverage_scope`/`updated_at` の**判別可能スキーマ**で返す MUST。推定は疑似、実/推定を別フィールドで
  区別し混在させない。
- **FR-006**: `GET /races/{race_id}/recommendations` は**永続済み `recommendations` 行を SELECT で**返す MUST: bet_type・selection
  （馬番配列）・market_odds_used・estimated_market_odds_used・is_estimated_odds・pseudo_odds・pseudo_roi・**double_pseudo**・
  logic_version・computed_at。**exotic 6 券種に限定**（win 推奨は selection が dict 形のため本エンドポイントの list[int] 契約外＝除外）。
  **生成（書込）はしない**。
- **FR-007**: システムは全レスポンスに監査/ラベルを含める MUST（憲法 V）: 予測は model/logic/run/時刻、オッズは source/estimated/
  coverage/updated_at、推奨は疑似/二重疑似。front が疑似を実と誤認できない。
- **FR-008**: システムは応答値（オッズ・q'・予測）を**モデル特徴に一切還流しない** MUST（憲法 II、読み取り専用で自明だが契約注記）。
- **FR-009**: 欠損は型付きで扱う MUST: レース無し=404、予測/オッズ/推奨無し=200 + 型付き空セクション、使用可能確率無しの結合確率=
  409/422（500 にしない）。
- **FR-010**: システムは**版付き**エンドポイント（`/api/v1/`）と pydantic スキーマからの **OpenAPI 自動生成**・`/docs`・`/openapi.json`
  を提供する MUST。型付きエラーモデル（status/code/detail）が一貫。
- **FR-011**: システムは長寿命 ASGI 向けに**アプリスコープ engine/sessionmaker** + リクエスト毎の**読み取り専用セッション**を用いる
  MUST。読み取り専用は **DB レベル（`SET TRANSACTION READ ONLY`）で強制**し（偶発書込を Postgres が拒否）、加えて確実な rollback/close。
  さらに静的解析（AST/import-graph）で書込 API・betting import を禁止（二重防御）。
- **FR-012**: 予測・結合確率は **canonical 母集団**（取消・除外を除外+再正規化、009/011 と同規律）で算出する MUST。
- **FR-013**: 認証・書込系（推奨生成/賭け実行）・Kelly・本格運用デプロイ・front 実装は将来に明示分離する MUST。

### Key Entities *(include if feature involves data)*

- **RaceSummary/RaceDetail**: レース属性 + 出走馬（馬番・horse_id・entry_status）。読み取り。
- **PredictionResponse**: 選択 run の per-horse win/top2/top3 + 監査（run_id/model/logic/時刻）+ 任意の結合確率（bet_type 別上位 K）。
- **OddsResponse**: 単勝（real）/ 推定（estimated, 疑似）/ 実 exotic（real, coverage/updated_at）を source 判別で。
- **RecommendationResponse**: 永続推奨行（selection・is_estimated_odds・pseudo_odds・pseudo_roi・double_pseudo・監査）。
- **ErrorModel / Pagination**: 型付きエラー（status/code/detail）・ページング（page/page_size/total/next）。
- **ContractVersion**: `/api/v1/` + OpenAPI（front 契約）。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: front/オペレーターが `/health`・`/races`（絞込・ページング）・`/races/{id}` でレースを一覧/詳細取得でき、欠損は 404/型付き
  空で一貫。
- **SC-002**: 予測が決定論的に選んだ run（採用優先 → computed_at → run_id）で返り、監査情報（run_id/model/logic/時刻）が全て付く。
- **SC-003**: 結合確率は bet_type + 上位 K に限定され、無指定で大グリッドを返さない（性能保護）。canonical 母集団で算出。
- **SC-004**: オッズが実/推定を判別可能スキーマ（source/estimated/coverage/updated_at）で返り、推定は疑似、混在しない。
- **SC-005**: 推奨が**永続行の SELECT のみ**で返り、書込（生成）を一切しない。二重疑似ラベルが付く。
- **SC-006**: 全エンドポイントが `/api/v1/` で版付けされ OpenAPI/`/docs` が自動生成、エラーモデルが型付きで一貫。
- **SC-007**: 応答値がモデル特徴に還流しない（リーク境界）。読み取り専用・スキーマ変更なし。

## Assumptions

- Feature 001–013 が適用済み（DB・予測・オッズ・推奨が永続）。`api/` は新規パッケージで db/probability に依存（**betting の書込には
  依存しない**——推奨は ORM で直接 SELECT）。010 推定オッズ・009 結合確率は純粋関数で API から呼べる。
- 「採用モデル優先」は `model_versions.adoption_status='active'` を優先し、無ければ最新 `computed_at` の run。選択 run_id を応答に含める。
- ページングは offset/page ベース（安定順序）。最大 page_size は既定上限（例 200）。版は `/api/v1/`。
- オッズ・推奨は単一最新値（上書き、履歴なし、憲法 V）。`updated_at`/`computed_at` を明示。
- 認証なし（ローカル/内部前提）。本格運用の認証・レート制限・デプロイは将来。日本語規約維持。
