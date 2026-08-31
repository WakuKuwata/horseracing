"""Stage 2: 発走前 exotic 価格での馬連ポートフォリオ再測定(F1-クリーン)。

旧ポートフォリオ backtest(2,082 レース・全 120 セル unprofitable)の 2 つの穴を塞ぐ:
  D  検出力: 発走前グリッド ∩ 確定配当の 2,605 レース(2025-2026)で再測定
  F1 closing 価格と実行可能価格の混同: **選定は発走前クオート、精算は確定配当**
     = 実際に賭けられる形。旧測定は選定にも closing 寄りオッズを使っていた

モデル p は本番忠実 arm E の walk-forward OOF(feature 104 の副産物・strict OOS)。

政策(事前登録・固定 16 セル)
----------------------------
選定器 4 種 × 政策 4 種(top-1 / top-3 / top-5 / EV>=1.0 全部):
  model   EV = P_model(ij) × O_pre   P_model は p の PL 導出(馬連 = 両順序の和)
  market  EV = P_q(ij) × O_pre      同じ式を devig 勝率 q で(クロスプール乖離の収束に賭ける)
  fav     O_pre 最小の組(人気サイド)
  random  一様(seed 固定)
賭けは全レース・均等額 1。的中 = 選定組 == 確定組。払戻 = 確定配当(パリミュチュエル準拠)。

判定規則(実行前に固定)
----------------------
開催日クラスタ bootstrap 95%CI で:
  CI 下限 > 1.0 → 利益セル候補(**16 セルの多重性があるので別窓の確認が必須**)
  CI 上限 < 1.0 → そのセルの不採算を確定
  それ以外      → NO_DECISION
参考: 馬連の控除は 22.5% なので情報ゼロの床は ≈0.775。

**SCREENING ONLY — can_adopt=false。**

    cd training && uv run python ../scripts/screen_exotic_preprice.py
"""

from __future__ import annotations

import argparse
import itertools
import json

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
POLICIES = ("top1", "top3", "top5", "ev1")
SELECTORS = ("model", "market", "fav", "random")


def quinella_probs(win: dict[int, float]) -> dict[tuple[int, int], float]:
    """PL(Harville)導出: P{i,j} = p_i p_j/(1−p_i) + p_j p_i/(1−p_j)。win は馬番→勝率。"""
    out = {}
    for i, j in itertools.combinations(sorted(win), 2):
        pi, pj = win[i], win[j]
        out[(i, j)] = pi * pj / max(1 - pi, 1e-9) + pj * pi / max(1 - pj, 1e-9)
    return out


def load(dump: str) -> list[dict]:
    eng = create_engine(DB)
    with eng.connect() as c:
        quotes = {r[0]: r[1] for r in c.execute(text(
            "select race_id, quotes from exotic_quotes where bet_type='quinella'"))}
        divs = {}
        for rid, sel, o in c.execute(text(
                "select race_id, selection, odds from exotic_odds where bet_type='quinella'")):
            divs.setdefault(rid, {})[tuple(sorted(sel))] = float(o)
        nums = pd.read_sql(text(
            "select race_id, horse_id, horse_number, odds from race_horses "
            "where entry_status='started'"), c)
    p = pd.read_parquet(dump)[["race_id", "race_date", "horse_id", "win"]]
    m = p.merge(nums, on=["race_id", "horse_id"])
    races = []
    for rid, g in m.groupby("race_id"):
        if rid not in quotes or rid not in divs or len(divs[rid]) != 1:
            continue  # 同着複数払戻は除外(円滑な ROI 定義のため)・件数は報告
        if g["horse_number"].isna().any() or g["odds"].isna().any():
            continue
        pw = dict(zip(g["horse_number"].astype(int), g["win"], strict=True))
        inv = 1.0 / g["odds"].to_numpy()
        qs = dict(zip(g["horse_number"].astype(int), inv / inv.sum(), strict=True))
        grid = {}
        for k, v in quotes[rid].items():
            a, b = k.split("-")
            if v and v[0]:
                grid[(min(int(a), int(b)), max(int(a), int(b)))] = float(v[0])
        if not grid:
            continue
        races.append({"rid": rid, "day": str(g["race_date"].iloc[0]), "p": pw, "q": qs,
                      "grid": grid, "winner": next(iter(divs[rid])),
                      "dividend": divs[rid][next(iter(divs[rid]))]})
    return races


