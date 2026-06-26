# Feature Specification: RaceFront（閲覧専用 React/Vite フロント）

**Feature Branch**: `015-racefront-spa`

**Created**: 2026-06-26

**Status**: Draft

**Input**: User description: "RaceFront（React/Vite SPA）。新規 front/ パッケージで 014 read-only API（/api/v1, OpenAPI）を消費する閲覧専用フロント。OpenAPI から型自動生成。レース一覧 + レース詳細（予測/オッズ/推奨）。実/推定/疑似/二重疑似を UI で明確区別（誤読防止）。閲覧専用。"

## 概要

新規 `front/` パッケージで、Feature 014 の **read-only 予測配信 API**（`/api/v1`, OpenAPI）を消費する**閲覧専用フロント**を実装する。
憲法 VI（契約先行）で 014 が確定した API/DB 契約を画面化する。OpenAPI から型を自動生成して API スキーマと同期させ、レース一覧と
レース詳細（出走表・予測・オッズ・推奨）を表示する。

**最重要（誤読防止 / 憲法 V）**: 実オッズ・推定市場オッズ・疑似・二重疑似を **UI で明確に区別**し、ユーザが**疑似 ROI を実 ROI と
誤読できない**ようにする。区別はプロ文ではなく、API の判別フィールド（`odds_source`/`is_estimated`/`double_pseudo`）に紐づけた
バッジ/ラベルで表現し、**「疑似値がラベルなしで表示されないこと」をテストで不変条件として担保**する。

**閲覧専用（憲法 II）**: フォーム/操作で書込・賭け実行を一切行わない。フロントは表示のみで、応答値をモデルに還流しない。API（014）は
**CORS を持たず変更しない**——フロントは開発時 Vite proxy 経由（`/api` → API サーバ）で相対パス呼び出しする。

「利用者」は、レースの予測・オッズ・推奨を閲覧する人間（オペレーター/分析者）。

## User Scenarios & Testing *(mandatory)*

### User Story 1 - レース一覧を絞り込んで閲覧し詳細へ遷移 (Priority: P1) 🎯 MVP

利用者が、日付・開催でレースを絞り込み、ページングで一覧を見て、各レースの詳細へ遷移できる。読み込み中・空・エラーが区別表示される。

**Why this priority**: フロントの入口。レース一覧が無いと詳細にも辿れない。

**Independent Test**: 一覧ページで日付/開催フィルタとページング操作ができ、各行から詳細へリンクし、(a) 読み込み中、(b) 該当ゼロ件
（空状態の明示）、(c) API エラー（型付きエラー本体）を**それぞれ別表示**する。

**Acceptance Scenarios**:

1. **Given** API にレースがある, **When** 一覧を開く, **Then** 日付/開催で絞り込み、ページング（次へ/前へ、総件数・has_next）で
   レースが安定順に並び、各行から詳細へ遷移できる。
2. **Given** フィルタ該当ゼロ件, **When** 表示, **Then** **空状態**（「該当レースなし」）を明示（空白やスピナー固定ではない）。
3. **Given** API がエラー/到達不能, **When** 表示, **Then** **エラー状態**（型付きエラー `code/detail` を踏まえたメッセージ）を表示。
4. **Given** 読み込み中, **When** 待機, **Then** **ローディング状態**を表示し、完了後に内容へ切替。

---

### User Story 2 - レース詳細で予測を監査情報付きで閲覧 (Priority: P1)

利用者が、レース詳細で出走表と予測（1着率/2着以内率/3着以内率）を、どのモデル/実行の予測かが分かる監査情報付きで閲覧できる。

**Why this priority**: 予測の閲覧が中核価値。再現性のため監査情報の提示は必須。

**Independent Test**: 詳細ページが出走表（馬番・馬・状態）と per-horse 予測（win/top2/top3）を表示し、**prediction_run_id /
model_version / logic_version / computed_at** を可視（ツールチップだけに埋めない）。予測無しのレースは型付き空を明示。

**Acceptance Scenarios**:

1. **Given** 予測のあるレース, **When** 詳細を開く, **Then** 出走表 + per-horse 1着率/2着以内率/3着以内率と、選択された予測実行の
   監査情報（run_id/model/logic/時刻）が**画面上に明示**される。
2. **Given** 確率が欠損（null）の馬, **When** 表示, **Then** 数値を `--`/`未提供` 等で**安全表示**（NaN/クラッシュにしない）。
3. **Given** 予測の無いレース, **When** 表示, **Then** 予測セクションを**空状態**として明示（404 ではなく 200 の型付き空を区別）。

---

### User Story 3 - オッズを実/推定で明確に区別表示 (Priority: P1)

利用者が、単勝の実オッズ・推定市場オッズ・実 exotic オッズを、**実か推定かが一目で分かる**形で閲覧できる。

**Why this priority**: 誤読防止の核（憲法 V）。実と推定を混同させない区別が契約の要。

