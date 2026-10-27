"""Read-only attribution of frozen143 profits; no fitting or policy selection."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / "artifacts/143-existing-feature-roi"
INPUT = OLD / "weighted_comparison"
OUT = ROOT / "artifacts/roi-weighted-attribution-20261024/primary"
KEYS = ["race_id", "horse_id"]
META = KEYS + ["horse_number", "race_date", "year", "odds", "won", "field_size"]


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load():
    paths = [INPUT / f"{arm}_{part}.parquet" for arm in ("baseline", "weighted_baseline")
             for part in ("ensemble", "bets", "races")]
    paths += [INPUT / "summary.json", OLD / "protocol.json", OLD / "protocol.sha256", Path(__file__).resolve()]
    before = {str(p.relative_to(ROOT)): sha(p) for p in paths}
    if sha(OLD / "protocol.json") != (OLD / "protocol.sha256").read_text().strip():
        raise ValueError("Frozen protocol differs")
    a = pd.read_parquet(INPUT / "baseline_ensemble.parquet")
    b = pd.read_parquet(INPUT / "weighted_baseline_ensemble.parquet")
    pd.testing.assert_frame_equal(a[META], b[META])
    if a.duplicated(KEYS).any() or not a.race_date.between("2019-01-01", "2026-06-26").all():
        raise ValueError("Invalid comparison population")
    d = a[META].copy()
    d["p_base"], d["p_weighted"] = a.p.to_numpy(), b.p.to_numpy()
    for arm, tag in (("baseline", "base"), ("weighted_baseline", "weighted")):
        bets = pd.read_parquet(INPUT / f"{arm}_bets.parquet")
        if bets.duplicated(KEYS).any() or not pd.MultiIndex.from_frame(bets[KEYS]).isin(pd.MultiIndex.from_frame(d[KEYS])).all():
            raise ValueError("Invalid bet keys")
        if not np.array_equal(bets.stake_yen.to_numpy(), bets.units.to_numpy()*100):
            raise ValueError("Stake unit differs")
        expected = bets.stake_yen*bets.won*bets.odds
        np.testing.assert_allclose(bets.payout_yen, expected, rtol=0, atol=1e-8)
        selected = bets[KEYS + ["units", "stake_yen", "payout_yen"]].rename(
            columns={c: f"{c}_{tag}" for c in ("units", "stake_yen", "payout_yen")})
        d = d.merge(selected, on=KEYS, how="left", validate="one_to_one", sort=False)
        for c in ("units", "stake_yen", "payout_yen"):
            d[f"{c}_{tag}"] = d[f"{c}_{tag}"].fillna(0)
        d[f"profit_{tag}"] = d[f"payout_yen_{tag}"]-d[f"stake_yen_{tag}"]
        expected_races = pd.read_parquet(INPUT / f"{arm}_races.parquet").sort_values("race_id")
        actual = d.groupby("race_id")[f"profit_{tag}"].sum().reindex(expected_races.race_id)
        np.testing.assert_allclose(actual, expected_races.profit_yen, rtol=0, atol=1e-8)
    d["delta_profit"] = d.profit_weighted-d.profit_base
    d["delta_stake"] = d.stake_yen_weighted-d.stake_yen_base
    d["delta_payout"] = d.payout_yen_weighted-d.payout_yen_base
    d["selection"] = np.select([(d.units_base > 0) & (d.units_weighted > 0), d.units_weighted > 0,
                                d.units_base > 0], ["common", "added", "removed"], default="neither")
    d["ev_base"], d["ev_weighted"] = d.p_base*d.odds, d.p_weighted*d.odds
    races = d.groupby(["year", "race_date", "race_id"], sort=True).agg(
        delta_profit=("delta_profit", "sum"), delta_stake=("delta_stake", "sum"),
        delta_payout=("delta_payout", "sum"), base_profit=("profit_base", "sum"),
        weighted_profit=("profit_weighted", "sum")).reset_index()
    for name, digest in before.items():
        if sha(ROOT / name) != digest:
            raise ValueError("Source changed during analysis")
    return d, races, before


def decompose(d):
    return d.groupby("selection", sort=True).agg(n=("race_id", "size"), delta_profit=("delta_profit", "sum"),
        delta_stake=("delta_stake", "sum"), delta_payout=("delta_payout", "sum"), wins=("won", "sum")).reset_index().to_dict("records")


def run():
    d, races, hashes = load()
    s = d[d.year == 2026]
    r = races[races.year == 2026]
    difference = float(s.delta_profit.sum())
    top = r.nlargest(1, "delta_profit").iloc[0]
    row = s[(s.race_id == top.race_id) & s.won.astype(bool)].iloc[0]
    summary = dict(analysis_date="2026-10-24", source_window_end="2026-06-21", can_adopt=False,
        untouched_holdout=False, new_training_fits=0, source_sha256=hashes,
        annual=races.groupby("year").agg(delta_profit=("delta_profit", "sum"), races=("race_id", "size"),
                     days=("race_date", "nunique")).reset_index().to_dict("records"),
        decomposition_2026=decompose(s), decomposition_all=decompose(d),
        monthly_2026=s.assign(month=s.race_date.str[:7]).groupby("month").delta_profit.sum().to_dict(),
        matched_jan1_jun21=races[races.race_date.str[5:] <= "06-21"].groupby("year").delta_profit.sum().to_dict(),
        key_race=dict(race_id=top.race_id, race_date=top.race_date, delta_profit=float(top.delta_profit),
                      winner={k: row[k].item() if isinstance(row[k], np.generic) else row[k] for k in
                              KEYS + ["horse_number", "odds", "p_base", "p_weighted", "ev_base", "ev_weighted",
                                      "units_base", "units_weighted", "delta_profit"]}),
        leave_key_race_out_2026=difference-float(top.delta_profit),
        leave_key_race_out_all=float(d.delta_profit.sum()-top.delta_profit),
        key_race_zero_payout_2026=difference-float(s[s.race_id == top.race_id].delta_payout.sum()),
        changed_races_2026=dict(improved=int((r.delta_profit > 0).sum()), worsened=int((r.delta_profit < 0).sum()),
                               unchanged=int((r.delta_profit == 0).sum())),
        diagnostics="Outcome-conditioned influence/deletion is retrospective sensitivity, never a tradable exclusion rule.")
    summary["net_2026_share"] = difference / d.delta_profit.sum()
    positive = races.groupby("year").delta_profit.sum().clip(lower=0).sum()
    summary["positive_year_gain_2026_share"] = difference/positive
    summary["same_calendar_2026_net_share"] = difference/sum(summary["matched_jan1_jun21"].values())
    summary["probability_changes"] = []
    for year, sub in d.groupby("year"):
        summary["probability_changes"].append(dict(year=int(year),
            mean_absolute_probability_change=float(abs(sub.p_weighted-sub.p_base).mean()),
            ev_cross_up=int(((sub.ev_base <= 1.2) & (sub.ev_weighted > 1.2)).sum()),
            ev_cross_down=int(((sub.ev_base > 1.2) & (sub.ev_weighted <= 1.2)).sum())))
    summary["weight_coverage"] = []
    for year in range(2019, 2027):
        receipt_path = OLD / "weighted_models/seed_01" / f"year_{year}/weighted_baseline/receipt.json"
        receipt = json.loads(receipt_path.read_text())
        spec = receipt["recipe"]["weights"]
        summary["weight_coverage"].append(dict(year=year, available=spec["informative_rows"],
            train_rows=receipt["recipe"]["train_rows"], fraction=spec["informative_rows"]/receipt["recipe"]["train_rows"],
            informative_raw_mean=spec["raw_mean"], neutral_rows=spec["neutral_rows"],
            mean=spec["mean"], minimum=spec["minimum"], maximum=spec["maximum"]))
        summary["source_sha256"][str(receipt_path.relative_to(ROOT))] = sha(receipt_path)
    summary["seed_key_race_ev"] = []
    for seed in range(1, 16):
        rec = dict(seed=seed)
        for tag, parent, arm in (("base", ROOT / "artifacts/142-roi-policy-model-improvement/models", "baseline"),
                                 ("weighted", OLD / "weighted_models", "weighted_baseline")):
            path = parent / f"seed_{seed:02d}/year_2026" / arm / "predictions.parquet"
            pred = pd.read_parquet(path, columns=KEYS + ["p", "odds"])
            selected = pred[(pred.race_id == row.race_id) & (pred.horse_id == row.horse_id)]
            if len(selected) != 1:
                raise ValueError("Key-race seed prediction missing")
            rec[tag] = float((selected.p*selected.odds).iloc[0])
            summary["source_sha256"][str(path.relative_to(ROOT))] = sha(path)
        summary["seed_key_race_ev"].append(rec)
    np.testing.assert_allclose(sum(v["delta_profit"] for v in summary["decomposition_2026"]), difference, rtol=0, atol=1e-8)
    np.testing.assert_allclose(d.delta_profit.sum(), 104930, rtol=0, atol=1e-8)
    np.testing.assert_allclose(difference, 92780, rtol=0, atol=1e-8)
    OUT.mkdir(parents=True, exist_ok=False)
    d.to_parquet(OUT / "horse_attribution.parquet", index=False)
    races.to_parquet(OUT / "race_attribution.parquet", index=False)
    with (OUT / "analysis.json").open("x") as stream:
        json.dump(summary, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"out": str(OUT), "2026_delta": difference, "key_race": summary["key_race"],
                      "all_without_key": summary["leave_key_race_out_all"]}, ensure_ascii=False))


if __name__ == "__main__":
    run()
