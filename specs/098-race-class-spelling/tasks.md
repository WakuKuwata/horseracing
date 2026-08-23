# Tasks: race_class の表記統一と再学習つき採否判定(098)

**Input**: `specs/098-race-class-spelling/`(spec.md / plan.md / research.md D1-D12 / data-model.md /
contracts/representation.md INV-R1..R7 / contracts/adoption-gate.md INV-A1..A5 / gate-config.json(draft)/ quickstart.md)

**Tests**: spec の SC と contracts の INV は「機構で担保」が前提なので、テストは必須として列挙する
(憲法「品質ゲート」: leakage / 時系列 / 評価ハーネス / 監査のテストを含む)。

**Organization**: Phase 3 = US2(表現と版バインディング。US1 が使う変換なので**先**)→ Phase 4 = US1(判定)
→ Phase 5 = US3(調査)→ Phase 6 = verdict 分岐の後始末 → Phase 7 = Polish。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 並列可(別ファイル・未完了タスクへの依存なし)
- **[Story]**: US1 / US2 / US3
- 実行は repo root。DB は `DATABASE_URL=postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing`

## Path Conventions

- features: `features/src/horseracing_features/`・`features/tests/unit/`
- training: `training/src/horseracing_training/`・`training/tests/unit/`
- serving: `serving/src/horseracing_serving/`・`serving/tests/unit/`
- driver/証跡: `scripts/`・`specs/098-race-class-spelling/`・`out/`(非追跡)

---

## Phase 1: Setup

- [X] T001 実 DB で前提を再確認して `specs/098-race-class-spelling/evidence-preflight.md` に記録: active=lgbm-094-cap900・`metadata.feature_version=features-021`・`metadata.feature_hash=663fe86c7564…`(完全値を貼る)・registry `FEATURE_VERSION="features-021"`・**features-022 が焼却番号である根拠**(commit 808f92c で使用 → main の 8122f64 で revert。8b0c7c7 は branch に載っていない orphan なので参照しない)・`race_class` の綴り別件数(`１勝/２勝/３勝` は 2025-10-11 以降のみ、`1勝/2勝/3勝` は 2025-10-05 以前のみ=quickstart §0)・active の `pandas_categorical` に両綴りが共存すること(model.txt から読む)
- [X] T002 **bump 前の基準を捕獲**: features-021 registry のまま `build_training_matrix` を 1 回走らせ、`race_id, horse_id` + 全 model_input 列を `out/098-baseline-features021.parquet` に保存(INV-R6 の比較対象。bump 後は旧 registry が存在しないので**今しか取れない**=091 INV-W7 と同型)。行数・列数・`race_class` の値分布(綴り別)を `evidence-preflight.md` に追記

---

## Phase 2: Foundational(全 US の前提)

