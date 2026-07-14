---
description: "Task list for 069 past-odds features + subgroup gate"
---

# Tasks: 過去オッズ量特徴(F02)+ subgroup ゲート拡張

**Input**: Design documents from `specs/069-past-odds-features/`

**Prerequisites**: plan.md, spec.md, research.md (D1–D8), data-model.md, contracts/cli.md, gate-config.json

**Tests**: 含む(TDD)。spec の Edge Cases・research D8 の codex 必須テストは correctness-critical のため実装前にテストを先行させる。

**Organization**: US1(subgroup ゲート拡張)→ US2(F02 bundle)。F02 の採否は US1 の subgroup 付き paired-eval に依存する。

## Path Conventions

- features: `features/src/horseracing_features/`, tests `features/tests/`
- eval: `eval/src/horseracing_eval/`, tests `eval/tests/{unit,integration}/`
- training: `training/src/horseracing_training/`, tests `training/tests/`
- 実行: `uv run --project <pkg> ...`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 事前登録 artifact の確定と loader の odds 追加(F02 の前提)。

- [X] T001 [P] `specs/069-past-odds-features/gate-config.json` を確定(**grain 別 subgroup margin: winner_nll ε=0.005 / horse_logloss ε=0.001**[analyze I1]・top_noninferior/calibration は top-level[analyze F1]・critical=[2026_only,nk,2026_nk]・三値判定・intersection-union・race/horse grain 集合・eval_window・F02 式パラメータ recent-K=有効観測/λ/trend/sd5 ddof/q complete-field/odds valid range)。OOS 前固定(FR-002/FR-011, III)。**odds_valid_range は保守的に暫定凍結し、read-only の sentinel quick-check(overround/境界値 1.0/999.9/0 の頻度を source 別=JRA-VAN/netkeiba で報告し、odds==1.0 の元返し馬を系統的に落としていないか測る、analyze I2)で確認**する。T001/T020 は **results-blind(odds 分布のみ)に pre-OOS で either 方向に確定し、その後凍結=post-OOS 不変**(結果を見る前の確定なので III 準拠、analyze I1/F2)。
- [X] T002 `features/src/horseracing_features/loader.py` に `RaceHorse.odds` を追加ロード(現状 popularity のみ)。**新ソース列**として source_fingerprint を odds 込みに拡張(056 同型, codex C4)。materialize stale 検知が odds backfill を捕捉することを確認。

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: US1/US2 双方が使う subgroup 割当基盤と F02 の q/s コア。

**⚠️ CRITICAL**: US1/US2 着手前に完了必須。

- [X] T003 [P] `eval/src/horseracing_eval/subgroups.py`: **注入された per-race/per-horse 属性**から band 割当(race-level: 2026_only/2026_field_has_nk、horse-level: canonical/nk/2026_nk/coverage帯)+ 集計。**`2026_field_has_nk` は注入された per-horse `nk:` prefix をレース単位に roll-up して導出**(analyze U2)。coverage 帯は注入 obs_count が無ければ出さない。結果ラベル非参照・overround 監査は含めない(codex C1/C7・FR-004)。
- [X] T004 [P] `features/src/horseracing_features/pm_core_strength.py` に **race 単位 q/s コア**を実装: started 全馬の有効オッズが揃うレースのみ `q_ik=(1/O_ik)/Σ(1/O_jk)`・`s_ik=log(q_ik×N_k)`(部分 field は s を作らない、FR-006・codex C3/D3)。**有効オッズ = gate-config.odds_valid_range(`1.0 ≤ O < 999.9`、`1.0` は元返し本命で有効、無効 sentinel は `≤0`・非有限のみ、999.9 は T001 確認保留)**(analyze F1/F2)。**N=1/N=0 started を div/log エラーなく処理し、s=0 の N=1 を obs_count に数える(gate-config.f02_formula.count_n1_s0_in_obs=true で凍結、analyze E1/U1)**。

**Checkpoint**: subgroup 割当 + q/s コアが揃い US1/US2 着手可能。

---

## Phase 3: User Story 1 - subgroup ゲート拡張 (Priority: P1) 🎯 MVP

**Goal**: 068 paired-eval に race/horse grain 別 subgroup CI + 三値 intersection-union ガードを追加し「全体改善だが 2026/nk: で死」を捕捉。

