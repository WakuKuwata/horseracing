"""US2(control variate)の足切り指標 ρ² を、既存の永続化予測から先に測る。

paired 差 d_r = (候補の winner NLL) − (基準の winner NLL) に対して、事前登録候補の共変量が
どれだけ説明力を持つかを見る。CUPED 型の分散削減率は 1−R² なので、R² が小さければ US2 は
契約を複雑にするだけの徒労になる。**spec を実装に移す前にここで足切りする。**

限界: ここで使う 2 アームは lgbm-042 と lgbm-058-acc で、通常の候補/基準ペア(特徴 1 群だけ
違う)よりはるかに離れている。離れたペアほど差の分散は大きく、共変量で説明できる余地も
違いうるので、**この ρ² はそのままの数値としては転用できない**。「桁として望みがあるか」を
見るための先行測定である。

    cd training && uv run python ../scripts/cv_rho_probe.py
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
ARM_CAND, ARM_ACT = "lgbm-042", "lgbm-058-acc"
EPS = 1e-15


def load(mv: str) -> pd.DataFrame:
    eng = create_engine(DB)
    with eng.connect() as c:
        return pd.read_sql(text("""
            SELECT pru.race_id, rp.horse_id, rp.win_prob AS p, r.race_date,
                   rh.odds,
                   (rr.result_status='finished' AND rr.finish_order=1)::int AS is_win
            FROM prediction_runs pru
            JOIN race_predictions rp USING (prediction_run_id)
            JOIN races r ON r.race_id = pru.race_id
            JOIN race_horses rh ON rh.race_id=pru.race_id AND rh.horse_id=rp.horse_id
            LEFT JOIN race_results rr ON rr.race_id=pru.race_id AND rr.horse_id=rp.horse_id
            WHERE pru.model_version=:mv AND rh.entry_status='started'
        """), c, params={"mv": mv})


def winner_nll(d: pd.DataFrame) -> pd.Series:
    """レース内で p を再正規化してから勝ち馬の −log p。1 レース 1 標本。"""
    s = d.groupby("race_id")["p"].transform("sum")
    q = (d["p"] / s).clip(EPS, 1 - EPS)
    w = d[d["is_win"] == 1]
    return (-np.log(q[d["is_win"] == 1])).groupby(w["race_id"]).first()


def main() -> None:
    cand, act = load(ARM_CAND), load(ARM_ACT)
    for f in (cand, act):
        keep = f.groupby("race_id")["is_win"].transform("sum") == 1
        f.drop(f.index[~keep], inplace=True)

    nc, na = winner_nll(cand), winner_nll(act)
    common = nc.index.intersection(na.index)
    d = pd.DataFrame({"cand": nc.loc[common], "act": na.loc[common]})
    d["diff"] = d["cand"] - d["act"]

    # --- 事前登録候補の共変量(いずれも結果を読まない) ---
    meta = (act[act["race_id"].isin(common)]
            .groupby("race_id")
            .agg(field_size=("horse_id", "size"),
                 race_date=("race_date", "first"),
                 n_odds=("odds", "count")))
    inv = act.assign(inv=1.0 / act["odds"].astype(float).replace(0, np.nan))
    tot = inv.groupby("race_id")["inv"].transform("sum")
    inv["q"] = inv["inv"] / tot
    ent = inv.groupby("race_id")["q"].apply(
        lambda s: float(-(s.dropna() * np.log(s.dropna())).sum()) if s.notna().all() else np.nan)
    qmax = inv.groupby("race_id")["q"].max()

    d = d.join(meta).assign(
        mkt_entropy_norm=(ent / np.log(meta["field_size"])).reindex(d.index),
        mkt_qmax=qmax.reindex(d.index),
        act_loss=d["act"],
    )
    d = d[d["n_odds"] == d["field_size"]].dropna(
        subset=["diff", "field_size", "mkt_entropy_norm", "mkt_qmax", "act_loss"])

    print(f"races={len(d)}  mean diff={d['diff'].mean():+.6f}  sd={d['diff'].std():.6f}")
    print(f"アーム相関 corr(cand, act) = {d['cand'].corr(d['act']):.4f}\n")

    covs = ["act_loss", "field_size", "mkt_entropy_norm", "mkt_qmax"]
    print("単変量 ρ(共変量, paired 差):")
    for c in covs:
        r = d["diff"].corr(d[c])
        print(f"  {c:20s} ρ={r:+.4f}   ρ²={r*r:.5f}  → 分散削減 {100*r*r:.2f}%")

    X = np.column_stack([np.ones(len(d))] + [d[c].to_numpy(float) for c in covs])
    y = d["diff"].to_numpy(float)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    r2 = 1 - resid.var(ddof=0) / y.var(ddof=0)
    print(f"\n多変量 R² = {r2:.5f}  → CI 幅の削減率 ≈ {100*(1-np.sqrt(max(1-r2,0))):.2f}%")
    print(f"  (paired 差の sd {y.std():.6f} → 調整後 {resid.std():.6f})")


if __name__ == "__main__":
    main()
