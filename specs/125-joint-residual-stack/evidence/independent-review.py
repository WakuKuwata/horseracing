"""125 saved-coefficient numerical audit; scalar reconstruction and no refitting."""
from __future__ import annotations
import datetime as dt
import gc
import math
from pathlib import Path
import sys
import types

import numpy as np
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts'))
import joint_residual_stack as driver
from horseracing_eval.dataset import population_masks
from horseracing_eval.paired import _score_arm
from horseracing_eval.gates import _window_start
from horseracing_training.calib_split import assemble_predictions

HELPER=ROOT/'specs/120-probability-mixture/evidence/independent-review.py'
helper=types.ModuleType('mixture120_audit_primitives');helper.__file__=str(HELPER)
exec(compile(HELPER.read_text(),str(HELPER),'exec'),helper.__dict__)
common=helper.common
close,mean=common.close,common.mean
SEEDS=(42,43,44)


def scalar_design(raw,p,selected):
    # raw columns are gap/prior-gap/female_sin/female_cos; their source formulas
    # and same-ID prior history were independently audited in119/121/124.
    rows=[];lp=[math.log(float(v)) for v in p];center=mean(lp)
    for index,values in enumerate(raw):
        gap,prior,sine,cosine=[0. if math.isnan(v) else float(v) for v in values]
        row=[gap]+([prior] if 'prior_gap' in selected else [])+[sine,cosine]
        if 'global_temperature' in selected:row.append(lp[index]-center)
        rows.append(row)
    h=np.asarray(rows)
    assert np.isfinite(h).all()
    return h


def scalar_vector(base,ids,h,beta):
    p=common.check_probabilities(base,ids)
    z=[math.fsum(float(x)*float(b) for x,b in zip(row,beta,strict=True)) for row in h]
    peak=max(z);weights=[float(p[i])*math.exp(v-peak) for i,v in enumerate(z)]
    denominator=math.fsum(weights);q=[v/denominator for v in weights]
    assert all(math.isfinite(v) and v>0 and (len(q)==1 or v<1) for v in q)
    close(math.fsum(q),1.)
    result=assemble_predictions(ids,q,eps=0.)
    common.check_probabilities(result,ids)
    return result


