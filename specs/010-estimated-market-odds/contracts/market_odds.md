# Contract: 推定市場オッズ変換

`horseracing_probability.market_odds` の契約。入力は市場オッズのみ(モデル p 非参照)。

## 控除率

```python
# payout_rate R_b = 1 - takeout。JRA 既定(平成26年6月7日以降)、設定可能。
DEFAULT_PAYOUT_RATES = {
    "win": 0.80, "place": 0.80, "quinella": 0.775, "wide": 0.775,
    "exacta": 0.75, "trio": 0.75, "trifecta": 0.725,
}
```

## 市場含意 q

```python
def market_implied_win_probs(win_odds: dict[str, float]) -> dict[str, float]:
    # 有効オッズ(>0)のみ母集団に。q_i=(1/odds_i)/Σ(1/odds_j)。Σ<=0/残存不足は MarketOddsError。
    # 返り値は市場投票シェア(真の勝率/モデル p ではない)。Σq=1。
```

## 推定オッズ

```python
@dataclass(frozen=True)
class EstimatedOdds:
    win: dict[str, float | None]
    place: dict[str, float | None] | None
    exacta: dict[tuple[str, str], float | None]
    quinella: dict[frozenset[str], float | None]
    wide: dict[frozenset[str], float | None] | None
    trifecta: dict[tuple[str, str, str], float | None]
    trio: dict[frozenset[str], float | None]
    payout_rates: dict[str, float]      # 使用した R_b(監査)
    is_estimated: bool = True           # 常に True(推定、実オッズではない)

def estimate_market_odds(
    win_odds: dict[str, float], *, field_size: int | None = None,
    payout_rates: dict[str, float] | None = None, odds_cap: float = 10000.0,
) -> EstimatedOdds:
    # 1. q = market_implied_win_probs(win_odds)
    # 2. jp = joint_probabilities(q)  (Feature 009、q を入力)
    # 3. 各券種 c: P_market(c) <= eps なら est = None(価格付け不能)、それ以外は est = min(R_b/P_market(c),
    #    odds_cap)(既定 odds_cap=10000)。確率本体 P_market は cap しない(整合性維持)
```

## 保証(テストで検証)

- 人工オッズ `odds_i=R/s_i` で `q_i=s_i`、推定単勝オッズ `=odds_i`(復元、SC-001)。
- `q` を 009 に通した出力が整合性(Σ=1・無順序=順序和・wide=Σ_k trio)を満たす(SC-002)。
- 各券種の推定オッズ `=(1−takeout_b)/P_market`、控除率が監査に残る。
- 欠損/0/負・取消・除外を母集団から除外して再正規化、推定不能を返す(SC-003)。
- `P_market→0` で推定オッズが cap/None、確率本体は壊れない(SC-003)。
- モデル p を一切参照しない(SC-005)。決定論(SC-006)。推定オッズは `is_estimated=True`。
