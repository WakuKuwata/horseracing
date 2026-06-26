# Research: RaceFront（閲覧専用フロント）

憲法 V（誤読防止）/ VI（契約先行）/ II（read-only）と codex second opinion（plan.md の表）を踏まえた設計判断。

## R1. 疑似ラベル強制（CRITICAL）— 型 + 単一描画経路 + 不変条件テスト

- **Decision**: 疑似値（推定オッズ・`pseudo_odds`・`pseudo_roi`・`double_pseudo`・推定使用フラグ）は**必ずラベル/バッジ付きで描画**する。
  - **判別ユニオン型**: API の `odds_source`("real"|"estimated")・`is_estimated`・`double_pseudo` を TS の discriminated union として扱い、
    推定/疑似分岐でラベル付与をコンパイラが要求する形にする。
  - **単一描画経路**: 疑似値は共通 `<PseudoBadge label="疑似ROI"|"二重疑似"|"推定（疑似）">` と `<SourceBadge source="real"|"estimated">`
    を**通してのみ**表示する（生数値を直接描画しない）。
  - **不変条件テスト**: 各画面/コンポーネントを MSW fixture でレンダリングし、**疑似値の近傍に必ず疑似/二重疑似ラベルが存在**する
    （= ラベル無しの疑似値ゼロ）ことを名前付きテストで assert。
- **Rationale**: codex CRITICAL — プロ文だけでは V を担保できずリファクタでラベルが落ちる。型 + 単一経路 + テストで機構化する。
- **Alternatives**: ラベルを各所で手書き → 落ちる。却下。

## R2. OpenAPI 型同期（HIGH）— コミットスナップショット + ドリフト検知

- **Decision**: 014 の `/openapi.json` を **`front/openapi.json` にコミット**し、`openapi-typescript` で `src/api/schema.d.ts` を**生成して
  コミット**。`scripts/gen-types.sh` が**起動中 API** から `openapi.json` を再取得し型を再生成。**テスト/CI で「コミット済みスナップショット
  から生成した型 == コミット済み型」を検査**してドリフトを検知。実行時は API 起動不要（スナップショット利用）。
- **Rationale**: codex HIGH — `/openapi.json` は API 起動が要る（chicken-and-egg）。スナップショットをコミットすれば生成・テストが API
  非依存になり、契約乖離も検知できる（VI）。
- **ドリフト検査の決定論（codex MED）**: `openapi-typescript` を**厳密ピン**（+ `packageManager`/lockfile コミット）し、ツール版を assert。
  `front/openapi.json` はキー整列の決定論出力にし、生成 vs コミットの diff がフォーマッタ/版差でフレークしないようにする。
- **Alternatives**: ビルド時に毎回 API から生成 → API 起動必須・CI 不安定。版未ピン → diff フレーク。却下。

## R3. CORS / dev proxy（MED）— API 無改変

- **Decision**: フロントは **相対 `/api/v1/*`** を呼ぶ。Vite `server.proxy = { '/api': 'http://localhost:8000' }` で開発時に API へ転送
  （same-origin 化で CORS 不要）。**014 API は CORS ミドルウェアを追加しない（無改変）**。本番は静的ビルド + リバースプロキシ/CORS で
  将来対応。
- **Rationale**: codex MED — Vite proxy で十分。014 を触らない（read-only 契約を凍結）。
- **Alternatives**: 014 に CORS 追加 → API 変更（契約面の余計な変更）。却下。

## R4. 3 状態区別 + ページング（HIGH/MED）

- **Decision**: フェッチは @tanstack/react-query で **loading / empty / error** を別状態に。**空は 200 typed-empty**（`run=null`/
  `horses=[]`/`items=[]`/オッズ配列空）として明示表示し、**エラーは型付き本体 `{status,code,detail}`** を防御的にパース（生成エラー型が
  不完全でも壊れない）。**ページングは `/races`（Page: page/page_size/total/has_next）のみ**。詳細の予測/オッズ/推奨はフラット配列で
  ページングしない。
- **Rationale**: codex HIGH/MED — 空とエラーと読込を混同すると空白固定/誤表示。サブリソースに無いページングを仮定しない。
- **Alternatives**: 空=エラー扱い → 誤誘導。却下。

## R5. null 数値の安全表示（HIGH）

- **Decision**: `win/top2/top3`・各オッズ・`pseudo_odds/pseudo_roi/market_odds_used/estimated_market_odds_used` は `number | null`。
  `lib/format.ts` の `formatNum(x, opts)`（null → `--`/`未提供`、確率は %、オッズは倍率）を**一元化**し、整形前に必ず null ガード。
- **Rationale**: codex HIGH — 直接整形で NaN/クラッシュ。
- **Alternatives**: 各所で `toFixed` 直書き → null で例外。却下。

## R6. 監査可視化（HIGH/V）

- **Decision**: 予測の **prediction_run_id/model_version/logic_version/computed_at** を `<RunAudit>` で画面に明示。推定オッズの
  **as_of**、実オッズの **updated_at**、実 exotic の **coverage_scope** を各パネルに表示（ツールチップのみに埋めない）。
- **Rationale**: 憲法 V。再現性の系譜を利用者が確認できる。

## R7. テスト方針（HIGH）

- **Decision**: **Vitest + RTL + MSW(v2) + user-event**。`setup.ts` で MSW ライフサイクル（`setupServer`/`beforeAll(listen)`/
  `afterEach(resetHandlers)`/`afterAll(close)`）、`utils.tsx` の `renderWithProviders`（テスト毎に新規 `QueryClient(retry:false)` +
  `QueryClientProvider` + `MemoryRouter`）でテスト分離。fixture で**各エンドポイント**の **full / 空(200 typed-empty) / エラー
  (404/409/422)** をモックし、(a) 3 状態の別表示、(b) null 数値の安全表示、(c) 一覧のページング操作（user-event）、(d) **疑似ラベル
  不変条件** を `assertPseudoLabelCoverage`（`data-pseudo` 要素ごとに必須バッジ存在を検査＝スポットでなくカバレッジ）で検証。
  さらに **openapi.json スナップショット一致**テスト、**書込/将来面の不在**静的走査。
- **Rationale**: codex CRITICAL/HIGH — 不変条件はスポットチェックでなく `data-pseudo` カバレッジで機構的に担保。MSW/react-query の
  分離を明示し、エラーは ErrorBody と FastAPI `{detail:[...]}` の両形を `parseApiError` で受理。
- **Alternatives**: E2E(Playwright) → MVP では重い、将来。スポットチェックのみ → ラベル落ちを見逃す。却下。

## まとめ（設計判断 → 要件）

| 研究項目 | 対応 FR / SC |
|---|---|
| R1 疑似ラベル強制 | FR-004 / FR-005 / FR-006 / SC-003 / SC-004 / SC-005 |
| R2 OpenAPI 型同期 | FR-009 / SC-006 |
| R3 proxy / API 無改変 | FR-010 / SC-007 |
| R4 3 状態・ページング | FR-002 / FR-007 / SC-001 |
| R5 null 安全表示 | FR-008 / SC-006 |
| R6 監査可視化 | FR-003 / SC-002 |
| R7 テスト | FR-011 / SC-005 |
