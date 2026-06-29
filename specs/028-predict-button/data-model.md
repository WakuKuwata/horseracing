# Phase 1 Data Model: 予測生成ボタン (028)

スキーマ変更なし（migration head 不変）。既存テーブルを再利用する。新規は in-memory/契約レベルのみ。

## 既存 DB（再利用・無改修）

### ingestion_jobs（024 の durable queue + 監査）
| 列 | 028 での用途 |
|----|----|
| job_type | **`predict`**（新値・スキーマ enum 制約なしの文字列） |
| scope | `race` |
| scope_value | race_id |
| status | queued → running → (succeeded / skipped / failed) |
| summary | `{kind:"predict", source:"manual", prediction_run_id?, model_version?, n_horses?, error?}` |
| trace_id / retry_count | 既存（監査） |

### prediction_runs（serving が persist・無改修）
model_version / logic_version / computed_at / prediction_run_id（監査・再現性）。

### race_predictions（serving が persist・無改修）
馬別 win/top2/top3（014 が表示に読む）。

## 契約レベル・エンティティ

### PredictJobRequest / JobAccepted（ops API）
- req: path race_id（body 無し、MVP は active モデル固定）。
- res(202): `{ job_id, status, reused }`（024 JobAccepted 流用）。
- err: 404（race 不在）/ 422（race_id 不正）。

### run_predict 状態遷移（runner）
```
claim(queued→running)  # run_predict(session, job, *, fetcher=None) — run_one 同型
  results = run_serving(session, race_id=scope_value)   # per-race persist 済み
  ├─ 空 results (started 馬なし)     → skipped (summary.reason="no started horses")
  ├─ ServingError (active 0/複数)    → failed  (catch, summary.error, リトライ不可)
  ├─ 非空 results                    → succeeded(summary: prediction_run_id/model_version/n_horses)
  └─ その他例外                      → worker retry→failed (error_message)
```

### PredictButton（front 状態）
idle → 受付中(queued) → 生成中(running) → 完了(succeeded, predictions invalidate) / 失敗(failed, 理由表示) / 対象なし(skipped)。受付中は disabled。

## Validation / 不変条件（FR 対応）
- read-only 不変（FR-007）: api に write エンドポイント 0（test_no_write_boundary 維持）。
- entries ガード（FR-003）: 不完全 → skipped、半端な run を残さない。
- active モデル一意（FR-004）: 0/複数 → failed + メッセージ。
- in-flight dedup（FR-004）: 進行中 predict があれば再利用、二重起動なし。
- 監査（FR-008）: ingestion_jobs + prediction_runs に記録。
- スキーマ不変（FR-009）: head 不変・新 `__tablename__` 無し。
- リーク不変（FR-012）: run_serving の as-of を再利用、新計算なし。
