# Contract: 推定市場オッズの検証(評価先行)

`horseracing_probability.market_calibration` の契約。変換は結果非参照、採点のみ結果を使う。全出力は疑似評価。

## 単勝オッズ復元

```python
@dataclass(frozen=True)
class RecoveryReport:
    n_races: int
    mean_abs_log_ratio: float   # mean_r |log(R_win·S_r)| (= 全馬同率の復元誤差)
    mean_abs_rel_error: float   # mean over (race,horse) |hat_odds/odds − 1|
    pseudo: bool = True

def recover_win_odds(win_odds: dict[str, float], *, payout_rate_win: float) -> dict[str, float]:
    # hat_odds_i = payout_rate_win / q_i。控除率=実オーバーラウンドなら hat_odds = odds。
```

## q 校正

```python
@dataclass(frozen=True)
class QCalibrationReport:
    n_races: int
    nll: float      # 実勝馬に対する市場含意 q の平均 NLL
    brier: float    # 実勝馬 one-hot に対する q の Brier
    pseudo: bool = True

def evaluate_market_odds(session, *, start_date, end_date,
                         payout_rates=None) -> tuple[RecoveryReport, QCalibrationReport]:
    # 1. 期間の各レースの started + 有効オッズ馬の win_odds を取得
    # 2. recover_win_odds で復元誤差、market_implied_win_probs で q を計算
    # 3. race_results の勝馬で q の NLL/Brier を集計
    # 全出力 pseudo=True(推定市場オッズの疑似評価)
```

## 保証(テストで検証)

- 過去データで単勝復元誤差(レース単位)と q 校正(NLL/Brier)が算出される。
- 控除率=実オーバーラウンドのとき復元誤差≈0。
- 変換が結果/モデル p を参照しない(採点のみ結果使用)。
- 全レポートが `pseudo=True`(推定市場オッズ、実 exotic 価格ではない)。
