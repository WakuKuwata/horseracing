"""Arm B: 貪欲結合探索(発見期で ROI を最大化する条件の結合を beam search で探し、資格期・確認窓で検証)。

原子条件 = 数値列の分位帯(5 分位)+ 固定のオッズ/人気帯 + カテゴリ列の等値。結合は AND のみ・深さ ≤3。
発見期でのみ選択し(最小支持: 賭け数 ≥ MIN_BETS・的中 ≥ MIN_HITS)、上位 K 本を資格期→確認窓で検証する。
帰無(勝者を市場 q から抽選)で同じ探索を反復し、「発見期 best ROI」と「資格期まで通る本数」の帰無分布を出す。

    cd training && uv run python ../scripts/roi_explore/greedy_search.py --kind market --null-reps 20
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import evaluate as ev  # noqa: E402

ART = ev.ART
NUMERIC_ATOMS = [
    "odds", "q", "popularity", "odds_rank", "fav_q", "odds_gap12", "q_entropy_norm", "field_size", "distance",
    "age", "frame", "weight", "weight_diff", "jockey_weight", "career_starts", "career_win_rate",
    "career_top3_rate", "prev_finish", "avg_last3_finish", "best_finish_last5", "wins_last5", "top3_last5",
    "prev_popularity", "prev_odds", "prev_finish_pct", "prev_beat_market", "dist_change", "class_change",
    "days_since_last", "weight_change_vs_prev", "prev_last3f_rank", "prev_margin_sec",
    "jockey_win_rate_365", "jockey_starts_365", "trainer_win_rate_365", "combo_win_rate_all",
    "jockey_excess_365", "trainer_excess_365", "jockey_wins_today_before", "day_prev_fav_win_rate", "day_prev_winner_pop_mean",
    "day_prev_winner_style_front_share", "race_number", "month", "n_odds_under_10", "prize_money",
]
MODEL_ATOMS = ["p", "p_rank", "ev", "p_over_q", "p_gap12", "p_top3"]
CAT_ATOMS = ["track_type", "dist_band", "race_class_canon", "going", "sex", "venue_code", "prev_running_style",
             "prev_class_canon", "weather"]
BOOL_ATOMS = ["is_fav", "is_debut", "is_last_race", "is_first_race", "is_graded", "tataki_2", "last_won",
              "jockey_change", "model_fav_is_market_fav"]
ODDS_BANDS = [(1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 4.0), (4.0, 6.0), (6.0, 8.0), (8.0, 11.0), (11.0, 15.0),
              (15.0, 21.0), (21.0, 31.0), (31.0, 51.0), (51.0, 101.0), (101.0, 1e9)]


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def build_atoms(arr: dict, fit_rows: np.ndarray, kind: str) -> list[tuple[str, np.ndarray]]:
    atoms = []
    n = len(arr["race_id"])
    for lo, hi in ODDS_BANDS:
        atoms.append((f"odds∈[{lo},{hi})", (arr["odds"] >= lo) & (arr["odds"] < hi)))
    fields = NUMERIC_ATOMS + (MODEL_ATOMS if kind == "model" else [])
    for f in fields:
        x = arr[f]
        xf = x[fit_rows]; xf = xf[~np.isnan(xf)]
        if len(xf) < 1000:
            continue
        qs = np.unique(np.quantile(xf, [0.2, 0.4, 0.6, 0.8]))
        edges = np.r_[-np.inf, qs, np.inf]
        for a, b in zip(edges[:-1], edges[1:]):
            if a == b:
                continue
            m = (x >= a) & (x < b) if np.isfinite(b) else (x >= a)
            m &= ~np.isnan(x)
            if m[fit_rows].sum() < 2000:
                continue
            atoms.append((f"{f}∈[{a:.3g},{b:.3g})", m))
        # also top/bottom 10 % tails
        for lab, m in ((f"{f}≥p90", x >= np.quantile(xf, 0.9)), (f"{f}≤p10", x <= np.quantile(xf, 0.1))):
            m = m & ~np.isnan(x)
            if m[fit_rows].sum() >= 2000:
                atoms.append((lab, m))
    for f in CAT_ATOMS:
        x = arr[f]
        vals, cnt = np.unique(x[fit_rows][x[fit_rows] != None], return_counts=True)  # noqa: E711
        for v, c in zip(vals, cnt):
            if c >= 5000:
                atoms.append((f"{f}={v}", x == v))
    for f in BOOL_ATOMS:
        x = arr[f]
        atoms.append((f"{f}=1", x == 1.0)); atoms.append((f"{f}=0", x == 0.0))
    # dedupe identical masks
    seen, out = set(), []
    for lab, m in atoms:
        key = (int(m.sum()), int(m[fit_rows].sum()), hash(m[::9973].tobytes()))
        if key in seen:
            continue
        seen.add(key); out.append((lab, m.astype(bool)))
    return out


def stats(mask: np.ndarray, rows: np.ndarray, payout: np.ndarray, day_idx: np.ndarray):
    sel = rows[mask[rows]]
    n = len(sel)
    if n == 0:
        return 0, 0, 0.0, 0.0, 0
    po = payout[sel]
    hits = int((po > 0).sum())
    roi = po.sum() / (100.0 * n)
    se = po.std() / np.sqrt(n) / 100.0 if n > 1 else 1.0
    return n, hits, float(roi), float(se), int(len(np.unique(day_idx[sel])))


def beam_search(atoms, rows, payout, day_idx, *, min_bets, min_hits, beam, depth, topk):
    """Return list of (labels_tuple, mask, D-stats) sorted by lower-confidence ROI on rows."""
    cands = []
    for i, (lab, m) in enumerate(atoms):
        n, h, roi, se, d = stats(m, rows, payout, day_idx)
        if n >= min_bets and h >= min_hits:
            cands.append(((i,), roi - 1.0 * se, roi, n, h, d))
    cands.sort(key=lambda t: -t[1])
    frontier = cands[:beam]
    best = list(frontier)
    seen = {c[0] for c in cands}
    for _ in range(depth - 1):
        nxt = []
        for combo, _, _, _, _, _ in frontier:
            base = atoms[combo[0]][1].copy()
            for j in combo[1:]:
                base &= atoms[j][1]
            for i, (lab, m) in enumerate(atoms):
                if i in combo:
                    continue
                key = tuple(sorted(combo + (i,)))
                if key in seen:
                    continue
                seen.add(key)
                mm = base & m
                n, h, roi, se, d = stats(mm, rows, payout, day_idx)
                if n >= min_bets and h >= min_hits:
                    nxt.append((key, roi - 1.0 * se, roi, n, h, d))
        nxt.sort(key=lambda t: -t[1])
        frontier = nxt[:beam]
        best.extend(frontier)
    best.sort(key=lambda t: -t[1])
    out, used = [], set()
    for c in best:
        if c[0] in used:
            continue
        used.add(c[0]); out.append(c)
        if len(out) >= topk:
            break
    return out


def mask_of(atoms, combo):
    m = atoms[combo[0]][1].copy()
    for j in combo[1:]:
        m &= atoms[j][1]
    return m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="market", choices=["market", "model"])
    ap.add_argument("--min-bets", type=int, default=3000)
    ap.add_argument("--min-hits", type=int, default=100)
    ap.add_argument("--beam", type=int, default=25)
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--topk", type=int, default=40)
    ap.add_argument("--null-reps", type=int, default=20)
    ap.add_argument("--boot", type=int, default=5000)
    args = ap.parse_args(argv)
    out_dir = ART / "results" / f"armB_{args.kind}_mb{args.min_bets}"
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    arr = ev.load_rows()
    win = ev.WINDOWS[args.kind]
    yr = arr["_year"]
    rowsW = {w: np.flatnonzero((yr >= a) & (yr <= b)) for w, (a, b) in win.items()}
    if args.kind == "model":
        inb = arr["in_bundle"] == 1.0
        rowsW = {w: r[inb[r]] for w, r in rowsW.items()}
    payout = arr["_won"] * arr["odds"] * 100.0
    day_idx = arr["_day_idx"]
    log(f"kind={args.kind} rows D/Q/C = {[len(r) for r in rowsW.values()]}")
    atoms = build_atoms(arr, rowsW["D"], args.kind)
    log(f"atoms={len(atoms)}")
    best = beam_search(atoms, rowsW["D"], payout, day_idx, min_bets=args.min_bets, min_hits=args.min_hits,
                       beam=args.beam, depth=args.depth, topk=args.topk)
    log(f"search done {time.time() - t0:.0f}s; best D lcb={best[0][1]:.4f} roi={best[0][2]:.4f}")
    results = []
    stake = np.full(len(payout), 100.0)
    for combo, lcb, roi, n, h, d in best:
        m = mask_of(atoms, combo)
        rec = {"conditions": [atoms[i][0] for i in combo], "D": {"roi": roi, "lcb": lcb, "n_bets": n, "n_hits": h, "n_days": d}}
        for w in ("Q", "C"):
            nq, hq, rq, seq, dq = stats(m, rowsW[w], payout, day_idx)
            rec[w] = {"roi": rq, "n_bets": nq, "n_hits": hq, "n_days": dq}
        selD = rowsW["D"][m[rowsW["D"]]]
        rec["D"]["max_hit_share"] = float(payout[selD].max() / payout[selD].sum()) if payout[selD].sum() > 0 else None
        rec["pass_DQ"] = bool(rec["D"]["roi"] >= 1.0 and rec["Q"]["roi"] >= 1.0 and rec["Q"]["n_hits"] >= 20)
        if rec["pass_DQ"] or rec["Q"]["roi"] >= 0.95:
            selC = rowsW["C"][m[rowsW["C"]]]
            if len(selC) > 0:
                rec["C_boot"] = ev.day_bootstrap(stake, payout, selC, arr, b=args.boot)
        # yearly in all windows
        sel_all = np.flatnonzero(m)
        ys = np.bincount(yr[sel_all] - 1986, weights=stake[sel_all], minlength=41)
        yp = np.bincount(yr[sel_all] - 1986, weights=payout[sel_all], minlength=41)
        rec["yearly"] = {int(1986 + i): (round(float(yp[i] / ys[i]), 3) if ys[i] > 0 else None) for i in range(41)}
        results.append(rec)
    n_pass = sum(r["pass_DQ"] for r in results)
    log(f"observed: top-{len(results)} conjunctions, pass D∧Q = {n_pass}")
    # ---- null: winner ~ q, redo the search
    null = {"reps": args.null_reps, "best_D_roi": [], "n_pass_DQ": [], "best_Q_roi_of_passers": []}
    if args.null_reps > 0:
        rng = np.random.default_rng(11)
        race_idx = arr["_race_idx"]; q = arr["q"]; odds = arr["odds"]
        nr = int(race_idx.max()) + 1
        starts = np.r_[0, np.flatnonzero(np.diff(race_idx)) + 1, len(race_idx)]
        cq = np.cumsum(q); race_cum0 = np.r_[0.0, cq[starts[1:-1] - 1]]; race_tot = cq[starts[1:] - 1] - race_cum0
        for rep in range(args.null_reps):
            u = rng.random(nr) * race_tot + race_cum0
            w_rows = np.clip(np.searchsorted(cq, u, side="right"), starts[:-1], starts[1:] - 1)
            w = np.zeros(len(race_idx)); w[w_rows] = 1.0
            pay0 = w * odds * 100.0
            b0 = beam_search(atoms, rowsW["D"], pay0, day_idx, min_bets=args.min_bets, min_hits=args.min_hits,
                             beam=args.beam, depth=args.depth, topk=args.topk)
            null["best_D_roi"].append(float(b0[0][2]) if b0 else None)
            passers = 0; bq = []
            for combo, lcb, roi, n, h, d in b0:
                m = mask_of(atoms, combo)
                nq, hq, rq, _, _ = stats(m, rowsW["Q"], pay0, day_idx)
                if roi >= 1.0 and rq >= 1.0 and hq >= 20:
                    passers += 1; bq.append(rq)
            null["n_pass_DQ"].append(passers)
            null["best_Q_roi_of_passers"].append(max(bq) if bq else None)
            log(f"null rep {rep + 1}/{args.null_reps}: best D roi={null['best_D_roi'][-1]:.4f} passers={passers}")
        bd = np.asarray([x for x in null["best_D_roi"] if x is not None])
        null["summary"] = {"best_D_roi_p50": float(np.median(bd)), "best_D_roi_p95": float(np.percentile(bd, 95)),
                           "best_D_roi_max": float(bd.max()), "mean_pass_DQ": float(np.mean(null["n_pass_DQ"])),
                           "p_any_pass_DQ": float(np.mean(np.asarray(null["n_pass_DQ"]) > 0))}
    out = {"kind": args.kind, "params": vars(args), "n_atoms": len(atoms), "windows": win,
           "rows_per_window": {w: int(len(r)) for w, r in rowsW.items()}, "observed": results,
           "observed_n_pass_DQ": n_pass, "null": null, "elapsed_s": round(time.time() - t0, 1)}
    (out_dir / "result.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=ev._default))
    log(f"done → {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
