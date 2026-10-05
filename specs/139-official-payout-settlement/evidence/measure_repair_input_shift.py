"""Non-saving measurement of the repair's input shift (139 T014 steps 1/5). Writes NOTHING to the DB.
mode=pre : compute ens15/single EV + S1..S5 membership for the target races on the current DB, save.
mode=post: same on the repaired DB, with the TARGET races' own odds/popularity overridden by the pre
           values (isolates the shift that comes only through past-race inputs); also the raw post.
"""
import sys, json, pathlib
import pandas as pd, numpy as np
from sqlalchemy import create_engine, text
from horseracing_training import market_ev as me
from horseracing_eval import attention_rules as ar

OUT = pathlib.Path(sys.argv[2])
mode = sys.argv[1]
D_FROM, D_TO = "2026-10-03", "2026-10-04"
ROOT = pathlib.Path("/Users/kuwatawaku/workspace/horseracing/artifacts/market_ev")
single = me.MarketEvModel.load(ROOT / "mev-binary-v2", "mev-binary-v2")
ens = me.EnsembleMarketEvModel.load(ROOT / "mev-ens15-v1", "mev-ens15-v1")
eng = create_engine("postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")

def run(raw):
    feats = me.build_features(raw)
    d = feats["race_date"].dt.date.astype(str)
    target = feats[(d >= D_FROM) & (d <= D_TO)]
    ps = me.predict(single, target)
    pe = me.predict_ensemble(ens, target)
    m = pe[["race_id", "horse_id", "horse_number", "odds_used", "win_prob", "expected_return"]].merge(
        ps[["race_id", "horse_id", "expected_return"]].rename(columns={"expected_return": "single_er"}),
        on=["race_id", "horse_id"])
    days = target.set_index(["race_id", "horse_id"])["days_since_last"]
    m["days_since_last"] = [days.get((r, h)) for r, h in zip(m.race_id, m.horse_id)]
    m["rules"] = [",".join(ar.applicable_rules(ens_ev=float(e), single_ev=float(s), odds=float(o),
                   days_since_last=None if pd.isna(g) else float(g)))
                  for e, s, o, g in zip(m.expected_return, m.single_er, m.odds_used, m.days_since_last)]
    return m

with eng.connect() as c:
    c.execute(text("SET TRANSACTION READ ONLY"))
    raw = me.load_rows(c, through=D_TO)
dates = pd.to_datetime(raw["race_date"]).dt.date.astype(str)
tmask = (dates >= D_FROM) & (dates <= D_TO)
if mode == "pre":
    raw.loc[tmask, ["race_id", "horse_id", "odds", "popularity"]].to_parquet(OUT / "pre_target_inputs.parquet")
    run(raw).to_parquet(OUT / "pre.parquet")
    print("pre rows", tmask.sum())
else:
    post_raw = run(raw)
    post_raw.to_parquet(OUT / "post_raw.parquet")
    pre_in = pd.read_parquet(OUT / "pre_target_inputs.parquet").set_index(["race_id", "horse_id"])
    raw2 = raw.copy()
    idx = list(zip(raw2.loc[tmask, "race_id"], raw2.loc[tmask, "horse_id"]))
    raw2.loc[tmask, "odds"] = [pre_in["odds"].get(k) for k in idx]
    raw2.loc[tmask, "popularity"] = [pre_in["popularity"].get(k) for k in idx]
    post = run(raw2)
    post.to_parquet(OUT / "post_fixed_target.parquet")
    pre = pd.read_parquet(OUT / "pre.parquet")
    def compare(a, b, label):
        j = a.merge(b, on=["race_id", "horse_id"], suffixes=("_pre", "_post"))
        d = (j.expected_return_post - j.expected_return_pre).astype(float)
        sets = {}
        for rid in ("S1", "S2", "S3", "S4", "S5"):
            pa = set(j.loc[j.rules_pre.str.split(",").apply(lambda x: rid in x), "horse_id"])
            pb = set(j.loc[j.rules_post.str.split(",").apply(lambda x: rid in x), "horse_id"])
            sets[rid] = {"pre": len(pa), "post": len(pb), "both": len(pa & pb), "only_pre": len(pa - pb), "only_post": len(pb - pa)}
        return {"label": label, "horses": int(len(j)), "races": int(j.race_id.nunique()),
                "er_diff_abs_mean": float(d.abs().mean()), "er_diff_abs_median": float(d.abs().median()),
                "er_diff_abs_p90": float(d.abs().quantile(0.9)), "er_diff_abs_max": float(d.abs().max()),
                "er_diff_mean": float(d.mean()), "rel_diff_abs_median": float((d / j.expected_return_pre.astype(float)).abs().median()),
                "share_changed_gt_0.01": float((d.abs() > 0.01).mean()), "rule_sets": sets}
    res = {"target_races": f"{D_FROM}..{D_TO} (no upcoming entries in DB at measurement time; most recent meeting used)",
           "past_inputs_only": compare(pre, post, "target odds/popularity held at pre-repair values"),
           "raw": compare(pre, post_raw, "raw (target races' own odds also repaired)")}
    (OUT / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps(res, ensure_ascii=False, indent=1))