**Independent Test**: オッズ表示が `odds_source`/`is_estimated` に基づき、推定には**「推定（疑似）」バッジ**、実には実ラベルを付け、
別セクション/別列で**混在させない**。推定 exotic には `as_of`、実 exotic には coverage/updated_at を表示。**推定値がラベルなしで
表示されないことをテストで担保**。

**Acceptance Scenarios**:

1. **Given** 実単勝オッズ, **When** 表示, **Then** 実ラベル（`odds_source=real`）+ updated_at。推定市場オッズは別セクションで
   **「推定（疑似）」** バッジ + `as_of`。実 exotic は coverage_scope + updated_at。
2. **Given** 推定オッズ行, **When** レンダリング, **Then** **必ず推定/疑似ラベルが付く**（ラベル無しの推定値は存在しない＝不変条件テスト）。
3. **Given** オッズ欠損（200 空）, **When** 表示, **Then** 空状態を明示（エラーにしない）。

---

### User Story 4 - exotic 推奨を二重疑似ラベル付きで閲覧 (Priority: P1)

利用者が、永続済み exotic EV 推奨を、**疑似 ROI を実 ROI と誤読しない**ラベル付きで閲覧できる。

**Why this priority**: 誤読防止の核。推奨の疑似 ROI を実績と取り違えると意思決定を誤る。

**Independent Test**: 推奨表示が各行の `is_estimated_odds`/`double_pseudo` に基づき、`pseudo_roi` を**「疑似ROI」**として、二重疑似行に
**「二重疑似」** バッジを付ける。実オッズ使用行（is_estimated_odds=false）と推定オッズ使用行を区別。**疑似値がラベルなしで表示されない**。

**Acceptance Scenarios**:

1. **Given** 推奨行, **When** 表示, **Then** bet_type/selection と、`pseudo_roi` は**「疑似ROI」**表記、`double_pseudo=true` は
   **「二重疑似（推定オッズ + PL 外挿）」** バッジ、監査（logic_version/computed_at/run_id）が付く。
2. **Given** 任意の疑似値（pseudo_odds/pseudo_roi/推定オッズ）, **When** レンダリング, **Then** **必ず疑似/二重疑似ラベルが伴う**
   （ラベル無しの疑似値は存在しない＝不変条件テスト）。
3. **Given** 推奨の無いレース, **When** 表示, **Then** 空状態を明示。

---

### User Story 5 - API 契約（OpenAPI）と型の同期 (Priority: P2)

開発者が、API の OpenAPI から型を生成して使い、フロント型が 014 契約から**乖離しない**ことを担保できる。

**Why this priority**: 契約安定性（憲法 VI）。型がドリフトすると誤データ表示や実行時エラーの温床になる。

**Independent Test**: コミット済みの OpenAPI スナップショットから型が生成され、再生成結果がスナップショットと一致（差分があれば検知）。
API を起動して `/openapi.json` から再取得・更新するスクリプトがある。

**Acceptance Scenarios**:

1. **Given** コミット済み OpenAPI スナップショット, **When** 型生成, **Then** 生成型がフロントで使われ、スナップショットとの差分が
   無いことを検査できる（ドリフト検知）。
2. **Given** API 変更, **When** スナップショット更新, **Then** 起動中 API の `/openapi.json` から再生成し差分をレビューできる。

---

### Edge Cases

- **疑似ラベル不変条件（最重要）**: 推定オッズ・pseudo_odds・pseudo_roi・double_pseudo の各値は**ラベル/バッジ無しでは表示されない**。
  これを型（判別ユニオン）+ コンポーネント + テストで担保（リファクタでラベルが落ちない）。
- **実/推定の非混在**: 実オッズと推定オッズを同一行/列に混ぜない（別セクション・別バッジ）。
- **null 数値**: win/top2/top3・各オッズ・pseudo_* は null をとりうる → `--`/`未提供` で安全表示（NaN/クラッシュ禁止、整形前に null ガード）。
- **3 状態の区別**: ローディング / 空（200 typed-empty: run=null・items=[]）/ エラー（404/409/422 の型付き本体）を**別表示**。空白固定にしない。
- **ページングは一覧のみ**: `/races` のみ Page（total/has_next）。詳細の予測/オッズ/推奨はフラット配列でページングしない。
- **監査の可視化**: prediction_run_id/model_version/logic_version/computed_at（予測）・as_of（推定オッズ）を画面に明示（ツールチップのみに埋めない）。
- **書込ゼロ**: フォーム送信/賭け/生成のような書込 UI を持たない（閲覧専用）。
- **CORS**: 014 は CORS 無しのまま。フロントは相対 `/api/v1/*` を Vite proxy 経由で呼ぶ。本番 CORS/リバースプロキシは将来。

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: システムは**閲覧専用フロント**を提供する MUST。書込・賭け実行・生成 UI を持たない。応答値をモデルに還流しない（憲法 II）。
- **FR-002**: システムはレース一覧（**日付/開催フィルタ・ページング（total/has_next）・各レースへのリンク**）を表示する MUST。
- **FR-003**: システムはレース詳細で出走表（馬番・馬・状態）と per-horse 予測（1着率/2着以内率/3着以内率）を表示し、
  **prediction_run_id/model_version/logic_version/computed_at を画面に明示**する MUST。
