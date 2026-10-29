# 175 data-model — 入力レコード・分類・集計・候補・出力

スキーマ変更なし(migration なし・新表なし)。すべて読み取り時の値。

## 1. QuoteRecord(live が組み立て、eval が読む)

| field | 出所 | 備考 |
|---|---|---|
| race_id / race_date / post_time / track_type | `races` | 平地判定は `track_type` に「障」を含まない |
| captured_at / official_at / seconds_to_post / capture_target | `decision_quotes` | tz-aware |
| field: list[{horse_id, horse_number, win_odds, popularity, place_low, place_high}] | `decision_quotes.field` | 馬番昇順 |
| pred: list[{horse_id, horse_number, p_ens, ev_ens, p_single, ev_single, p_mix, ev_mix}] | `decision_quotes.pred` | `p_mix`/`ev_mix` は `mix_status≠available` で null |
| mix_status / mix_coefficient_year / ensemble_manifest_sha256 / single_booster_sha256 / features_sha256 / logic_version / policy_version / run_id | `decision_quotes` | 出力に透過 |
| field_digest | `decision_quotes` | 現在の started 集合の digest(174 `field_digest`)と比較 |
| started: {horse_id: 馬番} | `race_horses`(entry_status=STARTED) | 読み取り時点の集合 |
| has_results / winner_horse_ids | `race_results` | 1 着=`finish_order==1 AND status==FINISHED`(同着は複数) |
| payouts: {馬番: 円/100 円} | `official_win_payouts` | 空=`payout_race_missing` |
| closing: {馬番: odds} or None | `race_horses.odds` | `races.win_odds_observed_at IS NOT NULL AND win_odds_official_at IS NULL AND has_results` のときだけ |

不変条件: `len(field) == len(pred) == n`・両者の馬番集合が一致(違えば `invalid_values`)。

## 2. 分類(ScoreClass・排他・先に失敗した 1 段)

`jump_race` → `window_out` (`|seconds_to_post − k×60| > 60`・k は `capture_target`=`t_minus_<k>` の k) → `official_after_capture` → `captured_after_post` → `field_mismatch`(field の (horse_id, 馬番) 集合 ≠ started、または field_digest ≠ digest(started)) → `invalid_values`(win_odds < 1・非有限・p̂ ∉ (0,1)・EV 非有限・field/pred 不一致) → `pending_result` → `payout_race_missing` → `payout_inconsistent`(`race_payout_consistent(勝馬の馬番, payouts.keys())` が偽) → `eligible`。

funnel: `{class: count}`・Σ = 候補行数。

## 3. 目印と精算(MarkedBet)

| field | 定義 |
|---|---|
| column | `expected_return`(`ev_ens > 1.2`)/ `expected_return_mix`(`mix_status==available AND ev_mix > 1.0`) |
| race_id / race_date / horse_number / odds_quote | 保存 quote |
| stake_yen | 100 |
| return_yen | `payouts.get(馬番, 0)` |
| hit | `return_yen > 0` |

列集計 ColumnTally(B8 と同じ項目): n_races / n_races_mix_unavailable / n_races_with_bets / n_bets / n_hits / stake_yen / return_yen / recovery_rate / ci / p_one_sided / n_days / min_points(300) / remaining_to_min / bootstrap{impl,b,seed,block,block_universe}。

## 4. 候補(CandidateRegistration・`candidates.json`)

```json
{"name": "ens15-norm148-v1", "kind": "postprocess", "artifact_path": null,
 "artifact_sha256": "<eval/decision_quote_scoring.py の sha256>",
 "mark_rule": {"column": "expected_return", "threshold": 1.2},
 "registered_on": "2026-10-30", "train_through": null,
 "note": "148: p_norm = p_ens / Σ_started p_ens, EV_norm = p_norm × odds"}
```

`kind ∈ {postprocess, ensemble_dir}`。`ensemble_dir` は `artifact_path` 必須・sha は当該年 manifest の sha(`EnsembleMarketEvModel.load_members` の戻り値)。

候補の予測 CandidatePred: {race_id, horse_number, p_cand, ev_cand, marked}。

## 5. 対(PairedRace)と再現ゲート

| field | 定義 |
|---|---|
| reproducible | 朝の特徴行+保存 quote で現行 `p_ens` を再予測し、全馬 `==` |
| max_abs_diff | 不一致時の最大 |Δp_ens|(一致時 0.0) |
| features_source | `parquet` / `rebuilt` |
| features_sha_match | 併記のみ(条件にしない) |
| in_verdict_window | `captured_at(JST 日付) ≥ registered_on` |

PairedTally: 対のレース集合は「eligible かつ reproducible」。現行・候補の列集計を同じレース集合で作り、差 `diff = cand.recovery − inc.recovery`、`diff_ci`(同期 replicate の行差)、`n_points = 候補の目印本数(in_verdict_window のみ)`、`verdict = decide(n_points, diff_ci_low)`。`pre_registration` には同じ集計を `in_verdict_window=False` の対で別掲。

## 6. 診断(Diagnostics・`diagnostic: true`)

- calibration[系統][ev_band][odds_band] = {n, sum_won, sum_p, ratio, ci}
- price_drift[group ∈ all/marked/pop_1_3/pop_4_8/pop_9p] = {n, p10, p50, p90, sd, mean}・`n_races_closing_unavailable`
- retention = {n_marked, n_still_marked_at_closing, rate, approximation: "p_fixed"}(任意 `replay` 版は `approximation: "replay"`)

## 7. 出力(ScoreReport)

contracts/report.md。`power_note`・`rule{version, json_sha256}`・`inputs{from,to,n_rows,candidates_sha256,code_sha}`・`funnel`・`columns[2]`・`paired[]`(候補ごと)・`diagnostics`・`run{run_id, computed_at, database_url_redacted}`。
