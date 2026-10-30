"""175: the pure scoring of decision quotes (funnel, settlement, synchronised diff, verdict)."""

from __future__ import annotations

import datetime
import json
import math

import pytest

from horseracing_eval import decision_quote_scoring as dqs
from horseracing_eval import display_update_rule as dur

UTC = datetime.UTC
JST = datetime.timezone(datetime.timedelta(hours=9))
POST = datetime.datetime(2026, 10, 31, 6, 0, tzinfo=UTC)
DAY = datetime.date(2026, 10, 31)


def horse(n, odds, p_ens, *, p_mix=None, pop=None, gap=None, p_single=None):
    ps = p_ens if p_single is None else p_single
    return dqs.QuoteHorse(
        horse_id=f"H{n:02d}", horse_number=n, win_odds=odds, popularity=pop or n,
        p_ens=p_ens, ev_ens=p_ens * odds, p_single=ps, ev_single=ps * odds,
        p_mix=p_mix, ev_mix=(p_mix * odds) if p_mix is not None else None,
        days_since_last=gap,
    )


def record(race_id="202610310101", *, horses=None, winners=("H01",), payouts=None, day=DAY,
           seconds_to_post=181, target="t_minus_3", has_results=True, mix_status="available",
           closing=None, track_type="芝", post=POST, official_lag=10, started=None, digest=None,
           captured_at=None):
    horses = horses if horses is not None else [
        horse(1, 2.0, 0.65, p_mix=0.4), horse(2, 10.0, 0.13, p_mix=0.09),
        horse(3, 40.0, 0.04, p_mix=0.05),
    ]
    started = started if started is not None else {h.horse_id: h.horse_number for h in horses}
    captured = captured_at or (post - datetime.timedelta(seconds=seconds_to_post))
    pay = payouts if payouts is not None else {started[w]: 200 for w in winners if started.get(w)}
    return dqs.QuoteRecord(
        race_id=race_id, race_date=day, post_time=post, track_type=track_type,
        captured_at=captured, official_at=captured - datetime.timedelta(seconds=official_lag),
        seconds_to_post=seconds_to_post, capture_target=target, horses=horses,
        mix_status=mix_status,
        field_digest=digest if digest is not None else dqs.digest_of_started(started),
        started=started, has_results=has_results, winner_horse_ids=frozenset(winners),
        payouts=pay, closing=closing,
    )


# ----------------------------------------------------------------------------- funnel


def test_eligible_record_and_funnel_sum():
    recs = [record(), record("202610310102")]
    f = dqs.funnel(recs)
    assert f["n_rows"] == 2 and f["eligible"] == 2
    assert sum(v for k, v in f.items() if k != "n_rows") == f["n_rows"]


@pytest.mark.parametrize("kwargs, expected", [
    ({"track_type": "障"}, "jump_race"),
    ({"seconds_to_post": 241}, "window_out"),
    ({"seconds_to_post": 119}, "window_out"),
    ({"seconds_to_post": 290, "target": "t_minus_4"}, "eligible"),      # collision member
    ({"seconds_to_post": 181, "target": "t_minus_5"}, "window_out"),
    ({"official_lag": -5}, "official_after_capture"),
    ({"captured_at": POST}, "captured_after_post"),
    ({"started": {"H01": 1, "H02": 2}}, "field_mismatch"),              # scratched after capture
    ({"digest": "0" * 64}, "field_mismatch"),
    ({"has_results": False}, "pending_result"),
    ({"payouts": {}}, "payout_race_missing"),
    ({"payouts": {2: 1000}}, "payout_inconsistent"),                    # winner is 馬番 1
])
def test_each_rule_fails_closed(kwargs, expected):
    assert dqs.classify(record(**kwargs)) == expected


def test_invalid_values_rule():
    bad = [horse(1, 0.5, 0.65), horse(2, 10.0, 0.13), horse(3, 40.0, 0.04)]
    assert dqs.classify(record(horses=bad)) == "invalid_values"
    nan_p = [horse(1, 2.0, float("nan")), horse(2, 10.0, 0.13), horse(3, 40.0, 0.04)]
    assert dqs.classify(record(horses=nan_p)) == "invalid_values"
    no_mix = [horse(1, 2.0, 0.65), horse(2, 10.0, 0.13), horse(3, 40.0, 0.04)]
    assert dqs.classify(record(horses=no_mix)) == "invalid_values"
    assert dqs.classify(record(horses=no_mix, mix_status="no_coefficients")) == "eligible"


def test_first_failing_rule_wins():
    assert dqs.classify(record(track_type="障", has_results=False)) == "jump_race"
    assert dqs.classify(record(seconds_to_post=500, payouts={})) == "window_out"


# ----------------------------------------------------------------------------- settlement


