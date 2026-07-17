# API Contract: Counterfactual Return Field Migration

**Feature**: 075 | 破壊的変更(後方互換フィールドなし)・値不変・DB/endpoint 不変(フィールド改名のみ)。

## 影響エンドポイント(read-only GET・パス不変)
- `GET /api/v1/recommendations`(win backtest 各行 + favorite_baseline)
- `GET /api/v1/shadow-log`(shadow-log サマリ)
- `GET /api/v1/models/{mv}/calibration`(**改名なし**=realized_rate 保持の回帰対象)

## フィールド契約(旧→新・値不変)
### win backtest 行
- `realized_return` → `counterfactual_snapshot_gross_return`
- `realized_roi` → `counterfactual_snapshot_net_return`
- + `valuation_basis: "frozen_snapshot_odds"`

### shadow-log サマリ
- `recovery_rate` → `counterfactual_snapshot_recovery_rate`
- `by_month[].recovery` → `by_month[].counterfactual_snapshot_recovery`
- + `valuation_basis: "frozen_snapshot_odds"`(分母は既存 `n_settled`・`n_scored` は追加しない)
- 保持: `n_settled`/`n_hit`/`hit_rate`

### favorite_baseline
- `realized_return` → `current_odds_gross_return`
- `realized_roi` → `current_odds_net_return`
- + `valuation_basis: "current_odds"`

### calibration bin(**改名しない**)
- `realized_rate` / `realized_ci_low` / `realized_ci_high` = 不変

## OpenAPI/型 同期(原子)
1. api の pydantic 応答モデル改名 → OpenAPI 自動再生成
2. `front/openapi.json`・`admin/openapi.json` を api 生成物と byte 一致に更新
3. openapi-typescript で `schema.d.ts` 再生成
4. drift-check(committed snapshot == 生成 == 型)緑

## 破壊しない契約
- DB スキーマ・migration・endpoint パス不変。
- read-only(全 GET・api は betting/serving 非 import)。
- 数値不変(命名のみ)。
