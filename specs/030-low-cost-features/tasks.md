---
description: "Task list — 低コスト特徴拡充 (030)"
---

# Tasks: 低コスト特徴拡充 (Low-cost Feature Expansion)

**Input**: [plan.md](plan.md) / [spec.md](spec.md) / [research.md](research.md) / [data-model.md](data-model.md) / [contracts/lowcost-features.md](contracts/lowcost-features.md) / [quickstart.md](quickstart.md)

**Tests**: リーク防止(憲法 II)・パリティ が核のため**テスト中核**。leak-guard / parity / columns / correctness を必須化。

**Organization**: group 単位（user story）。MVP = US1(斤量) + US2(複勝率)（最も確信度高い cheap wins）。

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [X] T001 前提確認: main(features-007/lgbm-026)・horseracing DB head 0006・025 materialization 利用可。`artifacts/` .gitignore 済み。DB 実カバレッジ(jockey_weight 100%/finish_order 99.7%/venue 100%)を確認
- [X] T002 [P] [contracts/lowcost-features.md](contracts/lowcost-features.md) の列契約・配置(静的/as-of)・集計契約・採用プロトコル(per-group 事前登録)・不変条件を確定(契約先行、codex Q1-Q5 反映)

## Phase 2: Foundational（全 group の前提）

- [X] T003 `features/src/horseracing_features/loader.py`: `race_horses` の SELECT に `jockey_weight` を追加(既存テーブルの未ロード列。fingerprint は全ロード列ハッシュで自動包含)
- [X] T004 `features/src/horseracing_features/registry.py`: 030 の 12 列を source/timing=PRE_ENTRY/missing=NULL で REGISTRY 登録、FEATURE_GROUPS に group(handicap/season/place_rate/human_form_plus/course_aptitude) 付与、**静的 5 列(carried_weight/_ratio/_rel/race_month/race_season)を STATIC_COLUMNS へ**(as-of 7 列は materialized_columns 自動収録)、`FEATURE_VERSION="features-008"`。**版 bump 波及**: `test_materialize_core.py`/`test_feature023_leak_guard.py` の features-007 リテラルを 008 に
- [X] T004b `features/tests/_frames.py`: make_frames に `jockey_weight`(任意, 既定値) を合成(斤量テスト用)

**Checkpoint**: ロード・列メタ・version・テスト合成が揃う。

---

## Phase 3: User Story 1 - 斤量(handicap) (P1, MVP)

**Goal**: 斤量関連を生成（静的 3 + as-of 1）。

**Independent Test**: jockey_weight=56・前走55→change +1、ratio=56/馬体重、rel=平均差、馬体重欠損→ratio NaN。

### 実装
- [X] T005 [US1] `features/src/horseracing_features/static_features.py`: `carried_weight`(jockey_weight)・`carried_weight_ratio`(jockey_weight/weight, 欠損→NaN)・`carried_weight_rel`(jockey_weight − 同 race started 平均) を追加。float64
- [X] T006 [US1] `features/src/horseracing_features/lowcost_features.py`(新): `carried_weight_change`(直前 started race の jockey_weight を merge_asof backward allow_exact_matches=False で取り今走差)。前走なし NaN。float64

### US1 テスト
- [X] T007 [P] [US1] `features/tests/unit/test_lowcost_features.py`: 斤量(値/差/比/相対)、馬体重欠損→ratio NaN、前走なし→change NaN

**Checkpoint**: 斤量群が生成（最有力 cheap win）。

---

## Phase 4: User Story 2 - 複勝率(place_rate) (P1, MVP)

**Goal**: top2/top3 率・距離帯別複勝（as-of 自馬）。

**Independent Test**: 過去5走2着内3→place_rate 0.6(strictly-before・同日除外)、デビュー NaN。

### 実装
- [X] T008 [US2] `lowcost_features.py`: `place_rate`(top2)・`show_rate`(top3)・`dist_band_place_rate` を `_cum_before_by`(cumsum−当日)で算出。float64・自馬同日除外