- [X] T003 `features/src/horseracing_features/race_class_canon.py` を新規: `CANONICAL_TABLE = {"１勝": "1勝", "２勝": "2勝", "３勝": "3勝"}`(凍結・表外は不変)、`REPRESENTATIONS = ("raw", "canonical-v1")`、`canonicalise(series: pd.Series) -> tuple[pd.Series, dict]`(写像・`audit={"mapped": {入力: 件数}, "out_of_table": {値: 件数}}`・NULL/既正準値は不変・object dtype を保つ)、`pseudo_split(series, race_dates, cutoff) -> pd.Series`(逆写像 `{1勝→１勝, 2勝→２勝, 3勝→３勝}` を `race_dates >= cutoff` の行にだけ適用・**シミュレーション専用**と docstring に明記)、`SPLIT_TOKENS = frozenset(CANONICAL_TABLE)`。結果・オッズ・他列を一切読まない純関数(憲法 II)
- [X] T004 [P] `features/tests/unit/test_race_class_canon.py`: (a) 手計算 fixture で 3 対応の写像 (b) `オープン`/`重賞`/`新馬`/`Ｇ１`/NULL/既正準 `1勝` が不変 (c) 冪等 `canonicalise(canonicalise(x)) == canonicalise(x)` (d) audit の件数が入力と一致 (e) `pseudo_split` がカットオフ以降の 3 トークン行だけを逆写像し、カットオフ前・表外・NULL は不変 (f) `pseudo_split` → `canonicalise` で元に戻る(往復) (g) dtype が object のまま・行順不変
- [X] T005 `training/src/horseracing_training/spelling_split.py` を新規(driver とテストが import する。`scripts/` はパッケージでない=097 T019 と同型): `make_arms(matrix: TrainingMatrix, *, mode: Literal["pseudo_split","canonicalise"], cutoff: date | None) -> tuple[TrainingMatrix, TrainingMatrix]`(A, B を返す。`mode="pseudo_split"`: B=原本・A=`frame.copy()` に `pseudo_split(cutoff)`; `mode="canonicalise"`: A=原本・B=copy に `canonicalise`)、`assert_arm_identity(a, b, *, allowed_rows_mask) -> dict`(契約=contracts/adoption-gate.md **INV-A1/A2/A4/A5**: `race_class` 以外の全列 `assert_frame_equal(check_exact=True, check_dtype=True)`[A1]・`race_class` の差異行 ⊆ `allowed_rows_mask` かつ差異行数>0[A2]・両アームの `race_class` 列 hash(`frame_projection_hash` を `(race_id, horse_id, race_class)` 射影で流用)を返す[A4]・`a.frame is not b.frame`・`feature_cols`/`categorical_cols` が同一[A5]。INV-A3(paired diff 全 0 で abort)は driver 側)
- [X] T006 [P] `training/tests/unit/test_spelling_split.py`: **アーム同一性 assert の kill-test** — (a) 正常系: 合成 TrainingMatrix で `make_arms` → `assert_arm_identity` が差異行数と hash を返す (b) 差が無い(両アーム同一)→ fail (c) 余計な差(他列を 1 値変える)→ fail (d) 許容外の行(カットオフ前の行を変える)→ fail (e) hash は行順入替で不変・1 値変更で変化

---

## Phase 3: User Story 2 — 表現と版バインディング (P2)(US1 が使う変換なので先行)

**Goal**: `race_class` の正準表現を features-023 に束ね、旧 active(021)が compat 経路で生表現のまま
バイト一致で serve でき、新版 artifact が語彙 hash と marker で適合判定されることを実 DB で証明する。

**Independent Test**: T010(parity)+T016(serving E2E バイト一致)+T009/T013/T015(単体・golden・注入)が緑なら US2 単独で完了。

### Implementation for User Story 2

