"""142: frozen retrospective policies with a common per-race cash budget.

Selection sees keys, probabilities and closing odds only. Historical closing odds
are an explicitly separate settlement proxy from the optional official-payout subset.
Nothing here changes S1..S5 or production models.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

MAX_DATE = "2026-06-26"
BUDGET_YEN = 1000
UNIT_YEN = 100
POLICIES = ("raw_ev_gt_1_2", "mix_ev_gt_1_0", "mix_ev_gt_1_2",
            "raw_gt_1_2_and_mix_gt_1_0", "market_favorite_reference")
DECISION_COLUMNS = ("race_id", "horse_id", "horse_number", "odds", "p_raw", "p_mix")


def _decisions(frame: pd.DataFrame) -> pd.DataFrame:
    missing = set(DECISION_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing decision columns: {sorted(missing)}")
    d = frame.loc[:, DECISION_COLUMNS].copy().reset_index(drop=True)
    if d.empty:
        raise ValueError("Empty race population")
    for col in ("race_id", "horse_id"):
        if d[col].isna().any() or d[col].astype(str).str.strip().eq("").any():
            raise ValueError(f"Invalid {col}")
        d[col] = d[col].astype(str)
    if d.duplicated(["race_id", "horse_id"]).any():
        raise ValueError("Duplicate race/horse keys")
    numeric = d[["horse_number", "odds", "p_raw", "p_mix"]].to_numpy(float)
    if not np.isfinite(numeric).all():
        raise ValueError("Incomplete/non-finite decision inputs")
    hn = d.horse_number.to_numpy(float)
    if np.any(hn < 1) or np.any(hn != np.floor(hn)):
        raise ValueError("Invalid horse numbers")
    d["horse_number"] = hn.astype(int)
    if d.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("Duplicate horse numbers")
    if (d.odds.astype(float) < 1).any():
        raise ValueError("Odds must be >= 1")
    for col in ("p_raw", "p_mix"):
        p = d[col].to_numpy(float)
        if np.any((p <= 0) | (p >= 1)):
            raise ValueError(f"Invalid {col}")
        d[col] = p
    if (d.groupby("race_id").size() < 2).any():
        raise ValueError("Incomplete race field")
    if not np.allclose(d.groupby("race_id").p_mix.sum(), 1., atol=1e-8, rtol=0):
        raise ValueError("p_mix must sum to one for every complete race")
    d["odds"] = d.odds.astype(float)
    return d


def allocate(frame: pd.DataFrame, policy: str) -> pd.DataFrame:
    """Return every input horse, with outcome-free score and integer stakes.

    At most ten candidates share ten 100-yen units. The remainder stays in cash;
    neither future race counts nor outcomes can change a race's allocation.
    """
    if policy not in POLICIES:
        raise ValueError(f"Unknown frozen policy: {policy}")
    d = _decisions(frame)
    raw = d.p_raw.to_numpy() * d.odds.to_numpy()
    mix = d.p_mix.to_numpy() * d.odds.to_numpy()
    if policy == "raw_ev_gt_1_2":
        mask, score = raw > 1.2, raw
    elif policy == "mix_ev_gt_1_0":
        mask, score = mix > 1., mix
    elif policy == "mix_ev_gt_1_2":
        mask, score = mix > 1.2, mix
    elif policy == "raw_gt_1_2_and_mix_gt_1_0":
        mask, score = (raw > 1.2) & (mix > 1.), mix
    else:
        mask, score = np.ones(len(d), bool), -d.odds.to_numpy()
    d["score"] = score
    d["units"] = 0
    ranked = d.loc[mask].sort_values(
        ["race_id", "score", "horse_number", "horse_id"],
        ascending=[True, False, True, True], kind="stable")
    cap = 1 if policy == "market_favorite_reference" else 10
    ranked = ranked.groupby("race_id", sort=False).head(cap)
    if len(ranked):
        sizes = ranked.groupby("race_id").horse_id.transform("size")
        d.loc[ranked.index, "units"] = (10 // sizes).to_numpy(int)
    d["stake_yen"] = d.units.astype(int) * UNIT_YEN
    return d


def _validate(frame: pd.DataFrame) -> pd.DataFrame:
    _decisions(frame)
    required = {"race_date", "year", "won"}
    if not required <= set(frame):
        raise ValueError(f"Missing evaluation columns: {sorted(required - set(frame))}")
    d = frame.copy().reset_index(drop=True)
    for col in ("race_id", "horse_id"):
        d[col] = d[col].astype(str)
    dates = pd.to_datetime(d.race_date, errors="raise")
    if dates.isna().any() or (dates.dt.strftime("%Y-%m-%d") > MAX_DATE).any():
        raise ValueError(f"Evaluation date must be <= {MAX_DATE}")
    d["race_date"] = dates.dt.strftime("%Y-%m-%d")
    years = pd.to_numeric(d.year, errors="raise").to_numpy(float)
    if not np.isfinite(years).all() or not np.array_equal(years, dates.dt.year.to_numpy()):
        raise ValueError("Year/date mismatch")
    if (dates.dt.year < 2007).any():
        raise ValueError("Pre-2007 data is outside the product population")
    if (d.groupby("race_id").race_date.nunique() != 1).any():
        raise ValueError("One race has multiple dates")
    won = d.won.to_numpy(float)
    if not np.isfinite(won).all() or not np.isin(won, [0., 1.]).all():
        raise ValueError("Invalid win labels")
    d["won"] = won.astype(int)
    if (d.groupby("race_id").won.sum() != 1).any():
        raise ValueError("A complete race must have exactly one winner")
    if "payout_yen_per_100" in d:
        pay = pd.to_numeric(d.payout_yen_per_100, errors="raise").to_numpy(float)
        finite = np.isfinite(pay)
        if np.isinf(pay).any() or np.any(pay[finite] < 0):
            raise ValueError("Invalid official payout")
        if np.any((won == 0) & finite & (pay != 0)) or np.any((won == 1) & finite & (pay <= 0)):
            raise ValueError("Official payout disagrees with winner identity")
        d["payout_yen_per_100"] = pay
    return d.sort_values(["race_date", "race_id", "horse_number", "horse_id"], kind="stable").reset_index(drop=True)


def _ratio(numerator, denominator):
    return float(numerator / denominator) if denominator > 0 else None


def _point(races: pd.DataFrame, bets: pd.DataFrame) -> dict:
    stake, payout, budget = (float(races[c].sum()) for c in ("stake_yen", "payout_yen", "budget_yen"))
    profit = payout - stake
    wealth = np.r_[0., races.profit_yen.cumsum().to_numpy()]
    dd = np.maximum.accumulate(wealth) - wealth
    losses = best = 0
    for net in races.loc[races.stake_yen > 0, "profit_yen"]:
        losses = losses + 1 if net < 0 else 0
        best = max(best, losses)
    payouts = np.sort(bets.payout_yen.to_numpy(float))[::-1]
    top1, top5 = float(payouts[:1].sum()), float(payouts[:5].sum())
    return {
        "n_races": int(len(races)), "n_days": int(races.race_date.nunique()),
        "n_betting_races": int((races.stake_yen > 0).sum()), "n_bets": int(len(bets)),
        "n_hits": int(bets.won.sum()), "n_hit_races": int((races.payout_yen > 0).sum()),
        "skip_rate": float((races.stake_yen == 0).mean()) if len(races) else None,
        "stake_yen": stake, "payout_yen": payout, "profit_yen": profit,
        "budget_yen": budget, "unspent_cash_yen": budget - stake,
        "budget_return": _ratio(profit, budget), "deployed_roi": _ratio(payout, stake),
        "max_race_settlement_drawdown_yen": float(dd.max()), "max_consecutive_losing_races": int(best),
        "profit_without_top1_payout_yen": profit - top1,
        "profit_without_top5_payouts_yen": profit - top5,
        "budget_return_without_top1_payout": _ratio(profit - top1, budget),
        "budget_return_without_top5_payouts": _ratio(profit - top5, budget),
        "top1_payout_share": _ratio(top1, payout), "top5_payout_share": _ratio(top5, payout),
    }


def _interval(values, alpha):
    good = np.asarray(values)[np.isfinite(values)]
    return np.quantile(good, [alpha / 2, 1 - alpha / 2]).tolist() if len(good) else None


def _bootstrap(days: pd.DataFrame, points: dict, repetitions: int, seed: int) -> dict:
    pairs = list(combinations(POLICIES, 2))
    calendar = sorted(days.race_date.unique())
    arrays = {c: days.pivot(index="race_date", columns="policy", values=c).reindex(
        index=calendar, columns=POLICIES).to_numpy(float) for c in ("stake_yen", "payout_yen", "profit_yen", "budget_yen")}
    if any(not np.isfinite(v).all() for v in arrays.values()):
        raise ValueError("Policy populations do not share the complete race calendar")
    draws_budget, draws_roi = [], []
    rng = np.random.default_rng(seed)
    for start in range(0, repetitions, 200):
        counts = rng.multinomial(len(calendar), np.full(len(calendar), 1 / len(calendar)), size=min(200, repetitions - start))
        stake, pay = counts @ arrays["stake_yen"], counts @ arrays["payout_yen"]
        den = counts @ arrays["budget_yen"]
        draws_budget.append((counts @ arrays["profit_yen"]) / den)
        draws_roi.append(np.divide(pay, stake, out=np.full_like(pay, np.nan), where=stake > 0))
    br, roi = np.vstack(draws_budget), np.vstack(draws_roi)
    out = {}
    for a, b in pairs:
        ai, bi = POLICIES.index(a), POLICIES.index(b)
        rec = {}
        for name, values, key in (("profit_per_budget", br, "budget_return"), ("deployed_roi", roi, "deployed_roi")):
            delta = values[:, ai] - values[:, bi]
            pa, pb = points[a][key], points[b][key]
            rec[name] = {"difference": pa - pb if pa is not None and pb is not None else None,
                         "ci95": _interval(delta, .05), "bonferroni_familywise95_ci": _interval(delta, .05 / len(pairs)),
                         "valid_replicates": int(np.isfinite(delta).sum()),
                         "zero_denominator_replicates": int((~np.isfinite(delta)).sum())}
        out[f"{a}_minus_{b}"] = rec
    return {"repetitions": repetitions, "seed": seed, "unit": "all eligible race days; identical draws for all policies",
            "family_size_per_metric": len(pairs), "adjustment": "Bonferroni, separately for each metric; exploratory reused history",
            "comparisons": out}


def _evaluate_subset(d: pd.DataFrame, settlement: str, repetitions: int, seed: int) -> dict:
    race_tables, bet_tables = [], []
    for policy in POLICIES:
        allocation = allocate(d.loc[:, DECISION_COLUMNS], policy)
        # Position is preserved by allocate; labels are introduced only AFTER selection.
        allocation["race_date"] = d.race_date.to_numpy()
        allocation["won"] = d.won.to_numpy()
        per100 = (d.won.to_numpy(float) * d.odds.to_numpy(float) * 100 if settlement == "closing_odds_proxy"
                  else d.payout_yen_per_100.to_numpy(float))
        allocation["payout_yen"] = allocation.units.to_numpy() * per100
        allocation["policy"] = policy
        race = allocation.groupby(["race_date", "race_id"], sort=True).agg(
            stake_yen=("stake_yen", "sum"), payout_yen=("payout_yen", "sum")).reset_index()
        race["budget_yen"] = BUDGET_YEN
        race["unspent_cash_yen"] = BUDGET_YEN - race.stake_yen
        race["profit_yen"] = race.payout_yen - race.stake_yen
        race["policy"] = policy
        race_tables.append(race)
        bet_tables.append(allocation.loc[allocation.stake_yen > 0].copy())
    races = pd.concat(race_tables, ignore_index=True)
    bets = pd.concat(bet_tables, ignore_index=True)
    days = races.groupby(["race_date", "policy"], sort=True)[
        ["stake_yen", "payout_yen", "budget_yen", "unspent_cash_yen", "profit_yen"]].sum().reset_index()
    points = {p: _point(races[races.policy == p], bets[bets.policy == p]) for p in POLICIES}
    summary = {"settlement": settlement, "n_races": int(d.race_id.nunique()), "n_days": int(d.race_date.nunique()),
               "date_min": d.race_date.min(), "date_max": d.race_date.max(), "policies": points,
               "bootstrap": _bootstrap(days, points, repetitions, seed)}
    return {"summary": summary, "races": races, "days": days, "bets": bets}


def evaluate(frame: pd.DataFrame, bootstrap_repetitions: int = 4000, seed: int = 20261019) -> dict:
    """Score frozen policies; return JSON summary and race/day/bet audit tables."""
    if not isinstance(bootstrap_repetitions, int) or bootstrap_repetitions < 1:
        raise ValueError("bootstrap_repetitions must be a positive integer")
    d = _validate(frame)
    result = _evaluate_subset(d, "closing_odds_proxy", bootstrap_repetitions, seed)
    result["summary"].update(
        can_adopt=False, untouched_holdout=False, budget_yen_per_race=BUDGET_YEN,
        cash_rule="At most 1000 yen per complete race; 100-yen units, equal floor(10/n); remainder and skipped-race budget stay in cash",
        selection_order={"raw_ev_gt_1_2": "raw EV descending", "mix_ev_gt_1_0": "mix EV descending",
                         "mix_ev_gt_1_2": "mix EV descending", "raw_gt_1_2_and_mix_gt_1_0": "mix EV descending",
                         "market_favorite_reference": "odds ascending; one horse",
                         "tie_break": "horse_number ascending, horse_id ascending"},
        drawdown_settlement_order="race_date, race_id; within-day race_id order is a proxy, not verified post-time order across venues",
        inference_limit="Retrospective closing-odds research; not decision-time return or independent confirmation")
    result["official_subset"] = None
    all_races = int(d.race_id.nunique())
    if "payout_yen_per_100" in d:
        complete = d.groupby("race_id").payout_yen_per_100.transform(lambda x: np.isfinite(x.to_numpy(float)).all())
        official = d.loc[complete].reset_index(drop=True)
        if len(official):
            result["official_subset"] = _evaluate_subset(official, "official_payout", bootstrap_repetitions, seed)
            result["official_subset"]["summary"].update(can_adopt=False, untouched_holdout=False)
        n_official = int(official.race_id.nunique())
    else:
        n_official = 0
    result["summary"]["official_payout_coverage"] = {
        "eligible_races": n_official, "excluded_missing_races": all_races - n_official,
        "rule": "Exclude the whole race if any official payout row is missing; never treat missing as zero"}
    return result


def _write_tables(directory: Path, result: dict):
    (directory / "summary.json").write_text(json.dumps(result["summary"], ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    for key in ("races", "days", "bets"):
        result[key].to_parquet(directory / f"{key}.parquet", index=False)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--bootstrap-repetitions", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=20261019)
    args = ap.parse_args(argv)
    if args.out.exists():
        raise SystemExit("Refusing to overwrite an existing output directory")
    digest = hashlib.sha256(args.input.read_bytes()).hexdigest()
    result = evaluate(pd.read_parquet(args.input), args.bootstrap_repetitions, args.seed)
    if hashlib.sha256(args.input.read_bytes()).hexdigest() != digest:
        raise RuntimeError("Input changed during evaluation")
    args.out.mkdir(parents=True, exist_ok=False)
    result["summary"]["input_sha256"] = digest
    _write_tables(args.out, result)
    if result["official_subset"] is not None:
        official_dir = args.out / "official_subset"
        official_dir.mkdir()
        _write_tables(official_dir, result["official_subset"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
