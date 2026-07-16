# Research: OOF-faithful Calibration Evidence

**Feature**: 074 | **Date**: 2026-07-16 | codex: `docs/plan/codex-074-review.md`

## D1. OOF bundle は foldfit 由来・persisted-run 非再利用

**Decision**: OOF prediction を `eval/foldfit.predict_over_folds(factory, eval_races)`([foldfit.py:41](../../eval/src/horseracing_eval/foldfit.py))で生成。factory は US2 の legacy attestation から構築した recipe-faithful `RecipeFactory`。得た `{race_id: Prediction}` を **content-addressed disk artifact**(`artifacts/oof/<digest>/`)に直列化。DB の PredictionRun は正本に使わない。`_latest_run_predictions` には base_model_version フィルタを足す(defense-in-depth のみ、[model_calibration.py:232](../../probability/src/horseracing_probability/model_calibration.py))。

**Rationale**: `predict_over_folds` は expanding fold ごと outer-train で fresh fit・保存 booster 不使用(codex C1)=構造的に OOF。persisted prediction は full-history 由来で非OOS(codex 最重要)。bundle を DB に入れると API/serving/model-selector を汚染([selection.py:21](../../api/src/horseracing_api/selection.py))。

**Alternatives**: (a) persisted run を base_mv+strict-past で絞る → full-history 由来は依然非OOS。(b) fold model を ModelVersion として保存 → model-selector 汚染。→ disk artifact。

## D2. lgbm-063 の legacy 完全 attestation

**Decision**: `training/legacy_attest.py`(新)で、073 freeze(`legacy-freeze-lgbm-063.json`)+ metadata.json([metadata.json](../../artifacts/model_versions/lgbm-063/metadata.json))から、resolved LightGBM params・objective/postprocess・ordered feature columns+feature_version・TE 列/smoothing・internal calibration method/fraction/split unit・seed/threads・drop list・source/materialized snapshot hash・code SHA を含む完全 attestation を content-addressed artifact 化。これから `ModelRecipe`(073 拡張済)+ resolved params を復元し RecipeFactory を構築。

**Rationale**: 現 `ModelRecipe`([recipe.py:31](../../training/src/horseracing_training/recipe.py))は完全再現 recipe でない(resolved params/ordered cols/HPO 不足、codex)。OOF を recipe-faithful にするには attestation が前提。ModelRecipe 自体は拡張せず別 artifact 化して recipe_hash 破壊を避ける。

## D3. serving parity 線引き(codex 表)

**Decision**: 出力面ごとの 074 後契約:

| 出力面 | 074 後 |
|---|---|
| `race_predictions.win_prob` / API win | **byte 不変**(SC-006、`model_internal_win_parity`) |
| persisted/API top2/top3 | 074 では**変更しない**(新 run で変わるのは 076) |
| win/exotic recommendation(two-gamma) | 074 では**変更しない**(結線は 076) |
| API `?bet_type=` joint | λ=1 再計算の現行契約維持 |
| 既存 PredictionRun/Recommendation | **不変**(SC-010) |

074 は evidence のみ生成。two-gamma は serving win に非適用(betting recommend 経路のみ、[betting cli.py:181](../../betting/src/horseracing_betting/cli.py))。

**Rationale**: 074 は production 非結線(FR-015)。校正の作り直しは artifact に留め、実際に serving/recommend が読むのは 076。

## D4. content-addressed manifest

