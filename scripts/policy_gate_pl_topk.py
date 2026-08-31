"""064 T029: production pl_topk での win policy ゲート(オッズ上限を既定 ON にする条件)。

064 が事前登録した経路: 「odds-cap の既定 ON は production pl_topk ゲート合格後」。
当時は binary proxy(19-fold・ADOPTED=True)までで、pl_topk faithful は十数時間の
学習ジョブが必要なため T029 未実施のまま止まっていた。

**feature 104 の副産物 = 本番忠実 arm E(pl_topk / rounds 900 / OOF isotonic / wmask)の
walk-forward OOF 予測 26,410 レース(2019-2026・strict OOS)** を使えばこれが学習ゼロで測れる。

政策(064 と同じ・事前固定)
--------------------------
  ev_all   EV = p×odds >= 1.0 の全馬・均等額(現行の既定 = cap なし)
  ev_cap21 同上 かつ odds < 21
  cap_all  odds < 21 の全馬(モデル不使用の対照)
  favorite 各レース最低オッズ 1 頭
  (no-bet = 1.00 は定義)

判定規則(実行前に固定)
----------------------
  既定 ON の条件 = paired(ev_cap21 − ev_all)の開催日クラスタ bootstrap CI 下限 > 0
                   かつ 年別で悪化する年が無い(evidence-of-harm 方式)
  合格 → 製品の recommend 経路の既定を win_odds_cap=21 に変更する
  参考の妥当性チェック: ev_cap21 ≈ 0.81-0.82(proxy 実測)・ev_all ≈ 0.72

    cd training && uv run python ../scripts/policy_gate_pl_topk.py
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
CAP = 21.0


def load(dump: str) -> pd.DataFrame:
    d = pd.read_parquet(dump)[["race_id", "race_date", "horse_id", "win"]]
    with create_engine(DB).connect() as c:
        o = pd.read_sql(text(
            "select rh.race_id, rh.horse_id, rh.odds, "
            "(rr.result_status='finished' and rr.finish_order=1)::int as is_win "
            "from race_horses rh left join race_results rr using(race_id, horse_id) "
            "where rh.entry_status='started'"), c)
    d = d.merge(o, on=["race_id", "horse_id"], how="left")
    d = d[d["odds"].notna()]
    d = d[d.groupby("race_id")["is_win"].transform("sum") == 1].reset_index(drop=True)
    d["ret"] = d["is_win"] * d["odds"]
    d["ev"] = d["win"] * d["odds"]
    return d


def roi_ci(d: pd.DataFrame, mask: np.ndarray, *, b=2000, seed=20260831):
    day = d["race_date"].astype(str).to_numpy()
    days = np.unique(day)
    st = pd.Series(mask.astype(float)).groupby(day).sum().reindex(days).to_numpy()
    rt = pd.Series(np.where(mask, d["ret"], 0.0)).groupby(day).sum().reindex(days).to_numpy()
    point = rt.sum() / st.sum()
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(b):
        ix = rng.integers(0, len(days), len(days))
        s = st[ix].sum()
        vals.append(rt[ix].sum() / s if s else np.nan)
    return point, *np.nanpercentile(vals, [2.5, 97.5]), int(mask.sum())


def paired_ci(d, m_cap, m_all, *, b=2000, seed=20260831):
    """開催日単位で ROI(cap) − ROI(all) を bootstrap(同じ日を両政策で引く = paired)。"""
    day = d["race_date"].astype(str).to_numpy()
    days = np.unique(day)
    def agg(m):
        st = pd.Series(m.astype(float)).groupby(day).sum().reindex(days).to_numpy()
        rt = pd.Series(np.where(m, d["ret"], 0.0)).groupby(day).sum().reindex(days).to_numpy()
        return st, rt
    s1, r1 = agg(m_cap)
    s2, r2 = agg(m_all)
    point = r1.sum() / s1.sum() - r2.sum() / s2.sum()
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(b):
        ix = rng.integers(0, len(days), len(days))
        a, c = s1[ix].sum(), s2[ix].sum()
        if a and c:
            vals.append(r1[ix].sum() / a - r2[ix].sum() / c)
    return point, *np.percentile(vals, [2.5, 97.5])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default="../out/104_active.parquet")
    ap.add_argument("--json", dest="json_out", default="../out/policy_gate_pl_topk.json")
    args = ap.parse_args()

    print("*** 064 T029: production pl_topk win-policy gate ***")
    print("既定 ON の条件(事前固定): paired(ev_cap21−ev_all) CI下限>0 かつ 年別で悪化なし\n")

    d = load(args.dump)
    print(f"{d['race_id'].nunique():,} レース / {len(d):,} 頭 (2019-2026・本番忠実 arm E OOF)\n")

    ev_all = (d["ev"] >= 1.0).to_numpy()
    ev_cap = (ev_all & (d["odds"] < CAP)).to_numpy()
    cap_all = (d["odds"] < CAP).to_numpy()
    fav = (d.groupby("race_id")["odds"].rank(method="first") == 1).to_numpy()

    res = {}
    print(f"{'policy':<10} {'bets':>9} {'ROI':>7} {'CI':>19}")
    for name, m in (("ev_all", ev_all), ("ev_cap21", ev_cap),
                    ("cap_all", cap_all), ("favorite", fav)):
        roi, lo, hi, nb = roi_ci(d, m)
        res[name] = {"roi": roi, "ci_low": lo, "ci_high": hi, "bets": nb}
        print(f"{name:<10} {nb:>9,} {roi:>7.4f} [{lo:>7.4f},{hi:>8.4f}]")

    pt, plo, phi = paired_ci(d, ev_cap, ev_all)
    print(f"\npaired ev_cap21 − ev_all = {pt:+.4f}  CI[{plo:+.4f}, {phi:+.4f}]")

    d["year"] = d["race_date"].astype(str).str[:4]
    yearly = []
    print(f"\n{'年':<6} {'ev_all':>8} {'ev_cap21':>9} {'差':>8}")
    for y, g in d.groupby("year"):
        ma = (g["ev"] >= 1.0).to_numpy()
        mc = (ma & (g["odds"] < CAP)).to_numpy()
        ra = float(np.where(ma, g["ret"], 0).sum() / max(ma.sum(), 1))
        rc = float(np.where(mc, g["ret"], 0).sum() / max(mc.sum(), 1))
        yearly.append({"year": y, "ev_all": ra, "ev_cap": rc, "diff": rc - ra})
        print(f"{y:<6} {ra:>8.4f} {rc:>9.4f} {rc-ra:>+8.4f}")
    n_bad = sum(1 for r in yearly if r["diff"] < 0)

    ok = plo > 0 and n_bad == 0
    print(f"\n=== 判定 ===")
    print(f"  paired CI下限 > 0 : {plo > 0}")
    print(f"  年別悪化なし      : {n_bad == 0} (悪化 {n_bad} 年)")
    print(f"  → {'**合格: 既定 ON へ**' if ok else '不合格: 既定は現状維持'}")

    json.dump({"gate": "064-T029-production-pl-topk", "cap": CAP,
               "policies": res, "paired": {"point": pt, "ci_low": plo, "ci_high": phi},
               "yearly": yearly, "passed": bool(ok)},
              open(args.json_out, "w"), indent=2)
    print(f"  wrote {args.json_out}")


if __name__ == "__main__":
    main()
