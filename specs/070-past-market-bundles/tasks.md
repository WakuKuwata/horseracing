---
description: "Task list: 070 過去市場 F03/F04/F05 bundle"
---

# Tasks: 過去市場 rank/residual/conditioned bundle(F03/F04/F05)

**Input**: [plan.md](plan.md) / [spec.md](spec.md) / [research.md](research.md) / [data-model.md](data-model.md) / [contracts/cli.md](contracts/cli.md) / [gate-config.json](gate-config.json)

**Prerequisites**: 069 merge 済(features-018 / lgbm-064-f02acc candidate / lgbm-063 active)。DB port 15432。

**Tests**: 含む(憲法 II の leak-guard・III の parity/採用ゲートは MUST=テスト必須)。

**Organization**: US1=F03(P1)/ US2=F04(P2)/ US3=F05(P3)。各 bundle は独立に **採否**できる(build は共有 primitive に依存=adoption≠import: F04 は F03 の u を、F05 residual は F04 の finish_residual を import)。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列可(別ファイル・未完了タスクに非依存)
- 列名は spec.md 正本(codex B1 で再整合済)。

---

## Phase 1: Setup

- [x] T001 069 baseline を確認: `uv run --project features python -c "from horseracing_features.registry import FEATURE_VERSION,model_input_features,FEATURE_GROUPS; print(FEATURE_VERSION,len(model_input_features())); print(sorted(set(FEATURE_GROUPS.values())))"` が `features-018 137` を返し、`pm_core_strength`/`past_market` 群が出力集合に含まれることを確認。**+ `materialized_columns()` の件数 = 112 を確認**(T031 が 131=112+19 を exact assert する baseline を仮定でなく実測で固定・analyze U2)(features/src/horseracing_features/registry.py)。
- [x] T002 lgbm-064-f02acc / lgbm-063 の **完全 feature_hash を artifact metadata から実測**し gate-config.json `serving.compat_pins` のプレースホルダを埋める。**注(analyze L2)**: features-017 の pin hash は既に registry に在る(`COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-018"]["features-017"]=300b28a9…`=lgbm-063 自身の hash・再利用可・metadata で裏取り)、**新規に実測が要るのは lgbm-064-f02acc の features-018 hash のみ**(specs/070-past-market-bundles/gate-config.json)。**+ `training paired-eval --help` を叩き 069 の再利用フラグ(`--candidate/--active/--gate-config/--subgroups/--from/--to`)の署名を fast-fail 確認**(長い eval 実行前に signature drift を検出・analyze U3)。**+ base recipe `pl_topk:isotonic:0.3` が lgbm-064-f02acc の config(objective/calibration/calib_frac）と一致することを metadata で確認**(不一致だと accuracy-first base が非代表=全段 verdict がバイアス・analyze I1)。**+ lgbm-064-f02acc の TE 列集合を metadata から読み T033 の `--target-encode` と一致させる**(036 lineage=`jockey_id,trainer_id`・CLI 既定 venue_code を混ぜない=T032 stack 比較の TE confound 防止・analyze I1)。

---

## Phase 2: Foundational(全 bundle の前提・ブロッキング)

- [x] T003 **共有 primitive**: features/src/horseracing_features/pm_core_strength.py の `_race_support` を、complete-field の **q / s / N を返す公開関数**(例 `race_market_primitive`)にリファクタ(現状 s のみ返す)。069 の s 出力は **byte 不変**(回帰テスト)—F04/F05 が q を、F03/F04 が u を再計算せず共有する土台(codex 論点2)。
- [x] T004 [P] FEATURE_VERSION を `features-018`→`features-019` に bump し、`COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-019"]={"features-018":<full>,"features-017":<full>}`(T002 の完全 hash・**非推移で両直接 pin**)を追加。既存 059/061/069 履歴 entry は不変(features/src/horseracing_features/registry.py)。
- [x] T005 FEATURE_VERSION を `features-018` と hard-code した version-assertion テストを **`grep -rn "features-018" features/ serving/ training/` で列挙**(固定件数を仮定しない=grep miss で bump 時に stale assertion が残らない・analyze U2)し `features-019` に機械更新。**⚠ `COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-018"]`(registry.py の compat-pin キー=018 のまま維持・非推移 pin・FR-007)と artifact metadata の 018 は書き換えない=テストディレクトリの assertion のみ対象**(T004 の後に実行・M1 hazard 回避のため非並列)(features/tests, serving/tests, training/tests)。
- [x] T006 pm_core_strength 公開 primitive の単体テスト: complete-field q の Σ=1・N・s=log(q·N) 一致・部分 field は void(features/tests/unit/test_pm_core_strength_primitive.py)。
- [x] T006a **段階評価の事前登録ガード**(analyze C1/A1/F1・**gate-config matrix が正本**): training/tests/unit/test_staged_matrix_symmetry.py で機械検証(operator の desync/窓 drift 防止・帰属正当性 FR-006a・III):(1) `staged_evaluation.stages` 内で各段の candidate/active が **bundle-under-test 群だけで差分・`both_drop` 両 arm 同一**、(2) **全段(1-5)を matrix + verdict 分岐規則から再構築して照合(analyze C1)**=各段の drop 群集合を `_expand_group_drops` で列展開し `ModelRecipe` を構築(matrix `both_drop`/`candidate_add`/`_base` から直接=prose 非依存)、**F03(ADOPT/REJECT)× F04(ADOPT/REJECT)× support(ADOPT/REJECT)の分岐 fixture で段2-5 の candidate/active recipe を生成し contracts の `<F03D>/<F04D>/<F05supportD>` 展開と byte 一致**(段1 だけでなく全段=desync/F1 型ドリフトを検出)、(3) **F03/F04 verdict 分岐の帰属**=F03 ADOPT で `past_market` drop・REJECT で `pm_rank_robust` drop / **F04 ADOPT で downstream(段3-5)base が `pm_expectation_residual` を keep・REJECT で drop**(勝者のみ base 累積・`f03/f04_verdict_resolution` bookkeeping・inversion & F04→F05 base 累積漏れの回帰ガード=analyze F1/F2・F05 support が F04 の分散を過大計上しない III 帰属)、(4) **gate-config `eval_window` が contracts/quickstart の `--from/--to` と byte 一致**、(5) **凍結式パラメータ↔コード定数の束縛(III・analyze C1)**=gate-config `f03/f04/f05_formula`(min_obs=3・λ=5・sd_ddof=1・finish_window=5・win_window=10・**sd の obs<2→NaN 閾値**[mean 列の min_obs=3 とは別・analyze I1]・fav_window=5・distband bins 等)を読み、feature モジュールの実定数と一致することを assert(コード↔gate-config の silent drift 防止)、(6) **compat pin の二重管理照合(analyze F1)**=registry `COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-019"]` の 018/017 hash が gate-config `serving.compat_pins` と一致、(7) **F05 distband bins が 020/023 の `≤1400/≤1800/≤2200/>2200` と一致**(gate-config `distband_bins:"existing_020_023"` の literal drift 防止・analyze D1)。**T006a は段階評価(T013 以降)の実行前に通す blocking precondition**(operator 駆動 orchestration の window/verdict-branch/param drift を eval 実行前に fail-fast=analyze P1・codex 論点3 の残リスク緩和)。

**Checkpoint**: features-019 に bump 済・共有 q/s/N primitive 稼働・compat 両 pin 済 → US1(F03)実装可能。**F04(US2)は追加で US1 の T007 が公開する u primitive に依存**(adoption ≠ import=F03 群 REJECT でも u は使える)、F05(US3)は F04 に依存。

---

## Phase 3: User Story 1 — F03 pm_rank_robust(P1)

**Goal**: 過去市場人気 rank を percentile 化(popularity-only complete-field)して 058 生 rank の置換候補にする。

**Independent Test**: features-019 build → candidate(`past_market` drop + F03)を baseline(058・F03 なし)に対し 069 subgroup 付き paired-eval → winner NLL・2026/nk: subgroup・CI。

- [x] T007 [US1] features/src/horseracing_features/pm_rank_robust.py 新規: **u primitive**(`u=1-(rank-1)/(N_started-1)`・N=1→u=1)を公開(F04 と共有)+ **popularity-only complete-field**(started 全馬に valid popularity)+ **competition rank tie**(行順非依存)+ started 内 re-rank。strictly-before + 同日除外(058 idiom)で 5 列: `asof_pm_rankpct_last`/`asof_pm_rankpct_mean5`/`asof_pm_favorite_rate5`/`asof_pm_top3fav_rate5`/`asof_pm_rank_obs_count`(F02 obs と別・min_obs=3 未満 NaN)。
- [x] T008 [US1] registry に独立 group `pm_rank_robust`(5列)を追加(features/src/horseracing_features/registry.py)。
- [x] T009 [US1] materialize の `build_asof_features` に F03 を **additive left-merge**(右キー (horse_id,race_id) 一意・列名 disjoint)。**source_fingerprint 不変**を確認(新ソース列なし)(features/src/horseracing_features/materialize.py)。
- [x] T010 [P] [US1] leak-guard テスト: 対象レース popularity/results を変えても F03 不変・過去 popularity で変化・`asof_pm_*` 命名・odds/popularity トークン非露出(features/tests/unit/test_pm_rank_robust_leak.py)。
- [x] T011 [P] [US1] 数式テスト: competition rank tie(入力行順シャッフル不変)・popularity-only complete-field・N=1→u=1・rate∈[0,1]・rank_obs_count が F02 obs と独立・**`asof_pm_rankpct_last`(直近1値)も obs<3 で NaN**(min_obs=3 は last 列にも適用=1-obs 高分散を出さない・analyze A1)(features/tests/unit/test_pm_rank_robust.py)。
- [x] T012 [P] [US1] additive parity テスト `test_pm_rank_robust_is_purely_additive`(共有列を数学的に perturb しない=features 内で完結)(features/tests/unit/test_pm_rank_robust_parity.py)。**注(analyze F2)**: F03↔058 の相互排他(SC-002/FR-002 の帰属)は recipe/`_expand_group_drops` が training 側にあるため **T006a(training/tests)が所有**=features 単体テストで cross-package import しない。
- [ ] T013 [US1] **F03 置換 paired-eval**(段1): contracts/cli.md の完全 recipe(両 arm から F04/F05 群 drop・`--active`・`--from 2019-01-01 --to 2026-07-12 --subgroups --gate-config`)を実行し、**verdict=`gate.adopted AND subgroup_guard`** を記録(out/f03.json)。**最初の out/f03.json で `subgroups.subgroup_guard` キーの存在を assert**(driver AND が正しい JSON 形状に依存・analyze U3)。F03 verdict を次 baseline に固定。**各段 verdict artifact に 2 残リスクを明記**(analyze C1/C2):(a)採否は TE-free re-fit 上・登録は TE 付き=TE-free↔TE delta を過大解釈しない、(b)同一 2019–2026 OOS 上の段階選択=stack は独立確認でない(production 昇格前に time holdout 要・スコープ外)。

**Checkpoint**: F03 単体緑・additive parity・段1 verdict 記録済。

---

## Phase 4: User Story 2 — F04 pm_expectation_residual(P2)

**Goal**: 市場の"読み違い"(人気より上・市場確率超えの勝率)を符号付き残差で持つ。

**Independent Test**: F04 additive candidate を baseline(F04 なし)に対し 069 subgroup 付き paired-eval。

- [x] T014 [US2] features/src/horseracing_features/pm_expectation_residual.py 新規: `finish_residual=v-u`(v=`1-(finish_order-1)/(N_started-1)`・**分母 N_started**・finished 母集団)・`win_residual=I(win)-q`(started 母集団・非勝利=0)。**q は F02 公開 primitive、u は F03 の u primitive を共有**(再計算しない)。6 列(spec 正本): `asof_pm_finish_resid_mean5`/`asof_pm_finish_resid_career`/`asof_pm_win_resid_mean10`(直近**10**)/`asof_pm_win_resid_career`/`asof_pm_resid_sd5`(ddof=1・win_residual の sd)/`asof_pm_result_obs_count`。**per-race の finish_residual を公開 primitive として expose**(T021 の F05 residual が import・adoption≠import=u/q と同型)。
- [x] T015 [US2] registry に独立 group `pm_expectation_residual`(6列)を追加(features/src/horseracing_features/registry.py)。
- [x] T016 [US2] materialize に F04 additive left-merge・source_fingerprint 不変を確認(features/src/horseracing_features/materialize.py)。
- [x] T017 [P] [US2] leak-guard テスト: 対象レース results/odds を変えても不変・過去 results×過去 q で変化・`asof_pm_*` 命名・**odds/popularity トークン非露出**(FR-010)(features/tests/unit/test_pm_expectation_residual_leak.py)。
- [x] T018 [P] [US2] 数式テスト: e/w∈[-1,1]・**N_started 分母**(DNF で分母不変)・win は mean10・resid_sd5 ddof=1・**win_resid の started 母集団が 068 win_realized と行一致**・q が F02 primitive と一致・**resid_sd5 は obs<2 で NaN(ddof=1・mean 列の min_obs=3 と別閾値・analyze I1)**・**q 欠損(F02 complete-field 不成立)レースは win_residual 観測を生成しない**(SC-003)・**finish_resid_* は finished 数<3 で NaN(started 数でなく=多出走・少完走で 1-obs 残差を出さない、gate-config `finish_resid_gate_count`)**・**F04 の u が F03 の u primitive と共有行で一致(再計算しない=adoption≠import、FR-004)**・**`asof_pm_resid_sd5` は win_residual の sd(finish でない、gate-config `sd_residual`)**(features/tests/unit/test_pm_expectation_residual.py)。
- [x] T019 [P] [US2] additive parity テスト `test_pm_expectation_residual_is_purely_additive`(features/tests/unit/test_pm_expectation_residual_parity.py)。
- [ ] T020 [US2] **F04 追加 paired-eval**(段2): 現 base(F03 verdict 反映)+F04 vs 現 base、両 arm から F05 群 drop。verdict 記録(out/f04.json)。**F04 ADOPT/REJECT を固定**(F05 residual 段の前提)。

**Checkpoint**: F04 単体緑・additive parity・段2 verdict(ADOPT 可否)記録済。

---

## Phase 5: User Story 3 — F05 pm_conditioned_support / _residual(P3)

**Goal**: 条件別(surface/distband/venue)の過去市場評価/残差を階層縮約で持つ。列別依存(support←F02, residual←F04)。

**Independent Test**: F05 support/residual を別 additive candidate として paired-eval。support は F02 採用が前提、finish_resid は F04 採用が前提。

- [x] T021 [US3] features/src/horseracing_features/pm_conditioned.py 新規: surface/distband/venue 条件別の **λ=5 階層縮約**。**distband は既存 020/023 の `dist_band` bin 境界ヘルパを再利用**(`≤1400/≤1800/≤2200/>2200`・新列名は `distband` だが bin 定義は既存 `dist_band` と同一=独自 bins を作らない・analyze M1)。**as-of は「最新 cell の累積 sum/count」と「target 直前の overall parent sum/count」を別々に取得してから縮約**(縮約済み値を持ち越さない・codex 論点1)。親=cumsum−当日(pool-end 非依存)。n_cell=0 は親 fallback。**軸別 valid count**(実セルのみ・親fallback除外)。support 群列 `asof_pm_support_{surface,distband,venue}` + `asof_pm_support_cond_count_{surface,distband,venue}`、residual 群列 `asof_pm_finish_resid_surface` + `asof_pm_finish_resid_surface_count`。support は F02 s、residual は F04 finish_resid を条件別に(共有 primitive 利用)。**residual 列は F04 が後で REJECT でも build する(全 arm で drop=NOT_RUN は eval の話・列の存在≠採用・adoption≠import・analyze A1)**。
- [x] T022 [US3] registry に **2 独立 group** `pm_conditioned_support` / `pm_conditioned_residual` を追加(別 drop のため・codex B3)(features/src/horseracing_features/registry.py)。
- [x] T023 [US3] materialize に F05 additive left-merge・source_fingerprint 不変を確認(features/src/horseracing_features/materialize.py)。
- [x] T024 [P] [US3] leak-guard テスト: 対象レース非参照・過去のみで変化・`asof_pm_*` 命名・**odds/popularity トークン非露出**(FR-010)(features/tests/unit/test_pm_conditioned_leak.py)。
- [x] T025 [P] [US3] 数式テスト: λ=5 縮約・n_cell=0 親 fallback・**親も空(デビュー馬)→ NaN(0 代入しない・0/0 回避・analyze U1)**・**軸別 valid count が実セルのみ**(親fallback除外)・**未来 pool を足しても過去行不変**(pool-end 非依存)・**distband の bin 境界が既存 `dist_band` ヘルパの edges と一致**(config 文字列でなく実 bin 値で assert・analyze T1)(features/tests/unit/test_pm_conditioned.py)。
- [x] T026 [P] [US3] additive parity テスト `test_pm_conditioned_is_purely_additive`(support/residual 両群)(features/tests/unit/test_pm_conditioned_parity.py)。
- [ ] T027 [US3] **F05 support paired-eval**(段3): 現 base+`pm_conditioned_support` vs 現 base、residual 群 drop。verdict 記録(out/f05_support.json)。**注(analyze L2)**: support-requires-F02 は `accuracy_first_base` に F02(lgbm-064-f02acc)が常在するため構成上常に満たされる(F02 不在の NOT_RUN 経路は本スコープで発火しない=設計上の前提)。
- [ ] T028 [US3] **F05 residual paired-eval**(段4)— **F04 が ADOPT(T020)の時のみ**: 現 base+`pm_conditioned_residual` vs 現 base。verdict 記録(out/f05_residual.json)。F04 未採用時は NOT_RUN を artifact に明記。

**Checkpoint**: F05 2 群単体緑・additive parity・段3/(段4)verdict 記録済。

---

## Phase 6: Polish & Cross-Cutting(全 bundle 統合検証)

- [x] T029 **features-019 共有137列 byte-parity**: features-018 build vs features-019 build の共有137 model-input 列が check_exact + check_dtype 一致(新群のみ増分)。**+ 確率整合性(憲法 IV)**: features-019 の win 予測 p が 009 で Σ(1着率)=1・順位保存を保つ(069 同経路=モデル path 不変の回帰確認)(features/tests/integration/test_features_019_parity.py)。
- [x] T030 **serving compat**: features-019 registry 下で lgbm-063(017)/lgbm-064(018)が compat-load・予測 win prob byte 一致(16頭 mismatch 0)。**同一版で列 subset を drop した artifact は NOT_SERVABLE**(F03 置換・未採用 F04/F05 drop の最終 candidate=型付き拒否)を固定。**これは既存 loader 挙動の regression テスト**(model_loader.py:194 の `exact` は `model_hash==current_hash` を要し、同一版 subset は hash 不一致 + compat map は prior version 専用 → 現状で ServingError fail-closed=serving source 変更不要・loader.py は [READ])(serving/tests/integration/test_features_019_compat.py, serving/tests/unit/test_not_servable_subset.py)。
- [x] T031 **materialize 再生成 + schema 不変**: `features materialize`(features-019)を 1 回実行し、source_fingerprint が 069 と同一・in-memory と bit 一致・018 parquet は version mismatch で拒否を確認。**+ migration head 不変(新 `__tablename__` なし・alembic head 変わらず=055 前例)を assert**(FR-011)・**materialized 列数 = 112+新19 = 131(全新列が as-of materialize 対象)を exact assert**(SC-001 の数値検証・analyze I1/L1)(artifacts/features.parquet, features/tests/test_no_schema_change.py)。
- [ ] T032 **stack-safety-check**(段5=paired-eval 通し番号・F03=1/F04=2/F05support=3/F05residual=4/stack=5、F03 verdict 固定は bookkeeping で番号外): 採用群合成 vs lgbm-064-f02acc 系で subgroup 付き paired-eval。**artifact に 2 残リスクを明記**(同一 2019–2026 OOS=独立 confirmatory でない・TE-free↔TE delta)。真の確認は未使用 time holdout=production 昇格(スコープ外)前に確保。
- [ ] T033 **accuracy-first candidate 登録**: 採用 bundle 合成を `train-evaluate --register-candidate --target-encode jockey_id,trainer_id --drop-groups <未採用群> --artifacts-dir $(pwd)/artifacts --model-version lgbm-0XX-pmbundle`(絶対パス・default lgbm-063 不変・NOT_SERVABLE)。**TE 列集合 = `jockey_id,trainer_id`**(036 lineage・CLI 既定の venue_code は入れない=lgbm-064 と一致させ T032 stack 比較を confound しない・analyze I1・T002 で裏取り)。
- [ ] T034 [P] coverage 監査記録: `coverage-audit --from 2019-01-01 --to 2026-07-12`(069 実装のまま・年×ID source×obs 帯)(out/cov.json)。
- [x] T035 [P] ruff / 全パッケージ pytest(features/eval/training/serving)緑を確認。
- [x] T036 **codex 再レビュー**(高リスク=features/registry/eval/serving 変更): `codex exec --sandbox read-only` で実装差分をレビューし、指摘採否を research.md D7 に追記(codex 不可時は self-review checklist を実施し `codex unavailable` を明記)。
- [ ] T037 docs/plan の Phase 3 節・CLAUDE.md・memory を最終 verdict(各 bundle ADOPT/REJECT/NO_DECISION)で更新。**FR-008 の framing 是正=`docs/plan/model-feature-redesign.md`(§47 周辺)の `p⊥q` 表現を「対象レース市場非入力」に修正**(現 active lgbm-063 は 058 を含む=統計的 p⊥q でない)。

---

## Dependencies

- **Setup(T001-T002)** → 全 Phase の前提。
- **Foundational(T003-T006)** → 全 US の前提(共有 primitive + version bump + compat)。
- **US1(F03, T007-T013)**: T003(u は F03 が公開だが primitive 基盤は T003 と同経路)後。**T013 の F03 verdict が US2 の baseline を決める**。
- **US2(F04, T014-T020)**: **T007(u primitive)+ T003(q primitive)必須**。T020 の F04 verdict が US3 段4 の前提。
- **US3(F05, T021-T028)**: T003(q)+ T014(F04 residual)必須。**T028 は T020=ADOPT の時のみ**。
- **Polish(T029-T037)**: 全群登録後(T008/T015/T022)。T030 は T004(compat)+ T002(full hash)必須。

## Parallel Opportunities

- T005 は **T004 の後に非並列**(grep-driven の version-assert 更新が compat-pin キーを誤書換しないよう、compat pin 追加後に実行=M1 hazard 回避)。
- 各 US 内のテスト群 [P](T010/T011/T012・T017/T018/T019・T024/T025/T026)は実装(T007/T014/T021)後に並列。
- **bundle 間は逐次**(段階評価の帰属分離=F03 verdict→F04 baseline→F05 のため並列化しない)。
- T034 / T035 並列。

## Implementation Strategy

- **MVP = US1(F03)**: features-019 bump + F03 群 + 段1 paired-eval。058 置換の可否を最初に確定(F04/F05 の土台=u primitive を先に固める)。
- **Incremental**: US1 verdict 固定 → US2(F04・u/q 共有)→ US3(F05・2 群・F04 ADOPT 時のみ residual)。各段で verdict を artifact 化してから次へ。
- **Cross-cutting は最後**: 全群登録後に byte-parity / compat / NOT_SERVABLE / materialize 再生成 / candidate 登録 / codex 再レビュー。
- **不変条件**: スキーマ/API/OpenAPI/migration 不変・source_fingerprint 不変(新ソース列なし)・default lgbm-063 byte-parity・全 candidate は NOT_SERVABLE(評価は再fit)。
