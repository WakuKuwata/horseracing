# Phase 1 Data Model: netkeiba データ更新ボタン

**スキーマ変更なし。** 既存 `ingestion_jobs`（migration 0002 + 0004）と `pg_advisory_xact_lock` のみを使う。本書は「既存テーブルをどう使うか」の利用契約を定義する。

## エンティティ: 更新ジョブ（Refresh Job）= `ingestion_jobs` の 1 行

| 概念 | `ingestion_jobs` カラム | 本機能での値 |
|------|------------------------|--------------|
| 種別 | `job_type` | `refresh_race`（子/単体）/ `refresh_day`（親） |
| 取得元 | `source` | `netkeiba` |
| スコープ種別 | `scope` | `race` / `day` |
| スコープ値 | `scope_value` | race の 12桁 `race_id` / `YYYY-MM-DD` |
| 状態 | `status` | `queued`→`running`→`succeeded`/`partial`/`failed`/`skipped` |
| 親子束ね | `trace_id` | 日次バッチ ID（親・子で共有。単体 refresh_race は自分の job_id を trace_id にしてもよい） |
| リトライ | `retry_count` / `max_retry` | 宙吊り `running` 復帰・上限超過で `failed` |
| 監査件数 | `processed_rows` / `skipped_rows` / `error_count` | scrape の書込/スキップ/エラー件数 |
| 結果詳細 | `summary` (JSONB) | `{kind: "entries+odds"|"results", parser_version, written:{...}, reused?:bool}` |
| 時刻 | `started_at` / `completed_at` / （`created_at` mixin） | 実行・完了時刻 |
| エラー | `error_message` | 失敗時の先頭メッセージ集約（既存 scrape 挙動） |

### 取得種別（`summary.kind`）の決定
worker 実行直前に `race_results` 行の有無で判定（D5）:
- 行なし（result-pending）→ `kind="entries+odds"`（`scrape_entries` + `scrape_odds`、URL は `entries_url(race_id)` / odds API）
- 行あり（確定後）→ `kind="results"`（`scrape_results`、URL は `result_url(race_id)`）

## 状態遷移（Refresh Job）

```
            enqueue                 worker pickup            scrape 実行
 (なし) ──────────────▶ queued ──(FOR UPDATE SKIP LOCKED)──▶ running ──┬──▶ succeeded   (全件取得 OK)
                          │                                            ├──▶ partial     (一部のみ取得)
                          │                                            ├──▶ failed      (取得失敗/例外, retry_count<max なら queued へ戻す)
   dedup 再利用 ◀─────────┘                                            └──▶ skipped     (対象なし/netkeiba 未公開 等)

 宙吊り回復: worker 起動時、しきい時間超過の running → retry_count+1 で queued（max_retry 超過は failed）
```

**終端状態**: `succeeded` / `partial` / `failed` / `skipped`。front はこれらで polling を止め、表示を再取得（succeeded/partial）。

## エンティティ: 一括更新（Day Refresh Batch）= 同一 `trace_id` のジョブ群

- 親 `refresh_day` 1 行＋子 `refresh_race` N 行を共通 `trace_id` で束ねる。
- バッチ全体状態は子ジョブ status の集約で導出（永続化しない＝計算で出す）:
  - 全子が `succeeded` → batch `succeeded`
  - 1 件以上終端だが失敗/部分あり → batch `partial`
  - 未終端の子が残る → batch `running`
- 対象レース列挙: ops が DB を読み、その日の全レース `race_id` を取得（result-pending も確定後も含む）。`is_valid_race_id` を通った 12 桁のみ。
- 失敗分の再実行: 失敗した子レースの `POST /races/{race_id}/refresh` を再 enqueue するだけ（新しい子ジョブが同 race_id で作られる）。

## dedup（重複起動の排他）

- enqueue は `pg_advisory_xact_lock(key)` 配下で:
  - `key = hashtext('refresh:race:' || race_id)`
  - active（`queued`/`running`）な同 race ジョブがあれば、その job を `reused=true` で返す（新規 INSERT しない）。
  - active が無く、直近 N 分以内に `succeeded` な同 race ジョブがあれば、`force=false` の場合はその job を `reused=true` で返す。
  - 上記いずれにも該当しなければ新規 `queued` を INSERT。
- `force=true`: 鮮度（succeeded）判定を無視。ただし active ジョブは常に再利用（多重取得を絶対に作らない）。

## バリデーション規則（要件→実装）

| 規則 | 由来 | 実装 |
|------|------|------|
| 12桁 race_id のみ／非存在拒否 | FR-019 | `is_valid_race_id` + DB 存在確認、不正は 422、未存在は 404（取得を起動しない） |
| 確定結果は追記のみ | FR-018 | `scrape_results` 既存の INSERT-only（JRA-VAN 保護）をそのまま使用 |
| 事前オッズ上書きは result-pending のみ | FR-018 | 種別判定（D5）で確定後は odds 更新を行わない |
| odds/results を特徴量にしない | FR-020 | ops/worker は training/eval/feature を import しない（leak-guard テスト） |
| read 経路に write を増やさない | FR-021 | 014 は無変更、ops/worker のみ owner ロール。014 の全 GET テストは不変 |
| 推定/疑似値ラベル維持 | FR-022 | front は 014 のデータ表示のまま（PseudoBadge 経路不変） |

## 関連既存エンティティ（読むだけ）

- **Race / RaceHorse / RaceResult**: 対象レースの存在確認・result-pending 判定・列挙に使用（ops の owner セッションで read）。書き込みは `scrape/` 経由のみ。
- **IdMapping**: 馬/騎手/調教師の ID 解決は `scrape/` の既存挙動（`id_mappings` 経由）に委譲。本機能で新たな結合ロジックは作らない。
