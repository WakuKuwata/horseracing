"""140 T006-T009: research harness for the judged-EV alignment (generator / judged training / correction / scoring).

cd training && uv run pytest ../scripts/tests/test_judged_20261011.py -q
"""

import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "roi_explore" / "judged_20261011"))
import common as C  # noqa: E402
import fit_correction as FC  # noqa: E402
import fit_generator as FG  # noqa: E402
import score as SC  # noqa: E402
import train_judged as TJ  # noqa: E402

from horseracing_training import market_ev  # noqa: E402

BANDS_TRUE = [
    (5.0, 0.30, 0.10, 0.20),
    (20.0, 0.50, 0.20, 0.25),
    (45.0, 0.60, 0.25, 0.30),
    (120.0, 0.70, 0.30, 0.35),
    (400.0, 0.80, 0.35, 0.35),
    (1200.0, 1.00, 0.36, 0.70),
]
RHO_TRUE = 0.16


def _synthetic_pairs(n_pairs_per_band=250, n_horses=12, seed=3):
    rng = np.random.default_rng(seed)
    rows = []
    for bi, (mins, a, b, s) in enumerate(BANDS_TRUE):
        for k in range(n_pairs_per_band):
            pid = f"p{bi}_{k}"
            of = np.exp(rng.normal(2.5, 1.0, n_horses)) + 1.0
            q = (1 / of) / (1 / of).sum()
            eps_r = rng.standard_normal()
            u = (
                a
                + b * np.log(q)
                + s
                * (
                    np.sqrt(RHO_TRUE) * eps_r
                    + np.sqrt(1 - RHO_TRUE) * rng.standard_normal(n_horses)
                )
            )
            oj = of * np.exp(u)
            for i in range(n_horses):
                rows.append(
                    {
                        "pair_id": pid,
                        "race_id": pid,
                        "horse_id": f"h{i}",
                        "horse_number": float(i + 1),
                        "O_J": oj[i],
                        "O_F": of[i],
                        "q_F": q[i],
                        "hours": mins / 60.0 + rng.uniform(-0.01, 0.01),
                        "race_date": "2026-09-01",
                        "source": "SYN",
                        "won": i == 0,
                    }
                )
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ T006 generator


def test_generator_recovers_known_coefficients():
    pairs = _synthetic_pairs()
    gen = FG.build_generator(pairs, pairs_path=None)
    assert [b["band"] for b in gen["bands"]] == list(C.H_BAND_LABELS)
    for est, (_, a, b, s) in zip(gen["bands"], BANDS_TRUE, strict=True):
        assert abs(est["a"] - a) < 0.06 and abs(est["b"] - b) < 0.03 and abs(est["s"] - s) < 0.04
    assert abs(gen["rho"] - RHO_TRUE) < 0.06
    assert gen["period"] == {"from": "2026-09-01", "to": "2026-09-01"}
    assert gen["comparison_with_m2_reference"]["reference"]["rho"] == 0.16


def test_generator_refuses_pairs_outside_fit_period():
    pairs = _synthetic_pairs(n_pairs_per_band=5)
    pairs["race_date"] = "2026-10-10"
    with pytest.raises(ValueError, match="fit period"):
        FG.build_generator(pairs, pairs_path=None)


def test_h_band_index_is_right_closed():
    idx = C.h_band_index(
        np.array([0.5, 10.0, 10.01, 30.0, 60.0, 180.0, 600.0, 600.5, 3000.0, 3001.0, 0.0])
    )
    assert idx.tolist() == [0, 0, 1, 1, 2, 3, 4, 5, 5, -1, -1]
    assert C.h_band_index(np.array([3001.0]), open_last=True).tolist() == [5]
    assert SC.h_band_label(np.array([5.0, 700.0])).tolist() == ["(0,10]", ">600"]