- [X] T007 [US2] `features/src/horseracing_features/registry.py`: `FEATURE_VERSION = "features-023"`(コメント: 022 は 097 の焼却番号=808f92c→8122f64)、`RACE_CLASS_REPRESENTATION = "canonical-v1"`(コメント: 「この版で**学習するときに渡す**値。読み込む側が推測する値ではない」=codex plan Q1/Q5)、`COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-023"] = {"features-021": <T001 の完全 hash>}`(コメント: compat 経路は生表現を渡すので共有列バイト同一が成立=INV-R2/R6。017 の空 compat と違う理由を書く)
- [X] T008 [US2] `training/src/horseracing_training/dataset.py`: `build_training_matrix(session, *, representation: str, ...)` に**必須引数**(既定値なし)を追加。`representation not in REPRESENTATIONS` は `ValueError`。ビルド後・`astype("category")` の**前**に `canonical-v1` なら `canonicalise(df["race_class"])` を適用し audit を `TrainingMatrix.build_audit["race_class"]` に保持(`TrainingMatrix` に `build_audit: dict = field(default_factory=dict)` を純増)。**INV-R7**: `astype("category")` 前後で `race_class` の NaN 数が増えたら `RuntimeError`(fail-closed)。`predictor.LightGBMPredictor.__init__` に `race_class_representation: str | None = None` を追加し `_ensure_data` で `None → registry.RACE_CLASS_REPRESENTATION` を解決して渡す(**INV-R2 の「唯一の解決点」**: 既存 constructor 呼出 40 箇所を壊さないための例外。解決値が `REPRESENTATIONS` に含まれることを assert。training CLI の `train-evaluate`/`model-eval`/`register-arm-e` は registry 定数を**明示**して渡す。driver/parity は None を渡さない — docstring に明記)。`fit_info_["race_class_representation"]` に解決値を記録
- [X] T009 [P] [US2] `features/tests/unit/test_registry_features023.py`(`test_registry_features021.py` を雛形に): `FEATURE_VERSION == "features-023"`・`RACE_CLASS_REPRESENTATION == "canonical-v1"`・`COMPATIBLE_PRIOR_FEATURE_VERSIONS["features-023"]["features-021"] == <T001 hash>`・`"features-022" not in COMPATIBLE_PRIOR_FEATURE_VERSIONS`・`model_input_features()` の列集合と列順が 021 と同一(`feature_hash` 不変)・`is_feature_version_servable("features-021", <hash>)` が True・hash 違いは False
- [X] T010 [US2] `scripts/parity_098.py`: 単一スナップショットで `build_training_matrix(representation="raw")` を T002 の `out/098-baseline-features021.parquet` と**全列** `assert_frame_equal(check_exact=True, check_dtype=True)`(INV-R6・SC-003)。続けて `representation="canonical-v1"` を取り、`race_class` 以外が全列一致・`race_class` の差異行が `値∈{１勝,２勝,３勝}` の行に限ること(SC-002)・差異行数・audit を `specs/098-race-class-spelling/evidence-parity.md` に記録。**中断点**: mismatch>0 なら即中断(変換が他列に触れた=設計違反)
- [X] T011 [US2] `training/src/horseracing_training/artifacts.py`: `categorical_vocab_from_booster(booster) -> dict[col, list[str]]`(model.txt の `pandas_categorical` を `categorical_cols` の順で対応づけ・順序保持)と `vocab_hash(vocab) -> str`(`json.dumps(sort_keys=False, ensure_ascii=False)` の sha256)を新規。`save_model_version` で metadata に `race_class_representation`(predictor の `fit_info_` から)・`categorical_vocab`・`categorical_vocab_hash` を純増、`metrics_summary["training"]["race_class_representation"]` にも転記(050 同型)。`build_preprocessor` の dict にも `race_class_representation` を入れる
- [X] T012 [US2] `serving/src/horseracing_serving/model_loader.py`: `ServingModel` に `race_class_representation: str`・`categorical_vocab: dict` を純増。`load_serving_model` で (a) **allowlist**(codex plan Q1): `(trained_fv, current_fv) -> representation` = `{("features-023","features-023"): metadata["race_class_representation"] が "canonical-v1" であること, ("features-021","features-023"): "raw"}`。それ以外・marker 欠落(023)・未知値は `ServingError`(fail-closed)。compat 経路では metadata の marker に関わらず **`"raw"` を強制** (b) booster から `categorical_vocab_from_booster` で再導出し metadata の `categorical_vocab_hash` と一致しなければ `ServingError`(INV-R3)。metadata に hash が無い旧 artifact(021)は「raw かつ compat」の場合に限り許容 (c) `canonical-v1` のとき `categorical_vocab["race_class"]` に `SPLIT_TOKENS` が含まれていれば `ServingError`(正準を名乗る分裂語彙)
- [X] T013 [P] [US2] `serving/tests/unit/test_model_loader_representation.py`: **golden fixture 3 種**(codex plan Q1) — (i) raw-021(marker 無し・hash 無し・compat pin 一致)→ representation=="raw" (ii) canonical-023(marker あり・語彙 hash 一致・分裂トークン無し)→ "canonical-v1" (iii) 拒否組合せ: 023 で marker 欠落 / 023 で marker="raw" / 語彙 hash 不一致 / canonical を名乗るが語彙に `１勝` あり / 未宣言ペア(例 "features-020"→023)→ すべて `ServingError`。fixture は `test_lgbm063_compat.py` の artifact 生成ヘルパを流用
- [X] T014 [US2] `serving/src/horseracing_serving/predictor.py` `predict_race`: 091 の体重正規化の直後・`astype("category")` の**前**に (a) `model.race_class_representation == "canonical-v1"` なら `canonicalise(rows["race_class"])` を適用(純関数・明示引数) (b) **INV-R4 監査**: `rows["race_class"]` のうち `model.categorical_vocab["race_class"]` に無い値の mask を取り `{"n_unknown", "unknown_values", "representation", "feature_version"}` を **第 4 戻り値 `audit`** として返す(`predict_race` を `(predictions, snapshots, explanations, audit)` の 4-tuple に。089 の 3-tuple 化と同型=`pipeline.py` の 2 呼出[L306/L314]と serving テストの unpack 18 箇所を更新し、`_predict_persist` は `audit` を run summary に載せる)。既知集合に対する未知率が 1% 超なら warning ログ (c) **INV-R7**: `astype("category")` 前後で NaN 数が増えたら `ServingError`。`run_serving` の logic_version に `;rcr=<representation>` を付与(076 の `;calib=` 同型・冪等キーにも参加)
- [X] T015 [P] [US2] `serving/tests/unit/test_predictor_representation.py`: (a) canonical-023 モデルで `１勝` 行が `1勝` として予測される(booster の入力列値を spy で確認) (b) raw-021 モデルでは無変換 (c) **注入テスト**(codex plan Q4): 語彙外の値 `４勝` を含む行でも予測が返り `audit.n_unknown==1`・`unknown_values==["４勝"]` (d) 既知トークン取りこぼし率 >1% で warning (e) INV-R7: 変換で NaN が増える細工(語彙外を NaN にする偽の表現)→ `ServingError` (f) logic_version に `;rcr=` マーカー
- [X] T016 [US2] 実 DB E2E(SC-004): features-023 registry 下で `serving predict --race-id <確定済み 2026 レース>` を active lgbm-094(021)で実行し、(a) compat 経路(`reg=features-021` マーカー+`rcr=raw`)でロード (b) win_prob が bump 前の永続化値と**全頭バイト一致** (c) 監査 dict の `n_unknown==0`。結果を `evidence-preflight.md` に追記
- [X] T017 [US2] `features materialize` を features-023 で実行し manifest の `source_fingerprint` が 021 の manifest と一致(静的列は非 materialize・新規ソース列ゼロ)・`feature_version=features-023` を確認。materialize の出力に `race_class` の表現 audit は**出ない**(静的列)こと=contract INV-R5(「training ビルド summary に記録・parquet 側には出ない」)および quickstart §2 の記述どおりであることを確認し、結果を `evidence-preflight.md` に追記

