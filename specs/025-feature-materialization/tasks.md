---
description: "Task list — 特徴量 materialization 基盤 (025)"
---

# Tasks: 特徴量 materialization 基盤 (Feature Materialization)

**Input**: [plan.md](plan.md) / [spec.md](spec.md) / [research.md](research.md) / [data-model.md](data-model.md) / [contracts/materialization.md](contracts/materialization.md)

**Tests**: infra のため**テスト先行**（パリティ bit / staleness fail-closed / 単一実装 / leak）が中核。

**Organization**: user story 単位。MVP = US1+US2（生成→read がパリティ保証で成立）。

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [X] T001 実 DB 前提を確認（horseracing DB head 不変・2007–2024、[[local-db-setup]]）。`artifacts/` が .gitignore 済みであることを確認（parquet/manifest 非コミット）
- [X] T002 [P] [contracts/materialization.md](contracts/materialization.md) の parquet/manifest 形式・read API・不変条件を確定（実装前の契約固定、憲法 VI）

## Phase 2: Foundational（全 US の前提）

- [X] T003 `features/src/horseracing_features/registry.py`: **materialize 対象列を機械導出するヘルパ**（FEATURE_GROUPS + history 由来の as-of 列、**static/current-race を除外**）を追加。static 列リストも定義（venue/distance/track/going/weather/race_class/race_number/age/sex/frame/horse_number/jockey_id/trainer_id/weight/weight_diff/field_size）
- [X] T004 `features/src/horseracing_features/materialize.py` を新規作成（土台）: `source_fingerprint(frames)`（races/race_horses/race_results の射影カラムの決定論ハッシュ）と manifest スキーマ（data_from/through・n_rows・feature_version・content_hash・generated_at・source_fingerprint・materialized_columns）

**Checkpoint**: materialize 列の機械導出と fingerprint/manifest 基盤が揃う。

---

## Phase 3: User Story 1 - 生成フェーズ（parquet materialize）(P1, MVP)

**Goal**: 全プールの as-of 特徴を 1 回計算し parquet + manifest を決定論で出力。

**Independent Test**: CLI 実行で parquet+manifest 出力、2 回実行で content_hash 一致。

### 実装
- [X] T005 [US1] `materialize.py`: `build_asof_features(frames)` を実装。**既存ブロック関数 `build_history_features`/`build_extra_features`/`build_human_form_features`/`build_pace_features` を唯一の as-of 源として呼ぶ**（二重実装禁止, FR-017）。出力は per-(race_id, horse_id) + materialize 列、(race_id,horse_id) 決定論ソート・明示 dtype・null≠0
- [X] T006 [US1] `materialize.py`: `write_materialized(path, frames)`（parquet + manifest.json 出力、content_hash/source_fingerprint 計算）
- [X] T007 [US1] `features/src/horseracing_features/cli.py`: `materialize [--from --to] [--out artifacts/features.parquet]` サブコマンド（DB read-only、書き込みなし）

### US1 テスト
- [X] T008 [P] [US1] `features/tests/integration/test_materialize_generate.py`: 生成で parquet+manifest 出力、**2 回実行で content_hash・行集合一致（決定論, SC-003）**、manifest に source_fingerprint/materialized_columns/version 記録

**Checkpoint**: as-of 特徴の決定論 materialize が成立。

---

## Phase 4: User Story 2 - read+merge とパリティ (P1, MVP)

**Goal**: builder が parquet を opt-in で read+merge、出力は in-memory と bit 一致、staleness は fail-closed。

**Independent Test**: materialize 経路と in-memory 経路の build_feature_matrix が全列 bit 一致、予測一致、fingerprint 不一致で fail-closed。

### 実装
- [X] T009 [US2] `materialize.py`: `read_materialized(path)` + `check_coverage(manifest, required_keys, current_fingerprint, feature_version)` → 合格/不合格判定（**不合格は fail-closed 例外**, FR-009）
- [X] T010 [US2] `features/src/horseracing_features/builder.py`: `assemble_feature_matrix(..., materialized=None, use_materialized=False)` に read 経路追加。`use_materialized` かつ coverage 合格 → as-of 列を parquet から merge、static は従来計算。未カバー/fingerprint 不一致 → fail-closed。既定 `use_materialized=False`＝現行経路（パリティ基準）

### US2 テスト
- [X] T011 [P] [US2] `features/tests/integration/test_materialize_parity.py`: 同一データで `use_materialized=True`（read）と `=False`（in-memory）の build_feature_matrix を `assert_frame_equal(check_exact=True, check_dtype=True)` で**全列・列順一致**（SC-001）。null/同日/pace/static を含むケース。**column group ごとにパラメータ化**して各 group の bit パリティを確認（FR-019 の段階有効化 rollout 規律＝全 group 緑でフラグ有効化, analyze G1）
- [X] T012 [P] [US2] `features/tests/integration/test_materialize_prediction_parity.py`: materialize 経由と in-memory 経由で学習した予測（win/top2/top3）が一致（SC-002）
- [X] T013 [P] [US2] `features/tests/integration/test_materialize_staleness.py`: parquet 生成後に範囲内の race_horses/race_results を 1 行変更 → fingerprint 不一致 → `use_materialized=True` build が **fail-closed**（黙って古い値 0 件, SC-004/008）。parquet 削除/未カバーも fail-closed

