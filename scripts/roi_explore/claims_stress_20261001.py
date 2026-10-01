"""別セッションの 480 条件(docs/roi-new-patterns-20260930)を締め上げる(2026-10-01)。

1. 15 本(参考例 12 + 事前選抜 3)の完全再現。
2. 親集合内の層別並べ替え検定: 「文脈条件(休養後3走目・先行馬密度 等)は親集合(ER 閾値 × オッズ帯 × 日数ガード)の
   どの馬を買っても同じ」という帰無で、年×親集合内オッズ四分位の層ごとに同じ頭数を無作為に選び直した ROI の分布を作る。
   的中は超幾何分布で数を引き、どの的中かは層内の的中払戻から無作為(行を並べ替えるのと同値で 1000 倍速い)。
   → 480 本すべてに適用し p 値の分布を出す(帰無なら一様)。多重比較の実効確認。
3. 15 本の近傍(数値条件を 1 段ずらす)の ROI 分布 — 実在の効果は近傍でもなだらか、ノイズは孤立した尖り。
4. 15 本(モデル使用)の seed 安定性 — market_er を別 seed / 3 seed 平均 / rounds 600 の予測に差し替える。

    cd training && uv run python ../scripts/roi_explore/claims_stress_20261001.py
"""
from __future__ import annotations

import copy
import json
import pathlib
import sys
import time

import numpy as np
import pandas as pd

ROOT = pathlib.Path("/Users/kuwatawaku/workspace/horseracing")
sys.path.insert(0, str(ROOT / "scripts" / "roi_explore"))
import explore_new_patterns_20260930 as ev  # noqa: E402
import new_pattern_definitions_20260930 as definitions  # noqa: E402
import new_pattern_features_20260930 as features  # noqa: E402

OUT = ROOT / "artifacts" / "roi_new_patterns_20260930" / "stress_20261001"
RES = ROOT / "artifacts" / "roi_explore" / "results"
ALT_PREDS = {
    "mybase": "armC_binary_drop-sameday+weightlive_serving_v2_2007",
    "seed2": "armC_binary_seed2_drop-sameday+weightlive_from2007",
    "seed3": "armC_binary_seed3_drop-sameday+weightlive_from2007",
    "r600": "armC_binary_drop-sameday+weightlive_from2007_r600",
}
POSTHOC12 = ["new26.market_er_context.er100.third.o20_40", "new26.market_er_context.er120.crowded_back.o20_40",
             "new26.market_er_context.er110.crowded_back.o20_40", "new26.market_er_context.er100.band_shorten.o20_40",
             "new26.market_er_context.er110.third.o8_20", "new26.surface_return.dirt.winner.flop6p.o8_20",
             "new26.market_er_context.er100.crowded_back.o20_40", "new26.age_career_rest.g57_112.a5_6_c5_15.poor.o8_20",
             "new26.surface_return.turf.winner.flop6p.o20_40", "new26.market_er_context.er100.crowded_back.o3_8",
             "new26.market_er_context.er110.crowded_back.o8_20", "new26.rest_cycle.rest_third.last_flop.o20_40"]
PRESELECTED3 = ["new26.market_er_context.er100.third.o8_20", "new26.market_er_context.er100.crowded_back.o8_20",
                "new26.market_er_context.er110.third.o8_20"]
WINDOWS = {"ALL": (2007, 2026), "D": (2007, 2014), "Q": (2015, 2018), "C": (2019, 2022), "R": (2023, 2026), "POST": (2019, 2026)}
GUARD = {"field": "days_since_last", "op": "ge", "value": 14}


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def load_table(fields: set[str]) -> pd.DataFrame:
    raw_columns = ((set(fields) - set(features.NEW_COLUMNS) - {"market_er"}) | set(features.REQUIRED_COLUMNS)
                   | {"race_id", "horse_id", "horse_number", "race_date", "year", "odds", "won", "race_ok", "dead_heat"})
    raw = pd.read_parquet(ev.ROWS, columns=sorted(raw_columns))
    d = features.build_features(raw); del raw
    pred = pd.read_parquet(ev.PRED, columns=["race_id", "horse_id", "pred"])
    d = d.merge(pred, on=["race_id", "horse_id"], how="left", validate="one_to_one")
    for k, tag in ALT_PREDS.items():
        v = pd.read_parquet(RES / tag / "predictions.parquet")[["race_id", "horse_id", "pred"]].rename(columns={"pred": f"pred_{k}"})
        d = d.merge(v, on=["race_id", "horse_id"], how="left", validate="one_to_one")
    d["pred_avg3"] = (d["pred_mybase"] + d["pred_seed2"] + d["pred_seed3"]) / 3.0
    d = d.loc[d.race_ok & ~d.dead_heat].sort_values(["race_date", "race_id", "horse_id"]).reset_index(drop=True)
    d["ret"] = np.where(d.won, d.odds * 100, 0) - 100
    d["market_er"] = 1 + d.pred / 100
    for k in list(ALT_PREDS) + ["avg3"]:
        d[f"er_{k}"] = 1 + d[f"pred_{k}"] / 100
    return d


