import pandas as pd, numpy as np
ROOT="/Users/kuwatawaku/workspace/horseracing/"
d=pd.read_parquet(ROOT+"artifacts/roi_comprehensive_20261005/price_robustness/scored_pairs.parquet")
d["race_id"]=d.race_id.astype(str)
h=d[(d.race_id=="202604020609")&(d.horse_number==15)]
print(h[["race_id","race_date","horse_number","O_J","EVe_J","O_F","EVe_F","O_settle","final_src","official_payout_yen","hours","source","tb","J_S1","J_S3","F_S3","N1_J","won"]].to_string())
a=pd.read_csv(ROOT+"artifacts/roi_explore/missed_20261004/R05_settlement/affected_races.csv",dtype={"race_id":str})
print(a.columns.tolist())
print(a[a.race_id.isin(["202604020609","202609040607"])].to_string())
print("affected by date 08-02/08-09:",a[a.race_date.isin(["2026-08-02","2026-08-09"])].groupby("race_date").agg(n=("race_id","size"),affected=("affected","sum"),correctable=("correctable","sum")) if "race_date" in a else "no race_date col")
# power: day-level SE of ROI for S3 at recent bet rates (from calibration predictions, 2023-2026)
c=pd.read_parquet(ROOT+"artifacts/roi_comprehensive_20261005/calibration_roi/predictions.parquet")
c=c[c.year>=2023]
for nm,col in [("S3","p_ens"),("N1","p_ens_norm")]:
    m=(c[col]*c.odds>1.2).to_numpy()
    days,di=np.unique(c.race_date.to_numpy(),return_inverse=True)
    den=np.bincount(di,weights=m.astype(float),minlength=len(days)); num=np.bincount(di,weights=m*(c.won*c.odds).to_numpy(),minlength=len(days))
    rng=np.random.default_rng(1); w=rng.multinomial(len(days),np.full(len(days),1/len(days)),size=4000).astype(float)
    reps=(w@num)/(w@den)
    print(nm,"2023-26 bets",int(m.sum()),"days",len(days),"roi",round(num.sum()/den.sum(),4),"boot SD",round(reps.std(),4),"-> SE per 500 bets ~",round(reps.std()*np.sqrt(m.sum()/500),3),"per 2000 bets ~",round(reps.std()*np.sqrt(m.sum()/2000),3))
# paired N1-S3 diff SD
m3=(c.p_ens*c.odds>1.2).to_numpy(); m1=(c.p_ens_norm*c.odds>1.2).to_numpy()
days,di=np.unique(c.race_date.to_numpy(),return_inverse=True)
pay=(c.won*c.odds).to_numpy()
def reps(m):
    den=np.bincount(di,weights=m.astype(float),minlength=len(days)); num=np.bincount(di,weights=m*pay,minlength=len(days)); return (w@num)/(w@den)
dd=reps(m1)-reps(m3); print("N1-S3 paired diff 2023-26: point",round((pay[m1].mean()-pay[m3].mean()),4),"SD",round(dd.std(),4),"n_S3",int(m3.sum()),"-> bets for SE=2.5pt:",int(m3.sum()*(dd.std()/0.025)**2))
