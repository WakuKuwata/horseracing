# codex 設計レビュー(085 arm E)採否記録

**日付**: 2026-07-25 | **方式**: `codex exec --sandbox read-only` を 3 並列(統計 / 実装・リーク /
製品化)| ログ: セッション scratchpad(`out-design-{1,2,3}-*.log`)

Claude が意思決定者。以下は指摘ごとの採否と理由。

## 採用(設計に反映済み)

| # | 指摘 | 出所 | 反映先 |
|---|---|---|---|
| 1 | **スコア空間の不整合**: C/D の OOF サンプルは `predict_race()`(identity clip + 再正規化済み)由来。A と serving は生 race-softmax に校正器を適用する。「C/D と同一機構」かつ「A と同一空間」は現コードでは両立しない | 2・3 が独立に指摘 | spec §3.1(生 softmax を返す共有メソッドを切り出す) |
| 2 | **`_single_winners` は E のラベル源にできない**(同着レースを捨てる)。started-all で同着は両馬正例・DNF/失格は負例・取消は行なし・結果欠損は 0 に化けさせない | 2 | spec §3.2 |
| 3 | **CLI ルート未実装 + 未知 method の暗黙 Platt フォールバック**。`pl_topk:oof_isotonic` は今のままだと別実験(Platt holdout)を黙って測る | 2 | spec §3.5 / §11。**Claude が実測で確認**(`fit_calibrator(method='oof_isotonic') → platt`) |
| 4 | **不十分 fold の fail-closed**: identity に落ちた fold を黙って arm E として採点しない(中断 or NO_DECISION)。fold 間の学習状態を全リセット | 2 | spec §3.3 / §3.4 |
| 5 | **この窓は confirmatory ではない**。E は C/D の結果を見て着想された。repo 自身が 2008–2026 を development evidence と明記 | 1・3 | spec §4.1(降格を明示・Holm を感度分析として併記・採用には未使用データ) |
| 6 | **seed の記述誤り**: 凍結 config の bootstrap seed は **20260713**(CLI `--seed` は上書きされる)。model seed は別に 42 | 1・2 | spec §4 |
| 7 | **`--confirmatory` が窓を照合していない**(CLI が from/to を渡さない)・`bootstrap.alpha` が不活性 | 1・2 | spec §11(arm E とは独立に先に塞ぐ) |
| 8 | **製品化は (c)**: 標準 model_version artifact(booster + `Calibrator` ラップした isotonic + preprocessor + metadata)+ 074 型 evidence 規律。`OofCalibratedPredictor` を pickle しない。074 manifest は lgbm-063 に hard-pin + schema が two-gamma/stage-λ のみで流用不可 | 3 | spec §5 |
| 9 | **provenance の意味論**: `calib_from/calib_through`・`calibration_split_unit` を E に流用しない(別語彙)。FEATURE_VERSION は bump しない・model_version は新規採番・candidate 固定 | 3 | spec §5 |
| 10 | **順序**: contract 凍結 → 評価 arm のみ実装 → 1 回実行 → PASS なら製品化 | 3 | spec §9 |
| 11 | 失敗モードの事前明記(OOF→final 転移ずれ・正規化後の周辺校正・plateau tie・field size 重み・primary 毀損) | 1・2 | spec §6 |
| 12 | リスク登録簿(serving stage-discount 既定 ON との不一致・betting の二重校正・複数 ACTIVE・same-version subset) | 3 | spec §5 / §8 / §9-5 |

## 不採用・保留

| 指摘 | 判断 |
|---|---|
| 「ECE ゲート 0.001 は厳しすぎる/別の校正指標(cross-fitted log-score calibration loss)にすべき」(1) | **この run では不採用**。OOS を見た後の閾値変更は憲法 III 違反。指摘自体は妥当なので **将来の別 feature で新規事前登録**する候補として spec に残さず本記録に留める |
| 「E を走らせる前に FWER 制御を gate に実装せよ」(1) | **保留**。この窓は development evidence と明示したので主判定を調整しない方が誠実(調整済みは感度分析)。未使用コホートを多 arm で共有する場合は必須 → その時に実装 |
| 「先に prospective コホートを凍結してから E を走らせよ(E をこの窓で走らせない)」(1 の推奨案 1) | **部分採用**。この窓での E は kill filter として価値がある(実装コストが小さく、駄目なら数か月待たずに閉じられる)。ただし **採用判定は未使用データ**と明記した(§4.1・§7) |
| 「nested calibration selection(identity/power/isotonic を inner で選ぶ pipeline)」(1 の代替案 2) | **不採用(今回は)**。degrees of freedom を隠す方向で、まず単純な E の 1 比較を見る方が読み解ける |
| 「production 実装を E の前に全部作る」 | **不採用**。codex 3 自身も「contract 凍結は先・実装は PASS 後」と結論しており、そちらを採る |

## Claude が独自に確認した事実(codex の指摘ではない)

- **精度限定の prospective holdout は発走前オッズ capture を要さない**。073 の DORMANT
  preconditions は ROI 仮説向け。観測 CI 半幅 0.00218@813 race-days の外挿で、−0.012 なら
  ~27 race-days(約 3 か月)、−0.006 でも ~108 race-days(約 12 か月)。→ spec §7
- `fit_calibrator(method='oof_isotonic')` が実際に `platt` を返すことを実行して確認(#3 の裏取り)
- 広窓 run が実際に使った bootstrap seed は 20260713(verdict JSON で確認)= config が CLI を
  上書きする正しい挙動

## 残リスク

- E が PASS しても、この窓の結果は development evidence にとどまる。未使用データでの確認を
  経ずに昇格すれば、B/C-D/E の探索で選ばれた勝者を確認なしに出荷することになる
- OOF→final 転移ずれは **事前に安価な診断**(短履歴 vs 長履歴 booster のスコア分位・isotonic
  support 外の質量)で測れる。spec §6 に挙げたが、これを事前登録の一部にするか診断に留めるかは
  実装時に決める(結果を見てから足すのは禁止)