- **FR-004**: システムはオッズを **実/推定で明確に区別**して表示する MUST: 推定は「推定（疑似）」バッジ + `as_of`、実は実ラベル +
  updated_at、実 exotic は coverage_scope。**別セクション/別列で混在させない**。
- **FR-005**: システムは exotic 推奨を表示し、**`pseudo_roi` を「疑似ROI」**、`double_pseudo=true` を**「二重疑似」**バッジで明示する
  MUST。is_estimated_odds の真偽で実/推定使用を区別する。
- **FR-006 (不変条件・最重要)**: システムは**疑似値（推定オッズ/pseudo_odds/pseudo_roi/double_pseudo）がラベル/バッジ無しで表示
  されない**ことを保証する MUST。判別フィールドにラベルを型レベルで紐づけ、**「ラベル無しの疑似値は存在しない」をテストで検証**する。
- **FR-007**: システムは **ローディング / 空（200 typed-empty）/ エラー（404/409/422 の `{status,code,detail}`）** を**それぞれ別表示**
  する MUST（空白固定にしない、型付きエラー本体を踏まえる）。
- **FR-008**: システムは null をとりうる数値（予測/オッズ/pseudo_*）を **`--`/`未提供` 等で安全表示**する MUST（NaN/クラッシュ禁止）。
- **FR-009**: システムは **API の OpenAPI から型を生成**し、フロント型が 014 契約と同期する MUST。**OpenAPI スナップショットを
  コミット**し、生成型がスナップショットと一致することを検査（ドリフト検知）、起動中 API から再生成するスクリプトを持つ。
- **FR-010**: システムは API を **相対 `/api/v1/*`** で呼び、開発時は Vite proxy 経由とする MUST。**API（014）は変更しない**
  （CORS 無しのまま）。本番 CORS/リバースプロキシは将来に明示分離。
- **FR-011**: テストは（RTL + API モック）で、各エンドポイントの **full/空/エラー** 分岐、**null 数値**、**ページング操作**、そして
  **疑似ラベル不変条件**（疑似値がラベル無しで出ない）を検証する MUST。
- **FR-012**: 認証・書込系・本格運用デプロイ・Kelly・E2E（Playwright）は将来に明示分離する MUST。

### Key Entities *(include if feature involves data)*

- **RaceListView**: フィルタ（日付/開催）・ページング（total/has_next）・レース行（→ 詳細リンク）。
- **RaceDetailView**: 出走表 + 予測（+ run 監査）+ オッズ（実/推定区別）+ 推奨（疑似/二重疑似）。
- **OddsDisplay**: 実（real）/推定（estimated, 疑似バッジ + as_of）/実 exotic（coverage）を判別表示。
- **RecommendationDisplay**: 疑似ROI/二重疑似バッジ + 監査。
- **ApiTypes**: OpenAPI 生成型（コミットスナップショット）+ ドリフト検査。
- **状態表現**: Loading / Empty / Error（型付き）。

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 利用者が一覧で日付/開催絞込・ページングし、各レース詳細へ遷移でき、空/エラー/ローディングが区別表示される。
- **SC-002**: 詳細で予測（1着率/2着以内率/3着以内率）と監査情報（run_id/model/logic/時刻）が画面上に明示される。
- **SC-003**: オッズが実/推定を視覚的に区別表示し、推定には疑似バッジ + as_of が必ず付く（混在なし）。
- **SC-004**: 推奨で pseudo_roi が「疑似ROI」、double_pseudo が「二重疑似」バッジで明示される。
- **SC-005（不変条件）**: 疑似値（推定オッズ/pseudo_odds/pseudo_roi/double_pseudo）がラベル無しで表示される箇所がゼロ（テストで担保）。
- **SC-006**: null 数値が安全表示（NaN/クラッシュなし）。OpenAPI 生成型がコミットスナップショットと一致（ドリフト検知）。
- **SC-007**: フロントは書込を一切行わず（閲覧専用）、API（014）を変更しない（CORS 無しのまま、相対 + dev proxy）。

## Assumptions

- Feature 014（read-only API, `/api/v1`, OpenAPI）が稼働。フロントは 014 の契約のみに依存し、DB/他パッケージに直接依存しない。
- 型は OpenAPI からコード生成し、**コミット済みスナップショット**で同期を担保（API 起動が必要な再生成はスクリプト + 任意実行）。
- 開発は Vite dev サーバ + proxy（`/api` → ローカル API）。本番配信（静的ビルド + CORS/リバースプロキシ）は将来。
- UI 文言は日本語。スタイルは最小（誤読防止のラベル/バッジを優先、デザインシステムは将来）。
- 表示は最新値（オッズは上書き・履歴なし）。フロントは API のラベル（疑似/実/coverage/updated_at/as_of）をそのまま尊重・可視化。
- 認証なし（内部/ローカル前提）。書込・Kelly・E2E・本格デプロイは将来。