def test_perturb_preserves_overround_and_floor():
    gen = {"bands": [{"a": 0.5, "b": 0.3, "s": 0.4}] * 6, "rho": 0.16}
    rng = np.random.default_rng(1)
    odds = np.array([1.5, 3.0, 8.0, 20.0, 50.0, 200.0, 2.2, 6.0, 15.0, 90.0])
    seg, n_r = C.race_segments(np.array(["a"] * 6 + ["b"] * 4))
    oj = C.perturb_odds(odds, seg, n_r, np.array([30.0, 500.0]), gen, rng)
    assert (oj >= 1.0).all() and np.allclose(oj, np.round(oj, 1))
    # before rounding the per-race Σ1/O is preserved: rounding to 0.1 keeps it within a few percent
    for r in range(n_r):
        m = seg == r
        assert abs((1 / oj[m]).sum() - (1 / odds[m]).sum()) < 0.05 * (1 / odds[m]).sum()
    zero = {"bands": [{"a": 0.0, "b": 0.0, "s": 0.0}] * 6, "rho": 0.16}
    assert np.allclose(C.perturb_odds(odds, seg, n_r, np.array([30.0, 500.0]), zero, rng), odds)


def test_perturb_race_constant_terms_cancel():
    """statistics-2: a(h) and the race-common noise s·√ρ·ε_race are exp(constant) per race and are
    cancelled exactly by the Σ1/O renormalisation — only b(h) and s·√(1−ρ) act on the odds."""
    odds = np.array([1.5, 3.0, 8.0, 20.0, 50.0, 200.0, 2.2, 6.0, 15.0, 90.0])
    seg, n_r = C.race_segments(np.array(["a"] * 6 + ["b"] * 4))
    mins = np.array([30.0, 500.0])
    a_only = {"bands": [{"a": 5.0, "b": 0.0, "s": 0.0}] * 6, "rho": 0.16}
    assert np.allclose(C.perturb_odds(odds, seg, n_r, mins, a_only, np.random.default_rng(1)), odds)
    rho_one = {"bands": [{"a": 0.0, "b": 0.0, "s": 0.9}] * 6, "rho": 1.0}
    assert np.allclose(C.perturb_odds(odds, seg, n_r, mins, rho_one, np.random.default_rng(1)), odds)
    # the injected within-race residual variance of log(O_J/O_F) is s^2 (1 - rho), whatever rho
    rng = np.random.default_rng(7)
    n_races, n_h = 600, 12
    of = np.exp(rng.normal(3.5, 0.6, n_races * n_h)) + 1.0          # 30-ish odds: 0.1 rounding ≈ 0
    seg, n_r = C.race_segments(np.repeat(np.arange(n_races), n_h).astype(str))
    for rho in (0.0, 0.08, 0.5):
        gen = {"bands": [{"a": 0.3, "b": 0.0, "s": 0.3}] * 6, "rho": rho}
        oj = C.perturb_odds(of, seg, n_r, np.full(n_r, 120.0), gen, np.random.default_rng(3))
        u = np.log(oj / of)
        within = pd.Series(u).groupby(seg).transform(lambda x: x - x.mean())
        var_within = float((within**2).sum() / (len(u) - n_r))
        assert abs(var_within - 0.3**2 * (1 - rho)) < 0.05 * 0.3**2 * (1 - rho) + 2e-4, (rho, var_within)


def test_fit_scripts_print_the_disclaimers(tmp_path, capsys, monkeypatch):
    """harness-leak-protocol-3 / FR-011: the disclaimers are on stdout too, not only in the JSON."""
    pairs = _synthetic_pairs(n_pairs_per_band=20)
    pairs_p = tmp_path / "pairs.parquet"
    pairs.to_parquet(pairs_p, index=False)
    monkeypatch.setattr(C, "ROOT", tmp_path)   # the input path is recorded relative to the repo root
    assert FG.main(["--pairs", str(pairs_p), "--out", str(tmp_path / "gen.json")]) == 0
    out = capsys.readouterr().out
    for line in C.DISCLAIMERS:
        assert line in out


