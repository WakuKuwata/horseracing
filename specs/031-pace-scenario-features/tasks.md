---
description: "Task list — 展開・ペース構成特徴 (031)"
---

# Tasks: 展開・ペース構成特徴 (Race Pace Scenario / Field Composition)

**Input**: [plan.md](plan.md) / [spec.md](spec.md) / [research.md](research.md) / [data-model.md](data-model.md) / [contracts/pace-scenario-features.md](contracts/pace-scenario-features.md) / [quickstart.md](quickstart.md)

**Tests**: リーク防止(憲法 II)・パリティ が核のため**テスト中核**。leak-guard / parity / columns / correctness を必須化。

**Organization**: user story 単位。MVP = US1(field 集計) + US2(相互作用) + US3(リーク) — フィールド構成と相性が中核、リーク安全が release gate。

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [X] T001 前提確認: main(features-008/lgbm-030)・023 `build_pace_features` が front_runner_rate/closer_rate/rel_corner_pos_avg を as-of で返す・025 materialization 利用可・horseracing DB head 0006。`artifacts/` .gitignore 済み。脚質カバレッジ(過去走 running_style 由来)を確認
- [X] T002 [P] [contracts/pace-scenario-features.md](contracts/pace-scenario-features.md) の列契約(7列)・leave-one-out 集計契約・NaN 規律・採用プロトコル(bundle 事前登録)・不変条件を確定(契約先行、codex 反映)

## Phase 2: Foundational（全 story の前提）

- [X] T003 `features/src/horseracing_features/pace_scenario_features.py`(新): スケルトン作成。`PACE_SCENARIO_COLUMNS`(7列) 定義、`build_pace_scenario_features(frames)` が build_pace_features(frames) を呼び targets=[(race_id,horse_id)] と own 脚質列(front_runner_rate/closer_rate/rel_corner_pos_avg)を取得、entry_status==STARTED でフィールド母集団を作る土台。生の今走列(running_style/corner/result)は読まない
- [X] T004 `features/src/horseracing_features/registry.py`: 7 列を source=derived/timing=PRE_ENTRY/missing=NULL で REGISTRY 登録、FEATURE_GROUPS に group=`pace_scenario` 付与(STATIC_COLUMNS には入れない=as-of/field 由来で materialized_columns 自動収録)、`FEATURE_VERSION="features-009"`。**版 bump 波及**: `test_materialize_core.py`/`test_feature023_leak_guard.py` の features-008 リテラルを 009 に

**Checkpoint**: モジュール骨格・列メタ・version が揃う。

---

## Phase 3: User Story 1 - フィールド構成(ペース予想) (P1, MVP)

**Goal**: 同レース他馬(自馬除外)の as-of 脚質を leave-one-out 集約。

**Independent Test**: 3 頭(A,B front・C closer)で C 行の field_front_rate_ex_self=mean(A,B front)、pace_imbalance_ex_self=front−closer。

### 実装
- [X] T005 [US1] `pace_scenario_features.py`: race_id 単位で対象列(front_runner_rate/closer_rate)の「非 null 和 S・件数 C」を集計し、leave-one-out(自馬非 null→(S−v)/(C−1)、自馬 null→S/C、他馬非 null 0→NaN)で `field_front_rate_ex_self`・`field_closer_rate_ex_self`・`pace_imbalance_ex_self`(=front−closer) を算出。float64

### US1 テスト
- [X] T006 [P] [US1] `features/tests/unit/test_pace_scenario_features.py`(新): INV-C1(field_front ex_self 値)・INV-C2(pace_imbalance)・全馬デビュー→NaN(INV-C5)・1頭判明(INV-C6)

**Checkpoint**: フィールド構成が成立。

---

## Phase 4: User Story 2 - 自馬脚質 × フィールド構成の相互作用 (P1, MVP)

**Goal**: own 脚質とフィールド構成の積/差で相性特徴。

**Independent Test**: own.closer_rate × field_front_rate_ex_self == closer_setup、style_mismatch == own.rel_corner_pos_avg − 他馬平均。