**中断点**: T010 の parity または T016 のバイト一致が破れたら即中断(以降の判定は無意味)。

---

## Phase 4: User Story 1 — 綴り統一の価値を再学習つきで測る (P1) 🎯 MVP(判定)

**Goal**: 凍結 gate-config の下で擬似分裂シミュレーション(primary)+実窓ガード+transportability を
1 回実行し、単一式で verdict を出す。

**Independent Test**: `verdict.json` が `artifact_kind=counterfactual_spelling_simulation`・
`eligible_for_verdict=False`・`feature_adoption_eligible=True`・gate hash・両アーム recipe hash・
各カットオフの `race_class` 列 hash と差異行数・`verdict.status ∈ {ADOPT, REJECT, NO_DECISION}` を持ち、
quickstart §5 の形と一致。

### Implementation for User Story 1

- [X] T018 [US1] **gate-config を凍結**: `specs/098-race-class-spelling/gate-config.json` の `_pre_registered_at` を凍結日に更新し、`representation.compat_pin`(既に完全 hash `663fe86c…0f7a`)が T001 で読んだ `metadata.feature_hash` と一致することを照合、`eval_window` と `scored_windows` の整合を確認(2020/2022/2024 ⊂ 2020-01-01..2024-12-31)、`smoke` 節の容量(rounds 50 / n_oof_blocks 2 / b 200)が T023 と一致、`horseracing_eval.decision.gate_config_hash`(canonical・`_` キー除外=`assert_confirmatory` が照合する値。ファイルの sha256 ではない)を `gate-config.hash.txt` に記録してコミット。**以降 gate-config.json を変更しない**(変更が必要なら再事前登録として記録)
- [X] T019 [US1] `scripts/098_spelling_split_gate.py` を新規(`097_simulated_supply_gate.py` を雛形に): `assert_confirmatory(cfg, expected_hash, eval_window)` を冒頭で通す。**シミュレーション用 matrix は 1 回だけ構築**(`LightGBMPredictor(session, objective=..., calibration="none", race_class_representation="canonical-v1")._ensure_data()`・`race_class_representation is not None`・`use_materialized is False` を assert。canonical-v1 が JRA-VAN 期の行を一切変えない=差異は `１勝/２勝/３勝` の行に限ることは T010 で証明済みなので、この matrix を B の原本とする)。各カットオフ: `make_arms(matrix, mode="pseudo_split", cutoff)` → `assert_arm_identity(A, B, allowed=race_date>=cutoff ∧ race_class∈{1勝,2勝,3勝})` → 両アームの `CalibSplitFactory._shared` に注入(`cand._shared is B`・`act._shared is A`・`A.frame is not B.frame`)・`drop_features=()` 両方・`feature_cols` 同一集合を assert → `load_eval_races(session, end_date=window_to)`・`paired_eval(candidate=B_factory, active=A_factory, eval_races, first_valid_year=window_from.year, valid_from=window_from, subgroups=False, num_threads=1, snapshot={driver, cutoff, window, race_class_hash_A/B, n_rows_differing})` → `diffs_by_day` 回収。**diff が全レースで 0 なら abort**(INV-A3・097 run 1)。採点窓・smoke 容量は gate-config から読む(ハードコード禁止・097 driver の `rounds, blocks, b = 50, 2, 200` は gate-config `smoke` 節に移す)。**出力規律**: 全成分が揃うまで効果の数値を stdout/JSON に出さない(進捗は「cutoff X: arms OK / identity OK / fit OK」のみ)
- [X] T020 [US1] 同 driver: pooled — 3 窓の `diffs_by_day` を union(互いに素を assert)→ `race_day_cluster_bootstrap_ci_v1` → `inflate_for_seed_noise(sd_fold=0.001816, n_folds=3)` → `recent_window_guard(max_date=2024-12-31)` → `evaluate_core_gate`(top2/top3/ECE は n_races 加重平均=gate-config `pooling`)。sufficiency: pooled n_days ≥ gate-config `eval_window.min_eval_days`(300)でなければ NO_DECISION(実行不能扱い・FR-007 優先順位 (1))。**新しい判定式を書かない**
- [X] T021 [US1] 同 driver: 実窓ガード `guard_real_direction` — 実データ(2025-10-11..DB 最新日)で `make_arms(matrix_raw, mode="canonicalise")`(ここだけ **raw 表現の matrix**を使う: `race_class_representation="raw"` で別途 1 回構築[INV-A5・D10 の 2 本目のビルド]し A=生/B=canonicalise コピー)→ `assert_arm_identity(allowed=race_class∈{１勝,２勝,３勝})` → `paired_eval(first_valid_year=2025, valid_from=2025-10-11)` → 標本 CI で `three_way(margin=0.005)`・`ci_low > 0.005` なら FAIL。`window_to` と `race_set_hash` を記録。**層別診断(報告のみ)**: 同じ paired diffs を「前走にリステッド/オープン歴あり vs なし」「レース内 nk: 馬割合 0 / <50% / ≥50%」で層別し点推定と n を verdict.json の `diagnostics` に出す(verdict には入れない=gate-config `diagnostics`)
- [X] T022 [US1] 同 driver: transportability(FR-007a・research D6)— (a) 各カットオフの点推定の符号が pooled と一致 (b) leave-one-cutoff-out pooled(3 通り・再 bootstrap 不要・点推定のみ)の符号が一致 (c) pooled<0 のとき実窓の標本 `ci_low ≤ 0`。**三値の優先順位(FR-007・凍結)**: 充足未達/実行不能 → NO_DECISION; `primary_pooled AND guard_real_direction` 不成立 → REJECT(transportability は `transportability` キーに記録するだけで verdict を変えない); 両方成立かつ transportable 不成立 → `verdict.status="NO_DECISION"`・`decision_reason.transportability=...`; 全成立 → ADOPT。verdict JSON: `evaluation_contract_version="v4"`・`artifact_kind="counterfactual_spelling_simulation"`・`eligible_for_verdict=False`・`feature_adoption_eligible=True`・`evidence_regime="simulated_spelling_split"`(=gate-config `primary_regime`)・`model_training_regime="real_canonical"`・gate hash・両アーム recipe hash・`representation`(023 / canonical-v1 / table)・各カットオフ `{window, n_races, n_days, point, race_class_hash_A, race_class_hash_B, n_rows_differing}`・pooled(point/標本 CI/総 CI/gate/n_days/sufficient)・`guard_real_direction`(window/race_set_hash/point/ci/three_way)・transportability・diagnostics・**`verdict` は RegimeReport 準拠オブジェクト** `{"status","adopt","formula":"primary_pooled AND guard_real_direction AND transportable","decision_reason"}`・「反実仮想頑健性として報告」の注記
- [X] T023 [US1] smoke(配線のみ): gate-config `smoke` 節(事前登録外カットオフ 2016-01-01・採点 2017・rounds 50 / n_oof_blocks 2 / b 200 を節から読む)で driver を通し、アーム同一性 assert(INV-A1..A5)・JSON 形・`promote-model --model-version lgbm-094-cap900 --verdict out/098-smoke.json`(`--apply` 無し=dry-run。`--dry-run` フラグは存在しない・`--model-version` は必須)が `verdict_artifact_not_eligible` で拒否することを確認。効果の数値は redact・`artifact_kind="smoke"`・`out/` に出す(非追跡)
- [X] T024 [US1] 本実行: quickstart §5 のコマンドで nohup 実行(推定 ≈2h・実績比。ビルド 2 回[canonical/raw]+fit 8 本)。完了後 `verdict.json` をコミット。**個別カットオフの数値を見て解釈を変えない**(pooled が正本)
- [X] T025 [US1] 結果を `spec.md` 末尾の「実測結果」節に転記(pooled・guard・transportability・diagnostics・verdict・D11 の限界の再掲)

