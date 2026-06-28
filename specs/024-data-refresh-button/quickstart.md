# Quickstart / 検証ガイド: netkeiba データ更新ボタン

end-to-end で「ボタン→受付→worker 取得→表示更新」を検証する手順。実装の詳細コードは tasks.md / 実装フェーズに置く。本書は前提・起動・確認の運び方のみ。

## 前提

- ローカル Postgres（`horseracing` DB、`localhost:15432`、owner=aiuma）。`DATABASE_URL_OWNER` を owner ロールに、`DATABASE_URL` を read-only ロール(app_ro)に向ける。
- 既存スキーマが head（`alembic upgrade head`）。**本機能はマイグレーション追加なし**。
- 014 API と front は既存どおり起動可能（[front 既存 quickstart 参照]）。
- netkeiba 実取得を伴うため、テストでは保存済み HTML/JSON フィクスチャ＋スタブ fetcher を使い**ネットワーク不要**にする（008/022 のフィクスチャ方式を踏襲）。

## 起動（ローカル）

1. **014 read-only API**（表示用、app_ro）: 既存どおり `uvicorn horseracing_api.app:app`（変更なし）。
2. **ops write API**（owner）: `uvicorn horseracing_ops.app:app --port 8001`（`DATABASE_URL_OWNER` を使用）。
3. **worker**（owner、常駐）: `python -m horseracing_ops.worker`（`FOR UPDATE SKIP LOCKED` でポーリング）。
4. **front**: 既存 dev サーバ。Vite proxy に `/ops` → `http://localhost:8001` を追加。

## 検証シナリオ

### US1: 詳細ページで 1 レース更新（P1, MVP）

1. result-pending な race の詳細を front で開く。
2. 「このレースを更新」を押す → `POST /ops/v1/races/{race_id}/refresh` が **202 + job_id** を即返す（UI は「受付済み」）。
3. front が `GET /ops/v1/jobs/{job_id}` を 1〜2 秒間隔で polling → `queued`→`running`→終端へ遷移。
4. worker は実行直前に result-pending を再判定し `kind="entries+odds"` で `scrape_entries`+`scrape_odds` を実行。
5. 終端 `succeeded`/`partial` で front が 014 の race detail を再取得 → 出走馬/オッズが最新化。
- **期待**: ボタン押下から受付表示まで体感即時（SC-001）。完了後に表示データが変わる（SC-002）。
- **確定後レース**で同操作 → `kind="results"`、結果が反映。既存確定データは壊れない（SC-005, FR-018）。

### US2: 一覧でその日を一括更新（P2）

1. 開催日の一覧を開き「この日を更新」→ `POST /ops/v1/days/{date}/refresh` が **202 + trace_id + children[]**。
2. front が `GET /ops/v1/batches/{trace_id}` を polling → レース単位の status が一覧上で区別表示。
3. 一部の子が `failed` → 失敗レースだけ `POST /races/{id}/refresh` で再実行できる（FR-010）。
- **期待**: レース別の成功/失敗が見え、失敗分のみ再実行できる（SC-003）。netkeiba 同時アクセスは上限内（FR-016）。

### US3: 重複起動の抑制と監査（P3）

1. 同一レースの更新を短時間に連打 → 2 回目以降は **`reused=true`** で既存 job_id が返り、取得は多重に走らない（SC-004, FR-015）。
2. 直近成功して新鮮なレースを `force=false` で更新 → 再取得されず直近結果が返る。`force=true` で再取得。
3. `ingestion_jobs` を直接参照 → 各更新の `job_type`/`scope_value`/`status`/件数/時刻/`summary` が記録されている（SC-007, FR-017）。

## 不変条件テスト（必須）

- **readonly 不変**: 014 の全エンドポイントが GET のみ・行数不変であることが本機能追加後も維持（既存 `test_readonly_invariant.py` が通る）。ops にボタン経路を足しても 014 は無変更。
- **leak-guard**: ops/worker パッケージが `horseracing_training`/`horseracing_eval`/特徴量経路を import しない（import グラフ検査）。取り込んだ odds/results が特徴量に流入しない（FR-020, 憲法 II）。
- **dedup 競合**: 同一 race への同時 enqueue（並行）で `queued` 行が 1 件しか増えないこと（advisory lock）。
- **種別判定**: result-pending と確定後で `kind` が正しく分岐し、確定後に事前オッズを上書きしないこと（FR-018）。
- **front 型 drift**: ops の committed OpenAPI スナップショットと生成型が一致（015 同様の drift-check）。
- **front 状態**: 読み込み中／空／型付きエラー／受付中／取得中／一部失敗／失敗／対象なし(skipped) の UI 状態が区別される（FR-008, FR-013）。
