"""R10_norm_meta — 事後(事前登録外)の診断。判定には使わない。

1. オッズ構成(人気薄ほど回収率が低い傾き)だけで説明できる Δ の大きさ:
   対称差の各馬に「同じオッズ区分の回収率」(W 窓の全馬 / 親集合 P)を割り当て、V 側と S3 側の平均差を出す。
2. V2 の選定変化のうち、切片だけ(特徴なし)の補正で起きる分(sigmoid の非線形で本命側が相対的に残る)。
3. 年等重み Δ の 2026(部分年・対称差 n が小さい)抜き感度。

    cd training && uv run python ../scripts/roi_explore/missed_20261004/r10_posthoc_diag.py
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("r10", HERE / "r10_norm_meta.py")
r10 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r10)


def main() -> int:
    d = r10.load()
    n = len(d)
    odds = d.odds.to_numpy(float)
    won = d.won.to_numpy(bool).astype(float)
    pay = won * odds * 100
    years = d.year.to_numpy(int)
    days = d.race_date.to_numpy()
    dsl = d.days_since_last.to_numpy(float)
    ev_s = np.stack([1.0 + d[f"pred{s}"].to_numpy(float) / 100.0 for s in r10.ENS_RUNS])
    p_bar = (ev_s / odds).mean(axis=0)
    ev_ens = p_bar * odds
    _, ridx = np.unique(d.race_id.to_numpy(), return_inverse=True)
    nr = ridx.max() + 1
    sum_p = np.bincount(ridx, weights=p_bar, minlength=nr)[ridx]
    ev_norm = p_bar / sum_p * odds
    s3 = r10.ar.match_mask(r10.ar.definition("S3"), ens_ev=ev_ens, single_ev=np.full(n, np.nan), odds=odds,
                           days_since_last=dsl)
    P = ev_ens > 1.0
    pidx = np.flatnonzero(P)
    ctx = r10.Ctx()
    ctx.nP = pidx.size
    ctx.p_year = years[pidx]
    ctx.odds_p = odds[pidx]
    pc = np.clip(p_bar[pidx], 1e-6, 1 - 1e-6)
    ctx.off_p = np.log(pc) - np.log1p(-pc)
    ctx.p_year_idx = {y: np.flatnonzero(ctx.p_year == y) for y in r10.APPLY_YEARS}
    pdays = days[pidx]
    ctx.p_win = {wn: (pdays >= lo) & (pdays <= hi) for wn, (lo, hi) in r10.WINDOWS.items()}
    n_by_year = {y: int((s3 & (years == y)).sum()) for y in r10.APPLY_YEARS}
    s3_p = s3[pidx]
    pay_p = pay[pidx]
    won_p = won[pidx]

    # observed V1 / V2 selections, recomputed exactly as in the main script
    sel_v1 = r10.select_top(ev_norm[pidx], ctx, n_by_year)
    res = json.loads((r10.OUT / "results.json").read_text())
    # rebuild Z exactly as main
    ph_s = ev_s / odds
    oq_dec = r10.qcut_within(ctx.odds_p, ctx.p_year, 10)
    sdlog = np.log(ph_s[:, pidx]).std(axis=0, ddof=1)
    lev = np.log(ev_ens[pidx])
    z1 = np.empty(ctx.nP)
    cell = ctx.p_year * 100 + oq_dec
    for c in np.unique(cell):
        m = cell == c
        Xc = np.column_stack([np.ones(m.sum()), lev[m]])
        beta, *_ = np.linalg.lstsq(Xc, sdlog[m], rcond=None)
        z1[m] = sdlog[m] - Xc @ beta
    q = (1 / odds) / np.bincount(ridx, weights=1 / odds, minlength=nr)[ridx]
    dlt = np.log(p_bar / sum_p) - np.log(q)
    s2r = np.bincount(ridx, weights=dlt**2, minlength=nr)
    cnt = np.bincount(ridx, minlength=nr)
    z4 = np.sqrt(np.maximum(s2r[ridx] - dlt**2, 0.0) / (cnt[ridx] - 1))[pidx]
    Z = np.column_stack([z1, (ev_s[:, pidx] > 1.2).sum(axis=0) / 15.0, np.abs(np.log(sum_p[pidx])), z4])
    sc_v2, _ = r10.run_v2(won_p, Z, ctx)
    sel_v2 = r10.select_top(sc_v2, ctx, n_by_year)
    chk = {"V1": r10.delta_stats(sel_v1, s3_p, pay_p, ctx)["W"], "V2": r10.delta_stats(sel_v2, s3_p, pay_p, ctx)["W"]}
    assert abs(chk["V1"] - res["decision"]["V1"]["Delta_W"]) < 1e-6
    assert abs(chk["V2"] - res["decision"]["V2"]["Delta_W"]) < 1e-6

    # 2. intercept-only V2
    sc_b0 = np.full(ctx.nP, np.nan)
    b0s = []
    for Y in r10.APPLY_YEARS:
        tr = ctx.p_year <= Y - 1
        ap = ctx.p_year == Y

        def f(b, tr=tr):
            eta = ctx.off_p[tr] + b[0]
            return (-(won_p[tr] * eta - np.logaddexp(0, eta)).sum(), np.array([-(won_p[tr] - expit(eta)).sum()]))

        b = minimize(f, np.zeros(1), jac=True, method="L-BFGS-B").x
        b0s.append(round(float(b[0]), 4))
        sc_b0[ap] = expit(ctx.off_p[ap] + b[0]) * ctx.odds_p[ap]
    sel_b0 = r10.select_top(sc_b0, ctx, n_by_year)
    ds_b0 = r10.delta_stats(sel_b0, s3_p, pay_p, ctx)
    ov_b0_v2 = (sel_b0 & sel_v2).sum() / sel_v2.sum()
    a_b0 = sel_b0 & ~s3_p
    a_v2 = sel_v2 & ~s3_p

    # 1. odds-composition baseline
    W = (days >= r10.WINDOWS["W"][0]) & (days <= r10.WINDOWS["W"][1])
    out_fl = {}
    for base_name, base_mask in (("all_rows_W", W), ("parent_P_W", W & P)):
        edges = np.unique(np.quantile(odds[base_mask], np.linspace(0, 1, 21)))
        b_all = np.clip(np.searchsorted(edges, odds, side="right") - 1, 0, len(edges) - 2)
        roi_bin = np.array([pay[base_mask & (b_all == k)].sum() / (100 * (base_mask & (b_all == k)).sum())
                            for k in range(len(edges) - 1)])
        base_roi = roi_bin[b_all]
        for v, sel in (("V1", sel_v1), ("V2", sel_v2), ("V2_intercept_only", sel_b0)):
            f = np.zeros(n, dtype=bool)
            f[pidx[sel]] = True
            for wn, (lo, hi) in r10.WINDOWS.items():
                wm = (days >= lo) & (days <= hi)
                A = f & ~s3 & wm
                B = s3 & ~f & wm
                obs = pay[A].sum() / (100 * A.sum()) - pay[B].sum() / (100 * B.sum())
                fl = base_roi[A].mean() - base_roi[B].mean()
                out_fl.setdefault(base_name, {}).setdefault(v, {})[wn] = {
                    "Delta_obs": round(float(obs), 6), "Delta_from_odds_composition": round(float(fl), 6),
                    "Delta_minus_composition": round(float(obs - fl), 6)}

    # 3. year-weighted without 2026
    per_year = pd.read_csv(r10.OUT / "per_year.csv")
    yw_ex26 = {v: round(float(per_year.loc[per_year.year < 2026, f"{v}_Delta"].mean()), 6) for v in ("V1", "V2")}
    yw_Q = {v: round(float(per_year.loc[per_year.year >= 2019, f"{v}_Delta"].mean()), 6) for v in ("V1", "V2")}
    yw_Q_ex26 = {v: round(float(per_year.loc[(per_year.year >= 2019) & (per_year.year < 2026), f"{v}_Delta"].mean()),
                          6) for v in ("V1", "V2")}

    out = {"note": "post-hoc diagnostics (NOT pre-registered; no decision role)",
           "check_reproduces_main_Delta_W": chk,
           "odds_composition_baseline_20_quantile_bins": out_fl,
           "V2_intercept_only": {"b0_by_year": dict(zip(r10.APPLY_YEARS, b0s, strict=True)),
                                 "Delta": {k: round(float(v), 6) for k, v in ds_b0.items()},
                                 "overlap_with_V2_selection": round(float(ov_b0_v2), 6),
                                 "n_symdiff_vs_S3": int(a_b0.sum()), "n_symdiff_V2_vs_S3": int(a_v2.sum()),
                                 "V2_added_rows_also_added_by_intercept_only": round(
                                     float((a_b0 & a_v2).sum() / a_v2.sum()), 6)},
           "year_weighted_Delta_excluding_2026": yw_ex26,
           "year_weighted_Delta_2019_2026": yw_Q, "year_weighted_Delta_2019_2025": yw_Q_ex26}
    (r10.OUT / "posthoc_diag.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
