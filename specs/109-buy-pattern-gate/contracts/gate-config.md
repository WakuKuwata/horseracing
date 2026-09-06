# Contract: gate-config.json(凍結設定)

- hash 規約: `horseracing_eval.decision.gate_config_hash`(`_` 前置キーを落として canonical JSON を hash。既定の注入・正規化禁止)。
- 実行時: `freeze` 以外の全サブコマンドは `--gate-config-hash` と一致しなければ開始前に拒否(fail-closed)。

```json
{
  "_comment": "feature 109 買い目パターン採否ゲート 凍結設定(実行前に凍結)",
  "feature": "109-buy-pattern-gate",
  "bet_type": "win",
  "stake_yen": 100,
  "payout_rule": "odds_x_stake_if_won",
  "dead_heat": {"primary": "exclude_race", "sensitivities": ["equal_split", "half_odds"], "half_odds_rule": "odds / max(2, n_winners)"},
  "population_rule": {"bundle_digest": "8bdde268…", "track_types": ["芝", "ダ"], "require_odds_all_started": true, "require_p_all_started": true, "exactly_one_winner": true},
  "windows": {"discovery": ["2008-01-01", "2013-12-31"], "qualification": ["2014-01-01", "2018-12-31"], "confirmatory": ["2019-01-01", "<DB の最終確定レース日・freeze が転記>"]},
  "expected_races_per_year": {"_source": "DB 実測 2026-09-05(races 表・2026 は部分年)", "2008": 3452, "2009": 3453, "2010": 3454, "2011": 3453, "2012": 3454, "2013": 3454, "2014": 3451, "2015": 3454, "2016": 3454, "2017": 3455, "2018": 3454, "2019": 3452, "2020": 3456, "2021": 3456, "2022": 3456, "2023": 3456, "2024": 3454, "2025": 3455, "2026": null, "_policy": "record_only"},
  "bootstrap": {"block": "race_day", "b": 20000, "seed": 20260905, "alpha_two_sided": 0.05,
                "sensitivity_blocks": ["iso_week", "calendar_month"], "block_kind": "fixed_cluster"},
  "mde_80": {"formula": "(z_{1-alpha/m} + z_{0.80}) * sd(replicates)", "alpha_one_sided": 0.025, "m_screening": 1, "m_confirmatory": "n_survivors"},
  "selftest": {"b_inner_size": 20000, "b_inner_power": 5000, "reps_size_per_config": 2000, "reps_power": 1000, "analysis_versions": ["race_day", "iso_week", "calendar_month"], "checkpoint_per_target": true, "boundary_null": "target_1.00_others_le_1.00",
               "rho_grid": [1.0, 1.025, 1.05, 1.075, 1.10, 1.15], "rho_negative_control": 0.796,
               "edge_shapes": ["klmin_roi_tilt", "long_odds_top20pct", "days_10pct"], "edge_shape_sensitivity": "odds_neutral", "size_alpha_one_sided": 0.025},
  "test": {"profit_null": "roi_le_1", "futility_null": "roi_ge_1.02", "p_value": "basic_bootstrap_one_sided_plus_one", "holm_alpha_one_sided": 0.025, "synchronized_block_draws": true},
  "demotion": {"min_hits": 20, "max_single_hit_share": 0.5, "min_days": 200},
  "field_size_definition": "entered", "past_quantile": "strict_past_expanding", "past_quantile_warmup_through": "2008-12-31", "survivor_rule": {"point_min": 1.0, "rank_key": "min_lcb", "max_survivors": 5, "merge_identical": true, "jaccard_max": 0.9},
  "ruled_out_delta": 0.02,
  "controls": ["no_bet", "favorite", "cap11_all", "cap21_all"], "control_aliases": {"ev1_all": "X.ev_ge_1"},
  "patterns_hash": "<patterns.json 生成後に転記>",
  "code_sha": "<freeze 時の HEAD = patterns.json を生成したコード。判定式の実装は run_code_sha で証拠側に記録>",
  "evaluation_contract_version": "roi-v1"
}
```

規則: `patterns_hash` と `code_sha` を転記した時点で凍結。以後の変更は新しい feature。