**Decision**: `training/calib_manifest.py`(新)。073 legacy_freeze を起点にするが 3-file では不足。manifest は schema/version・artifact kind・base model version・model/calibrator/preprocessor/**metadata** checksum・完全 resolved recipe hash・feature_version/ordered-column hash/**source fingerprint**(列名 hash の値意味論弱点を補完)・fold ごと train/valid race set hash+train_through+生成 model digest・OOF race 集合+prediction checksum・確率 stage 順・**full 精度**の two-gamma/λ params+fit race hash+fallback・code SHA/seed/threads・最終出力 checksum を含む。create-only・atomic publish(temp→rename)・冪等(同 payload=同 digest)・fail-closed(同 key 異内容 conflict、改竄/partial/未知 schema/世代不一致=load 前拒否)。wall-clock/自己 digest は hash 対象外。identity fallback も明示 artifact。

**Rationale**: `logic_version` は γ/λ を小数5桁に丸め=byte 再現不可([model_calibration.py:213](../../probability/src/horseracing_probability/model_calibration.py))。serving は metadata.json にも依存([model_loader.py:174](../../serving/src/horseracing_serving/model_loader.py))。feature_hash は列名中心で値意味論に弱い([model_loader.py:180](../../serving/src/horseracing_serving/model_loader.py))→ source fingerprint 補完。

## D5. OOF 上の two-gamma/λ prequential fit + strictly-later ECE + 048 再検証

**Decision**: 既存 `fit_two_gamma`/`fit_p_calibrator`/`fit_product_stage_discount`([model_calibration.py:153/179/403](../../probability/src/horseracing_probability/model_calibration.py))の **sample source を OOF bundle に差し替え**。各 fold の校正は **prior OOF fold のみ**で fit(prequential)、fit に使った fold を評価 CI に含めない。ECE は **strictly-later OOF block** で測定(073 の帯別 ECE を適用)、calibrated-stage(two-gamma 後 win / stage discount 後 top2/top3)。048 採否を OOF で測り直し verdict = ADOPT/REJECT/NO_DECISION(点推定不可)。OOF→full-history 分布 transfer check、ミスマッチは NO_DECISION/fallback。成果は `evaluation_contract_version=v2` append-only evaluation artifact。073 FR-007 はこれを参照して fulfill(073 過去 verdict 不変)。

**Rationale**: 073 の三値 gate・帯別 ECE・bootstrap を再利用。048 の persisted-sample OOS provenance 未証明を OOF で解消(codex)。

**fold と block の対応(analyze U2)**: OOF fold = expanding walk-forward の valid year(`eval/splits.expanding_folds`)。各 fold の校正器は **その fold より前の valid-year fold(prior OOF)だけ**で fit し、ECE を測る「strictly-later OOF block」= **その校正器の fit に使った最後の fold より後の valid-year fold 群**。したがって block 粒度 = valid-year fold で、fit fold は評価 CI から除外される(`gate-config.prequential.exclude_fit_fold_from_ci`)。fold≠別分割。

**pivot 固定(analyze U3)**: two-gamma の `pivot=0.15` は 048 で事前登録された固定値であり、074 の OOF 再 fit でも**固定維持**(再 fit しない)。γ_lo/γ_hi のみ OOF で fit。evaluation artifact の fit 記録に pivot を **full 精度で保持**(data-model §3)。

**bundle 範囲 vs eval 窓(analyze I2)**: OOF bundle は `--first-valid-year 2008` で 2008–2026 を張る(strict-past OOF を全期間でカバー・SC-001)。一方 calibration eval の CI は `eval_window.from=2019`(060 前例=expanding-window の初期 fold は学習量僅少でアーティファクト化するため、採否 CI は 2019+ に限定)。bundle=全期間 / eval CI=2019+ は意図的な切り分けで、bundle の strict-past 性(SC-001)と eval 窓は別スコープ。

## D8. 決定論 vs recipe-faithful の num_threads(analyze I1)

**Decision**: OOF 生成は **`num_threads=1` を既定**にして byte 決定論(FR-006/SC-005)を優先する。attestation は lgbm-063 の実 `num_threads`(metadata.json)を「決定論条件」として記録するが、OOF 生成が 1 と異なる場合は **明示 fallback** として evaluation artifact/manifest に「recipe-faithful in all params except thread count(LightGBM は thread 数で結果が変わりうるため決定論を優先)」と差分注記する。

**Rationale**: LightGBM は multi-thread で histogram 集約順序が変わり非決定的になりうる。byte 決定論(監査・再現)を recipe-faithful の thread 数一致より優先する方が、evidence の再現性契約に整合。thread 数は確率分布に軽微影響しうるが、OOF の目的(リーク是正=strict-past provenance)には影響しない。差分は隠さず manifest に明記。

**Alternatives**: attested num_threads を使い決定論を諦める → SC-005 と両立不能。→ 決定論優先 + 差分明記。

## D6. 同日リークを race_date に統一

**Decision**: OOF fit の同日除外を `race_date < target_date` に統一。single-race 経路が race_id 順で同日 earlier を使う現状([betting cli.py:396](../../betting/src/horseracing_betting/cli.py))を OOF 生成側では採らない。

**Rationale**: race_id 順は結果利用可能時刻を証明できない(timestamp 不在、codex)。

## D7. 066 / joint calibration の leaky loader は診断のみ

**Decision**: 066 dispersion 用 two-gamma([training cli.py:1055](../../training/src/horseracing_training/cli.py))と joint calibration([calibration.py:82](../../probability/src/horseracing_probability/calibration.py))も同型 leak を持つが、074 は **evidence のみ**=これらの是正結線はしない。research artifact に「同型 leak 存在」を診断併記し、是正は 076 に送る。

**Rationale**: 074 のスコープ肥大を防ぐ(1 feature 1 関心)。是正は production 結線を伴うため 076。

## D9. features-017/018 gap の解決 — OOF fit を lgbm-063 の正確な列に制限

**問題(実装で判明)**: `build_training_matrix` は `feature_cols = model_input_features()`(現行 **features-018** の全列)を常に返す([dataset.py:118](../../training/src/horseracing_training/dataset.py))。lgbm-063 は **features-017**(F02 なし)で学習されたが、attestation から復元した recipe は `drop_features` を持たないため、OOF fit が **F02 列込みの features-018** で走る=lgbm-063 と非同一の特徴集合。これは recipe-faithful 性を崩す。

**なぜ byte-faithful に解ける**: 069 が **features-018 = features-017 + additive F02(left-merge・共有列 byte 一致を check_exact で実証)**を確立済み。したがって features-018 build を **features-017 の列だけに制限**すれば、features-017 build と **byte 一致**の特徴値が得られる(058/069 の serving COMPATIBLE_PRIOR パリティと同じ保証を OOF fit に適用)。近似ではなく厳密。

**Decision**: OOF fit を **attestation の `ordered_feature_columns`(lgbm-063 の正確な順序付き列)に制限**する。

- **機構**: `LightGBMPredictor` に `restrict_features: tuple[str,...] | None = None`(inclusion)を追加。`_ensure_data` で full matrix 構築後、`restrict_features` 指定時:
  - `missing = set(restrict_features) − set(full.feature_cols)` → 空でなければ fail-closed(`ValueError`=特徴が現行から消えた=再現不能)。
  - `keep = list(restrict_features)`(**lgbm-063 の正確な順序**をそのまま採用=order 保存)。`feature_cols=keep`・`categorical_cols` も keep で filter。
- **`drop_features`(除外)で代替可だが不採用**: `drop = model_input_features() − ordered_cols` を静的計算して drop に足せば予測器改変ゼロで済む(F02 が末尾追加なら順序も保たれる)が、**列が再順序化された場合に drop は順序を復元できない**。`restrict_features`(inclusion)は subset+reorder の両方を厳密に扱えるため堅い(LightGBM は単一 thread でも列順で tie-break が変わりうる=faithful には順序一致が安全)。
- **配線**: `legacy_attest.factory_from_attestation(session, att)`(新)= recipe 復元 + `RecipeFactory(session, recipe, restrict_features=tuple(att["ordered_feature_columns"]))`。`oof_generate.generate_oof_bundle` はこれを使う(現行の raw recipe→factory を置換)。
- **TE 整合**: 予測器は既に `te_cols_ = [c for c in target_encode_cols if c in data.feature_cols]` で filter 済み。lgbm-063 の TE(jockey_id/trainer_id)は features-017 ⊆ restrict なので不変。

**Rationale**: 069 のパリティを再利用するため近似でなく byte-faithful。予測器への追加は opt-in(`restrict_features=None` で既存挙動 byte 不変)= ModelRecipe の hash を汚さない(recipe field にせず factory 経由)。fail-closed で「lgbm-063 の列が現行に無い=再現不能」を検出。

**Alternatives**: (a) features-017 コードを checkout して matrix 再構築 → 重い・不要(additive superset ゆえ制限で足りる)。(b) 「superset ゆえ許容」だけの明文化 → 制限しなければ fit は余分な F02 を使う=faithful でない、単独では誤り。→ `restrict_features` による厳密制限を採用。

**実装スコープ**: `predictor.py`(restrict_features 追加・_ensure_data filter)+ `recipe.py`(RecipeFactory に restrict_features field)+ `legacy_attest.py`(factory_from_attestation)+ `oof_generate.py`(差し替え)+ テスト(restrict で features-017 列のみ・missing で fail-closed・restrict=None で byte 不変)。**features/training path + パリティ変更のため実装前に codex レビュー MUST**(憲法品質ゲート)。

## 未解決

なし。計算コスト(fold 再学習=長時間)は tasks で smoke→フルに段階化(実装可否は smoke で判定)。features-017 gap は D9 で解決設計済み(実装は codex レビュー後)。