**verdict 分岐**(中断ではない): 実行は全成分を完走させ、verdict は最後に 1 回だけ評価する(出力規律)。

---

## Phase 5: User Story 3 — リステッド競走の復元可否 (P3)

**Goal**: 切替後 `オープン` のうちリステッド相当を読み取りのみで分類し、`オープン` 変換の別 feature の入力を残す。

**Independent Test**: `evidence-listed.md` に件数(リステッド相当/非/曖昧)・規則・必要リクエスト数(=0)が数字で載る。

- [X] T026 [P] [US3] `specs/098-race-class-spelling/evidence-listed.md`: research D9 の賞金規則(面別の賞金集合)を DB で再実行して固定(JRA-VAN 期 2023〜2025-10 の `OP(L)`/`ｵｰﾌﾟﾝ` 賞金集合が互いに素であること・切替後 `オープン` 164 のうち障害 37 を除く平地 127 の内訳 49/62/16=2026-08-23 再実測値[D9]。再実行で数字が動いたら理由(取込・再ラベル)ごと記録し和が平地件数と一致することを確認・曖昧 16 の race_id 一覧・`race_name` に `(L)` 無し 0/164)。結論: 本 feature では `オープン` を変換しない(FR-002)、`オープン→ｵｰﾌﾟﾝ/OP(L)` は賞金規則を事前登録する別 feature(新規取得 0)

