---
description: "Task list for 068 evaluation-contract + calibration-split"
---

# Tasks: 評価契約の是正 + 校正分割の見直し

**Input**: Design documents from `specs/068-evaluation-contract-calibration/`

**Prerequisites**: plan.md, spec.md, research.md (D1–D7), data-model.md, contracts/cli.md

**Tests**: 含む（TDD）。spec の Edge Cases・research D7 の codex 必須テスト群は correctness-critical のため実装前にテストを先行させる。

**Organization**: US1（評価契約）→ US2（校正分割）→ US3（provenance）。US2 は US1 の paired-eval 経路に依存する。

## Path Conventions

- eval: `eval/src/horseracing_eval/`, tests `eval/tests/{unit,integration}/`
- training: `training/src/horseracing_training/`, tests `training/tests/{unit,integration}/`
- 実行: `uv run --project eval ...` / `uv run --project training ...`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 実験の事前登録 artifact と共通型の土台。

- [X] T001 [P] `specs/068-evaluation-contract-calibration/gate-config.json` に採用ゲート事前登録値を作成（winner NLL primary・CI上限<0・**直近ガード=3年かつ5年の両窓非悪化[AND, analyze C2]**・top2/top3 non-inferiority幅・**ECEガード=mean-ECE の non-inferiority幅[worst-fold単発blipで否決しない, analyze A1・020/023/039前例]**・絶対ECE 0.05非常停止・bootstrap seed・B・block=開催日・**決定論閾値 1e-9・num_threads=1**・**A–D screening の go/no-go 基準[inner-valid winner NLL のマージンと NO_DECISION の CI 扱い, analyze U1]**）。採用ゲートと screening 基準の両方を OOS結果を見る前に固定（FR-009/FR-014, III）。
- [X] T002 [P] `eval/src/horseracing_eval/foldfit.py` に `PredictorFactory` Protocol（`fit(train_rows, fold) -> Predictor` + `recipe_meta` dict + `recipe_hash`）を定義。eval は `ModelRecipe` を import しない（analyze C1）。HashContract/SnapshotAudit は T006 hashing.py が単一 home（T002 では定義しない、analyze D1）。

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: US1/US2 の両方が使う「各 outer fold 再fit harness」「母集団分類」「hash/snapshot 監査」。

**⚠️ CRITICAL**: US1/US2 着手前に完了必須。

- [X] T003t [P] `training/tests/unit/test_recipe_market_offset.py`（**TDD: T003 より先に作成し T003 まで FAIL**）: `ModelRecipe`/factory 構築で `market_offset=true` が typed fail-closed error を送出（対象race自身のoddsを読むリーク防御、FR-019・codex C3・analyze G1）。他の codex-critical（C1/C4/C6/C8）と同様にテストで固定。
- [X] T003 [P] `training/src/horseracing_training/recipe.py` に `ModelRecipe` dataclass（objective/calibration/calib_frac/booster_alloc/feature_version/feature_cols/target_encode_cols/te_smoothing/seed/params/market_offset）+ 既存 metadata → ModelRecipe 復元 + **ModelRecipe → eval.foldfit.PredictorFactory 実装**（recipe を training 側で factory に包み eval に注入、analyze C1）。**`market_offset != False` は fail-closed で拒否**（FR-019, codex C3）。data-model §2/§2b。
- [X] T004 [P] `eval/src/horseracing_eval/dataset.py` の entry_status を用いた母集団ヘルパ `population_masks(...)` を追加（started / finished / winner-NLL-eligible[勝者ちょうど1頭] を返し、cancel/DNF/失格/同着/勝者不在/未確定/部分取込を分類、除外件数を surface）。data-model §1・spec Edge Cases。
- [X] T005 `eval/src/horseracing_eval/foldfit.py` に、注入された `PredictorFactory`（T002 Protocol）で **各 outer fold で candidate/active を再fit** する harness を実装（保存 booster を使わない、codex C1）。**eval は training を import せず factory を受け取るだけ**（020 の import 境界維持）。outer-train 全量 fit → outer-valid を一度だけ予測。**`num_threads` を設定可能にする**: SC-002 の決定論検証（T019）は `num_threads=1` 固定で bit 再現を担保し、計算の重い A–D screening / フル walk-forward（~20分/run）は multi-thread 可（決定論は T019 の単一スレッド run で別途担保、CI が残差ノイズを吸収）。両者を混同しない（analyze U1/I1）。
- [X] T006 [P] `eval/src/horseracing_eval/hashing.py` に hash 契約 **6種**（feature_schema_hash / raw_matrix_content_hash = 全arm同一、model_race_set_hash / calib_race_set_hash = arm別の race 分割、transformed_matrix_hash / model_artifact_hash = arm別）と SnapshotAudit（repeatable-read snapshot・result/entry hash・manifest hash・recipe hash・code SHA）を実装（FR-018, V, codex C5/C9・data-model §3）。

