"""Independent153 audit; never imports153 implementation or fits a model.

Uses independent143 scoring helpers, own74-feature reconstruction/normalization,
bootstrap and candidate receipt checks. All reads are frozen local evidence.
"""
from __future__ import annotations
import os
for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS'):
    os.environ[key]='1'
import argparse
import hashlib
import importlib.util
import json
import platform
from pathlib import Path
import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.special import expit

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'artifacts/153-market-three-column-ablation'
OLD142=ROOT/'artifacts/142-roi-policy-model-improvement'
OLD143=ROOT/'artifacts/143-existing-feature-roi'
OLD148=ROOT/'artifacts/148-postprediction-normalization'
KEYS=['race_id','horse_id','horse_number','race_date','year','odds','won','field_size']
SOURCE_ARMS=('baseline','weighted_baseline','market_drop3')
NORMALIZED={'normalized_weighted_baseline':'weighted_baseline','normalized_market_drop3':'market_drop3'}
ARMS=('current_reference','baseline','weighted_baseline',*NORMALIZED)
CONTRASTS=tuple(('normalized_market_drop3',b) for b in ('baseline','weighted_baseline','normalized_weighted_baseline'))
HELPER_PATH=ROOT/'specs/143-existing-feature-roi/evidence/independent_audit.py'
spec=importlib.util.spec_from_file_location('independent143_helpers_for153',HELPER_PATH)
helpers=importlib.util.module_from_spec(spec);spec.loader.exec_module(helpers)
sha,read,equal=helpers.sha,helpers.read,helpers.equal


def folder(arm,year,seed):
    suffix=f'seed_{seed:02d}/year_{year}/{arm}'
    if arm=='baseline':return OLD142/'models'/suffix
    if arm=='weighted_baseline':return OLD143/'weighted_models'/suffix
    return OUT/'models'/suffix


def verify_sources(protocol):
    for oldout in (OLD142,OLD143,OLD148):
        assert sha(oldout/'protocol.json')==(oldout/'protocol.sha256').read_text().strip()
        for name,digest in read(oldout/'protocol.json')['inputs_and_source_sha256'].items():
            assert sha(ROOT/name)==digest,name
    old=read(OLD143/'protocol.json')
    for year in range(2019,2027):
        for seed in range(1,16):
            for arm in ('baseline','weighted_baseline'):
                path=folder(arm,year,seed); rec=read(path/'receipt.json'); identity=rec['recipe']['identity']
                assert set(rec['hashes'])=={'model.txt','predictions.parquet','feature_spec.json'}
                for file in ('receipt.json','predictions.parquet'):
                    assert str((path/file).relative_to(ROOT)) in protocol['inputs_and_source_sha256']
                if arm=='baseline':
                    assert identity['protocol_sha256']==sha(OLD142/'protocol.json')
                    assert identity['source_sha256']==sha(ROOT/'scripts/roi_explore/roi_improvement_142/models.py')
                    assert identity['dependency_sha256']['direct_return_model']==sha(ROOT/'scripts/roi_explore/direct_return_model.py')
                else:
                    assert identity['source_sha256']==sha(ROOT/'scripts/roi_explore/roi_improvement_143/models.py')
                    assert identity['weights_sha256']==sha(ROOT/old['weights'])
                    equal(dict(legacy=sha(ROOT/'scripts/roi_explore/direct_return_model.py'),models142=sha(ROOT/'scripts/roi_explore/roi_improvement_142/models.py')),identity['dependencies'])
                assert identity['research_only'] and not identity['can_adopt'] and not identity['untouched_holdout']


