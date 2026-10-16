import numpy as np, pandas as pd
ROOT="/Users/kuwatawaku/workspace/horseracing/"
C=ROOT+"artifacts/roi_comprehensive_20261005/calibration_roi/"
d=pd.read_parquet(C+"predictions.parquet")
d["ev_q"]=d.p_calibrated_q*d.odds; d["ev_qe"]=d.p_calibrated_q_ens*d.odds; d["ev_s3"]=d.p_ens*d.odds; d["ev_n1"]=d.p_ens_norm*d.odds
pay=(d.won*d.odds).to_numpy()
n_s3=int((d.ev_s3>1.2).sum())
print("rank-matched (top-N by each score, N = S3 EV>1.2 count =",n_s3,") -- post-hoc diagnostic, not a policy")
for nm,col in [("q-only cal","ev_q"),("q+ens cal","ev_qe"),("S3 raw","ev_s3"),("N1 norm","ev_n1")]:
    for N in (2330,n_s3):
        idx=np.argsort(-d[col].to_numpy(),kind="stable")[:N]
        thr=d[col].to_numpy()[idx[-1]]
        print(f"  {nm:10s} N={N:6d} roi={pay[idx].sum()/N:.4f} hits={int(d.won.to_numpy()[idx].sum())} threshold_ev={thr:.3f}")
# per-window for N=n_s3 on q+ens and S3
for w,(lo,hi) in {"D":(2013,2018),"Q":(2019,2022),"R":(2023,2026)}.items():
    m=d.year.between(lo,hi).to_numpy(); sub=d[m]; p2=pay[m]
    N=int((sub.ev_s3>1.2).sum())
    out=[]
    for nm,col in [("q+ens",'ev_qe'),("S3",'ev_s3'),("N1",'ev_n1')]:
        idx=np.argsort(-sub[col].to_numpy(),kind="stable")[:N]
        out.append(f"{nm} {p2[idx].sum()/N:.4f}")
    print(w,"N=",N," ".join(out))
