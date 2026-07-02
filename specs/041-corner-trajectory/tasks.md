---
description: "Task list — コーナー通過順の軌跡特徴 (041)"
---

# Tasks: コーナー通過順の軌跡特徴 (Corner Trajectory)

**Input**: [plan.md](plan.md) / [spec.md](spec.md) / [research.md](research.md) / [data-model.md](data-model.md) / [contracts/corner-trajectory-features.md](contracts/corner-trajectory-features.md) / [quickstart.md](quickstart.md)

**Tests**: リーク防止(憲法 II)・パリティ(憲法 V)が核のため**テスト中核**。値/leak/parity を必須化。

**Organization**: MVP = US1(算出) + US2(リーク)。US3(パリティ)/US4(採用)は上に乗る。030-033 確立パターン踏襲。

## Phase 1: Setup
- [X] T001 前提確認: main(040 マージ後、features-011/lgbm-039)・023 pace_features の runs/_final_corner/field_size/merge_asof 機構・025 materialize・migration head 0008・corner_orders 実データ形式(数値文字列 list、100%)。features-011 リテラル残存箇所を grep で確定

## Phase 2: Foundational
- [X] T002 `features/src/horseracing_features/corner_trajectory_features.py`(新): `CORNER_TRAJECTORY_COLUMNS`(4) 定義、`build_corner_trajectory_features(frames)` 骨格(runs 構築=finished+started+race_date+field_size、corner parse ヘルパ)。今走列の生参照なし
- [X] T003 `features/src/horseracing_features/registry.py`: 4 列を FeatureMeta("pace", PRE_ENTRY, NULL) で登録、FEATURE_GROUPS group=`corner_trajectory`、`FEATURE_VERSION="features-012"`。**版リテラル波及**: features-011 を持つテスト(test_feature023_leak_guard.py 等 T001 で確定した箇所)を 012 に

## Phase 3: US1 - 軌跡特徴の算出 (P1, MVP)
- [X] T004 [US1] `corner_trajectory_features.py`: 生スコア(late_gain/early_pos/mid_move、field_size 正規化、コーナー<2 は mid_move NaN・parse 失敗/fs≤0 は NaN)+ expanding as-of(cumsum/cumcount avg・cummax best)+ merge_asof(on=race_date, by=horse_id, backward, allow_exact_matches=False)。最終 4 列 astype float64
- [X] T005 [P] [US1] `features/tests/unit/test_corner_trajectory_features.py`(新): INV-C(late_gain=(corner_last−fo)/fs・early_pos・mid_move=max 連続改善)・expanding 集約値・エッジ(コーナー1つ→mid_move NaN・parse 失敗スキップ・デビュー→全 NaN・fs=0→NaN)・float64

## Phase 4: US2 - リーク安全 (P1, MVP)
- [X] T006 [P] [US2] `features/tests/unit/test_corner_trajectory_leak.py`(新): INV-L1(今走 corner/finish 変更で対象行不変)・INV-L2(同日他レース変更で不変=allow_exact_matches=False)・INV-L3(未来レース変更で不変)・INV-L4(grep: 対象行への今走 result 直接結合なし・odds/payout/dividend 非参照)

## Phase 5: US3 - materialization パリティ (P2)
- [X] T007 [US3] `features/src/horseracing_features/materialize.py`: build_asof_features に corner_trajectory ブロック(merge チェーン追加、031-033 同型)。source_fingerprint 無改修(新ソース列なし)を確認。serving 未来=単一レース fallback 既存機構
- [X] T008 [P] [US3] `features/tests/unit/test_materialize_core.py`(拡張): INV-P1(parity 4 列込み bit 一致)・INV-P2(4 列 materialized・odds トークン無し)・INV-P3(FEATURE_VERSION=="features-012")

## Phase 6: US4 - 採用判定 (P1)
- [X] T009 [US4] `training/src/horseracing_training/cli.py`: feature-eval 既定 `--drop-groups` を `corner_trajectory` に
- [X] T010 [US4] 実 DB 検証: features materialize parity bit 一致(features-012、4 列カバレッジ ~89%)→ 18-fold `feature-eval`(事前登録基準機械適用、PRIMARY+fold ガード)。結果を research に記録
- [X] T011 [US4] 採否反映: 採用なら `train-evaluate --model-version lgbm-041 --objective cond_logit --calibration isotonic --target-encode jockey_id,trainer_id --te-smoothing 50 --baseline baseline-uniform-v1 --artifacts-dir ../artifacts` → active 昇格・lgbm-039 retired・serving ロード確認(feature_hash=features-012)。不採用ならブランチ保全

## Phase 7: Polish
- [X] T012 [P] `features`/`training` lint(ruff)+ pytest 緑、eval/serving/api 透過で緑
- [X] T013 [P] `CLAUDE.md` 041 サマリを OOS 結果で更新・memory に結果記録
- [X] T014 codex 反映確認: ブレスト指摘(今走 corner 禁止・過去走 as-of・頭数正規化・コーナー数差処理)が実装に反映されていることを最終確認

## Dependencies & Execution Order
- T001→T002/T003(骨格+registry)が全 story をブロック。T004(算出)→T005/T006 テスト。US3(T007/T008)は実装後。US4(T009-T011)は結線後。Polish 最後。

## Parallel 実行例
- T005[P]/T006[P](別ファイル)並行可。T008[P]・Polish T012/T013[P]。

## 注意
- 今走の corner_orders/finish_order/result は生参照しない(merge_asof 経由のみ)。
- NaN 伝播・0 埋め禁止(Unknown≠0)。全列 float64(プール依存 dtype ドリフト防止)。
- bundle 採用後の列削り禁止(選択リーク)。閾値は 030-033 と同一・事前登録(数値を見てから動かさない)。
- materialize parity bit 一致は非交渉 release gate。