### 実装
- [X] T007 [US2] `pace_scenario_features.py`: `front_pressure`=own.front_runner_rate×field_front_rate_ex_self・`closer_setup`=own.closer_rate×field_front_rate_ex_self・`style_mismatch`=own.rel_corner_pos_avg − rel_corner_pos_avg の ex_self_mean を算出。片側 NaN→NaN(0埋め禁止)。`field_style_coverage`=nonnull(front_runner_rate)馬数/field_size(leave-one-out しない)。最終 `out[PACE_SCENARIO_COLUMNS].astype("float64")`

### US2 テスト
- [X] T008 [P] [US2] `test_pace_scenario_features.py`(追記): INV-C3(closer_setup)・INV-C4(style_mismatch)・own NaN→相互作用 NaN・INV-C7(全列 float64)

**Checkpoint**: 相互作用(本命シグナル)が成立。

---

## Phase 5: User Story 3 - リーク安全保証 (P1, MVP)

**Goal**: 今走結果・他馬今走・同日・未来 に不変。

**Independent Test**: leak-guard 全通過 + ソース grep。

### テスト
- [X] T009 [P] [US3] `features/tests/unit/test_pace_scenario_leak.py`(新): INV-L1(自馬今走 finish/corner/running_style/result_status 変更で不変)・INV-L2(同レース他馬今走変更で不変)・INV-L3(同日他レース・未来変更で不変)・INV-L4(ソース grep: 今走 running_style/corner_orders/finish_order/result_status を生参照しない=build_pace_features 経由のみ)

**Checkpoint**: リーク境界を新設しないことを保証(release gate)。

---

## Phase 6: User Story 4 - materialization パリティ・カバレッジ (P2)

**Goal**: 単一 as-of 源結線・bit パリティ・serving fallback。

**Independent Test**: materialize==in-memory bit 一致、pace_scenario in materialized_columns。

### 実装
- [X] T010 [US4] `features/src/horseracing_features/materialize.py`: `build_asof_features` に pace_scenario ブロック(build_pace_scenario_features)を単一経路で merge。loader/source_fingerprint 無改修(新ソース列なし=running_style/corner は 023 で既にロード&fingerprint 包含)を確認。serving 未来レース(parquet 非カバー)は単一レース fallback で同一実装

### US4 テスト
- [X] T011 [P] [US4] `features/tests/unit/test_materialize_core.py`(拡張): INV-P1(parity materialize==in-memory, pace_scenario 7 列含む, assert_frame_equal check_exact/check_dtype)・INV-P2(7 列 materialized・odds/payout/dividend トークン無し)・INV-P3(FEATURE_VERSION=="features-009")

**Checkpoint**: 「展開特徴を足すが出力再現可能・serving 一貫」を保証。

---

## Phase 7: User Story 5 - 採用判定（事前登録 bundle OOS） (P1)

**Goal**: bundle ゲートで採否、採用なら serving 昇格。

**Independent Test**: `feature-eval --drop-groups pace_scenario` の AdoptionReport。

### 実装/評価
- [X] T012 [US5] `training/src/horseracing_training/cli.py`: feature-eval 既定 `--drop-groups` を `pace_scenario` に(baseline=features-008、candidate=full features-009)。`_group_columns` は registry 自動。`--candidate-drop-groups`(030 で追加済)は流用
- [X] T013 [US5] 実 DB walk-forward OOS(quickstart): `feature-eval --drop-groups pace_scenario` で AdoptionReport(win LogLoss 差・ECE・fold・worst-fold)取得。**事前登録基準**(primary=LogLoss改善 かつ ECE非悪化 + strict majority + worst-fold ECE 2e-3 + worst-fold dLogLoss 5e-3)を機械適用、結果を research/quickstart に記録。`feature-ablation`(field_only/interaction_only/diversity_only 相当)・`feature-diagnostic`(market_edge)は SECONDARY 併記

**Checkpoint**: 採否が客観ゲートで決まる。

---

## Phase 8: Polish & 横断