# ------------------------------------------------------------------ T007 judged training + loader rejection


def _tiny_base_spec():
    feats = (
        json.loads((C.ENS_DIR / "seed_01" / "model.spec.json").read_text())["features"]
        if (C.ENS_DIR / "seed_01" / "model.spec.json").exists()
        else [
            "odds",
            "q",
            "popularity",
            "odds_rank",
            "q_share_of_fav",
            "fav_q",
            "fav_odds",
            "second_odds",
            "odds_gap12",
            "q_entropy_norm",
            "n_fav_under_2",
            "n_odds_under_10",
            "field_size",
            "distance",
        ]
    )
    cats = ["venue_code", "track_type"]
    return {
        "objective": "binary",
        "features": feats,
        "cats": cats,
        "cat_maps": {"venue_code": {"05": 0, "06": 1}, "track_type": {"芝": 0, "ダ": 1}},
        "drop_groups": ["sameday", "weightlive"],
        "feature_hash": "x",
    }


def _tiny_rows(spec, n_races=40, n_horses=10, seed=5):
    rng = np.random.default_rng(seed)
    rows = []
    for r in range(n_races):
        rid = f"2025{r:08d}"
        odds = np.round(np.exp(rng.normal(2.2, 0.9, n_horses)) + 1.0, 1)
        w = rng.integers(0, n_horses)
        for i in range(n_horses):
            d = {f: float(rng.normal()) for f in spec["features"]}
            d.update(
                {
                    "race_id": rid,
                    "horse_id": f"h{r}_{i}",
                    "horse_number": float(i + 1),
                    "odds": odds[i],
                    "won": i == w,
                    "race_ok": True,
                    "dead_heat": False,
                    "year": 2025 if r % 5 else 2026,
                    "venue_code": "05" if r % 2 else "06",
                    "track_type": "芝",
                }
            )
            rows.append(d)
    return pd.DataFrame(rows)


def _r02():
    return C.load_r02()


def test_training_frame_doubles_rows_and_sets_log_min():
    spec = _tiny_base_spec()
    rows = _tiny_rows(spec)
    gen = {"bands": [{"a": 0.5, "b": 0.3, "s": 0.3}] * 6, "rho": 0.16}
    trn, info = TJ.make_training_frame(rows, gen, np.array([20.0, 300.0]), _r02(), seed=1)
    n25 = int((rows["year"] <= 2025).sum())
    assert len(trn) == 2 * n25 and info["n_rows_injected"] == n25
    assert (trn["log_min"].iloc[:n25] == 0.0).all()
    inj = trn.iloc[n25:]
    assert set(np.round(np.exp(inj["log_min"]), 6)) <= {20.0, 300.0}
    assert (inj["popularity"] == inj["odds_rank"]).all()
    # the market block is recomputed from the perturbed odds
    inv = 1 / inj["odds"]
    assert np.allclose(inj["q"], inv / inv.groupby(inj["race_id"]).transform("sum"))


