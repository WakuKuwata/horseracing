import numpy as np, pandas as pd, json
C="/Users/kuwatawaku/workspace/horseracing/artifacts/roi_comprehensive_20261005/calibration_roi/"
d=pd.read_parquet(C+"predictions.parquet")
print("rows",len(d),"dup keys",d.duplicated(["race_id","horse_id"]).sum(),"race_ok all",d.race_ok.all(),"dead_heat any",d.dead_heat.any())
print("years",d.year.min(),d.year.max(),"dates",d.race_date.min(),d.race_date.max())
d["ev_qe"]=d.p_calibrated_q_ens*d.odds; d["ev_s3"]=d.p_ens*d.odds; d["ev_n1"]=d.p_ens_norm*d.odds
pay=(d.won.astype(float)*d.odds).to_numpy()
def topN(col,N,mask=None):
    s=d[col].to_numpy().copy()
    if mask is not None: s=np.where(mask,s,-np.inf)
    # independent impl: nth-value threshold, then >= threshold (ties shown)
    thr=np.partition(s,-N)[-N]
    sel=s>=thr
    return sel,thr
# ALL
N=int((d.ev_s3>1.2).sum()); print("N_S3",N, "N1 EV>1.2",int((d.ev_n1>1.2).sum()),"qe EV>1.2",int((d.ev_qe>1.2).sum()))
for col in ["ev_qe","ev_s3","ev_n1"]:
    for NN in (2330,N):
        sel,thr=topN(col,NN); print(f"{col} N={NN} nsel={sel.sum()} roi={pay[sel].sum()/sel.sum():.4f} hits={int(d.won[sel].sum())} thr={thr:.4f}")
# per-window & per-year distribution of picks for ALL-pooled top-N
selqe,_=topN("ev_qe",N); sels3,_=topN("ev_s3",N); seln1,_=topN("ev_n1",N)
tab=pd.DataFrame({"year":d.year,"qe":selqe,"s3":sels3,"n1":seln1,"pay":pay})
agg=tab.groupby("year").agg(n_qe=("qe","sum"),n_s3=("s3","sum"),n_n1=("n1","sum"))
agg["roi_qe"]=tab[tab.qe].groupby("year").pay.sum()/agg.n_qe
agg["roi_s3"]=tab[tab.s3].groupby("year").pay.sum()/agg.n_s3
agg["roi_n1"]=tab[tab.n1].groupby("year").pay.sum()/agg.n_n1
print(agg.round(3))
# overlap
print("overlap qe&s3",(selqe&sels3).sum(),"qe&n1",(selqe&seln1).sum())
# window-matched
wins={"D":(2013,2018),"Q":(2019,2022),"R":(2023,2026)}
day=d.race_date.to_numpy()
rng=np.random.default_rng(20261007)
def boot_diff(selA,selB,m,B=4000):
    days=np.unique(day[m]); 
    df=pd.DataFrame({"day":day,"a_pay":pay*selA,"a_n":selA.astype(int),"b_pay":pay*selB,"b_n":selB.astype(int)})[m]
    g=df.groupby("day").sum().reindex(days).fillna(0)
    A=g.to_numpy(); nd=len(A)
    diffs=[];
    for _ in range(B):
        idx=rng.integers(0,nd,nd); s=A[idx].sum(0)
        diffs.append(s[0]/s[1]-s[2]/s[3])
    return np.percentile(diffs,[2.5,97.5])
res={}
for w,(lo,hi) in wins.items():
    m=d.year.between(lo,hi).to_numpy(); Nw=int((d.ev_s3[m]>1.2).sum())
    out={}
    sels={}
    for col in ["ev_qe","ev_s3","ev_n1"]:
        sel,thr=topN(col,Nw,m); sels[col]=sel; out[col]=(sel.sum(),round(pay[sel].sum()/sel.sum(),4),round(thr,4))
    ci1=boot_diff(sels["ev_qe"],sels["ev_s3"],m); ci2=boot_diff(sels["ev_qe"],sels["ev_n1"],m)
    print(w,"N",Nw,out,"CI qe-s3",ci1.round(4),"CI qe-n1",ci2.round(4))
m=np.ones(len(d),bool)
print("ALL CI qe-s3",boot_diff(selqe,sels3,m).round(4),"qe-n1",boot_diff(selqe,seln1,m).round(4))
# fixed prospective threshold check: 1.04 for qe across all years, per window
for thr in (1.03,1.04,1.041,1.05,1.06):
    sel=(d.ev_qe>thr).to_numpy()
    s=f"qe>{thr}: ALL n={sel.sum()} roi={pay[sel].sum()/sel.sum():.4f}"
    for w,(lo,hi) in wins.items():
        mm=sel&d.year.between(lo,hi).to_numpy(); s+=f" | {w} n={mm.sum()} roi={pay[mm].sum()/max(mm.sum(),1):.4f}"
    print(s)
