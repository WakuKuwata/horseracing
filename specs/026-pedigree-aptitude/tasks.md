---
description: "Task list — 血統適性 as-of 特徴 (026)"
---

# Tasks: 血統適性 as-of 特徴 (Pedigree-Aptitude Features)

**Input**: [plan.md](plan.md) / [spec.md](spec.md) / [research.md](research.md) / [data-model.md](data-model.md) / [contracts/pedigree-features.md](contracts/pedigree-features.md) / [quickstart.md](quickstart.md)

**Tests**: リーク防止(憲法 II)・パリティ・staleness が核のため**テスト中核**。leak-guard / parity / staleness / columns / debut を必須化。

**Organization**: user story 単位。MVP = US1（sire 適性をリーク安全に生成）+ US3（leak/parity/staleness 保証）。

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [X] T001 実 DB 前提を確認（horseracing DB head=0006 不変・sire_name ~100%/sire_id ~0%、[[local-db-setup]]）。`artifacts/` が .gitignore 済みであること、025 基盤（materialize.py/builder use_materialized）が main にある前提を確認
- [X] T002 [P] [contracts/pedigree-features.md](contracts/pedigree-features.md) の列契約・集計契約(自馬除外/strictly-before/min_starts)・registry/group・fingerprint 拡張・不変条件を確定（実装前の契約固定、憲法 VI）

## Phase 2: Foundational（全 US の前提）

- [X] T003 `features/src/horseracing_features/loader.py`: `Frames` に **optional** `horses: pd.DataFrame`（default 空、後方互換）を追加し、`load_frames` で horses（horse_id, sire_name, dam_name, damsire_name, sire_id, dam_id, damsire_id）を SELECT して同梱
- [X] T004 `features/tests/_frames.py`: `make_frames` を拡張し specs の horse dict から `sire_name`/`damsire_name`（既定 None）を読み horses フレームを合成（既存テストは血統 NaN のまま壊れない）
- [X] T005 `features/src/horseracing_features/registry.py`: sire_aptitude 5 列（sire_win_rate/sire_avg_finish/sire_starts/sire_dist_band_win_rate/sire_surface_win_rate）+ damsire_aptitude 2 列（damsire_win_rate/damsire_avg_finish）を source=`pedigree`/timing=`PRE_ENTRY`/missing(表) で REGISTRY 登録、FEATURE_GROUPS に group 付与、`FEATURE_VERSION="features-007"` に bump（STATIC_COLUMNS には追加しない）。**版 bump で壊れる既存リテラルを更新**: `features/tests/unit/test_materialize_core.py:44` と `features/tests/unit/test_feature023_leak_guard.py:30` の `"features-006"` を `"features-007"` に（analyze A1）。`test_feature020_leak_guard.test_feature020_groups_registered` は FEATURE_GROUPS 全列を走査するので血統列が自動で「登録済み・post_result でない」ことを担保（追加変更不要）

**Checkpoint**: 血統ソースの load・テスト合成・列メタ/版が揃う。

---

## Phase 3: User Story 1 - sire 適性をリーク安全に特徴化 (P1, MVP)

**Goal**: 「同じ種牡馬の他産駒の、対象レース日より前の（全体/距離帯/芝ダート）走力」を per-(race_id, horse_id) で生成。

**Independent Test**: 合成データで他産駒集計・距離/馬場条件付き・min_starts→NaN・debut に値、を確認。

### 実装
- [X] T006 [US1] `features/src/horseracing_features/pedigree_features.py` 新規: `build_pedigree_features(frames, *, min_starts=10)`。`runs` に horses(sire_name/damsire_name) を join（020/human_form の `_runs` 同型）。`_cum_before_by` で (sire_name,date)・(horse_id,date) の cumsum−当日 累積を取り **他産駒=sire累積−自馬累積** を算出（FR-004/005）。分母0→NaN、`sire_starts`=他産駒 finished cnt(ZERO_OK)
- [X] T007 [US1] `pedigree_features.py`: 距離帯別/芝ダート別を (sire_name,dist_band,date)−(horse_id,dist_band,date)、(sire_name,track_type,date)−(horse_id,track_type,date) で算出（020 `_DIST_BINS`/track_type 再利用）。条件付き他産駒 cnt < min_starts → NaN（FR-006）。出力列順を data-model に固定・dtype 明示・(race_id,horse_id) ソート
- [X] T008 [US1] `features/src/horseracing_features/materialize.py`: `build_asof_features` に pedigree ブロックを追加（history/extra/human_form/pace と同じ単一経路で `_KEYS` merge、FR-007）。`materialized_columns()` 経由で血統列が自動収録されること。**パリティ保護（analyze A2）**: `min_starts` は `pedigree_features.py` の**モジュール定数（既定 10、特徴定義の一部）**とし、`build_asof_features`/`build_feature_matrix` の runtime 引数として通さない（materialize と in-memory で同一値を保証＝bit パリティを壊さない。閾値変更は再 materialize＝特徴再生成を要する設計）