**Checkpoint**: 再fit harness + 母集団 + hash/snapshot が揃い US1/US2 着手可能。

---

## Phase 3: User Story 1 - 評価契約の是正 (Priority: P1) 🎯 MVP

**Goal**: race-level winner NLL を PRIMARY にした started-all 母集団の paired 同時評価 + block bootstrap CI + 採用ゲートを提供。

**Independent Test**: lgbm-062 vs lgbm-061 を同一 race 集合で paired 再評価し、winner NLL・started-all・block bootstrap CI・期間別を1レポートに出す（SC-001）。

### Tests for User Story 1 ⚠️（先行）

- [X] T007 [P] [US1] `eval/tests/unit/test_winner_nll.py`: race 等重み `-log(p_winner)` と per-horse micro 集約の区別、同着/勝者不在/未確定の除外と件数 surface（codex テスト）。
- [X] T008 [P] [US1] `eval/tests/unit/test_population_masks.py`: started/DNF/失格/取消/除外/同着/勝者不在/未確定/部分取込の母集団 golden test（codex テスト）。cancel が started に入らない。
- [X] T009 [P] [US1] `eval/tests/unit/test_ece_variants.py`: equal-mass ECE の tie-safe（isotonic plateau を bin 境界で分断しない）・実bin数/edge/count、確率帯別・頭数別 ECE（codex C10）。
- [X] T010 [P] [US1] `eval/tests/unit/test_bootstrap.py`: 開催日 block contiguity・共通 resample・seed 決定論・少block時 `NO_DECISION`・AR(1)/開催日cluster 合成データで CI coverage simulation（codex テスト・D2）。
- [X] T011 [P] [US1] `eval/tests/integration/test_paired.py`: 同一モデルで paired 差=0、candidate/active 交換で符号反転、race/date/snapshot/recipe/code hash 不一致で fail-closed、片側予測欠落は contract failure（codex C8）、**両 arm が top2/top3 LogLoss を出力**（ゲート入力の存在確認、analyze U1）。
- [X] T012 [P] [US1] `eval/tests/unit/test_leak_guard_068.py`: 指標・reliability・paired差・CI・**provenance（model_fit_through 等）**をモデル特徴に戻さない・`eval`→`training` import 禁止（FR-016, II, analyze C1）。**対象race odds変更で予測不変・result変更で score のみ変化も本ファイルで一元化**（odds/result 不変テストは本タスクに集約、analyze D2）。
- [X] T012a [P] [US1] `eval/tests/unit/test_recent_guard.py`: recent_guard の保守的 AND — 3年/5年いずれか悪化で不合格・両非悪化で合格・**片窓/両窓が空（該当raceなし）は除外+報告し誤合格しない**（FR-008c 空窓, analyze U1）。
- [X] T012b [P] [US1] `eval/tests/unit/test_period_boundary.py`: 全/直近3年/5年 のカットオフ境界 golden（境界日ちょうどのレースの帰属・端点処理、FR-005, analyze U2）。

### Implementation for User Story 1