- [X] T014 [P] `features` lint/test: `uv run ruff check src tests` + `uv run pytest` 緑、eval/training/serving 既存テスト透過で緑
- [X] T015 実 DB 生成スモーク(quickstart): `features materialize`(features-009・pace_scenario 7 列収録)、`use_materialized` で parity bit 一致、7 列カバレッジ・field_style_coverage 分布確認
- [X] T016 採否に応じた serving 反映: 採用なら `train-evaluate --model-version lgbm-031 --baseline baseline-uniform-v1 --artifacts-dir ../artifacts`→adopted=active で active 昇格・lgbm-030 retired(feature_hash=features-009 整合)・serving が lgbm-031 を自動ロード確認。不採用なら main を features-008/lgbm-030 のまま維持しブランチ保全(027 前例)
- [X] T017 [P] `CLAUDE.md` の 031 サマリを OOS 結果で更新(採否・LogLoss/AUC/ECE/fold)
- [X] T018 codex 反映確認: 実装が codex(leave-one-out 連続量・相互作用主役・0埋め禁止・coverage 列・entry_status 母集団・bundle 事前登録) に沿うことを最終確認

---

## Dependencies & Execution Order
- Phase1→2(T003 モジュール骨格・T004 registry/version)が全 story をブロック。
- US1(T005)→US2(T007)は同一ファイル(pace_scenario_features.py)を編集するため逐次。US3(T009 leak)は US1/US2 実装後。MVP=US1+US2+US3。
- US4(T010 結線・T011 parity)は実装後。US5(T012-T013 評価)は結線後。Polish(T014-T018)は最後。

### User Story 独立性
- US1(field 集計)・US2(相互作用)は中核で逐次。US3(リーク)は横断保証。US4(パリティ)・US5(採用)は上に乗る。

## Parallel 実行例
- T006/T008 は同ファイル追記のため逐次、T009[P](leak 別ファイル)・T011[P](materialize テスト別ファイル)は並行可。Polish T014/T017[P]。

## 実装戦略
1. MVP: Phase1→2→US1(field)→US2(相互作用)→US3(リーク)。
2. 横断: US4(パリティ/serving fallback)。
3. 採用: US5 で事前登録 bundle ゲート→採否→serving 反映。
4. 憲法 II(他馬の過去 as-of のみ・今走非参照・leave-one-out 自馬除外)/III(bundle OOS)/IV(009 不変)/V(parity)/VI(スキーマ変更なし)維持。**最優先 release gate = leak-guard + parity bit 一致**。

## analyze 反映（inline 実行・findings 解消）
- **A1 (確認)**: features-008 リテラルは `test_materialize_core.py`/`test_feature023_leak_guard.py` の 2 箇所(030 で 008 に更新済)。eval/training/serving は版を動的参照=透過 → T004 で 2 箇所を 009 に更新。
- **A2 (確認)**: `LightGBMPredictor(drop_features=tuple)` + feature-eval の `--drop-groups`/`--candidate-drop-groups`(030 で追加済)実在 → bundle 評価は既定 drop を pace_scenario にするのみ(T012)。eval コア不変。
- **A3 (リーク構造)**: pace_scenario は build_pace_features の **出力のみ** を入力に取り生今走列を読まない設計 → リーク面が 023 の as-of 機構に閉じ込められる。leak-guard(T009)で自馬今走/他馬今走/同日/未来 不変 + grep を担保。
- **A4 (NaN/dtype)**: leave-one-out は非 null の和/件数ベースで NaN 伝播、0 埋め禁止。全列 float64 固定(パリティ + プール依存 dtype ドリフト防止)。
- codex 反映済(連続 leave-one-out・相互作用主役・0埋め禁止・coverage 列・entry_status 母集団・bundle 事前登録、カテゴリ化=027 同型で却下)。

## 注意
- 今走 running_style/corner_orders/finish_order/result_status は**生参照しない**(build_pace_features の as-of 出力経由のみ)。
- フィールド母集団は entry_status==STARTED(取消は serving と一致)。
- bundle 採用後に OOS を見て列を削るのは禁止(選択リーク、削るなら次版で再事前登録)。market_edge は SECONDARY。