### US1 テスト
- [X] T009 [P] [US1] `features/tests/unit/test_pedigree_features.py`: (a)他産駒のみ集計（自馬の過去結果は母集団から除外）、(b)距離帯/芝ダート条件付き、(c)他産駒 cnt<min_starts→NaN、(d)**debut 馬（自馬実績ゼロ）でも sire_name があれば sire 特徴に値**（他産駒由来, SC-001）、(e)sire_name 欠損→NaN・0 補完なし

**Checkpoint**: sire 適性が決定論・リーク安全に生成（MVP の核）。

---

## Phase 4: User Story 3 - リーク安全性とパリティの保証 (P1, MVP)

**Goal**: 憲法 II と 025 パリティ/staleness を血統ブロックでも担保。

**Independent Test**: leak-guard（自馬・同日・未来 不変）、materialize parity bit 一致、血統 backfill で fail-closed。

### 実装
- [X] T010 [US3] `materialize.py`: `source_fingerprint` を horses 血統列（sire_name/dam_name/damsire_name + sire_id/dam_id/damsire_id）を含むよう拡張（FR-010）。`_restrict` は horses を **through までの kept-race 出走 horse_id** に絞って通す（未来馬で誤発火しない）。object/None セルは 025 同様 str 化してハッシュ

### US3 テスト
- [X] T011 [P] [US3] `features/tests/unit/test_pedigree_leak.py`: 当該 target の血統特徴が (a)対象馬自身の過去/今走結果、(b)同日の同種牡馬別産駒の結果、(c)未来レース結果 のいずれを変えても**不変**（SC-002, 憲法 II）
- [X] T012 [P] [US3] `features/tests/unit/test_materialize_core.py`(拡張): parity（`assemble_feature_matrix(use_materialized=True)==(=False)` を血統列含め `assert_frame_equal(check_exact=True,check_dtype=True)`, SC-003）。血統データ（sire_name）後埋め変更 → fingerprint 不一致 → **fail-closed**（SC-004）
- [X] T013 [P] [US3] `features/tests/unit/test_materialize_columns.py`(拡張): 血統列が `materialized_columns()` に含まれ STATIC でない・odds/payout/dividend/今走結果 トークン無し（leak-guard）。`test_no_schema_change`: migration head=0006・features に `__tablename__` 追加なし（SC-007）

**Checkpoint**: 「血統を足すが安全・出力再現可能」をテストで保証。

---

## Phase 5: User Story 2 - 母父(damsire/BMS)を任意 group で追加・効果測定 (P2)

**Goal**: damsire_aptitude を ablation-gated で追加し寄与を測れる。

**Independent Test**: damsire group を drop/有効化でき、feature-eval で sire のみ baseline への寄与が出る。

### 実装
- [X] T014 [US2] `pedigree_features.py`: damsire_name 全体集計（win_rate/avg_finish、他産駒・strictly-before・自馬除外＝sire と同型、距離/馬場別は作らない）を出力に追加。registry の damsire_aptitude group は T005 で登録済みを確認

### US2 テスト
- [X] T015 [P] [US2] `features/tests/unit/test_pedigree_features.py`(追記): damsire 集計の正しさ（他産駒・自馬除外）と group 単位 drop（`drop_features`/`--drop-groups` で damsire 列除外、sire のみで matrix 成立）

**Checkpoint**: BMS の上積みを独立に検証可能。

---

## Phase 6: User Story 4 - 採用判定（OOS）と効きどころ診断 (P2)

**Goal**: 020/023 同型の OOS ゲートで採否を機械判定、効きどころをセグメント診断。

**Independent Test**: `feature-eval --drop-groups sire_aptitude,damsire_aptitude` で baseline=features-006 vs 候補=features-007 の AdoptionReport + prior_starts セグメント診断が出る。

### 実装/評価
- [X] T016 [US4] `training` の feature-eval が 026 群を drop して baseline=features-006 を構成できることを確認（既存 `--drop-groups` に sire_aptitude,damsire_aptitude を渡す）。必要なら CLI の既定 drop 群へ 026 群を追加（020/023 と同様の baseline 構成）
- [X] T017 [US4] 実 DB walk-forward OOS で feature-eval を実行し AdoptionReport（平均 win LogLoss 差・ECE 差・fold 別勝敗・worst-fold 判定）を取得。SECONDARY 診断: market_edge + **prior_starts バンド別 OOS**（021 few/some/many と整合、採否に使わない）を併記し結果を research/quickstart に記録

**Checkpoint**: 採否が客観ゲートで決まり、血統の効きどころが可視化される。

---

## Phase 7: Polish & 横断