def test_train_judged_cli_writes_spec_and_production_loader_rejects_it(tmp_path):
    spec = _tiny_base_spec()
    rows = _tiny_rows(spec)
    rows_p, base_p, gen_p, pairs_p = (
        tmp_path / "rows.parquet",
        tmp_path / "base.spec.json",
        tmp_path / "generator.json",
        tmp_path / "pairs.parquet",
    )
    rows.to_parquet(rows_p, index=False)
    base_p.write_text(json.dumps(spec))
    gen_p.write_text(json.dumps({"bands": [{"a": 0.5, "b": 0.3, "s": 0.3}] * 6, "rho": 0.16}))
    pd.DataFrame({"pair_id": ["a", "b"], "hours": [0.5, 5.0]}).to_parquet(pairs_p, index=False)
    out = tmp_path / "judged"
    assert (
        TJ.main(
            [
                "--seed",
                "1",
                "--out",
                str(out),
                "--rows",
                str(rows_p),
                "--pairs",
                str(pairs_p),
                "--generator",
                str(gen_p),
                "--base-spec",
                str(base_p),
                "--rounds",
                "2",
                "--limit-rows",
                "200",
            ]
        )
        == 0
    )
    sj = json.loads((out / "seed_01" / "model.spec.json").read_text())
    assert sj["judged"] is True and sj["version"] == C.JUDGED_VERSION
    assert (
        sj["features"] == spec["features"] + ["log_min"]
        and len(sj["features"]) == len(spec["features"]) + 1
    )
    assert sj["num_threads"] == 1 and sj["deterministic"] is True and sj["seed"] == 1
    assert sj["trained_for_year"] == 2026 and sj["train_through"] == 2025
    assert sj["model_sha256"] == C.sha256_file(out / "seed_01" / "model_2026.txt")
    # (1) the production ensemble loader needs ensemble.spec.json → a judged dir cannot be loaded at all
    with pytest.raises(FileNotFoundError):
        market_ev.EnsembleMarketEvModel.load(out, C.ENS_VERSION)
    # (2) even if someone copied the judged spec into place, ``judged: true`` is refused first
    # (harness-leak-protocol-1) — and without that flag the version still differs → refused
    (out / market_ev.ENSEMBLE_SPEC_NAME).write_text(
        json.dumps(dict(sj, seeds=list(C.SEEDS), train_from=2007))
    )
    with pytest.raises(market_ev.EnsembleIntegrityError, match="judged"):
        market_ev.EnsembleMarketEvModel.load(out, C.ENS_VERSION)
    (out / market_ev.ENSEMBLE_SPEC_NAME).write_text(
        json.dumps(dict(sj, seeds=list(C.SEEDS), train_from=2007, judged=False))
    )
    with pytest.raises(market_ev.EnsembleIntegrityError, match="version"):
        market_ev.EnsembleMarketEvModel.load(out, C.ENS_VERSION)
    # (2b) the judged flag alone is refused even under the production version string
    (out / market_ev.ENSEMBLE_SPEC_NAME).write_text(
        json.dumps(dict(sj, seeds=list(C.SEEDS), train_from=2007, version=C.ENS_VERSION))
    )
    with pytest.raises(market_ev.EnsembleIntegrityError, match="judged"):
        market_ev.EnsembleMarketEvModel.load(out, C.ENS_VERSION)
    # (3) and the production feature frame has no log_min → the judged spec cannot build a design matrix
    with pytest.raises(KeyError):
        market_ev._design_matrix(sj, rows.head(5))


def test_train_judged_rejects_seed_out_of_range():
    with pytest.raises(SystemExit):
        TJ.main(["--seed", "16"])


# ------------------------------------------------------------------ T008 correction


def test_logistic_fit_recovers_coefficients():
    rng = np.random.default_rng(11)
    n = 60000
    p_hat = rng.uniform(0.01, 0.6, n)
    h = np.exp(rng.uniform(np.log(5), np.log(1500), n))
    q = rng.uniform(0.01, 0.6, n)
    true = np.array([0.3, 0.8, -0.05, 0.2])
    z = FC.design(p_hat, h, q) @ true
    y = (rng.uniform(size=n) < C.sigmoid(z)).astype(float)
    fit = FC.fit_logistic(FC.design(p_hat, h, q), y)
    est = np.array([fit["coef"][k] for k in FC.COEF_NAMES])
    se = np.array([fit["se"][k] for k in FC.COEF_NAMES])
    assert (
        fit["converged"]
        and np.all(np.abs(est - true) < 4 * se)
        and np.all(np.abs(est - true) < 0.1)
    )
    assert fit["loglik"] > fit["loglik_null"]
    p2 = FC.apply_correction(fit["coef"], p_hat, h, q)
    assert p2.shape == (n,) and (0 < p2).all() and (p2 < 1).all()


