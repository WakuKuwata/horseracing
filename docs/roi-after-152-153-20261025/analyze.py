"""Describe frozen152/153 changes only; no new model or purchase rule scored."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'artifacts/roi-after-152-153-20261025'
META=['race_id','horse_id','horse_number','race_date','year','odds','won','field_size']
KEYS=['race_id','horse_id']
D152=ROOT/'artifacts/152-normalized-pace/comparison'
D153=ROOT/'artifacts/153-market-three-column-ablation/comparison'
LOCATIONS={'baseline':(D153,'baseline'),'gamma':(D153,'weighted_baseline'),
    'full':(D153,'normalized_weighted_baseline'),'pace_raw':(D152,'pace'),
    'pace_full':(D152,'normalized_pace'),'drop3_full':(D153,'normalized_market_drop3')}
CONTRASTS=[('pace_full','pace_raw'),('pace_full','full'),('pace_full','baseline'),
    ('drop3_full','full'),('drop3_full','baseline'),('drop3_full','gamma')]


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

def quality_attribution(candidate,reference):
    # Assign each winner to the REFERENCE's fixed EV band, not a new selection policy.
    ev=reference.p*reference.odds
    bands=np.select([ev.le(1.),ev.le(1.2)],['ev_le1','ev_1_12'],default='ev_gt12')
    ca_mass=candidate.groupby('race_id').p.transform('sum')
    re_mass=reference.groupby('race_id').p.transform('sum')
    delta=-np.log(candidate.p/ca_mass)+np.log(reference.p/re_mass)
    d=pd.DataFrame(dict(year=reference.year,band=bands,won=reference.won,delta=delta))
    result={}
    for name,mask in periods(d).items():
        winners=d.loc[mask & d.won.eq(1)]
        result[name]=dict(total_races=len(winners),delta_winner_nll=float(winners.delta.mean()),
            groups={str(k):dict(winners=len(g),mean_nll_delta=float(g.delta.mean()),
                contribution_to_overall_nll=float(g.delta.sum()/len(winners))) for k,g in winners.groupby('band')})
    return result


def selected_diagnostic(frame):
    result={}
    for name,mask in periods(frame).items():
        d=frame.loc[mask & frame.stake.gt(0)]
        result[name]=dict(n_bets=len(d),hits=int(d.won.sum()),sum_predicted_wins=float(d.p.sum()),
            observed_over_predicted_wins=float(d.won.sum()/d.p.sum()),
            stake_yen=float(d.stake.sum()),profit_yen=float(d.profit.sum()),
            predicted_payout_yen=float((d.stake*d.p*d.odds).sum()),
            predicted_roi=float((d.stake*d.p*d.odds).sum()/d.stake.sum()),
            observed_roi=float((d.stake*d.won*d.odds).sum()/d.stake.sum()))
    return result


def run():
    paths=[Path(__file__).resolve()]
    for slug in ('152-normalized-pace','153-market-three-column-ablation'):
        folder=ROOT/'artifacts'/slug
        audit=ROOT/'specs'/slug/'evidence/independent_final.json'
        doc=json.loads(audit.read_text())
        assert not doc['blockers'] and doc['protocol_sha256']==sha(folder/'protocol.json')
        assert doc['summary_sha256']==sha(folder/'comparison/summary.json')
        paths += [audit,folder/'protocol.json',folder/'protocol.sha256']
        proto=json.loads((folder/'protocol.json').read_text())
        for name,digest in proto['inputs_and_source_sha256'].items():
            assert sha(ROOT/name)==digest,name
    frames={}
    for tag,(folder,arm) in LOCATIONS.items():
        paths += [folder/f'{arm}_{part}.parquet' for part in ('ensemble','bets','races')]
        paths += [folder/'summary.json']
        d=pd.read_parquet(folder/f'{arm}_ensemble.parquet').reset_index(drop=True)
        if frames:pd.testing.assert_frame_equal(d[META],frames['baseline'][META],check_exact=True)
        assert len(d)==343883 and d.race_id.nunique()==24855 and d.race_date.nunique()==807
        bets=pd.read_parquet(folder/f'{arm}_bets.parquet').set_index(KEYS)
        assert not bets.index.duplicated().any()
        d['stake']=bets.stake_yen.reindex(pd.MultiIndex.from_frame(d[KEYS]),fill_value=0).to_numpy()
        d['profit']=d.stake*(d.won*d.odds-1)
        old=json.loads((folder/'summary.json').read_text())['arms'][arm]['policy']
        assert abs(d.profit.sum()-old['profit_yen'])<1e-8 and abs(d.stake.sum()-old['stake_yen'])<1e-8
        frames[tag]=d
    hashes={str(p.relative_to(ROOT)):sha(p) for p in sorted(set(paths))}
    result=dict(new_fits=0,new_policies_scored=0,can_adopt=False,untouched_holdout=False,eligible_for_verdict=False,
        attribution={c+'_minus_'+b:change(frames[c],frames[b]) for c,b in CONTRASTS},
        fixed_reference_ev_winner_nll={c+'_minus_full':quality_attribution(frames[c],frames['full']) for c in ('pace_full','drop3_full')},
        selected_diagnostics={tag:selected_diagnostic(d) for tag,d in frames.items()},source_sha256=hashes,
        limitations=['Closing-odds proxy hypothetical single-win settlement; not executable-price profit.',
            'Repeatedly inspected historical development data; retrospective diagnostic groups are not trading rules.',
            'Winner-NLL groups use frozen reference EV; do not equate winner-only conditional loss with candidate portfolio profit.'])
    for name,digest in hashes.items():assert sha(ROOT/name)==digest
    OUT.mkdir(exist_ok=False,parents=True)
    with (OUT/'analysis.json').open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n')
    print(json.dumps({k:v['all']['total']['delta_yen'] for k,v in result['attribution'].items()}))


if __name__=='__main__':run()
