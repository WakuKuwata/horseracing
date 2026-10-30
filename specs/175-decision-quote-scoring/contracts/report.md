# 175 contracts — 出力 JSON(ScoreReport)

```json
{
 "schema": "near-post-score-v1",
 "power_note": {"min_points": 300, "verdict_blocked_below_min": true,
   "expected_points_2026": {"expected_return": 140, "expected_return_mix": 230},
   "text": "300 点未満のあいだ三値は insufficient。2026 年内の見込み点数は …"},
 "rule": {"version": "display-update-rule-v1", "json_sha256": "…", "settlement": "official_win_payout_100yen_per_marked_horse"},
 "inputs": {"from": "2026-10-31", "to": "2026-11-01", "n_rows": 48, "candidates_json_sha256": "…", "code_sha": "…", "quote_source": "decision_quotes"},
 "funnel": {"n_rows": 48, "jump_race": 0, "window_out": 0, "official_after_capture": 0, "captured_after_post": 0,
   "field_mismatch": 1, "invalid_values": 0, "pending_result": 0, "payout_race_missing": 0, "payout_inconsistent": 0, "eligible": 47},
 "columns": [{"column": "expected_return", "threshold": 1.2, "n_races": 47, "n_bets": 11, "n_hits": 1, "stake_yen": 1100, "return_yen": 850,
   "recovery_rate": 0.7727, "ci": [0.0, 2.1], "p_one_sided": 0.7, "n_days": 2, "min_points": 300, "remaining_to_min": 289,
   "bootstrap": {"impl": "horseracing_eval.bootstrap.race_block_ratio_bootstrap_ci_v1", "b": 20000, "seed": 20260905, "block": "race_day", "block_universe": "race-days with >=1 bet of the column"}},
   {"column": "expected_return_mix", "threshold": 1.0, "n_races_mix_unavailable": 0, "...": "..."}],
 "paired": [{"name": "ens15-norm148-v1", "kind": "postprocess", "artifact_sha256": "…", "registered_on": "2026-10-30",
   "n_races_eligible": 47, "n_races_reproducible": 45, "n_races_not_reproducible": 2, "max_abs_diff_p_ens": 1.2e-5,
   "features_source": {"parquet": 45, "rebuilt": 2}, "features_sha_match": 45,
   "incumbent": {"n_bets": 10, "recovery_rate": 0.8}, "candidate": {"n_bets": 7, "recovery_rate": 1.1},
   "diff": 0.3, "diff_ci": [-1.2, 1.9], "n_points": 7, "verdict": "insufficient",
   "pre_registration": {"n_points": 0, "diff": null}}],
 "attention": {"diagnostic": true, "not_used_for_verdict": true, "rule_set_version": "attention-S1-S5-v1",
   "definitions_sha256": "…", "match_impl": "horseracing_eval.attention_rules.match_mask",
   "gap_definition": "days_since_last = race_date − the horse's most recent STARTED race_date strictly before it …",
   "note": "138 の S1〜S5(凍結定義)を T−3 の判断記録に当てた集計。…",
   "rules": [{"rule_id": "S1", "rank": 1, "definition_ja": "…", "uses_ensemble": true, "ev_gt": 1.2, "odds_band": [20, 40], "gap_days": [14, 112],
     "n_races": 47, "n_races_with_bets": 3, "n_bets": 3, "n_hits": 0, "stake_yen": 300, "return_yen": 0, "recovery_rate": 0.0,
     "ci": null, "p_one_sided": null, "n_days": 2, "valuation_basis": "official_win_payout",
     "bootstrap": {"...": "same impl/b/seed/block as columns", "block_universe": "race-days with >=1 bet of the rule"},
     "price_drift_marked": {"definition": "log(closing_win_odds / quoted_win_odds)", "n": 3, "p10": -0.2, "p50": 0.05, "p90": 0.3, "sd": 0.2, "mean": 0.04},
     "n_races_with_bets_closing_unavailable": 0}, {"rule_id": "S2", "...": "..."}]},
 "diagnostics": {"diagnostic": true, "not_used_for_verdict": true,
   "calibration": {"ens15": {"ev_band": {"(1.2,inf)": {"odds_band": {"[20,40)": {"n": 5, "sum_won": 1, "sum_p": 0.31, "ratio": 3.2, "ci": [0.0, 9.9]}}}}}},
   "price_drift": {"n_races_closing_unavailable": 0, "all": {"n": 600, "p10": -0.3, "p50": 0.0, "p90": 0.3, "sd": 0.22, "mean": 0.01}, "marked": {"...": "..."}},
   "retention": {"n_marked": 11, "n_still_marked_at_closing": 2, "rate": 0.18, "approximation": "p_fixed"}},
 "run": {"run_id": "…", "computed_at": "…", "logic_version": "near-post-score-v1"}
}
```

不変条件:
- `funnel` の値の合計 = `funnel.n_rows`。
- `columns[*].stake_yen == 100 × n_bets`、`recovery_rate == return_yen / stake_yen`(n_bets>0)。
- `paired[*].verdict ∈ {insufficient, keep, replace}`、`n_points < 300 ⇒ insufficient`、`verdict == replace ⇔ n_points ≥ 300 AND diff_ci[0] > 0`。
- 候補なしの実行は `paired == []` と `verdict_overall: "no_candidate"`。
- `attention`(2026-10-30 追加・10/31 の結果を読む前): `rules` は 138 の `RULE_IDS` の順(S1〜S5)で、各目印は `attention_rules.match_mask`(凍結定義・`definitions_sha256` で束ねる)。精算・bootstrap は `columns` と同一。`diagnostic: true`・`not_used_for_verdict: true`=三値にも 138 のチェックポイントにも使わない。前走からの日数は `days_since_last`(直前の STARTED 出走との日数・無ければ None=間隔つき規則は不一致)。
- 語彙: キー・文言に「採用/改善/利益」を含まない(テストで固定)。
- 出力ファイルは新規作成のみ。