def test_c1_frame_recomputes_q_from_judged_odds():
    S = pd.DataFrame(
        {
            "pair_id": ["a"] * 3 + ["b"] * 2,
            "horse_id": list("vwxyz"),
            "horse_number": [1.0, 2.0, 3.0, 1.0, 2.0],
            "O_J": [2.0, 4.0, 8.0, 1.5, 3.0],
            "EVe_J": [0.9, 0.8, 1.2, 0.95, 1.0],
            "hours": [0.5] * 3 + [10.0] * 2,
            "won": [True, False, False, False, True],
            "dead_heat": [False] * 5,
        }
    )
    d = FC.c1_frame(S, _r02())
    assert np.allclose(d.loc[d.pair_id == "a", "q_J"], np.array([0.5, 0.25, 0.125]) / 0.875)
    assert np.allclose(d["p_hat_J"], d["EVe_J"] / d["O_J"]) and np.allclose(
        d["h_min"], [30.0] * 3 + [600.0] * 2
    )


# ------------------------------------------------------------------ T009 scoring


def test_check_window_refuses_fit_period_and_inverted():
    with pytest.raises(ValueError, match="fit period"):
        SC.check_window("2026-10-04", "2026-10-11")
    with pytest.raises(ValueError, match="fit period"):
        SC.check_window("2026-09-01", "2026-10-11")
    with pytest.raises(ValueError, match="after"):
        SC.check_window("2026-10-12", "2026-10-11")
    assert SC.check_window("2026-10-05", "2026-10-11") == (
        dt.date(2026, 10, 5),
        dt.date(2026, 10, 11),
    )


def _synthetic_db(
    provenance_for=("r1",), dead_heat_race=None, drop_pred_row=None, null_odds_race=None
):
    post = pd.Timestamp("2026-10-10 05:00", tz="UTC")
    races = {"r1": "2026-10-10", "r2": "2026-10-10", "r3": "2026-10-11"}
    preds, started, results, pay, prov = [], [], [], [], []
    for rid, day in races.items():
        for i in range(4):
            hid = f"{rid}h{i}"
            if drop_pred_row == (rid, i):
                pass
            else:
                preds.append(
                    {
                        "race_id": rid,
                        "horse_id": hid,
                        "horse_number": i + 1,
                        "odds_used": None
                        if (null_odds_race == rid and i == 0)
                        else [2.0, 4.0, 8.0, 16.0][i],
                        "odds_observed_at": post
                        - pd.Timedelta(minutes=[5, 5, 5, 5][i] + 40 * (rid == "r3")),
                        "computed_at": post,
                        "post_time": post,
                        "race_date": day,
                        "stored_win_prob": 0.25,
                        "logic_version": (
                            "mev-ens15-v1;seeds=1-15;oddsobs=quote"
                            if rid in provenance_for
                            else ("mev-ens15-v1;seeds=1-15;oddsobs=proxy" if rid == "r2" else "mev-ens15-v1;seeds=1-15")
                        ),
                    }
                )
            started.append(
                {
                    "race_id": rid,
                    "horse_id": hid,
                    "horse_number": i + 1,
                    "odds": [2.5, 4.0, 7.0, 20.0][i],
                }
            )
            fo = 1 if i == 0 or (dead_heat_race == rid and i == 1) else i + 1
            results.append(
                {"race_id": rid, "horse_id": hid, "finish_order": fo, "result_status": "finished"}
            )
        pay.append({"race_id": rid, "horse_number": 1, "payout_yen": 250})
        prov.append(
            {"race_id": rid, "win_odds_observed_at": post if rid in provenance_for else None}
        )
    return (
        pd.DataFrame(preds),
        pd.DataFrame(started),
        pd.DataFrame(results),
        pd.DataFrame(pay),
        pd.DataFrame(prov),
    )


