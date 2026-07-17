# Quickstart: Counterfactual Return API Terminology

**Feature**: 075 | 目的: backtest/shadow-log の誤称 realized→counterfactual_snapshot 改名を、値を変えず api/front/admin/OpenAPI 原子同期で実施。詳細は [data-model.md](data-model.md) / [contracts/api-fields.md](contracts/api-fields.md)。

## 1. 数値パリティ(FR-007・SC-002)
改名前の応答値(既知 fixture / DB 固定レース)と改名後の対応フィールド値が **完全一致**することを回帰で確認(命名のみ・値不変)。

## 2. 改名の網羅(US1/US2・SC-001/SC-004)
- backtest/shadow-log/favorite 経路の応答に `realized_return`/`realized_roi` が **0 件**。
- win backtest = `counterfactual_snapshot_gross_return`/`net_return` + `valuation_basis="frozen_snapshot_odds"`。
- shadow-log = `counterfactual_snapshot_recovery_rate`(分母は既存 `n_settled`・`by_month[].counterfactual_snapshot_recovery`)。
- favorite = `current_odds_gross_return`/`net_return` + `valuation_basis="current_odds"`。

## 3. empirical 保護(US3・SC-005)
- calibration `realized_rate`/`realized_ci_low`/`realized_ci_high` は **同名・同値**(改名しない)。

## 4. 原子同期・drift(SC-003/SC-007)
- api OpenAPI 再生成 → `front/openapi.json`・`admin/openapi.json` byte 一致 → `schema.d.ts` 再生成 → **drift-check 緑**。
- api / front / admin の全テスト緑。

## 5. 不変(SC-006)
- DB migration 追加 0・read-only 境界維持(全 GET・書き込み経路 0)。

## 受け入れ判定
| 検証 | SC |
|---|---|
| realized_return/roi が backtest 経路に 0 件 | SC-001 |
| 改名前後で数値 100% 一致 | SC-002 |
| OpenAPI drift-check 緑 | SC-003 |
| favorite=current_odds provenance で別名 | SC-004 |
| calibration realized_rate 同名同値 | SC-005 |
| migration 0・read-only | SC-006 |
| api/front/admin 全テスト緑 | SC-007 |
