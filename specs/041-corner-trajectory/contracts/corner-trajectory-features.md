# Contract: corner_trajectory features (041)

## モジュール

```
features/src/horseracing_features/corner_trajectory_features.py

CORNER_TRAJECTORY_COLUMNS = [
    "asof_late_gain_avg", "asof_late_gain_best",
    "asof_early_pos_avg", "asof_mid_move_avg",
]

build_corner_trajectory_features(frames: Frames) -> pd.DataFrame
    # returns per (race_id, horse_id) rows: [race_id, horse_id, *CORNER_TRAJECTORY_COLUMNS]
    # 全行 = frames.race_horses の (race_id, horse_id)(started 以外含む、他モジュールと同じ母集団規約)
```

## 算出契約

1. **runs**(過去走プール): race_results(result_status=finished かつ finish_order 非 null)に race_horses(entry_status=STARTED)・races(race_date)を結合。field_size = 当該レースの started 数。
2. **生スコア**(per run): `late_gain=(corner_last−finish_order)/field_size`・`early_pos=corner_first/field_size`・`mid_move=max 連続改善/field_size`。corner parse 失敗・field_size≤0・コーナー<2(mid_move のみ)→ NaN。
3. **as-of**: horse_id×race_date 昇順で expanding(cumsum/cumcount → avg、cummax → best)を per-run に付与 → `merge_asof(targets, on=race_date, by=horse_id, direction=backward, allow_exact_matches=False)`。
4. **出力**: 4 列 astype("float64")。NaN 伝播(0 埋め禁止)。

## registry / version

- 4 列: `FeatureMeta("pace", PRE_ENTRY, NULL)`、FEATURE_GROUPS group=`corner_trajectory`。
- `FEATURE_VERSION = "features-012"`。リテラル波及: test_feature023_leak_guard.py(features-011→012)。

## materialize 結線

- `build_asof_features` 末尾 merge チェーンに `.merge(cornertraj, on=_KEYS, how="left")` を追加(031-033 同型)。
- source_fingerprint 無改修(新ソース列なし)。serving 未来レース = 単一レース fallback(既存機構)。

## 採用ゲート

- `training feature-eval --drop-groups corner_trajectory`(既定 drop を corner_trajectory に変更)。
- PRIMARY: mean win LogLoss 改善 かつ mean ECE 非悪化(tol 1e-3)+ strict majority + worst-fold ECE 2e-3 + worst-fold dLogLoss 5e-3(030-033 と同一、事前登録)。
- 採用時: `train-evaluate --model-version lgbm-041 --objective cond_logit --calibration isotonic --target-encode jockey_id,trainer_id --te-smoothing 50` → active 昇格・lgbm-039 retired(feature_hash=features-012)。
