# Tasks: RaceFront（閲覧専用 React/Vite フロント）

**Input**: Design documents from `specs/015-racefront-spa/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ui_components.md, contracts/api_consumption.md, quickstart.md

**Tests**: 含む（憲法 V 誤読防止/II read-only/VI 契約同期は必須。Vitest + RTL + MSW。**疑似ラベル不変条件**が最重要）

**Organization**: User story 単位（P1 US1 一覧 → P1 US2 予測 → P1 US3 オッズ → P1 US4 推奨 → P2 US5 型同期）。MVP=US1。

## パス規約

新規 `front/` パッケージ（React + Vite + TS）。src=`front/src/`、tests=`front/src/tests/`。全パスはルート相対。**閲覧専用**:
書込 UI なし。API（014）は無改変、相対 `/api/v1/*` + Vite proxy。pnpm。

---

## Phase 1: Setup（Vite/React/TS 雛形・依存）

- [X] T001 `front/package.json` を作成: deps `react`/`react-dom`/`react-router-dom`/`@tanstack/react-query`/`openapi-fetch`、devDeps `vite`/`@vitejs/plugin-react`/`typescript`/**`openapi-typescript`（厳密ピン）**/`vitest`/`@testing-library/react`/`@testing-library/jest-dom`/**`@testing-library/user-event`**/`jsdom`/`msw`/`eslint`。**`packageManager` フィールド + lockfile をコミット**。scripts: `dev`/`build`/`test`/`typecheck`/`lint`/`gen:types`/`check:openapi`
- [X] T002 [P] `front/vite.config.ts`（plugin-react、`server.proxy={'/api':{target: process.env.VITE_API_BASE||'http://localhost:8000', changeOrigin:true}}`、vitest: jsdom + `setupFiles`）・`front/tsconfig.json`（strict）・`front/index.html` を作成する。`front/src/tests/setup.ts` に **MSW v2 ライフサイクル**（`setupServer(...handlers)`、`beforeAll(listen)`/`afterEach(resetHandlers)`/`afterAll(close)`）+ jest-dom を実装。`front/src/tests/utils.tsx` に **`renderWithProviders`**（テスト毎に新規 `QueryClient`（retry:false）+ `QueryClientProvider` + `MemoryRouter`）を実装する(research.md R7)
- [X] T003 `cd front && pnpm install` を実行し、`pnpm run typecheck` と空の `pnpm run test` が起動することを確認する

**Checkpoint**: ビルド/テスト基盤が起動。

---

## Phase 2: Foundational（型・クライアント・状態・バッジ・null 安全 — 全 US 前提）

**⚠️ CRITICAL: 疑似値の唯一描画経路（PseudoBadge/SourceBadge）と判別ユニオン・null 安全表示をここで確定。全 US が依存。**

- [X] T004 `front/openapi.json`（014 の OpenAPI スナップショット）を配置し、`front/scripts/gen-types.sh`（起動中 API の `/openapi.json`→`front/openapi.json` 更新 + `openapi-typescript` で `src/api/schema.d.ts` 再生成）を作成。`src/api/schema.d.ts` を生成・コミットする(research.md R2)
- [X] T005 [P] `front/src/api/client.ts` に `openapi-fetch` クライアント（baseUrl 相対、パス `/api/v1/...`、型 = schema.d.ts）と、`parseApiError(body)`（**`{status,code,detail}` ErrorBody と FastAPI 既定の `{detail:[...]}` バリデーション形の両方**を受理し統一 ErrorInfo を返す防御的パーサ）+ Result 型ラッパを実装する(research.md R3/R4)
- [X] T006 [P] `front/src/lib/format.ts` に `formatNum(x: number|null, kind: "prob"|"odds"|"roi")`（null→`--`/`未提供`、prob=%、odds=×、roi=符号付き）と日本語ラベル定数を実装する(research.md R5)
- [X] T007 `front/src/components/PseudoBadge.tsx` / `SourceBadge.tsx` + `src/components/PseudoValue.tsx` を実装する: 疑似値の**唯一の描画経路**。`<PseudoValue badge="推定（疑似）"|"疑似ROI"|"二重疑似" value={…}>` が `data-pseudo` 属性付きラッパに **値 + 必須 `<PseudoBadge>`** を描画する（疑似値はこのコンポーネント経由でのみ表示）。`<SourceBadge source>`（real/estimated）。判別ユニオン型を `src/api/labels.ts` に定義し、疑似値表示にラベルを型レベルで要求。これにより `assertPseudoLabelCoverage`（T010）が `data-pseudo` 要素のバッジ有無を機械検査できる(research.md R1)
- [X] T008 [P] `front/src/components/StateView.tsx`（loading/empty/error の 3 状態、error は `{status,code,detail}` 表示）を実装する(research.md R4)

