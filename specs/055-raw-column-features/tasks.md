# Tasks: JRA-VAN 生データ未使用カラムの活用 — テン3F・馬主/生産者・賞金レベル・系統

**Input**: Design documents from `specs/055-raw-column-features/`

**Prerequisites**: plan.md, spec.md, research.md (D1–D7), data-model.md, quickstart.md

**Tests**: 含む(憲法必須: leakage test・冪等 test・パリティ test。migration/ingest は testcontainers 統合)

**Organization**: US1=ingest 拡張(P1)、US2=features-013 特徴群(P2)、US3=採用ゲート+再学習(P3)。**T023 のゲート判定が ADOPTED の場合のみ再学習・昇格を実施**。

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [x] T001 変更前ベースライン確認: `db`/`ingest`/`features`/`training` スイート全緑を記録(冪等・バイト不変検証の基準点)

---

## Phase 2: Foundational (Blocking Prerequisites)

- [x] T002 `db/migrations/versions/0010_raw_column_features.py` 新規 + `db/src/horseracing_db/models.py` 拡張: race_results.first_3f(NUMERIC)/ races.prize_money(INTEGER)/ horses.owner_name・breeder_name・sire_line・damsire_line(TEXT)、全 nullable(data-model.md 表が正)。head assert を使う既存テスト(features/live 等の 0009 前提)を 0010 に更新
- [x] T003 [P] `db/tests` に migration 統合テスト: upgrade head で新列存在・nullable・既存行不変

**Checkpoint**: スキーマ準備完了

---

## Phase 3: User Story 1 - 未使用カラムの ingest 拡張 (Priority: P1) 🎯 MVP

**Goal**: 既存 CSV の再取込だけで新 6 列が populate、既存データはバイト不変・冪等

**Independent Test**: 1 年分再 ingest で新列カバレッジ(first_3f ~96%・他 ~100%)+既存列同値+再実行同一

### Implementation for User Story 1

- [x] T004 [US1] `ingest/src/horseracing_ingest/layout.py` に索引追加: FIRST_3F=54 / PRIZE_MONEY=23 / OWNER_NAME=64 / BREEDER_NAME=65 / SIRE_LINE=69 / DAMSIRE_LINE=70(コメントに col 番号と意味、spike 検証への言及)
- [x] T005 [US1] `ingest/src/horseracing_ingest/parser.py` で新列を CoreRecords に結線: first_3f(空→None、float)、prize_money(空/0→None、int)、owner/breeder/sire_line/damsire_line(空→None、strip)。pipeline/upsert は不変
- [x] T006 [P] [US1] `ingest/tests` 単体: 実 fixture 行で新列パース(欠損含む)・EXPECTED_COLUMNS 不変・既存列の出力が変更前と一致
- [x] T007 [US1] `ingest/tests` 統合(testcontainers): 同一ファイル 2 回 ingest で行数・全列値が完全一致(冪等)+新列 populate。**既存列バイト不変**は「変更前 parser 出力とのスナップショット比較」で担保
- [x] T008 [US1] 実 DB スモーク(quickstart §1): migration 適用 → 2024 年再 ingest → カバレッジ SQL(first_3f ≥95%・prize/owner/breeder ≥99%)と行数不変を確認し記録

**Checkpoint**: 1 年分で US1 成立。全期間 backfill は US2 の前提として T014 で実施

---

## Phase 4: User Story 2 - features-013 特徴群 (Priority: P2)

**Goal**: 4 群 11 列(data-model.md 表)をリーク安全に構築、025 単一源・パリティ・fail-closed 維持

**Independent Test**: leak-guard(今走/同日/未来の改変に不変)緑・materialize==in-memory ビット一致・カバレッジ明示

### Implementation for User Story 2

- [x] T009 [US2] `features/src/horseracing_features/pace_features.py`: loader に first_3f 追加、過去走ごとの rel_first3f(レース finisher 平均差)→ asof_rel_first3f_avg / asof_rel_first3f_best(cummin)/ asof_pace_balance_avg(rel_last3f − rel_first3f の expanding 平均)。023 の merge_asof(allow_exact_matches=False)機構流用
- [x] T010 [P] [US2] `features/src/horseracing_features/owner_breeder_features.py` 新規: horses.owner_name/breeder_name(NFKC 正規化、026 `_normalize_name` 流用)キーの跨エンティティ as-of — asof_owner_win_rate / asof_owner_place_rate / asof_breeder_win_rate(daily cumsum−当日=020 human_form 規律、min_starts=20 未満 NaN、float64)
- [x] T011 [P] [US2] `features/src/horseracing_features/race_level_features.py` 新規: asof_prize_avg(過去走レース log1p(prize) expanding 平均)。builder 側で prize_money_log(今走 static)と prize_rel(= log − asof、いずれか NaN→NaN)を合成
- [x] T012 [US2] `features/src/horseracing_features/static_features.py` + `registry.py`: prize_money_log / sire_line / damsire_line を STATIC_COLUMNS へ、FEATURE_GROUPS に 4 群 11 列登録、**FEATURE_VERSION features-012→013**
- [x] T013 [US2] `features/src/horseracing_features/materialize.py`: build_asof_features に pace_first3f / owner_breeder / race_level(as-of 分)ブロック追加、**source_fingerprint に新ソース列(race_results.first_3f / races.prize_money / horses×4)追加**(fail-closed、D6)
- [x] T014 [US2] 全期間 backfill 実行(quickstart §2: 2007–2025 ingest-year)+ materialize 再生成 + **実 DB パリティ(materialize==in-memory ビット一致)確認**を記録
- [x] T015 [P] [US2] `features/tests/unit/test_raw_column_leak.py` 新規: 新 4 群の leak-guard — 今走 first_3f/結果の改変・同日他レース・未来行の追加で対象行の特徴が不変(023/026 拡張 leak-guard 同型)。owner/breeder は「対象行+同日を含めない」検証
- [x] T016 [P] [US2] `features/tests` 単体: 各群の値検証(手計算 fixture)・NaN 伝播(欠損 first_3f/prize/min_starts 未満)・dtype 固定(float64/categorical)・fingerprint 不一致で fail-closed 発火
- [x] T017 [US2] `features/tests/unit/test_feature020_leak_guard.py` の head assert を 0010 に更新(既に 0009 前提のため)+ 禁止トークン検査に新列が抵触しないこと確認

