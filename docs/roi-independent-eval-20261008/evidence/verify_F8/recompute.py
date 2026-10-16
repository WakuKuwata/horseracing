import pandas as pd, numpy as np
ROOT="/Users/kuwatawaku/workspace/horseracing/"
d=pd.read_parquet(ROOT+"artifacts/roi_comprehensive_20261005/price_robustness/scored_pairs.parquet")
d["race_id"]=d.race_id.astype(str)
print("cols",d.columns.tolist())
print("rows",len(d),"races",d.race_id.nunique(),"days",d.race_date.nunique())
print("final_src counts",d.final_src.value_counts(dropna=False).to_dict())
print("official_race races",d.loc[d.official_race,"race_id"].nunique())
# my own definition of 389: archive csv has official payout >0 for the race
arc=pd.read_csv(ROOT+"artifacts/roi_explore/missed_20261004/R05_settlement/archived_final_odds.csv",dtype={"race_id":str,"horse_id":str})
print("archive cols",arc.columns.tolist(), "races",arc.race_id.nunique())
conf=set(arc.loc[arc.official_payout_yen.fillna(0)>0,"race_id"])
d["arc"]=d.race_id.isin(conf)
print("arc-confirmed races in scored:",d.loc[d.arc,"race_id"].nunique(),"unconfirmed:",d.loc[~d.arc,"race_id"].nunique())
print("unconfirmed dates:",d.loc[~d.arc].groupby("race_date").race_id.nunique().to_dict())
# independent ROI: payout = won * O_settle (and alt: won*O_F); stake 1 per bet
def roi(mask,price):
    x=d[mask]; return len(x), int(x.won.sum()), round(float((x.won*x[price]).sum()/len(x)),4) if len(x) else None
for pol in ["J_S1","J_S3","N1_J"] if "N1_J" in d else ["J_S1","J_S3"]:
    sel=d[pol].astype(bool)
    for nm,m in [("443",np.ones(len(d),bool)),("389",d.arc.to_numpy()),("54",~d.arc.to_numpy())]:
        print(pol,nm,"O_settle:",roi(sel&m,"O_settle"),"O_F:",roi(sel&m,"O_F"))
# settlement change in 389: where official exists, compare official_payout_yen/100 vs O_settle and vs O_F for winners
w=d[d.won]
print("winners total",len(w),"winners with official",w.official_payout_yen.notna().sum())
# merge archive payout for winners by race_id+horse_number
arcw=arc[arc.official_payout_yen.fillna(0)>0]
print("archive payout rows",len(arcw), arcw[["race_id","horse_number" if "horse_number" in arcw else "horse_id","official_payout_yen"]].head(3).to_string())
key=["race_id","horse_number"] if "horse_number" in arcw else None
if key:
    m=w.merge(arcw[key+["official_payout_yen"]].rename(columns={"official_payout_yen":"arc_pay"}),on=key,how="left")
    m["arc_mult"]=m.arc_pay/100
    chg=m[m.arc_pay.notna()&(~np.isclose(m.arc_mult,m.O_settle))]
    print("winners in 389 with archive payout:",m.arc_pay.notna().sum(),"changed vs O_settle:",len(chg))
    print(chg[["race_id","race_date","horse_number","O_J","O_F","O_settle","arc_mult"]].head(10).to_string())
    # among S1/S3 hits
    for pol in ["J_S1","J_S3"]:
        h=m[m[pol].astype(bool)]
        print(pol,"hits:",h[["race_id","race_date","horse_number","O_J","O_F","O_settle","arc_mult","final_src","hours","source","tb","arc"]].to_string())