def is_parent_atom(c: dict, uses_model: bool) -> bool:
    if c == GUARD or c["field"] in ("year", "market_er", "odds"):
        return True
    if uses_model and c["field"] == "days_since_last" and c["op"] == "le" and c["value"] == 112:
        return True
    return False


def roi_of(mask: np.ndarray, pay: np.ndarray) -> tuple[float, int, int]:
    n = int(mask.sum())
    return (float(pay[mask].sum() / (100.0 * n)) if n else float("nan")), n, int((pay[mask] > 0).sum())


def strata_labels(parent: np.ndarray, year: np.ndarray, odds: np.ndarray, nq: int = 4) -> np.ndarray:
    """年 × 親集合内オッズ四分位。親集合外は -1。"""
    lab = np.full(len(parent), -1, dtype=np.int64)
    idx = np.flatnonzero(parent)
    if len(idx) == 0:
        return lab
    qs = np.quantile(odds[idx], np.linspace(0, 1, nq + 1)[1:-1])
    ob = np.searchsorted(qs, odds[idx], side="right")
    lab[idx] = year[idx] * 10 + ob
    return lab


def perm_null(parent: np.ndarray, subset: np.ndarray, pay: np.ndarray, lab: np.ndarray, *, b: int, seed: int) -> dict:
    """層別・超幾何サンプリングによる帰無 ROI 分布(subset ⊂ parent・層内で同じ頭数を無作為抽出)。"""
    rng = np.random.default_rng(seed)
    k_tot = int(subset.sum())
    if k_tot == 0:
        return {"p_one_sided": float("nan"), "null_mean": float("nan"), "n": 0}
    obs = pay[subset].sum()
    tot = np.zeros(b)
    for s in np.unique(lab[parent]):
        in_s = (lab == s) & parent
        n_s = int(in_s.sum()); k_s = int((in_s & subset).sum())
        if k_s == 0:
            continue
        hits = pay[in_s & (pay > 0)]
        H = len(hits)
        if H == 0:
            continue
        h = rng.hypergeometric(H, n_s - H, k_s, size=b)  # 的中数
        # どの的中か: H 個の的中払戻を b 通り並べ替え、先頭 h 個の和
        keys = rng.random((b, H)); order = np.argsort(keys, axis=1)
        cs = np.concatenate([np.zeros((b, 1)), np.cumsum(hits[order], axis=1)], axis=1)
        tot += cs[np.arange(b), h]
    null_roi = tot / (100.0 * k_tot)
    obs_roi = obs / (100.0 * k_tot)  # BUG FIX 2026-10-01: compare ROI with ROI (first run compared ROI with a payout SUM → p≡1/(b+1))
    return {"p_one_sided": float((1 + np.sum(null_roi >= obs_roi)) / (b + 1)), "null_mean": float(null_roi.mean()),
            "null_sd": float(null_roi.std()), "null_q95": float(np.quantile(null_roi, 0.95)), "n": k_tot,
            "obs_roi": float(obs_roi), "p_null_ge1": float(np.mean(null_roi >= 1.0)),
            "null_q05": float(np.quantile(null_roi, 0.05)), "null_q50": float(np.quantile(null_roi, 0.5))}


