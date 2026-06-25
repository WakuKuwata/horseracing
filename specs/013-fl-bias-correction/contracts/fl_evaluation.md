# Contract: FL 補正の評価（勝率校正 + 乖離比較）

評価先行（憲法 III）。第一指標=勝率校正（採否ゲート）、補助=012 乖離（診断）。

## 勝率校正: q vs q'（market_calibration 拡張・第一指標）

```
evaluate_q_vs_qprime(samples, calibrator, *, bands=..., bins=...) -> list[QvsQpReport]
```

- `samples`: walk-forward **評価期間**の `(win_odds, winner|None)`（学習窓と重複しない）。
- 各レースで `q`（生）と `q'=apply_calibrator`（正規化後）を計算し、実現勝者に対し:
  - **NLL** `-log(prob_winner)`、**Brier**、**ECE**（**正規化後 q'**）。
  - **信頼性曲線（reliability）**: 各ビンの `(mean_pred, empirical_rate, n)` の配列を q / q' それぞれ返す（散布で校正を可視化）。
  - ECE/reliability の**ビンは固定既定エッジ** `DEFAULT_BINS`（既定 = 10 等幅 `[0,0.1,…,1.0]`、契約で固定）。空ビンは寄与 0 + n=0 で明示。
  - **人気帯別**（固定境界 = popularity または q 分位の固定エッジ）に overall + 帯別レポート（各帯サンプル数併記）。
- 同着・勝者なしは除外し件数明示。不足データは fail-fast / 不十分マーク。
- **採否ゲート**: q' が q を NLL/Brier/ECE で改善するか（baseline=補正なし生 q）。`improved` を返す。`pseudo=True`。

## 乖離比較: 補正前後（exotic_divergence 再利用・補助指標）

```
compare_divergence(session, *, date_from, date_to, calibrator, model_version=None) -> dict[str, DivergenceDeltaReport]
```

- 012 の `exotic_divergence` を **生 q** と **補正 q'**（estimate_market_odds(calibrator=...)）で 2 回回し、券種別に coverage_rate /
  `log(実/推定)` の **median・MAE・P90**（生 q / 補正 q' 両方）を並べる。
- **診断のみ**: 実 exotic は独自の控除/偏りを含むため、乖離縮小を採否条件にしない（採否は勝率校正で判断）。`pseudo=True`。

## CLI（probability/cli.py 拡張）

```
uv run python -m horseracing_probability fl-fit --train-from <d> --train-to <d> --method power
uv run python -m horseracing_probability fl-evaluate --train-from <d> --train-to <d> --eval-from <d> --eval-to <d> [--method power]
```

- `fl-fit`: 校正器要約（方式・γ・学習窓・サンプル数・q 範囲）を表示。
- `fl-evaluate`: q vs q' の NLL/Brier/ECE（人気帯別）+ 乖離前後比較を**疑似評価・採否=勝率校正**明示で表示。

## エラー/エッジ

- 評価期間が学習窓と重なる → ERROR（リーク防止）。
- 小サンプル人気帯 → サンプル数明示、過小帯は統合。
- 決定論: 同一 (samples, calibrator, bands, bins) → 同一レポート。
