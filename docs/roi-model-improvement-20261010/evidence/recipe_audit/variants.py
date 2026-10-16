import pandas as pd, numpy as np, json, pathlib
R=pathlib.Path('/Users/kuwatawaku/workspace/horseracing/artifacts/roi_explore/results')
def tag(s): return f"armC_binary{'' if s==1 else f'_seed{s}'}_drop-sameday+weightlive_from2007_ens15"
def load(t):
    d=pd.read_parquet(R/t/'predictions.parquet').sort_values(['race_id','horse_id']).reset_index(drop=True); return d
base=load(tag(1)); key=base[['race_id','horse_id']]
odds=base.odds.values; won=base.ret.values>-100; rid=base.race_id.values; yr=base.year.values
def p_of(d):
    assert (d.race_id.values==rid).all() and (d.horse_id.values==base.horse_id.values).all(), 'key mismatch'
    return (1+d.pred.values/100)/d.odds.values
P=np.array([p_of(load(tag(s))) for s in range(1,16)])
def wnll(p): return -np.log(np.clip(p[won],1e-12,1))
def paired(pa,pb,mask=None):
    d=wnll(pa)-wnll(pb)
    if mask is not None: d=d[mask]
    return (round(float(d.mean()),5), round(float(d.std(ddof=1)/np.sqrt(len(d))),5), int(len(d)))
wy=yr[won]; C=(wy>=2019)
out={}
pbar=P.mean(0)
# seed curve (prefix averages)
out['seed_curve_vs_seed1']={k:paired(P[:k].mean(0),P[0]) for k in (1,2,3,5,7,10,15)}
# variants
var={}
for name,t in [('r600_seed1','armC_binary_drop-sameday+weightlive_from2007_r600'),('r600_seed2','armC_binary_seed2_drop-sameday+weightlive_from2007_r600'),('r600_seed3','armC_binary_seed3_drop-sameday+weightlive_from2007_r600'),('win8_seed1','armC_binary_drop-sameday+weightlive_from2007_win8'),('withp_seed1','armC_binary_withp_drop-sameday+weightlive_from2007'),('serving_v2_seed1','armC_binary_drop-sameday+weightlive_serving_v2_2007')]:
    d=load(t)
    if len(d)!=len(base):
        m=key.merge(d,on=['race_id','horse_id'],how='left'); p=(1+m.pred.values/100)/m.odds.values; ok=~np.isnan(p)
        var[name]={'rows':int(ok.sum()),'note':'subset join'}
        pp=np.where(ok,p,P[0]); var[name]['vs_seed1_ALL']=paired(pp,P[0],ok[won]); var[name]['vs_seed1_C']=paired(pp,P[0],ok[won]&C)
    else:
        p=p_of(d); var[name]={'rows':len(d),'vs_seed1_ALL':paired(p,P[0]),'vs_seed1_C':paired(p,P[0],C),'vs_ens15_ALL':paired(p,pbar),'vs_ens15_C':paired(p,pbar,C)}
r600=[]
for t in ['armC_binary_drop-sameday+weightlive_from2007_r600','armC_binary_seed2_drop-sameday+weightlive_from2007_r600','armC_binary_seed3_drop-sameday+weightlive_from2007_r600']:
    r600.append(p_of(load(t)))
r600=np.array(r600); var['r600_avg3_vs_ens3_ALL']=paired(r600.mean(0),P[:3].mean(0)); var['r600_avg3_vs_ens3_C']=paired(r600.mean(0),P[:3].mean(0),C)
var['r600_avg3_vs_ens15_ALL']=paired(r600.mean(0),pbar)
out['variants']=var
# S3-band overconfidence by k
def s3(p):
    m=p*odds>1.2; return {'n':int(m.sum()),'won_over_p':round(float(won[m].sum()/p[m].sum()),3),'roi':round(float((won[m]*odds[m]).sum()/m.sum()),3),'mean_pred_ev':round(float((p*odds)[m].mean()),3)}
out['S3_by_k']={k:s3(P[:k].mean(0)) for k in (1,3,7,15)}
out['S3_r600_avg3']=s3(r600.mean(0))
json.dump(out,open('/private/tmp/claude-501/-Users-kuwatawaku-workspace-horseracing/76c2ac6f-96e3-4c6c-8f6a-7693441ba707/scratchpad/improve1010/recipe_audit/variants.json','w'),indent=1)
print(json.dumps(out,indent=1))
