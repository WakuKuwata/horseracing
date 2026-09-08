# Bundle contract

- `schema_version:1`, `artifact_kind:candidate_mixture_bundle`, `profile:125_new_joint_mixed6_v1`, `mode:shadow`, `can_adopt:false`, `eligible_for_verdict:false`.
- `bundle_id` is fixed before construction, and externalSHA256 ofbundle.json identifies exactcontents. `train_through:2026-08-23`, `coefficient_train_through:2025-12-31`, `created_at` UTC.
- `members` fixedorderedIDs `joint-42,joint-43,joint-44,anchor-42,anchor-43,anchor-44`; each has `id,branch,seed,weight,artifact_dir,files,correction`. branchpruning/anchor; weights1/6.
- Member artifactfiles `model.txt,calibrator.pkl,preprocessor.pkl,metadata.json`, SHA256 map. Metadata binds feature_cols/hash/category list, rawrepresentation, recipe/actualparams/OOF_info, trainpopulation/date, model/runtime/source, no DBrow required.
- `correction:{terms,coefficients,source_study,source_sha256,valid_year:2026,fit_through:2025-12-31}`. jointterms `gap_log,prior_gap_log,female_sin,female_cos,centered_logp`; anchorterms `gap_log`. Positiveeffectiveglobalexponent andfinitevaluesrequired.
- `feature_profile:{full_columns,pruning_drops,full_columns_hash,raw_representation,source_feature_version}`; customprofilevalidatesexact125/138lists ratherthanweakening globalregistry.
- `inference:{base_eps:1e-6,assembly_eps:0,head_aggregation:arithmetic_mean,postprocess:none,history_start:2007-01-01,same_day_excluded:true}` and `sources` pins research125/runfreeze/audit/config andbuilder dependencies.
- `mixture_correction.build_correction_inputs(target_rows,started_history)` returns row-alignedFrame withkeys andterms excludingcenteredlogp; nofit/DBsideeffects.
- `mixture_correction.correct_member_predictions(started_ids,base_p,inputs,terms,coefficients)` returns mappinghorse→Prediction; base_p alreadyclipped/calibrated/race-normalized. `average_member_predictions(list_of_six_maps)` enforcesidenticalkeyset/fullheads/order/finite/sums andaverages.
- `mixture_model.load_mixture_bundle(path)` validatescompletebundlebeforeinference. `predict_mixture(bundle,race_id,feature_rows,history,regime)` returns predictions plusinputs/audit. regimes preweight/serving/full_information_replay; lastisdiagnostic only. `python -m horseracing_serving.mixture_shadow` offersfile-basedparallelrecording, notlegacyDBmodelactivation.
