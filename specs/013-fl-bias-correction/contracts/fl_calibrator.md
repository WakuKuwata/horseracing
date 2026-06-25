# Contract: FL バイアス校正器

`probability/fl_bias.py` の公開契約。校正は正規化後 q' を学習対象、p 非参照、walk-forward 厳密前。

## 校正器（fit / apply）

```
fit_fl_calibrator(samples, *, method="power", select="mle", min_samples=...) -> FLCalibrator
```

- `samples`: list of `(win_odds: dict[str,float], winner_horse_id|None)`（同着 winner=None で除外）。strictly-before は
  **`(race_date, race_id) < (target_date, target_race_id)`**（辞書順、race_date 常在・決定論。post_time が両側非 null なら intra-day を
  精緻化）。呼び出し側が walk-forward を保証（日付単位 `<=` 禁止、学習/評価窓は非重複）。
- 学習: 各レースで `q=market_implied_win_probs(win_odds)` → `q'_i=g(q_i)/Σg(q_j)` の**正規化後勝者尤度（conditional-logit）を最大化**。
  - **power（MVP の唯一実装）**: `g(q)=q^γ`。目的 `Σ_races −log(q_w^γ/Σ_j q_j^γ)` を γ ∈ `[GAMMA_MIN, GAMMA_MAX]`(既定 `[0.1,5.0]`)で
    有界 1 次元最小化（黄金分割等、決定論・seedless）。**情報レースのみ**（有効馬≥2 かつ q が全馬同一でない）を使用。情報レース 0 件
    なら **γ=1（恒等）+ 不十分マーク**。
  - **isotonic / loglog**: 将来（正規化後目的の実装が非自明）。`method` 引数は受けるが**未実装は明示エラー**（NotImplementedError）。
- 方式/ハイパラ選択（`select`）は**学習窓内**で、最終評価期間を使わない。
- 戻り: `FLCalibrator`(method/params/train_window/n_races/n_samples/odds_range/logic_version)。p を一切参照しない。

```
apply_calibrator(calibrator, win_odds) -> CorrectedMarketProbs
```

- `q=market_implied_win_probs(win_odds)`（取消・除外・無効オッズ除外）→ `q'_i=g(q_i)/Σg(q_j)`（Σ=1、単調保持）。
- field_size は**補正後の有効出走集合**から導出。**009 の `_normalize_clip`（renorm→clip[eps,1−eps]→renorm）を末尾に同一適用**して
  q' を生成 → エンジンに対し**冪等**（`_normalize_clip(q')≈q'`、評価=使用）。極小テールは clip で端点へ寄せ再正規化。
- 不変: `Σq'=1`、q→q' 単調、決定論。

## 補正済み推定オッズ（market_odds 拡張・opt-in）

```
estimate_market_odds(win_odds, *, calibrator=None, field_size=None, payout_rates=None, odds_cap=...) -> EstimatedOdds
```

- `calibrator` 指定時: q→q'(補正)→009→`O_est=(1−控除率)/P_market(q')`。未指定は生 q（**後方互換**）。`is_estimated=True`（疑似）。
- 補正済み推定単勝オッズは生オッズを**厳密復元しない**（バイアス除去の意図、明示）。
- **禁止**: モデル p 参照、q'/オッズを win モデル特徴化。

## エラー/エッジ

- 有効馬 < 2 → 補正不能（生 q と同様にスキップ）。
- 学習レンジ外 q → 端点クリップ（isotonic）/ 外挿（power）、範囲外件数を監査。
- 同着・勝者なしレース → 学習サンプルから除外（件数明示）。
- 決定論: 同一 (samples, method, select) → 同一 calibrator。
