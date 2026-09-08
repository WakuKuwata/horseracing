"""Independent124 all-head replay of saved121 coefficients; no parameter/model fitting."""
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
import extra_residual_quality as driver
from horseracing_eval.dataset import population_masks
from horseracing_eval.gates import _window_start
from horseracing_eval.paired import _score_arm
from horseracing_training.calib_split import assemble_predictions

HELPER=ROOT/'specs/118-anchor-gap-recheck/evidence/independent-review.py'
common=types.ModuleType('audit118_primitives');common.__file__=str(HELPER)
exec(compile(HELPER.read_text(),str(HELPER),'exec'),common.__dict__)
close,mean=common.close,common.mean


def apply_saved(base,context,lookup,candidate,fold):
    ids=[h.horse_id for h in context.started_horses];p=common.check_probabilities(base,ids)
    gap,prior=[],[]
    for horse in ids:
        day,g,v=lookup[(context.race_id,horse)];assert day==str(context.race_date)
        gap.append(0. if math.isnan(g) else g);prior.append(0. if math.isnan(v) else v)
    beta=fold['beta'];assert len(beta)==2 and np.isfinite(beta).all() and fold['year']==context.race_date.year
    assert fold['fit_last_day']<fold['eval_first_day']
    if candidate=='global_temperature':
        assert 1.+beta[1]>0
        x=np.log(p);second=x-mean(x)
    else:second=prior
    offsets=np.array([float(beta[0])*g+float(beta[1])*v for g,v in zip(gap,second,strict=True)])
    weights=p*np.exp(offsets-offsets.max());q=weights/math.fsum(weights)
    assert (q>0).all() and (len(q)==1 or (q<1).all());close(q.sum(),1.)
    result=assemble_predictions(ids,q,eps=0.)
    win=common.check_probabilities(result,ids)
    return result,int((q<1e-6).sum()),float(np.max(np.abs(win-q)))


