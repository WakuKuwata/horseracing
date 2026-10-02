"""注目条件 S1〜S5(specs/138-attention-conditions)の凍結統計を書き出す(2026-10-01・T005 で本番 15 本用に改修)。

入力: artifacts/market_ev/rows_2007.parquet
      + ENS_RUNS(本番 15 本 = --threads 1 --deterministic --tag _ens15 の walk-forward 予測)
      + SINGLE_RUN(S5 = 137 の単 seed 系列 mev-binary-v2 の walk-forward 予測)。
出力: --out(既定 artifacts/market_ev/mev-ens15-v1/rules_S1_S5_freeze.json)。specs/138-attention-conditions/evidence/ に
      コピーしてコミットする(plan 0.1)。

選定 = horseracing_eval.attention_rules.match_mask(唯一の実装・本スクリプトに閾値や帯を書かない)。
精算 = 確定オッズ × 100 円・同着レース除外・2010 年以降。
区間と p = horseracing_eval.bootstrap.race_block_ratio_bootstrap_ci_v1(開催日クラスタ・b=20000・seed=20260905・
      block universe = その rule の選定が 1 点以上ある開催日・日付キーは race_date の ISO 文字列の昇順)と、
      同じ replicates からの centered_one_sided_p_from_replicates(CI と p が同じ抽選)。
価格ずれ試験(代理)= p̂ 固定・選定だけ odds×exp(N(0,σ))・精算は保存オッズ・seed 100..109 の 10 反復平均。
較正(analyze C1・憲法 III)= ens15 と単 seed の全馬 LogLoss・ECE・信頼度区分、rule ごとの選ばれた馬の対比、
      事前登録の採否条件(plan D15)。

研究 fit の暫定版(evidence/rules_S1_S5_freeze_research_provisional.json)は T005 以前の版(scripts/roi_explore/
evaluate.day_bootstrap・B=10000・seed 20260923)で作った。研究の evaluate.day_bootstrap は過去の研究の数値を再現
するためそのまま残し、凍結値だけを eval の単一実装で出す(plan D2 の要件=画面の数値は製品が再現できる実装で出す)。

    cd training && uv run python ../scripts/roi_explore/freeze_rules_S1_S5_20261001.py [--out ABS]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess

import numpy as np
import pandas as pd
from horseracing_eval import attention_rules as ar
from horseracing_eval.bootstrap import centered_one_sided_p_from_replicates, race_block_ratio_bootstrap_ci_v1
from horseracing_eval.metrics import ece_equal_mass, ece_label

ROOT = pathlib.Path(__file__).resolve().parents[2]
ROWS = ROOT / "artifacts" / "market_ev" / "rows_2007.parquet"
RES = ROOT / "artifacts" / "roi_explore" / "results"
ENS_RUNS = {1: "armC_binary_drop-sameday+weightlive_from2007_ens15"} | {
    s: f"armC_binary_seed{s}_drop-sameday+weightlive_from2007_ens15" for s in range(2, 16)}
SINGLE_RUN = "armC_binary_drop-sameday+weightlive_serving_v2_2007"  # = mev-binary-v2(137・parity 済み)
DEFAULT_OUT = ROOT / "artifacts" / "market_ev" / "mev-ens15-v1" / "rules_S1_S5_freeze.json"
WINDOWS = {"ALL": (2010, 2026), "C": (2019, 2026)}
BOOT = {"impl": "horseracing_eval.bootstrap.race_block_ratio_bootstrap_ci_v1", "b": 20000, "seed": 20260905,
        "block": "race_day", "block_universe": "race-days with >=1 selected bet of the rule in the window",
        "rng": "numpy.default_rng(PCG64)", "day_key_order": "ascending ISO race_date"}
PVALUE = {"method": "centered_one_sided_ratio_bootstrap_v1", "impl": "centered_one_sided_p_from_replicates",
          "center": "pooled_roi_1", "alternative": "greater", "plus_one_correction": True, "same_draws_as_ci": True}
NOISE = {"sigmas": [0.1, 0.2, 0.3], "reps": 10, "seed0": 100}
NOISE_PROVENANCE = {
    "rng": ("numpy.random.default_rng(seed0 + rep), rep = 0..reps-1; one N(0, sigma) draw per row of the evaluated "
            "population (rows_2007.parquet row order, left-joined with the 15+1 prediction files preserving order, "
            "then filtered to race_ok & ~dead_heat & year >= 2010); draws follow that row order "
            "(see evaluated_population.row_order_sha256)"),
    "perturbation": "odds * exp(N(0, sigma))",
    "selection": "attention_rules.match_mask with EV = p_hat * perturbed odds and the odds band on perturbed odds",
    "settlement": "stored odds x 100 yen (unperturbed)",
    "years": ">= 2010",
    "overlap_with_unperturbed": "|perturbed selection AND unperturbed selection| / |perturbed selection|, mean over reps",
    "aggregation": "roi / n / overlap are means over reps",
}
CALIB = {"ece_bins": 10, "reliability_edges": [0.0, 0.02, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 1.0]}
# plan D15(事前登録・2026-10-02): 表示版を単 seed → ens15 に切り替える採否条件。ALL 窓・全馬で判定する。
GATE = {"window": "ALL", "logloss": "ens15 <= single", "ece_equal_mass": "ens15 <= single + 0.001", "tol_ece": 0.001}
ROUND = 6


def sha(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:  # noqa: BLE001 — provenance only
        return "unknown"


def r6(x):
    return None if x is None else round(float(x), ROUND)


def ci_and_p(pay: np.ndarray, days: np.ndarray, sel: np.ndarray) -> dict:
    """Pooled ROI (100-yen stakes), race-day block CI and centred p via eval (block universe = selected days)."""
    idx = np.flatnonzero(sel)
    if idx.size == 0:
        return {"roi": None, "ci95": [None, None], "p_one_sided": None, "n_days": 0}
    frame = pd.DataFrame({"d": days[idx], "pay": pay[idx]})
    g = frame.groupby("d", sort=True)["pay"].agg(["sum", "size"])
    res = race_block_ratio_bootstrap_ci_v1(g["sum"].to_numpy(float), 100.0 * g["size"].to_numpy(float),
                                           list(g.index), block=BOOT["block"], b=BOOT["b"], seed=BOOT["seed"])
    point = float(res.point[0])
    p = centered_one_sided_p_from_replicates(res.replicates[0], point)
    return {"roi": r6(point), "ci95": [r6(res.ci_low[0]), r6(res.ci_high[0])], "p_one_sided": r6(p),
            "n_days": int(len(g))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    out_path = pathlib.Path(args.out)

    raw = pd.read_parquet(ROWS, columns=["race_id", "horse_id", "race_date", "year", "odds", "won",
                                         "days_since_last", "race_ok", "dead_heat"])
    input_rows = {"path": str(ROWS.relative_to(ROOT)), "sha256": sha(ROWS), "row_count": int(len(raw)),
                  "min_date": str(raw.race_date.min()), "max_date": str(raw.race_date.max())}
    d = raw
    ens_inputs = []
    for s, tag in ENS_RUNS.items():
        f = RES / tag / "predictions.parquet"
        v = pd.read_parquet(f)[["race_id", "horse_id", "pred"]].rename(columns={"pred": f"pred{s}"})
        ens_inputs.append({"seed": s, "path": str(f.relative_to(ROOT)), "sha256": sha(f), "row_count": int(len(v))})
        d = d.merge(v, on=["race_id", "horse_id"], how="left", validate="one_to_one")
    f = RES / SINGLE_RUN / "predictions.parquet"
    v = pd.read_parquet(f)[["race_id", "horse_id", "pred"]].rename(columns={"pred": "pred_single"})
    single_input = {"path": str(f.relative_to(ROOT)), "sha256": sha(f), "row_count": int(len(v))}
    d = d.merge(v, on=["race_id", "horse_id"], how="left", validate="one_to_one")
    d = d[d.race_ok & ~d.dead_heat & (d.year >= 2010)].reset_index(drop=True)
    missing = int(d[[f"pred{s}" for s in ENS_RUNS] + ["pred_single"]].isna().any(axis=1).sum())
    if missing:
        raise SystemExit(f"FAIL: {missing} evaluated rows lack a prediction (inputs out of sync)")

    years = d.year.to_numpy()
    odds = d.odds.to_numpy(float)
    won = d.won.to_numpy(bool)
    pay = won * odds * 100.0
    dsl = d.days_since_last.to_numpy(float)
    days = d.race_date.astype(str).str.slice(0, 10).to_numpy()
    # EV = 1 + pred/100 (pred = 100*(p̂*odds - 1)); ens15 = arithmetic mean of the 15 seeds' EV (= odds × mean p̂)
    ens_ev = np.mean([1.0 + d[f"pred{s}"].to_numpy(float) / 100.0 for s in ENS_RUNS], axis=0)
    single_ev = 1.0 + d["pred_single"].to_numpy(float) / 100.0
    src = {"ens15": ens_ev, "single": single_ev}
    p_hat = {k: v / odds for k, v in src.items()}

    def window(lo, hi):
        return (years >= lo) & (years <= hi)

    def mask(defn, o=None):
        if o is None:
            return ar.match_mask(defn, ens_ev=ens_ev, single_ev=single_ev, odds=odds, days_since_last=dsl)
        return ar.match_mask(defn, ens_ev=ens_ev / odds * o, single_ev=single_ev / odds * o, odds=o,
                             days_since_last=dsl)

    def stats(m, lo, hi):
        sel = m & window(lo, hi)
        n = int(sel.sum())
        b = ci_and_p(pay, days, sel)
        tot = pay[sel].sum()
        return {"roi": b["roi"], "ci95": b["ci95"], "p_one_sided": b["p_one_sided"], "n": n,
                "hits": int(won[sel].sum()), "n_days": b["n_days"],
                "max_hit_share": r6(pay[sel].max() / tot) if tot > 0 else None}

    def calibration(p, lo, hi):
        w = window(lo, hi)
        pw = np.clip(p[w], 1e-15, 1 - 1e-15)
        yw = won[w].astype(float)
        edges = CALIB["reliability_edges"]
        rel = []
        for a, b in zip(edges[:-1], edges[1:], strict=True):
            mm = (pw >= a) & ((pw < b) if b < 1.0 else (pw <= b))
            if mm.any():
                rel.append({"p_lo": a, "p_hi": b, "n": int(mm.sum()), "mean_p": r6(pw[mm].mean()),
                            "win_rate": r6(yw[mm].mean())})
        return {"n": int(w.sum()), "logloss": r6(-np.mean(yw * np.log(pw) + (1 - yw) * np.log(1 - pw))),
                "ece_equal_width": r6(ece_label(pw, yw, bins=CALIB["ece_bins"])),
                "ece_equal_mass": r6(ece_equal_mass(pw, yw, bins=CALIB["ece_bins"])["ece"]),
                "mean_p": r6(pw.mean()), "win_rate": r6(yw.mean()), "reliability": rel}

    def selected_calibration(m, name, lo, hi):
        sel = m & window(lo, hi)
        n = int(sel.sum())
        return {"n": n, "mean_p_hat": r6(p_hat[name][sel].mean()), "win_rate": r6(won[sel].mean()),
                "mean_ev": r6(src[name][sel].mean()), "realized_roi": r6(pay[sel].sum() / (100 * n))}

    calib = {k: {wn: calibration(p_hat[k], *wr) for wn, wr in WINDOWS.items()} for k in ("ens15", "single")}
    ga, gs = calib["ens15"][GATE["window"]], calib["single"][GATE["window"]]
    gate = GATE | {"ens15": {"logloss": ga["logloss"], "ece_equal_mass": ga["ece_equal_mass"]},
                   "single": {"logloss": gs["logloss"], "ece_equal_mass": gs["ece_equal_mass"]},
                   "passed": bool(ga["logloss"] <= gs["logloss"]
                                  and ga["ece_equal_mass"] <= gs["ece_equal_mass"] + GATE["tol_ece"])}
    # 注記用(ExpectedReturnNote): 全馬・ens15 EV>1.2(=S3)・単 seed EV>1.2(=S5)。選定は registry 経由
    s3, s5 = mask(ar.definition("S3")), mask(ar.definition("S5"))
    all_horses = {}
    for wn, (lo, hi) in WINDOWS.items():
        w = window(lo, hi)
        all_horses[wn] = {"all": r6(pay[w].sum() / (100 * w.sum())),
                          "ens15_ev_gt_1.2": r6(pay[w & s3].sum() / (100 * (w & s3).sum())),
                          "single_ev_gt_1.2": r6(pay[w & s5].sum() / (100 * (w & s5).sum()))}

    order = (d.race_id.astype(str) + "|" + d.horse_id.astype(str)).str.cat(sep="\n")
    out = {"created": "2026-10-01", "frozen_from": "production ens15 fits (--threads 1 --deterministic)",
           "freeze_script_sha256": sha(pathlib.Path(__file__)), "git_commit": git_commit(),
           "numpy_version": np.__version__, "input_rows": input_rows, "ens_prediction_inputs": ens_inputs,
           "single_prediction_input": single_input,
           "evaluated_population": {"row_count": int(len(d)),
                                    "row_order_sha256": hashlib.sha256(order.encode()).hexdigest()},
           "windows": WINDOWS, "bootstrap": BOOT, "pvalue": PVALUE, "price_noise": NOISE,
           "price_noise_provenance": NOISE_PROVENANCE,
           "rule_set_version": ar.RULE_SET_VERSION, "definitions_sha256": ar.definitions_sha256(),
           "definition_semantics": ar.DEFINITION_SEMANTICS,
           "settlement": "stored_odds_x100_yen; dead-heat races excluded; years>=2010; ens15 = arithmetic mean of 15 seeds' EV",
           "rounding": f"roi/ci/p and calibration values rounded to {ROUND} decimals",
           "calibration_spec": CALIB | {"p_hat": "EV / odds", "population": "race_ok & ~dead_heat & year in window (all horses)"},
           "calibration": calib, "adoption_gate": gate, "all_horses": all_horses, "rules": {}}
    for wn in WINDOWS:
        for name in ("ens15", "single"):
            c = calib[name][wn]
            print(f"calib {name} {wn}: n={c['n']} logloss={c['logloss']:.6f} ece_w={c['ece_equal_width']:.6f} "
                  f"ece_m={c['ece_equal_mass']:.6f}")
    print(f"gate: passed={gate['passed']} ens15={gate['ens15']} single={gate['single']}")
    for defn in ar.RULE_DEFINITIONS:
        name = "ens15" if defn.uses_ensemble else "single"
        m0 = mask(defn)
        rec = {"definition": {"ensemble": name, "ev_gt": defn.ev_gt, "odds_band": list(defn.odds_band) if defn.odds_band else None,
                              "gap_days": list(defn.gap_days) if defn.gap_days else None},
               "ALL": stats(m0, *WINDOWS["ALL"]), "C": stats(m0, *WINDOWS["C"]),
               "selected_calibration": {wn: selected_calibration(m0, name, *wr) for wn, wr in WINDOWS.items()},
               "bets_by_year": {str(y): int((m0 & (years == y)).sum()) for y in range(2010, 2027)},
               "yearly_roi": {str(y): r6(pay[m0 & (years == y)].sum() / (100 * max(1, (m0 & (years == y)).sum())))
                              for y in range(2010, 2027)},
               "price_noise_ALL": {}}
        for sigma in NOISE["sigmas"]:
            rois, ns, ovs = [], [], []
            for rep in range(NOISE["reps"]):
                rng = np.random.default_rng(NOISE["seed0"] + rep)
                o2 = odds * np.exp(rng.normal(0, sigma, len(odds)))
                mm = mask(defn, o2)
                rois.append(pay[mm].sum() / (100 * mm.sum()))
                ns.append(int(mm.sum()))
                ovs.append((mm & m0).sum() / max(1, mm.sum()))
            rec["price_noise_ALL"][str(sigma)] = {"roi": r6(np.mean(rois)), "n": int(np.mean(ns)),
                                                  "overlap_with_unperturbed": round(float(np.mean(ovs)), 3)}
        out["rules"][defn.id] = rec
        a, c = rec["ALL"], rec["C"]
        print(f"{defn.id}: ALL {a['roi']:.4f} {a['ci95']} p={a['p_one_sided']:.4f} n={a['n']} h={a['hits']} | "
              f"C {c['roi']:.4f} {c['ci95']} p={c['p_one_sided']:.4f} n={c['n']} h={c['hits']} | noise "
              + " ".join(f"{k}:{v['roi']:.4f}(n={v['n']},ov={v['overlap_with_unperturbed']:.2f})"
                         for k, v in rec["price_noise_ALL"].items()))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True))
    print("→", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
