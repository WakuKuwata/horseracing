"""R06_plus10 の敵対的検証(読み取りのみ・DB は SELECT だけ)。

    cd training && uv run python ../scripts/roi_explore/missed_20261004/r06_plus10_adversarial.py

元スクリプト r06_plus10.py を import せず、主要な数値を独立に再計算し、
配当データの整合性・標本の偏り・親集合比較の頑健性を点検する。
出力: artifacts/roi_explore/missed_20261004/R06_plus10/adversarial_verify.json
"""
from __future__ import annotations

import json
import pathlib
import sys

import numpy as np
import pandas as pd
import sqlalchemy as sa

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "eval" / "src"))
from horseracing_eval.bootstrap import race_block_ratio_bootstrap_ci_v1  # noqa: E402

OUT = REPO / "artifacts/roi_explore/missed_20261004/R06_plus10/adversarial_verify.json"
DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
B = 20000


def main() -> int:
    res: dict = {}
    cols = ["race_id", "race_date", "year", "odds", "odds_rank", "field_size", "race_ok",
            "result_status", "finish_order", "horse_number", "won", "n_winners"]
    r = pd.read_parquet(REPO / "artifacts/market_ev/rows_2007.parquet", columns=cols)
    res["rows_dup_race_horse"] = int(r.duplicated(["race_id", "horse_number"]).sum())
    # 独立の母集団フィルタ(元と同じ定義を別実装)
    nullrace = set(r.loc[r.result_status.isna(), "race_id"])
    r = r[(r.race_ok == True) & (~r.race_id.isin(nullrace))].copy()  # noqa: E712
    o = r.odds.round(1)
    r["band"] = np.select([o <= 1.2, o == 1.3, (o >= 1.4) & (o <= 1.5)], ["B1", "B2", "B3"], default="")
    r["placed"] = ((r.result_status == "finished")
                   & np.where(r.field_size >= 8, r.finish_order <= 3,
                              (r.field_size >= 5) & (r.finish_order <= 2))).astype(int)

    # ---- A. stage1 再現 ----
    s1 = r[(r.field_size >= 8) & (r.race_date >= "2008-01-01") & (r.race_date <= "2026-06-26")]
    res["A_stage1"] = {b: {"n": int((s1.band == b).sum()), "placed": int(s1[s1.band == b].placed.sum()),
                           "P": float(s1[s1.band == b].placed.mean())} for b in ["B1", "B2", "B3"]}
    # 2008-2018 と 2019-2026 の P の差(定常性)
    res["A_stage1_split"] = {b: {"2008_2018": float(s1[(s1.band == b) & (s1.race_date < "2019-01-01")].placed.mean()),
                                 "2019_2026": float(s1[(s1.band == b) & (s1.race_date >= "2019-01-01")].placed.mean()),
                                 "2023_2026": float(s1[(s1.band == b) & (s1.race_date >= "2023-01-01")].placed.mean()),
                                 "n_2023_2026": int(((s1.band == b) & (s1.race_date >= "2023-01-01")).sum())}
                             for b in ["B1", "B2", "B3"]}
    # 帯の割り当てが odds_rank==1 と一致するか
    res["A_band_not_rank1"] = int(((s1.band != "") & (s1.odds_rank != 1)).sum())

    # ---- B. 配当の整合性 ----
    with sa.create_engine(DB).connect() as c:
        dv = pd.read_sql(sa.text(
            "select race_id, (selection->>0)::int as horse_number, odds::float as div, source, "
            "jsonb_array_length(selection) as sel_len from exotic_odds where bet_type='place'"), c)
    res["B_sources"] = dv.source.value_counts().to_dict()
    res["B_sel_len"] = dv.sel_len.value_counts().to_dict()
    res["B_dup_rows"] = int(dv.duplicated(["race_id", "horse_number"]).sum())
    cov = set(dv.race_id)
    s2 = r[(r.race_date >= "2025-01-01") & (r.race_date <= "2026-09-22") & r.race_id.isin(cov)]
    m = s2.merge(dv[["race_id", "horse_number", "div"]], on=["race_id", "horse_number"], how="left")
    # 着外なのに配当がある馬・3着内なのに配当がない馬(馬番対応の誤り検出)
    res["B_unplaced_with_div"] = int(((m.placed == 0) & m["div"].notna() & (m.field_size >= 5)).sum())
    res["B_placed_without_div"] = int(((m.placed == 1) & m["div"].isna()).sum())
    # 配当行のうち rows に対応馬がいないもの
    jj = dv[dv.race_id.isin(set(s2.race_id))].merge(s2[["race_id", "horse_number"]], how="left",
                                                     on=["race_id", "horse_number"], indicator=True)
    res["B_div_rows_without_horse"] = int((jj._merge == "left_only").sum())
    # 帯の馬の配当がレース内最小か(超本命は最小配当のはず)
    m8 = m[m.field_size >= 8]
    mins = m8[m8.placed == 1].groupby("race_id")["div"].min().rename("race_min")
    bh = m8[(m8.band != "") & (m8.placed == 1)].join(mins, on="race_id")
    res["B_band_div_is_race_min"] = {"n": int(len(bh)), "is_min": int((bh["div"] <= bh.race_min + 1e-9).sum())}
    # 1.0 配当の馬の単勝オッズ分布
    one = m[np.isclose(m["div"].fillna(-1), 1.0)]
    res["B_div1.0_win_odds"] = one.odds.round(1).value_counts().sort_index().to_dict()
    res["B_div1.0_n_in_rows"] = int(len(one))
    # 帯ごとの配当分布(再現)
    res["B_stage2_dist"] = {}
    for b in ["B1", "B2", "B3"]:
        d = m8[m8.band == b]
        pl = d[d.placed == 1]
        res["B_stage2_dist"][b] = {"n": int(len(d)), "placed": int(len(pl)),
                                   "div1.0": int(np.isclose(pl["div"], 1.0).sum()),
                                   "div1.1": int(np.isclose(pl["div"], 1.1).sum()),
                                   "M": float(pl["div"].mean()),
                                   "R_direct": float(pl["div"].sum() / len(d))}

    # ---- C. 被覆の偏り(2025-01..2025-10 は部分標本) ----
    full = r[(r.race_date >= "2025-01-01") & (r.race_date <= "2026-09-22") & (r.field_size >= 8)]
    fav = full[full.odds_rank == 1].copy()
    fav["covered"] = fav.race_id.isin(cov)
    fav["ym"] = fav.race_date.str[:7]
    early = fav[fav.race_date < "2025-11-01"]
    res["C_cov_early"] = {
        "n_fav_races": int(len(early)), "n_covered": int(early.covered.sum()),
        "fav_placed_rate_covered": float(early[early.covered].placed.mean()),
        "fav_placed_rate_uncovered": float(early[~early.covered].placed.mean()),
        "fav_odds_median_covered": float(early[early.covered].odds.median()),
        "fav_odds_median_uncovered": float(early[~early.covered].odds.median()),
        "band_share_covered": float((early[early.covered].band != "").mean()),
        "band_share_uncovered": float((early[~early.covered].band != "").mean()),
    }
    bandall = full[full.band != ""]
    res["C_band_horses_cov"] = {b: {"n_all": int((bandall.band == b).sum()),
                                    "n_cov": int(((bandall.band == b) & bandall.race_id.isin(cov)).sum()),
                                    "P_all": float(bandall[bandall.band == b].placed.mean()),
                                    "P_cov": float(bandall[(bandall.band == b) & bandall.race_id.isin(cov)].placed.mean())}
                                for b in ["B1", "B2", "B3"]}

    # ---- D. 親集合比較の頑健性 ----
    par = m8[m8.odds_rank == 1].copy()
    par["pay"] = np.where(par.placed == 1, par["div"].fillna(0.0), 0.0)
    par["one"] = 1.0
    # 1 番人気の複勝回収率の勾配(オッズ帯別)
    bins = [0, 1.25, 1.35, 1.55, 1.95, 2.45, 2.95, 3.95, 100]
    labs = ["<=1.2", "1.3", "1.4-1.5", "1.6-1.9", "2.0-2.4", "2.5-2.9", "3.0-3.9", ">=4.0"]
    par["ob"] = pd.cut(par.odds, bins=bins, labels=labs)
    g = par.groupby("ob", observed=True).agg(n=("one", "sum"), placed=("placed", "sum"), pay=("pay", "sum"))
    g["roi"] = g.pay / g.n
    g["P"] = g.placed / g.n
    res["D_fav_place_roi_gradient"] = {str(k): {"n": int(v.n), "P": round(float(v.P), 4), "roi": round(float(v.roi), 4)}
                                       for k, v in g.iterrows()}
    # B3 - rest の開催日ブロック bootstrap(差の比率 CI)
    days = sorted(par.race_date.unique())

    def per_day(df, col):
        return df.groupby("race_date")[col].sum().reindex(days, fill_value=0.0).to_numpy(float)

    out = {}
    for b in ["B1", "B2", "B3"]:
        lab = par.band == b
        bt = race_block_ratio_bootstrap_ci_v1(
            np.vstack([per_day(par[lab], "pay"), per_day(par[~lab], "pay")]),
            np.vstack([per_day(par[lab], "one"), per_day(par[~lab], "one")]), days, b=B, seed=7)
        diff = bt.replicates[0] - bt.replicates[1]
        diff = diff[np.isfinite(diff)]
        out[b] = {"diff_point": float(bt.point[0] - bt.point[1]),
                  "diff_ci95": [float(np.percentile(diff, 2.5)), float(np.percentile(diff, 97.5))],
                  "share_reps_le0": float((diff <= 0).mean())}
    res["D_band_minus_rest_dayblock"] = out
    # B3 vs 隣接帯 1.6-1.9(FL 勾配の連続性だけで説明できるか)
    nb = par[par.ob == "1.6-1.9"]
    b3 = par[par.band == "B3"]
    rng = np.random.default_rng(11)
    pool = np.concatenate([b3.pay.to_numpy(), nb.pay.to_numpy()])
    yrs = np.concatenate([b3.year.to_numpy(), nb.year.to_numpy()])
    lab = np.r_[np.ones(len(b3), bool), np.zeros(len(nb), bool)]
    obs = pool[lab].mean() - pool[~lab].mean()
    ge = 0
    idx = {y: np.flatnonzero(yrs == y) for y in np.unique(yrs)}
    cnt = {y: int(lab[ix].sum()) for y, ix in idx.items()}
    for _ in range(B):
        l2 = np.zeros(len(pool), bool)
        for y, ix in idx.items():
            l2[rng.choice(ix, size=cnt[y], replace=False)] = True
        ge += pool[l2].mean() - pool[~l2].mean() >= obs - 1e-12
    res["D_B3_vs_1.6-1.9"] = {"n_b3": int(len(b3)), "n_nb": int(len(nb)), "roi_b3": float(b3.pay.mean()),
                              "roi_nb": float(nb.pay.mean()), "diff": float(obs), "p_one_sided": (1 + ge) / (B + 1)}
    # 〜2026-06-26 に限った B3 vs rest(帯割り当てが確定オッズの期間)
    pe = par[par.race_date <= "2026-06-26"]
    res["D_B3_vs_rest_le0626"] = {"n_b3": int((pe.band == "B3").sum()),
                                  "roi_b3": float(pe[pe.band == "B3"].pay.mean()),
                                  "roi_rest": float(pe[pe.band != "B3"].pay.mean())}
    # 帯別の年別 R_direct(年集中の確認)
    res["D_year"] = {b: par[par.band == b].groupby("year").agg(n=("one", "sum"), roi=("pay", "mean")).round(4)
                     .to_dict(orient="index") for b in ["B1", "B2", "B3"]}

    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