def select(r: dict, selector: str, rng) -> list[tuple[tuple[int, int], float]]:
    """(組, EV っぽいスコア) を降順で返す。grid に価格がある組だけが対象。"""
    combos = list(r["grid"])
    if selector == "fav":
        return sorted(((c, -r["grid"][c]) for c in combos), key=lambda t: -t[1])
    if selector == "random":
        rng.shuffle(combos)
        return [(c, 0.0) for c in combos]
    probs = quinella_probs(r["p"] if selector == "model" else r["q"])
    return sorted(((c, probs.get(c, 0.0) * r["grid"][c]) for c in combos), key=lambda t: -t[1])


def bootstrap_roi(bets_by_day: dict, *, b=2000, seed=20260831):
    days = sorted(bets_by_day)
    stakes = np.array([sum(x[0] for x in bets_by_day[d]) for d in days])
    rets = np.array([sum(x[1] for x in bets_by_day[d]) for d in days])
    point = rets.sum() / stakes.sum()
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(b):
        ix = rng.integers(0, len(days), len(days))
        s = stakes[ix].sum()
        vals.append(rets[ix].sum() / s if s else np.nan)
    lo, hi = np.nanpercentile(vals, [2.5, 97.5])
    return point, float(lo), float(hi)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default="../out/104_active.parquet")
    ap.add_argument("--json", dest="json_out", default="../out/screen_exotic_preprice.json")
    args = ap.parse_args()

    print("*** SCREENING ONLY — can_adopt=false ***")
    print("判定: CI下限>1.0 → 利益セル候補(別窓確認必須) / "
          "CI上限<1.0 → 不採算確定 / 他 NO_DECISION")
    print("参考: 馬連の控除 22.5% → 情報ゼロの床 ≈0.775\n")

    races = load(args.dump)
    n_day = len({r["day"] for r in races})
    print(f"対象 {len(races):,} レース / {n_day} 開催日(発走前グリッド ∩ 確定配当 ∩ OOF p)")
    # F1 の定量: 発走前クオートは確定配当からどれだけ動くか
    moves = [r["grid"][r["winner"]] / r["dividend"] for r in races if r["winner"] in r["grid"]]
    print(f"当たり組の 発走前/確定 オッズ比: 中央値 {np.median(moves):.3f}  "
          f"[10%,90%]=[{np.percentile(moves,10):.2f}, {np.percentile(moves,90):.2f}]  "
          f"(1 から遠いほど closing 選定はズレていた)\n")

    res = []
    print(f"{'selector':<8} {'policy':<6} {'bets':>7} {'hit%':>6} {'ROI':>7} {'CI':>18}  判定")
    for selector in SELECTORS:
        rng = np.random.default_rng(20260831)
        ranked = {r["rid"]: select(r, selector, rng) for r in races}
        for policy in POLICIES:
            by_day: dict[str, list] = {}
            nb = nh = 0
            for r in races:
                rows = ranked[r["rid"]]
                if policy == "ev1":
                    pick = [c for c, ev in rows if ev >= 1.0] if selector in ("model", "market") \
                        else []
                else:
                    pick = [c for c, _ in rows[: int(policy[3:])]]
                for c in pick:
                    hit = c == r["winner"]
                    by_day.setdefault(r["day"], []).append((1.0, r["dividend"] if hit else 0.0))
                    nb += 1
                    nh += int(hit)
            if nb == 0:
                res.append({"selector": selector, "policy": policy, "bets": 0})
                print(f"{selector:<8} {policy:<6} {0:>7}  (賭けなし)")
                continue
            roi, lo, hi = bootstrap_roi(by_day)
            verdict = ("利益セル候補" if lo > 1.0 else
                       "不採算確定" if hi < 1.0 else "NO_DECISION")
            res.append({"selector": selector, "policy": policy, "bets": nb, "hits": nh,
                        "roi": roi, "ci_low": lo, "ci_high": hi, "verdict": verdict})
            print(f"{selector:<8} {policy:<6} {nb:>7,} {100*nh/nb:>5.1f}% {roi:>7.3f} "
                  f"[{lo:>7.3f},{hi:>7.3f}]  {verdict}")

    with open(args.json_out, "w") as fh:
        json.dump({"screening_only": True, "can_adopt": False,
                   "n_races": len(races), "n_days": n_day,
                   "quote_vs_dividend_ratio_median": float(np.median(moves)),
                   "results": res}, fh, indent=2)
    print(f"\nwrote {args.json_out}")


if __name__ == "__main__":
    main()
