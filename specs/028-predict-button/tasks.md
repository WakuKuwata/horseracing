---
description: "Task list — レース詳細の予測生成ボタン (028)"
---

# Tasks: レース詳細の予測生成ボタン (Predict Button via ops path)

**Input**: [plan.md](plan.md) / [spec.md](spec.md) / [research.md](research.md) / [data-model.md](data-model.md) / [contracts/ops-predict.md](contracts/ops-predict.md) / [quickstart.md](quickstart.md)

**Tests**: read-only 境界・dedup・過去レース予測・entries/active モデル ガード・front ポーリングが核のため**テスト中核**。

**Organization**: user story 単位。MVP = US1（予測その場生成・表示）+ US3（read-only 境界・監査）。

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [X] T001 前提確認: 024 ops 基盤(enqueue/worker/runner/jobs ルータ・front RefreshButton/opsClient)と lgbm-026 採用、horseracing DB head 不変を確認。**境界テスト発覚により ops→serving 依存は追加しない**(C: subprocess。`test_boundary.py` が serving import 禁止、R1b)
- [X] T002 [P] [contracts/ops-predict.md](contracts/ops-predict.md) の endpoint/req-res・ジョブ状態・dedup・エラー(404/422)・不変条件を確定(契約先行、憲法 VI)

## Phase 2: Foundational（全 US の前提）

- [X] T003 `ops/src/horseracing_ops/__init__.py`: `JOB_TYPE_PREDICT = "predict"` 追加。`ops/schemas.py`: KindT に predict を追加(JobAccepted は流用)
- [X] T004 `ops/src/horseracing_ops/enqueue.py`: `enqueue_predict(session, race_id)` — advisory 排他キー `predict:{race_id}`(refresh と分離)下で**進行中(queued/running)の predict ジョブを再利用、無ければ新規**(完了済みは再利用しない)。`IngestionJob(job_type=predict, scope=race, scope_value=race_id, summary={"kind":"predict","source":"manual"})`。(job, reused) を返す

**Checkpoint**: predict ジョブの enqueue/dedup 土台が揃う。

---

## Phase 3: User Story 1 - 予測その場生成・表示 (P1, MVP)

**Goal**: ボタン→ジョブ→worker→予測生成→画面自動更新。

**Independent Test**: 予測の無い実レースで「予測する」→完了後に予測セクション表示(再読込不要)。

### 実装
- [X] T005 [US1] `ops/src/horseracing_ops/runner.py`: `run_predict(session, job, *, fetcher=None)`(run_one 同型, fetcher 未使用)。**境界保持(C)**: serving を import せず `_serving_predict(race_id)` で serving CLI を subprocess 起動(`uv run --project serving ... predict --race-id`、cwd=serving/ で weights_uri の `../artifacts` 解決、VIRTUAL_ENV 除外、monkeypatch 可)。CLI 結果→status: rc≠0→failed(summary.error, リトライ不可)・rc0+"no races inferred"→skipped(reason)・他→succeeded(summary.output)。commit→return
- [X] T006 [US1] `ops/src/horseracing_ops/worker.py`: `_CLAIMABLE` に JOB_TYPE_PREDICT 追加、`_run_claimed` の dispatch を 3-way(`run_day if DAY elif PREDICT run_predict else run_one`)に。`runner(session, job, fetcher=fetcher)` 呼び出し形は不変
- [X] T007 [US1] `ops/src/horseracing_ops/routers/predict.py`(新) + `app.py` に include: `POST /ops/v1/races/{race_id}/predict` → race 存在確認(404)→enqueue_predict→202 JobAccepted。race_id 不正→422
- [X] T008 [US1] `front/src/api/opsClient.ts`: `predictRace(raceId)`(POST)。`front/src/components/PredictButton.tsx`(新): RefreshButton 同型(mutation→job_id→getJob ポーリング→terminal 停止)、**成功で `invalidateQueries(["predictions", raceId])`**(queries.ts の実キー確認)、受付中 disabled、状態ラベル(受付/生成中/完了/失敗/対象なし)
- [X] T009 [US1] `front/src/pages/RaceDetailPage.tsx`: 予測セクション付近に `<PredictButton raceId={raceId} />` 配置

### US1 テスト
- [X] T010 [P] [US1] `ops/tests/integration/test_predict_flow.py`: 過去レースで enqueue→worker→run_predict→prediction_runs/race_predictions 生成・status=succeeded(testcontainers、run_serving は軽量 fake or 小データ)
- [X] T011 [P] [US1] `front/src/components/PredictButton.test.tsx`(新, Vitest+RTL+MSW): 押下→ポーリング(queued→running→succeeded)→predictions invalidate、受付中 disabled

**Checkpoint**: 予測のその場生成・自動表示が成立(MVP の核)。

---

## Phase 4: User Story 3 - read-only 境界と監査 (P1, MVP)

**Goal**: 014 read-only 不変・predict は ops のみ・監査記録。

**Independent Test**: api は GET のみ(既存テスト維持)、predict 後 ingestion_jobs/prediction_runs に監査。

### US3 テスト
- [X] T012 [P] [US3] `ops/tests/integration/test_predict_contract.py`: endpoint 契約(202/404/422)、ingestion_jobs に job_type=predict 監査行(status/summary)、prediction_runs に model_version/computed_at
- [X] T013 [P] [US3] read-only 不変: `api` の `test_no_write_boundary`(全ルート GET) が predict 追加後も緑であることを確認(api 無改修)。front が write するのは ops のみ(opsClient 経由)

