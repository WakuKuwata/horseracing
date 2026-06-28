---
description: "Task list — ペース/時計シグナルの特徴量化 (023)"
---

# Tasks: ペース/時計シグナルの特徴量化 (Pace & Time Features)

**Input**: [plan.md](plan.md) / [spec.md](spec.md) / [research.md](research.md) / [data-model.md](data-model.md) / [contracts/feature_contract.md](contracts/feature_contract.md)

**Tests**: 憲法品質ゲート（leak / cutoff / 確率整合 / 採用ゲート）に従い test タスクを含める。

**Organization**: user story 単位。MVP = US1+US2（pace_time group がリーク安全に計算でき、正規化される）。

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [X] T001 実 DB 前提を確認（horseracing DB head 不変・2007–2024 ingest 済・馬番修正済 c8cd98b、[[local-db-setup]]）。023 はスキーマ変更なしを前提化
- [X] T002 [P] 候補特徴と group を [contracts/feature_contract.md](contracts/feature_contract.md) で確定（pace_time 主・position_style 任意、features-006）。OOS で特徴選択しない＝事前固定（憲法 III）

## Phase 2: Foundational（全 US の前提）

- [X] T003 `features/src/horseracing_features/loader.py` の `load_frames` を拡張: race_results に `finish_time`/`finish_time_diff`/`corner_orders`、race_horses に `running_style` を SELECT 追加（read-only・2007+ scope・end_date フィルタ維持）。**最大のリーク危険点なので追加後すぐ leak test を固める**（research R2）
- [X] T004 `features/src/horseracing_features/registry.py`: `FEATURE_GROUPS` に pace_time（+任意 position_style）の FeatureMeta(source/timing=as-of/missing=Unknown) を追加、`FEATURE_VERSION` を features-005→**features-006**

**Checkpoint**: 新ソース列がロードされ、registry に新 group が宣言される。

---

## Phase 3: User Story 1 - リーク安全な as-of 特徴 (P1, MVP)

**Goal**: 過去走のみから pace_time 特徴を as-of 集計（今走 result-time を一切使わない）。

**Independent Test**: 今走結果/同走馬今走値/同日他レース/未来年基準を変更しても各特徴が不変、新馬は Unknown(null)、欠損走は除外。

### 実装
- [X] T005 [US1] `features/src/horseracing_features/pace_features.py` を新規作成: **正規化済みの「過去走 row」を先に構築** → `_cumulative_before`(daily cumsum−当日)+`merge_asof(allow_exact_matches=False)` で各馬の as-of 集計（今走 row を集計経路に入れない、同日・将来除外）。欠損走（中止/故障）は集計から除外、過去走なしは Unknown（research R2/FR-002a/005）
- [X] T006 [US1] `features/src/horseracing_features/builder.py`: pace_features を assemble に結線（020 の extra_features/human_form と同層）

### US1 テスト
- [X] T007 [P] [US1] `features/tests/unit/test_pace_features_leak.py`: **拡張 leak-guard** — (a) 今走 last_3f/finish_time/finish_time_diff/corner_orders/running_style、(b) 同走馬の今走値、(c) 同日他レース、(d) 未来年の時計基準 を変更しても各 pace_time 特徴が不変（FR-002, SC-001）
- [X] T008 [P] [US1] `features/tests/unit/test_pace_features_cutoff.py`: 対象レース当日以降のデータ変更で不変（cutoff）、新馬は Unknown(null・0 でない)、欠損走は集計除外（FR-003/004/005, SC-002）

**Checkpoint**: pace_time 特徴がリーク安全に計算できる（正規化前の as-of 集計が成立）。

---

## Phase 4: User Story 2 - 条件正規化 (P1)

**Goal**: 距離/馬場の水準差を正規化してから集計（主＝レース内相対、着差併用、条件別 z-score は as-of 基準のみ）。

**Independent Test**: 異なる距離/馬場の同等パフォーマンスが正規化後に生秒より近づく、正規化基準が過去のみから作られる。

### 実装
- [X] T009 [US2] `features/src/horseracing_features/pace_features.py`: **レース内相対化**（各過去レースの平均/基準との差）を主に実装し、`finish_time_diff`（着差）併用で強メンバー戦の相対不利を緩和。条件別（距離帯×芝ダ×going）z-score を補助で、平均/分散は **as-of（過去）分布のみ**から算出、少数条件は null/粗い条件フォールバック（research R1/FR-006a/006b/007）

### US2 テスト
- [X] T010 [P] [US2] `features/tests/unit/test_pace_normalization.py`: 距離/馬場の異なる同等パフォーマンスが正規化後に生秒差より縮む（条件差吸収, SC-003）、正規化基準が今走結果・同走馬今走値・同日を含まない（FR-007）

**Checkpoint**: US1+US2 で pace_time group がリーク安全＋条件正規化済みで完成（MVP）。

---

## Phase 5: User Story 3 - 採用判定 + 市場超過診断 (P2)

**Goal**: 固定候補を walk-forward OOS で baseline=features-005 と比較し改善時のみ採用。市場超過は診断。

**Independent Test**: feature-eval が strict majority・worst-fold LogLoss 上限・条件別差分込みで判定、baseline 未超過なら adopted=false、ablation/market_edge が算出される。