### US2 テスト
- [X] T009 [P] [US2] `test_lowcost_features.py`(追記): place/show率・dist_band複勝・デビュー NaN・strictly-before

**Checkpoint**: 複勝系が成立。

---

## Phase 5: User Story 3 - 人拡充(human_form_plus) (P2)

**Goal**: 騎手/調教師 複勝・直近・×芝ダ・コンビ・乗り替わり（as-of 跨馬, 対象行+同日除外）。

**Independent Test**: 騎手複勝率・コンビ勝率が対象行+同日除外、乗り替わり flag。

### 実装
- [X] T010 [US3] `lowcost_features.py`: human_form 機構(cumsum−当日)を拡張: `jockey_place_rate`/`trainer_place_rate`・`jockey_recent_win_rate`(rolling)・`jockey_surface_win_rate`((jockey,track_type))・`jt_combo_win_rate`((jockey_id,trainer_id))・`jockey_change`(今走 vs 直前 started race 騎手, merge_asof)。float64

### US3 テスト
- [X] T011 [P] [US3] `test_lowcost_features.py`(追記): 複勝/コンビ/乗り替わり、対象行+同日除外、デビュー NaN

**Checkpoint**: 人拡充が成立。

---

## Phase 6: User Story 4 - コース適性・季節 (P2)

**Goal**: venue 自馬率（as-of）+ season（静的）。

**Independent Test**: venue as-of 勝率/複勝率(母数<min_starts→NaN)、race_month/season。

### 実装
- [X] T012 [US4] `lowcost_features.py`: `venue_win_rate`/`venue_place_rate`((horse_id, venue_code) as-of, 母数<min_starts→NaN)。float64
- [X] T013 [US4] `static_features.py`: `race_month`(race_date.month)・`race_season`(月→季節区分)。float64

### US4 テスト
- [X] T014 [P] [US4] `test_lowcost_features.py`(追記): venue率(母数閾値→NaN)・season(月/季節)

**Checkpoint**: course/season が成立。

---

## Phase 7: 結線・リーク・パリティ (横断, MVP 必須)

- [X] T015 `features/src/horseracing_features/materialize.py`: `build_asof_features` に lowcost as-of ブロック(carried_weight_change/place/show/dist_band_place/human_form_plus/venue)を単一経路で merge。loader/fingerprint は無改修(jockey_weight 追加は race_horses 既存ハッシュに自動包含)を確認
- [X] T016 [P] `features/tests/unit/test_lowcost_leak.py`(新): 今走結果(着順/corner_orders/running_style)・同日他レース・未来 を変えても 030 列不変(SC-002)。**ソースが running_style/corner/finish_order の“今走分”を参照しない**ことを grep で担保(FR-006)
- [X] T017 [P] `features/tests/unit/test_materialize_core.py`(拡張): parity(materialize==in-memory, 030 as-of 列含む, assert_frame_equal check_exact)。`test_materialize_fallback_columns`(拡張): as-of 7 列が materialized・静的 5 列が STATIC・odds/payout/dividend トークン無し・dtype float64

**Checkpoint**: 「安価特徴を足すが安全・出力再現可能」を保証。

---

## Phase 8: User Story 5 - 採用判定（per-group 事前登録 OOS） (P1)

**Goal**: 各 group 独立ゲートで採否、効いた群の和集合を採用。

**Independent Test**: 各 group で baseline=features-007 vs candidate=features-007+g の AdoptionReport。

### 実装/評価
- [X] T018 [US5] `training/src/horseracing_training/cli.py`(+必要なら eval): feature-eval に `--candidate-drop-groups`(candidate=full − cand_drop, 既定 none) を追加。既定 `--drop-groups` を 030 群に。`_group_columns` は registry から自動
- [X] T019 [US5] 実 DB walk-forward OOS を **group 毎**に実行(quickstart の loop): 各 g で candidate=features-007+g vs baseline=features-007 の AdoptionReport(win LogLoss 差・ECE・fold・worst-fold)取得。**事前登録ルール**(同一ゲート通過で採用)を機械適用、通過群を research/quickstart に記録。`feature-ablation` は診断併記

