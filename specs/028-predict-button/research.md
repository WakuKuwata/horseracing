# Phase 0 Research: 予測生成ボタン (028)

## R1: 配置（ops write 経路 vs 014 read-only）
**Decision**: 予測生成は ops に新 job_type `predict` として置く。014 は read-only 不変。**ただし ops は ML スタックを import できない**(下記 R1b) → run_predict は serving を import せず **serving CLI を subprocess 起動**(ユーザー選択 C)。
**Rationale**: codex Q1 ACCEPT — `api/tests/unit/test_no_write_boundary.py`(全ルート GET 静的検証)＋ `api/deps.py` の rollback-only session の二層で read-only が担保。024 で「front の write は ops のみ」分離が確立済み。
**Alternatives**: 014 に生成エンドポイント → 憲法 VI 違反。却下。GET 時自動生成 → read が write を誘発。却下（明示ボタン）。

## R1b: ops↔serving 境界（実装時に発覚・codex/plan の見落とし）
**Decision**: ops は `horseracing_serving` を import しない。run_predict は serving CLI を `uv run --project serving python -m horseracing_serving predict --race-id` で subprocess 起動（cwd=serving/ で weights_uri の相対 `../artifacts` が解決、VIRTUAL_ENV を除いた env）。
**Rationale**: **既存の `ops/tests/integration/test_boundary.py` が ops の `horseracing_serving`/features/training 等の import を明示的に禁止**（憲法 II/VI: 取込/write 経路は ML スタックに触れない）。spec/plan/codex は「ops→serving は循環なしで OK」としていたが**この境界テストを見落としていた**（codex は ops→serving を追認しており、境界テストの存在を検出できなかった）。A(境界更新)/B(別 worker)/C(subprocess) をユーザーに提示し **C を選択**。C は ops の import グラフをクリーンに保ち境界テストを通す。
**実装時の追加修正(T019 実 DB smoke)**: subprocess の cwd は **serving/** にする必要がある（lgbm-026 の weights_uri が `../artifacts/...` と相対保存されているため、cwd=repo root だと repo 外に解決して "metadata.json missing" で失敗）。serving/ を cwd にすると `<repo>/artifacts` に正しく解決。実 DB で rc0・prediction_run(16頭) 生成・~34s を確認。
**Alternatives**: A(境界の forbidden から serving を外す) — NON-NEGOTIABLE 近傍の境界を緩めるため却下（ユーザー判断）。B(serving 側の別 predict worker) — 関心分離は最も厳密だが worker プロセス2つで複雑、却下。

## R2: 対象レース（過去/未来）
**Decision**: 過去・未来とも許可。ただし**出走馬が未確定(entries 不完全)の未来レースは skipped**。
**Rationale**: codex Q2 ACCEPT-WITH-CAVEAT — run_serving は past/future 問わず as-of で動作、result-pending ガードは live/ のみ＝ops から past も可。落とし穴は「entries 不完全な未来レースで中途半端な prediction_run」→ **analyze A2 で実コード確認**: run_serving は started 馬が無い race を feature scope(`present`)から除外し**空 results** を返す(per-race persist なので空は no-op＝中途半端な run を残さない)。よって専用 pre-check は不要で「空 results→skipped」で達成。
**Alternatives**: live(019) の result-pending ガード流用 → past を弾いてしまい目的(過去レース予測表示)に反する。却下。

## R3: dedup / model_version / 冪等性
**Decision**: **in-flight(queued/running) 限定 dedup**。完了済みは明示クリックで再生成。model_version は dedup キーに入れず prediction_runs 監査に残す。
**Rationale**: codex Q3 — ingestion_jobs に汎用 payload 列が無く model_version を dedup キーに埋めるには scope_value 文字列エンコード or migration が要る(過剰)。in-flight 限定なら連打・二重起動を防ぎつつ、モデル/エントリ更新後の明示再生成を許せる(目的に合致)。serving.persist_run は append-only、API selection は active model → computed_at DESC で最新 run を表示するので再生成は自然に上書き表示。predict と refresh は別 job_type＝advisory lock キー分離(`predict:{race_id}`)で競合なし。
**Alternatives**: 鮮度(1h)再利用 → モデル更新直後に古い予測再利用のリスク。force フラグ → スキーマ表現が煩雑。却下。

## R4: レイテンシ / コスト
**Decision**: 非同期ジョブ + ポーリング(024 RefreshButton 同型)。use_materialized は deferred。
**Rationale**: codex Q4 ACCEPT — ~30-40s は worker 非同期で吸収。run_serving は use_materialized 未接続＝in-memory 計算。parquet 化は「同一レース反復生成」でしか効かず、通常 1 回操作なので優先度低。将来 opt-in 時は fingerprint 自動再生成トリガが必要(deferred 留意)。

## R5: 監査 / 再現性（憲法 V）
**Decision**: prediction_runs(model_version/logic_version/computed_at) + ingestion_jobs(predict ジョブ summary) に監査。active モデル一意性異常は failed + エラー summary。
**Rationale**: codex Q5 ACCEPT + risk — run_serving は active モデル 1 本要求＝0/複数で例外。catch して job failed + summary にメッセージを残し front に表示(サイレント失敗回避、FR-004/006)。front RunAudit が model_version/computed_at を表示済み。

## R6: 代替・拡張
**Decision**: predict_day バッチ・job summary `source='manual'` は deferred(MVP は race 単位)。
**Rationale**: codex Q6 INVESTIGATE — 一覧から「今日の全レース予測」は自然だが worker は race ループのみで follow-up 可。source='manual' は live 自動実行とバックテスト分離に有用 → summary に入れておく(低コスト、本 plan の runner で付与)。

## Codex second opinion（取得・反映済み）
read-only レビュー(13 tool use)で Q1-Q6 + Top risks を取得。反映: (a)entries 不完全 skipped ガード(FR-003)、(b)active モデル 0/複数 failed(FR-004)、(c)in-flight 限定 dedup(FR-004)、(d)PredictButton は ["predictions",raceId] を invalidate、(e)source='manual' summary。use_materialized・predict_day・force は deferred。spec/plan に反映、差分なし(設計追認 + caveat 取り込み)。
