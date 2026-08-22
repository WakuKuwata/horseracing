# 097 gate run 1 — 無効(verdict として採用しない)

2026-08-22 15:43-19:13(3.5h)。全窓で paired diff が **厳密に +0.000000**、標本 CI [0, 0]、
top2/top3 差 0.0、ECE が両アーム同値 0.0037264853933708713 → **両アームが同一モデル**だった。
REJECT として記録してはならない(比較が成立していない=評価実行不能)。成果物は
`out/097-invalid-run-1.json` に退避(追跡しない)。

## 根本原因(潜在バグ・085 以来)

`calib_split.py` の `OofCalibratedPredictor._make_base` が shared matrix を `p._data` に**直接代入**
していたため、`LightGBMPredictor._ensure_data()` 内の `drop_features` / `restrict_features` の
スコープが迂回されていた。arm E(CalibSplitFactory)は常に shared matrix を使うので、
**arm E + drop= の組合せは無音で drop を無視する**。097 が初めてこの組合せを実行した。
CLI の `_factory_from_spec` の oof 分岐も `drop=` を黙って捨てていた(同型の穴)。

## 修正

- `predictor._scope_columns()` に restrict/drop を集約し、`_ensure_data` と注入経路の両方が通る
- CLI の oof 分岐で `drop=` を保持
- 回帰テスト `test_arm_e_drop_scope.py`(旧コードで 1 failed を確認)
- driver に構造 assert 2 箇所: 学習前に「実 matrix 上で両アームの有効列集合の差 == drop 集合」、
  学習後に「非ゼロ paired diff が 1 件以上」(0 件なら abort)。smoke は**非ゼロ件数**だけ出す
  (構造であって効果量ではないので redact 規律に反しない)

## 教訓

smoke が効果数値を redact していたため、配線の故障(両アーム同一)が smoke で見えなかった。
**redact するのは効果量であって構造ではない** — 「差が存在するか」は smoke で必ず確認する。