def test_select_population_rules_and_dry_run():
    preds, started, results, pay, prov = _synthetic_db(provenance_for=("r3",))
    pop, excl = SC.select_population(preds, started, results, pay, prov)
    assert pop["race_id"].nunique() == 3 and all(len(v) == 0 for v in excl.values())
    # h in minutes with floor 1; band labels; provenance → day-level dry_run
    assert np.allclose(pop.loc[pop.race_id == "r1", "h_min"], 5.0)
    assert set(pop.loc[pop.race_id == "r3", "h_band"]) == {"(30,60]"}
    assert (
        pop.loc[pop.race_id == "r1", "dry_run"].all()
        and not pop.loc[pop.race_id == "r3", "dry_run"].any()
    )
    assert dict(pop.drop_duplicates("race_id").set_index("race_id")["odds_observed_source"]) == {
        "r1": "proxy",
        "r2": "proxy",
        "r3": "quote",
    }
    assert (
        pop["won"].sum() == 3
        and (pop.loc[pop.won, "payout_yen"] == 250).all()
        and pop["has_official_payout"].all()
    )
    assert (pop.loc[pop.race_id == "r1", "O_F"].to_numpy() == [2.5, 4.0, 7.0, 20.0]).all()


def test_select_population_exclusions():
    preds, started, results, pay, prov = _synthetic_db(
        dead_heat_race="r2", drop_pred_row=("r3", 2), null_odds_race="r1"
    )
    pop, excl = SC.select_population(preds, started, results, pay, prov)
    assert len(pop) == 0
    assert excl["dead_heat_or_no_winner"] == ["r2"] and excl["prediction_rows_mismatch"] == ["r3"]
    assert excl["odds_used_missing"] == ["r1"]
    # no oddsobs=quote token on any race → every race proxy, whatever races.win_odds_observed_at says
    preds, started, results, pay, prov = _synthetic_db(provenance_for=())
    prov["win_odds_observed_at"] = pd.Timestamp("2026-10-10 06:00", tz="UTC")  # final-odds write
    pop, _ = SC.select_population(preds, started, results, pay, prov)
    assert (pop["odds_observed_source"] == "proxy").all() and pop["dry_run"].all()
    assert pop["race_provenance_present_now"].all()
    # one proxy row in an otherwise quote race makes the race proxy
    preds, started, results, pay, prov = _synthetic_db(provenance_for=("r1", "r2", "r3"))
    preds.loc[(preds.race_id == "r3") & (preds.horse_number == 2), "logic_version"] = (
        "mev-ens15-v1;seeds=1-15;oddsobs=proxy"
    )
    pop, _ = SC.select_population(preds, started, results, pay, prov)
    src = dict(pop.drop_duplicates("race_id").set_index("race_id")["odds_observed_source"])
    assert src == {"r1": "quote", "r2": "quote", "r3": "proxy"}
    assert SC.odds_observed_source([None]) == "proxy" and SC.odds_observed_source([]) == "quote"
    # a race without results is excluded
    pop, excl = SC.select_population(preds, started, results[results.race_id != "r2"], pay, None)
    assert excl["no_results"] == ["r2"] and pop["race_id"].nunique() == 2


def test_select_population_excludes_a_race_with_mixed_observed_at(capsys):
    """protocol v2 (statistics-4): a race whose rows carry two observation times would be split
    across h bands and have its winner NLL normalised over a partial race — excluded, counted."""
    preds, started, results, pay, prov = _synthetic_db(provenance_for=("r1", "r2", "r3"))
    preds.loc[(preds.race_id == "r2") & (preds.horse_number == 3), "odds_observed_at"] -= (
        pd.Timedelta(minutes=50)
    )
    pop, excl = SC.select_population(preds, started, results, pay, prov)
    assert excl["odds_observed_at_mixed_within_race"] == ["r2"]
    assert set(pop["race_id"]) == {"r1", "r3"}
    assert not pop["dry_run"].any()   # the exclusion is not a provenance verdict on the day


