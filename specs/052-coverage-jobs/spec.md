# Feature Specification: 運用コンソール第2回 — データ被覆率 + ジョブ履歴 (Coverage & Jobs)

**Feature Branch**: `052-coverage-jobs` / **Created**: 2026-07-03 / **Status**: Draft
**Input**: 運用コンソール プログラム第 2 回(全体像・アーキ・ロードマップは `specs/051-admin-console/spec.md` の「プログラム全体像」を参照)。051 でレジストリが見えたが、「製品はどの日までデータ/予測/推奨で埋まっているか」(backfill の成果と穴)と「ジョブは通ったか」(ingestion_jobs は現状 1 件ポーリングのみ=一覧・履歴・検索の盲点)が依然 CLI/DB 直読。どちらも**既存テーブルの read のみ**で解消できる。

## スコープ
- **US1 (P1) データ被覆率 API + 画面**: `GET /api/v1/coverage?from=&to=` — 日ごとに n_races / オッズあり / 結果あり / **active モデルで予測済み** / 推奨あり のレース数を返す(races に日付があるだけの未開催日も 0 行として明示しない=races 由来の開催日のみ)。範囲必須・最大 400 日(超過は typed 422)。active モデル不在時は n_predicted_active=0 + active_model_version=null(正直に)。admin に被覆マップ画面(直近 30 日既定・「予測<レース数」の日をハイライト=穴が一目)。
- **US2 (P2) ジョブ履歴 API + 画面**: `GET /api/v1/jobs?status=&job_type=&limit=` — ingestion_jobs を created_at DESC で一覧(limit 既定 50・上限 200)。行 = id/source/job_type/scope/scope_value/status/trace_id/retry_count/started_at/completed_at/error_message/processed_rows/skipped_rows/error_count/created_at。フィルタは完全一致(未知値=空結果、エラーにしない)。admin にジョブ履歴画面(status/job_type フィルタ・失敗行の error_message 表示)。

## Requirements
- **FR-001**: 両 endpoint とも read-only(SELECT 集計のみ・書込/再計算なし)。api の全 path GET・betting/training 非 import 境界不変。
- **FR-002**: 被覆の「予測済み」= **active モデルの prediction_run があるレース**(044 冪等と同一意味論)。any-model の別集計は持たない(意思決定に使うのは active 被覆)。
- **FR-003**: 範囲ガード(from>to・>400 日は typed 422)。集計はグループ化 SQL(レース単位のループ禁止=一定コスト)。
- **FR-004**: OpenAPI 契約先行: 純追加 → front 期待リスト更新 + admin/front snapshot 同期(byte 一致テスト維持)+ 両 drift-check 緑。
- **FR-005**: スキーマ変更なし・migration なし・front(end-user)UI 変更なし。admin は 051 の tooling/規律(null 安全・typed 3 状態・localhost)踏襲。

## Success Criteria
- **SC-001**: 実 DB で 2025-01 の被覆が「レース数=予測済み=推奨あり」でほぼ全埋まり、未 backfill 期間(例 2023 以前)は予測 0 と正直に出る。
- **SC-002**: ジョブ履歴が実 DB の ingestion_jobs を新しい順に返し、status/job_type フィルタが効く。
- **SC-003**: admin 画面で被覆マップ(穴ハイライト)とジョブ一覧(フィルタ・エラー表示)が機能。空/エラー/範囲超過は typed 表示。
- **SC-004**: api/admin/front スイート緑・両 drift-check 緑・migration head 不変。

## Assumptions
- ロードマップ 3(アクション起動)は次回 — 本 feature は「見る」のみ(被覆の穴を見つけても埋めるのは CLI/既存 ops ボタン)。
- codex CLI 本セッション 3 回起動失敗 → 見送り宣言・single-opinion(051 確立パターンの反復)。

## Deferred
ロードマップ 3-5・被覆の券種別内訳・ジョブの再実行/キャンセル操作・ページネーション(limit で十分)・被覆のレース単位ドリルダウン