**Checkpoint**: features-013 が実 DB で構築・パリティ一致・リーク検証済み

---

## Phase 5: User Story 3 - 採用ゲートと再学習 (Priority: P3)

**Goal**: シリーズ標準 18-fold bundle ゲートで機械判定、ADOPTED 時のみ lgbm-055 昇格

**Independent Test**: feature-eval が baseline=features-012 / candidate=features-013 でレポート出力

### Implementation for User Story 3

- [x] T018 [US3] `training/src/horseracing_training/cli.py`: feature-eval 既定 drop_groups を `_DEF_055 = "pace_first3f,owner_breeder,race_level,sire_line"` に更新(旧群は明示 --drop-groups で到達可能、041 同型)
- [x] T019 [P] [US3] `training/tests` / `eval/tests`: 既定 drop 更新の単体(baseline が新 4 群を落とすこと)+ 既存 feature-eval 回帰
- [x] T020 [US3] **実 DB でゲート実行(採否決定点)**: quickstart §4 の feature-eval(18-fold、シリーズ標準閾値)+ ablation diagnostic を実行し、fold 別数値・採否判定を spec の Status/結果セクションに記録。**不採用なら T021–T022 をスキップし負結果記録へ**
- [ ] T021 [US3] (ADOPTED 時のみ)quickstart §5 の train-evaluate で lgbm-055 学習(pl_topk+TE+isotonic、baseline=uniform)→ 機械ゲート判定 → active 昇格・lgbm-042 retired・serving ロード(features-013)確認
- [ ] T022 [US3] (ADOPTED 時のみ)実 DB E2E: serving predict 1 レースで新特徴込み予測が整合性テスト通過・feature_snapshots に新列・API 透過(openapi 不変)

**Checkpoint**: 採否確定(+採用時は lgbm-055 が production)

---

## Phase 6: Polish & Cross-Cutting Concerns

- [ ] T023 全パッケージ回帰(quickstart §6: db/ingest/features/eval/probability/training/serving/betting)+ front drift-check(openapi 不変)
- [ ] T024 [P] spec.md Status 更新(実測数値+採否)・CLAUDE.md SPECKIT 要約更新
- [ ] T025 [P] メモリ更新: feature-055 結果ノート(採否・各群寄与・「生データ棚卸し」の学び)+ MEMORY.md 索引行

---

## Dependencies & Execution Order

- **Phase 1 → 2 → 3(US1)→ 4(US2)→ 5(US3)→ 6**。US2 は US1 の列が前提、US3 は US2 の特徴が前提(直列)
- US1 内: T004→T005→(T006 ∥ T007)→T008
- US2 内: T009 ∥ T010 ∥ T011(別ファイル)→ T012 → T013 → T014、テスト T015 ∥ T016 は対応実装後、T017 は T002 後いつでも
- US3 内: T018→(T019)→T020 →(ADOPTED)→ T021→T022
- **T020 が decision point**(憲法 III: 閾値はシリーズ既定値、実行後に動かさない)

### Parallel Opportunities

T003 ∥ T002 後の作業、T006 ∥ T007、T009 ∥ T010 ∥ T011、T015 ∥ T016、T024 ∥ T025

---

## Implementation Strategy

**MVP = Phase 3(US1)完了**: 新データが DB に載り、既存が壊れていないことが実 DB で証明された状態。

**注意**:
- FEATURE_VERSION bump(T012)以降、旧モデル(lgbm-042)の feature_hash 検証はこのブランチ内でのみ変化 — **main へのマージは T020 ADOPTED 後**(035 教訓、FR-006)
- 全期間再 ingest(T014)は数十分かかる — バックグラウンド実行し、その間に T015/T016 を先行
- codex CLI が復旧していれば T020 前に second opinion 再試行(plan deviation 記録)
