"""Describe frozen149/150 outcomes and old143 probability mass; score no new rule."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'artifacts/roi-after-149-150-20261025'
META=['race_id','horse_id','horse_number','race_date','year','odds','won','field_size']
KEYS=['race_id','horse_id']
DIR149=ROOT/'artifacts/149-half-normalization/comparison'
DIR150=ROOT/'artifacts/150-flat-stake/comparison'
LOCATIONS={'gamma':(DIR149,'weighted_baseline'),'full':(DIR149,'normalized_weighted_baseline'),
    'half':(DIR149,'half_normalized_weighted_baseline'),
    'flat_gamma':(DIR150,'flat_weighted_baseline'),'flat_full':(DIR150,'flat_normalized_weighted_baseline')}


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1<<20),b''):
            h.update(chunk)
    return h.hexdigest()


def accounting(d):
    result=dict(rows=len(d),winner_rows=int(d.won.sum()))
    for name in ('candidate','reference'):
        st=d['stake_'+name]
        pay=st*d.won*d.odds
        result[name]=dict(n_bets=int(st.gt(0).sum()),stake_yen=float(st.sum()),payout_yen=float(pay.sum()),
            profit_yen=float((pay-st).sum()),roi=float(pay.sum()/st.sum()) if st.sum() else None)
    result['delta_yen']=result['candidate']['profit_yen']-result['reference']['profit_yen']
    return result


def periods(frame):
    return {'all':np.ones(len(frame),dtype=bool),'before2026':frame.year.lt(2026),'2026':frame.year.eq(2026),
        **{str(y):frame.year.eq(y) for y in range(2019,2026)}}


def change(candidate,reference):
    d=candidate[META].copy()
    d['stake_candidate'],d['stake_reference']=candidate.stake,reference.stake
    ca,rb=candidate.stake.gt(0),reference.stake.gt(0)
    d['group']=np.select([ca&~rb,rb&~ca,ca&rb&candidate.stake.ne(reference.stake),ca&rb],
        ['added','removed','common_stake_changed','common_same_stake'],default='neither')
    results={}
    for name,mask in periods(d).items():
        sub=d.loc[mask]
        groups={label:accounting(g) for label,g in sub.groupby('group')}
        total=accounting(sub)
        assert abs(sum(g['delta_yen'] for g in groups.values())-total['delta_yen'])<1e-8
        delta=(sub.stake_candidate-sub.stake_reference)*(sub.won*sub.odds-1)
        by_race=sub[['race_date','race_id']].assign(delta_yen=delta).groupby(['race_date','race_id']).delta_yen.sum().reset_index()
        results[name]=dict(groups=groups,total=total,
            without_known_race_delta_yen=float(by_race.loc[by_race.race_id.ne('202608030111'),'delta_yen'].sum()),
            top_negative=by_race.nsmallest(10,'delta_yen').to_dict('records'),
            top_positive=by_race.nlargest(10,'delta_yen').to_dict('records'))
    return results


def exposure(candidate,reference):
    np.testing.assert_array_equal(candidate.stake.gt(0),reference.stake.gt(0))
    results={}
    for name,mask in periods(candidate).items():
        ca,re=candidate.loc[mask],reference.loc[mask]
        st_new,st_old=float(ca.stake.sum()),float(re.stake.sum())
        p_new,p_old=float(ca.profit.sum()),float(re.profit.sum())
        r_new=float((ca.profit.sum()+st_new)/st_new)
        r_old=float((re.profit.sum()+st_old)/st_old)
        scale=p_old*(st_new/st_old-1)
        composition=st_new*(r_new-r_old)
        assert abs(scale+composition-(p_new-p_old))<1e-8
        results[name]=dict(stake_new=st_new,stake_old=st_old,stake_ratio=st_new/st_old,
            profit_new=p_new,profit_old=p_old,profit_delta=p_new-p_old,roi_new=r_new,roi_old=r_old,
            scale_component_yen=scale,composition_component_yen=composition,
            caution='Aggregate accounting identity, not a scaled investment policy or an executable equal-exposure comparison')
    return results


def count_composition(reference,candidate):
    counts=reference.stake.gt(0).groupby(reference.race_id).sum()
    results={}
    for name,mask in periods(reference).items():
        re,ca=reference.loc[mask],candidate.loc[mask]
        n=re.race_id.map(counts)
        total_old,total_new=float(re.stake.sum()),float(ca.stake.sum())
        records={}
        for count in sorted(n.unique()):
            m=n.eq(count)
            old,new=re.loc[m],ca.loc[m]
            so,sn=float(old.stake.sum()),float(new.stake.sum())
            po,pn=float(old.profit.sum()),float(new.profit.sum())
            records[str(int(count))]=dict(races=old.race_id.nunique(),n_bets=int(old.stake.gt(0).sum()),
                stake_old=so,stake_new=sn,profit_old=po,profit_new=pn,
                roi_old=(po+so)/so if so else None,roi_new=(pn+sn)/sn if sn else None,
                stake_share_old=so/total_old,stake_share_new=sn/total_new)
        results[name]=records
    return results


def run():
    pace_dir=ROOT/'artifacts/143-existing-feature-roi/features_comparison'
    paths=[Path(__file__).resolve(),pace_dir/'pace_ensemble.parquet',pace_dir/'summary.json']
    for folder,arm in LOCATIONS.values():
        paths += [folder/f'{arm}_{part}.parquet' for part in ('ensemble','bets','races')]
        paths.append(folder/'summary.json')
    for feature,slug in [(149,'half-normalization'),(150,'flat-stake')]:
        folder=ROOT/f'artifacts/{feature}-{slug}'
        audit=ROOT/f'specs/{feature}-{slug}/evidence/independent_final.json'
        doc=json.loads(audit.read_text())
        assert doc['summary_sha256']==sha(folder/'comparison/summary.json') and not doc['blockers']
        assert doc['protocol_sha256']==sha(folder/'protocol.json')
        paths += [audit,folder/'protocol.json',folder/'protocol.sha256']
    hashes={str(p.relative_to(ROOT)):sha(p) for p in sorted(set(paths))}
    frames={}
    for tag,(folder,arm) in LOCATIONS.items():
        d=pd.read_parquet(folder/f'{arm}_ensemble.parquet').reset_index(drop=True)
        if frames:
            pd.testing.assert_frame_equal(d[META],frames['gamma'][META],check_exact=True)
        assert len(d)==343883 and d.race_id.nunique()==24855 and d.race_date.nunique()==807
        bets=pd.read_parquet(folder/f'{arm}_bets.parquet').set_index(KEYS)
        assert not bets.index.duplicated().any()
        d['stake']=bets.stake_yen.reindex(pd.MultiIndex.from_frame(d[KEYS]),fill_value=0).to_numpy()
        d['profit']=d.stake*(d.won*d.odds-1)
        point=json.loads((folder/'summary.json').read_text())['arms'][arm]['policy']
        assert abs(d.profit.sum()-point['profit_yen'])<1e-8 and abs(d.stake.sum()-point['stake_yen'])<1e-8
        frames[tag]=d
    mass=frames['gamma'].groupby('race_id').p.transform('sum').to_numpy()
    np.testing.assert_array_equal(frames['gamma'].p.to_numpy()/mass,frames['full'].p)
    np.testing.assert_array_equal(frames['gamma'].p.to_numpy()/np.sqrt(mass),frames['half'].p)
    pace=pd.read_parquet(pace_dir/'pace_ensemble.parquet')
    pd.testing.assert_frame_equal(pace[META],frames['gamma'][META],check_exact=True)
    pace_mass=pace.groupby('race_id').p.sum()
    # Do not construct or score any proposed normalized-pace betting policy.
    pace_diagnostic=dict(rows=len(pace),races=len(pace_mass),mass_mean=float(pace_mass.mean()),
        mass_sd=float(pace_mass.std()),mass_quantiles={str(q):float(pace_mass.quantile(q)) for q in (.01,.25,.5,.75,.99)},
        outside_095_105_fraction=float(((pace_mass<.95)|(pace_mass>1.05)).mean()),
        existing_raw_quality=json.loads((pace_dir/'summary.json').read_text())['arms']['pace']['quality'])
    result=dict(new_fits=0,new_policies_scored=0,can_adopt=False,untouched_holdout=False,
        half_minus_full=change(frames['half'],frames['full']),
        flat_accounting={'gamma':exposure(frames['flat_gamma'],frames['gamma']),'full':exposure(frames['flat_full'],frames['full'])},
        normalized_ticket_count_composition=count_composition(frames['full'],frames['flat_full']),
        pace_mass_diagnostic=pace_diagnostic,source_sha256=hashes)
    for name,digest in hashes.items():
        assert sha(ROOT/name)==digest
    OUT.mkdir(exist_ok=False,parents=True)
    with (OUT/'analysis.json').open('x') as f:
        json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False)
        f.write('\n')
    print(json.dumps(dict(half_minus_full=result['half_minus_full']['all']['total']['delta_yen'],
        flat_accounting=result['flat_accounting']['full']['all'],pace_mass=pace_diagnostic),ensure_ascii=False))


if __name__=='__main__':
    run()
