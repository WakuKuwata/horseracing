"""R12_cross_pool — POST-HOC diagnostics (NOT pre-registered; never used for the verdict).

Motivation (written after seeing results): the pre-registered pre-race check of arm (iii) reversed
direction and the pre-registered leak diagnostic showed much larger pool gains after 2026-06-26 than
before. Stored win odds after 2026-06-26 are a mix of final values and pre-race snapshots (R05), so
q (and P3W, h2) of those races is not contemporaneous with the grid. Here the post-2026-06-26 races are
split by the R05 timing class of the stored win odds, and recomputed with archived result-page
(final) win odds where available.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import r12_common as C  # noqa: E402
import r12_cross_pool as M  # noqa: E402

OUT = C.OUT


def main():
    res = {"note": "post-hoc, not pre-registered, diagnostic only"}
    aff = pd.read_csv(C.R05 / "affected_races.csv", dtype={"race_id": str})
    arch = pd.read_csv(C.R05 / "archived_final_odds.csv", dtype={"race_id": str})
    tclass = dict(zip(aff.race_id, aff.timing_class))
    arch_ok = set(aff.race_id[aff.archive_tansho_ok.astype(bool)])
    aodds = {(r.race_id, int(r.horse_number)): float(r.result_page_odds) for r in arch.itertuples()
             if r.race_id in arch_ok and np.isfinite(r.result_page_odds) and r.result_page_odds > 0}
    prereg = json.loads((OUT / "results.json").read_text())
    cuts = {int(k): tuple(v) for k, v in prereg["arm_iii"]["cut_points"].items()}
    s3d = prereg["arm_iii"]["direction"]

    # ---------------- arm (iii) after 2026-06-26 by q timing class and with archived final q
    H3 = pd.read_parquet(OUT / "arm_iii_horses.parquet")
    H3["window"] = H3.race_date.map(lambda s: M.window_of(s, "iii"))
    H3["qclass"] = H3.race_id.map(tclass).fillna("NA")
    post = H3[H3.race_date > C.WIN_CUTOFF].copy()
    out3 = {}
    for timing in ("pre", "final"):
        for qc in ("FILLED_AFTER_RESULTS", "PRE_RACE_SNAPSHOT"):
            sel = (H3.race_date > C.WIN_CUTOFF) & (H3.timing == timing) & (H3.qclass == qc)
            r = M.arm_iii_eval(H3, sel, cuts) if sel.any() else {"n_horses": 0}
            out3[f"{timing}|stored_q_{qc}"] = {k: r.get(k) for k in ("n_races", "n_top", "hits_top", "roi_top", "roi_parent_matched",
                                                                    "delta", "roi_bottom", "n_bottom")}
            if "roi_top_ci" in r:
                out3[f"{timing}|stored_q_{qc}"]["roi_top_ci"] = r["roi_top_ci"]["ci"]
            if "per_band" in r:
                out3[f"{timing}|stored_q_{qc}"]["band4"] = r["per_band"].get(4)
    # recompute P3W with archived final odds where the whole field is archived
    rows = []
    for rid, g in post.groupby("race_id", sort=False):
        o = np.array([aodds.get((rid, int(n)), np.nan) for n in g.horse_number])
        if not np.isfinite(o).all():
            continue
        q = (1 / o) / (1 / o).sum()
        _, p3w = C.harville_top_marginals(q, C.MARKET_L2, C.MARKET_L3)
        g = g.copy()
        g["P3W"] = p3w
        g["r"] = g.P3T / g.P3W
        # popularity from archived odds rank (band definition uses stored popularity; keep stored)
        rows.append(g)
    if rows:
        HA = pd.concat(rows, ignore_index=True)
        for timing in ("pre", "final"):
            sel = HA.timing == timing
            r = M.arm_iii_eval(HA, sel, cuts) if sel.any() else {"n_horses": 0}
            out3[f"{timing}|archived_final_q"] = {k: r.get(k) for k in ("n_races", "n_top", "hits_top", "roi_top", "roi_parent_matched",
                                                                       "delta", "roi_bottom", "n_bottom")}
            if "roi_top_ci" in r:
                out3[f"{timing}|archived_final_q"]["roi_top_ci"] = r["roi_top_ci"]["ci"]
            if "per_band" in r:
                out3[f"{timing}|archived_final_q"]["band4"] = r["per_band"].get(4)
        # leak diag top3 with archived final q
        HA["placedf"] = HA.placed.astype(float)
        res["leak_top3_archived_final_q"] = M.leak_diag(HA, "P3T", "P3W", "placedf",
                                                        {"pre": HA.timing == "pre", "final": HA.timing == "final"})
    res["arm_iii_post_0626_splits"] = out3
    res["arm_iii_discovery_direction"] = s3d

    # top3 leak diag by stored-q class
    post["placedf"] = post.placed.astype(float)
    res["leak_top3_by_stored_q_class"] = M.leak_diag(post, "P3T", "P3W", "placedf", {
        f"{t}|{qc}": (post.timing == t) & (post.qclass == qc)
        for t in ("pre", "final") for qc in ("FILLED_AFTER_RESULTS", "PRE_RACE_SNAPSHOT")})

    # ---------------- arm (ii) / top2 leak by stored-q class and archived q
    H2 = pd.read_parquet(OUT / "arm_ii_horses.parquet")
    H2["qclass"] = H2.race_id.map(tclass).fillna("NA")
    p2 = H2[(H2.race_date > C.WIN_CUTOFF) & ~H2.dead_heat].copy()
    p2["top2f"] = p2.top2.astype(float)
    res["leak_top2_by_stored_q_class"] = M.leak_diag(p2, "x", "h2", "top2f", {
        f"{t}|{qc}": (p2.timing == t) & (p2.qclass == qc)
        for t in ("pre", "final") for qc in ("FILLED_AFTER_RESULTS", "PRE_RACE_SNAPSHOT")})
    rows = []
    for rid, g in p2.groupby("race_id", sort=False):
        o = np.array([aodds.get((rid, int(n)), np.nan) for n in g.horse_number])
        if not np.isfinite(o).all():
            continue
        q = (1 / o) / (1 / o).sum()
        h2, _ = C.harville_top_marginals(q, C.MARKET_L2, C.MARKET_L3)
        g = g.copy()
        g["h2"] = h2
        rows.append(g)
    if rows:
        HB = pd.concat(rows, ignore_index=True)
        res["leak_top2_archived_final_q"] = M.leak_diag(HB, "x", "h2", "top2f",
                                                        {"pre": HB.timing == "pre", "final": HB.timing == "final"})

    # ---------------- arm (ii) S3 confirm: hit concentration
    c = H2[(H2.timing == "final") & ~H2.dead_heat & (H2.race_date >= "2025-01-01") & (H2.race_date <= C.WIN_CUTOFF) & H2.S3]
    keep = c[c.v >= 1]
    hits = keep[keep.won]
    tot = float(np.where(keep.won, keep.odds, 0).sum())
    res["arm_ii_S3_confirm_keep_hits"] = {
        "n_keep": int(len(keep)), "hit_odds": [float(x) for x in sorted(hits.odds, reverse=True)],
        "max_hit_share": float(hits.odds.max() / tot) if len(hits) else None,
        "roi_keep_drop_largest_hit": float((tot - hits.odds.max()) / len(keep)) if len(hits) else None,
        "roi_S_drop_largest_keep_hit": float((np.where(c.won, c.odds, 0).sum() - hits.odds.max()) / len(c)) if len(hits) else None}

    # ---------------- arm (iii) band composition of the effect (final grids, discovery+confirm)
    fin = H3[(H3.timing == "final") & H3.window.isin(["discovery", "confirm"])]
    res["arm_iii_band4_only"] = {}
    for win in ("discovery", "confirm"):
        r = M.arm_iii_eval(fin, (fin.window == win) & (fin.band == 4), cuts)
        r2 = M.arm_iii_eval(fin, (fin.window == win) & (fin.band != 4), cuts)
        res["arm_iii_band4_only"][win] = {"band4": {k: r.get(k) for k in ("n_top", "hits_top", "roi_top", "roi_parent_matched", "roi_bottom")},
                                          "bands0_3": {k: r2.get(k) for k in ("n_top", "hits_top", "roi_top", "roi_parent_matched", "roi_bottom")}}
    (OUT / "posthoc.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float))
    print(json.dumps(res, ensure_ascii=False, indent=1, default=float))


if __name__ == "__main__":
    main()
