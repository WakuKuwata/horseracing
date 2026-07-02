---
description: "Task list — p校正の推奨経路組込 (046)"
---
# Tasks: 検証済み p 校正の推奨経路組込
## US1/US2
- [X] T001 [US2] `betting/recommend.py`: generate_recommendations に p_calibrator opt-in — renormalized_started_probs → apply_p_calibrator → horses へ書き戻し → select_ev_bets。lv に pcal 追記。None=従来
- [X] T002 [P] `betting/tests/`: 007 校正適用(EV 変化)・None 後方互換・lv 記録
- [X] T003 [US1] `betting/cli.py`: `_fit_product_p_calibrator(session, before_date, target)` = 予測最古日〜前日を load_p_samples → split_before → fit(identity fallback)。_generate_product_set に p_calibrator を通し win/exotic 両方へ。serve=レース前・backfill=日単位1回フィット
- [X] T004 [P] `betting/tests/`: serve/backfill の pcal lv 記録・リーク境界(対象日以降不混入)・不足→identity
- [X] T005 実 DB E2E: 校正付き生成(γ 記録)を確認
## Polish
- [X] T006 [P] betting/api スイート緑
- [X] T007 [P] CLAUDE.md 046 サマリ(マージ時に追記)