- [X] T018 [P] `features` lint/test ゲート: `uv run ruff check src tests` + `uv run pytest` 緑、training/eval/serving の既存テストが透過で緑（血統列追加後も build_feature_matrix 経由で動作）
- [X] T019 実 DB 生成スモーク（[quickstart.md](quickstart.md)）: `features materialize` を実データ実行（feature_version=features-007・血統列収録・生成時間/メモリ実測）、`use_materialized` で parity bit 一致、血統列の非null率（カバレッジ）を確認（SC-003/008）
- [X] T020 [P] `CLAUDE.md` に 026 の 1 行サマリを追記（014–025 と同形式: sire_name キー・他産駒のみ(sire累積−自馬累積)・距離/馬場別・damsire 任意・025 materialization に載せ fingerprint 血統拡張・FEATURE_VERSION 007・OOS 採用ゲート・スキーマ変更なし を要約）
- [X] T021 codex second opinion 再試行（[[pedigree-embedding-036-result]] 教訓）: 設計（特に自馬除外・名前キー・fingerprint 拡張）の独立検証を試み、結果到着時に plan/research と reconcile し差分を記録（不達なら理由を明記）

---

## Dependencies & Execution Order

- **Phase 1 → 2**: Setup → Foundational（T003 loader・T004 frames・T005 registry/version）が全 US をブロック。
- **Phase 3 (US1)**: T006→T007→T008、テスト T009。Foundational 後。
- **Phase 4 (US3)**: T010→ テスト T011/T012/T013[P]。US1 後（生成物が要る）。MVP は US1+US3。
- **Phase 5 (US2)**: T014→T015。US1 後（同型実装の拡張）。
- **Phase 6 (US4)**: T016→T017。US1（+任意 US2）後。
- **Phase 7**: 全実装後。T018/T020[P]、T019、T021。

### User Story 独立性
- US1（sire 生成）= 核。US3（leak/parity/staleness）= US1 の安全保証＝MVP に必須同梱。US2（damsire）= 上積み・独立 drop 可能。US4（採用評価）= 評価のみ、実装不変。

## Parallel 実行例
- Foundational 後: US1 テスト T009 と US2 実装 T014 は別関心。
- US3 テスト T011/T012/T013[P]。Polish: T018/T020[P]。

## 実装戦略
1. **MVP**: Phase 1→2→3→4（sire をリーク安全に生成＝US1、leak/parity/staleness 保証＝US3）。
2. **上積み**: US2 damsire を ablation-gated 追加。
3. **評価**: US4 で OOS 採用ゲート＋セグメント診断（採否は客観ゲート、市場超過は努力目標）。
4. 各 Checkpoint で独立テスト緑。憲法 II（自馬除外/同日除外/strictly-before・odds/結果非特徴・単一実装）/ III（OOS 採用・評価先行）/ IV（009 不変）/ V（manifest 血統 fingerprint・再現性）/ VI（スキーマ変更なし・契約先行）を維持。**最優先 release gate = leak-guard 不変 + parity bit 一致 + 血統 fingerprint fail-closed**。

## analyze 反映（inline 実行・findings 解消）

- **A1 (HIGH, 整合崩れ)**: FEATURE_VERSION 006→007 bump で既存テストのハードコード `"features-006"` が壊れる（`test_materialize_core.py:44`, `test_feature023_leak_guard.py:30`）→ T005 に明示更新を追加。serving/training は model metadata の feature_version を使い registry と runtime 比較しないため透過（破壊なし）と確認。
- **A2 (HIGH, パリティ罠)**: `min_starts` を runtime 引数にすると materialize と in-memory で値が食い違い bit パリティが壊れ得る → T008/contract C1 で「固定モジュール定数・上位へ通さない・変更は再 materialize」と確定。
- **A3 (MEDIUM, 品質ゲート)**: 憲法の codex second opinion ゲートが当環境の background 機構不具合で未達 → plan に PARTIAL 明記、T021 で implement 前に再試行（不達なら自己レビュー根拠を記録）。
- **A4 (LOW, 自動担保)**: `test_feature020_leak_guard.test_feature020_groups_registered` が FEATURE_GROUPS 全列を走査するため血統列の「登録済み・post_result でない・odds/payout/dividend 非含」を追加コストなしで担保（T013 と重複ガード＝二重で安全）。
- **A5 (LOW, FR 文言)**: FR-006 が全体率と条件付き率の Unknown 条件を一文に束ねるが、research R3/data-model/T006-T007 で「全体=分母0→NaN、条件付き=min_starts 未満→NaN」と分離済み＝解釈一意。spec 変更は不要。
- **カバレッジ確認**: FR-001〜016・SC-001〜008 が全て 1 つ以上のタスクに対応（FR↔task / SC↔task 突合済み）。

## 注意（実データ前提）
- 集計キーは `sire_name`（実 DB 100%）。`sire_id`（0%）は不使用、ID 版 deferred。名前キーの限界（同名・表記ゆれ）は limitation。
- `min_starts=10`（実分布 p25=10 根拠）、configurable。条件付き列の充足率は T019 で確認。
- 採用は OOS 全体ゲート。血統は効きどころが限定的なら全体改善が薄い可能性 → prior_starts セグメント診断で価値確認（採否は 020/023 同型の客観ゲート）。