**Independent Test**: 2 recipe を paired 評価し race-level(2026_only/2026_field_has_nk)/ horse-level(canonical/nk/2026_nk/coverage帯)の CI と critical 三値ガードが1レポートに出る(SC-001)。

### Tests for User Story 1 ⚠️（先行）

- [X] T005 [P] [US1] `eval/tests/unit/test_subgroups.py`: race-level/horse-level grain 別割当・`2026_nk` 交互群・結果ラベル変更で割当不変(属性のみ)・missing annotation fail-close・dead-heat/empty 群(codex C1/C3)。
- [X] T006 [P] [US1] `eval/tests/unit/test_subgroup_gate.py`: 三値判定(PASS=CI上限<ε / FAIL=CI下限>ε / NO_DECISION=跨ぐ)・intersection-union(critical 全PASSで採用)・NO_DECISION は非否決だが十分条件でもない(codex C2)。
- [X] T007 [P] [US1] `eval/tests/unit/test_subgroup_bootstrap.py`: subgroup 別 開催日 block bootstrap の seed 決定論・少開催日で NO_DECISION・subgroup 内 cand−uniform 絶対水準(codex C6)。

### Implementation for User Story 1

- [X] T008 [US1] `eval/src/horseracing_eval/paired.py` を拡張: per-race winner NLL 差と started-all per-horse loss を subgroup 集計(T003 注入)、subgroup 別 block bootstrap CI(068 bootstrap 再利用)、`SubgroupGateResult`(data-model §4)。既存 068 ゲートは不変で加算(FR-005)。**`--subgroups` なしの PairedReport が 068 baseline とバイト同等である後方互換 assert を追加**(共有 paired.py の回帰面を明示テスト、analyze C1)。
- [X] T009 [US1] `paired.py` に subgroup ガード: critical(2026_only/nk/2026_nk)の三値判定を intersection-union で採否に加算、cand−uniform 絶対水準を診断併記(FR-002/003, codex C2/C6)。gate-config から閾値読取。**gate-config の top_noninferior/calibration/subgroup margin が実際に `_build_gate` へ届くことをテストで検証**(068 は top-level 読み=069 config も top-level、distinctive 値を入れて gate が使うか assert、analyze F1)。
- [X] T010 [US1] `training/src/horseracing_training/cli.py` の `paired-eval` に `--subgroups` フラグ追加(未指定時 068 と byte 同等=後方互換)。属性(race_date/nk: prefix/厳密前観測数)を eval に注入。
- [X] T011 [US1] `eval/tests/integration/test_subgroup_e2e.py`(testcontainers): 実 DB で 2 recipe を `--subgroups` paired 評価し race/horse subgroup CI・三値ガードが出る(SC-001)。同一 seed・単一スレッドで決定論。

**Checkpoint**: US1 単独で「2026/nk: を見る物差し」が動く(F02 なしでも既存モデル比較に使える)。

---

## Phase 4: User Story 2 - F02 pm_core_strength bundle (Priority: P2)

**Goal**: 過去オッズ量 `s=log(q×N)` を strictly-before as-of 集約し、features-018 純加算・accuracy-first candidate で US1 拡張ゲート評価。

**Independent Test**: features-018 を build し 058 込み baseline に対し F02 candidate を US1 subgroup ゲート付き paired 評価(SC-004)。

### Tests for User Story 2 ⚠️（先行）

- [X] T012 [P] [US2] `features/tests/test_pm_core_strength.py`: q/s 数式(共通オッズ倍率不変・Σq=1・一様 s=0・単調)・1頭でも無効オッズなら race 全体 q 無効・**odds==1.0(元返し本命)は有効=強本命レースを落とさない**(analyze D1)・recent-K=有効観測・trend(直近3単回帰)・sd5(ddof=1)・best5 の手計算 golden・2観測未満 NaN・0観測 has_obs=0(FR-006/010/011, codex D2)。**F02 coverage-loss アサート**: complete-field 除外で強本命レースが系統的に落ちない(1.0 保持で確認)。
- [X] T013 [P] [US2] `features/tests/test_pm_leak_guard.py`: 今走・同日・未来のオッズ変更で F02 不変、過去オッズ変更で変化。列名に odds/popularity トークンなし(FR-007/014, behavioral leak-guard)。行順・race 内馬順を変えても完全一致(決定論)。
- [X] T014 [P] [US2] `features/tests/test_features018_parity.py`: features-017 build と features-018 build の共有列 byte-parity(check_exact/check_dtype)・F02 は additive left-merge(FR-009, codex D5)。**共有列数を実測して期待値(≈128、literal を無検証で使わない)と一致 assert**(analyze V1)。
- [X] T015 [P] [US2] `serving/tests/test_lgbm063_compat.py`: features-018 registry 下で lgbm-063(features-017)が compat pin で load・同一入力で予測 byte 一致・wrong hash/version 拒否(FR-009, codex)。**pin する hash は lgbm-063 metadata.feature_hash を実測して registry の値と一致検証**(literal `300b28a9…` を無検証で使わない、analyze V1)。**シーケンス**: hash 一致 assert は T017 前の hard gate、compat-load assert は T017(features-018 registry 生成)後(analyze S1)。

