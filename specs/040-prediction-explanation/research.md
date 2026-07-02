# Research: 予測根拠表示 (040)

Phase 0 — 設計上の未知を実コード確認 + codex second opinion で解消した記録。NEEDS CLARIFICATION なし。

## R1. 寄与分解の方式と「分解対象スコア」の正体

**Decision**: `Booster.predict(X, pred_contrib=True)`（LightGBM 内蔵 TreeSHAP、新規依存なし）。分解対象・再構成ターゲットは **`booster.predict(X, raw_score=True)` の margin**（binary: log-odds margin、cond_logit(callable objective): booster 出力そのもの= s_i）。

**Rationale**:
- serving の `raw_predict` は cond_logit で **race-softmax 済み**の値を返す（model_loader.py:49-62）。pred_contrib の合算はこれには一致しない — 一致するのは booster margin。再構成テストは `raw_score=True` に対して行う（codex R1 レシピ）。
- カスタム目的関数は学習時に木が何を学ぶかを変えるだけで、学習済み木集合への寄与分解（PredictContrib path）は目的関数非依存に動く（codex 同意）。ただし T0 spike で実 booster 検証を先行（039 教訓）。
- 寄与計算に使う X は serving `predict_race` が組む **TE 適用後・`model.feature_cols` 順**の行列そのもの（predictor.py:63-68）。特徴順序・TE 注入・欠損処理・iteration が本番 predict と自動的に一致する。

**Alternatives considered**: shap ライブラリ（新規依存+速度、内蔵で足りるため不要）/ permutation importance per-horse（非加法的・遅い）/ 最終確率への寄与再配分（softmax+isotonic の非線形で厳密加法性が消える → 正直さを損なうため不採用、限界注記で対処）。

## R2. 永続化方式

**Decision**: `race_predictions` に nullable JSONB `explanation` 1 列（migration 0008）。serving `persist_run` で RacePrediction と同時書込。旧行 NULL。

**Rationale**: per-(prediction_run_id, horse_id) の 1:1 データで race_predictions と同一 PK・同一ライフサイクル。API は既に race_predictions を SELECT しており読路変更が最小。codex 同意。

**Alternatives considered**:
- `feature_snapshots.features` に `_explanation` キー混入 — スキーマ変更ゼロで魅力的だが、「予測入力スナップショット」という既存契約を汚染し、strict-schema 検証と監査分離（入力 vs 説明）を壊す。却下。
- 新テーブル prediction_explanations — フル寄与ベクトル・複数説明手法・backfill 要件が出た時のみ正当。現要件（top-K 1 手法）には過剰。却下（将来再検討可）。
- serving 側 read endpoint — 予測読み出し経路の二重化で 014 の単一 read-path を破壊。却下（codex 明確同意）。

## R3. explanation JSONB スキーマ（固定・検証可能）

**Decision**（contracts/prediction-explanation.md に契約化）:

```json
{
  "method": "lgbm_pred_contrib",
  "method_version": 1,
  "k": 5,
  "base_value": -3.21,
  "score": -2.47,
  "other_contribution": 0.12,
  "items": [
    {"feature": "te_jockey_id", "value": 0.081, "contribution": 0.42},
    {"feature": "rel_time_avg", "value": -0.35, "contribution": 0.31}
  ]
}
```

- 不変条件: `base_value + Σ items[].contribution + other_contribution == score`（rel 1e-6）— **保存単体で加法性検証可能**（codex R2）。
- top-K 選択 = |contribution| 降順・feature 名昇順タイブレーク（決定論、憲法 V）。
- `value` は serving snapshot と同じ `_jsonable` 変換（NaN→null、category→str）。
- model_version / prediction_run_id / feature_version は**別列・既存 FK で正規化済み**のため JSONB 内に重複させない（feature_snapshots.feature_version が同 PK で参照可能）。method/method_version で説明手法の版だけ持つ。

## R4. グローバル重要度

