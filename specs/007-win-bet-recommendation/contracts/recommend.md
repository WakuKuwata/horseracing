# Contract: EV 選択と推奨生成

`horseracing_betting.ev` / `recommend` の契約。

## EV 選択(純関数)

```python
@dataclass(frozen=True)
class Bet:
    horse_id: str
    horse_number: int | None
    win_prob: float      # 再正規化後
    odds: float
    ev: float            # win_prob * odds
    stake: float

def select_ev_bets(horses: list[dict], *, threshold: float, stake: float) -> list[Bet]:
    # horses: [{horse_id, horse_number, win_prob, odds, entry_status}]
    # 1. started のみ (取消・除外を除外)
    # 2. odds が null/<=0 / win_prob<=0 の馬を除外
    # 3. 残存馬の win_prob を Σ=1 に再正規化 (INV-B1)
    # 4. EV = win_prob_renorm * odds、EV>=threshold の馬をすべて Bet に (INV-B4)
    # 結果(着順)は一切参照しない (INV-B2)
```

## 推奨生成(永続化)

```python
def generate_recommendations(session, *, prediction_run_id, threshold, stake,
                             logic_version=None) -> list[uuid.UUID]:
    # 1. prediction_run の race_id と race_predictions(win_prob) をロード
    # 2. race_horses から odds/horse_number/entry_status を結合
    # 3. select_ev_bets で買い目決定
    # 4. recommendations に append-only 保存:
    #    bet_type='win', selection={horse_id,horse_number}, market_odds_used=odds,
    #    is_estimated_odds=false, estimated_market_odds_used=null,
    #    pseudo_odds=1/win_prob_renorm, pseudo_roi=win_prob_renorm*odds-1,
    #    logic_version, computed_at
    # 返り値: 生成した recommendation_id 群
```

## 保証(テストで検証)

- EV>=閾値 の馬だけが `bet_type='win'` で保存される。各行に監査情報(odds/pseudo_odds/pseudo_roi/selection/
  logic_version)が揃う。
- odds null/<=0・取消/除外・win_prob=0 の馬には推奨を出さない。除外後に残存馬で再正規化される。
- 買い目選択に `race_results`(着順)を参照しない。
- append-only(再生成は新しい recommendation 群)。
- 決定論(同一 prediction_run・閾値・stake・logic_version で同一の買い目集合)。