### Foundational テスト

- [X] T009 [P] `front/src/tests/format.test.ts` を作成: `formatNum` が null→`--`/`未提供`、prob/odds/roi 整形、NaN を出さないことを検証（SC-006）
- [X] T010 [P] `front/src/tests/pseudo_invariant.ts`（共有ヘルパ）+ `pseudo_invariant.test.tsx` を作成する（**最重要・不変条件**）: `assertPseudoLabelCoverage(container)` を実装 — 描画ツリー中の**各疑似値**（estimated odds / pseudo_odds / pseudo_roi / double_pseudo を含む行/セクション = `data-pseudo` 属性で標識）について、その**同じ行/セクション内に必須バッジ（推定（疑似）/疑似ROI/二重疑似）が存在する**ことを検査（単なる存在チェックでなく**カバレッジ**）。`data-pseudo` を持つ要素が PseudoBadge を欠く場合は失敗。US3/US4 のパネルテストはこのヘルパを呼ぶ（SC-005）

**Checkpoint**: 型・クライアント・状態・バッジ・null 安全が単体検証済み。疑似ラベル経路が確立。

---

## Phase 3: User Story 1 - レース一覧（絞込・ページング・遷移） (Priority: P1) 🎯 MVP

**Goal**: 一覧で日付/開催絞込・ページング・詳細遷移、loading/空/エラー 3 状態。

**Independent Test**: MSW で /races をモックし、フィルタ・ページング操作、空(ゼロ件)・エラー(型付き)・loading を別表示、行→詳細リンク。

### 実装

- [X] T011 [US1] `front/src/main.tsx`/`App.tsx`/`router.tsx`（react-router: `/`=一覧, `/races/:raceId`=詳細）と react-query Provider を実装する
- [X] T012 [US1] `front/src/components/RaceTable.tsx`・`Pagination.tsx`（page/page_size/total/has_next、次/前）を実装する
- [X] T013 [US1] `front/src/pages/RaceListPage.tsx` を実装する: 日付/開催フィルタ、`GET /api/v1/races` を react-query で取得、`StateView` で loading/empty/error、`RaceTable`+`Pagination`、行→ `/races/:raceId`（FR-002/FR-007）

### US1 テスト

- [X] T014 [P] [US1] `front/src/tests/RaceListPage.test.tsx` を作成: MSW で full（ページング・total/has_next）/空（ゼロ件明示）/エラー（型付き）/loading を別表示、フィルタ反映、詳細リンクを検証（SC-001）

**Checkpoint**: US1 単独で動作・テスト緑（MVP）。

---

## Phase 4: User Story 2 - レース詳細・予測 + 監査 (Priority: P1)

**Goal**: 詳細で出走表 + 1着率/2着以内率/3着以内率 + 監査（run/model/logic/computed_at）、予測無しは空。

**Independent Test**: MSW で /races/{id} と /predictions をモックし、予測 + 監査表示、null 確率の安全表示、run=null の空状態。

### 実装

- [X] T015 [US2] `front/src/components/PredictionTable.tsx`（per-horse 1着率/2着以内率/3着以内率、`formatNum(_, "prob")`）・`RunAudit.tsx`（API フィールド名 **`prediction_run_id`/`model_version`/`logic_version`/`computed_at`** を明示。表示コピーは「実行ID」等可）を実装する(FR-003/FR-008)
- [X] T016 [US2] `front/src/pages/RaceDetailPage.tsx`（骨子）を実装する: `GET /races/{id}` + `GET /predictions`、`StateView`、`PredictionTable`+`RunAudit`、予測無し（run=null）は空状態（FR-003/FR-007）