**Checkpoint**: 採否が group 独立の客観ゲートで決まる。

---

## Phase 9: Polish & 横断

- [X] T020 [P] `features` lint/test: `uv run ruff check src tests` + `uv run pytest` 緑、eval/training/serving 既存テスト透過で緑
- [X] T021 実 DB 生成スモーク([quickstart.md](quickstart.md)): `features materialize`(features-008・as-of 7 列収録)、`use_materialized` で parity bit 一致、030 列カバレッジ確認
- [X] T022 採用群で serving 再学習(lgbm-030, `train-evaluate --baseline baseline-uniform-v1 --model-version lgbm-030 --artifacts-dir ../artifacts`)→ 採用なら active 昇格・先行 retired(028 で確立した手順、feature_hash 整合)
- [X] T023 [P] `CLAUDE.md` に 030 の 1 行サマリ追記(採用群・OOS 結果を反映)
- [X] T024 codex 反映確認: 実装が codex Q1-Q5(running_style 非使用/draw_bias 除外/斤量 NaN/per-group 事前登録/season) に沿うことを最終確認

---

## Dependencies & Execution Order
- Phase1→2(T003 loader・T004 registry/version)が全 group をブロック。
- US1-4(T005-T014) は群ごとに独立(同一 lowcost_features.py を編集するため逐次推奨)。Phase7(T015 結線・T016/T017 リーク/パリティ)は US 実装後。MVP=US1+US2+Phase7。
- US5(T018-T019 評価)は結線後。Polish(T020-T024)は最後。

### User Story 独立性
- 各 group は独立に実装・評価可能(ablation 分離)。斤量(US1)・複勝(US2)が最優先。人/コース/season は上積み。

## Parallel 実行例
- テスト T007/T009/T011/T014 は同ファイル追記のため逐次、T016/T017[P] は別ファイル。Polish T020/T023[P]。

## 実装戦略
1. MVP: Phase1→2→US1(斤量)→US2(複勝)→Phase7(結線/リーク/パリティ)。
2. 上積み: US3(人)・US4(course/season)。
3. 採用: US5 で per-group 事前登録ゲート→通過群を採用→serving 再学習。
4. 憲法 II(strictly-before/同日除外・running_style 非使用・odds 非特徴)/III(per-group OOS)/IV(009 不変)/V(parity)/VI(スキーマ変更なし)維持。**最優先 release gate = leak-guard + parity bit 一致**。

## analyze 反映（inline 実行・findings 解消）
- **A1 (確認)**: features-007 リテラルは `test_materialize_core.py`/`test_feature023_leak_guard.py` の 2 箇所のみ(全パッケージ grep)。eval/training/serving は版を動的参照=透過 → T004 で 2 箇所更新。
- **A2 (確認)**: `LightGBMPredictor(drop_features=tuple)` 実在 → candidate=`LightGBMPredictor(session,seed,drop_features=cand_drop)` で per-group 評価が CLI 追加のみで実現(T018)。eval コア不変。
- **A3 (MEDIUM, dtype)**: `race_horses.jockey_weight` は Numeric(Decimal)。pandas で Decimal/object になり得る → static_features で **carried_weight 系を float64 に明示 cast**(パリティ + LightGBM 数値化)。030 列は全て float64 固定(T005/T008/T010/T012/T013)。
- codex Q1-Q5 反映済(running_style 除外/draw_bias 除外/斤量 NaN/per-group 事前登録/season・grade deferred)。新ソース無し(jockey_weight は race_horses 既存ハッシュに自動包含)。

## 注意
- running_style/corner_orders/finish_order の**今走分**は特徴にしない(結果リーク)。脚質系は §3。
- draw_bias/grade は deferred。market(odds/popularity)は非特徴。
- per-group 採用は事前登録ルールを eval 前に凍結(選択リーク回避)。
