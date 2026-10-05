"""R02 post-hoc descriptive extras (added AFTER seeing results.json; not pre-registered, no decisions).

  1. winners' O_F/O_J median and aggregate by time band (reconciles 'winners shorten' with H_real > 1)
  2. where S1/S3 judged picks land at final (final odds band, EV_F bins)
  3. retention and rho split by judged source (CHAOS vs STORED_PRE)
  4. g calibration check on the paired sample: mean g over all horses vs realized parent ROI
  5. model odds-elasticity of p_hat implied by J->F for judged picks

    cd training && uv run python ../scripts/roi_explore/missed_20261004/r02_posthoc.py
"""
from __future__ import annotations

import json
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import r02_drift as R  # noqa: E402

OUT = R.OUT


def main() -> None:
    ph = pd.read_parquet(OUT / "pairs.parquet")
    res = json.loads((OUT / "results.json").read_text())
    out: dict = {"note": "post-hoc, descriptive only (added after seeing results.json)"}
    nd = ~ph.dead_heat.astype(bool)
    w = ph[ph.won & nd]
    out["winners_ratio_by_tb"] = {
        t: {"n": int((w.tb == t).sum()), "median_OF_over_OJ": R.r6(np.median((w.O_F / w.O_J)[w.tb == t])),
            "share_shortened": R.r6(((w.O_F < w.O_J)[w.tb == t]).mean()),
            "aggregate_sum_settle_over_sum_OJ": R.r6(w.O_settle[w.tb == t].sum() / w.O_J[w.tb == t].sum())}
        for t in R.TB}
    out["losers_mean_d_by_tb"] = {t: R.r6(ph.d[(~ph.won) & (ph.tb == t)].mean()) for t in R.TB}
    land = {}
    for r in ["S1", "S3"]:
        J = ph[ph[f"J_{r}"]]
        land[r] = {"n": int(len(J)),
                   "final_band": J.bF.value_counts().sort_index().to_dict(),
                   "EV_F_bins": pd.cut(J.EVe_F, [0, 0.8, 1.0, 1.2, 10]).value_counts().sort_index()
                   .rename(lambda x: str(x)).to_dict(),
                   "median_OF_over_OJ": R.r6(np.median(J.O_F / J.O_J))}
    out["judged_picks_at_final"] = land
    # g on the paired sample
    hist = R.load_history()
    gtab = res["g_implied_buyable_roi"]["g_tables"]["ens"]["primary_2010_2025"]["g"]
    g = np.array([np.nan if x is None else x for x in gtab])
    cells = R.cell_ids(ph.EVe_F.to_numpy(), ph.O_F.to_numpy(), ph.days_since_last.to_numpy())[0]
    m = nd.to_numpy()
    out["g_check_paired_parent"] = {"mean_g_all_horses": R.r6(np.nanmean(g[cells[m]])),
                                    "realized_parent_roi": R.r6(ph.ret[m].sum() / m.sum()), "n": int(m.sum()),
                                    "hits": int((ph.won & nd).sum())}
    # source split
    split = {}
    for r in ["S1", "S3"]:
        evc = "EVe"
        split[r] = {}
        for s in ["CHAOS", "STORED_PRE"]:
            sub = ph[ph.source == s]
            J = sub[f"J_{r}"].to_numpy()
            F = sub[f"F_{r}"].to_numpy()
            c = cells[(ph.source == s).to_numpy()]
            GJ = np.nanmean(g[c[J]]) if J.any() else np.nan
            GF = np.nanmean(g[c[F]]) if F.any() else np.nan
            split[r][s] = {"n_pairs": int(sub.pair_id.nunique()), "n_J": int(J.sum()), "n_F": int(F.sum()),
                           "n_JF": int((J & F).sum()),
                           "retention": R.r6((J & F).sum() / J.sum()) if J.any() else None,
                           "G_J": R.r6(GJ), "G_F": R.r6(GF), "rho": R.r6(GJ / GF) if J.any() and F.any() else None,
                           "median_hours": R.r6(sub.hours.median())}
    out["by_source"] = split
    # elasticity of p_hat to odds for judged picks: log(pF/pJ) / log(OF/OJ)
    el = {}
    for r in ["S1", "S3"]:
        J = ph[ph[f"J_{r}"] & (np.abs(ph.d) > 0.05)]
        lp = np.log((J.EVe_F / J.O_F) / (J.EVe_J / J.O_J))
        el[r] = {"n": int(len(J)), "median_dlogp_over_dlogO": R.r6(np.median(lp / J.d)),
                 "median_d": R.r6(np.median(J.d))}
    out["p_hat_odds_elasticity_judged_picks"] = el
    out["ev_shrink_decomposition"] = decompose(ph)
    (OUT / "posthoc.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))


OWN = ["odds", "q", "popularity", "odds_rank", "q_share_of_fav"]
RACE = ["fav_odds", "second_odds", "odds_gap12", "fav_q", "q_entropy_norm", "n_fav_under_2", "n_odds_under_10"]


def decompose(ph: pd.DataFrame) -> dict:
    """EV_F/EV_J of judged picks split into 'own price' columns vs 'race-level market structure' columns.
    Popularity is rank(min) of the version's odds in all four versions here (pairs.parquet keeps no judged
    popularity), so the J/F end points differ slightly from results.json."""
    feats = pd.read_parquet(OUT / "feats_cand.parquet").sort_values(["race_id", "horse_number"])
    sc = R.Scorer()
    base = ph[["pair_id", "race_id", "horse_id", "horse_number", "O_J", "O_F"]].merge(
        feats.drop(columns=["odds", "popularity"]), on=["race_id", "horse_id", "horse_number"], how="left")
    base = base.sort_values(["pair_id", "horse_number"]).reset_index(drop=True)
    vers = {}
    for v, col in (("J", "O_J"), ("F", "O_F")):
        x = base.copy()
        x["odds"] = x[col]
        mb = R.market_block(x[["pair_id", "horse_id", "horse_number", "odds"]], "pair_id")
        for c in R.MARKET_COLS:
            x[c] = mb[c].values
        x["popularity"] = x.groupby("pair_id")["odds"].rank(method="min")
        vers[v] = x
    hyb_own = vers["J"].copy()      # race structure judged, own price final
    for c in OWN:
        hyb_own[c] = vers["F"][c].values
    hyb_race = vers["J"].copy()     # own price judged, race structure final
    for c in RACE:
        hyb_race[c] = vers["F"][c].values
    p = {k: sc.score(v)[0] for k, v in (("J", vers["J"]), ("F", vers["F"]), ("own", hyb_own), ("race", hyb_race))}
    ev = {"J": p["J"] * base.O_J.to_numpy(), "F": p["F"] * base.O_F.to_numpy(),
          "own": p["own"] * base.O_F.to_numpy(), "race": p["race"] * base.O_J.to_numpy()}
    sel = ph.set_index(["pair_id", "horse_id"])
    out = {}
    for r in ["S1", "S3"]:
        mask = sel.loc[list(zip(base.pair_id, base.horse_id)), f"J_{r}"].to_numpy()
        out[r] = {"n": int(mask.sum()),
                  "median_EV_F_over_EV_J": R.r6(np.median(ev["F"][mask] / ev["J"][mask])),
                  "median_only_own_price_to_final": R.r6(np.median(ev["own"][mask] / ev["J"][mask])),
                  "median_only_race_structure_to_final": R.r6(np.median(ev["race"][mask] / ev["J"][mask]))}
    return out


if __name__ == "__main__":
    main()