def neighbours(p: dict) -> list[dict]:
    """数値条件を 1 段ずらした近傍パターン(名前つき)。"""
    out = []
    conds = p["conditions"]
    band = {"odds": [(3, 8), (8, 20), (20, 40)]}
    # odds band variants: adjacent frozen bands + half-shifted bands
    lo = next(c["value"] for c in conds if c["field"] == "odds" and c["op"] == "ge")
    hi = next(c["value"] for c in conds if c["field"] == "odds" and c["op"] == "lt")
    variants = {"odds_lower_half": (lo * 0.75, hi * 0.75), "odds_upper_half": (lo * 1.25, hi * 1.25),
                "odds_wider": (lo * 0.8, hi * 1.25), "odds_narrow": (lo * 1.1, hi * 0.9)}
    for name, (a, bb) in variants.items():
        q = copy.deepcopy(p); q["conditions"] = [c for c in conds if c["field"] != "odds"] + [
            {"field": "odds", "op": "ge", "value": float(a)}, {"field": "odds", "op": "lt", "value": float(bb)}]
        out.append({"name": name, "pattern": q})
    steps = {"market_er": 0.1, "days_since_last": 14, "field_size": 1, "age": 1, "career_starts": 2, "prev_finish": 1,
             "distband_starts": 1, "surface_starts": 1, "pace_front_count": 1, "pace_front_share": 1 / 12, "dist_change": 100,
             "pace_known_count": 1, "distband_wins": 1, "surface_wins": 1}
    for i, c in enumerate(conds):
        if c == GUARD or c["field"] in ("year", "odds") or c["op"] not in ("ge", "gt", "le", "lt"):
            continue
        st = steps.get(c["field"])
        if st is None:
            continue
        for sgn, tag in ((-1, "minus"), (1, "plus")):
            q = copy.deepcopy(p); q["conditions"][i] = {**c, "value": c["value"] + sgn * st}
            out.append({"name": f"{c['field']}:{c['op']}{c['value']}->{c['value'] + sgn * st}", "pattern": q})
    return out


