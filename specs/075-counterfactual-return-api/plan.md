# Implementation Plan: Counterfactual Return API Terminology

**Branch**: `075-counterfactual-return-api` | **Date**: 2026-07-16 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/075-counterfactual-return-api/spec.md`

## Summary

073 で予約された公開契約の破壊的変更(**命名のみ・値不変**)。backtest/shadow-log の `realized_return`/`realized_roi`/`recovery_rate` は凍結 `market_odds_used`(判断時 snapshot)由来=反実仮想値なので `counterfactual_snapshot_*` に改名し `valuation_basis="frozen_snapshot_odds"` を明示(`n_scored` は追加しない=`n_settled` が分母)。favorite baseline(current `race_horses.odds` 由来)は `current_odds` provenance に分離。calibration の `realized_rate`(実際の勝率=empirical)は改名しない。api/front/admin/OpenAPI を原子的に migration し drift-check 緑・数値パリティ回帰で値不変を担保。

**設計の核**: `api/backtest.py:win_realized()` は shadow-log(frozen)と favorite(current)が共有する **odds 非依存コア**。provenance は呼び出し側のオッズで決まる。→ **内部 dataclass は中立の `gross_return`/`net_return`、provenance ラベル(counterfactual_snapshot_* / current_odds_*)は API 応答モデル(schemas.py)層で付与**。

## Technical Context

**Language/Version**: Python 3.12(api)/ TypeScript(front・admin)

**Primary Dependencies**: FastAPI + pydantic(api)、React + Vite + openapi-typescript(front・admin)。新規依存なし。

**Storage**: PostgreSQL 16(**読み取りのみ・migration なし**)。DB スキーマ不変。

**Testing**: pytest(api)、Vitest + RTL + MSW(front・admin)、OpenAPI drift-check。

**Target Platform**: read-only serving API + SPA。

**Project Type**: web(api backend + front/admin frontends)。

**Performance Goals**: N/A(命名 migration)。

**Constraints**: **数値不変**(命名・provenance のみ)。read-only 境界維持。DB スキーマ/migration 不変。破壊的変更(後方互換フィールドなし)。api/front/admin/OpenAPI 原子同期。

**Scale/Scope**: 触るのは `api/schemas.py`・`api/backtest.py`・`api/routers/{recommendations,shadow_log}.py`(両 router・A1)、`front/`(型 `schema.d.ts`・`fixtures.ts`・**表示** `ShadowLogPanel.tsx`+`RecommendationPanel.tsx`[内 `WinBacktestSummary` inline・I3])、`admin/`(型 `schema.d.ts`・snapshot **のみ**=表示コンポーネント無し・I2)、`front/openapi.json`・`admin/openapi.json`。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] **I. データ契約**: raceId/ラベル契約不変。ID/ラベル定義に触れない。**PASS**。
- [x] **II. リーク防止**: 表示命名のみ。派生値をモデル特徴に還流しない(該当なし=read-only 表示)。**PASS**。
- [x] **III. 評価先行**: モデル/特徴量変更なし。評価契約に触れない。**N/A**。
- [x] **IV. 確率整合性**: 確率値・精算数値を変えない(FR-007)。**PASS**。
- [x] **V. 再現性・監査**: **counterfactual/pseudo を明示ラベル**にするのが本 feature の目的。`valuation_basis` で provenance(frozen_snapshot_odds / current_odds)を明示。empirical(realized_rate)と区別。**PASS(強化)**。
- [x] **VI. feature 分割規律**: **契約先行**=OpenAPI を api で生成 → front/admin committed snapshot + 生成 TS を原子同期 → drift-check 緑。DB スキーマ不変。read-only 境界維持(全 GET)。**PASS**。
- [x] **品質ゲート**: API 契約変更=codex second opinion 対象。ただし本セッションで codex は repo skill に 2 回 derail(unavailable)。純命名 migration=低リスクのためセルフレビュー checklist(research D4)で代替。

### codex second opinion 記録
本セッションで codex に 073/074 の設計レビューを投げたが、いずれも repo の AGENTS.md/speckit skill に derail し結論を出さず(**codex unavailable**)。075 は数値・ロジック変更なしの純命名 migration(アルゴリズムリスクなし)であり、命名自体は 073 proposal + 074 codex レビューで既に確定済み。→ 実装前セルフレビュー(research D4)で代替。差分: なし(命名は確定事項)。

## Project Structure

### Documentation (this feature)

```text
specs/075-counterfactual-return-api/
├── plan.md · research.md · data-model.md · quickstart.md
├── contracts/api-fields.md   # 改名前後のフィールド対応表(契約)
└── tasks.md                  # /speckit-tasks で生成
```

### Source Code (repository root)

```text
api/src/horseracing_api/
├── backtest.py    # WinRealized/FavoriteRealized/ShadowLogSummary の内部 field を中立 gross_return/net_return に
├── schemas.py     # 応答モデルに provenance 命名(counterfactual_snapshot_* / current_odds_* / valuation_basis)
└── routers/recommendations.py (+ shadow-log router)  # dataclass→schema マッピング更新

front/src/                          # 表示改名はここだけ(admin に該当表示なし・analyze I2)
├── api/schema.d.ts                 # openapi-typescript で再生成
├── tests/fixtures.ts               # 新フィールド名
├── components/ShadowLogPanel.tsx (+ .test)      # shadow-log 表示
└── components/RecommendationPanel.tsx (+ .test) # WinBacktestSummary(inline function・I3)+ FavoriteBaseline

admin/src/                          # 型/snapshot 再生成のみ(表示コンポーネント無し)
├── api/schema.d.ts                 # 再生成
└── tests/fixtures.ts               # 該当あれば更新

front/openapi.json · admin/openapi.json   # api 生成 OpenAPI と byte 一致(drift-check)
```

**Structure Decision**: `api/`(命名の source of truth)→ OpenAPI 再生成 → **front は snapshot+型+fixtures+表示、admin は snapshot+型のみ**(admin は backtest/shadow-log/favorite を表示しない・analyze I2)を原子同期。DB・betting・serving・eval には触れない。

## Complexity Tracking

| 論点 | 判断 |
|---|---|
| win_realized が shared core(frozen/current 両用) | 内部 field は中立 gross/net、provenance は**応答モデル層**で付与(二重命名を避ける) |
| calibration realized_rate の巻き添え改名 | 回帰テストで同名・同値を assert(FR-006)。改名は backtest/shadow-log/favorite 経路に限定 |
| 破壊的変更(後方互換なし) | front/admin を原子同期(openapi 再生成 → 型再生成 → fixtures/components 更新 → drift-check)。数値パリティ回帰で値不変を担保 |
