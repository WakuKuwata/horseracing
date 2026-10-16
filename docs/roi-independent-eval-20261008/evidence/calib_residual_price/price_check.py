import json, numpy as np, pandas as pd
ROOT="/Users/kuwatawaku/workspace/horseracing/"
P=ROOT+"artifacts/roi_comprehensive_20261005/price_robustness/"
D=ROOT+"artifacts/roi_explore/missed_20261004/R02_odds_drift/"
pairs=pd.read_parquet(D+"pairs.parquet")
print(pairs.columns.tolist())
ver=pairs[["race_id","pair_id","hours","source","tb","race_date"]].drop_duplicates()
print("pairs",len(ver),"races",ver.race_id.nunique())
print(ver.groupby(["source","tb"]).size())
keep=ver.sort_values(["race_id","hours","source","pair_id"],kind="stable").drop_duplicates("race_id")
print("chosen by source/tb:\n",keep.groupby(["source","tb"]).size())
print("chosen hours quantiles",keep.hours.quantile([0,.25,.5,.75,1]).round(2).to_dict())
multi=ver.groupby("race_id").size(); print("races with >1 snapshot:",(multi>1).sum())
# retention for chosen vs non-chosen snapshots (S3 judged vs final)
pairs["chosen"]=pairs.pair_id.isin(keep.pair_id)
for ch,g in pairs.groupby("chosen"):
    j=g.J_S3.astype(bool); f=g.F_S3.astype(bool)
    print("chosen" if ch else "other","S3 judged",j.sum(),"retention",round((j&f).sum()/j.sum(),3),"S1 ret",round((g.J_S1.astype(bool)&g.F_S1.astype(bool)).sum()/g.J_S1.astype(bool).sum(),3),"n_pairs",g.pair_id.nunique())
# retention by time band (chosen or not)
for tb,g in pairs.groupby("tb"):
    j=g.J_S3.astype(bool); f=g.F_S3.astype(bool)
    print("tb",tb,"S3 judged",j.sum(),"retention",round((j&f).sum()/max(j.sum(),1),3))
# scored pairs
d=pd.read_parquet(P+"scored_pairs.parquet")
print("scored races",d.race_id.nunique(),"official races",d.loc[d.official_race,"race_id"].nunique())
arc=pd.read_csv(ROOT+"artifacts/roi_explore/missed_20261004/R05_settlement/archived_final_odds.csv",dtype={"race_id":str,"horse_id":str})
d["race_id"]=d.race_id.astype(str)
confirmed=set(arc.loc[arc.official_payout_yen.notna()&(arc.official_payout_yen>0),"race_id"])
d["arc_ok"]=d.race_id.isin(confirmed)
print("archive-confirmed races in scored set:",d.loc[d.arc_ok,"race_id"].nunique())
comp=d[~d.arc_ok]
print("non-confirmed races:",comp.race_id.nunique(),"dates:",sorted(comp.race_date.unique()))
# S3 hits
s3=d[d.J_S3.astype(bool)]
print("S3 bets",len(s3),"hits",int(s3.won.sum()))
print(s3.loc[s3.won,["race_id","race_date","horse_number","O_J","O_F","O_settle","official_payout_yen","settle_multiplier","arc_ok","source","tb","hours"]])
s1=d[d.J_S1.astype(bool)]
print("S1 bets",len(s1),"hits",int(s1.won.sum()))
print(s1.loc[s1.won,["race_id","race_date","horse_number","O_J","O_F","O_settle","official_payout_yen","settle_multiplier","arc_ok"]])
# decomposition: S3 on 443 with O_settle only vs official-where-available; S3 on 389 subset; S3 on 54 complement
for nm,m in [("443 all",np.ones(len(d),bool)),("389 arc",d.arc_ok.to_numpy()),("54 non-arc",~d.arc_ok.to_numpy())]:
    x=d[m&d.J_S3.astype(bool).to_numpy()]
    print(nm,"S3 n",len(x),"hits",int(x.won.sum()),"roi(settle_mult)",round(x.settle_multiplier.mean(),4) if len(x) else None,"roi(O_F)",round((x.won*x.O_F).mean(),4) if len(x) else None)
# non-confirmed races: O_settle vs O_F
print("non-arc rows: O_settle==O_F?", np.allclose(comp.O_settle,comp.O_F))
print(comp[["race_date","source","tb"]].drop_duplicates().head(20))
# how many of 54 non-arc have winners with O_F (stored final) -- check whether these are in R05 contamination window
print(comp.groupby("race_date").race_id.nunique())
