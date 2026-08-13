# Contract: aggregation — shadow-log read-time summary (api, read-only)

## `api/backtest.py::shadow_log_summary(recs, *, finish_maps) -> ShadowLogSummary`（純関数・新規）

- 入力 = recommendations 行(+ 各レースの finish_map)。対象述語(全 AND=codex 指摘): `bet_type==win` ∧ **厳密 marker**(`;` split で `prospective=1` トークン) ∧ `is_estimated_odds is False` ∧ `market_odds_used>0` ∧ `estimated_market_odds_used is None` ∧ 有効な WIN dict selection ∧ settled。
- 各 rec の realized は既存 `win_realized(selection, market_odds_used, finish_map, n_winners)`（**凍結 market_odds_used** で評価）。**favorite_realized や race_horses.odds の現在値は一切読まない**(favorite_realized は現在オッズを読む=禁止)。
- ROI/的中の分母は `hit is not None`(**void は分母から除外し別計上**)。
- 集計: `n_settled`・`n_hit`・`hit_rate`・`recovery_rate`（Σrealized_return / n_settled_valued）・`n_pending`（marker あり未確定=集計外）・`n_void`・`by_month`・`first_at`/`last_at`・`weak_pretime`(post_time 未知=弱保証の別掲数)。**skip-rate は出さない**(行が残らず分母不能)。
- betting 非 import(049 と同じ純述語境界)。results はモデル特徴に戻さない(II)。

## 予測の入力 regime を混ぜない（091 T062・上の「closing を読まない」と同型の禁止）

shadow-log が守っているのは「**判断した時点で知り得た情報だけで採点する**」であって、オッズは
その一例にすぎない。091 以降、予測は **入力 regime** でも二分される:

- `logic_version` に `;wregime=serving` = 当日馬体重が公表される前に計算された予測
- `;wregime=full_info` = 当日馬体重ありで計算された予測（backfill はほぼ常にこちら）

**full-info 予測の成績を live 品質の代理として報告してはならない**。live の予測は実測で 97.4%
が体重なしで走るので、体重ありの backfill 成績を「このモデルの実力」として出すと、065 が
closing オッズで踏んだのと同型の楽観バイアスになる（[091 contracts/weight-mask.md §7]）。

- regime 別に**分けて**報告する。混ぜた単一の数字を出さない
- 混在した母集団しか作れない場合は、その旨を明示するか報告しない
- marker を持たないモデル（`prev_weight` を持たないもの）は regime 区別が存在しないので対象外

同じ禁止は backtest 表示（`api/backtest.py`）にも及ぶ。現状 `shadow_log_summary` は
`;prospective=1` marker で絞っており、prospective 行は定義上 live 実行なので **今は混入しない**
——ただしそれは述語の副作用であって設計上の保証ではないので、regime 別集計を足すときは
上の規約に従うこと。

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