def main():
    output=Path(__file__).with_suffix('.json');assert not output.exists(),'Preserve existing124 independent review'
    summary_path=driver.SPEC/'verdict.json';stored=driver.s.read_json(summary_path);summary_hash=driver.s.p.digest(summary_path)
    cfg,frozen=driver.verify();report_hashes={};reports={};evidence={}
    for candidate in driver.CANDIDATES:
        report_hashes[candidate],reports[candidate],evidence[candidate]={},{},{}
        for seed in driver.SEEDS:
            report_hashes[candidate][str(seed)],reports[candidate][seed],evidence[candidate][seed]={},{},{}
            for contrast in driver.CONTRASTS:
                name=contrast['id'];assert driver.verified_result(seed,candidate,name,cfg,frozen)
                path=driver.result_path(seed,candidate,name);r=driver.s.read_json(path)
                reports[candidate][seed][name]=r;evidence[candidate][seed][name]=driver.s.read_json(r['evidence_path'])
                report_hashes[candidate][str(seed)][name]={'report_sha256':driver.s.p.digest(path),'evidence_sha256':driver.s.p.digest(r['evidence_path'])}
    assert stored['sources']==report_hashes
    matrix,races,folds,lookup,population,audit=driver.load_inputs()
    assert audit==frozen['input_audit']
    for y in driver.probe.YEARS:
        for k in ('all_races','eligible_races'):assert population[str(y)][k]==frozen['sources']['population'][str(y)][k]
    del matrix,races;gc.collect()
    old113=driver.s.read_json(driver.s.WORK/'run-freeze.json');old116=driver.s.read_json(driver.d.WORK/'run-freeze.json')
    seed_audits=[];metrics={c:{} for c in driver.CANDIDATES};losses={c:{} for c in driver.CANDIDATES}
    for seed in driver.SEEDS:
        coefficients={c:driver.coefficients(seed,c) for c in driver.CANDIDATES}
        old_coef=driver.a.old_coefficients(seed);pred={c:{} for c in ['anchor','retained']+driver.CANDIDATES}
        valid=[];below={c:0 for c in driver.CANDIDATES};rounding={c:0. for c in driver.CANDIDATES};max_error=0.
        for candidate in driver.CANDIDATES:losses[candidate][seed]={}
        for year,fold in sorted(folds.items()):
            if year<2020:continue
            caches={arm:driver.s.reuse.load(common.cache_path(seed,arm,year,{},old116,old113)) for arm in ('anchor','pruning')}
            for arm in caches:assert set(caches[arm]['predictions'])=={r.context.race_id for r in fold.valid}
            annual={c:next(f for f in coefficients[c]['folds'] if f['year']==year) for c in driver.CANDIDATES}
            for race in fold.valid:
                ctx,pop=race.context,population_masks(race);ids=[h.horse_id for h in ctx.started_horses]
                base=caches['pruning']['predictions'][ctx.race_id]
                anchor=caches['anchor']['predictions'][ctx.race_id];common.check_probabilities(anchor,ids)
                gap=np.array([0. if math.isnan(lookup[(ctx.race_id,h)][1]) else lookup[(ctx.race_id,h)][1] for h in ids])
                retained,_,_=common.tilt(base,ids,gap,old_coef['gammas'][str(year)])
                pred['anchor'][ctx.race_id]=anchor;pred['retained'][ctx.race_id]=retained
                valid.append(race)
                for candidate in driver.CANDIDATES:
                    result,low,change=apply_saved(base,ctx,lookup,candidate,annual[candidate])
                    pred[candidate][ctx.race_id]=result;below[candidate]+=low;rounding[candidate]=max(rounding[candidate],change)
                    if pop.eligible:
                        values={'candidate':common.nll(result[pop.winner_horse_id].win),
                                'anchor':common.nll(anchor[pop.winner_horse_id].win),'retained':common.nll(retained[pop.winner_horse_id].win)}
                        losses[candidate][seed][ctx.race_id]=values
            del caches
        assert len(valid)==23030 and all(len(losses[c][seed])==22990 for c in driver.CANDIDATES)
        scores={c:_score_arm(valid,ps,band_edges=[.05,.15,.30]) for c,ps in pred.items()}
        for candidate in driver.CANDIDATES:
            metrics[candidate][seed]={}
            for contrast in driver.CONTRASTS:
                name=contrast['id'];r=reports[candidate][seed][name];rows=evidence[candidate][seed][name]['rows']
                assert [(x['race_id'],x['race_day']) for x in rows]==[(v.context.race_id,str(v.context.race_date)) for v in valid if population_masks(v).eligible]
                for row in rows:
                    v=losses[candidate][seed][row['race_id']]
                    for actual,expected in [(v['candidate'],row['candidate_winner_nll']),(v[name],row['active_winner_nll']),(v['candidate']-v[name],row['diff'])]:
                        close(actual,expected);max_error=max(max_error,abs(actual-expected))
                cs,bs=scores[candidate],scores[name]
                quality=[cs.top2_logloss-bs.top2_logloss,cs.top3_logloss-bs.top3_logloss,cs.ece_equal_width_like['ece'],bs.ece_equal_width_like['ece']]
                metrics[candidate][seed][name]=quality
                for value,key in zip(quality,('top2_diff','top3_diff','cand_ece','act_ece'),strict=True):close(value,r['gate']['reasons'][key])
                close(cs.winner_nll,r['periods']['all']['candidate']);close(bs.winner_nll,r['periods']['all']['active'])
                primary=common.bootstrap(rows)
                for key,limits in [('bootstrap_ci',primary['sample']),('total_ci',primary['total'])]:
                    close(limits[0],r[key]['ci_low']);close(limits[1],r[key]['ci_high'])
                for years in (3,5):
                    cutoff=str(_window_start(dt.date(2026,8,23),years));subset=[x for x in rows if x['race_day']>=cutoff]
                    close(mean([losses[candidate][seed][x['race_id']]['candidate'] for x in subset]),r['periods'][f'recent_{years}y']['candidate'])
                    close(mean([losses[candidate][seed][x['race_id']][name] for x in subset]),r['periods'][f'recent_{years}y']['active'])
                assert r['assembly_audit']['below_legacy_clip_horses']==below[candidate]
                close(rounding[candidate],r['assembly_audit']['max_post_assembly_win_change'])
        seed_audits.append({'seed':seed,'all_races':len(valid),'eligible_races':22990,'max_nll_error':max_error,
                           'top2_top3_ece_recent_primary_sample_total_ci_match':True})
        del scores,pred,valid;gc.collect();print(f'124 independent all-head PASS seed={seed}',flush=True)
    def quality_pass(v):return v[0]<=.0005 and v[1]<=.0005 and v[2]-v[3]<=.001 and v[2]<.05
    decisions={}
    for candidate in driver.CANDIDATES:
        result=stored['candidates'][candidate]
        expected=driver.summarize_candidate(reports[candidate])
        for key,value in expected.items():assert result[key]==value
        states={}
        for contrast in driver.CONTRASTS:
            name=contrast['id'];values=[metrics[candidate][seed][name] for seed in driver.SEEDS]
            mq=[mean([v[k] for v in values]) for k in range(4)]
            blocked=any(reports[candidate][s][name]['research_disposition']['state']=='BLOCKED' or not quality_pass(metrics[candidate][s][name]) for s in driver.SEEDS)
            diff=mean([mean([v['candidate']-v[name] for v in losses[candidate][seed].values()]) for seed in driver.SEEDS])
            close(diff,result['contrasts'][name]['equal_seed_mean_periods']['all']['diff']['mean'])
            state='REVIEW_REQUIRED' if blocked or not quality_pass(mq) else 'MEAN_IMPROVEMENT' if diff<0 else 'MEAN_NOT_IMPROVED'
            assert result['contrasts'][name]['state']==state;states[name]=state
        decision='REVIEW_REQUIRED' if 'REVIEW_REQUIRED' in states.values() else 'RETAIN' if all(s=='MEAN_IMPROVEMENT' for s in states.values()) else 'DEFER'
        assert result['state']==decision;decisions[candidate]=decision
    assert stored['retained_candidates']==[c for c in driver.CANDIDATES if decisions[c]=='RETAIN']
    assert stored['candidate_order_is_priority'] is False and stored['can_adopt'] is False and stored['eligible_for_verdict'] is False
    driver.verify()
    assert driver.s.p.digest(summary_path)==summary_hash,'Summary changed during independent audit'
    for candidate in driver.CANDIDATES:
        for seed in driver.SEEDS:
            for contrast in driver.CONTRASTS:
                name=contrast['id'];assert driver.verified_result(seed,candidate,name,cfg,frozen)
                path=driver.result_path(seed,candidate,name);r=driver.s.read_json(path)
                assert report_hashes[candidate][str(seed)][name]=={'report_sha256':driver.s.p.digest(path),'evidence_sha256':driver.s.p.digest(r['evidence_path'])}
    driver.write_json(output,{'artifact_kind':'extra_residual_quality_independent_review','status':'PASS','can_adopt':False,'eligible_for_verdict':False,
        'method_sha256':driver.s.p.digest(__file__),'helper_sha256':driver.s.p.digest(HELPER),
        'run_freeze_sha256':driver.s.p.digest(driver.WORK/'run-freeze.json'),'summary_sha256':summary_hash,
        'report_hashes':report_hashes,'additional_fits':0,'seeds':seed_audits,'candidate_decisions':decisions,
        'max_scalar_error':common.MAX_ERROR,'limitations':['Full probability and primary/recent/quality scalar replay; critical subgroup disposition uses the frozen tested v4 evaluator.',
            'Saved coefficient numeric/strict-prior proof is inherited from SHA-bound independent121 PASS.',
            'Mean losses are not probability ensembles; transferred noise and fixed-coefficient bootstrap do not cover all refit/selection uncertainty.']})


if __name__=='__main__':main()
