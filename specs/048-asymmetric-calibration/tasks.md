---
description: "Task list — 非対称 p 校正 two_gamma (048)"
---
# Tasks: 非対称 p 校正 two_gamma
- [X] T001 `probability/model_calibration.py`: method "two_gamma" — `_apply_two_gamma(p, γlo, γhi, pivot)`(連続区分 power+_norm)・`fit_two_gamma(samples, pivot=0.15)`(粗グリッド→座標 golden、train 内 NLL)・PCalibrator/apply/fit_p_calibrator 結線(identity fallback 共通)
- [X] T002 [P] `probability/tests/`: 連続性(pivot境界)・単調性・Σ=1・決定論・γlo=γhi=power 一致・identity fallback・既存 power 不変(test_two_gamma.py 7 tests)
- [X] T003 `probability/cli.py`: calibrate-eval に `--method {power,two_gamma}`(evaluate_calibration_db へ透過、params 表示汎用化)
- [X] T004 実 DB A/B(事前登録ゲート): 第1回=全期間窓は eval n=3 で無効(underpowered、密度から判明)→ 密集窓 2024-11-02..2024-12-28 に改訂し再実行 → PRIMARY/MUST 全通過 **ADOPTED**(spec 結果セクション)
- [X] T005 採否反映: betting `_fit_product_p_calibrator` を method="two_gamma" に切替(serve/backfill 両経路が経由、046 テストは fit method 非依存で不変)・実 DB E2E で pcal=two_gamma の lv 記録確認
- [X] T006 [P] probability/betting スイート緑(probability 94 / betting 138 passed)
- [X] T007 [P] CLAUDE.md 048 サマリ(マージ時)
