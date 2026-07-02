# Implementation Plan: 予測根拠表示 (Prediction Explanation Display)

**Branch**: `040-prediction-explanation` | **Date**: 2026-07-02 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/040-prediction-explanation/spec.md`

## Summary

表示専用 3 点セット: (US1) 予測時に LightGBM `pred_contrib`（TreeSHAP、新規依存なし）で生スコアへの特徴寄与を分解し top-K+監査付帯を `race_predictions.explanation`（新 nullable JSONB、migration 0008）に永続化 → API SELECT → front 馬行展開で「スコア寄与」表示、(US2) 学習時に gain 重要度を `model_versions.metrics_summary` に追記（スキーマ変更なし）→ `GET /models/{mv}/importance`（021 calibration と同型）、(US3) 保存済み p と 021 の市場 q から読み時純関数で乖離バンドを導出し純事実比較バッジ（保存なし）。モデル・特徴量・確率パイプラインに介入しない（p バイト一致・FEATURE_VERSION 不変）。codex second opinion 反映済み（純事実文言・監査付帯保存・再構成テスト先行・as_of 明示）。

## Technical Context

**Language/Version**: Python 3.12 (uv workspace) + TypeScript (React 18 / Vite)

**Primary Dependencies**: LightGBM（`Booster.predict(pred_contrib=True)` / `feature_importance("gain")`、新規依存なし）、SQLAlchemy 2.0 + Alembic（migration 0008）、FastAPI + pydantic、openapi-typescript（front 型再生成）

**Storage**: PostgreSQL 16 — `race_predictions` に nullable JSONB `explanation` 1 列追加（migration 0008、憲法 VI 正当化は下記）。importance は既存 `model_versions.metrics_summary`(JSONB) 追記。乖離バンドは非保存（読み時導出）

**Testing**: pytest（training/serving/api unit + testcontainers）、Vitest + RTL + MSW（front）、openapi drift-check

**Target Platform**: 既存構成（serving CLI / FastAPI / RaceFront SPA / docker deploy 透過）

**Project Type**: 既存 multi-package repo の横断 feature（db / training / serving / api / front）

**Performance Goals**: pred_contrib は 1 レース（≤18 行 × 93 特徴）で従来 predict に対し体感増なし（実測タスクで確認、目標 <100ms/レース追加）

**Constraints**: API は read-only・ML 非依存のまま（explanation は SELECT のみ）。予測値 p はバイト一致（副作用ゼロ）。乖離バンド閾値は spec FR-011 で事前登録済み・変更禁止

**Scale/Scope**: explanation は新規予測 run からのみ（旧行 NULL=未提供、backfill なし）。JSONB は top-K(5)+付帯で <2KB/行

## Constitution Check

- [x] **I. データ契約**: race_id 12 桁・id_mappings 経由は不変。新カラムは表示派生値のみ、ID 体系に触れない。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: explanation の入力 = 学習済み booster + 予測時に構築済みの as-of 特徴行 X のみ（odds/今走結果は非入力）。explanation/importance/乖離バンドはモデル特徴に戻さない（leak-guard テスト T-LG）。q は表示側でのみ結合、p≠q 分離維持。**PASS**
- [x] **III. 評価先行 (NON-NEGOTIABLE)**: 表示のみで OOS 採否ゲート対象外（021 前例）。代替の機械検証: 再構成（加法性）テスト・p バイト一致・バンド決定論。乖離バンド閾値は spec で事前登録し結果を見て動かさない。**PASS**
- [x] **IV. 確率整合性**: win→joint(009) 非介入。explanation は予測値を変えない読み出し専用の追加計算（p バイト一致テストで機械保証）。**PASS**
- [x] **V. 再現性・監査**: explanation に method/method_version/base/score/other を保存し**保存単体で加法性検証可能**（codex R2）。run/model への紐付けは既存 PK（prediction_run_id, horse_id）で担保。q 由来バッジは odds_as_of を近傍表示（FR-012b）。gain 重要度は「分割利得重要度」と限定命名。**PASS**
- [x] **VI. feature 分割規律**: API 契約（OpenAPI/front 型）先行で front に着手。migration 0008 は Complexity Tracking で正当化。**PASS**
- [x] **品質ゲート**: codex second opinion 取得済み（spec 段階）。相違点と採用: 「弱気/強気」文言却下→純事実比較（採用）、監査付帯フィールド保存（採用）、再構成テストレシピ（採用）、導出特徴バッジ（採用）、as_of 明示（採用）。**PASS**

## Project Structure

### Documentation (this feature)

```text
specs/040-prediction-explanation/
├── plan.md              # This file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   └── prediction-explanation.md   # explanation JSONB / API / バンド / ラベル対応の契約
└── tasks.md             # (/speckit-tasks)
```

### Source Code (repository root)

```text
db/
├── migrations/versions/0008_prediction_explanation.py   # race_predictions.explanation JSONB (nullable)
└── src/horseracing_db/models/prediction.py              # RacePrediction.explanation 追加

