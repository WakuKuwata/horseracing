# Feature Specification: 一括更新コマンド + 学習ウィンドウ記録 (Refresh Pipeline)

**Feature Branch**: `050-refresh-pipeline` / **Created**: 2026-07-03 / **Status**: Draft
**Input**: 運用整備 2 点。(a) ingest 後の製品更新は predict-backfill(serving)→ recommend-backfill(betting)の手動 2 段連結で、進行中の netkeiba 結果取得が完了するたびにオペレータが順序と引数を揃えて 2 コマンド打つ必要がある(044/043 deferred「予測+推奨を束ねる 1 コマンド」)。(b) `model_versions.metrics_summary` に**学習データ範囲が未記録**(憲法 V の再現性ギャップ — disk の artifact metadata.json には train_through/n_model_rows があるのに DB に無く、2026-07-03 の in-sample 検証で DB からは学習ウィンドウを特定できなかった)。

## 背景
- 予測は serving `run_serving_backfill`(044、日単位・冪等・p-parity)、推奨は betting `_cmd_recommend_backfill`(043/045/046/048、群単位冪等・日単位 two_gamma フィット)として完成済み。**足りないのは束ねだけ**で新ロジックは不要。
- 束ねの置き場所は `live/`(019 で確立したオーケストレーション層 — serving+betting を両方 import できる唯一のパッケージ。ops は subprocess 境界のため import 不可、serving↔betting は相互依存を作らない)。
- **順序が正しさに関わる**: 推奨の two_gamma 校正は「その日より厳密前の永続化済み予測」でフィットするため、予測を全範囲先行させてから推奨を流す(前回の手動 backfill と同一の順序)を 1 コマンドで強制する。
- 学習ウィンドウ: lgbm-042 実測 train_through=2025-10-25(artifact 由来)。backfill 予測 87,996 頭の LogLoss 0.21888 ≈ OOS 0.21706 で **in-sample 楽観は非発現**(GBM+レース softmax は個別レースを記憶しない)を確認済み — ただしこの検証が artifact ファイル頼みだったこと自体が (b) の動機。

## User Stories
- **US1 (P1) 一括更新**: `live refresh --from --to [--force]` 1 コマンドで、範囲内の予測 backfill(serving 044)→ 推奨 backfill(betting 043 経路)を**この順序で**実行し、両段の件数サマリを表示する。各段の冪等・例外隔離・件数 reconciliation は既存実装のまま(新ロジック禁止)。
- **US2 (P2) 学習ウィンドウ記録**: train 時に `metrics_summary["training"]` へ `train_through` / `n_model_rows` / `n_calib_rows` を記録し、DB だけで「このモデルは何をいつまで学習したか」を再現できる(disk artifact metadata.json と同値)。既存モデル行の遡及書換はしない(append-only 規律、次回学習から populate = 040 importance と同じ方針)。

## Requirements
- **FR-001**: `live refresh` は serving `run_serving_backfill` と betting の推奨 backfill を**関数として直接呼ぶ**(subprocess にしない=live の 019 設計)。betting 側は CLI 関数から**コア関数 `recommend_backfill(session, *, date_from, date_to) -> counts` を抽出**して再利用(CLI 出力・挙動はバイト同等、既存テスト不変)。
- **FR-002**: 実行順序は 予測全範囲完了 → 推奨(校正フィットの walk-forward 母数を最大化)。予測段が例外で全滅しても推奨段は実行する(冪等なので安全、各段の error 件数を表示)。
- **FR-003**: `--force` は予測段のみに伝播(044 の再生成 append-only)。推奨段の群単位冪等は変更しない。
- **FR-004**: US2 は `summary["training"]` への 3 キー追加のみ。スキーマ変更なし(JSONB 内)・API/openapi 不変・既存キーのバイト不変。
- **FR-005**: 新予測/確率/EV ロジックなし。リーク境界不変(呼ぶ関数が既にリーク安全、live は 019 と同じく orchestration のみ)。

## Success Criteria
- **SC-001**: `live refresh --from D --to D` が 1 コマンドで両段を実行し、`predict: generated/skip_exists/... | recommend: generated/topped_up/...` の両サマリを出す(wiring テスト: serving/betting コアが正しい引数・順序で呼ばれる)。
- **SC-002**: betting リファクタ後、`betting recommend-backfill` CLI の出力・挙動が従来同等(既存 betting テスト全緑)。
- **SC-003**: train 実行で `metrics_summary["training"]` に train_through/n_model_rows/n_calib_rows が入る(単体テスト)。既存 lgbm-042 行は不変。
- **SC-004**: 実 DB E2E: 既 backfill 済み範囲で `live refresh` → 両段 skip 系カウントで完走(冪等の通し確認)。live/betting/training スイート緑。

## Assumptions
- 実運用の使い方: netkeiba 結果取得が完了 → `live refresh --from <取得開始日> --to <取得末日>` 1 回。モデルリフレッシュ再学習(lgbm-042 同一構成・最新データ)は ingest 完了後に既存 train CLI で別途実行(本 feature のスコープ外、US2 はその際の記録を担保)。
- codex CLI セッション内 3 回起動失敗 → 見送り宣言・single-opinion(019 live 設計・043/044 既存経路の薄い結線のみ)。

## Deferred
ops job / front からの範囲更新起動・自動スケジュール(019)・scrape→refresh の全自動連結・既存モデル行への train_through 遡及記録