**Decision**: `save_artifacts` で `booster.feature_importance(importance_type="gain")` を全列分 `metrics_summary["importance"] = {"type": "gain", "values": {feature: gain}}` に追記（93 列で軽量、表示側で top-N）。読み出しは新 `GET /api/v1/models/{mv}/importance` — calibration router（021）と完全同型（存在しないモデル 404 model_not_found / 未収録 404 importance_unavailable）。degenerate モデル（booster なし）は未収録扱い。

**Rationale**: スキーマ変更なし・021 の確立パターン踏襲。gain は高利得分割特徴へ偏るため表示名を「分割利得（gain）重要度」に限定（codex）。

**Alternatives considered**: split 回数 importance（gain より情報少）/ SHAP 平均絶対値のグローバル集計（学習データ全行の pred_contrib が必要=重い、deferred）。

## R5. 乖離バンドの実装位置

**Decision**: `api/selection.py` に純関数 `divergence_band(p, q) -> str | None` を追加し、predictions router が HorsePrediction.divergence（nullable enum: `market_higher` / `model_higher` / `similar`）に結線。q None または canonical_consistent=False → None（バッジ抑制）。バンド定義は spec FR-011 の事前登録値をコード定数化し、境界値テストで固定。

**Rationale**: p, q とも router が既に同一 canonical field で保持（021 機構）。保存不要・純関数（read-only 維持）。オッズ更新でバンドが変わる点は既存 `odds_as_of` の近傍表示で可視化（FR-012b、スナップショット非保存は憲法 V の既存方針）。

## R6. 特徴ラベル対応表

**Decision**: `front/src/components/featureLabels.ts` に単一 map（feature 名 → {label, derived?: boolean}）。TE 列は `te_jockey_id → 騎手成績（統計）+ 導出特徴バッジ`。未知キーは元名 fail-open。ExplanationPanel / ImportanceChart が共有。

**Rationale**: 表示専用のためフロント単一ソースで足りる（API は生 feature 名を返し、変換は表示層 — 監査時に生名が API から見える方が正直）。

**Alternatives considered**: API 側でラベル変換 — 生の feature 名が外から見えなくなり監査性低下。却下。

## R7. degenerate / 欠損の扱い

- booster None（degenerate モデル）→ explanation 生成せず NULL 保存（未提供表示）。
- 取消・除外馬 → そもそも予測行がない（現行どおり）。
- 旧 run → NULL（backfill しない、再予測で自然充足）。

## R8. レイテンシ

pred_contrib は (18 行 × 94 列) 規模で predict の数倍程度・絶対値 ms オーダーの見込み。T0 spike で実測し、1 レース +100ms 未満を確認してから結線（codex 追加リスク対応）。閾値超過時は tasks で対応判断（それでも予測1回あたりの絶対コストは小さい見込み）。

## T0 spike 実測結果（2026-07-02、lgbm-039 active・実 DB 5 レース）= **GO**

```
model=lgbm-039 objective=cond_logit n_features=91 n_encoders=2
race         n   contrib_shape  max_abs_err  max_rel    pred_contrib_latency
202504040303 13  (13, 92)       1.55e-15     8.83e-15   5.8ms
202504040304 15  (15, 92)       1.33e-15     2.23e-15   3.9ms
202508030809 16  (16, 92)       1.50e-15     1.10e-14   4.3ms
202504040301 15  (15, 92)       1.78e-15     3.62e-15   3.9ms
202508030804  8  (8, 92)        1.33e-15     1.89e-15   3.0ms
```

- **INV-E1 加法性成立**: `contrib[:, :-1].sum(1) + contrib[:, -1] == booster.predict(X, raw_score=True)` が max_rel 1.1e-14 ≪ 1e-6（機械精度）。**cond_logit(callable objective) でも pred_contrib は raw margin へ正しく分解**（codex R1 の見立て通り、039 教訓の検証済み）。
- contrib 形状 = (n_horses, n_features+1)、最終列 = base_value。
- feature_cols=91（te_jockey_id/te_trainer_id は feature_cols に含まれる=分解対象、jockey_id/trainer_id 生列は含まれない）。
- **latency 3-6ms/レース ≪ 100ms 目標** → 結線に問題なし。
- 検証 X は serving `predict_race` と同一構築（TE 適用後・feature_cols 順）。**→ 本 feature 続行 GO**。
