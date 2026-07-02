# Contract: 予測根拠表示 (040)

契約先行（憲法 VI）。API 変更は openapi.json 再生成 + front 型再生成 + drift-check で固定する。

## 1. explanation JSONB（race_predictions.explanation、書込 = serving のみ）

```json
{
  "method": "lgbm_pred_contrib",
  "method_version": 1,
  "k": 5,
  "base_value": -3.2109,
  "score": -2.4711,
  "other_contribution": 0.1198,
  "items": [
    {"feature": "te_jockey_id", "value": 0.081, "contribution": 0.42},
    {"feature": "rel_time_avg", "value": -0.35, "contribution": 0.31},
    {"feature": "sire_win_rate", "value": 0.12, "contribution": -0.28},
    {"feature": "venue_code", "value": "05", "contribution": 0.15},
    {"feature": "days_since_last", "value": null, "contribution": -0.09}
  ]
}
```

### 不変条件

- **INV-E1 加法性**: `base_value + Σ items[].contribution + other_contribution == score`（rel tol 1e-6）。`score` は booster margin（`predict(raw_score=True)`）— cond_logit の race-softmax・isotonic 校正・009 正規化の**前**。
- **INV-E2 副作用ゼロ**: explanation 計算の有無で `win_prob/top2_prob/top3_prob` はバイト一致。
- **INV-E3 決定論**: 同一入力 → 同一 JSONB（top-K は |contribution| 降順 + feature 名昇順タイブレーク）。
- **INV-E4 リーク境界**: items[].feature はモデル入力列のみ（odds/payout/dividend/result トークン不在）。explanation はモデル特徴に戻らない（leak-guard）。
- **INV-E5**: NULL 許容 — 旧 run・degenerate モデル・生成失敗時は NULL（予測本体は保存継続）。

## 2. API 変更（すべて GET、read-only 維持）

### 2a. `GET /api/v1/races/{race_id}/predictions`（既存拡張）

HorsePrediction に追加（どちらも nullable、旧データで欠損安全）:

```json
{
  "horse_number": 7,
  "horse_id": "...",
  "win": 0.21, "top2": 0.38, "top3": 0.52,
  "market_win_prob": 0.31,
  "prior_starts_band": "many",
  "explanation": { ...上記 JSONB をそのまま型付きで... },
  "divergence": "market_higher"
}
```

- `explanation`: persisted JSONB の透過（API で再計算しない）。NULL → null。
- `divergence`: `"market_higher" | "model_higher" | "similar" | null`。
  - バンド（事前登録、FR-011）: p < q − max(0.03, 0.5q) → market_higher / p > q + max(0.03, 0.5q) → model_higher / 他 similar。等号は similar。
  - 抑制: q null または `canonical_consistent=false` → null。
- response 既存の `odds_as_of` / `canonical_consistent` を front バッジが参照（新フィールド不要）。

### 2b. `GET /api/v1/models/{model_version}/importance`（新設、021 calibration 同型）

- 200: `{"model_version": "lgbm-039", "type": "gain", "values": [{"feature": "te_jockey_id", "gain": 1234.5}, ...]}`（gain 降順・feature 昇順タイブレーク、全列）
- 404 `model_not_found` / 404 `importance_unavailable`（未収録）— typed error `{status, code, detail}`。
- クエリなし（top-N 切りは front 表示側）。

## 3. front 表示契約

- **ExplanationPanel**（馬行展開）: items を正負色分け水平バー。ラベルは featureLabels.ts（単一対応表）経由、`derived: true` の特徴（te_*）は「導出特徴」補助バッジ。**限界注記を常時表示**: 「校正・レース内正規化前のスコア寄与」「相関に基づく説明であり因果ではない」。explanation null → 「未提供」。UI 主要語は「スコア寄与」（「根拠」「理由」を見出しに使わない）。
- **ImportanceChart**: 「分割利得（gain）重要度」の限定命名で top-N（20）横棒。未収録 → 「このモデルには重要度が収録されていない」。
- **DivergenceBadge**: 純事実比較文言のみ — market_higher「市場評価がモデルより高い」/ model_higher「モデル評価が市場より高い」/ similar「ほぼ同等」（similar は既定でバッジ非表示も可、目立たせない）。禁止語: 危険/妙味/買い/儲かる/弱気/強気/edge/バリュー。損益色・乖離ソート禁止。ツールチップに「モデルと市場の意見相違であり、的中や利益の保証ではない」+ odds_as_of 時点参照。
- **不変条件テスト（021 同型）**: 「注記なしで寄与・バッジが render されない」を named invariant test で機械検証。

## 4. 特徴ラベル対応表（featureLabels.ts）

```ts
export const FEATURE_LABELS: Record<string, {label: string; derived?: boolean}> = {
  te_jockey_id: {label: "騎手成績（統計）", derived: true},
  te_trainer_id: {label: "調教師成績（統計）", derived: true},
  rel_time_avg: {label: "走破時計（相対・平均）"},
  // ... 全 model_input_features 分を列挙（tasks で全列カバレッジテスト）
};
// 未知キー → {label: featureName}（fail-open、隠さない）
```

## 5. 変更しないもの（回帰境界）

- win/top2/top3 の値・意味・PROB_MONOTONIC 制約
- feature_snapshots の契約（入力スナップショットのまま、explanation を混入しない）
- FEATURE_VERSION / feature_hash / モデル学習・予測ロジック
- API の read-only（全 path GET のみ）・ML 非依存（training/serving を import しない）
- 021 の p/q 併記・PseudoBadge・reliability 表示
