"""Kill-test: テン3F(first_3f)が現行レジームで消えたことは、いくらの損失か。

`race_results.first_3f` は JRA-VAN 生 CSV の col55 由来で、2024 年 96.8% → 2025 年 74.4% →
**2026 年 0.0%** と消えた。netkeiba の結果ページには後3F(上がり)しか載らず、テン3F は出ない。
影響を受ける 3 列 `asof_rel_first3f_avg` / `asof_rel_first3f_best` / `asof_pace_balance_avg` は
稼働モデルの split の **3.0%** を占め、充足率は 88% → 46%(2026)に落ちている。

ゼロでなく 46% 残っているのは、as-of 集計が過去走を見るため — 2024 年以前に走ったことのある馬は
当時の値を持ち続ける。つまり劣化は**若い馬に集中**する。

**項目 1(馬主・生産者)と決定的に違うのは、修復コストが大きいこと。** 供給源は `race_laps`(034)
だが 2025/2026 は 0 件・2024 も 1,594 レースしかなく、埋めるにはラップページを約 5,700 レース
分取得し(1req/分で約 4 日)、以後も日次で 1 レース 1 リクエスト増やし続ける必要がある。だから
測ってから決める。

    arm A (real)     : first_3f あり = 復旧後にあるべき状態
    arm B (all NULL) : 全消し = **上限**(学習時 88% 充足に対し 0% は未学習分布)
    arm C (馬単位)   : 実測 2026 の充足率 46% を再現 = **現状**
    arm D (1200m のみ): 恒等式で復元できる分だけ残す = **無料でできる復旧の到達点**

**当初想定した復旧経路は使えなかった。** `race_laps` が持つのは `pace_first_3f` = レース単位の
先頭ペースで、特徴が使う `race_results.first_3f` = **馬ごとのテン3F** とは別物である
(`rel_first3f = 自分のテン3F − そのレースの完走馬平均`)。netkeiba は馬ごとの上がり3F しか出さず、
テン3F は出さない。ラップを取っても per-horse には届かない。

唯一の無料経路は 1200m の恒等式 `first_3f = finish_time − last_3f`。実測で 1200m のみ
187,833 行・平均誤差 **0.0000 秒**で厳密成立し、他の距離(1000/1400/1600/2000m)では 3〜50 秒
ずれてまったく成立しない。2026 の出走行のうち 1200m 以下は 23.4%。arm D はその復元後の定常状態
(履歴も含めて 1200m だけが値を持つ)を測る。

欠損を馬単位に与えるのは、本番の劣化が実際に馬単位で効いているため(2024 以前の出走歴がある馬は
値を持ち、2025 年以降デビューの馬は持たない)。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pickle
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from horseracing_features.builder import build_feature_matrix
from horseracing_training.cond_logit import race_softmax
from horseracing_training.target_encoding import apply_encoded_columns
from sqlalchemy import text
from sqlalchemy.orm import Session

from horseracing_db.session import create_db_engine

OUT = Path(__file__).resolve().parent.parent / "evidence"
CLIP = 1e-6
END = dt.date(2026, 8, 16)
FROM = dt.date(2019, 1, 1)
CURRENT_REGIME = dt.date(2025, 1, 1)
COLS = ("asof_rel_first3f_avg", "asof_rel_first3f_best", "asof_pace_balance_avg")
#: 学習窓 88% -> 2026 実測 46% を再現するには、値を持つ馬の (88-46)/88 = 48% を落とす
MASK_PCT = 48


def _load_model(engine):
    with engine.connect() as c:
        mv, uri = c.execute(text(
            "SELECT model_version, weights_uri FROM model_versions "
            "WHERE adoption_status = 'active'")).one()
    root = Path(uri).parent
    print(f"active = {mv}  ({root})")
    return (mv, lgb.Booster(model_file=str(root / "model.txt")),
            pickle.load(open(root / "preprocessor.pkl", "rb")),
            pickle.load(open(root / "calibrator.pkl", "rb")))


def _predict(rows, sizes, booster, prep, calib):
    cols, cats = list(prep["feature_cols"]), list(prep.get("categorical_cols", []))
    encoders = prep.get("encoders", {})
    rows = rows.copy()
    for c in cats:
        if c in rows.columns:
            rows[c] = rows[c].astype("category")
    for c in [c for c in cols if c not in cats and c not in encoders]:
        rows[c] = pd.to_numeric(rows[c], errors="coerce")
    X = rows[cols].copy()
    if encoders:
        X = apply_encoded_columns(X, {c: e.transform(rows[c]) for c, e in encoders.items()}, cols)
    p = np.clip(np.asarray(calib.transform(race_softmax(
        np.asarray(booster.predict(X), dtype=float), sizes)), dtype=float), CLIP, 1 - CLIP)
    idx = np.repeat(np.arange(len(sizes)), sizes)
    return p / np.bincount(idx, weights=p, minlength=len(sizes))[idx]


def _by_day(feats, p, winners, days):
    out: dict[str, list[float]] = {}
    for rid, g in feats.assign(_p=p).groupby("race_id", sort=True):
        w = winners.get(rid)
        if w is None:
            continue
        hit = g.loc[g["horse_id"] == w, "_p"]
        if not hit.empty:
            out.setdefault(str(days[rid])[:10], []).append(
                -float(np.log(max(float(hit.iloc[0]), CLIP))))
    return out


def _ci(diff_by_day, b=2000, seed=20260820):
    days = sorted(diff_by_day)
    if not days:
        return None
    per = np.array([np.mean(diff_by_day[d]) for d in days])
    wts = np.array([len(diff_by_day[d]) for d in days], dtype=float)
    rng = np.random.default_rng(seed)
    boots = [float(np.sum(per[k] * wts[k]) / wts[k].sum())
             for k in (rng.integers(0, len(days), len(days)) for _ in range(b))]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"point": float(np.sum(per * wts) / wts.sum()), "ci_low": float(lo),
            "ci_high": float(hi), "n_days": len(days), "n_races": int(wts.sum())}


def main() -> int:
    os.environ.setdefault(
        "DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
    OUT.mkdir(parents=True, exist_ok=True)
    engine = create_db_engine()
    mv, booster, prep, calib = _load_model(engine)

    with Session(engine) as s:
        winners = {r.race_id: r.horse_id for r in s.execute(text(
            "SELECT race_id, horse_id FROM race_results "
            "WHERE finish_order = 1 AND result_status = 'finished'"))}
        days = {r.race_id: r.race_date for r in s.execute(
            text("SELECT race_id, race_date FROM races"))}

        per_arm = {}
        try:
            for arm in ("A_real", "B_masked_all", "C_masked_horses", "D_1200m_only"):
                s.rollback()
                if arm == "B_masked_all":
                    n = s.execute(text(
                        "UPDATE race_results SET first_3f = NULL "
                        "WHERE first_3f IS NOT NULL")).rowcount
                    print(f"  arm B: {n:,} 行の first_3f を NULL 化(全消し・未 commit)")
                elif arm == "D_1200m_only":
                    n = s.execute(text("""
                        UPDATE race_results SET first_3f = NULL
                        WHERE first_3f IS NOT NULL
                          AND race_id IN (SELECT race_id FROM races WHERE distance <> 1200)
                    """)).rowcount
                    print(f"  arm D: {n:,} 行を NULL 化(1200m 以外・未 commit)")
                elif arm == "C_masked_horses":
                    n = s.execute(text("""
                        UPDATE race_results SET first_3f = NULL
                        WHERE first_3f IS NOT NULL
                          AND (abs(hashtext(horse_id)) % 100) < :pct
                    """), {"pct": MASK_PCT}).rowcount
                    print(f"  arm C: {n:,} 行を NULL 化({MASK_PCT}%・馬単位・未 commit)")
                feats = build_feature_matrix(s, end_date=END)
                feats = feats[feats["race_id"].map(days).between(FROM, END)]
                feats = feats[feats["race_id"].isin(winners)].sort_values(
                    ["race_id", "horse_id"]).reset_index(drop=True)
                cov = {c: round(float(feats[c].notna().mean()), 3) for c in COLS
                       if c in feats.columns}
                sizes = feats.groupby("race_id", sort=True).size().tolist()
                p = _predict(feats, sizes, booster, prep, calib)
                per_arm[arm] = {"by_day": _by_day(feats, p, winners, days), "coverage": cov}
                print(f"  {arm}: rows={len(feats):,}  充足={cov}")
        finally:
            s.rollback()

    a = per_arm["A_real"]["by_day"]
    out: dict = {}

    def _paired(x: str, y: str):
        """arm x − arm y を対で取る。A からの差を 2 本引き算するのではなく、同じ日を突き合わせて
        1 本の区間にする(引き算では区間が出ない)。"""
        px, py = per_arm[x]["by_day"], per_arm[y]["by_day"]
        d = {k: [u - v for u, v in zip(px[k], py[k])] for k in sorted(set(px) & set(py))}
        return _ci(d)
    print("\n=== diff = A(あり) − arm。負なら「ある方が良い」= 復旧の価値 ===")
    for arm in ("B_masked_all", "C_masked_horses", "D_1200m_only"):
        z = per_arm[arm]["by_day"]
        diff = {d: [x - y for x, y in zip(a[d], z[d])] for d in sorted(set(a) & set(z))}
        full, recent = _ci(diff), _ci({d: v for d, v in diff.items()
                                       if dt.date.fromisoformat(d) >= CURRENT_REGIME})
        out[arm] = {"full": full, "recent": recent}
        tag = {"B_masked_all": "上限(全消し)",
               "C_masked_horses": f"現状({MASK_PCT}% 馬単位)",
               "D_1200m_only": "1200m のみ復元(無料経路の到達点)"}[arm]
        print(f"\n  --- {tag} ---")
        for label, r in (("全窓 2019-2026", full), ("切替後 2025-2026", recent)):
            if r:
                print(f"    {label:<20} {r['point']:+.6f}  "
                      f"CI[{r['ci_low']:+.6f}, {r['ci_high']:+.6f}]  n_races={r['n_races']:,}")
    # 供給停止は恒久なので、いずれ全履歴が停止後になる。その定常状態での比較が本命:
    # 「何も無い(B)」に対して「1200m だけ導出できる(D)」がどれだけ良いか。
    steady = _paired("D_1200m_only", "B_masked_all")
    print("\n=== 定常状態の比較: D(1200m のみ) − B(何も無い)。負なら D が良い ===")
    if steady:
        print(f"    {steady['point']:+.6f}  CI[{steady['ci_low']:+.6f}, {steady['ci_high']:+.6f}]"
              f"  n_races={steady['n_races']:,}")
    out["steady_state_D_minus_B"] = steady

    (OUT / "first3f-killtest.json").write_text(json.dumps(
        {"arms": out, "coverage": {k: v["coverage"] for k, v in per_arm.items()},
         "model": mv, "window": [str(FROM), str(END)], "mask_pct": MASK_PCT}, indent=2))
    print(f"\n  wrote {OUT/'first3f-killtest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