**Checkpoint**: 「速くするが出力不変」がパリティ＋ fail-closed で保証。

---

## Phase 5: User Story 3 - serving fallback（未来レース）(P2)

**Goal**: parquet 非カバーの新規レースを単一レース fallback 計算で補完（生成と同値）。

**Independent Test**: 新規レースの特徴が生成フェーズと同値、generator==fallback 契約一致。

### 実装
- [X] T014 [US3] `builder.py`/`materialize.py`: parquet カバー外の**未来レース**のみ、既存ブロック関数で単一レース fallback 計算（audit warning）。fallback も `build_asof_features` と同一実装経由（FR-010/017）

### US3 テスト
- [X] T015 [P] [US3] `features/tests/integration/test_materialize_fallback.py`: 履歴のみ parquet + 新規レースで build → 当該レースの as-of 特徴が fallback 計算で生成と同値。**同一合成 target race で generator 出力 == fallback 出力**（単一実装契約, SC-005/SC-009）

**Checkpoint**: 訓練(bulk=parquet)と serving(新規=fallback)が同一定義で両立。

---

## Phase 6: Polish & 横断

- [X] T016 [P] `features/tests/unit/test_materialize_leak.py`: materialize 後に target/同日/未来レースの結果を変更しても当該 target の as-of 特徴が不変（pool-end 非依存/leak, SC-008, 憲法 II）
- [X] T017 [P] `features/tests/unit/test_materialize_columns.py`: materialize 列が registry から機械導出され **static/current-race 列を 0 件含む**（FR-002/017, SC-009）。odds/結果が materialize 列に無い（leak-guard）
- [X] T018 [P] no-schema-change test: db migration head 不変、features に `__tablename__` 追加なし、**FEATURE_VERSION 不変**（FR-014, SC-006）
- [ ] T019 実 DB スモーク（[quickstart.md](quickstart.md)）: `features materialize` を実データで実行し生成時間/メモリを実測（性能予算）、`use_materialized=True` で feature-eval/train-evaluate が parquet read で通り**予測が materialize 前と一致**することを確認（SC-007）
- [X] T020 [P] lint/test ゲート: `uv run ruff check` + `uv run pytest`（features）緑、training/eval/serving の既存テストが透過で緑のまま（出力不変）
- [ ] T021 [P] `CLAUDE.md` に 025 の 1 行サマリを追記（014–024 と同形式: as-of 特徴の parquet materialize・単一実装・bit パリティ・fail-closed staleness(source fingerprint)・read opt-in 段階有効化・スキーマ変更なし・FEATURE_VERSION 不変・026 血統の土台 を要約）

---

## Dependencies & Execution Order

- **Phase 1 → 2**: Setup → Foundational（T003 機械導出・T004 fingerprint/manifest）が全 US をブロック。
- **Phase 3 (US1)**: T005→T006→T007、テスト T008。
- **Phase 4 (US2)**: T009→T010、テスト T011/T012/T013[P]。US1 後（生成物が要る）。
- **Phase 5 (US3)**: T014、テスト T015。US2 後。
- **Phase 6**: 全実装後。T016/T017/T018/T020/T021[P]、T019。

### User Story 独立性
- US1（生成）= 基盤。US2（read+パリティ）= US1 の生成物を消費＝MVP の核。US3（fallback）= serving 経路、独立。

## Parallel 実行例
- US2 test: T011/T012/T013[P]。Polish: T016/T017/T018/T020/T021[P]。

## 実装戦略
1. **MVP**: Phase 1→2→3→4（生成→read が**パリティ bit 一致**で成立、fail-closed staleness）。
2. **serving**: US3 で未来レース fallback（単一実装）。
3. **段階有効化**: read 経路は既定 off（opt-in）、parity/leak 全合格まで本番デフォルトにしない（FR-019）。026 血統は本基盤に載せる。
4. 各 Checkpoint で独立テスト緑。憲法 II（leak 不変・単一実装・odds/結果非特徴）/ III（出力不変＝採用済みモデル予測一致）/ IV（009 不変）/ V（manifest/fingerprint 再現性・staleness）/ VI（スキーマ変更なし・契約先行・opt-in）を維持。**最優先の release gate = パリティ bit 一致 + fingerprint fail-closed + generator==fallback**。

## analyze 反映（findings 解消）
- **G1 (MEDIUM)**: FR-019/SC-010 を「単一 opt-in フラグ + group 別 parity の rollout 規律（T011 を group ごとにパラメータ化、全 group 緑で有効化）」に明確化。runtime per-group トグルは作らない。
- **G2 (LOW)**: FR-013 の 009 整合は予測一致(T012)で透過担保＝専用 assert 不要と明記。
- **G3 (LOW)**: FR-018 の cutoff 不変 eligibility は将来 as-of 特徴追加時の手順、現行集合の不変性は T016 が証明と明記。