- [X] T013 [P] [US1] `eval/src/horseracing_eval/metrics.py` に `winner_nll`（+ excluded件数）・`started_all_logloss`/`started_all_brier`・`ece_equal_mass`・`ece_by_prob_band`・`ece_by_field_size`・**`uniform_baseline`（sanity 用の一様確率 winner NLL、算出のみ・昇格比較に使わない、FR-007 の算出 owner）** を追加。帯境界/頭数バケットは事前固定（III）。
- [ ] T014 [US1] `eval/src/horseracing_eval/harness.py` の `evaluate()` を started-all 母集団併記に拡張し、per-race loss を保持（finished-only は互換併記）。既存 reliability_bins は維持。**top2/top3 LogLoss は既存 Harville top-k harness（049）を paired 同一 race 集合で再利用して算出**（ゲート入力 FR-008d の明示源、analyze U1）。
- [X] T015 [US1] `eval/src/horseracing_eval/bootstrap.py`: block bootstrap（block = 開催日、1開催日=1ブロックの moving-block）の seeded paired-diff 95% CI（i.i.d.禁止・serial保存・seed記録、D2）。
- [X] T016 [US1] `eval/src/horseracing_eval/paired.py`: 注入された2 PredictorFactory を T005 foldfit で各fold再fit→race集合 model-blind 固定→PairedEvalReport 生成（recipe は plain dict + hash で保持、winner NLL diff・started-all・finished互換・ECE群・top2/top3・期間別[全/直近3年/5年]・bootstrap CI・snapshot監査）。data-model §3。
- [X] T017 [US1] `paired.py` に GateResult（primary/stat_guard/recent_guard/top_noninferior/calibration）を実装。閾値は T001 gate-config から読み、OOS後変更を拒否（gate artifact 改変拒否テスト含む、FR-008/009）。uniform baseline は sanity のみ・昇格比較に使わない（FR-007）。**テストで絶対ECE≥0.05 が mean-ECE non-inferiority 合格時でも `calibration=fail` を強制する非常停止のトリップを検証**（analyze T1）。
- [X] T018 [US1] `training/src/horseracing_training/cli.py` に `paired-eval` サブコマンド追加（contracts/cli.md）。candidate/active は recipe.json or model_version（metadata復元）→ **ModelRecipe から PredictorFactory を構築して `eval.paired` に注入**（T003）、`--seed`/`--gate-config`/`--use-materialized`/`--json`。read-only（DB書込なし）。
- [ ] T019 [US1] `eval/tests/integration/test_paired_e2e.py`（testcontainers）: lgbm-062 vs lgbm-061 を実 DB で paired 再評価し race_id_set_hash 一致・全指標が出る（SC-001）+ 同一seed・単一スレッドで2回実行し winner NLL/paired差/CI の絶対差 `< 1e-9`（SC-002、bit一致は要求しない）。**この demo(062↔061)は歴史的比較で A–D の運用 baseline(db-active=lgbm-063)とは別**（analyze N1）。

**Checkpoint**: US1 単独で「serving 母集団の正しい物差し」が動く（既存モデル比較だけで価値）。

---

## Phase 4: User Story 2 - 校正分割と全履歴学習の比較 (Priority: P2)

**Goal**: A/B/C/D を feature/objective/seed 固定で比較し、inner-valid screening → 勝ち候補のみ独立 window で active と paired 評価。

**Independent Test**: A〜D を直近fold（inner-valid）で比較し各 experiment の winner NLL と go/no-go を出す（SC-003）。

### Tests for User Story 2 ⚠️（先行）

- [X] T020 [P] [US2] `training/tests/unit/test_calib_score_space.py`: `softmax(s/T) == normalize(softmax(s)^(1/T))`、calibrator の score-space 誤接続拒否、power は race-normalized p に作用し Σ=1・finite・clip・no-inversion（IV, codex C7・D3）。
- [X] T021 [P] [US2] `training/tests/unit/test_day_level_split.py`: model-fit/calib 分割が開催日単位で同一日跨りなし、A の現行70/30（race数ベース）再現をテスト専用に保持（FR-014b, codex C4）。
- [X] T022 [P] [US2] `training/tests/unit/test_strict_past_oof.py`: C/D 校正 OOF の各行で `max(train_date) < prediction_date`、inner-heldout label 変更でそのfoldの予測・TE不変（FR-014a, codex C6）。
- [X] T023 [P] [US2] `training/tests/unit/test_hash_contract.py`: raw feature/schema hash は全arm同一（校正分割 arm 内、同一 feature_version）、transformed_matrix/model_artifact hash は arm別。**within-arm 再実行の hash 一致は `num_threads=1`・小さな合成データで検証**（実 multi-thread 実行の model_artifact_hash は arm 判別子であって再実行再現保証ではない、analyze I2）。（FR-018, codex C5）。

