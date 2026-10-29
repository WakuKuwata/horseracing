"""173: candidate B (odds-noise training = 140's ``mev-ens15-judged-v0``) scored on the acceptance the
2026-10-08 independent evaluation pre-registered — judgement-time retention at closing and EV_F/EV_J —
instead of 140's NLL rule. No retraining. Research only, production untouched, DB read-only.

Primary set  = R02 scored pairs (443 races / 17 race days, judgement odds O_J + closing odds O_F).
Secondary set = DB 2026-10-05..10-25 (140 protocol v2 population; record only, out of the generator fit period).

    cd training && uv run python ../scripts/roi_explore/odds_noise_retention_173/score_retention.py [--no-db]
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import sys
import time

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts/roi_explore/judged_20261011"))
import common as C  # noqa: E402
import score as SC  # noqa: E402

SPEC_DIR = ROOT / "specs/173-odds-noise-retention"
PREREG_JSON = SPEC_DIR / "prereg.json"
PREREG_SHA = SPEC_DIR / "prereg.sha256"
OUT_DIR = ROOT / "artifacts/173-odds-noise-retention"
RULES = ("S1", "S2", "S3", "S4")
HOURS_EDGES = (0.0, 0.25, 0.5, 1.0, 3.0, 10.0, 40.0)
HOURS_LABELS = ("(0,0.25]", "(0.25,0.5]", "(0.5,1]", "(1,3]", "(3,10]", "(10,40]", ">40")
BOOT_B = 4000
BOOT_SEED = 20261029
PARITY_TOL = 1e-6


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


# ------------------------------------------------------------------ pure helpers (unit-tested)


def load_prereg(path: pathlib.Path = PREREG_JSON, sha_path: pathlib.Path = PREREG_SHA) -> dict:
    """The frozen pre-registration; refuses to run if its sha256 no longer matches the recorded one."""
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    recorded = sha_path.read_text(encoding="utf-8").strip()
    if sha != recorded:
        raise RuntimeError(f"prereg.json sha256 {sha} != recorded {recorded}; refusing to score")
    pre = json.loads(path.read_text(encoding="utf-8"))
    pre["_sha256"] = sha
    return pre


def hours_band(hours) -> np.ndarray:
    h = np.asarray(hours, dtype=float)
    idx = np.searchsorted(np.asarray(HOURS_EDGES[1:]), h, side="left")
    return np.asarray(HOURS_LABELS, dtype=object)[idx]


def weighted_median(v, w) -> float:
    v = np.asarray(v, float)
    w = np.asarray(w, float)
    keep = w > 0
    if not keep.any():
        return float("nan")
    v, w = v[keep], w[keep]
    order = np.argsort(v, kind="mergesort")
    cw = np.cumsum(w[order])
    return float(v[order][np.searchsorted(cw, 0.5 * cw[-1], side="left")])


def day_index(days) -> tuple[np.ndarray, list[str]]:
    uniq = sorted(set(str(d) for d in days))
    pos = {d: i for i, d in enumerate(uniq)}
    return np.array([pos[str(d)] for d in days], dtype=int), uniq


def rule_masks(ens_ev, odds, days_since_last) -> dict[str, np.ndarray]:
    """S1..S4 through the frozen ``attention_rules.match_mask`` (S5 needs the single-seed EV: not here)."""
    from horseracing_eval import attention_rules as ar

    return {
        r: np.asarray(
            ar.match_mask(
                ar.definition(r),
                ens_ev=ens_ev,
                single_ev=None,
                odds=odds,
                days_since_last=days_since_last,
            ),
            dtype=bool,
        )
        for r in RULES
    }


def arm_block(
    j: np.ndarray,
    f: np.ndarray,
    ev_j: np.ndarray,
    ev_f: np.ndarray,
    won: np.ndarray,
    p_j: np.ndarray,
    day_idx: np.ndarray,
    counts: np.ndarray,
    *,
    official_ret: np.ndarray | None = None,
) -> dict:
    """Retention / EV ratio / calibration for one (arm, rule) with race-day block bootstrap CIs.

    j: judgement-time selection of the arm; f: closing-side selection (production ens15 at O_F);
    official_ret: per-row return in 100-yen units from the official payout (NaN where unavailable)."""
    j = np.asarray(j, bool)
    f = np.asarray(f, bool)
    n_j = int(j.sum())
    out = {"n_j": n_j, "n_retained": int((j & f).sum()), "n_f": int(f.sum())}
    if n_j == 0:
        out.update(
            {
                "retention": None,
                "retention_ci": None,
                "ev_ratio_median": None,
                "ev_ratio_median_ci": None,
                "calib_won_over_p": None,
                "won": 0,
                "sum_p_j": 0.0,
                "ev_j_median": None,
            }
        )
        return out
    ratio = np.asarray(ev_f, float)[j] / np.asarray(ev_j, float)[j]
    out["retention"] = out["n_retained"] / n_j
    # lower weighted median with unit weights = the same estimator the bootstrap replicates use
    out["ev_ratio_median"] = weighted_median(ratio, np.ones_like(ratio))
    out["ev_j_median"] = float(np.median(np.asarray(ev_j, float)[j]))
    w = np.asarray(won, float)[j]
    out["won"] = int(w.sum())
    out["sum_p_j"] = float(np.asarray(p_j, float)[j].sum())
    out["calib_won_over_p"] = float(w.sum() / out["sum_p_j"]) if out["sum_p_j"] > 0 else None
    n_days = counts.shape[1]
    nj_d = np.bincount(day_idx[j], minlength=n_days).astype(float)
    nr_d = np.bincount(day_idx[j & f], minlength=n_days).astype(float)
    den = counts @ nj_d
    with np.errstate(invalid="ignore", divide="ignore"):
        reps = (counts @ nr_d) / den
    reps = reps[den > 0]
    out["retention_ci"] = [float(np.percentile(reps, 2.5)), float(np.percentile(reps, 97.5))]
    dj = day_idx[j]
    med_reps = np.array([weighted_median(ratio, counts[b, dj]) for b in range(counts.shape[0])])
    med_reps = med_reps[np.isfinite(med_reps)]
    out["ev_ratio_median_ci"] = [
        float(np.percentile(med_reps, 2.5)),
        float(np.percentile(med_reps, 97.5)),
    ]
    if official_ret is not None:
        r = np.asarray(official_ret, float)[j]
        ok = np.isfinite(r)
        out["roi_official_reference"] = {
            "n": int(ok.sum()),
            "roi": float(r[ok].mean()) if ok.any() else None,
            "note": "official-payout rows only; reference, not a decision input",
        }
    return out


def verdict(block: dict, prereg: dict) -> dict:
    v = prereg["verdict"]
    n_j = int(block.get("n_j") or 0)
    if n_j < int(v["min_n_j"]):
        return {"outcome": "NO_DECISION", "reason": f"n_j {n_j} < {v['min_n_j']}"}
    ret = block["retention"]
    med = block["ev_ratio_median"]
    ok_ret = ret >= float(v["pass_if"]["retention_ge"])
    ok_med = med >= float(v["pass_if"]["ev_ratio_median_ge"])
    if ok_ret and ok_med:
        return {"outcome": "PASS", "reason": "retention and ev_ratio_median both at or above the pre-registered floors"}
    return {
        "outcome": "REJECT",
        "reason": f"retention {ret:.3f} (>= {v['pass_if']['retention_ge']}: {ok_ret}), "
        f"ev_ratio_median {med:.3f} (>= {v['pass_if']['ev_ratio_median_ge']}: {ok_med})",
    }


def frame_at(base: pd.DataFrame, odds: np.ndarray, log_min: np.ndarray | float, r02) -> pd.DataFrame:
    """Feature frame with the 12 market columns recomputed from ``odds`` (same path as 140) + log_min."""
    d = base.reset_index(drop=True).copy()
    d["odds"] = np.asarray(odds, float)
    d = C.apply_market_block(d, r02)
    d["log_min"] = np.asarray(log_min, float) if np.ndim(log_min) else float(log_min)
    return d


def popularity_with_fallback(observed, odds, group) -> np.ndarray:
    """Observed 人気 where known, else the rank of ``odds`` within ``group`` (method='min'), as R02 did."""
    obs = pd.to_numeric(pd.Series(np.asarray(observed, dtype=object)), errors="coerce").to_numpy(float)
    fb = pd.Series(np.asarray(odds, float)).groupby(np.asarray(group)).rank(method="min").to_numpy(float)
    return np.where(np.isnan(obs), fb, obs)


def predict_both(
    frame: pd.DataFrame, ens, judged, *, ens_popularity: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """ens15 sees the observed 人気 when given (production/R02 convention: ``race_horses.popularity`` or the
    quote's 人気, rank-of-odds fallback); the judged model always sees popularity := odds_rank, the convention
    it was trained with (140 ``apply_market_block``)."""
    from horseracing_training import market_ev

    model, boosters, _ = ens
    fe = frame
    if ens_popularity is not None:
        fe = frame.copy()
        fe["popularity"] = np.asarray(ens_popularity, float)
    X = model.design_matrix(fe)
    p_ens = np.mean(np.vstack([b.predict(X, num_threads=1) for b in boosters]), axis=0)
    spec_j, boosters_j, _ = judged
    XJ = market_ev._design_matrix(spec_j, frame)
    p_jud = np.mean(np.vstack([b.predict(XJ, num_threads=1) for b in boosters_j]), axis=0)
    return p_ens, p_jud


def score_population(
    pop: pd.DataFrame,
    base: pd.DataFrame,
    ens,
    judged,
    r02,
    *,
    official_ret: np.ndarray | None = None,
    pop_j: np.ndarray | None = None,
    pop_f: np.ndarray | None = None,
    b: int = BOOT_B,
    seed: int = BOOT_SEED,
) -> dict:
    """pop: race_id, O_J, O_F, hours, won, days_since_last, day (aligned with base rows).
    pop_j / pop_f: observed 人気 for the ens15 frames (None = odds_rank for both arms, the 140 convention).
    Returns the per-arm / per-rule blocks + the raw predictions (for parity checks by the caller)."""
    from horseracing_eval.bootstrap import block_bootstrap_counts

    o_j = pop["O_J"].to_numpy(float)
    o_f = pop["O_F"].to_numpy(float)
    hours = pop["hours"].to_numpy(float)
    won = pop["won"].to_numpy(bool)
    dsl = pop["days_since_last"].to_numpy(float)
    log_min_j = np.log(np.maximum(1.0, hours * 60.0))
    fr_j = frame_at(base, o_j, log_min_j, r02)
    fr_f = frame_at(base, o_f, 0.0, r02)
    p_ens_j, p_jud_j = predict_both(fr_j, ens, judged, ens_popularity=pop_j)
    p_ens_f, p_jud_f0 = predict_both(fr_f, ens, judged, ens_popularity=pop_f)
    ev = {
        "ens15_J": p_ens_j * o_j,
        "judged_J": p_jud_j * o_j,
        "ens15_F": p_ens_f * o_f,
        "judged_F0": p_jud_f0 * o_f,
    }
    masks = {k: rule_masks(v, o_j if k.endswith("_J") else o_f, dsl) for k, v in ev.items()}
    didx, days = day_index(pop["day"].to_numpy())
    counts = block_bootstrap_counts(len(days), b, seed)
    hb = hours_band(hours)
    out = {"n_rows": int(len(pop)), "n_races": int(pop["race_id"].nunique()), "n_days": len(days), "arms": {}}
    p_j_by_arm = {"ens15": p_ens_j, "judged": p_jud_j}
    for arm in ("ens15", "judged"):
        out["arms"][arm] = {}
        for r in RULES:
            j = masks[f"{arm}_J"][r]
            blk = arm_block(
                j, masks["ens15_F"][r], ev[f"{arm}_J"], ev["ens15_F"], won, p_j_by_arm[arm], didx, counts,
                official_ret=official_ret,
            )
            # diagnostic: closing side = the judged model itself at log_min=0 (not the pre-registered side)
            jf0 = j & masks["judged_F0"][r]
            blk["retention_vs_judged_closing_diag"] = (
                float(jf0.sum() / j.sum()) if j.sum() else None
            )
            blk["by_hours_band"] = {}
            for lab in HOURS_LABELS:
                m = hb == lab
                nj = int((j & m).sum())
                blk["by_hours_band"][lab] = {
                    "n_j": nj,
                    "n_retained": int((j & m & masks["ens15_F"][r]).sum()),
                    "retention": float((j & m & masks["ens15_F"][r]).sum() / nj) if nj else None,
                    "ev_ratio_median": float(
                        np.median(ev["ens15_F"][j & m] / ev[f"{arm}_J"][j & m])
                    )
                    if nj
                    else None,
                }
            out["arms"][arm][r] = blk
    out["ev_gt_1_2_counts_J"] = {arm: int((ev[f"{arm}_J"] > 1.2).sum()) for arm in ("ens15", "judged")}
    out["_pred"] = {"p_ens_j": p_ens_j, "p_jud_j": p_jud_j, "p_ens_f": p_ens_f, "masks": masks, "ev": ev}
    return out


# ------------------------------------------------------------------ sets


def load_primary(prereg: dict) -> tuple[pd.DataFrame, str]:
    path = ROOT / prereg["primary_set"]["path"]
    s = pd.read_parquet(path)
    exp = prereg["primary_set"]["expected"]
    got = {
        "races": int(s["race_id"].nunique()),
        "rows": int(len(s)),
        "race_days": int(s["race_date"].nunique()),
        "pairs": int(s["pair_id"].nunique()),
    }
    if got != exp:
        raise RuntimeError(f"primary set differs from the pre-registered one: {got} != {exp}")
    s = s.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
    return s, C.sha256_file(path)


R02_DIR = ROOT / "artifacts/roi_explore/missed_20261004/R02_odds_drift"
R05_DIR = ROOT / "artifacts/roi_explore/missed_20261004/R05_settlement"


def primary_observed_popularity(s: pd.DataFrame, r02) -> tuple[np.ndarray, np.ndarray, dict]:
    """Rebuild the 人気 the R02 scorer saw (judgement: CHAOS snapshot field or the stored row's value;
    closing: R05 archive page for archive races, race_horses for db_stored), rank-of-odds fallback."""
    chaos_p, rh_p, arc_p = R02_DIR / "db_chaos.parquet", R02_DIR / "db_race_horses.parquet", R05_DIR / "archived_final_odds.csv"
    chaos = pd.read_parquet(chaos_p)
    cmap = {
        row.snap_id: {h["horse_id"]: h.get("popularity") for h in json.loads(row.field_json)}
        for row in chaos.itertuples()
    }
    cha = chaos[chaos["status"] == "active"].copy()
    cha["hours"] = cha["seconds_to_post"] / 3600.0
    cha["tb"] = r02.band(cha["hours"].to_numpy(), r02.TB_EDGES, r02.TB)
    chosen = {(r.race_id, r.source): r.hours for r in s.drop_duplicates("pair_id").itertuples()}
    cha = cha[[abs(r.hours - chosen.get((r.race_id, "CHAOS"), -1e9)) < 1e-12 for r in cha.itertuples()]]
    look = {(r.race_id, r.tb): r.snap_id for r in cha.itertuples()}
    obs_j = [
        cmap.get(look.get((r.race_id, r.tb)), {}).get(r.horse_id) if r.source == "CHAOS" else r.popularity
        for r in s.itertuples()
    ]
    pop_j = popularity_with_fallback(obs_j, s["O_J"].to_numpy(float), s["pair_id"].to_numpy())
    rh = pd.read_parquet(rh_p)[["race_id", "horse_id", "popularity"]].rename(columns={"popularity": "pop_db"})
    arc = pd.read_csv(arc_p, dtype={"race_id": str, "horse_id": str})[["race_id", "horse_number", "popularity_page"]]
    arc["horse_number"] = arc["horse_number"].astype(int)
    m = s[["race_id", "horse_id", "horse_number", "final_src"]].copy()
    m["horse_number"] = m["horse_number"].astype(int)
    m = m.merge(rh, on=["race_id", "horse_id"], how="left").merge(arc, on=["race_id", "horse_number"], how="left")
    obs_f = np.where(m["final_src"].to_numpy() == "archive", m["popularity_page"].to_numpy(), m["pop_db"].to_numpy())
    pop_f = popularity_with_fallback(obs_f, s["O_F"].to_numpy(float), s["pair_id"].to_numpy())
    shas = {p.name: C.sha256_file(p) for p in (chaos_p, rh_p, arc_p)}
    return pop_j, pop_f, shas


def primary_parity(s: pd.DataFrame, res: dict) -> dict:
    """The ens15 arm must reproduce the stored EVe_J / EVe_F and the stored J_S*/F_S* flags (fail-closed)."""
    ev = res["_pred"]["ev"]
    masks = res["_pred"]["masks"]
    d_j = float(np.nanmax(np.abs(ev["ens15_J"] - s["EVe_J"].to_numpy(float))))
    d_f = float(np.nanmax(np.abs(ev["ens15_F"] - s["EVe_F"].to_numpy(float))))
    flags = {}
    for r in RULES:
        flags[r] = {
            "J_mismatch": int((masks["ens15_J"][r] != s[f"J_{r}"].to_numpy(bool)).sum()),
            "F_mismatch": int((masks["ens15_F"][r] != s[f"F_{r}"].to_numpy(bool)).sum()),
        }
    ok = d_j < PARITY_TOL and d_f < PARITY_TOL and all(
        v["J_mismatch"] == 0 and v["F_mismatch"] == 0 for v in flags.values()
    )
    return {"ev_j_max_abs": d_j, "ev_f_max_abs": d_f, "flags": flags, "ok": bool(ok)}


def run_primary(prereg: dict, ens, judged, r02) -> dict:
    s, sha = load_primary(prereg)
    pop = pd.DataFrame(
        {
            "race_id": s["race_id"].astype(str),
            "O_J": s["O_J"].astype(float),
            "O_F": s["O_F"].astype(float),
            "hours": s["hours"].astype(float),
            "won": s["won"].astype(bool),
            "days_since_last": pd.to_numeric(s["days_since_last"], errors="coerce"),
            "day": s["race_date"].astype(str),
        }
    )
    official = np.where(
        s["official_race"].to_numpy(bool),
        np.where(s["won"].to_numpy(bool), s["official_payout_yen"].to_numpy(float) / 100.0, 0.0),
        np.nan,
    )
    base = s.drop(columns=["O_J", "O_F", "EVe_J", "EVe_F", "EVs_J", "EVs_F"], errors="ignore")
    pop_j, pop_f, pop_sha = primary_observed_popularity(s, r02)
    res = score_population(pop, base, ens, judged, r02, official_ret=official, pop_j=pop_j, pop_f=pop_f)
    res["parity_ens15"] = primary_parity(s, res)
    res["input_sha256"] = sha
    res["popularity_inputs_sha256"] = pop_sha
    res["popularity_convention"] = {
        "ens15": "observed 人気 (CHAOS snapshot field / stored row / R05 archive page), rank-of-odds fallback",
        "judged": "popularity := odds_rank (training convention of mev-ens15-judged-v0)",
    }
    res["hours_quantiles"] = {
        str(q): float(v) for q, v in s.drop_duplicates("pair_id")["hours"].quantile([0.1, 0.5, 0.9]).items()
    }
    res.pop("_pred")
    return res


def run_secondary(prereg: dict, ens, judged, r02, db_url: str) -> dict:
    sec = prereg["secondary_set"]
    d_from, d_to = SC.check_window(sec["from"], sec["to"])
    conn = SC._conn(db_url)
    try:
        inp = SC.fetch_inputs(conn, d_from, d_to)
        pop, excl = SC.select_population(
            inp["preds"], inp["started"], inp["results"], inp["payouts"], inp["provenance"]
        )
        if not len(pop):
            return {"note": "empty population", "excluded": {k: len(v) for k, v in excl.items()}}
        feat, bad = SC.build_judged_features(conn, pop, d_to, r02)
    finally:
        conn.close()
    if bad:
        pop = pop[~pop["race_id"].isin(bad)].reset_index(drop=True)
    pop = pop.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
    feat = feat.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
    if not (feat["race_id"].to_numpy() == pop["race_id"].to_numpy()).all() or not (
        feat["horse_id"].to_numpy() == pop["horse_id"].to_numpy()
    ).all():
        raise RuntimeError("secondary set: feature rows and population rows are not aligned")
    if pop["O_F"].isna().any() or (pop["O_F"] <= 0).any():
        bad_rid = sorted(pop.loc[pop["O_F"].isna() | (pop["O_F"] <= 0), "race_id"].unique())
        pop = pop[~pop["race_id"].isin(bad_rid)].reset_index(drop=True)
        feat = feat[~feat["race_id"].isin(bad_rid)].reset_index(drop=True)
        excl["closing_odds_missing"] = bad_rid
    p2 = pd.DataFrame(
        {
            "race_id": pop["race_id"].astype(str),
            "O_J": pop["odds_used"].astype(float),
            "O_F": pop["O_F"].astype(float),
            "hours": pop["h_min"].astype(float) / 60.0,
            "won": pop["won"].astype(bool),
            "days_since_last": pd.to_numeric(feat["days_since_last"], errors="coerce"),
            "day": pop["day"].astype(str),
        }
    )
    official = np.where(
        pop["has_official_payout"].to_numpy(bool), pop["payout_yen"].to_numpy(float) / 100.0, np.nan
    )
    base = feat.drop(columns=["odds", "log_min", "odds_used", "h_min", "odds_final_db"], errors="ignore")
    res = score_population(p2, base, ens, judged, r02, official_ret=official)
    res.pop("_pred")
    res["excluded_races"] = {k: len(v) for k, v in excl.items()}
    res["dry_run_days"] = sorted(pop.loc[pop["dry_run"], "day"].unique().tolist())
    res["quote_days"] = sorted(pop.loc[~pop["dry_run"], "day"].unique().tolist())
    res["h_min_quantiles"] = {
        str(q): float(v) for q, v in pop.drop_duplicates("race_id")["h_min"].quantile([0.1, 0.5, 0.9]).items()
    }
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-url", default=SC.DEFAULT_DB)
    ap.add_argument("--no-db", action="store_true", help="skip the secondary (DB) set")
    ap.add_argument("--out", default=str(OUT_DIR / "result.json"))
    args = ap.parse_args(argv)
    out_path = pathlib.Path(args.out)
    if out_path.exists():
        raise SystemExit(f"{out_path} exists (append-only: pick another --out)")
    t0 = time.time()
    prereg = load_prereg()
    r02 = C.load_r02()
    ens = SC.load_ens15(C.ENS_DIR)
    judged = SC.load_judged(C.JUDGED_DIR, ens[0].spec["features"])
    if judged[0] is None:
        raise SystemExit(f"judged artifact incomplete: {judged[2]}")
    log("models loaded")
    primary = run_primary(prereg, ens, judged, r02)
    log(f"primary scored ({time.time() - t0:.0f}s) parity={primary['parity_ens15']}")
    result = {
        "name": prereg["name"],
        "run_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "prereg_sha256": prereg["_sha256"],
        "models": {"ens15": ens[2], "judged": {k: v for k, v in judged[2].items() if k != "model_sha256"},
                   "judged_model_sha256": judged[2].get("model_sha256")},
        "primary": primary,
        "disclaimers": prereg["disclaimers"],
    }
    if primary["parity_ens15"]["ok"]:
        pv = verdict(primary["arms"]["judged"]["S3"], prereg)
    else:
        pv = {"outcome": "INVALID", "reason": "ens15 arm does not reproduce the stored EVe_J/EVe_F/flags"}
    result["verdict"] = {"primary": {"set": "primary", "arm": "judged", "rule": "S3"}, **pv}
    if not args.no_db:
        try:
            result["secondary"] = run_secondary(prereg, ens, judged, r02, args.db_url)
            log(f"secondary scored ({time.time() - t0:.0f}s)")
        except Exception as e:  # record-only set: never blocks the primary verdict
            result["secondary"] = {"error": f"{type(e).__name__}: {e}"}
            log(f"secondary failed: {e}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    C.write_json(out_path, result)
    log(f"wrote {out_path} verdict={result['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
