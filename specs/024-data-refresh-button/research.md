# Phase 0 Research: netkeiba データ更新ボタン

設計の非自明点について 2 回の `codex:codex-rescue` second opinion（① spec 前のアーキ方針、② plan 前の実装論点）を取得し、現状コード（`scrape/`・`live/`・`api/`・`deploy/`・`db/` モデル）を確認した上で確定した判断を記録する。

## D1. 書き込み経路の置き場所

- **Decision**: 014 read-only API には write を一切足さず、新 `ops/` write サービス（FastAPI、別アプリ・別ポート `/ops/v1/`）を新設。front のボタンは ops へ POST、表示は引き続き 014 を GET。
- **Rationale**: 014 は `test_no_write_boundary.py`（AST + import グラフで commit/flush/add・ingest/betting import・write DML を禁止）と `test_readonly_invariant.py`（全 GET・行数不変）で read-only を機械的に強制。さらに 018 本番では API が DB ロール `app_ro`（SELECT 専用）で動くため、write 口を足しても DB が拒否する。経路を分ければ read/write が物理分離でき憲法 VI に整合。
- **Alternatives considered**: 014 に POST を追加（テスト・app_ro・憲法に全て抵触＝却下）。CLI/operator のみ（要望のボタンを満たさない＝却下）。

## D2. ジョブ実行形態

- **Decision**: ops API は `ingestion_jobs` に `status=queued` 行を INSERT して即 202 を返すだけ。別の常駐 **worker** プロセス（同一 repo の CLI、owner ロール）が `SELECT … FOR UPDATE SKIP LOCKED` でジョブを取り出して実行する。耐久キュー＝`ingestion_jobs`。
- **Rationale**: netkeiba 取得は rate-limit 前提で遅く失敗もあるため、リクエスト寿命に縛られる in-process 実行は不適。API 再起動で取得中ジョブが宙吊り・無監査になる。`ingestion_jobs` は既に `retry_count`/`max_retry`/`checkpoint`/`status` を持ち、耐久キューとして追加コストがほぼ無い。外部ブローカ（Celery/RQ）は単一インスタンス運用に対し過剰。
- **宙吊り対策**: worker 起動時、古い `running`（しきい時間超過）を `retry_count+1` で `queued` に戻す。`max_retry` 超過で `failed`。
- **Alternatives considered**: (a) FastAPI BackgroundTasks / asyncio task（最小だが再起動で消失・無監査＝MVP でも却下）。(c) Celery/RQ + ブローカ（運用過剰＝却下）。

## D3. 重複起動の排他と鮮度判定（dedup）

- **Decision**: enqueue 時に `pg_advisory_xact_lock(hash('refresh:race:{race_id}'))` を取得し、同一レースに (i) active なジョブ（queued/running）があればそれを再利用して返す、(ii) 直近 N 分以内に `succeeded` なジョブがあれば再取得せず直近結果を返す。`force=true` 指定時は (ii) の鮮度判定のみ無視（active ジョブは常に再利用）。
- **Rationale**: `ingestion_jobs` に race 単位の UNIQUE 制約が無いため `SELECT FOR UPDATE` 単独では同時 enqueue の競合を防げない。advisory lock はスキーマ無改変で確実な相互排他を与える。鮮度しきい値 N は既定値（例 10 分）を設定ファイルで調整可能にする（spec Assumptions）。
- **Alternatives considered**: `ingestion_jobs` への部分 UNIQUE インデックス追加（スキーマ変更＝憲法 VI で要正当化、advisory lock で代替可能＝却下）。

## D4. 一覧＝日次バッチの親子表現と並列度

- **Decision**: `job_type=refresh_day` の親ジョブ 1 件＋レース単位の `job_type=refresh_race` 子ジョブ群を、同じ `trace_id` で束ねる。対象 race_id は ops が DB を読んで列挙（`live.list_pending` 同等のロジック＋確定後レースも含めて全レース）。worker の同時実行数に上限を設け、`HttpFetcher` のドメイン rate-limit と二重に netkeiba 負荷を抑える。失敗した子ジョブだけの再実行は、その race の refresh を再 enqueue するだけで成立。
- **Rationale**: `trace_id` は既存カラムで親子束ねに使え、新テーブル/新カラム不要（憲法 VI）。バッチ全体状態は子ジョブ status の集約で算出（`GET /batches/{trace_id}`）。
- **Alternatives considered**: 新 `refresh_batches` テーブル（スキーマ変更＝却下）。

## D5. 取得種別の出し分けタイミング

- **Decision**: enqueue 時点では種別を確定せず、**worker 実行直前に再判定**する。`race_results` 行が無い（result-pending）＝出馬表+オッズを取得、行が有る（確定後）＝結果を取得。
- **Rationale**: enqueue から実行までの待機中にレース状態が変わり得る（result-pending→確定）。`live/guards.py` の `is_result_pending` と同じ判定を実行直前に行えば不整合な書き込みを避けられる。書き込み規則は 008 既存（結果は INSERT-only で JRA-VAN 保護、事前オッズは result-pending のみ上書き）をそのまま適用。
- **Alternatives considered**: enqueue 時固定（待機中の状態変化で誤種別＝却下）。

## D6. ロール分離

- **Decision**: ops API と worker は `DATABASE_URL_OWNER`（owner ロール、read+write）で動かす。014 API は `DATABASE_URL`（app_ro、SELECT 専用）のまま不変。ops 内の read（result-pending 判定・対象レース列挙）は「書き込み前提の内部判定」専用で、front 表示用の read には使わない（表示は 014 経由）。
- **Rationale**: 018 が既に owner/app_ro を分けている。経路ごとにロールを割り当てれば read/write の物理分離が DB レベルで担保される。
- **Alternatives considered**: ops も app_ro（書けない＝却下）。

## D7. ops API の契約（OpenAPI）

- **Decision**: `/ops/v1/` 名前空間で 014 と完全分離。エンドポイントは最小 4 本:
  - `POST /ops/v1/races/{race_id}/refresh`（任意 body `{force?: bool}`）→ 202 受付
  - `POST /ops/v1/days/{date}/refresh`（任意 body `{force?: bool}`）→ 202 受付（親ジョブ＋子ジョブ）
  - `GET /ops/v1/jobs/{job_id}` → ジョブ状態
  - `GET /ops/v1/batches/{trace_id}` → バッチ集約状態＋子ジョブ一覧
  202 body は `{job_id, status, reused, scope, scope_value, poll_url}`（日次は加えて `trace_id, children[]`）。front は 1〜2 秒間隔で poll、終端状態（succeeded/partial/failed/skipped）で 014 の react-query を invalidate。
- **Rationale**: 別名前空間・別アプリで read-only 契約（014）と混線しない。front 型は openapi-typescript で生成し drift-check（015 同様）。
- **Alternatives considered**: 014 と同一 `/api/v1/` に相乗り（read-only 契約を汚す＝却下）。

## スキーマ変更の要否（結論）

**変更なし。** `ingestion_jobs` の既存カラム（`job_type`=自由テキスト、`trace_id`、`retry_count`/`max_retry`、`status`、`processed_rows`/`skipped_rows`/`error_count`、`summary` JSONB）と `pg_advisory_xact_lock` で、ジョブ・親子バッチ・リトライ・dedup・監査をすべて表現できる。新カラム・新テーブルは不要。