**Checkpoint**: write 経路分離と監査が保証。

---

## Phase 5: User Story 2 - ジョブ状態と失敗の明示 (P1)

**Goal**: 受付/生成中/完了/失敗/対象なし と失敗理由の明示。

**Independent Test**: 各状態でラベル変化、entries 不完全→対象なし、active 異常→失敗+理由。

### US2 テスト
- [X] T014 [P] [US2] `ops/tests/integration/test_predict_guards.py`: entries 不完全(未来レース 出走馬なし)→skipped+reason、active モデル 0/複数→failed+error、存在しない race→404
- [X] T015 [P] [US2] `front/src/components/PredictButton.test.tsx`(追記): failed→「生成失敗」+理由表示・再実行可、skipped→「対象なし」表示、typed error ハンドリング

**Checkpoint**: 非同期の状態・失敗が常に可視。

---

## Phase 6: Polish & 横断

- [X] T016 [P] dedup/競合テスト `ops/tests/integration/test_predict_dedup.py`: 進行中 predict の二重 enqueue は reused=true、完了済みは新規生成、predict と refresh の同一レース同時 enqueue が競合しない(別 advisory キー)
- [X] T017 [P] 型同期: ops OpenAPI を front `ops-openapi.json`/`ops-schema.d.ts` に再生成し `pnpm run check-openapi`(drift-check) 緑。`specs/024-data-refresh-button/contracts/ops-api.yaml` に predict path 追記
- [X] T018 [P] lint/test ゲート: `ops` `uv run ruff check && uv run pytest` 緑、`front` `pnpm test` 緑、`api`/`serving` 既存テスト透過で緑
- [X] T019 実 e2e スモーク([quickstart.md](quickstart.md)): API+ops+front 起動、予測の無い実レースで「予測する」→完了→予測セクション表示・ingestion_jobs/prediction_runs 監査確認(スクショ)
- [X] T020 [P] `CLAUDE.md` に 028 の 1 行サマリ追記(014–026 と同形式: 予測ボタンは ops 経路 predict job・run_serving 再利用・過去/未来対応・entries/active ガード・in-flight dedup・014 read-only 不変・スキーマ変更なし)
- [X] T021 codex 反映確認: 実装が codex Q1-Q6(read-only 境界/entries ガード/active モデル/in-flight dedup/predictions invalidate/source=manual)に沿うことを最終確認、差分あれば research 追記

---

## Dependencies & Execution Order

- **Phase 1 → 2**: Setup(serving 依存) → Foundational(T003 定数・T004 enqueue)が全 US をブロック。
- **Phase 3 (US1)**: T005→T006→T007(ops)→T008→T009(front)、テスト T010/T011。MVP は US1+US3。
- **Phase 4 (US3)**: テスト T012/T013。US1 後。
- **Phase 5 (US2)**: テスト T014/T015。US1 後。
- **Phase 6**: 全実装後。T016/T017/T018/T020[P]、T019、T021。

### User Story 独立性
- US1(その場生成・表示)=核。US3(read-only/監査)=境界保証＝MVP 必須同梱。US2(状態/失敗明示)=UX 完成度、独立テスト可能。

## Parallel 実行例
- US1 テスト T010(ops)/T011(front)[P]。Polish T016/T017/T018/T020[P]。

## 実装戦略
1. **MVP**: Phase 1→2→3→4(その場生成・表示＝US1、read-only/監査＝US3)。
2. **UX**: US2 で状態・失敗明示。
3. 各 Checkpoint で独立テスト緑。憲法 VI(read-only・契約先行) / V(監査) / II(run_serving の as-of 再利用＝新リーク無し) / IV(009 不変) を維持。**最優先 release gate = 014 read-only 不変 + 予測その場生成・表示**。

## analyze 反映（inline 実行・findings 解消）
- **A1 (MEDIUM, シグネチャ不一致)**: worker dispatch は `runner(session, job, fetcher=fetcher)`。run_predict を `(session, job, *, fetcher=None)` に修正(当初の `predict_fn=` 案は dispatch と不整合) → T005/T006/plan 修正。
- **A2 (MEDIUM, ガード簡素化)**: run_serving は started 馬が無い race を `present` から除外し**空 results** を返す(per-race persist は no-op)。よって「entries 不完全」は専用 pre-check 不要＝**空 results→skipped** で実現 → T005/plan/research 修正。
- **A3 (MEDIUM, エラー分類)**: active モデル 0/複数は `load_serving_model` の `ServingError`。run_predict で **ServingError を catch して failed(リトライ不可)**、その他例外のみ worker retry。→ T005/plan 修正。
- **A4 (LOW, 確認のみ)**: front `usePredictions` キーは `["predictions", raceId, joint]`。`invalidateQueries(["predictions", raceId])` は prefix 一致で当たる(修正不要)。
- **A5 (LOW, 確認のみ)**: `api/tests/unit/test_no_write_boundary.py` 実在(T013 参照は有効)。
- codex Q1-Q6 反映済(entries skipped・active failed・in-flight dedup・predictions invalidate・source=manual)。スキーマ変更なし・新ソース無し。

## 注意
- run_serving は use_materialized 未接続(in-memory, ~数十秒) → 非同期+ポーリングで吸収。parquet 高速化・predict_day バッチは deferred。
- 過去レース予測は最新採用モデルで上書き(目的に合致、限界として開示)。
