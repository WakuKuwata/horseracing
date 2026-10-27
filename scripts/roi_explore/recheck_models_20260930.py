"""Frozen, retrospective policy comparison on existing annual out-of-sample predictions.

No fitting, production writes, or claim of an untouched holdout. Run with training/.venv/bin/python.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/roi_recheck_20260930/model_replay"
BASE = ROOT / "artifacts/roi_explore/results"
SOURCES = {
    "regression_1986": "armC_regression",
    "binary_1986": "armC_binary",
    "binary_1986_serving": "armC_binary_drop-sameday+weightlive_serving_v1",
    "binary_2007_v2": "armC_binary_drop-sameday+weightlive_serving_v2_2007",
}
PERIODS = {
    "all_available": (2000, 2026), "common_2010plus": (2010, 2026),
    "discovery": (2010, 2018), "validation": (2019, 2022),
    "later": (2023, 2026), "legacy_confirmation": (2019, 2026),
    "recent": (2025, 2026),
}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def policies() -> list[dict]:
    return [dict(policy_id=f"er>{t:g}_cap{cap or 'none'}_{selection}",
                 expected_return_gt=t, odds_lt=cap, selection=selection,
                 stake_yen=100, primary=t == 1.2 and cap in (None, 21) and selection == "all")
            for t, cap, selection in itertools.product(
                (1.0, 1.05, 1.1, 1.2, 1.3, 1.5), (None, 10, 21, 50), ("all", "top1"))]


def select(frame: pd.DataFrame, policy: dict) -> np.ndarray:
    """Selection receives only race/horse identity, odds, and OOS model output."""
    expected = 1.0 + frame["pred"].to_numpy() / 100.0
    mask = expected > policy["expected_return_gt"]
    if policy["odds_lt"] is not None:
        mask &= frame["odds"].to_numpy() < policy["odds_lt"]
    if policy["selection"] == "top1":
        ranked = frame.loc[mask, ["race_id", "horse_id", "pred"]].sort_values(
            ["race_id", "pred", "horse_id"], ascending=[True, False, True], kind="stable")
        keep = ranked.drop_duplicates("race_id").index
        mask[:] = False
        mask[keep] = True
    return mask


def metrics(bets: pd.DataFrame, eligible_races: int) -> dict:
    if bets.empty:
        return {"n_bets": 0, "roi": None, "n_races": 0, "skip_rate": 1.0}
    payout = bets["ret"].to_numpy(dtype=float) + 100.0
    stake = len(bets) * 100.0
    by_day = pd.DataFrame({"day": bets["race_date"], "net": payout - 100.0}).groupby("day")["net"].sum().sort_index()
    wealth = np.r_[0.0, by_day.cumsum().to_numpy()]
    dd = np.maximum.accumulate(wealth) - wealth
    race_net = pd.DataFrame({"race_id": bets["race_id"], "net": payout - 100.0}).groupby("race_id")["net"].sum()
    n_races = bets.race_id.nunique()
    return dict(n_bets=len(bets), n_races=n_races, n_days=bets.race_date.nunique(),
                n_hits=int((payout > 0).sum()), stake_yen=stake, payout_yen=float(payout.sum()),
                profit_yen=float(payout.sum() - stake), roi=float(payout.sum() / stake),
                hit_rate=float((payout > 0).mean()), skip_rate=1 - n_races / eligible_races,
                max_daily_drawdown_yen=float(dd.max()),
                largest_hit_share=float(payout.max() / payout.sum()) if payout.sum() else None,
                roi_without_largest_hit=float((payout.sum() - payout.max()) / stake),
                roi_with_5pct_payout_reduction=float(payout.sum() * .95 / stake),
                positive_race_fraction=float((race_net > 0).mean()))


def bootstrap(bets: pd.DataFrame, repetitions: int = 4000) -> dict | None:
    if bets.empty:
        return None
    daily = bets.assign(pay=bets.ret + 100).groupby("race_date").agg(n=("ret", "size"), pay=("pay", "sum"))
    s = daily.n.to_numpy() * 100.; p = daily.pay.to_numpy(); n = len(daily)
    rng = np.random.default_rng(20260930)
    samples = np.empty(repetitions)
    for start in range(0, repetitions, 200):
        k = min(200, repetitions - start)
        ix = rng.integers(0, n, size=(k, n))
        samples[start:start+k] = p[ix].sum(axis=1) / s[ix].sum(axis=1)
    return {"lo": float(np.quantile(samples, .025)), "hi": float(np.quantile(samples, .975)),
            "repetitions": repetitions, "unit": "betting_day", "selection_adjusted": False}


def qualifies(stats: dict) -> bool:
    return bool(stats["n_bets"] >= 200 and stats.get("n_hits", 0) >= 20
                and stats.get("n_days", 0) >= 100 and stats.get("roi_without_largest_hit", 0) > 1.)


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=lambda x: x.item() if hasattr(x, "item") else str(x)) + "\n")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    resume = "--resume-schema-fix" in sys.argv
    if (OUT / "freeze.json").exists() and not resume:
        raise SystemExit("Refusing to overwrite a prior run; inspect or choose a new output directory.")
    if (OUT / "scores.json").exists():
        raise SystemExit("Completed results already exist; refusing to overwrite.")
    paths = {k: BASE / v / "predictions.parquet" for k, v in SOURCES.items()}
    pats = policies()
    freeze = dict(created_at=datetime.now(timezone.utc).isoformat(),
                  research_only=True, untouched_holdout=False, num_policies_per_model=len(pats),
                  total_comparisons=len(pats)*len(paths), policies=pats, periods=PERIODS,
                  selection="discovery and validation: >=200 bets, >=20 hits, >=100 betting days, ROI removing largest hit >1",
                  input_sha256={str(p.relative_to(ROOT)): sha(p) for p in paths.values()},
                  source_sha256=sha(Path(__file__)),
                  note="Existing result reports already read. New thresholds fixed before this replay; retrospective sensitivity, not fresh confirmation.",
                  settlement="Original saved ret+100: winner indicator times saved odds times 100; not certified official payout.")
    if resume:
        original = json.loads((OUT / "freeze.json").read_text())
        assert original["policies"] == freeze["policies"]
        assert original["input_sha256"] == freeze["input_sha256"]
        write_json(OUT / "schema_fix_receipt.json", dict(
            created_at=freeze["created_at"], source_sha256=freeze["source_sha256"],
            added_race_calendar_sha256=sha(ROOT / "artifacts/roi_explore/rows.parquet"),
            reason="Initial run failed before scoring: old regression artifact has no race_date. Join race calendar by race_id, with many-to-one validation. Rules and prediction inputs unchanged."))
    else:
        freeze["race_calendar_sha256"] = sha(ROOT / "artifacts/roi_explore/rows.parquet")
        write_json(OUT / "freeze.json", freeze)
    rows = []
    input_audit = {}
    for model, path in paths.items():
        d = pd.read_parquet(path)
        if "race_date" not in d:
            calendar = pd.read_parquet(ROOT / "artifacts/roi_explore/rows.parquet", columns=["race_id", "race_date"]).drop_duplicates()
            d = d.merge(calendar, on="race_id", validate="many_to_one", how="left")
        assert d.race_date.notna().all()
        d["race_date"] = pd.to_datetime(d.race_date).dt.strftime("%Y-%m-%d")
        d = d.sort_values(["race_date", "race_id", "horse_id"]).reset_index(drop=True)
        assert d[["race_id", "horse_id"]].duplicated().sum() == 0
        assert np.isfinite(d[["pred", "ret", "odds"]].to_numpy()).all()
        assert (d.odds >= 1).all() and (d.ret >= -100).all()
        same_day_dupes = d.duplicated(["horse_id", "race_date"], keep=False)
        affected = d.loc[same_day_dupes, "race_id"].unique()
        input_audit[model] = dict(rows=len(d), races=d.race_id.nunique(),
                                 date_min=str(d.race_date.min()), date_max=str(d.race_date.max()),
                                 same_horse_same_day_rows=int(same_day_dupes.sum()), affected_races=affected.tolist())
        decision = d[["race_id", "horse_id", "pred", "odds"]]
        for policy in pats:
            mask = select(decision, policy)
            rec = dict(model=model, **policy, periods={})
            for period, (start, end) in PERIODS.items():
                window = d.year.between(start, end)
                bets = d.loc[mask & window]
                rec["periods"][period] = metrics(bets, d.loc[window, "race_id"].nunique())
                if policy["primary"]:
                    rec["periods"][period]["ci95"] = bootstrap(bets)
            rec["screen_pass"] = all(qualifies(rec["periods"][p]) for p in ("discovery", "validation"))
            if rec["screen_pass"]:
                later = d.loc[mask & d.year.between(*PERIODS["later"])]
                rec["periods"]["later"]["ci95"] = bootstrap(later)
            if policy["primary"]:
                bets = d.loc[mask]
                rec["yearly"] = {str(y): metrics(g, d.loc[d.year == y, "race_id"].nunique()) for y, g in bets.groupby("year")}
                rec["excluding_duplicate_horse_day_races"] = metrics(
                    d.loc[mask & ~d.race_id.isin(affected)], d.loc[~d.race_id.isin(affected), "race_id"].nunique())
                bets.to_parquet(OUT / f"bets_{model}_{policy['policy_id']}.parquet", index=False)
            rows.append(rec)
        print(f"scored {model}: {len(pats)} policies", flush=True)
    write_json(OUT / "scores.json", rows)
    write_json(OUT / "input_audit.json", input_audit)
    primaries = [r for r in rows if r["primary"]]
    shortlisted = [r for r in rows if r["screen_pass"]]
    write_json(OUT / "summary.json", dict(total_comparisons=len(rows), primary=primaries,
                                         shortlisted=shortlisted, profit_proven=False,
                                         reason="Reused historical periods and non-certified settlement; CI is exploratory, no multiplicity-adjusted profitability claim."))
    flat = []
    for r in rows:
        for period, stats in r["periods"].items():
            flat.append(dict(model=r["model"], policy_id=r["policy_id"], period=period, screen_pass=r["screen_pass"],
                             **{k:v for k,v in stats.items() if k != "ci95"}))
    pd.DataFrame(flat).to_csv(OUT / "scores.csv", index=False)
    assert all(sha(ROOT / p) == h for p, h in freeze["input_sha256"].items())
    print(f"complete: {len(rows)} comparisons, {len(shortlisted)} D/V survivors; {OUT}", flush=True)


if __name__ == "__main__":
    main()