### Implementation for User Story 2

- [X] T016 [US2] `features/src/horseracing_features/pm_core_strength.py` に **as-of 集約**を実装(T004 の q/s → 馬単位 strictly-before merge_asof allow_exact_matches=False + 同日除外、058 idiom)。9列(data-model §1)・欠損 NaN + obs_count/has_obs(FR-007/010)。
- [X] T017 [US2] `features/src/horseracing_features/registry.py`: FEATURE_VERSION features-017→**018**・独立 group `pm_core_strength`(058 rank は削除しない、FR-008)・`COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-018"]={"features-017":"300b28a9…"}`(FR-009)。ALL_COLUMNS 自動導出。
- [X] T018 [US2] `features/src/horseracing_features/materialize.py` の build_asof に F02 ブロック結線(single as-of 源、025 同型・materialize/in-memory bit-parity・stale fail-closed、FR-015)。**odds を source_fingerprint に含めるため既存 materialized parquet は無効化 → 一度きり再 materialize が必須**(055 前例、quickstart/運用ノートに明記、analyze U1)。
- [X] T019 [US2] `training/src/horseracing_training/recipe.py`: accuracy-first candidate recipe(features-018 全群 F02 込み)+ active recipe。**`drop_features` は列名タプルで group 名では drop されない**(predictor は `c not in drop_features` で列フィルタ、group→列 展開は cli の `--drop-groups` のみ)→ **`FEATURE_GROUPS` を反転して group の展開列を drop_features に渡す**(analyze F1)。active = `pm_core_strength` の展開列のみ drop(058 rank は両者に残す)。default 意思決定支援モデルは **market-history = {`past_market`, `pm_core_strength`} の展開列を全 drop**。**active 側特徴集合が実際に pm_core_strength 列を含まないことを assert**(fail-open 防止)。
- [X] T020 [US2] `training/src/horseracing_training/cli.py` に `coverage-audit`(年×source×coverage帯の 1/3/5走 coverage + overround/境界値率/pop-vs-qrank 不一致、SC-005・D7)。read-only。
- [X] T021 [US2] `features/tests/test_pm_materialize_parity.py` + `training/tests/test_default_model_drops_market.py`: materialize parity・default モデルに **market-history 両群(`past_market` 058 rank + `pm_core_strength` F02)の展開列が1つも入らない**(F02 だけでなく 058 rank も=p⊥q leak-guard、analyze M1、SC-006）。**F02 candidate 予測が 009 経路で Σp≈1 を満たす回帰 assert**(IV を exemption でなくテストで閉じる、analyze C1)。
- [X] T021a [US2] **F02 採否の paired-eval を凍結窓(gate-config.eval_window)で実行し機械判定を artifact に記録**(candidate=features-018 全群 vs active=F02 群 drop、accuracy-first、--subgroups、SC-004/FR-013)。P2 の成果=採否 verdict を US2 の第一級 deliverable にする(analyze C1、T023 は quickstart smoke)。

**Checkpoint**: F02 が features-018 で build でき、US1 ゲートで採否判定できる。

---

## Phase 5: Polish & Cross-Cutting

- [X] T022 [P] 契約不変の回帰: スキーマ/API/OpenAPI/migration に diff なし、FEATURE_VERSION bump は compat pin のみ(SC-006, FR-016)。
- [X] T023 [P] quickstart.md の SC-001〜006 を通し**スモーク実行**(実 DB: features-018 parity + lgbm-063 compat + subgroup 付き F02 paired-eval + coverage-audit)。**採否 verdict の正本記録は T021a**(T023 は smoke 再実行のみ・二重記録しない、analyze L2)。
- [X] T023a [P] `eval/tests/unit/test_subgroup_leak_guard.py`: subgroup CI・coverage-audit 出力がモデル特徴に戻らない(import-graph + behavioral、II、analyze F6)。既存 068 leak-guard と同型。
- [X] T024 [P] `docs/plan/model-accuracy-improvement-proposal.md` の Phase 3 に 069 実装状況を反映。
- [X] T025 ruff/lint クリーン(069 変更ファイル)+ features/eval/training/serving テストスイート緑。

---

## Dependencies & Execution Order

- **前提**: 068(paired.py / bootstrap.py / gate machinery、commit 4233776)が 069 base に merge 済み(確認済み=ancestor)。T008–T010 はこれを拡張する(analyze D1)。
- **Setup (P1)**: T001 独立。T002(loader odds)は F02(T004/T016)の前提。
- **Foundational (P2)**: Setup 後。T003(subgroups)は US1 を、T004(q/s コア)は US2 を BLOCK。
- **US1 (P3)**: Foundational(T003)後。単独完結(MVP)。**critical 3 subgroup(2026_only/nk/2026_nk)は F02 非依存**で動く。**coverage 帯 subgroup は F02 の `asof_pm_obs_count` が前提**のため US2(T016)後に populate される診断(non-critical、analyze U1)。US1 の subgroup 割当(T003)は obs_count が注入されない場合 coverage 帯を出さず critical のみで動く。
- **US2 (P4)**: Foundational(T004)+ T002(odds loader)後。**採否は US1 の T008/T009(subgroup paired-eval)に依存**。
- **T015 → T017 の hard gate**(analyze C1): T017 が compat pin の literal hash を registry に入れる**前に**、T015 が lgbm-063 metadata.feature_hash を実測して literal と一致検証する。不一致なら T017 は literal を merge しない(SC-002 serving byte-parity 死守)。
- **T001 → T021a の blocking 前提**(analyze A1): T021a(F02 採否 paired-eval)を走らせる**前に**、T001 の sentinel/scale quick-check を実行し、**per-grain diff scale を実測して margin(winner_nll ε / horse_logloss ε)と odds_valid_range を凍結**する(III 事前登録=直感でなく実測で確定、tighten のみ可)。
- **Polish (P5)**: 全ストーリー後。

### Within Each Story

- テスト先行(FAIL 確認)→ 実装。
- US1: subgroups → paired 拡張 → gate → CLI。
- US2: q/s コア → as-of 集約 → registry(version/compat)→ materialize → recipe。

### Parallel Opportunities

- Setup T001。Foundational T003/T004(別ファイル)。
- US1 テスト T005–T007 並行。US2 テスト T012–T015 並行。
- US1(eval)と US2 の feature 実装(features)は別パッケージで一部並行可能だが、F02 採否(US2)は US1 完成後。

---

## Parallel Example: User Story 2 テスト

```bash
uv run --project features pytest features/tests/test_pm_core_strength.py features/tests/test_pm_leak_guard.py \
  features/tests/test_features018_parity.py
uv run --project serving pytest serving/tests/test_lgbm063_compat.py
```

---

## Implementation Strategy

### MVP First (US1 のみ)

1. Setup(T001/T002)→ Foundational(T003)→ US1(subgroup ゲート)。
2. **STOP & VALIDATE**: 既存 2 recipe を `--subgroups` paired 評価し 2026/nk: subgroup CI が出る(SC-001)。F02 なしでも物差しの価値を確認。

### Incremental Delivery

1. Setup + Foundational → subgroup 割当 + q/s コア。
2. US1 → 2026/nk: を見る subgroup ゲート(MVP)。
3. US2 → F02 bundle を US1 ゲートで採否。

---

## Notes

- [P] = 別ファイル・依存なし。
- **correctness-critical(codex C1–C5)**: T003(grain 分離)/ T009(三値 intersection-union)/ T004(q complete-field)/ T002(odds loader)/ T019(paired-eval 経路・058 保持)。ここを崩すと物差し空洞化 or F02 不動作。
- 058 rank 4列は削除しない(帰属分離、T017/T019)。default モデルに F02 を入れない(p⊥q、T019/T021)。
- codex second opinion 取得済み(research D8)。高リスク判断は親から `codex exec` 直叩きで再確認([codex-env-recovery])。