### US2 テスト

- [X] T017 [P] [US2] `front/src/tests/RaceDetailPredictions.test.tsx` を作成: 予測 + 監査情報が画面明示、null 確率が `--`/`未提供`、run=null で予測セクション空、race 404 でエラー状態を検証（SC-002/SC-006）

**Checkpoint**: US2 単独で動作・テスト緑。

---

## Phase 5: User Story 3 - オッズ（実/推定 区別） (Priority: P1)

**Goal**: win(real)/estimated(疑似+as_of)/real_exotic(coverage) を別セクション・判別バッジで表示、混在なし。

**Independent Test**: MSW で /odds をモックし、3 種を別セクションで区別、推定に疑似バッジ+as_of、**推定値がラベル無しで出ない**、欠損は空。

### 実装

- [X] T018 [US3] `front/src/components/OddsPanel.tsx` を実装する: `OddsResponse` の win/estimated/real_exotic を**別セクション**で、estimated は `PseudoValue badge="推定（疑似）"`+as_of、real は `SourceBadge=real`+updated_at、real_exotic は coverage_scope。**exotic 推定オッズは `GET /odds?bet_type=<type>&top=K` 指定時のみ返る**ため、券種セレクタ（既定 exacta）を持ち選択券種で再フェッチする。値は `formatNum(_, "odds")`。実/推定を同一行に混ぜない（FR-004/FR-006）
- [X] T019 [US3] `front/src/pages/RaceDetailPage.tsx` に `GET /api/v1/races/{id}/odds?bet_type=<selected>&top=K` を統合し `OddsPanel`（券種セレクタ連動）を表示する（欠損=空状態、エラー=エラー状態）（FR-004/FR-007）

### US3 テスト

- [X] T020 [P] [US3] `front/src/tests/OddsPanel.test.tsx` を作成（MSW）: full（win/estimated/real_exotic 別セクション、推定に疑似バッジ+as_of、券種セレクタで `/odds?bet_type=` 再フェッチ）/ **空（200 typed-empty）/ エラー（型付き）** の 3 分岐、混在なし、**`assertPseudoLabelCoverage` で疑似値がラベル無しで描画されないこと（不変条件）**、null odds 安全表示を検証（SC-003/SC-005）

**Checkpoint**: US3 単独で動作・テスト緑。

---

## Phase 6: User Story 4 - 推奨（疑似/二重疑似 ラベル） (Priority: P1)

**Goal**: 推奨を pseudo_roi=疑似ROI、double_pseudo=二重疑似 バッジ + 監査で表示。

**Independent Test**: MSW で /recommendations をモックし、疑似ROI/二重疑似バッジ、is_estimated_odds で実/推定区別、**疑似値がラベル無しで出ない**、空。

### 実装

- [X] T021 [US4] `front/src/components/RecommendationPanel.tsx` を実装する: 各行 bet_type/selection、`pseudo_roi`→`PseudoBadge=疑似ROI`、`double_pseudo=true`→`PseudoBadge=二重疑似`、監査（logic_version/computed_at/prediction_run_id）、is_estimated_odds で実/推定使用区別。値は `formatNum`（FR-005/FR-006/FR-008）
- [X] T022 [US4] `front/src/pages/RaceDetailPage.tsx` に `GET /recommendations` を統合し `RecommendationPanel` を表示する（無し=空状態）（FR-005/FR-007）

### US4 テスト

- [X] T023 [P] [US4] `front/src/tests/RecommendationPanel.test.tsx` を作成（MSW）: full（pseudo_roi=疑似ROI、double_pseudo=二重疑似 バッジ、is_estimated_odds の実/推定区別）/ **空（items=[]）/ エラー（型付き）** の 3 分岐、**`assertPseudoLabelCoverage` で疑似値がラベル無しで描画されないこと（不変条件）**、null 数値安全表示を検証（SC-004/SC-005）

**Checkpoint**: 全 P1 完了。詳細ページが予測/オッズ/推奨を誤読防止ラベル付きで表示。

---

## Phase 7: User Story 5 - OpenAPI 型同期 / ドリフト検知 (Priority: P2)

