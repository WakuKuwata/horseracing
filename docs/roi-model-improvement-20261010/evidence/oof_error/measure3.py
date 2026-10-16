import os; os.environ["OMP_NUM_THREADS"]="1"
import json,pathlib,numpy as np,pandas as pd
REPO=pathlib.Path("/Users/kuwatawaku/workspace/horseracing"); RES=REPO/"artifacts/roi_explore/results"; OUT=pathlib.Path(__file__).resolve().parent
rng=np.random.default_rng(7)
def sd(s): return RES/f"armC_binary{'' if s==1 else f'_seed{s}'}_drop-sameday+weightlive_from2007_ens15"/"predictions.parquet"
base=pd.read_parquet(sd(1)); P=np.empty((len(base),15))
for s in range(1,16): d=pd.read_parquet(sd(s)); P[:,s-1]=(1+d.pred.values/100)/d.odds.values
rows=pd.read_parquet(REPO/"artifacts/market_ev/rows_2007.parquet",columns=["race_id","horse_id","won","race_ok","dead_heat","days_since_last"])
df=base.merge(rows,on=["race_id","horse_id"],how="left"); df=df[df.race_ok&~df.dead_heat&(df.race_date<="2026-06-26")&(df.year>=2013)].copy(); P=P[df.index.values]; df=df.reset_index(drop=True)
df["pbar"]=P.mean(1); df["won_f"]=df.won.astype(float); df["q"]=(1/df.odds)/df.groupby("race_id")["odds"].transform(lambda s:(1/s).sum()); df["ev"]=df.pbar*df.odds
EVs=P*df.odds.values[:,None]; n_over=(EVs>1.2).sum(1)
def boot(dates,num,den,B=1000):
    t=pd.DataFrame({"d":dates,"n":num,"m":den}).groupby("d").sum(); k=len(t); W=rng.multinomial(k,np.full(k,1/k),size=B); r=(W@t.n.values)/(W@t.m.values); return [round(float(np.quantile(r,.025)),3),round(float(np.quantile(r,.975)),3)]
out={}
# seed agreement within EV band 1.2-1.5 and >1.5 separately
for lab,lo,hi in [("1.2-1.5",1.2,1.5),(">1.5",1.5,99)]:
    band=(df.ev>lo)&(df.ev<=hi); res=[]
    for a,b in [(1,9),(10,12),(13,15)]:
        sel=band&(n_over>=a)&(n_over<=b); c=df[sel]
        if len(c)<50: continue
        res.append(dict(seeds=f"{a}-{b}",n=int(len(c)),wins=int(c.won_f.sum()),won_over_p=round(float(c.won_f.sum()/c.pbar.sum()),3),ci=boot(c.race_date.values,c.won_f.values,c.pbar.values),roi=round(float((c.won_f*c.odds).sum()/len(c)),3),mean_ev=round(float(c.ev.mean()),3)))
    out[f"agreement_within_ev_{lab}"]=res
# truth weight w per odds band within EV>1.2 and in EV 1.0-1.2
def wstar(c):
    lp=np.log(c.pbar.values); lq=np.log(c.q.values); W=c.won_f.sum(); ws=np.linspace(0,1.2,121); v=np.array([np.exp(w*lp+(1-w)*lq).sum() for w in ws]); return float(ws[np.argmin(np.abs(v-W))])
OD_B=[0,5,10,20,40,100,np.inf]; OD_L=["<5","5-10","10-20","20-40","40-100","100+"]; df["od"]=pd.cut(df.odds,OD_B,labels=OD_L,right=False)
out["w_model_by_odds_band_ev_gt_1_2"]={str(k):dict(n=int(len(c)),w=wstar(c)) for k,c in df[df.ev>1.2].groupby("od",observed=True) if len(c)>=300}
out["w_model_ev_1_0_1_2"]=wstar(df[(df.ev>1.0)&(df.ev<=1.2)]); out["w_model_ev_0_8_1_0"]=wstar(df[(df.ev>0.8)&(df.ev<=1.0)]); out["w_model_ev_lt_0_8"]=wstar(df[df.ev<=0.8])
# S1 cell: 20-40 x 14-112 days
s1=df[(df.ev>1.2)&(df.odds>=20)&(df.odds<40)&(df.days_since_last>=14)&(df.days_since_last<=112)]
out["S1_2013plus"]=dict(n=int(len(s1)),wins=int(s1.won_f.sum()),won_over_p=round(float(s1.won_f.sum()/s1.pbar.sum()),3),ci=boot(s1.race_date.values,s1.won_f.values,s1.pbar.values),won_over_q=round(float(s1.won_f.sum()/s1.q.sum()),3),roi=round(float((s1.won_f*s1.odds).sum()/len(s1)),3),roi_ci=boot(s1.race_date.values,(s1.won_f*s1.odds).values,np.ones(len(s1))))
s3b=df[(df.ev>1.2)&(df.odds>=20)&(df.odds<40)]
out["S3_20_40_2013plus"]=dict(n=int(len(s3b)),wins=int(s3b.won_f.sum()),won_over_p=round(float(s3b.won_f.sum()/s3b.pbar.sum()),3),roi=round(float((s3b.won_f*s3b.odds).sum()/len(s3b)),3))
# dose response: Σwon/Σp vs EV finer bins (EV>1.0)
bins=[1.0,1.1,1.2,1.3,1.4,1.5,1.7,2.0,99]; df["evb"]=pd.cut(df.ev,bins)
out["dose_ev_bins"]=[dict(ev=str(k),n=int(len(c)),won_over_p=round(float(c.won_f.sum()/c.pbar.sum()),3),won_over_q=round(float(c.won_f.sum()/c.q.sum()),3),roi=round(float((c.won_f*c.odds).sum()/len(c)),3)) for k,c in df.groupby("evb",observed=True)]
json.dump(out,open(OUT/"report3.json","w"),indent=1); print(json.dumps(out,indent=1))
