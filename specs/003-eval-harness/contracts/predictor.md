# Contract: Predictor Protocol

`horseracing_eval.predictor` が公開する抽象。baseline と将来の LightGBM/校正器が同一契約を満たす。

```python
from typing import Protocol

class RaceContext:
    race_id: str
    race_date: datetime.date
    started_horses: list[HorseEntry]   # entry_status='started' のみ (取消・除外は除外済み)

class HorseEntry:
    horse_id: str
    frame: int | None
    horse_number: int | None
    # 発走前に利用可能な属性のみをここに置く (将来の feature-based predictor 用)。
    result_market: ResultMarket | None   # 結果確定時 odds/popularity は別オブジェクトに隔離

class ResultMarket:
    """結果確定時の odds/popularity。市場 baseline の参照線専用 (FR-013)。
    feature-based predictor はこのフィールドを参照してはならない (リーク)。"""
    odds: float | None
    popularity: int | None

class Prediction:
    win: float
    top2: float
    top3: float

class Predictor(Protocol):
    def fit(self, train_races: list[RaceContext]) -> None: ...   # baseline は no-op
    def predict_race(self, race: RaceContext) -> dict[str, Prediction]: ...
        # 戻り値: started_horses の horse_id -> Prediction (全頭)
```

## 契約上の保証 (ハーネスが検証)

- `predict_race` は母集団 (started 馬) 全頭の予測を返す。
- 各 Prediction は `0<=win<=top2<=top3<=1`。レース内合計は許容誤差内 (data-model INV-E1/E2)。違反は
  ハーネスが fail-fast。
- `fit` は valid 窓より前の train のみを受け取る (リーク防止、INV-E3)。baseline は状態を持たないため no-op。
- 決定論的 (同一入力で同一出力)。

## 非ゴール

- 特徴量生成・永続化・serving は Predictor の責務外 (別 feature)。
- 結果確定 odds/popularity は市場 baseline の参照線専用。将来モデルの特徴量には使わない (FR-013)。