def main():
    output=Path(__file__).with_suffix('.json')
    assert not output.exists(),'Preserve previous independent review'
    assert (driver.SPEC/'verdict.json').exists(),'Wait for parent125 summary completion'
    cfg,frozen=driver.verify();selected=frozen['sources']['selected'];comparisons=frozen['comparisons']
    reports,evidence,hashes={},{},{}
    for c in comparisons:
        assert driver.verified_result(c,cfg,frozen)
        p=driver.result_path(c);r=reports[c['id']]=driver.s.read_json(p)
        evidence[c['id']]=driver.s.read_json(r['evidence_path'])
        hashes[c['id']]={'report_sha256':driver.s.p.digest(p),'evidence_sha256':driver.s.p.digest(r['evidence_path'])}
    summary_sha=driver.s.p.digest(driver.SPEC/'verdict.json')
    freeze_sha=driver.s.p.digest(driver.WORK/'run-freeze.json')
    stored=driver.s.read_json(driver.SPEC/'verdict.json')
    assert stored['sources']==hashes and stored['run_freeze_sha256']==freeze_sha
    assert stored['population']=={'n_races':23030,'n_eligible':22990,'n_days':715,'same_ordered_population':True}
    coefs={seed:driver.verified_coefficients(seed,frozen) for seed in SEEDS}
    old119=driver.s.read_json(driver.n.WORK/'run-freeze.json')
    old118=driver.s.read_json(driver.a.WORK/'run-freeze.json')
    old116=driver.s.read_json(driver.a.d.WORK/'run-freeze.json')
    old113=driver.s.read_json(driver.s.WORK/'run-freeze.json')
    season_coefs={seed:driver.n.verified_coefficients(seed,old119) for seed in SEEDS}
    pruning_coefs={seed:driver.a.old_coefficients(seed) for seed in SEEDS}
    anchor_coefs={seed:driver.a.verified_coefficients(seed,old118) for seed in SEEDS}
    additional={(seed,c):driver.q.coefficients(seed,c) for seed in SEEDS for c in selected}
    matrix,races,folds,lookup,population,input_audit=driver.load_inputs()
    assert input_audit==frozen['input_audit']
    for year,pop in population.items():
        for k in ('all_races','eligible_races'):assert pop[k]==frozen['sources']['population'][year][k]
    del matrix,races;gc.collect()
    names=[f'{name}:{seed}' for seed in SEEDS for name in ('new','season',*selected)]+['new_mixture','joint_mixed6','mixed6','anchor42']
    predictions={name:{} for name in names};losses={};valid=[]
    raw_rows={name:{r['race_id']:r for r in e['rows']} for name,e in evidence.items()}
    priors={seed:[] for seed in SEEDS};fit_checks={str(seed):[] for seed in SEEDS}
    maximum=0.;max_jensen=-math.inf
    def roles(c):
        return ((f"new:{c['seed']}",f"{c['baseline']}:{c['seed']}") if c['kind']=='joint' else ('new_mixture',c['baseline']))
    for year,fold in sorted(folds.items()):
        arms=('pruning',) if year==2019 else ('pruning','anchor')
        caches={(arm,seed):driver.s.reuse.load(common.cache_path(seed,arm,year,old118,old116,old113)) for arm in arms for seed in SEEDS}
        for cache in caches.values():assert set(cache['predictions'])=={r.context.race_id for r in fold.valid}
        block={seed:[] for seed in SEEDS}
        for race in fold.valid:
            ctx,pop=race.context,population_masks(race);ids=[h.horse_id for h in ctx.started_horses]
            assert all(lookup[(ctx.race_id,h)][0]==str(ctx.race_date) for h in ids)
            raw=np.asarray([lookup[(ctx.race_id,h)][1:] for h in ids]);neutral=np.nan_to_num(raw,nan=0.)
            current,old_pruning,old_anchor={},[],[]
            for seed in SEEDS:
                base=caches[('pruning',seed)]['predictions'][ctx.race_id]
                p=common.check_probabilities(base,ids);h=scalar_design(raw,p,selected)
                if pop.eligible:block[seed].append((str(ctx.race_date),p,h,ids.index(pop.winner_horse_id)))
                if year==2019:continue
                beta=coefs[seed]['gammas'][str(year)]
                if 'global_temperature' in selected:assert 1.+beta[-1]>0
                current[f'new:{seed}']=scalar_vector(base,ids,h,beta)
                current[f'season:{seed}']=scalar_vector(base,ids,neutral[:,[0,2,3]],season_coefs[seed]['gammas'][str(year)])
                for candidate in selected:
                    previous=additional[(seed,candidate)]['folds'][year-2020]
                    lp=np.asarray([math.log(float(v)) for v in p])
                    design=np.column_stack((neutral[:,0],neutral[:,1] if candidate=='prior_gap' else lp-mean(lp)))
                    current[f'{candidate}:{seed}']=scalar_vector(base,ids,design,previous['beta'])
                old_pruning.append(helper.scalar_tilt(base,ids,neutral[:,0],pruning_coefs[seed]['gammas'][str(year)]))
                old_anchor.append(helper.scalar_tilt(caches[('anchor',seed)]['predictions'][ctx.race_id],ids,neutral[:,0],anchor_coefs[seed]['gammas'][str(year)]))
            if year==2019:continue
            current['new_mixture'],excess=helper.scalar_mixture([current[f'new:{seed}'] for seed in SEEDS]+old_anchor,ids)
            max_jensen=max(max_jensen,excess)
            current['joint_mixed6'],_=helper.scalar_mixture([current[f'season:{seed}'] for seed in SEEDS]+old_anchor,ids)
            current['mixed6'],_=helper.scalar_mixture(old_pruning+old_anchor,ids)
            current['anchor42']=caches[('anchor',42)]['predictions'][ctx.race_id]
            for name,pred in current.items():predictions[name][ctx.race_id]=pred
            valid.append(race)
            assert all((ctx.race_id in values)==pop.eligible for values in raw_rows.values())
            if pop.eligible:
                values=losses[ctx.race_id]={name:common.nll(pred[pop.winner_horse_id].win) for name,pred in current.items()}
                for c in comparisons:
                    cand,base=roles(c);row=raw_rows[c['id']][ctx.race_id]
                    for field,value in [('candidate_winner_nll',values[cand]),('active_winner_nll',values[base]),('diff',values[cand]-values[base])]:
                        close(value,row[field]);maximum=max(maximum,abs(value-row[field]))
        for seed in SEEDS:
            prior=priors[seed]
            if year>2019:
                beta=np.asarray(coefs[seed]['gammas'][str(year)]);diagnostic=coefs[seed]['fit_diagnostics'][year-2020]
                assert diagnostic['eval_year']==year and diagnostic['fit_races']==len(prior)
                assert diagnostic['fit_last_day']==max(r[0] for r in prior)<min(r[0] for r in block[seed])==diagnostic['eval_first_day']
                terms=[];grad=np.zeros(len(beta))
                for day,p,h,winner in prior:
                    p=p/p.sum();z=h@beta;z-=z.max();weight=p*np.exp(z)
                    terms.append(float(-z[winner]+np.log(weight.sum())))
                    grad+=(weight/weight.sum())@h-h[winner]
                objective=mean(terms)+.5e-6*float(beta@beta)
                norm=float(np.max(np.abs(grad/len(prior)+1e-6*beta)))
                assert objective<=1e-8 and norm<=1e-5
                close(objective,diagnostic['regularized_fit_objective']);close(norm,diagnostic['gradient_inf'])
                fit_checks[str(seed)].append({'year':year,'fit_races':len(prior),'objective':objective,'gradient_inf':norm,
                    'temperature_exponent':float(1.+beta[-1]) if 'global_temperature' in selected else None})
            prior.extend(block[seed])
        del caches
        print(f'125 independent probabilities/coefficients PASS year={year}; no fit',flush=True)
    assert len(valid)==23030 and len(losses)==22990
    del priors;gc.collect()
    scores={name:_score_arm(valid,ps,band_edges=[.05,.15,.30]) for name,ps in predictions.items()}
    numeric_quality={}
    for c in comparisons:
        cand,base=roles(c);cs,bs=scores[cand],scores[base];r=reports[c['id']];q=r['gate']['reasons']
        values=[cs.top2_logloss-bs.top2_logloss,cs.top3_logloss-bs.top3_logloss,cs.ece_equal_width_like['ece'],bs.ece_equal_width_like['ece']]
        for v,expected in zip(values,[q[k] for k in ('top2_diff','top3_diff','cand_ece','act_ece')],strict=True):close(v,expected)
        close(cs.winner_nll,r['periods']['all']['candidate']);close(bs.winner_nll,r['periods']['all']['active'])
        numeric_quality[c['id']]=values
        ci=common.bootstrap(evidence[c['id']]['rows'])
        for key,limits in [('bootstrap_ci',ci['sample']),('total_ci',ci['total'])]:
            close(limits[0],r[key]['ci_low']);close(limits[1],r[key]['ci_high'])
        for years in (3,5):
            cutoff=str(_window_start(dt.date(2026,8,23),years));rows=[x for x in raw_rows[c['id']].values() if x['race_day']>=cutoff]
            close(mean([losses[x['race_id']][cand] for x in rows]),r['periods'][f'recent_{years}y']['candidate'])
            close(mean([losses[x['race_id']][base] for x in rows]),r['periods'][f'recent_{years}y']['active'])
        if c['kind']=='mixture':close(max_jensen,r['mixture_audit']['max_jensen_excess'])
    for key,value in driver.summarize_reports(reports,selected,frozen['sources']['prior_preference']).items():assert stored[key]==value
    def quality(v):return v[0]<=.0005 and v[1]<=.0005 and v[2]-v[3]<=.001 and v[2]<.05
    states=[]
    for baseline in ('season',*selected):
        keys=[f'joint-seed-{seed}-vs-{baseline}' for seed in SEEDS]
        bad=any(reports[k]['research_disposition']['state']=='BLOCKED' or not quality(numeric_quality[k]) for k in keys)
        bad|=not quality([mean([numeric_quality[k][i] for k in keys]) for i in range(4)])
        diff=mean([mean([v[f'new:{seed}']-v[f'{baseline}:{seed}'] for v in losses.values()]) for seed in SEEDS])
        states.append('REVIEW_REQUIRED' if bad else 'RETAIN' if diff<0 else 'DEFER')
    joint='REVIEW_REQUIRED' if 'REVIEW_REQUIRED' in states else 'RETAIN' if all(v=='RETAIN' for v in states) else 'DEFER'
    states=[]
    for baseline in driver.MIX_BASELINES:
        key=f'mixture-vs-{baseline}';bad=reports[key]['research_disposition']['state']=='BLOCKED' or not quality(numeric_quality[key])
        diff=mean([v['new_mixture']-v[baseline] for v in losses.values()])
        states.append('REVIEW_REQUIRED' if bad else 'RETAIN' if diff<0 else 'DEFER')
    mixture_state='REVIEW_REQUIRED' if 'REVIEW_REQUIRED' in states else 'RETAIN' if all(v=='RETAIN' for v in states) else 'DEFER'
    assert (joint,mixture_state)==(stored['joint_retention'],stored['mixture_retention'])
    attrs=driver.s.read_json(driver.a.diagnostic.WORK/'race-diagnostic.json')['rows']
    for group,rule in {'2026_all':lambda r:r['year']==2026,'2026_nakayama':lambda r:r['year']==2026 and r['venue']=='06',
        '2026_partial_relative':lambda r:r['year']==2026 and r['relative_coverage']=='(.5,1)'}.items():
        selected_rows=[r for r in attrs if rule(r)];saved=stored['fixed_diagnostics'][group]
        assert (len(selected_rows),len({r['race_day'] for r in selected_rows}))==(saved['n_races'],saved['n_days'])
        for c in comparisons:
            cand,base=roles(c);close(mean([losses[r['race_id']][cand]-losses[r['race_id']][base] for r in selected_rows]),saved['mean_diffs'][c['id']])
    driver.verify();assert all(driver.verified_result(c,cfg,frozen) for c in comparisons)
    assert driver.s.p.digest(driver.SPEC/'verdict.json')==summary_sha
    assert driver.s.p.digest(driver.WORK/'run-freeze.json')==freeze_sha
    for c in comparisons:
        assert driver.s.p.digest(driver.result_path(c))==hashes[c['id']]['report_sha256']
        assert driver.s.p.digest(reports[c['id']]['evidence_path'])==hashes[c['id']]['evidence_sha256']
    driver.write_json(output,{'status':'PASS','artifact_kind':'joint_residual_stack_independent_numeric_review','can_adopt':False,
        'eligible_for_verdict':False,'additional_fits':0,'method_sha256':driver.s.p.digest(__file__),
        'helper_method_sha256':driver.s.p.digest(HELPER),'run_freeze_sha256':driver.s.p.digest(driver.WORK/'run-freeze.json'),
        'summary_sha256':driver.s.p.digest(driver.SPEC/'verdict.json'),'report_hashes':hashes,'selected':selected,
        'checks':common.CHECKS,'max_numerical_error':common.MAX_ERROR,'max_race_nll_error':maximum,
        'fit_checks':fit_checks,'population':stored['population'],'full_quality_recent_primary_cis_and_fixed117_match':True,
        'joint_retention':joint,'mixture_retention':mixture_state,'no_new_model_or_coefficient_fit':True,
        'notes':['Scalar probability/mixture calculations and objective gradients are independently reconstructed.',
                 'Registered Harville/scoring primitives reused; input formulas rely on frozen119/121/124 audits.']})
    print(f'125 independent numerical PASS {output}',flush=True)


if __name__=='__main__':main()