def test_verdict_never_judges_a_diagnostic_arm():
    """harness-leak-protocol-2: a --judged-seeds partial average is recorded under 'judged' but
    the protocol's rules (ii)/(iii) are not applied to it."""
    proto = json.loads(C.PROTOCOL_JSON.read_text())
    pr = {"overall": {"nll_J": {"ci_high": -0.001, "point": -0.002, "ci_low": -0.003},
                      "calib_ratio_ev_gt1": {"arm_value": 1.01, "ref_value": 0.8}}}
    ev = {"n_days": 9, "n_races": 250,
          "arms": {"ens15": {}, "judged": {"paired_vs_ens15": pr}, "c1": {"paired_vs_ens15": pr}}}
    v = SC.verdict_block({}, ev, proto)
    assert v["verdict"] == "judgeable" and v["diagnostic_arms_not_judged"] == []
    assert v["checks_on_eligible_days"]["judged"]["rule_ii_nll_ci_high_lt_0"] is True
    v = SC.verdict_block({}, ev, proto, diagnostic_arms={"judged"})
    assert v["verdict"] == "judgeable" and v["diagnostic_arms_not_judged"] == ["judged"]
    assert "rule_ii_nll_ci_high_lt_0" not in v["checks_on_eligible_days"]["judged"]
    assert "skipped" in v["checks_on_eligible_days"]["judged"]
    assert v["checks_on_eligible_days"]["c1"]["rule_ii_nll_ci_high_lt_0"] is True


def test_metrics_and_paired_bootstrap_shape():
    preds, started, results, pay, prov = _synthetic_db(provenance_for=("r1", "r2", "r3"))
    pop, _ = SC.select_population(preds, started, results, pay, prov)
    rng = np.random.default_rng(0)
    p_ref = np.tile([0.4, 0.3, 0.2, 0.1], 3)
    p_arm = np.clip(p_ref + rng.normal(0, 0.02, len(p_ref)), 0.01, 0.99)
    m = SC.metrics(pop, p_ref)
    assert m["n"] == 12 and m["n_races"] == 3 and m["won"] == 3
    assert (
        set(m["ev_bands"]) == set(SC.EV_BAND_LABELS)
        and sum(v["n"] for v in m["ev_bands"].values()) == 12
    )
    assert m["ev_gt12"]["n_settled_official"] == m["ev_gt12"]["n"]
    days = sorted(pop["day"].unique())
    didx = pop["day"].map({d: i for i, d in enumerate(days)}).to_numpy()
    from horseracing_eval.bootstrap import block_bootstrap_counts

    counts = block_bootstrap_counts(len(days), 200, 1)
    same = SC.paired_bootstrap(didx, len(days), SC.paired_series(pop, p_ref, p_ref), counts)
    assert (
        same["nll_J"]["point"] == 0.0
        and same["nll_J"]["ci_low"] == 0.0
        and same["nll_J"]["ci_high"] == 0.0
    )
    diff = SC.paired_bootstrap(didx, len(days), SC.paired_series(pop, p_arm, p_ref), counts)
    for k in ("nll_J", "winner_nll_J", "calib_ratio_ev_gt1"):
        assert set(diff[k]) >= {"point", "ci_low", "ci_high", "arm_value", "ref_value"}
    assert diff["nll_J"]["ci_low"] <= diff["nll_J"]["point"] <= diff["nll_J"]["ci_high"]
    assert (
        abs(
            diff["nll_J"]["point"]
            - (C.nll_binary(p_arm, pop.won).mean() - C.nll_binary(p_ref, pop.won).mean())
        )
        < 1e-12
    )
    # the same draws are used for every arm: identical input → identical replicates
    again = SC.paired_bootstrap(didx, len(days), SC.paired_series(pop, p_arm, p_ref), counts)
    assert again == diff


