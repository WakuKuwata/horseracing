# Contract: 結合確率の校正評価

`horseracing_probability.calibration` の契約。確率導出は結果非参照、採点のみ結果を使う(リーク境界)。

## 独立積 baseline

```python
def independent_product_joint(win_probs: dict[str, float]) -> JointProbabilities:
    # 誤った独立仮定の baseline: exacta ∝ p_i·p_j、trifecta ∝ p_i·p_j·p_k を Σ=1 に再正規化
    # (復元なしを無視。PL の優位を示すための対照)
```

## 校正評価

```python
@dataclass(frozen=True)
class CalibrationReport:
    strategy: str            # 'plackett_luce' | 'independent_product'
    bet_type: str            # 'exacta' | 'trifecta' (順序付き多クラス)
    n_races: int
    nll: float               # 実現組み合わせの平均負対数尤度
    brier: float             # 実現 one-hot に対する Brier

def evaluate_calibration(session, *, start_date, end_date, bet_type) -> dict[str, CalibrationReport]:
    # 1. 期間の race_predictions(win_prob)を取得(出走母集団、再正規化)
    # 2. 各レースで joint_probabilities と independent_product_joint を計算
    # 3. race_results から実現した順序付き組み合わせ(exacta=確定1,2着 / trifecta=1,2,3着)を取得
    #    取消・除外/未完走/同着は規則で扱う(該当しないレースは除外)
    # 4. 実現組み合わせの確率で NLL/Brier を集計、{strategy: CalibrationReport} を返す
    # 確率導出は結果を見ない。結果は採点のみ。
```

## 保証(テストで検証)

- 実現した馬単/三連単に対する NLL/Brier が算出される。
- Plackett-Luce と independent_product が同一レース集合・同一条件で比較される。
- PL が baseline を悪化させない(校正で同等以上)。
- 確率導出は結果/オッズ非参照(採点のみ結果使用)。
