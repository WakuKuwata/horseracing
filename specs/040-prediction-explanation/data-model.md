# Data Model: 予測根拠表示 (040)

## 変更エンティティ

### race_predictions（既存、migration 0008 で 1 列追加）

| 列 | 型 | 制約 | 説明 |
|---|---|---|---|
| prediction_run_id | Uuid (PK, FK) | 既存 | |
| horse_id | Text (PK, FK) | 既存 | |
| win_prob / top2_prob / top3_prob | Numeric | 既存 | **本 feature で値・意味とも不変（バイト一致）** |
| **explanation** | **JSONB, nullable** | **新規** | スコア寄与 top-K + 監査付帯（下記スキーマ）。NULL = 未提供（旧 run / degenerate モデル） |

explanation JSONB 固定スキーマ（[contracts/prediction-explanation.md](contracts/prediction-explanation.md) が正）:

- `method`: 文字列 `"lgbm_pred_contrib"`（説明手法識別子）
- `method_version`: 整数（現行 1）
- `k`: 整数（保存した top-K、既定 5）
- `base_value`: 数値（TreeSHAP base = 期待スコア）
- `score`: 数値（分解対象 = booster margin `raw_score=True`）
- `other_contribution`: 数値（top-K 外の寄与合算）
- `items`: 配列 `[{feature: str, value: number|string|null, contribution: number}]`、|contribution| 降順・feature 昇順タイブレーク

**不変条件 INV-E1（加法性）**: `base_value + Σ items[].contribution + other_contribution == score`（rel 1e-6）。保存単体で検証可能。

**不変条件 INV-E2（副作用ゼロ）**: explanation 計算の有無で win/top2/top3 の永続値はバイト一致。

model_version / feature_version との紐付けは既存の prediction_runs.model_version / feature_snapshots.feature_version（同 PK）から辿る — JSONB 内に重複保持しない。

### model_versions.metrics_summary（既存 JSONB、キー追記のみ・スキーマ変更なし）

```
metrics_summary["importance"] = {"type": "gain", "values": {<feature>: <gain: number>, ...}}
```

全モデル入力列（TE 列含む）を保存。未収録（旧モデル・degenerate）はキー不在 → API typed 404。

## 読み時導出（非保存）

### 乖離区分 divergence（HorsePrediction 応答フィールド）

入力: p = win_prob（canonical 正規化後、既存 pmap）、q = market_win_prob（021、同一 canonical field）。

| 状態 | 条件（FR-011 事前登録、変更禁止） |
|---|---|
| `market_higher`（市場評価がモデルより高い） | p < q − max(0.03, 0.5×q) |
| `model_higher`（モデル評価が市場より高い） | p > q + max(0.03, 0.5×q) |
| `similar`（ほぼ同等） | 上記以外 |
| null（バッジ抑制） | q が null、または canonical_consistent=false |

決定論・純関数。境界値（等号は similar 側）をテスト固定。

## リレーション図（変更部のみ）

```
prediction_runs 1─* race_predictions (+ explanation JSONB)   ← serving persist_run が同時書込
              1─* feature_snapshots (features = 入力スナップショット、既存・不変)
model_versions.metrics_summary += importance                  ← training save_artifacts
HorsePrediction(API) = race_predictions ⋈ (q, divergence)     ← 読み時導出、非保存
```

## バリデーション

- explanation は書込側（training explanation.py）で固定スキーマ生成 + 加法性自己検証してから保存（不正なら explanation を None にして予測は保存継続 — 予測本体を巻き添えにしない。ただしログ・テストで検知）。
- API 応答スキーマ（pydantic）は explanation を型付きで通す（自由 dict でなく ExplanationItem モデル）。
