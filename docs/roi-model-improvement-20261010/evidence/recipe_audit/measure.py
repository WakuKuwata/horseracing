import pandas as pd, numpy as np, json, pathlib
R=pathlib.Path('/Users/kuwatawaku/workspace/horseracing/artifacts/roi_explore/results')
def tag(s): return f"armC_binary{'' if s==1 else f'_seed{s}'}_drop-sameday+weightlive_from2007_ens15"
base=pd.read_parquet(R/tag(1)/'predictions.parquet').sort_values(['race_id','horse_id']).reset_index(drop=True)
P=np.empty((15,len(base)))
for s in range(1,16):
    d=pd.read_parquet(R/tag(s)/'predictions.parquet').sort_values(['race_id','horse_id']).reset_index(drop=True)
    assert (d.race_id.values==base.race_id.values).all() and (d.horse_id.values==base.horse_id.values).all()
    P[s-1]=(1+d.pred.values/100)/d.odds.values
pbar=P.mean(0); p1=P[0]
odds=base.odds.values; won=(base.ret.values>-100)
rid=base.race_id.values
g=pd.Series(np.arange(len(base))).groupby(rid)
inv=1/odds; q=inv/pd.Series(inv).groupby(rid).transform('sum').values
sum_pbar=pd.Series(pbar).groupby(rid).transform('sum').values
pn=pbar/sum_pbar
sum_p1=pd.Series(p1).groupby(rid).transform('sum').values
p1n=p1/sum_p1
yr=base.year.values
out={}
# race-level sums
rs=pd.DataFrame({'rid':rid,'yr':yr,'sum_pbar':sum_pbar}).drop_duplicates('rid')
out['n_rows']=int(len(base)); out['n_races']=int(len(rs))
out['sum_pbar']={'mean':float(rs.sum_pbar.mean()),'sd':float(rs.sum_pbar.std()),'q05':float(rs.sum_pbar.quantile(.05)),'q50':float(rs.sum_pbar.quantile(.5)),'q95':float(rs.sum_pbar.quantile(.95)),'frac_ge_1.05':float((rs.sum_pbar>=1.05).mean()),'frac_le_0.95':float((rs.sum_pbar<=0.95).mean())}
out['sum_pbar_by_year']={int(y):round(float(v),4) for y,v in rs.groupby('yr').sum_pbar.mean().items()}
def wnll(p):
    return -np.log(np.clip(p[won],1e-12,1))
def ece(p,y,bins=20):
    o=np.argsort(p); ps=p[o]; ys=y[o].astype(float); n=len(p)
    idx=np.array_split(np.arange(n),bins); return float(sum(abs(ps[i].mean()-ys[i].mean())*len(i)/n for i in idx))
tab={}
for name,p in [('q',q),('ens15_raw',pbar),('ens15_norm',pn),('seed1_raw',p1),('seed1_norm',p1n)]:
    nll=wnll(p); wy=yr[won]
    tab[name]={'wNLL_all':float(nll.mean()),'wNLL_by_year':{int(y):round(float(nll[wy==y].mean()),5) for y in np.unique(wy)},
               'ECE20_all':ece(p,won),'logloss_all':float(-(won*np.log(np.clip(p,1e-12,1))+(~won)*np.log(np.clip(1-p,1e-12,1))).mean())}
    for w,(a,b) in {'D2010-15':(2010,2015),'Q2016-18':(2016,2018),'C2019-26':(2019,2026)}.items():
        m=(wy>=a)&(wy<=b); tab[name]['wNLL_'+w]=float(nll[m].mean())
        mm=(yr>=a)&(yr<=b); tab[name]['ECE20_'+w]=ece(p[mm],won[mm])
out['metrics']=tab
# paired differences per race (winner NLL) with race-level SE
def paired(pa,pb):
    d=wnll(pa)-wnll(pb); return {'mean':float(d.mean()),'se':float(d.std(ddof=1)/np.sqrt(len(d))),'n':int(len(d))}
out['paired']={'ens15_raw-q':paired(pbar,q),'ens15_norm-q':paired(pn,q),'ens15_norm-ens15_raw':paired(pn,pbar),'ens15_raw-seed1_raw':paired(pbar,p1),'seed1_norm-seed1_raw':paired(p1n,p1)}
# calibration in EV bands (raw ens15 EV = pbar*odds)
ev=pbar*odds
def band(mask,p):
    return {'n':int(mask.sum()),'wins':int(won[mask].sum()),'sum_p':float(p[mask].sum()),'calib_ratio_won_over_p':float(won[mask].sum()/p[mask].sum()),'roi_stored':float((won[mask]*odds[mask]).sum()/mask.sum())}
out['ev_bands_raw']={}
for lo,hi in [(0,0.8),(0.8,1.0),(1.0,1.1),(1.1,1.2),(1.2,1.4),(1.4,99)]:
    m=(ev>lo)&(ev<=hi); out['ev_bands_raw'][f'({lo},{hi}]']=band(m,pbar)
m=ev>1.2; out['S3_calib']={w:band(m&(yr>=a)&(yr<=b),pbar) for w,(a,b) in {'ALL':(2010,2026),'D':(2010,2015),'Q':(2016,2018),'C':(2019,2026)}.items()}
# normalized EV>1.2
evn=pn*odds; m=evn>1.2; out['N1_calib']={w:band(m&(yr>=a)&(yr<=b),pn) for w,(a,b) in {'ALL':(2010,2026),'C':(2019,2026)}.items()}
# odds band calibration of raw pbar
out['odds_bands_raw']={}
for lo,hi in [(1,2),(2,5),(5,10),(10,20),(20,40),(40,100),(100,9999)]:
    m=(odds>=lo)&(odds<hi); out['odds_bands_raw'][f'[{lo},{hi})']={**band(m,pbar),'sum_q':float(q[m].sum())}
# seed spread in S3 band
sd_seed=P.std(0); out['seed_sd']={'all_mean_rel':float((sd_seed/pbar).mean()),'S3_mean_rel':float((sd_seed/pbar)[ev>1.2].mean())}
json.dump(out,open('/private/tmp/claude-501/-Users-kuwatawaku-workspace-horseracing/76c2ac6f-96e3-4c6c-8f6a-7693441ba707/scratchpad/improve1010/recipe_audit/measure.json','w'),indent=1)
print(json.dumps(out,indent=1))