def eval_pattern(arr: dict, pat: dict, pay: np.ndarray, year: np.ndarray, cache: dict) -> dict:
    mask = ev.select(arr, pat, cache)
    uses_model = any(c["field"] == "market_er" for c in pat["conditions"])
    rec = {}
    for w, (lo, hi) in WINDOWS.items():
        lo2 = max(lo, 2010) if uses_model else lo
        m = mask & (year >= lo2) & (year <= hi)
        roi, n, h = roi_of(m, pay); rec[w] = {"roi": roi, "n": n, "hits": h}
    return rec


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    pats = definitions.patterns(); byid = {p["id"]: p for p in pats}
    fields = {c["field"] for p in pats for c in p["conditions"]} | {"prev_gap_days", "prev2_gap_days", "rest_second"}
    d = load_table(fields)
    log(f"table {d.shape} / years {d.year.min()}..{d.year.max()}")
    year = d["year"].to_numpy(); odds = d["odds"].to_numpy(float); pay = (d["ret"].to_numpy(float) + 100.0)
    arr = {f: d[f].to_numpy() for f in fields | {"race_id"}}
    for k in list(ALT_PREDS) + ["avg3"]:
        arr[f"er_{k}"] = d[f"er_{k}"].to_numpy()
    cache: dict = {}

    # ---- 1+2: all 480 — replicate ALL roi, and stratified permutation p within parent (ALL and POST)
    log("480 patterns: replicate + permutation null")
    all480 = []
    for i, p in enumerate(pats):
        uses_model = any(c["field"] == "market_er" for c in p["conditions"])
        sub = ev.select(arr, p, cache)
        par_pat = {**p, "conditions": [c for c in p["conditions"] if is_parent_atom(c, uses_model)]}
        par = ev.select(arr, par_pat, cache)
        assert not (sub & ~par).any()
        rec = {"id": p["id"], "family": p["family"], "uses_model": uses_model, "windows": {}}
        for w in ("ALL", "POST", "D", "Q", "C", "R"):
            lo, hi = WINDOWS[w]; lo = max(lo, 2010) if uses_model else lo
            inw = (year >= lo) & (year <= hi)
            P, S = par & inw, sub & inw
            roi, n, h = roi_of(S, pay); proi, pn, ph = roi_of(P, pay)
            rec["windows"][w] = {"roi": roi, "n": n, "hits": h, "parent_roi": proi, "parent_n": pn}
            if w in ("ALL", "POST") and n > 0:
                lab = strata_labels(P, year, odds)
                rec["windows"][w]["perm"] = perm_null(P, S, pay, lab, b=2000, seed=20261001 + i)
        all480.append(rec)
        if (i + 1) % 60 == 0:
            log(f"  {i + 1}/480 ({time.time() - t0:.0f}s)")
    (OUT / "all480_perm.json").write_text(json.dumps(all480, ensure_ascii=False, indent=1))
    pv = np.array([r["windows"]["ALL"]["perm"]["p_one_sided"] for r in all480 if "perm" in r["windows"]["ALL"]])
    pv_post = np.array([r["windows"]["POST"]["perm"]["p_one_sided"] for r in all480 if "perm" in r["windows"]["POST"]])
    summ = {"n_with_p": int(len(pv)),
            "ALL": {"p_lt_0.05": int((pv < 0.05).sum()), "p_lt_0.01": int((pv < 0.01).sum()), "p_lt_0.001": int((pv < 0.001).sum()),
                    "expected_0.05": round(0.05 * len(pv), 1), "expected_0.01": round(0.01 * len(pv), 1), "min_p": float(pv.min())},
            "POST": {"p_lt_0.05": int((pv_post < 0.05).sum()), "p_lt_0.01": int((pv_post < 0.01).sum()), "min_p": float(pv_post.min())},
            "roi_ge1_ALL": int(sum(1 for r in all480 if (r["windows"]["ALL"]["roi"] or 0) >= 1.0)),
            "roi_ge1_n1000_ALL": int(sum(1 for r in all480 if (r["windows"]["ALL"]["roi"] or 0) >= 1.0 and r["windows"]["ALL"]["n"] >= 1000)),
            "parent_roi_ge1_ALL": int(sum(1 for r in all480 if (r["windows"]["ALL"]["parent_roi"] or 0) >= 1.0))}
    # Holm over 480 (ALL) with the 2000-rep resolution
    order = np.argsort(pv); m = len(pv); holm_rej = 0
    for rank, j in enumerate(order):
        if pv[j] <= 0.025 / (m - rank):
            holm_rej += 1
        else:
            break
    summ["holm_rejections_ALL"] = holm_rej
    # expected counts under the within-parent null (sum of per-pattern P(null roi>=1); patterns sharing a parent are correlated → approximation)
    summ["expected_roi_ge1_ALL_under_null"] = round(sum(r["windows"]["ALL"]["perm"]["p_null_ge1"] for r in all480 if "perm" in r["windows"]["ALL"]), 1)
    summ["expected_roi_ge1_n1000_ALL_under_null"] = round(sum(r["windows"]["ALL"]["perm"]["p_null_ge1"] for r in all480
                                                            if "perm" in r["windows"]["ALL"] and r["windows"]["ALL"]["n"] >= 1000), 1)
    summ["n_patterns_n1000_ALL"] = int(sum(1 for r in all480 if r["windows"]["ALL"]["n"] >= 1000))
    summ["expected_roi_ge1_POST_under_null"] = round(sum(r["windows"]["POST"]["perm"]["p_null_ge1"] for r in all480 if "perm" in r["windows"]["POST"]), 1)
    summ["roi_ge1_POST"] = int(sum(1 for r in all480 if (r["windows"]["POST"]["roi"] or 0) >= 1.0))
    (OUT / "summary_480.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1))
    log(json.dumps(summ, ensure_ascii=False))

    # ---- 3+4: the 15 claimed patterns — neighbours + seeds + tail
    log("15 claimed patterns: neighbours + seed stability")
    claims = {}
    for pid in POSTHOC12 + PRESELECTED3:
        p = byid[pid]
        rec = {"kind": "posthoc12" if pid in POSTHOC12 else "preselected3", "label": p["label"], "own": eval_pattern(arr, p, pay, year, cache)}
        # permutation (finer B) on ALL & POST
        uses_model = any(c["field"] == "market_er" for c in p["conditions"])
        sub = ev.select(arr, p, cache)
        par = ev.select(arr, {**p, "conditions": [c for c in p["conditions"] if is_parent_atom(c, uses_model)]}, cache)
        rec["perm"] = {}
        for w in ("ALL", "POST", "C", "R"):
            lo, hi = WINDOWS[w]; lo = max(lo, 2010) if uses_model else lo
            inw = (year >= lo) & (year <= hi)
            rec["perm"][w] = perm_null(par & inw, sub & inw, pay, strata_labels(par & inw, year, odds), b=20000, seed=7)
        # neighbours
        rec["neighbours"] = []
        for nb in neighbours(p):
            r = eval_pattern(arr, nb["pattern"], pay, year, cache)
            rec["neighbours"].append({"name": nb["name"], "ALL": r["ALL"], "POST": r["POST"]})
        # rest_third variants (recomputed from gap columns) for rest patterns
        if any(c["field"] == "rest_third" for c in p["conditions"]):
            g1 = d["prev_gap_days"].to_numpy(float); g2 = d["prev2_gap_days"].to_numpy(float); g0 = d["days_since_last"].to_numpy(float)
            for name, (lay, lo_w, hi_w) in {"layoff70": (70, 14, 56), "layoff98": (98, 14, 56), "win14_70": (84, 14, 70),
                                              "win21_56": (84, 21, 56), "win14_42": (84, 14, 42)}.items():
                flag = (g2 >= lay) & (g1 >= lo_w) & (g1 <= hi_w) & (g0 >= lo_w) & (g0 <= hi_w)
                arr["_rest_var"] = flag
                q = copy.deepcopy(p); q["conditions"] = [({"field": "_rest_var", "op": "eq", "value": True} if c["field"] == "rest_third" else c) for c in q["conditions"]]
                r = eval_pattern(arr, q, pay, year, {})
                rec["neighbours"].append({"name": f"rest_third:{name}", "ALL": r["ALL"], "POST": r["POST"]})
            arr.pop("_rest_var", None)
        # seed stability (model patterns)
        if uses_model:
            rec["seeds"] = {}
            for k in ["mybase", "seed2", "seed3", "avg3", "r600"]:
                q = copy.deepcopy(p); q["conditions"] = [({**c, "field": f"er_{k}"} if c["field"] == "market_er" else c) for c in q["conditions"]]
                # eval_pattern's model detection looks for market_er → force lo 2010 via year atom (present)
                r = eval_pattern(arr, q, pay, year, cache)
                rec["seeds"][k] = {w: r[w] for w in ("ALL", "POST", "C", "R")}
        claims[pid] = rec
    (OUT / "claims15.json").write_text(json.dumps(claims, ensure_ascii=False, indent=1))
    # compact print
    for pid, rec in claims.items():
        o = rec["own"]
        nb_all = [x["ALL"]["roi"] for x in rec["neighbours"] if x["ALL"]["n"] >= 300]
        nb_post = [x["POST"]["roi"] for x in rec["neighbours"] if x["POST"]["n"] >= 150]
        print(f"\n## {pid} ({rec['kind']})\n   own ALL {o['ALL']['roi']:.3f} n={o['ALL']['n']} | POST {o['POST']['roi']:.3f} n={o['POST']['n']} | C {o['C']['roi']:.3f} | R {o['R']['roi']:.3f}")
        pa = rec["perm"]["ALL"]; pp = rec["perm"]["POST"]
        print(f"   perm-within-parent: ALL p={pa['p_one_sided']:.4f} (null mean {pa['null_mean']:.3f}, q95 {pa['null_q95']:.3f}) | POST p={pp['p_one_sided']:.4f} (null mean {pp['null_mean']:.3f}, q95 {pp['null_q95']:.3f}) | C p={rec['perm']['C']['p_one_sided']:.3f} R p={rec['perm']['R']['p_one_sided']:.3f}")
        if nb_all:
            print(f"   neighbours ALL: n={len(nb_all)} median {np.median(nb_all):.3f} min {min(nb_all):.3f} max {max(nb_all):.3f} | POST median {np.median(nb_post) if nb_post else float('nan'):.3f}")
            worst = sorted(rec["neighbours"], key=lambda x: x["ALL"]["roi"] if x["ALL"]["n"] >= 300 else 9)[:3]
            print("   lowest neighbours:", "; ".join(f"{x['name']} {x['ALL']['roi']:.3f}(n={x['ALL']['n']})" for x in worst))
        if "seeds" in rec:
            print("   seeds ALL/POST:", " | ".join(f"{k} {v['ALL']['roi']:.3f}/{v['POST']['roi']:.3f} (n={v['ALL']['n']})" for k, v in rec["seeds"].items()))
    log(f"done in {time.time() - t0:.0f}s → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
