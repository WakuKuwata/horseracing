"""feature 104 confirmatory の判定。2 アームの出力だけから verdict を出す。

`confirm_pool_arm.py` が吐いた 2 つの parquet を突き合わせ、100 US1 の証拠 artifact
(`PairedEvidenceArtifact`)を作り、**証拠だけから**点推定・sampling CI・total CI を
再計算して、凍結した gate-config の式を適用する。

符号規約は `candidate − active`(負 = candidate が良い)。

    cd training && uv run python ../scripts/confirm_pool_verdict.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math

import pandas as pd
from horseracing_db.session import create_db_engine
from horseracing_eval.decision import gate_config_hash
from horseracing_eval.evidence import (
    PairedEvidenceArtifact,
    build_rows,
    recompute,
)
from sqlalchemy import text
from sqlalchemy.orm import Session

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
GATE = "../specs/104-historical-data-backfill/gate-config.json"
EPS = 1e-15


def nll(p: float) -> float:
    return -math.log(min(max(float(p), EPS), 1 - EPS))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", default=GATE)
    ap.add_argument("--active", default="../out/104_active.parquet")
    ap.add_argument("--candidate", default="../out/104_candidate.parquet")
    ap.add_argument("--out", default="../specs/104-historical-data-backfill/verdict.json")
    args = ap.parse_args()

    cfg = json.load(open(args.gate))
    frozen = open(args.gate.replace("gate-config.json", "gate-config.hash.txt")).read().strip()
    live = gate_config_hash(cfg)
    if live != frozen:
        raise SystemExit(f"gate-config が凍結後に変わっている: {live} != {frozen}")
    print(f"gate_config_hash {frozen[:16]}… 照合 OK")

    a = pd.read_parquet(args.active)
    c = pd.read_parquet(args.candidate)
    ra, rc = set(a["race_id"]), set(c["race_id"])
    if ra != rc:
        raise SystemExit(f"valid レース集合が違う: active {len(ra):,} / candidate {len(rc):,} "
                         f"(差 {len(ra ^ rc):,})")
    print(f"valid レース {len(ra):,}(両アーム一致)")

    # --- 勝者と 2/3 着以内を DB から取る(結果は判定にのみ使う。特徴には入らない) ---
    with Session(create_db_engine(DB)) as s:
        res = pd.read_sql(text("""
            SELECT rr.race_id, rr.horse_id, rr.finish_order, rr.result_status
            FROM race_results rr WHERE rr.race_id = ANY(:ids)
        """), s.connection(), params={"ids": sorted(ra)})
    fin = res[res["result_status"] == "finished"]
    winners = fin[fin["finish_order"] == 1].groupby("race_id")["horse_id"].apply(list)
    top2 = fin[fin["finish_order"] <= 2].groupby("race_id")["horse_id"].apply(set)
    top3 = fin[fin["finish_order"] <= 3].groupby("race_id")["horse_id"].apply(set)

    a = a.set_index(["race_id", "horse_id"])
    c = c.set_index(["race_id", "horse_id"])
    dates = pd.read_parquet(args.active, columns=["race_id", "race_date"]).drop_duplicates()
    day = dict(zip(dates["race_id"], dates["race_date"].astype(str), strict=True))

    entries, t2, t3, skipped = [], [], [], 0
    for rid in sorted(ra):
        w = winners.get(rid)
        if not w or len(w) != 1:          # 勝者がちょうど 1 頭のレースのみ(winner NLL の定義)
            skipped += 1
            continue
        hid = w[0]
        if (rid, hid) not in a.index or (rid, hid) not in c.index:
            skipped += 1
            continue
        entries.append((rid, day[rid], nll(c.loc[(rid, hid), "win"]),
                        nll(a.loc[(rid, hid), "win"])))
        for tbl, acc, col in ((top2, t2, "top2"), (top3, t3, "top3")):
            st = tbl.get(rid)
            if st and hid in st:
                acc.append(nll(c.loc[(rid, hid), col]) - nll(a.loc[(rid, hid), col]))
    print(f"判定対象 {len(entries):,} レース(除外 {skipped:,} = 勝者不在/同着/予測欠落)")

    art = PairedEvidenceArtifact(
        rows=build_rows(entries),
        bootstrap=cfg["bootstrap"], seed_noise=cfg["seed_noise"],
        evaluation_contract_version=cfg["evaluation_contract_version"],
        gate_config_hash=frozen,
        race_id_set_hash=hashlib.sha256("\n".join(sorted(ra)).encode()).hexdigest(),
        candidate_recipe_hash=json.load(open(args.candidate.replace(".parquet", ".meta.json")))
        ["recipe_hash"],
        active_recipe_hash=json.load(open(args.active.replace(".parquet", ".meta.json")))
        ["recipe_hash"],
        window=cfg["eval_window"], artifact_kind="paired_evidence",
        eligible_for_verdict=True,
    )
    r = recompute(art)
    point = r["point"]
    sci, tci = r["sample_ci"], r["total_ci"]
    print("\n=== PRIMARY: winner NLL (candidate − active) ===")
    print(f"  点推定  {point:+.6f}")
    print(f"  sample  CI[{sci['ci_low']:+.6f}, {sci['ci_high']:+.6f}]  n_days={sci['n_days']}")
    print(f"  total   CI[{tci['ci_low']:+.6f}, {tci['ci_high']:+.6f}]  (seed 分散込み)")

    d2, d3 = (sum(t2) / len(t2) if t2 else 0.0), (sum(t3) / len(t3) if t3 else 0.0)
    ni = cfg["non_inferiority"]
    ok2, ok3 = d2 <= ni["top2_tolerance"], d3 <= ni["top3_tolerance"]
    print("\n=== 非劣性ガード ===")
    print(f"  top2 diff {d2:+.6f}  <= {ni['top2_tolerance']}  → {'PASS' if ok2 else 'FAIL'}")
    print(f"  top3 diff {d3:+.6f}  <= {ni['top3_tolerance']}  → {'PASS' if ok3 else 'FAIL'}")

    delta = cfg["min_effect_delta"]
    sub = {"effect_beats_delta": point <= -delta,
           "ci_upper_below_zero": tci["ci_high"] < 0,
           "top2_noninferior": ok2, "top3_noninferior": ok3}
    adopted = all(sub.values())
    print("\n=== 判定(凍結した式) ===")
    print(f"  {cfg['verdict_formula']}")
    for k, v in sub.items():
        print(f"    {k:<22} {v}")
    print(f"\n  **{'ADOPT' if adopted else 'REJECT'}**")

    json.dump({"gate_config_hash": frozen, "adopted": adopted, "sub_gates": sub,
               "primary": {"point": point, "sample_ci": sci, "total_ci": tci},
               "non_inferiority": {"top2_diff": d2, "top3_diff": d3},
               "n_races": len(entries), "n_skipped": skipped,
               "min_effect_delta": delta, "evidence": art.to_dict()},
              open(args.out, "w"), indent=2)
    print(f"  wrote {args.out}")


if __name__ == "__main__":
    main()