def test_marks_and_settlement_per_column():
    rec = record()  # ev_ens 1.30/1.30/1.60 → all marked at 1.2; ev_mix 0.8/0.9/2.0 → only 馬番 3
    assert dqs.marked_numbers(rec, "expected_return") == [1, 2, 3]
    assert dqs.marked_numbers(rec, "expected_return_mix") == [3]
    col = dqs.column_tally([rec], "expected_return")
    assert col["n_bets"] == 3 and col["n_hits"] == 1 and col["stake_yen"] == 300
    assert col["return_yen"] == 200 and col["recovery_rate"] == pytest.approx(200 / 300)
    assert col["ci"] is None and col["n_days"] == 1          # one race day: undecidable
    assert col["remaining_to_min"] == 297 and col["min_points"] == 300
    mix = dqs.column_tally([rec], "expected_return_mix")
    assert mix["n_bets"] == 1 and mix["n_hits"] == 0 and mix["recovery_rate"] == 0.0


def test_dead_heat_pays_each_winner_its_own_figure():
    rec = record(winners=("H01", "H02"), payouts={1: 150, 2: 600})
    col = dqs.column_tally([rec], "expected_return")
    assert col["n_hits"] == 2 and col["return_yen"] == 750


def test_missing_payout_excludes_the_whole_race():
    recs = [record(), record("202610310102", payouts={})]
    col = dqs.column_tally(recs, "expected_return")
    assert col["n_races"] == 1 and col["n_bets"] == 3
    assert dqs.funnel(recs)["payout_race_missing"] == 1


def test_mix_unavailable_counts_as_race_without_bets():
    col = dqs.column_tally([record(mix_status="no_coefficients", horses=[
        horse(1, 2.0, 0.65), horse(2, 10.0, 0.13), horse(3, 40.0, 0.04)])], "expected_return_mix")
    assert col["n_races"] == 1 and col["n_races_mix_unavailable"] == 1 and col["n_bets"] == 0


def test_bootstrap_is_the_frozen_rule_bootstrap():
    col = dqs.column_tally([record()], "expected_return")
    assert col["bootstrap"]["impl"] == dur.BOOTSTRAP["impl"]
    assert col["bootstrap"]["b"] == dur.BOOTSTRAP["b"] == 20000
    assert col["bootstrap"]["seed"] == dur.BOOTSTRAP["seed"]


def _many_days(n_days=6):
    recs = []
    for i in range(n_days):
        day = DAY + datetime.timedelta(days=i)
        post = POST + datetime.timedelta(days=i)
        recs.append(record(f"2026110{i:02d}0101", day=day, post=post,
                           winners=("H01",) if i % 2 else ("H03",)))
    return recs


def test_interval_appears_with_several_race_days():
    col = dqs.column_tally(_many_days(), "expected_return")
    assert col["n_days"] == 6 and col["ci"] is not None
    assert col["ci"][0] <= col["recovery_rate"] <= col["ci"][1]


# ----------------------------------------------------------------------------- candidates


def test_normalize_148_is_p_over_sum_times_odds():
    ev = dqs.normalize_148([0.2, 0.2, 0.6], [5.0, 10.0, 1.5])
    assert ev == pytest.approx([1.0, 2.0, 0.9])
    with pytest.raises(ValueError):
        dqs.normalize_148([0.2, -0.1], [5.0, 10.0])


def _identity_candidate(recs):
    return {r.race_id: dqs.CandidateRace(
        race_id=r.race_id, reproducible=True, max_abs_diff=0.0, features_source="parquet",
        features_sha_match=True, ev_by_number={h.horse_number: h.ev_ens for h in r.horses},
        incumbent_ev_by_number={h.horse_number: h.ev_ens for h in r.horses},
    ) for r in recs}


def test_identity_candidate_has_zero_diff_and_degenerate_interval():
    recs = _many_days()
    out = dqs.paired_tally(recs, _identity_candidate(recs), name="identity", threshold=1.2,
                           registered_on=DAY, tz=JST)
    assert out["n_races_reproducible"] == 6 and out["n_races_not_reproducible"] == 0
    assert out["name"] == "identity" and isinstance(out["candidate"], dict)
    assert out["diff"] == 0.0 and out["diff_ci"] == [0.0, 0.0]
    assert out["n_points"] == out["candidate"]["n_bets"] == 18
    assert out["verdict"] == "insufficient"


def test_not_reproducible_races_leave_the_pair_and_are_counted():
    recs = _many_days()
    cands = _identity_candidate(recs)
    rid = recs[0].race_id
    cands[rid] = dqs.CandidateRace(rid, False, 1.2e-5, "rebuilt", False, None, None)
    out = dqs.paired_tally(recs, cands, name="x", threshold=1.2, registered_on=DAY, tz=JST)
    assert out["n_races_not_reproducible"] == 1 and out["max_abs_diff_p_ens"] == 1.2e-5
    assert out["features_source"] == {"parquet": 5, "rebuilt": 1} and out["n_points"] == 15