---

## Phase 6: verdict に従った後始末(US2 の分岐・FR-008/009)

### REJECT / NO_DECISION 分岐

- [X] T027 [US2] 結線を revert: registry(`FEATURE_VERSION` を features-021 に戻す・`RACE_CLASS_REPRESENTATION` と compat pin 023 を削除)、`dataset.build_training_matrix` の表現適用(引数は残し `"raw"` 以外を `NotImplementedError` にするか、呼出元から引数ごと外すかは**引数を残す**=非結線保全の一部)、`model_loader` の allowlist を 021 のみに、`predictor` の表現適用を無効化。`race_class_canon.py`・`spelling_split.py`・単体テスト・driver・golden fixture は**非結線保全**(062/070/090/097 同型・テストは直接呼び出しで緑)
- [X] T028 [US2] revert 後に T016 と同じ E2E で active 予測バイト一致を再確認し、`features materialize` で `feature_version=features-021` に戻ることを確認。結果(replay +0.029・verdict 数値)を spec と memory に記録

### ADOPT 分岐

- [ ] T029 [US2] (非該当: verdict=REJECT) features-023 を確定(registry そのまま)。正準データで候補を学習・登録: `register-arm-e --model-version lgbm-098-canon --n-estimators 900 --weight-mask-rate 0.5 --weight-mask-seed 20260810 --n-oof-blocks 8 --seed 42 --artifacts-dir <絶対パス>`(gate-config `arms.recipe` と一致・`--weight-mask-*` 省略禁止=097 T032 の教訓・`--artifacts-dir` は絶対パス)。metadata に `race_class_representation="canonical-v1"`・`categorical_vocab["race_class"]` に分裂トークン無しを確認。`promote-model --model-version lgbm-098-canon --verdict verdict.json`(`--apply` 無し=dry-run)が `verdict_artifact_not_eligible` で拒否されることを `evidence-preflight.md` に貼る(構造的拒否の証明)。**昇格しない**
- [ ] T030 [US2] (非該当: verdict=REJECT) 候補モデルで標準窓(2019-2024)の対 active 非劣化を確認評価(別 gate-config・新規事前登録)。prospective 監視の開始日と判定日を `prospective.md` に凍結。昇格は 3 点セット(標準窓非劣化 + 本 verdict + prospective)で別途。旧 active は compat(raw)で serve 継続=暗転なし

