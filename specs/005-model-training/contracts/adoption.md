# Contract: 採用判定と保存

`horseracing_training.adoption` / `artifacts` の契約。

## 採用ゲート

```python
@dataclass(frozen=True)
class AdoptionGate:
    ece_threshold: float            # 設定可能 (既定は research/実データ確定)

@dataclass(frozen=True)
class AdoptionDecision:
    adopted: bool                   # True -> active, False -> candidate
    reasons: dict                   # 各条件の合否

def evaluate_gate(model_summary: dict, baseline_summary: dict, gate: AdoptionGate) -> AdoptionDecision:
    # win LogLoss(model) < win LogLoss(baseline) AND
    # top2/top3 LogLoss(model) <= baseline AND
    # win ECE(model) <= gate.ece_threshold
```

## 保存

```python
def save_model_version(session, *, model_version, eval_result, adopted,
                       artifacts_dir, seed, params, calibration, feature_version,
                       fold_boundaries, feature_hash, git_sha) -> None:
    # model_versions に upsert: model_family='lightgbm', label_schema='win_top2_top3',
    #   adoption_status = 'active' if adopted else 'candidate',
    #   metrics_summary = eval_result.to_summary() + 学習メタ,
    #   weights_uri / calibrator_uri = artifacts のパス
    # artifacts/model_versions/{model_version}/ に model.txt / calibrator.pkl / metadata.json を書く
```

## 保証

- ゲート合格モデルのみ `adoption_status='active'`。不合格は `candidate` のまま。
- baseline は既存 `model_versions` (market/uniform) を同一評価条件で参照。
- `report.compare` (Feature 003) で model と baseline の同一条件比較が出る。
- metadata.json に再現情報 (seed/params/fold/校正方式/feature_version/feature hash/git sha) を保存。
