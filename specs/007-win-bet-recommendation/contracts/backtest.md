# Contract: 戦略・ROI baseline・疑似ROIバックテスト

`horseracing_betting.strategies` / `roi` / `backtest` の契約。

## 戦略インターフェース

```python
class Strategy(Protocol):
    name: str
    def bets_for_race(self, horses: list[dict], *, stake: float) -> list[Bet]: ...

class EVStrategy:          # win_prob*odds>=threshold を全頭
    def __init__(self, threshold: float): ...
class FavoriteROIBaseline: # 最低 odds(人気1番)を 1 頭
    ...
class UniformROIBaseline:  # 全出走馬を均等
    ...
```

すべて `started` のみ・odds null/<=0 を除外。EVStrategy は win_prob を再正規化して使う。
baseline は odds のみで動き win 確率を使わない(ROI 専用)。

## 疑似ROI 採点

```python
@dataclass(frozen=True)
class RoiReport:
    strategy: str
    n_races: int
    n_bets: int
    total_stake: float
    total_payout: float
    recovery_rate: float    # total_payout / total_stake (疑似評価)
    hit_rate: float         # hits / n_bets
    skip_rate: float        # races with no bet / n_races
    max_drawdown: float     # 賭けたレースのみの累積損益(Σ bet_pnl)の最大DD = 絶対額(stake 単位)
                            #   = max(running_peak - running_cum) over bet races. 正規化はしない。
    max_losing_streak: int  # 賭けたレースのみの最大連敗
    pseudo: bool = True     # 常に True(確定オッズ使用)

def score_backtest(per_race_results: list[RaceOutcome], strategy: Strategy, *,
                   stake: float) -> RoiReport:
    # 各レースで bets_for_race -> 的中(finished&finish_order==1)で payout=stake*odds、外れ 0
    # 取消・除外は母集団から除外、DNF は負け、同着 1 着は的中 (R3)
    # max_drawdown / max_losing_streak は「賭けたレースのみ」で計算 (FR-009)
```

## バックテスト(期間)

```python
def run_backtest(session, *, start_date, end_date, model_version=None,
                 threshold, stake) -> dict[str, RoiReport]:
    # 1. serving.load_serving_model(once)
    # 2. build_feature_matrix(end_date=end_date) を 1 度、期間内レースを抽出
    # 3. 各レース: serving.predict_race(in-memory) で win_prob、race_horses.odds、race_results を集約
    # 4. EVStrategy / FavoriteROIBaseline / UniformROIBaseline を同一レース集合で score_backtest
    # 5. {strategy_name: RoiReport} を返す(全 pseudo=True)
```

## 保証(テストで検証)

- 勝ち/負け/DNF/取消/同着を定義どおり採点(回収率・的中率・見送り率・最大DD・最大連敗)。
- EV 戦略と 2 baseline が同一レース集合・同一 stake で比較される。
- 最大DD・最大連敗は賭けたレースのみ、見送りは skip_rate に計上。
- 全レポートが `pseudo=True`(疑似評価)。
- 決定論(同一データ・同一パラメータで同一レポート)。
