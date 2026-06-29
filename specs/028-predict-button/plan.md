# Implementation Plan: レース詳細の予測生成ボタン (Predict Button via ops path)

**Branch**: `028-predict-button` | **Date**: 2026-06-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/028-predict-button/spec.md`

## Summary

レース詳細に「予測する」ボタンを追加し、見ているレースの予測をその場生成・表示する。予測生成(write)は read-only の 014 API ではなく 024 の `ops/` 書き込み経路に新 job_type `predict` として載せる: `POST /ops/v1/races/{race_id}/predict` → `enqueue_predict`(in-flight 限定 dedup) → worker claim → `run_predict`(serving.run_serving 実行→prediction_runs/race_predictions 永続) → front が getJob ポーリング → 成功で `["predictions", raceId]` invalidate。スキーマ変更なし(ingestion_jobs/prediction_runs/race_predictions 再利用)。codex Q1-Q6 反映済み(entries 不完全ガード・active モデル一意性・in-flight dedup)。

## Technical Context

**Language/Version**: Python 3.12(ops, FastAPI)、TypeScript/React(front)

**Primary Dependencies**: ops=FastAPI + SQLAlchemy + horseracing-db、新規に horseracing-serving 依存(run_serving 呼び出し、循環なし)。front=React + @tanstack/react-query + openapi 型(ops-schema)。

**Storage**: PostgreSQL 16。ops=owner ロール(write)、api=app_ro(read-only)。既存 ingestion_jobs(durable queue+監査) / prediction_runs / race_predictions を再利用。**スキーマ変更なし(head 不変)**

**Testing**: ops=pytest(testcontainers; enqueue dedup / worker claim / runner 過去レース予測 / endpoint 契約 / entries 不完全 skipped / active モデル異常 failed)。front=Vitest + RTL + MSW(PredictButton ポーリング/状態/成功 invalidate)。

**Project Type**: ops(write サービス)拡張 + front(SPA)拡張。serving は run_serving を再利用(無改修)。

**Performance Goals**: 予測 1 レース ~数十秒(特徴行列構築) → 非同期ジョブ + ポーリングで吸収(同期応答にしない)。use_materialized 高速化は deferred。

**Constraints**: 014 read-only 不変(write は ops のみ)、リーク境界不変(run_serving の as-of 再利用)、確率整合性不変(009 経由)。

**Scale/Scope**: 新規ファイル少数(ops predict router/enqueue/runner 分岐 + front PredictButton)。新シグナル・新モデルなし。

## Constitution Check

- [x] **I. データ契約**: race_id 12桁・既存 ID 体系。新規結合なし。**PASS**
- [x] **II. リーク防止 (NON-NEGOTIABLE)**: 予測は既存 run_serving の as-of(build_feature_matrix(end_date=race_date)、結果/オッズ非入力)を再利用＝新リーク面なし。**PASS**
- [x] **III. 評価先行**: モデル/特徴の変更なし(既採用 lgbm の予測を生成するだけ)＝評価ゲート対象外。**N/A（モデル変更なし）**
- [x] **IV. 確率整合性**: run_serving が 009 win→joint + check_consistency を通る(既存)。本機能は経路追加のみ。**PASS**
- [x] **V. 再現性・監査**: prediction_runs に model_version/logic_version/computed_at、ingestion_jobs に predict ジョブ監査(status/summary/trace_id)。**PASS**
- [x] **VI. feature 分割規律 / read-only**: 014 は read-only 不変(write を api に足さない＝`test_no_write_boundary` で担保)、write は ops のみ。契約先行(contracts/ops-predict.md + front 型同期)。**PASS**
- [x] **品質ゲート**: codex second opinion 取得・反映済み(Q1-Q6)。research に記録。**PASS**

**Gate result**: PASS。

## 設計詳細（codex 反映）

### ops（write 経路）
- `ops/__init__.py`: `JOB_TYPE_PREDICT = "predict"` 追加。
- `ops/routers/predict.py`(新): `POST /ops/v1/races/{race_id}/predict` → race 存在確認 → `enqueue_predict` → 202 + JobAccepted(job_id/status/reused)。404(存在しない race)/422(不正 id) を typed で返す。
- `ops/enqueue.py`: `enqueue_predict(session, race_id)` — **race 単位 advisory 排他(キー `predict:{race_id}` で refresh と分離)下で、進行中(queued/running)の predict ジョブがあれば再利用(reused=True)、無ければ新規**。完了済みは再利用しない(明示クリックで再生成)。`IngestionJob(job_type=predict, scope=race, scope_value=race_id, summary={"kind":"predict","source":"manual"})`。
- `ops/worker.py`: `_CLAIMABLE` に `JOB_TYPE_PREDICT` 追加。`_run_claimed` の dispatch を 3-way 化(run_day/run_predict/run_one)。runner は `runner(session, job, fetcher=fetcher)` で呼ばれるため **run_predict も `(session, job, *, fetcher=None)` シグネチャ**(fetcher 未使用)にする(analyze A1)。
- `ops/runner.py`: `run_predict(session, job, *, fetcher=None)` — run_one/run_day 同型で status/summary 設定→commit→return。entries ガードは「run_serving の戻りが空(started 馬なし=feature scope 外)なら skipped」で実現(専用 pre-check 不要、analyze A2)。active モデル 0/複数は `load_serving_model` が `ServingError` → run_predict で catch して failed(決定論なので worker リトライに回さない、analyze A3)。それ以外の例外は worker の retry に委ねる。run_serving が per-race persist するので run_predict は別途 persist しない。詳細: — (1)**entries 不完全ガード**: 当該 race に出走馬が無ければ status=skipped + summary 理由(codex Q2)。(2)**active モデル一意性**: run_serving が active 0/複数で例外 → catch して status=failed + summary にエラー(codex risk)。(3)正常: run_serving(session, race_id=race_id) →persist→status=succeeded + summary(prediction_run_id/model_version/horse 数)。
- 依存: `ops/pyproject.toml` に horseracing-serving 追加(ops→serving、循環なし)。
- 注意: run_serving は内部で build_feature_matrix(use_materialized 無し) ＝ in-memory 計算(parquet 化は deferred)。

### front
- `front/src/api/opsClient.ts`: `predictRace(raceId)` → POST /ops/v1/races/{race_id}/predict。
- `front/src/components/PredictButton.tsx`(新): RefreshButton 同型(mutation→job_id→getJob ポーリング→terminal 停止)。**成功時 `qc.invalidateQueries(["predictions", raceId])`**(RefreshButton は ["race"] を invalidate するのに対し、予測は predictions クエリ＝codex risk 反映、queries.ts の実キーを確認)。受付中はボタン無効(二重起動防止)。状態ラベル(受付/生成中/完了/失敗/対象なし)。
- `front/src/pages/RaceDetailPage.tsx`: 予測セクション付近に `<PredictButton raceId={raceId} />` 配置。
- 型同期: ops OpenAPI を front `ops-openapi.json`/`ops-schema.d.ts` に再生成(契約 drift-check)。

### 契約（先行）
- `specs/028-predict-button/contracts/ops-predict.md`: POST /ops/v1/races/{race_id}/predict の req/res・ジョブ状態・エラー(404/422)・dedup 挙動を定義。024 ops-api.yaml にも predict path を追記。

## Project Structure

### Documentation
```text
specs/028-predict-button/
├── plan.md / research.md / data-model.md / quickstart.md
├── contracts/ops-predict.md
└── tasks.md（/speckit-tasks）
```

### Source Code
```text
ops/src/horseracing_ops/
├── __init__.py        # MOD: JOB_TYPE_PREDICT
├── routers/predict.py # NEW: POST /races/{id}/predict
├── enqueue.py         # MOD: enqueue_predict (in-flight dedup)
├── worker.py          # MOD: _CLAIMABLE + claim 分岐
├── runner.py          # MOD: run_predict (entries guard / active-model / run_serving)
├── app.py             # MOD: include predict router
├── schemas.py         # MOD: KindT / JobAccepted 流用
└── pyproject.toml     # MOD: + horseracing-serving 依存

front/src/
├── components/PredictButton.tsx       # NEW
├── components/PredictButton.test.tsx  # NEW (Vitest+RTL+MSW)
├── api/opsClient.ts                   # MOD: predictRace
├── pages/RaceDetailPage.tsx           # MOD: <PredictButton/>
└── api/ops-schema.d.ts / ops-openapi.json  # MOD: 型同期

api/  # 不変（read-only 維持、test_no_write_boundary が担保）
serving/  # 不変（run_serving 再利用）
```

**Structure Decision**: 024 ops パターンの素直な拡張。新規は predict router + run_predict + PredictButton。serving/api は無改修。スキーマ変更なし。

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| ops→serving 依存追加 | 予測生成(run_serving)を worker から呼ぶ | api は read-only で呼べない。serving を ops に取り込むのは循環なしで最小 |
| in-flight 限定 dedup(完了再利用しない) | ingestion_jobs に汎用 payload 列が無く model_version を dedup キーに埋められない(codex Q3) | scope_value 文字列エンコード/ migration は過剰。in-flight 限定で二重起動を防ぎ、明示クリック再生成を許す方が単純で目的に合致 |
