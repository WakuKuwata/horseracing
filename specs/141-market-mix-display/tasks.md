# Tasks: 141 期待回収率の市場混合版を並走表示する

## Phase 1: 定義と再現
- [x] T001 eval `market_mix.py`(式・入力検証・年引き・閾値・版)+ 単体テスト(手計算一致・Σ=1・レース内定数の打ち消し・極限 a/b・数値安定・入力拒否・未知年・厳密閾値・fit 出力との一致)
- [x] T002 `fit_mix.py`: 年次 walk-forward で係数を fit し 10/10 報告を再現(SC-001)・共有定義との parity 3e-16
- [x] T003 2025・2026 年用係数を凍結(source_sha256 つき)

## Phase 2: held-out(一度だけ)
- [x] T004 `protocol.json` + sha256 を凍結(窓 2026-08-10〜10-18・確定オッズの判定・規則)
- [x] T005 `heldout.py` を一度実行 → `results/heldout_2026-08-10_2026-10-18.json`(FR-006 の規則で表示可)

## Phase 3: API・front
- [x] T006 API: `build_market_ev(race_date=)` 必須化・`_mix`・応答に `expected_return_mix`/`exceeds_mix_threshold`/`mix`(純追加)・attention の 2 経路に race_date
- [x] T007 API テスト(単体: 手計算一致・年・unavailable 4 理由で null・必須引数 / schema: 必須かつ nullable・判別共用体・p_mix 拒否 / 統合: 保存行から計算・保存行数不変)
- [x] T008 OpenAPI snapshot・生成型(front/admin)・drift-check
- [x] T009 front: 「市場混合」列(推定バッジ・並び替え不可・中立の「100%超」)・注記の固定文言・colSpan・テスト
- [x] T010 実画面確認(API 再起動・10-17 のレースで列と目印を確認)

## Phase 4: レビューと記録
- [x] T011 独立レビュー(codex unavailable のため代替)と修正
- [x] T012 記録: plan §3/§4・spec の結果・CLAUDE.md・memory
