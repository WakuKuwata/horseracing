# Contract: 予測生成エンドポイント (028, ops 経路)

ops(write サービス, owner ロール)に追加。014 read-only API には足さない。024 ops-api.yaml にも本 path を追記し、front の ops 型(ops-openapi.json/ops-schema.d.ts)を同期する。

## C1: エンドポイント
```
POST /ops/v1/races/{race_id}/predict
  path: race_id (JRA-VAN 12桁 or netkeiba 解決済み race_id)
  body: なし（MVP は active モデル固定）
  202 Accepted: { job_id: int, status: "queued"|"running"|..., reused: bool }
  404: race 不在（typed {status, code:"race_not_found", detail}）
  422: race_id 不正（typed）
```
- 受付のみ（同期生成しない）。実行は worker。

## C2: ジョブ（ingestion_jobs 再利用）
- job_type=`predict`, scope=`race`, scope_value=race_id。
- summary: `{kind:"predict", source:"manual", prediction_run_id?, model_version?, n_horses?, reason?, error?}`。
- dedup: 進行中(queued/running)の同一 race predict があれば再利用(reused=true)。完了済みは再生成。advisory lock キー `predict:{race_id}`（refresh と分離）。

## C3: worker / runner 契約
- worker `_CLAIMABLE` に predict 追加、`_run_claimed` dispatch 3-way。run_predict は `(session, job, *, fetcher=None)`(run_one 同型, fetcher 未使用)。
- run_predict（`results = run_serving(session, race_id=scope_value)`、run_serving は per-race persist 済み）:
  - 空 results（started 馬なし＝feature scope 外）→ status=skipped, summary.reason="no started horses"（中途半端な run は残らない）。
  - `ServingError`（active モデル 0/複数）→ catch して status=failed, summary.error（決定論ゆえ worker リトライに回さない）。
  - 非空 results → status=succeeded, summary に prediction_run_id/model_version/n_horses。
  - その他例外 → worker の try/except（retry→failed, error_message）に委譲（サイレント失敗禁止）。

## C4: ジョブ状態取得（既存流用）
- `GET /ops/v1/jobs/{job_id}` で status/summary をポーリング（024 既存）。terminal=succeeded/skipped/failed。

## C5: front 契約
- `predictRace(raceId)` → POST、`getJob(jobId)` ポーリング（既存）。
- terminal 到達で停止。**succeeded 時 `invalidateQueries(["predictions", raceId])`** で 014 予測再取得。
- 受付中は disabled（二重起動防止）。状態ラベル: 受付/生成中/完了/失敗/対象なし。

## C6: 不変条件（テスト）
1. read-only: api は GET のみ（test_no_write_boundary 維持）、predict は ops のみ。
2. dedup: 進行中 predict 二重 enqueue を防ぐ（reused=true）、完了済みは新規。
3. 過去レース: run_predict で過去レースの prediction_run が生成される。
4. entries 不完全: skipped（半端な run を残さない）。
5. active モデル異常: failed + エラー summary。
6. front: PredictButton ポーリング→succeeded で predictions invalidate、失敗表示、受付中 disabled。
7. no-schema-change: head 不変。
