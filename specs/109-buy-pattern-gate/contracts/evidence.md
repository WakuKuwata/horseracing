# Contract: 証拠と verdict

## 証拠(窓ごと)
- `artifacts/109/<window>-bets.parquet`(gitignore 済み・`evidence_refs` の sha256 で参照): 賭け行(`pattern_id, race_id, horse_number, horse_id, race_date, odds, q, p, won, dead_heat, n_winners, payout, stake`)。対照を含む。この列定義が正本。git にコミットするのは生存者と対照の確認窓分(`evidence/confirmatory-bets.parquet`)のみ。
- `artifacts/109/rows-<window>.parquet`: `screen` が固定した行スナップショット。`confirm` / `recompute` は DB を再読せずこれを読む(`population.json` の `rows_hash` 照合)。
- 全ての証拠 JSON と verdict は `run_code_sha`(実行時 HEAD)と `run_tree_dirty` を持つ。
- `evidence/<window>-summary.json`: パターン別集計([data-model.md §5](../data-model.md))。確認窓では `p_profit_one_sided` と `p_futility_one_sided`(c=1.02)の両方と、参考の max-T 同時上限を持つ。`second_aggregation_match` が false なら書き出し自体を中断。
- `recompute --window <w>`: bets parquet と `population.json` の `day_universe` だけから点推定・CI・p 値(主判定と感度 4 版)を再計算し summary とビット一致を assert(100 US1 同型)。

## 生存者
- `evidence/survivors.json`: [data-model.md §6](../data-model.md)。`screen` 完了時に一度だけ生成。既存なら拒否。

## verdict
- `verdict.json`: append-only(既存パスは拒否・`--force` なし)。[data-model.md §7](../data-model.md)。`evidence_refs`(窓・パス・sha256)と `controls` の確認窓値を必ず含む。生存者ごとに `available_at` と `historical_close_signal`。
- 全体結論は状態件数で書く。禁止表現: 「全パターン REJECT」「効果なし」「利益パターンは存在しない」。許容: 「凍結した探索で確認候補なし」「δ=0.02 超の利益を N 本で否定」。
- `limitations` は固定 5 項(closing_price_leak / payout_approximation / dead_heat_handling / no_correction_history / win_only)を必ず含む。
