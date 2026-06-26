# Contract: Kelly 推奨生成（CLI）

レース指定で Kelly 最適賭け金を算出し `recommendations` に append-only 保存する。betting/ の CLI を拡張。

## コマンド（例）

```
betting kelly-recommend <race_id> [--bankroll 100] [--lambda-real 0.25] [--lambda-est 0.10] \
  [--cap-bet 0.05] [--cap-total 0.10] [--o-min 1.5] [--allocation exact|heuristic] \
  [--enable-estimated/--no-estimated] [--bet-types quinella,exacta,...] [--prediction-run <id>]
```

## 入力（読み取りのみ、結果非参照）

- `race_predictions.win_prob`（モデル win 確率 p）→ 009 joint_probabilities で P_model(c)。
- `exotic_odds`（012 実）優先 / `race_horses.odds`→010 estimate_market_odds（推定）フォールバック。
- prediction_run 選択は 014 と同じ決定論（active → computed_at DESC → run_id tie-break）。

## 処理（順序固定）

1. canonical field 構築（p と使用オッズが両方有効な馬／買い目、取消・除外を除外し再正規化）。011/012 と同一経路。
2. 各買い目 c: P_model(c)=009、O(c)=実 or 推定。edge=P_model·O−1。edge≤0 / O<O_min は見送り。
3. fractional Kelly: 実効 fraction = clip(λ·f*, 0, cap_bet)。λ=odds 源で λ_real/λ_est。
4. 同一(race,bet_type)で `allocation=exact` なら期待対数成長最大化、`heuristic` なら個別+合計比例縮小。Σ ≤ cap_total。
5. stake = stake_fraction × bankroll。recommendations に append-only 保存（is_estimated_odds / double_pseudo は odds 源で決定）。

## 出力（recommendations 行）

| 列 | 値 |
|---|---|
| bet_type / selection | 券種 / 011 to_selection |
| market_odds_used | 実 O（実経路）／NULL |
| estimated_market_odds_used | 推定 O（推定経路）／NULL |
| is_estimated_odds | 推定経路 = true（= double_pseudo） |
| pseudo_odds | 1/P_model |
| pseudo_roi | edge = P_model·O−1 |
| **stake_fraction** | **実効 Kelly fraction（新列）** |
| logic_version | Kelly 設定一式 + 009/010 版 |
| prediction_run_id / race_id / computed_at | 既存 |

## 不変条件 / エラー

- edge ≤ 0 は不保存（見送り）。Σ stake_fraction(race,bet_type) ≤ cap_total。stake_fraction ∈ [0,cap_bet]。
- 確率は P_model のみ（q を確率に使わない、p≠q）。結果非参照。決定論（同一入力 → 同一出力）。
- P_model か O の一方欠損 → 当該買い目除外。推定不能券種・成立しない券種は除外。
- 採用後 scratch → void/skip（011/012 と同一）。
