"""Outcome attribution only; no new models, policies, gates or threshold search."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/roi-next-proposals-20261025"
META = ["race_id", "horse_id", "horse_number", "race_date", "year", "odds", "won", "field_size"]
KEYS = ["race_id", "horse_id"]
LOCATIONS = {
    "baseline": ("143-existing-feature-roi/weighted_comparison", "baseline"),
    "weighted_baseline": ("143-existing-feature-roi/weighted_comparison", "weighted_baseline"),
    "pace": ("143-existing-feature-roi/features_comparison", "pace"),
    "within_race": ("144-race-normalized-weight/comparison", "within_race"),
    "race_only": ("144-race-normalized-weight/comparison", "race_only"),
    "weighted_pace": ("145-pace-weight-combination/comparison", "weighted_pace"),
    "weighted_backfill": ("146-old-oof-backfill/comparison", "weighted_backfill"),
}
CONTRASTS = [(a, b) for a in ("within_race", "weighted_pace", "weighted_backfill")
             for b in ("baseline", "weighted_baseline")] + [("weighted_pace", "pace")]


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def amounts(pred):
    """Reproduce only the already frozen EV1.2/equal-budget policy."""
    ev = pred.p.to_numpy() * pred.odds.to_numpy()
    ranked = pred.loc[ev > 1.2, KEYS + ["horse_number"]].copy()
    ranked["ev"] = ev[ev > 1.2]
    ranked = ranked.sort_values(["race_id", "ev", "horse_number", "horse_id"], ascending=[True, False, True, True], kind="stable")
    ranked = ranked.groupby("race_id", sort=False).head(10)
    units = np.zeros(len(pred), dtype=int)
    units[ranked.index] = (10 // ranked.groupby("race_id").horse_id.transform("size")).to_numpy(int)
    return units * 100


def verify_receipts(summary_paths):
    checks = []
    for feature in (143, 144, 145, 146):
        names = {143: "existing-feature-roi", 144: "race-normalized-weight", 145: "pace-weight-combination", 146: "old-oof-backfill"}
        parent = ROOT / f"specs/{feature}-{names[feature]}/evidence"
        audit = parent / ("independent_weighted.json" if feature == 143 else "independent_final.json")
        data = json.loads(audit.read_text())
        if feature != 143:
            target = next(p for p in summary_paths if p.parts[-3] == f"{feature}-{names[feature]}")
            assert data["summary_sha256"] == sha(target)
        checks.append(dict(feature=feature, evidence=str(audit.relative_to(ROOT)), sha256=sha(audit)))
    return checks


def describe(values):
    return dict(mean=float(np.mean(values)), median=float(np.median(values)),
        p90=float(np.quantile(values, .9)), p99=float(np.quantile(values, .99)))


def attribution(a, b, data):
    new, old = data[a], data[b]
    stake_new, stake_old = new.stake.to_numpy(), old.stake.to_numpy()
    hit_return = new.won.to_numpy(float) * new.odds.to_numpy(float) - 1
    delta = (stake_new - stake_old) * hit_return
    new_bought, old_bought = stake_new > 0, stake_old > 0
    groups = np.select([new_bought & ~old_bought, old_bought & ~new_bought,
        new_bought & old_bought & (stake_new != stake_old), new_bought & old_bought],
        ["added", "removed", "common_stake_changed", "common_same_stake"], default="neither")
    frame = new[META].copy()
    frame["stake_new"], frame["stake_old"], frame["delta_profit"], frame["group"] = stake_new, stake_old, delta, groups
    records = []
    for label, sub in frame.groupby("group", sort=True):
        pn = float((sub.stake_new * (sub.won*sub.odds-1)).sum())
        po = float((sub.stake_old * (sub.won*sub.odds-1)).sum())
        sn, so = float(sub.stake_new.sum()), float(sub.stake_old.sum())
        records.append(dict(group=label, n=len(sub), wins=int(sub.won.sum()),
            stake_new=sn, stake_old=so, profit_new=pn, profit_old=po, delta_profit=pn-po,
            roi_new=(pn+sn)/sn if sn else None, roi_old=(po+so)/so if so else None))
    annual = frame.groupby("year").delta_profit.sum().to_dict()
    expected = float(new.profit.sum()-old.profit.sum())
    np.testing.assert_allclose(sum(r["delta_profit"] for r in records), expected, atol=1e-8, rtol=0)
    absolute = np.abs(new.p.to_numpy()-old.p.to_numpy())
    old_ev, new_ev = old.p.to_numpy()*old.odds.to_numpy(), new.p.to_numpy()*new.odds.to_numpy()
    crossed = (old_ev > 1.2) != (new_ev > 1.2)
    return dict(total_delta_yen=expected, annual=annual, groups=records,
        probability_absolute_change=describe(absolute), ev_crossings=int(crossed.sum()),
        crossing_old_ev_within_0_1_of_threshold=int((crossed & (np.abs(old_ev-1.2) <= .1)).sum()),
        before2026_delta_yen=float(frame.loc[frame.year.lt(2026), "delta_profit"].sum()),
        without_known_race_delta_yen=float(frame.loc[frame.race_id.ne("202608030111"), "delta_profit"].sum()))


def run():
    paths = {ROOT / "docs/roi-next-proposals-20261025/analyze.py"}
    for directory, arm in LOCATIONS.values():
        paths.update(ROOT / "artifacts" / directory / f"{arm}_{part}.parquet" for part in ("ensemble", "bets", "races"))
        paths.add(ROOT / "artifacts" / directory / "summary.json")
    hashes = {str(p.relative_to(ROOT)): sha(p) for p in sorted(paths)}
    checks = verify_receipts([p for p in paths if p.name == "summary.json"])
    frames, summary = {}, {}
    for tag, (directory, arm) in LOCATIONS.items():
        folder = ROOT / "artifacts" / directory
        pred = pd.read_parquet(folder / f"{arm}_ensemble.parquet").reset_index(drop=True)
        if frames:
            pd.testing.assert_frame_equal(frames["baseline"][META], pred[META], check_exact=True)
        assert len(pred) == 343883 and pred.race_id.nunique() == 24855
        stake = amounts(pred)
        bets = pd.read_parquet(folder / f"{arm}_bets.parquet").set_index(KEYS)
        index = pd.MultiIndex.from_frame(pred[KEYS])
        saved = bets.stake_yen.reindex(index, fill_value=0).to_numpy()
        np.testing.assert_array_equal(stake, saved)
        frame = pred[META + ["p"]].copy()
        frame["stake"] = stake
        frame["profit"] = stake * (pred.won.to_numpy(float)*pred.odds.to_numpy()-1)
        policy = json.loads((folder / "summary.json").read_text())["arms"][arm]["policy"]
        np.testing.assert_allclose(frame.profit.sum(), policy["profit_yen"], atol=1e-8, rtol=0)
        np.testing.assert_allclose(stake.sum(), policy["stake_yen"], atol=1e-8, rtol=0)
        frames[tag], summary[tag] = frame, policy
    contrasts = {f"{a}_minus_{b}": attribution(a, b, frames) for a, b in CONTRASTS}
    result = dict(analysis_date="2026-10-25", new_fits=0, new_policies_scored=0,
        scope="Descriptive attribution of already frozen selections; not a new gate or profit confirmation",
        summaries=summary, contrasts=contrasts, independent_audit_sources=checks,
        can_adopt=False, untouched_holdout=False, source_sha256=hashes)
    for name, digest in hashes.items():
        assert sha(ROOT / name) == digest
    OUT.mkdir(parents=True, exist_ok=False)
    (OUT / "attribution.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False)+"\n")
    print(json.dumps(dict(out=str(OUT), contrasts={k:v["total_delta_yen"] for k,v in contrasts.items()}), ensure_ascii=False))


if __name__ == "__main__":
    run()