**Goal**: 生成型がコミットスナップショットと一致、ドリフト検知。

**Independent Test**: `check:openapi` が、コミット済み `front/openapi.json` から生成した型 == コミット済み `src/api/schema.d.ts` を検査。

### 実装

- [X] T024 [US5] `front/package.json` に `check:openapi` を実装する: **厳密ピンした `openapi-typescript`** で `front/openapi.json`（キー整列の決定論出力）から型を一時生成し `src/api/schema.d.ts` と diff（差分で fail）。ツールのバージョンも assert（フォーマッタ/版差でのフレーク回避）（FR-009/research.md R2）

### US5 テスト

- [X] T025 [P] [US5] `front/src/tests/openapi_drift.test.ts` を作成: コミット済み openapi.json から生成した型がコミット済み型と一致（ドリフトなし）、不一致なら失敗することを検証（SC-006）

**Checkpoint**: 型同期が CI 検査可能。

---

## Phase 8: Polish & Cross-Cutting

- [X] T026 [P] `front/src/tests/scope_guard.test.ts`（静的走査）で **書込/将来面の不在**を検証する（FR-012/SC-007/憲法 II）: `front/src` を AST/文字列走査し (a) `<form ... onSubmit>` / `useMutation` / `method: 'POST'|'PUT'|'DELETE'|'PATCH'` が無い、(b) auth/Kelly/Playwright/deploy 系の import・依存が無いことを assert
- [X] T027 [P] eslint/typecheck を解消: `cd front && pnpm run lint && pnpm run typecheck`
- [X] T028 全テスト緑を確認: `cd front && pnpm run test`
- [X] T029 [P] [quickstart 検証] `specs/015-racefront-spa/quickstart.md` を実行: API 起動 → `gen:types` → `pnpm dev` で一覧/詳細を実 DB データで目視（実/推定/疑似/二重疑似ラベル・3 状態・null 安全）（SC-001〜SC-007）

---

## Dependencies & Execution Order

- **Phase 1 (Setup)**: 先頭。T001→T002[P]→T003。
- **Phase 2 (Foundational)**: Setup 後。T004→T005[P]/T006[P]/T007/T008[P]、テスト T009/T010[P]。**全 US をブロック**（型/クライアント/バッジ/状態/null）。
- **Phase 3 (US1, MVP)**: Foundational 後。T011→T012→T013、テスト T014[P]。
- **Phase 4 (US2)**: Foundational + US1（ルーティング/詳細ページ骨子）後。T015→T016、テスト T017[P]。
- **Phase 5 (US3)**: US2（詳細ページ）後。T018→T019、テスト T020[P]。
- **Phase 6 (US4)**: US2（詳細ページ）後。T021→T022、テスト T023[P]。US3/US4 は詳細ページに統合（順次）。
- **Phase 7 (US5)**: Foundational（型生成）後いつでも。T024→T025[P]。
- **Phase 8 (Polish)**: 全実装後。

### User Story 独立性

- US1 は一覧で独立。US2/US3/US4 は共通の RaceDetailPage に各パネルを足す（詳細骨子=US2、オッズ=US3、推奨=US4）。US5 は型同期で独立。

## Parallel 実行例

- Foundational: T005/T006/T008 を並走、テスト T009/T010 を並走。
- 各 US テストは [P]。Polish: T026/T027/T029 を並走。

## 実装戦略

1. **MVP first**: Phase 1→2→3（US1）で「一覧 + 3 状態」を最短達成。
2. **詳細の段階追加**: US2（予測+監査）→ US3（オッズ実/推定）→ US4（推奨 疑似/二重疑似）を RaceDetailPage に積む。
3. **誤読防止の機構化**: 疑似値は PseudoBadge 経由のみ + 不変条件テスト（US3/US4 で対象を増やす）。
4. **契約同期**: US5 でドリフト検知。Polish で書込 UI 不在・lint・全テスト・quickstart 目視。
5. 各 Checkpoint で独立テスト緑。憲法 II（read-only・書込 UI 無し）/ V（疑似ラベル強制・監査可視化）/ VI（型同期・API 無改変）を全タスクで維持。