### Implementation for User Story 2

- [X] T024 [US2] `training/src/horseracing_training/calibration.py` の `split_train_by_time` を**開催日単位**に変更（同一日跨り禁止）。race数ベースは `split_train_by_race_count`（A再現用）として残す（FR-014b）。
- [X] T025 [P] [US2] `training/src/horseracing_training/calib_split_eval.py` に temperature 校正器（raw score）と race-normalized power 校正器（正規化p, 017/048 の probability 側実装を再利用）を C/D 用に配線。predictor 内 isotonic とは別経路（二重適用しない、codex C7）。
- [X] T026 [US2] `calib_split_eval.py`: A/B/C/D driver。全arm feature/objective/seed 固定、C/D は expanding strict-past OOF 生成 + score-transfer-check（inner-valid、悪化で B フォールバック理由記録）。data-model §4。
- [X] T027 [US2] driver に **inner-valid screening**（outer-valid 非参照・**T001 gate-config の事前登録 screening 基準を使用**、analyze U1）+ 勝ち候補のみ screening 非使用の独立 confirmation window で T016 paired-eval を内部再利用（FR-014, codex C2）。
- [X] T028 [US2] `training/src/horseracing_training/cli.py` に `calib-split-eval` サブコマンド追加（`--experiments A,B,C,D`/`--derisk-recent-folds`/`--full-walk-forward-winners`/`--seed`/`--json`、contracts/cli.md）。read-only（active昇格しない）。
- [X] T029 [US2] `training/tests/integration/test_calib_split_e2e.py`（testcontainers）: A〜D を直近fold で比較し winner NLL と go/no-go・C/D の transfer-check・B フォールバック理由が出る（SC-003）。A が現行70/30経路を再現・各arm within-arm 決定論（`num_threads=1` 固定時、analyze I2、codex テスト）。

**Checkpoint**: US1+US2 で「校正分割の限界効果を正しい物差しで測る」が完結。

---

## Phase 5: User Story 3 - 学習期間 provenance の記録 (Priority: P3)

**Goal**: booster 実学習期間を metadata に個別記録。

**Independent Test**: 校正分割ありの学習後、`model_fit_through < train_through` と calib_from/through が populate（SC-005）。

### Tests for User Story 3 ⚠️（先行）

- [X] T030 [P] [US3] `training/tests/unit/test_provenance.py`: 校正分割時 `model_fit_through != train_through`、全履歴refit時 `model_fit_through == train_through`、calib退化で calib_from/through=null（data-model §5）。

### Implementation for User Story 3

- [X] T031 [US3] `training/src/horseracing_training/predictor.py` の `fit_info_` に `model_fit_through`（model-fit分割の最大race_date）・`calib_from`/`calib_through`（校正データ期間min/max）を追加。既存 n_model_rows/n_calib_rows/train_through は維持・意味変えない（FR-015）。
- [X] T032 [US3] `training/src/horseracing_training/artifacts.py` が新 provenance を metadata.json / metrics_summary に透過することを確認（既存行は遡及書換なし、040/050前例）。

**Checkpoint**: 3ストーリー全て独立に機能。

---

## Phase 6: Polish & Cross-Cutting

