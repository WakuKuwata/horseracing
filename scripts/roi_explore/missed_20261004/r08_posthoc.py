"""R08_frontier post-hoc diagnostics (NOT pre-registered; written after results.json was seen).

(1) MCU15 veto with fine odds strata (year x log-odds bins of width 0.05 in log10) to separate the
    model's information from the favourite-longshot slope inside the prereg's coarse odds bands.
(2) MCU15 veto inside the R05 affected & correctable races only (selection on stale pre-race odds,
    win settled with the official payout) — a crude check of decision-time robustness.
(3) Veto rate and ROI by fine odds bin (descriptive).

Run: cd training && uv run python ../scripts/roi_explore/missed_20261004/r08_posthoc.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import r08_frontier as F  # noqa: E402

OUT = F.OUT


def perm_fine(t, rng, b, strata):
    pay = t.payout.to_numpy(float)
    st = t.stake.to_numpy(float)
    veto = t.veto.to_numpy(bool)
    P, Sd = pay.sum(), st.sum()
    kv = Sd - st[veto].sum()
    T_obs = (P - pay[veto].sum()) / kv - P / Sd
    vsum = np.zeros(b)
    for s in np.unique(strata):
        m = strata == s
        k = int(veto[m].sum())
        if k == 0:
            continue
        N = int(m.sum())
        wp = pay[m][pay[m] > 0]
        W = len(wp)
        if W == 0:
            continue
        nwin = rng.hypergeometric(W, N - W, k, size=b)
        for c0 in range(0, b, 1000):
            c1 = min(b, c0 + 1000)
            order = np.argsort(rng.random((c1 - c0, W)), axis=1)
            cs = np.cumsum(wp[order], axis=1)
            nw = nwin[c0:c1]
            vsum[c0:c1] += np.where(nw > 0, cs[np.arange(c1 - c0), np.maximum(nw - 1, 0)], 0.0)
    T_perm = (P - vsum) / kv - P / Sd
    return {"T_obs": F.r6(T_obs), "T_perm_mean": F.r6(T_perm.mean()), "T_perm_sd": F.r6(T_perm.std()),
            "T_minus_perm_mean": F.r6(T_obs - T_perm.mean()),
            "p_one_sided": F.r6((1 + int((T_perm >= T_obs).sum())) / (b + 1)), "n": int(len(t)),
            "n_veto": int(veto.sum()), "n_strata": int(len(np.unique(strata)))}


def main():
    rows = F.load_rows()
    aff = pd.read_csv(F.R05, dtype={"race_id": str})
    amap = {r: (a, c, w) for r, a, c, w in zip(aff.race_id, aff.affected, aff.correctable,
                                               aff.official_win_payout_yen)}
    rng = np.random.default_rng(F.SEED + 1)
    out = {"note": "post-hoc, not pre-registered"}

    sec = rows[(rows.race_date >= F.SEC_LO) & (rows.race_date <= F.SEC_HI)].copy()
    sec = sec[~sec.race_id.map(lambda r: amap.get(r, (0,))[0] == 1)]
    sec["payout"] = np.where(sec.won, sec.odds * 100.0, 0.0)
    sec["stake"] = 100.0
    fine = {}
    for name, m in (("WIN_FAV", sec.odds_rank == 1), ("WIN_CAP21", sec.odds < 21.0)):
        t = sec[m].copy()
        lb = np.floor(np.log10(t.odds.to_numpy(float)) / 0.05).astype(int)
        strata = t.year.to_numpy() * 1000 + lb
        fine[name] = perm_fine(t, rng, 2000, strata)
        # descriptive by fine bin (pooled years)
        t["lb"] = lb
        g = t.groupby("lb").apply(lambda x: pd.Series({
            "odds_lo": 10 ** (x.name * 0.05), "n": len(x), "veto_rate": x.veto.mean(),
            "roi_kept": x[~x.veto].payout.sum() / max(1, (~x.veto).sum() * 100),
            "roi_veto": x[x.veto].payout.sum() / max(1, x.veto.sum() * 100)}), include_groups=False)
        fine[name]["by_bin"] = {f"{r.odds_lo:.2f}": {"n": int(r.n), "veto_rate": F.r6(r.veto_rate),
                                                     "roi_kept": F.r6(r.roi_kept), "roi_veto": F.r6(r.roi_veto)}
                                for r in g.itertuples() if r.n >= 2000}
    out["mcu15_secondary_fine_strata"] = fine
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "by_bin"} for k, v in fine.items()}), flush=True)

    # (2) affected & correctable races in rows (2026-07..09), stale-odds selection, official payout
    w = rows[(rows.race_date >= "2026-06-27") & (rows.race_date <= F.W_HI)].copy()
    w = w[w.race_id.map(lambda r: amap.get(r, (0, False))[0] == 1 and bool(amap.get(r, (0, False))[1]))]
    w["payout"] = np.where(w.won, w.race_id.map(lambda r: amap[r][2]).astype(float), 0.0)
    w["stake"] = 100.0
    st = {}
    for name, m in (("WIN_FAV", w.odds_rank == 1), ("WIN_CAP21", w.odds < 21.0)):
        t = w[m]
        k = t[~t.veto]
        st[name] = {"races": int(t.race_id.nunique()), "n": int(len(t)), "n_veto": int(t.veto.sum()),
                    "roi_parent": F.r6(t.payout.sum() / t.stake.sum()),
                    "roi_kept": F.r6(k.payout.sum() / k.stake.sum()),
                    "roi_veto": F.r6(t[t.veto].payout.sum() / max(1, t.veto.sum() * 100)),
                    "hits_veto": int((t[t.veto].payout > 0).sum()), "hits_kept": int((k.payout > 0).sum())}
    out["mcu15_stale_odds_affected_correctable"] = st
    print(json.dumps(st), flush=True)
    (OUT / "posthoc.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