def test_registration_date_splits_verdict_and_pre_registration_points():
    recs = _many_days()
    reg = DAY + datetime.timedelta(days=3)   # 06:00 UTC = 15:00 JST, same calendar day
    out = dqs.paired_tally(recs, _identity_candidate(recs), name="x", threshold=1.2,
                           registered_on=reg, tz=JST)
    assert out["n_points"] == 9 and out["pre_registration"]["n_points"] == 9


def test_verdict_wiring_to_the_frozen_rule():
    assert dqs.verdict(299, 0.5) == "insufficient"
    assert dqs.verdict(300, 0.0) == "keep"
    assert dqs.verdict(300, 1e-9) == "replace"


def test_candidate_that_marks_more_winners_shows_positive_diff():
    recs = _many_days()
    cands = {}
    for r in recs:
        win = {r.started[w] for w in r.winner_horse_ids}
        cands[r.race_id] = dqs.CandidateRace(
            r.race_id, True, 0.0, "parquet", True,
            ev_by_number={h.horse_number: (2.0 if h.horse_number in win else 0.5)
                          for h in r.horses},
            incumbent_ev_by_number=None)
    out = dqs.paired_tally(recs, cands, name="oracle", threshold=1.2, registered_on=DAY, tz=JST)
    assert out["candidate"]["recovery_rate"] > out["incumbent"]["recovery_rate"]
    assert out["diff"] > 0 and out["diff_ci"] is not None
    assert out["bootstrap"]["seed"] == dur.BOOTSTRAP["seed"]


# ----------------------------------------------------------------------------- diagnostics


def test_calibration_band_edges():
    assert dqs._ev_band(0.8) == "(-inf,0.8]" and dqs._ev_band(0.8000001) == "(0.8,1]"
    assert dqs._ev_band(1.2) == "(1,1.2]" and dqs._ev_band(1.21) == "(1.2,inf]"
    assert dqs._odds_band(4.99) == "[1,5)" and dqs._odds_band(5.0) == "[5,10)"
    assert dqs._odds_band(40.0) == "[40,inf)" and dqs._odds_band(39.9) == "[20,40)"


def test_calibration_cells_sum_won_over_sum_p():
    rec = record()
    cells = dqs.calibration_cells([rec])["systems"]["ens15"]
    c = cells["(1.2,inf]"]["[1,5)"]      # 馬番 1: ev 1.3, odds 2.0, winner
    assert c["n"] == 1 and c["sum_won"] == 1 and c["sum_p"] == pytest.approx(0.65)
    assert c["ratio"] == pytest.approx(1 / 0.65)
    assert "mix" in dqs.calibration_cells([rec])["systems"]
    plain = [horse(1, 2.0, 0.65), horse(2, 10.0, 0.13), horse(3, 40.0, 0.04)]
    assert "mix" not in dqs.calibration_cells([record(mix_status="disabled", horses=plain)])["systems"]


def test_price_drift_and_retention_use_final_odds_only():
    rec = record(closing={1: 4.0, 2: 10.0, 3: 20.0})
    d = dqs.price_drift([rec])
    assert d["all"]["n"] == 3 and d["all"]["p50"] == pytest.approx(0.0)
    assert d["marked"]["n"] == 3 and d["pop_1_3"]["n"] == 3
    assert dqs.price_drift([record()])["n_races_closing_unavailable"] == 1
    r = dqs.retention([rec])
    # p_ens × O_F: 0.65×4=2.6 ✓, 0.13×10=1.3 ✓, 0.04×20=0.8 ✗
    assert r == {"diagnostic": True, "approximation": "p_fixed", "n_marked": 3,
                 "n_still_marked_at_closing": 2, "rate": pytest.approx(2 / 3)}


# ----------------------------------------------------------------------------- report


def test_report_is_deterministic_and_free_of_forbidden_words():
    recs = _many_days()
    cands = {"identity": {"registered_on": DAY.isoformat(), "mark_rule": {"threshold": 1.2}}}
    races = {"identity": _identity_candidate(recs)}
    a = dqs.build_report(recs, candidates=cands, candidate_races=races, tz=JST)
    b = dqs.build_report(recs, candidates=cands, candidate_races=races, tz=JST)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert a["schema"] == dqs.SCHEMA and a["power_note"]["min_points"] == 300
    assert a["verdict_overall"] == "insufficient"
    assert a["diagnostics"]["not_used_for_verdict"] is True
    assert dqs.forbidden_vocabulary(a) == []
    assert dqs.build_report(recs, tz=JST)["verdict_overall"] == "no_candidate"
    assert not math.isnan(a["columns"][0]["recovery_rate"])