def test_evaluate_reports_bands_days_and_h_distribution():
    preds, started, results, pay, prov = _synthetic_db(provenance_for=("r1", "r2", "r3"))
    pop, _ = SC.select_population(preds, started, results, pay, prov)
    feat = pop[["race_id", "horse_id", "odds_used", "h_min"]].copy()
    arms = {"ens15": np.tile([0.4, 0.3, 0.2, 0.1], 3), "c1": np.tile([0.5, 0.3, 0.1, 0.1], 3)}
    ev = SC.evaluate(pop, feat, arms, b=50, seed=1)
    assert ev["n_days"] == 2 and ev["n_races"] == 3 and set(ev["arms"]) == {"ens15", "c1"}
    assert set(ev["arms"]["c1"]["by_h_band"]) == set(C.H_BAND_LABELS_SCORE)
    assert (
        ev["arms"]["c1"]["by_h_band"]["(0,10]"]["n"] == 8
        and ev["arms"]["c1"]["by_h_band"]["(30,60]"]["n"] == 4
    )
    assert "paired_vs_ens15" in ev["arms"]["c1"] and "paired_vs_ens15" not in ev["arms"]["ens15"]
    assert ev["arms"]["c1"]["by_day"]["2026-10-10"]["dry_run"] is False
    assert sum(ev["h_distribution"]["by_band"].values()) == 12 and ev["bootstrap"]["B"] == 50


def test_verdict_recorded_only_below_threshold():
    proto = json.loads(C.PROTOCOL_JSON.read_text())
    v = SC.verdict_block({}, {"n_days": 2, "n_races": 35, "arms": {}}, proto)
    assert v["verdict"] == "recorded_only" and v["eligible_days"] == 2
    v2 = SC.verdict_block({}, None, proto)
    assert v2["verdict"] == "recorded_only" and v2["eligible_races"] == 0


def test_write_result_is_append_only(tmp_path):
    p = tmp_path / "2026-10-10_2026-10-11.json"
    SC.write_result(p, {"a": 1})
    with pytest.raises(FileExistsError):
        SC.write_result(p, {"a": 2})
    assert json.loads(p.read_text()) == {"a": 1}


def test_protocol_sha_is_frozen(monkeypatch, tmp_path):
    info = SC.verify_protocol()
    assert info["protocol_sha256"] == C.PROTOCOL_SHA.read_text().split()[0]
    assert info["protocol"]["judgement_rules"]["threshold"].startswith("no verdict until >= 8")
    assert info["protocol"]["version"] == "v2"
    assert any("odds_observed_at_mixed_within_race" in r for r in info["protocol"]["population"])
    assert [r["version"] for r in info["protocol"]["revisions"]] == ["v0", "v1", "v2"]
    tampered = tmp_path / "protocol.json"
    tampered.write_text(C.PROTOCOL_JSON.read_text() + "\n")
    monkeypatch.setattr(C, "PROTOCOL_JSON", tampered)
    with pytest.raises(RuntimeError, match="sha256"):
        SC.verify_protocol()


def test_frozen_artifacts_are_consistent():
    gen = json.loads(C.GENERATOR_JSON.read_text())
    assert [b["band"] for b in gen["bands"]] == list(C.H_BAND_LABELS) and 0 < gen["rho"] < 1
    assert gen["period"]["from"] >= C.FIT_PERIOD_FROM and gen["period"]["to"] <= C.FIT_PERIOD_TO
    coef = json.loads(C.COEFFICIENTS_JSON.read_text())
    for c in ("c1", "c2"):
        assert (
            set(coef[c]["coef"]) == set(FC.COEF_NAMES) and coef[c]["converged"] and coef[c]["n"] > 0
        )
    assert coef["refit_at_evaluation"] is False
