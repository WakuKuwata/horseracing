# Contract: aggregation — shadow-log read-time summary (api, read-only)

## `api/backtest.py::shadow_log_summary(recs, *, finish_maps) -> ShadowLogSummary`（純関数・新規）

- 入力 = recommendations 行(+ 各レースの finish_map)。対象述語(全 AND=codex 指摘): `bet_type==win` ∧ **厳密 marker**(`;` split で `prospective=1` トークン) ∧ `is_estimated_odds is False` ∧ `market_odds_used>0` ∧ `estimated_market_odds_used is None` ∧ 有効な WIN dict selection ∧ settled。
- 各 rec の realized は既存 `win_realized(selection, market_odds_used, finish_map, n_winners)`（**凍結 market_odds_used** で評価）。**favorite_realized や race_horses.odds の現在値は一切読まない**(favorite_realized は現在オッズを読む=禁止)。
- ROI/的中の分母は `hit is not None`(**void は分母から除外し別計上**)。
- 集計: `n_settled`・`n_hit`・`hit_rate`・`recovery_rate`（Σrealized_return / n_settled_valued）・`n_pending`（marker あり未確定=集計外）・`n_void`・`by_month`・`first_at`/`last_at`・`weak_pretime`(post_time 未知=弱保証の別掲数)。**skip-rate は出さない**(行が残らず分母不能)。
- betting 非 import(049 と同じ純述語境界)。results はモデル特徴に戻さない(II)。

## クエリ(重要)

- recommendations を **run 跨ぎで直接クエリ**(prospective marker 条件で全 run から集める)。**active-run scoped の表示クエリ(select_prediction_run)は使わない**=codex 指摘(active/latest run に限定されてしまう)。

## API `GET /api/v1/shadow-log`（read-only 純追加）

- prospective 実績サマリを返す(全 path GET・OpenAPI 純追加・front snapshot/drift-check 同期)。
- 空(marker あり settled が0)= typed-empty(偽の集計を出さない・FR-006)。
- 疑似は含めない(win real 単勝オッズのみ)。

## テスト

- `test_shadow_log_filters_exact_prospective_settled_real_win_only`: backfill/exotic/estimated/無効 marker/未確定が混在しても prospective settled real-win のみ集計(SC-002)。
- `test_shadow_log_uses_frozen_market_odds_after_current_odds_change`: 記録後に race_horses.odds を closing へ更新しても集計がバイト不変(SC-001)。
- `test_shadow_log_voids_excluded_from_roi_denominator`: `hit=None`(void)は valued 分母から除外し n_void に計上。
- `test_shadow_log_includes_non_active_prediction_runs`: active-run scoped でなく run 跨ぎで prospective 行を集める。
- `test_shadow_log_empty_is_typed_empty`: prospective データ0で偽集計を出さず空を返す。
- read-only 境界(全 path GET・betting 非 import)・OpenAPI drift-check 緑。