training/src/horseracing_training/
├── explanation.py        # 新: compute_contributions(booster, X, feature_cols, k) → ExplanationPayload
│                         #     (pred_contrib 実行・top-K 決定論選択・加法性自己検証・JSON 形成)
└── artifacts.py          # save_artifacts: metrics_summary["importance"] = {"type":"gain", values} 追記

serving/src/horseracing_serving/
├── predictor.py          # predict_race: booster あり時に explanation を計算し per-horse で返す
└── persistence.py        # persist_run: RacePrediction(explanation=...) で保存

api/src/horseracing_api/
├── schemas.py            # HorsePrediction.explanation / .divergence、ImportanceResponse
├── selection.py          # divergence_band(p, q, canonical_consistent) 純関数（FR-011 事前登録バンド）
├── queries.py            # run_predictions に explanation 列追加
└── routers/
    ├── predictions.py    # explanation/divergence を response に結線
    └── importance.py     # 新: GET /models/{mv}/calibration と同型の /importance

front/
├── openapi.json          # ライブ OpenAPI から再生成（drift-check 維持）
├── src/api/types.ts      # openapi-typescript 再生成
└── src/components/
    ├── ExplanationPanel.tsx   # 新: 馬行展開のスコア寄与バー（日本語ラベル・導出特徴バッジ・限界注記）
    ├── ImportanceChart.tsx    # 新: gain 重要度横棒（限定命名）
    ├── DivergenceBadge.tsx    # 新: 純事実比較バッジ（as_of 参照・抑制）
    └── featureLabels.ts       # 新: 特徴名→日本語ラベル単一対応表（fail-open）

tests（各パッケージ）:
├── training: test_explanation.py（再構成/決定論/degenerate）
├── serving:  test_predict_explanation.py（p バイト一致・persist・cond_logit margin）
├── api:      test_importance.py / test_divergence.py（バンド境界値・抑制・read-only 維持）
├── features: leak-guard 拡張（explanation トークンがモデル特徴に無い）
└── front:    ExplanationPanel/ImportanceChart/DivergenceBadge .test.tsx（注記必須の不変条件）
```

**Structure Decision**: 寄与計算ロジックは training（booster を持つ層）に置き serving が呼ぶ（TE encoders/feature_cols と同じ依存方向、api には持ち込まない）。API は persisted JSONB の SELECT と純関数バンドのみ（read-only/ML 非依存維持）。

## 実装順（リスク先頭）

1. **T0 de-risk spike（最初、他の全てをブロック）**: 実 DB の lgbm-039 booster で `pred_contrib` 再構成検証 — `contrib[:, :-1].sum(axis=1) + contrib[:, -1] ≈ booster.predict(X, raw_score=True)`（rel 1e-6）を serving と同一の X（TE 適用後・feature_cols 順）で確認。**不成立なら中断して原因究明**（spec Assumptions、codex R1 レシピ）。あわせてレイテンシ実測。
2. db: migration 0008 + ORM（explanation nullable JSONB）。
3. training: explanation.py（純関数）+ artifacts の importance 追記。
4. serving: predict_race → persistence 結線（p バイト一致テスト同時）。
5. api: schemas/queries/routers + divergence 純関数（openapi.json 更新）。
6. front: 型再生成 → featureLabels → 3 コンポーネント → ページ結線。
7. 横断: leak-guard・read-only・注記不変条件・quickstart 実データ検証。

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| migration 0008（`race_predictions.explanation` JSONB 追加、012/016 以来のスキーマ変更） | per-(run, horse) の構造化根拠は予測時にしか計算できず（API は read-only かつ ML 非依存で読み時計算不可）、予測行と同一 PK・同一ライフサイクルで保存する必要がある | (a2) `feature_snapshots.features` に `_explanation` キーを混入 → スキーマ変更ゼロだが「入力スナップショット」契約を汚染し strict-schema 検証（codex）と監査分離を壊すため却下。(b) 新テーブル → 同一 PK の 1:1 データに新テーブルは過剰（フル寄与ベクトル等の将来要件が出た時に再検討）。(c) serving 側 read endpoint → 予測読み出し経路が二重化し 014 の単一 read-path を壊すため却下（codex 同意） |