- [X] T033 [P] 契約不変の回帰: スキーマ/API/OpenAPI/FEATURE_VERSION/**`feature_schema_hash`（列名/列順の不変 hash、model_artifact_hash は arm別で変わるのが正常, analyze A1）** に diff なしを確認（`git diff` + migration head 不変、FR-017/FR-018, SC-006）。
- [X] T034 [P] quickstart.md の SC-001/002/003/005/006 を通し実行し結果を検証（SC-004=ゲート機械判定+artifact改変拒否は T017 unit で担保・quickstart 対象外、analyze Cov1）。
- [ ] T035 [P] research D7 の codex 必須テスト一覧と実装済みテスト（T007–T012b, T020–T023, T030）を突き合わせ、未カバー項目のみ補完（odds/result 不変は T012 に統合済み、analyze D2 — 重複作成しない）。
- [X] T036 ruff/lint クリーン（068 変更ファイル）+ eval/training テストスイート緑を確認。
- [X] T037 [P] `docs/plan/model-accuracy-improvement-proposal.md` の Phase 0/1 実行順に本feature完了を反映（後続 Phase -1/2/3/4 が本評価契約を前提にする旨）。

---

## Dependencies & Execution Order

- **Setup (P1)**: 依存なし。
- **Foundational (P2)**: Setup 後。US1/US2 を BLOCK（T003 recipe / T004 母集団 / T005 foldfit / T006 hash は両ストーリーの前提）。
- **US1 (P3)**: Foundational 後。単独完結（MVP）。
- **US2 (P4)**: Foundational + **US1 の T016 paired-eval に依存**（confirmation window で再利用）。T024–T027 は US1 と別ファイルだが T016 完成後に T027 が結線。
- **T005 の依存**: T005（eval foldfit harness）は **T002 の PredictorFactory Protocol にのみ依存**し、T003（training/recipe.py）には依存しない（eval→training import 禁止の維持、analyze C1）。実 recipe は T018 の CLI 結線・統合時にのみ必要。
- **US3 (P5)**: Foundational 後。US1/US2 と独立（predictor 単独変更）。US1/US2 と並行可。
- **Polish (P6)**: 全ストーリー後。

### Within Each Story

- テスト先行（FAIL 確認）→ 実装。
- eval: metrics → harness → bootstrap → paired → CLI。
- training: split変更 → calibrator配線 → driver → CLI。

### Parallel Opportunities

- Setup T001/T002、Foundational T003/T003t/T004/T006 は並行可。T005 は T002（同一ファイル foldfit.py・Protocol 定義）の後に逐次で実装するが、T003/T004/T006 とは並行可（T003 の実 recipe は T018 統合時にのみ必要）。
- US1 テスト T007–T012 は並行。US2 テスト T020–T023 は並行。
- US3（T030/T031/T032）は US1/US2 と並行可能（別 developer）。

---

## Parallel Example: User Story 1 テスト

```bash
uv run --project eval pytest eval/tests/unit/test_winner_nll.py eval/tests/unit/test_population_masks.py \
  eval/tests/unit/test_ece_variants.py eval/tests/unit/test_bootstrap.py eval/tests/unit/test_leak_guard_068.py
```

---

## Implementation Strategy

### MVP First (US1 のみ)

1. Phase 1 Setup → Phase 2 Foundational → Phase 3 US1。
2. **STOP & VALIDATE**: lgbm-062 vs lgbm-061 を paired 再評価（SC-001）。既存モデル比較だけで「物差し是正」の価値を確認。

### Incremental Delivery

1. Setup + Foundational → 再fit harness ready。
2. US1 → 正しい物差し（MVP）。
3. US2 → 校正分割 A〜D 実験。
4. US3 → provenance。

---

## Notes

- [P] = 別ファイル・依存なし。
- **correctness-critical（codex C1–C4）**: T003(market_offset fail-closed) / T005(recipe再fit) / T024(日単位split) / T027(inner-valid screening) は設計の要。ここを崩すと「物差し自体がリークする」。
- 校正・HPO・特徴を同時に変えない（提案書の規律）。本feature は評価契約 + 校正分割のみ。
- codex second opinion 取得済み（research D7）。実装中に高リスク判断が出たら親から `codex exec` 直叩きで再確認（[codex-env-recovery]）。
- commit は task 単位 or 論理グループ。checkpoint で US 独立検証。
