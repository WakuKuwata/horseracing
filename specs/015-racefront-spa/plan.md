# Implementation Plan: RaceFront（閲覧専用 React/Vite フロント）

**Branch**: `015-racefront-spa` | **Date**: 2026-06-26 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/015-racefront-spa/spec.md`

## Summary

新規 `front/` パッケージ（React + Vite + TypeScript）で Feature 014 の read-only API（`/api/v1`, OpenAPI）を消費する**閲覧専用**
フロント。レース一覧・詳細（予測/オッズ/推奨）を表示し、**実/推定/疑似/二重疑似を UI で明確区別**（誤読防止、憲法 V）。型は OpenAPI
から生成し**コミットスナップショット**で同期（ドリフト検知、VI）。**閲覧専用で API（014）は変更しない**（CORS 無しのまま、相対 +
Vite proxy）。

codex の CRITICAL（疑似ラベル強制）を**判別ユニオン型 + 共通バッジ + 不変条件テスト**で機構解消する（下表）。

## Technical Context

**Language/Version**: TypeScript 5.x / Node 20+（実機 Node 25 / pnpm 10）。React 18 + Vite 5。

**Primary Dependencies**: react / react-dom / react-router-dom（ルーティング）、@tanstack/react-query（フェッチ/状態: loading/empty/
error）、openapi-typescript（OpenAPI → 型生成、devDep）、openapi-fetch（型付き軽量クライアント）。test: vitest /
@testing-library/react / @testing-library/jest-dom / jsdom / msw（API モック）。**バックエンド依存なし**（HTTP のみ）。

**Storage**: なし（フロントは状態を持たない、API 読み取りのみ）。

**Testing**: **Vitest + React Testing Library + MSW**。各エンドポイントの full/空(200 typed-empty)/エラー(404/409/422) 分岐、null
数値、ページング操作、**疑似ラベル不変条件**（疑似値がラベル無しで描画されない）を検証。

**Target Platform**: モダンブラウザ。開発は Vite dev サーバ（proxy `/api` → ローカル API）。本番静的ビルドは将来。

**Project Type**: 新規 `front/` パッケージ（リポジトリ初の JS/Node・SPA）。

**Performance Goals**: SPA。一覧はページング、詳細は単一レースのみ取得（軽量）。

**Constraints**: 閲覧専用（書込 UI 無し）。API 非変更（CORS 無し、相対 + dev proxy）。OpenAPI 生成型をコミット + ドリフト検知。疑似/実
区別を型・コンポーネント・テストで強制。null 安全表示。3 状態区別。監査可視化。

**Scale/Scope**: 2 画面（一覧・詳細）。MVP。認証/書込/Kelly/E2E/本番デプロイは将来。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Constitution v1.0.0 に基づくゲート:

- [x] **I. データ契約**: 014 の契約（race_id 12 桁等）をそのまま消費。フロントは ID を生成・改変しない。表示ラベルは
  「1着率/2着以内率/3着以内率」（内部 win/top2/top3 を日本語表示）。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: **閲覧専用**で書込 UI・賭け実行・生成を持たない。応答値をモデル/特徴量に還流しない
  （フロントは表示のみ、学習系に何も渡さない）。**PASS**
- [x] **III. 評価先行**: 本フィーチャーはモデル/特徴量を変更しない（表示のみ）。配信される疑似評価値（pseudo_roi 等）には**疑似/二重
  疑似ラベルを必ず付す**。該当外だが配信側の明示は満たす。**PASS（対象外・明示）**
- [x] **IV. 確率整合性**: 確率は 014/009 が保証。フロントは加工せず表示（null は安全表示）。**PASS（継承）**
- [x] **V. 再現性と監査**: 予測の **run_id/model_version/logic_version/computed_at**、推定オッズの **as_of**、推奨の疑似/二重疑似を
  **画面に明示**（ツールチップのみに埋めない）。疑似を実と誤読させない。**PASS（本原則の UI 実装）**
- [x] **VI. feature 分割規律**: **UI の前に API/DB 契約を 014 で確定済み**。本フィーチャーは契約消費のみで、型は OpenAPI 生成 +
  コミットスナップショットでドリフト検知。API は変更しない。**PASS**
- [x] **品質ゲート**: `codex:codex-rescue` second opinion を取得・記録（下表）。CRITICAL/HIGH を機構解消。**PASS**

### Second Opinion 記録（codex:codex-rescue — spec/plan 段階）

| 重大度 | codex 助言 | 本 plan の対応 |
|---|---|---|
| **CRITICAL** | ラベル無しの疑似値表示は V 違反。バッジを判別フィールドに紐づけ、RTL で「ラベル無し疑似値ゼロ」を assert | **判別ユニオン型**（is_estimated/double_pseudo）+ 共通 `<PseudoBadge>` を経由しないと疑似値を描画できない設計、**不変条件テスト**（R1） |
| HIGH | OpenAPI 型生成の CI 化が無い → ドリフト | **front/openapi.json スナップショットをコミット** + 生成型コミット、再生成スクリプト、テストで生成結果 == スナップショットを検査（R2） |
| MED | Vite proxy で十分・014 は CORS 無しのまま | 相対 `/api/v1/*` + Vite `proxy`、FastAPI 無改変、本番リバースプロキシは将来（R3） |
| HIGH | 200 typed-empty / 型付きエラーを 3 状態で区別、エラー本体を防御的にパース | loading/empty/error の 3 状態、`{status,code,detail}` を防御的に解釈（R4） |
| HIGH | null 数値で NaN/クラッシュ | `formatNum(x: number\|null)` を一元化、`--`/`未提供`（R5） |
| MED | ページングは /races のみ、詳細はフラット配列 | 一覧のみ page/has_next UI、詳細はページングしない（R4） |
| HIGH | 監査（run/model/logic/computed_at/as_of）を埋もれさせない | 画面に明示表示（R6/V） |
| HIGH | RTL + モックで全分岐 + 疑似ラベル不変条件 | MSW fixture で full/空/エラー/null/ページング + 名前付き不変条件テスト（R1/R7） |
| HIGH | II/V/VI の UI 固有リスク（書込混入・ラベル落ち・型乖離） | 書込 UI 無し、判別ユニオンでコンパイラ強制、スナップショット検査（R1/R2） |

最重要 TOP3: ①疑似ラベルを型+コンポーネント+テストで強制（誤読防止）②OpenAPI 型のコミットスナップショット + ドリフト検知
③3 状態区別 + null 安全表示。

## Project Structure

### Documentation (this feature)

```text
specs/015-racefront-spa/
├── plan.md
├── research.md          # R1 疑似ラベル強制 / R2 OpenAPI 型同期 / R3 proxy / R4 状態・ページング / R5 null / R6 監査 / R7 テスト
├── data-model.md        # ビュー/コンポーネント・判別ユニオン型・状態・不変条件
├── quickstart.md        # API 起動 → 型生成 → dev 起動 → 画面/テスト検証
├── contracts/
│   ├── ui_components.md     # 画面/コンポーネントの入出力・ラベル規約
│   └── api_consumption.md   # 消費する 014 エンドポイントと生成型・proxy の契約
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
front/                                         # 新規 SPA パッケージ
├── package.json                               # react/vite/ts + react-query/router + openapi-typescript/openapi-fetch + vitest/RTL/msw
├── vite.config.ts                             # dev proxy /api -> http://localhost:8000、vitest 設定
├── tsconfig.json
├── openapi.json                               # 014 のコミット済みスナップショット（契約）
├── scripts/gen-types.sh                       # 起動中 API の /openapi.json から openapi.json + 型再生成
├── index.html
└── src/
    ├── main.tsx / App.tsx / router.tsx        # ルート（/ 一覧、/races/:raceId 詳細）
    ├── api/
    │   ├── schema.d.ts                        # openapi-typescript 生成型（コミット）
    │   └── client.ts                          # openapi-fetch クライアント（相対 /api/v1）
    ├── lib/format.ts                          # formatNum(number|null) 等 null 安全表示
    ├── components/
    │   ├── PseudoBadge.tsx / SourceBadge.tsx  # 疑似/二重疑似/実・推定 バッジ（唯一の疑似値描画経路）
    │   ├── StateView.tsx                      # Loading / Empty / Error の 3 状態
    │   ├── RunAudit.tsx                       # run_id/model/logic/computed_at
    │   ├── OddsPanel.tsx / RecommendationPanel.tsx / PredictionTable.tsx
    │   └── RaceTable.tsx / Pagination.tsx
    ├── pages/RaceListPage.tsx / RaceDetailPage.tsx
    └── tests/                                 # vitest + RTL + MSW（full/空/エラー/null/ページング/疑似ラベル不変条件）
```

**Structure Decision**: 新規 `front/` は HTTP で 014 のみに依存（バックエンド非依存）。**疑似値は `<PseudoBadge>`/`<SourceBadge>` を
唯一の描画経路**にし、判別ユニオン型でラベル付与をコンパイラに強制、不変条件テストで「ラベル無し疑似値ゼロ」を担保。OpenAPI を
`front/openapi.json` にコミット + 生成型をコミットし、スナップショット一致をテストでドリフト検知。dev は Vite proxy で API 無改変。

## Complexity Tracking

> Constitution Check 違反なし。新規 JS/Node 依存はフロント層の目的に内在。スキーマ変更なし・API 非変更。記入不要。