def candidate_ensemble(protocol,counter):
    old=read(OLD143/'protocol.json')
    expected_features=[c for c in old['base_features'] if c not in ('odds','popularity','odds_rank')]
    assert len(expected_features)==74 and protocol['candidate_features']==expected_features
    cats=old['base_features'][-11:];numeric=[c for c in expected_features if c not in cats]
    runtime=dict(python=platform.python_version(),numpy=np.__version__,pandas=pd.__version__,lightgbm=lgb.__version__)
    completion=read(OUT/'models_complete.json')
    assert completion['jobs']==completion['fits']==120 and len(completion['hashes'])==360
    assert completion['protocol_sha256']==sha(OUT/'protocol.json') and completion['can_adopt'] is False
    for name,digest in completion['hashes'].items():assert sha(OUT/name)==digest,name
    parts=[]
    for year in range(2019,2027):
        first,total=None,None
        for seed in range(1,16):
            path=folder('market_drop3',year,seed); rec=read(path/'receipt.json'); r=rec['recipe'];fs=read(path/'feature_spec.json')
            ident=r['identity']
            assert r['arm']=='market_drop3' and r['seed']==seed and r['rounds']==rec['actual_iterations']==300
            assert r['features']==expected_features
            assert r['train_start'].startswith('2007-') and r['train_end']<r['target_start']
            assert r['train_end'].startswith(str(year-1)) and r['target_start'].startswith(str(year))
            assert ident['protocol_sha256']==sha(OUT/'protocol.json')
            assert ident['year']==year and ident['seed']==seed
            equal(runtime,ident)
            for key in ('rows','additions','weights'):assert ident[f'{key}_sha256']==sha(ROOT/protocol[key])
            assert ident['source_sha256']==sha(ROOT/'scripts/roi_explore/roi_improvement_153/models.py')
            equal(dict(models143=sha(ROOT/'scripts/roi_explore/roi_improvement_143/models.py'),models142=sha(ROOT/'scripts/roi_explore/roi_improvement_142/models.py'),legacy=sha(ROOT/'scripts/roi_explore/direct_return_model.py')),ident['dependencies'])
            assert ident['bundles']=={'drop':['odds','popularity','odds_rank']} and ident['arms']==['market_drop3'] and ident['kernel_arm']=='market_drop3'
            assert ident['research_only'] is True and ident['can_adopt'] is False and ident['untouched_holdout'] is False
            assert read(path.parent/'identity.json')==ident
            evaluation=read(path.parent/'evaluation.json')
            assert evaluation['identity']==ident and evaluation['year']==year and evaluation['seed']==seed
            equal(dict(metrics=rec['metrics'],elapsed_s=rec['elapsed_s'],actual_iterations=300),evaluation['arms']['market_drop3'])
            for flag in ('can_adopt','untouched_holdout','production_changed'):assert evaluation[flag] is False
            original=read(folder('weighted_baseline',year,seed)/'receipt.json')['recipe']
            assert fs['cat_maps']==read(folder('weighted_baseline',year,seed)/'feature_spec.json')['cat_maps']
            for key in ('train_start','train_end','target_start','target_end','train_rows','target_rows','weights'):
                equal(original[key],r[key],f'candidate_parent.{year}.{seed}.{key}')
            params=dict(old['models']['params'],seed=seed,objective='binary',metric='binary_logloss')
            assert r['params']==fs['params']==params and fs['features']==numeric and fs['cats']==cats
            assert fs['feature_order']==expected_features and fs['feature_hash']==hashlib.sha256(json.dumps(expected_features,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
            assert fs['objective']=='binary' and fs['best_iteration']==300 and fs['train_from']==2007 and fs['train_through']==year-1 and fs['trained_for_year']==year and fs['training_cutoff']==r['train_end']
            assert fs['research_only'] is True and fs['can_adopt'] is False
            assert rec['roundtrip_max_abs_raw_difference']<=1e-12
            assert set(rec['hashes'])=={'model.txt','predictions.parquet','feature_spec.json'}
            for name,digest in rec['hashes'].items():
                assert sha(path/name)==digest;counter['artifact_hashes']+=1
            p=pd.read_parquet(path/'predictions.parquet')
            assert len(p)==r['target_rows'] and p.year.eq(year).all() and p.p.between(0,1).all() and np.isfinite(p.p).all()
            equal(helpers.quality(p),rec['metrics'],f'fit_metrics.{year}.{seed}')
            np.testing.assert_allclose(p.pred,100*(p.p.to_numpy()*p.odds.to_numpy()-1),rtol=0,atol=1e-12)
            if first is None:first,total=p[KEYS].copy(),np.zeros(len(p))
            else:assert first.equals(p[KEYS])
            total+=p.p.to_numpy(float);counter['fits']+=1
        first['p']=total/15;parts.append(first)
    return pd.concat(parts,ignore_index=True)


def bootstrap(tables):
    dates=sorted(tables['baseline'].race_date.unique());assert len(dates)==807
    draws=np.random.default_rng(20261025).multinomial(len(dates),np.full(len(dates),1/len(dates)),size=4000)
    daily={a:f.groupby('race_date').agg(stake=('stake_yen','sum'),payout=('payout_yen','sum'),budget=('budget_yen','sum'),profit=('profit_yen','sum'),nll=('winner_nll','sum'),races=('race_id','size')).reindex(dates) for a,f in tables.items()}
    def interval(point,samples):
        good=np.asarray(samples)[np.isfinite(samples)]
        return dict(difference=float(point),ci95=np.quantile(good,[.025,.975]).tolist(),bonferroni_familywise95_ci=np.quantile(good,[.05/6,1-.05/6]).tolist(),valid_replicates=len(good))
    result={}
    for a,b in CONTRASTS:
        new,old=daily[a],daily[b]
        result[f'{a}_minus_{b}']=dict(winner_nll=interval((new.nll-old.nll).sum()/old.races.sum(),draws@(new.nll-old.nll)/(draws@old.races)),profit_per_budget=interval((new.profit-old.profit).sum()/old.budget.sum(),draws@(new.profit-old.profit)/(draws@old.budget)),deployed_roi=interval(new.payout.sum()/new.stake.sum()-old.payout.sum()/old.stake.sum(),(draws@new.payout)/(draws@new.stake)-(draws@old.payout)/(draws@old.stake)))
    return result


def sensitivity(tables):
    result={}
    for a,b in CONTRASTS:
        old,new=tables[b].set_index('race_id'),tables[a].set_index('race_id');assert old.index.equals(new.index)
        delta=new.profit_yen-old.profit_yen;years=old.race_date.str[:4].astype(int);calendar=old.race_date.str[5:].le('06-21');known='202608030111';top=delta.idxmax()
        result[f'{a}_minus_{b}']=dict(known_race_delta_yen=float(delta.loc[known]),without_known_race_delta_yen=float(delta.drop(known).sum()),top_difference_race=top,top_difference_yen=float(delta.loc[top]),without_top_difference_yen=float(delta.drop(top).sum()),same_calendar_jan1_jun21_by_year={str(y):float(delta.loc[years.eq(y)&calendar].sum()) for y in range(2019,2027)},before2026_delta_yen=float(delta.loc[years.lt(2026)].sum()),year2026_delta_yen=float(delta.loc[years.eq(2026)].sum()))
    return result


def replay():
    protocol=read(OLD143/'protocol.json');checks=[]
    for year,seed in ((2019,1),(2024,8),(2026,15)):
        cols=list(dict.fromkeys(['race_id','horse_id']+protocol['base_features']))
        original=pd.read_parquet(ROOT/protocol['rows'],columns=cols,filters=[('year','=',year)]).set_index(['race_id','horse_id'])
        for arm in SOURCE_ARMS:
            path=folder(arm,year,seed);fs=read(path/'feature_spec.json');pred=pd.read_parquet(path/'predictions.parquet').iloc[:64]
            idx=pd.MultiIndex.from_frame(pred[['race_id','horse_id']]);selected=original.loc[idx]
            x=np.column_stack([selected[fs['features']].to_numpy(np.float32)]+[selected[c].astype('string').map(fs['cat_maps'][c]).to_numpy(np.float32,na_value=np.nan) for c in fs['cats']])
            booster=lgb.Booster(model_file=str(path/'model.txt'));assert booster.feature_name()==fs.get('feature_order',fs['features']+fs['cats'])
            actual=expit(booster.predict(x,raw_score=True,num_threads=1));np.testing.assert_array_equal(actual,pred.p.to_numpy())
            checks.append(dict(arm=arm,year=year,seed=seed,rows=len(pred),max_abs_p_difference=0.))
    return checks


def audit():
    protocol=read(OUT/'protocol.json');assert sha(OUT/'protocol.json')==(OUT/'protocol.sha256').read_text().strip()
    for name,digest in protocol['inputs_and_source_sha256'].items():assert sha(ROOT/name)==digest,name
    assert protocol['contrasts']==[list(pair) for pair in CONTRASTS]
    equal(dict(repetitions=4000,seed=20261025,family_size=3),protocol['bootstrap'])
    assert protocol['new_fits']==120 and protocol['reused_control_fits']==240
    assert protocol['seeds']==list(range(1,16)) and protocol['max_workers']==2 and protocol['threads_each']==1
    equal(dict(training_from=2007,through='2026-06-26',model_outer=list(range(2019,2027))),protocol['window'])
    equal(dict(raw_ev_gt=1.2,budget_yen_per_race=1000,stake_unit_yen=100,max_horses=10,allocation='equal floor(10/n)100yen units; remaining and skipped budget staycash'),protocol['policy'])
    assert protocol['dropped_features']==['odds','popularity','odds_rank']
    for flag in ('can_adopt','untouched_holdout','eligible_for_verdict'):assert protocol[flag] is False
    verify_sources(protocol)
    original=read(OLD143/'protocol.json');counter=dict(fits=0,artifact_hashes=0)
    frames={a:helpers.ensemble(a,original,counter) for a in ('baseline','weighted_baseline')}
    frames['market_drop3']=candidate_ensemble(protocol,counter);assert counter==dict(fits=360,artifact_hashes=1080)
    weight_checks=helpers.weights_audit(original)
    for check in weight_checks:
        for seed in range(1,16):
            info=read(folder('market_drop3',check['year'],seed)/'receipt.json')['recipe']['weights']
            assert info['vector_sha256']==check['vector_sha256']
    base=frames['baseline'];assert len(base)==343883 and base.race_id.nunique()==24855 and base.race_date.nunique()==807
    assert base.race_date.min()=='2019-01-05' and base.race_date.max()=='2026-06-21'
    for f in frames.values():assert base[KEYS].equals(f[KEYS])
    norms={}
    for candidate,parent in NORMALIZED.items():
        raw=frames[parent];derived=raw[KEYS].copy();values=raw.p.to_numpy(float);normalized=np.zeros(len(raw))
        for positions in raw.groupby('race_id',sort=False).indices.values():
            selected=values[positions];assert len(positions)==raw.field_size.iloc[positions[0]] and selected.sum()>0 and np.isfinite(selected).all()
            normalized[positions]=selected/selected.sum()
        derived['p']=normalized;sums=derived.groupby('race_id').p.sum().to_numpy();np.testing.assert_allclose(sums,1.,atol=1e-14,rtol=0)
        frames[candidate]=derived;norms[candidate]=dict(parent=parent,max_abs_race_sum_error=float(abs(sums-1).max()))
    current=pd.read_parquet(OLD142/'existing_policies/inputs.parquet');frames['current_reference']=current.loc[current.year.ge(2019),KEYS+['p_raw']].rename(columns={'p_raw':'p'})
    raw_quality=helpers.quality(frames.pop('market_drop3'))
    saved=read(OUT/'comparison/summary.json');assert set(saved['arms'])==set(ARMS)
    assert saved['new_fits']==120 and saved['reused_control_fits']==240 and len(saved['fits'])==120
    assert saved['protocol_sha256']==sha(OUT/'protocol.json') and saved['settlement']=='closing_odds_proxy'
    equal(protocol['normalization'],saved['normalization'])
    for flag in ('can_adopt','untouched_holdout','eligible_for_verdict'):assert saved[flag] is False
    tables={}
    for arm,f in frames.items():
        ordered=f.sort_values(['race_id','horse_number']).reset_index(drop=True);assert base[KEYS].reset_index(drop=True).equals(ordered[KEYS])
        stored=pd.read_parquet(OUT/f'comparison/{arm}_ensemble.parquet').sort_values(['race_id','horse_number']).reset_index(drop=True)
        assert ordered[KEYS].equals(stored[KEYS]);np.testing.assert_allclose(ordered.p,stored.p,rtol=1e-14,atol=1e-15)
        scored,races,bets=helpers.score(f);equal(scored,saved['arms'][arm],arm)
        sr=pd.read_parquet(OUT/f'comparison/{arm}_races.parquet');assert races[['race_date','race_id']].equals(sr[['race_date','race_id']])
        for col in ('stake_yen','payout_yen','profit_yen','budget_yen','winner_nll'):np.testing.assert_allclose(races[col],sr[col],atol=1e-12,rtol=1e-12)
        sb=pd.read_parquet(OUT/f'comparison/{arm}_bets.parquet');assert bets[['race_id','horse_id']].reset_index(drop=True).equals(sb[['race_id','horse_id']].reset_index(drop=True))
        np.testing.assert_array_equal(bets.units.to_numpy(),sb.units.to_numpy());np.testing.assert_allclose(bets.payout_yen,sb.payout_yen,atol=1e-10,rtol=0)
        tables[arm]=races
        masses=f.groupby('race_id').p.sum().to_numpy()
        equal(dict(min=float(masses.min()),max=float(masses.max()),mean=float(masses.mean()),quantiles={str(q):float(np.quantile(masses,q)) for q in (.01,.25,.5,.75,.99)},outside_095_105_fraction=float(((masses<.95)|(masses>1.05)).mean())),saved['mass_diagnostics'][arm],f'mass.{arm}')
        if arm!='normalized_market_drop3':equal(saved['arms'][arm],read(OLD148/'comparison/summary.json')['arms'][arm],f'control.{arm}')
    assert np.isclose(raw_quality['winner_nll'],saved['arms']['normalized_market_drop3']['quality']['winner_nll'],atol=5e-15,rtol=0)
    paired,diagnostic=bootstrap(tables),sensitivity(tables);equal(paired,saved['paired']['results'],'paired');equal(diagnostic,saved['sensitivity'],'sensitivity')
    return dict(auditor='market153_review',protocol_sha256=sha(OUT/'protocol.json'),summary_sha256=sha(OUT/'comparison/summary.json'),counters=counter,frozen_file_count=len(protocol['inputs_and_source_sha256']),independent_weights=weight_checks,independent_saved_body_replays=replay(),normalization_diagnostics=norms,raw_candidate_quality_diagnostic_only=raw_quality,points={a:saved['arms'][a]['policy'] for a in ARMS},paired=paired,sensitivity=diagnostic,no_model_fits_performed=True,can_adopt=False,untouched_holdout=False,blockers=[],audit_script_sha256=sha(__file__),independent143_helpers_sha256=sha(HELPER_PATH))


if __name__=='__main__':
    parser=argparse.ArgumentParser(__doc__);parser.add_argument('--self-test',action='store_true');args=parser.parse_args()
    if args.self_test:helpers.self_test()
    else:
        result=audit();output=Path(__file__).with_name('independent_final.json')
        with output.open('x') as stream:json.dump(result,stream,indent=2,ensure_ascii=False,allow_nan=False);stream.write('\n')
        print(json.dumps(dict(evidence=str(output),counters=result['counters'],blockers=[])))
