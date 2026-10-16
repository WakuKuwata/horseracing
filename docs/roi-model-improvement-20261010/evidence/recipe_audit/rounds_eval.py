import pandas as pd, numpy as np, json
D='/private/tmp/claude-501/-Users-kuwatawaku-workspace-horseracing/76c2ac6f-96e3-4c6c-8f6a-7693441ba707/scratchpad/improve1010/recipe_audit/'
d=pd.read_parquet(D+'rounds_curve_preds.parquet')
won=d.won.values>0.5; yr=d.year.values; odds=d.odds.values; rid=d.race_id.values
inv=1/odds; q=inv/pd.Series(inv).groupby(rid).transform('sum').values
ITERS=[50,100,150,200,250,300,400]
def wnll(p): return -np.log(np.clip(p[won],1e-12,1))
def ece(p,y,bins=20):
    o=np.argsort(p); ps=p[o]; ys=y[o].astype(float); n=len(p); idx=np.array_split(np.arange(n),bins)
    return float(sum(abs(ps[i].mean()-ys[i].mean())*len(i)/n for i in idx))
wy=yr[won]; C=wy>=2019
base=wnll(d['p_300'].values); out={}
for k in ITERS:
    p=d[f'p_{k}'].values; n=wnll(p); dd=n-base
    m=p*odds>1.2
    out[k]={'wNLL_ALL':round(float(n.mean()),5),'wNLL_C':round(float(n[C].mean()),5),'diff_vs300_ALL':(round(float(dd.mean()),5),round(float(dd.std(ddof=1)/np.sqrt(len(dd))),5)),'diff_vs300_C':(round(float(dd[C].mean()),5),round(float(dd[C].std(ddof=1)/np.sqrt(C.sum())),5)),
            'ECE20_ALL':round(ece(p,won),5),'S3_n':int(m.sum()),'S3_won_over_p':round(float(won[m].sum()/p[m].sum()),3),'S3_roi_stored':round(float((won[m]*odds[m]).sum()/m.sum()),3),'S3_roi_C':round(float((won[m&(yr>=2019)]*odds[m&(yr>=2019)]).sum()/(m&(yr>=2019)).sum()),3),'sum_p_sd':round(float(pd.Series(p).groupby(rid).sum().std()),4)}
out['q']={'wNLL_ALL':round(float(wnll(q).mean()),5),'wNLL_C':round(float(wnll(q)[C].mean()),5)}
json.dump(out,open(D+'rounds_eval.json','w'),indent=1); print(json.dumps(out,indent=1))
