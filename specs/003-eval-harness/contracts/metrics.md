# Contract: 指標とハーネスの入出力

`horseracing_eval.metrics` / `harness` が公開する評価契約。

## 予測品質指標 (label 別)

各 label (win/top2/top3) について、採点母集団の `(prob, label)` 配列から算出:

```python
def log_loss_label(prob, label) -> float        # sklearn.log_loss
def brier_label(prob, label) -> float           # sklearn.brier_score_loss
def auc_label(prob, label) -> float | None      # sklearn.roc_auc_score (片側クラスのみは None)
def ndcg_label(prob, label, groups) -> float    # レース単位 group で sklearn.ndcg_score
def ece_label(prob, label, bins=10) -> float    # 自前 (等幅 bin, 加重平均)
def ece_by_field_size(prob, label, field_sizes, bins=10) -> dict[int, float]
```

- 入力配列は (race_id, horse_id) で安定ソート。NDCG はレース単位 group。
- AUC は valid に片側クラスしか無い場合 None (空 fold 同様にスキップし記録)。

## 確率整合性 (consistency)

```python
def check_consistency(predictions_by_race, tolerance) -> None
    # 各馬 0<=win<=top2<=top3<=1、レース内合計が tolerance 内。違反は ConsistencyError を raise (fail-fast)。
# tolerance 既定: {win: 0.05, top2: 0.10, top3: 0.15}
```

## ハーネス

```python
def evaluate(predictor, *, first_valid_year=2008, ece_bins=10, tolerance=...) -> EvalResult
    # expanding-window walk-forward。fold ごとに fit(train)→predict(valid)→consistency 検証→指標集計。
    # 戻り値 EvalResult: overall(label別) + by_fold + by_field_size_ece。data-model の metrics_summary 形に対応。
```

- 決定論的 (乱数なし)。空 fold / 全馬非完走レースはスキップして記録。
- `store.save_baseline(session, model_version, result)` が `model_versions.metrics_summary` に保存。