# ----------------------------------------------------------------------------- attention (138 S1〜S5)


def test_attention_marks_follow_the_frozen_definitions():
    from horseracing_eval import attention_rules as ar

    hs = [
        horse(1, 25.0, 0.05, gap=30.0),                # ens EV 1.25, band, gap → S1/S3/S4/S5 (not S2: ≤1.3)
        horse(2, 25.0, 0.06, gap=None),                # gap unknown → S3 only
        horse(3, 25.0, 0.06, gap=113.0),               # gap out of range → S3 only
        horse(4, 40.0, 0.04, gap=30.0),                # odds 40 (band is < 40) → S3 only
        horse(5, 30.0, 0.045, gap=14.0),               # EV 1.35 → S1+S2+S3+S4
        horse(6, 2.0, 0.3, gap=30.0, p_single=0.65),   # ens 0.6 / single 1.3 → S5 only
        horse(7, 39.9, 0.03, gap=112.0),               # EV 1.197 → S4 only (>1.1)
    ]
    rec = record(horses=hs, mix_status="no_coefficients")
    assert dqs.classify(rec) == "eligible"
    assert dqs.attention_marks(rec, "S1") == [1, 5]
    assert dqs.attention_marks(rec, "S2") == [5]
    assert dqs.attention_marks(rec, "S3") == [1, 2, 3, 4, 5]
    assert dqs.attention_marks(rec, "S4") == [1, 5, 7]
    assert dqs.attention_marks(rec, "S5") == [1, 2, 3, 4, 5, 6]
    # the eval definitions are the single source: each mark agrees with attention_rules.matches
    for h in hs:
        for rid in dqs.ATTENTION_RULE_IDS:
            assert (h.horse_number in dqs.attention_marks(rec, rid)) == ar.matches(
                ar.definition(rid), ens_ev=h.ev_ens, single_ev=h.ev_single, odds=h.win_odds,
                days_since_last=h.days_since_last)


def test_attention_tally_settles_like_the_columns_and_keeps_marked_drift():
    hs = [horse(1, 25.0, 0.06, gap=30.0), horse(2, 30.0, 0.05, gap=20.0), horse(3, 2.0, 0.5)]
    rec = record(horses=hs, winners=("H02",), payouts={2: 2800}, closing={1: 20.0, 2: 28.0, 3: 2.0},
                 mix_status="no_coefficients")
    t = dqs.attention_tally([rec], "S1")
    assert t["n_bets"] == 2 and t["n_hits"] == 1 and t["stake_yen"] == 200 and t["return_yen"] == 2800
    assert t["recovery_rate"] == pytest.approx(14.0) and t["ci"] is None and t["n_days"] == 1
    assert t["valuation_basis"] == "official_win_payout"
    assert t["bootstrap"]["impl"] == dur.BOOTSTRAP["impl"]
    d = t["price_drift_marked"]
    assert d["n"] == 2 and d["p50"] == pytest.approx((math.log(20 / 25) + math.log(28 / 30)) / 2)
    # a race whose payout is missing leaves the funnel and the tally alike
    t2 = dqs.attention_tally([rec, record("202610310102", horses=hs, payouts={},
                                           mix_status="no_coefficients")], "S1")
    assert t2["n_races"] == 1 and t2["n_bets"] == 2
    # closing unavailable: counted, no drift
    t3 = dqs.attention_tally([record(horses=hs, winners=("H02",), payouts={2: 2800},
                                     mix_status="no_coefficients")], "S1")
    assert t3["n_races_with_bets_closing_unavailable"] == 1 and t3["price_drift_marked"]["n"] == 0


def test_attention_section_is_diagnostic_and_tied_to_the_frozen_definitions():
    from horseracing_eval import attention_rules as ar

    rep = dqs.build_report(_many_days(), tz=JST)
    sec = rep["attention"]
    assert sec["diagnostic"] is True and sec["not_used_for_verdict"] is True
    assert sec["rule_set_version"] == ar.RULE_SET_VERSION
    assert sec["definitions_sha256"] == ar.definitions_sha256()
    assert [r["rule_id"] for r in sec["rules"]] == list(ar.RULE_IDS)
    assert [r["rank"] for r in sec["rules"]] == sorted(r["rank"] for r in sec["rules"])
    for r in sec["rules"]:
        assert r["stake_yen"] == 100 * r["n_bets"]
        assert r["definition_ja"] == ar.definition(r["rule_id"]).definition_ja
    assert rep["verdict_overall"] == "no_candidate"     # unchanged by the attention section
    assert dqs.forbidden_vocabulary(rep) == []
