# Contract: eval — policy adoption gate (walk-forward OOS)

**依存方向(codex)**: production pl_topk/features-016 を使うため **driver は training 側**(LightGBMPredictor を持つ)、**scorer は eval 側の純関数**(`market_gate.py` 型)。既存 `betting/backtest.py`(単一 model 期間 backtest)・`eval/operational.py`(最高 EV 1 頭のみ)は 064 の全馬買い walk-forward 比較には**流用不可**=新規。import 境界: training→eval→betting.roi/strategies(循環しないことを境界テストで確認。循環する場合は win-only scorer を eval 側に薄く再実装)。

## `eval/policy_gate.py::evaluate_policy_gate(oos_rows, *, cap=21.0, threshold=1.0) -> PolicyGateReport`

- OOS 収集(fold 毎 fit→predict)は training 側 driver が行い、per-horse (p, odds, won, race_id, year) を eval scorer に渡す(pure・predictor 非依存)。

- walk-forward OOS(expanding_folds、fold 毎 `predictor.fit(train)` → `predict_race(valid)`、market_edge と同じ骨格)で各 started 馬の (p, odds, won, race_id, year) を収集(**closing odds**・障害除外オプション)。
- 各 strategy を `betting.roi.score_backtest`(または eval 側の同義 win-only スコア=依存境界次第、research §R4)で採点:
  - `EVStrategy(threshold)`(現行)
  - `OddsCappedEVStrategy(threshold, cap)`(候補)
  - `FavoriteROIBaseline` / `UniformROIBaseline` / no-bet(×1.0)
- 追加集計: fold(年)別 recovery・odds帯別 recovery/n・log growth・n_folds_improved・worst_fold_delta。

## PolicyGateReport(data-model 参照)

- 採否 `adopted` = cap policy recovery > 現行 recovery **かつ** 過半 fold 改善 **かつ** 最悪 fold 非悪化。
- `note`: 「closing-oracle バイアス込み・相対比較のみ有効・ROI>1 は採否バーでない」を常時添付。

## CLI(training 側で predictor 注入)

- `training policy-gate-eval --from --to --first-valid-year <y> [--cap 21] [--objective pl_topk --calibration isotonic --target-encode jockey_id,trainer_id]`
- production 構成(pl_topk+features-016)で最終確認。proxy(binary)でも走るが忠実版は pl_topk。

## テスト

- `test_policy_gate_same_population`: 現行と cap policy が同一 OOS 母集団・同一 fold で採点される。
- `test_policy_gate_cap_fixed_no_selection_leak`: cap 値は評価期間外で固定(引数)・fold 内で cap を選ばない。
- `test_policy_gate_report_by_fold_and_band`: fold 別・odds帯別が算出される。
- `test_policy_gate_adoption_rule`: 合格条件(相対改善+fold 安定)が正しく判定される(合成データ)。
- `test_gate_uses_closing_odds_flag`: レポートに closing-oracle 注記が付く。