### 実装
- [ ] T011 [US3] `eval/src/horseracing_eval/feature_eval.py`: AdoptionReport に **strict majority**（`n_win > n_folds/2`、偶数 fold で半数通過しない）・**worst-fold LogLoss 悪化上限**・**条件別（距離帯/芝ダ/going/開催年/q bucket）LogLoss・ECE 差分** を追加（research R5/FR-011/011a）
- [ ] T012 [US3] `eval/src/horseracing_eval/ablation.py`（020 既存）を本特徴に適用できることを確認/微修正: pace_time / position_style group の寄与分離（diagnostic、採否に使わない、FR-012）
- [ ] T013 [US3] position_style group（任意）を `pace_features.py`/`position_features.py` に実装（**FR-008 はこの任意 group に属する**, analyze G1）: 通過順位の頭数正規化（pos/field_size・最終コーナー相対・位置取り変化）+ 過去脚質分布。**ablation で寄与が無ければ採用しない（検証先行、欠損 0 代入禁止）**（research R3）
- [ ] T014 [US3] `training/cli.py`（020 の feature-eval/feature-ablation/feature-diagnostic）が baseline=`drop_features=(pace_time+position_style 全列)` で features-006 を評価できることを確認/結線

### US3 テスト
- [ ] T015 [P] [US3] `eval/tests/integration/test_pace_adoption.py`（合成データ, fake predictor）: strict majority（偶数 fold で半数通過を弾く）・worst-fold LogLoss 上限・条件別差分が機能、baseline 未超過なら adopted=false（false positive なし, SC-004）
- [ ] T016 [P] [US3] `eval/tests/integration/test_pace_ablation_marketedge.py`: pace_time/position_style 寄与が分離報告され、market_edge が p−q gap/edge bucket を算出（「絶対改善≠市場超過」明示, SC-005）

**Checkpoint**: 全 P1+P2 完了。OOS 改善ゲートと市場超過診断が成立。

---

## Phase 6: Polish & 横断

- [X] T017 [P] `features/tests/unit/test_feature023_leak_guard.py`: pace_time/position_style の全特徴が odds・今走結果由来でない（`model_input_features` に出現する name が leak 源でない）こと、market odds が特徴にないことを assert（憲法 II）
- [X] T018 [P] no-schema-change test: db migration head 不変、features に `__tablename__` 追加なし（FR-016, SC-006）
- [ ] T019 実 DB スモーク（[quickstart.md](quickstart.md)）: `feature-eval`（features-006 vs 005）+ `feature-ablation` + `feature-diagnostic` を実データで実行し、採用判定/group 寄与/市場超過診断を確認（改善が無ければ adopted=false、市場超過ゼロでも想定内）
- [ ] T020 [P] lint/test ゲート: `uv run ruff check` + `uv run pytest`（features/eval/training）緑
- [ ] T021 [P] `CLAUDE.md` に 023 の 1 行サマリを追記（014–022 と同形式: 既存 result-time データの as-of 特徴・レース内相対正規化・loader 拡張がリーク危険点・採用ゲート strict majority+条件別差分・market_edge 診断・スキーマ変更なし features-006・market 超過は努力目標を要約）

---

## Dependencies & Execution Order

- **Phase 1 → 2**: Setup → Foundational（T003 loader 拡張・T004 registry）が全 US をブロック。
- **Phase 3 (US1)**: T005→T006、テスト T007/T008[P]。
- **Phase 4 (US2)**: T009（US1 の pace_features に正規化を足す）→ テスト T010。US1 と同一ファイルのため US1 後。
- **Phase 5 (US3)**: T011→T012→T014、T013（任意 group・検証先行）、テスト T015/T016[P]。
- **Phase 6**: 全実装後。T017/T018/T020/T021[P]、T019。

### User Story 独立性
- US1（as-of リーク安全）= 計算の土台。US2（正規化）= US1 の同一ビルダに正規化を足す（密結合の P1 ペア＝MVP）。US3（採用判定）= US1+US2 の特徴を評価、独立。

## Parallel 実行例
- US1 test: T007/T008[P]。US3 test: T015/T016[P]。Polish: T017/T018/T020/T021[P]。

## 実装戦略
1. **MVP**: Phase 1→2→3→4（pace_time group がリーク安全＋正規化済みで計算可能）。
2. **採用判定**: US3 で walk-forward OOS（strict majority+条件別差分）+ ablation + market_edge。position_style は検証先行で採否。
3. **現実評価**: 市場超過は努力目標。届かなくても OOS win 改善があれば採用、無ければ次候補（条件替わり等, spec deferred）へ。
4. 各 Checkpoint で独立テスト緑。憲法 II（as-of/同日除外・拡張 leak-guard・odds/結果非特徴）/ III（OOS ゲート・事前固定・position_style 検証先行）/ IV（win→joint 維持）/ V（features-006・決定論）/ VI（スキーマ変更なし）を維持。

## analyze 反映（findings 解消）
- **G1 (MEDIUM)**: FR-008（通過順位/脚質の正規化）は **US3 の position_style 任意 group** に属すると spec/T013 で明記。MVP(P1) の正規化対象は pace_time のみに統一。
- **G2 (LOW)**: FR-013 の過学習対策＝fold 安定性は採用ゲート(T011/FR-011a)、正則化/early-stopping は **020 predictor 既存設定を継承**（023 で学習ロジック不変）、候補事前固定で特徴数を抑制、と spec に明記。
- **A1 (LOW)**: FR-014 の win→joint は 023 で不変＝新規 assert 不要（既存 009 不変条件で担保）と spec に明記。