---

## Phase 7: Polish & Cross-Cutting

- [ ] T031 [P] memory: `feature-health-audit-2026-08-22` に 098 の結論を追記、新規 `feature-098-race-class-spelling-result`(verdict・機構=表現の版バインディング・codex 採否・教訓)を作成し `MEMORY.md` に 1 行
- [ ] T032 [P] CLAUDE.md の current-plan 要約を実測結果で更新(SPECKIT 区間は Edit で手動・agent-context hook は使わない)
- [ ] T033 全パッケージのテストと lint: `features`・`training`・`serving`・`eval` の pytest 緑、`ruff check` クリーン(scripts 含む)。serving の既存 compat テスト(`test_lgbm063_compat.py`)が allowlist 変更で赤くならないことを確認(旧版の compat は `raw` 強制で従来どおり)
- [ ] T034 コミット(spec 一式 + 実装 + evidence)。REJECT 分岐なら revert 後の状態で 1 コミット、ADOPT 分岐なら bump を含む 1 コミット。コミットメッセージに verdict と主要数値を書く

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1(T001-T002)→ Phase 2(T003-T006)→ Phase 3 US2(T007-T017)→ Phase 4 US1(T018-T025)
  → Phase 5 US3(T026・独立・いつでも可)→ Phase 6(verdict 分岐)→ Phase 7
- **T002 は T007 より前が必須**(bump 後は 021 の基準が取れない)
- **T018(凍結)は T019(driver)より前が必須**(凍結→driver の順序・憲法 III)
- **T023(smoke)は T024(本番)より前が必須**

### Within US2

- T007 → T008 → T010(parity)/ T009 は T007 後に並列 / T011 → T012 → T013 / T014 → T015 / T016 は T012・T014 後 / T017 は T007 後

### Within US1

- T018 → T019 → T020 → T021 → T022 → T023 → T024 → T025(直列。driver は 1 ファイル)

### Parallel Opportunities

- Phase 2: T004 ∥ T006(別ファイル)
- US2: T009 ∥ T013 ∥ T015(テストファイルは別)/ T026(US3)は Phase 3 と並列可
- Phase 7: T031 ∥ T032

---

## Implementation Strategy

1. **MVP = US2 の土台(Phase 1-3)まで**: これだけで「旧 active がバイト一致で serve され、新版が語彙 hash で適合判定される」が実証される。判定(US1)は独立した 1 回の実行
2. **US1 は凍結→smoke→本番の順序を崩さない**。途中結果で設計を変えない(事前登録)
3. **REJECT でも成果は残る**: 表現バインディング機構(allowlist・語彙 hash・INV-R4/R7)は非結線保全し、
   次の値変更 bump の土台になる(017 型の暗転窓を以後避けられる)
4. 推定作業量: Phase 1-3 ≈ 1 日(実装+テスト+E2E)、Phase 4 ≈ 半日+実行 2h、Phase 5-7 ≈ 半日
