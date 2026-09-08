"""124: full v4 quality for fixed121 A/B coefficients; no fitting, historical research."""
from __future__ import annotations
import argparse
from copy import deepcopy
import datetime as dt
import gc
from pathlib import Path
import pickle
import time

import numpy as np
import pandas as pd
import extra_residual_probes as probe
import anchor_gap_recheck as a
import anchor_gap_summary as aggregation
import gap_seed_recheck as d
import gap_log_quality as g
import small_gain_stack as s
from horseracing_eval.dataset import population_masks

ROOT=s.ROOT
SPEC=ROOT/'specs/124-extra-residual-quality'
WORK=ROOT/'artifacts/124-extra-residual-quality'
SEEDS=[42,43,44]
CANDIDATES=['prior_gap','global_temperature']
CONTRASTS=[{'id':'anchor','baseline':'anchor'},{'id':'retained','baseline':'retained'}]
write_json=g.screen.write_json


def load_config():
    cfg=s.read_json(SPEC/'gate-config.json');expected=(SPEC/'gate-config.hash.txt').read_text().strip()
    if s.p.gate_config_hash(cfg)!=expected:raise ValueError('124 config changed')
    s.p.assert_confirmatory(cfg,expected_hash=expected,eval_window=cfg['eval_window'])
    s.p.assert_delta_provenance(cfg,root=ROOT)
    old=d.load_config()
    for key in ('arms','eval_window','bootstrap','seed_noise','min_effect_delta','recent_guard','top_noninferior','calibration','subgroup_guard'):
        if cfg[key]!=old[key]:raise ValueError(f'124 fixed quality recipe changed: {key}')
    if (cfg['candidates']!=CANDIDATES or cfg['seeds']!=SEEDS or cfg['contrasts']!=CONTRASTS
        or cfg['new_fit_jobs']!=0 or cfg['coefficient_refits']!=0 or cfg['assembly_eps']!=0.
        or cfg['can_adopt'] is not False or cfg['eligible_for_verdict'] is not False):
        raise ValueError('124 fixed candidate/seed/research scope changed')
    return cfg


def source_hash():
    return s.p.stable_hash({'driver':s.p.digest(__file__),'121_source':probe.source_hash(),
                            'summary_method':s.p.digest(aggregation.__file__)})


def report121():
    cfg=probe.load_config()
    return probe.verified_output(probe.SPEC/'evidence/residual-probes.json',probe.WORK/'result-receipt.json',cfg)


def selected_candidates(report):
    selected=[c for c in probe.CANDIDATES if report['summary'][c]['state']=='ADVANCE_TO_FULL_QUALITY']
    if selected!=CANDIDATES:raise ValueError('124 requires exact121 prespecified A/B progression; no manual candidate selection')
    if any(report['seed_results'][str(seed)][c]['state']!='COMPLETE' for seed in SEEDS for c in CANDIDATES):
        raise ValueError('All retained121 coefficient sequences must be complete')
    return selected


def source_state():
    _,frozen=probe.verify();report=report121();selected_candidates(report)
    review_path=probe.SPEC/'evidence/independent-review.json';review=s.read_json(review_path)
    output=probe.SPEC/'evidence/residual-probes.json';receipt=probe.WORK/'result-receipt.json'
    if (review.get('artifact_kind')!='extra_residual_probe_independent_review' or review.get('status')!='PASS'
        or review.get('can_adopt') is not False or review.get('eligible_for_verdict') is not False
        or review.get('additional_fits')!=0
        or review.get('method_sha256')!=s.p.digest(review_path.with_suffix('.py'))
        or review.get('run_freeze_sha256')!=s.p.digest(probe.WORK/'run-freeze.json')
        or review.get('report_sha256')!=s.p.digest(output) or review.get('receipt_sha256')!=s.p.digest(receipt)
        or review.get('prepared_input_sha256')!=frozen['prepared_input_sha256']
        or any(review['summary'][c]['state']!=report['summary'][c]['state'] for c in probe.CANDIDATES)):
        raise ValueError('121 independent review binding changed')
    if ({v['seed'] for v in review['seeds']}!=set(SEEDS)
        or any(v['candidates'][c]['state']!='PASS' for v in review['seeds'] for c in CANDIDATES)):
        raise ValueError('121 selected saved coefficients lack independent numerical PASS')
    paths=[probe.WORK/'run-freeze.json',output,receipt,review_path,review_path.with_suffix('.py')]
    return {'files':{str(p):s.p.digest(p) for p in paths},'population':frozen['sources']['population'],
        'snapshot_sha256':frozen['sources']['snapshot_sha256'],'prepared_input_sha256':frozen['prepared_input_sha256'],
        'selected_candidates':CANDIDATES,'selection_history':'All fixed121 candidates screened; only exact ADVANCE branches A/B proceed.'}


def coefficients(seed,candidate):
    result=report121()['seed_results'][str(seed)][candidate]
    if result['state']!='COMPLETE' or [v['year'] for v in result['folds']]!=list(range(2020,2027)):
        raise ValueError('Incomplete saved121 coefficient folds')
    for fold in result['folds']:
        beta=np.asarray(fold['beta'])
        if (beta.shape!=(2,) or not np.isfinite(beta).all() or fold['context_edges'] is not None
            or fold['fit_last_day']>=fold['eval_first_day']):raise ValueError('Invalid frozen A/B coefficient/chronology')
    return result


def load_inputs():
    matrix,races,folds=s.inputs(s.load_config())
    augmented,audit=probe.prior_gap_features(matrix.frame)
    augmented=augmented.loc[augmented.race_date>=pd.Timestamp('2019-01-01')]
    lookup={(r.race_id,r.horse_id):(str(r.race_date.date()),float(r.gap_log),float(r.prior_gap_log)) for r in augmented.itertuples()}
    populations={};order=[]
    for year,fold in sorted(folds.items()):
        masks=[population_masks(r) for r in fold.valid]
        populations[str(year)]={'all_races':len(fold.valid),'eligible_races':sum(p.eligible for p in masks)}
        for race,pop in zip(fold.valid,masks,strict=True):
            h_values(race.context,lookup)
            if pop.eligible:order.append((race,pop))
    # All three frozen121 inputs used exactly these features and started-horse order.
    for seed in SEEDS:
        with probe.prepared_path(seed).open('rb') as f:prepared=pickle.load(f)
        if len(prepared)!=len(order):raise ValueError('124 feature population differs from121')
        for record,(race,pop) in zip(prepared,order,strict=True):
            gap,prior=h_values(race.context,lookup)
            if ((record['race_id'],record['day'])!=(race.context.race_id,str(race.context.race_date))
                or not np.array_equal(gap,record['gap'],equal_nan=True)
                or not np.array_equal(prior,record['prior_gap'],equal_nan=True)
                or record['winner']!=pop.started_horse_ids.index(pop.winner_horse_id)):
                raise ValueError('124 gap/prior-gap/order differs from121 prepared source')
        del prepared
    del augmented,order;gc.collect()
    return matrix,races,folds,lookup,populations,audit


def h_values(context,lookup):
    keys=[(context.race_id,h.horse_id) for h in context.started_horses]
    if any(k not in lookup or lookup[k][0]!=str(context.race_date) for k in keys):
        raise ValueError('124 feature date/horse scope differs')
    h=np.array([lookup[k][1:] for k in keys],dtype=float)
    if h.shape!=(len(keys),2) or np.isinf(h).any():raise ValueError('Invalid124 feature matrix')
    return h[:,0],h[:,1]


def assemble(base,context,lookup,candidate,fold):
    ids=[h.horse_id for h in context.started_horses]
    p=g.screen.validate_probabilities(base,ids);gap,prior=h_values(context,lookup)
    row=probe.Row(context.race_id,str(context.race_date),p,gap,prior,0)
    beta=np.array(fold['beta']);probe.effective_temperature(row,candidate,beta)
    q=probe.corrected_p(row,probe.design(row,candidate),beta)
    out=g.assemble_predictions(ids,q,eps=0.)
    values=np.array([[out[h].win,out[h].top2,out[h].top3] for h in ids])
    if (not np.isfinite(values).all() or (values < -1e-10).any() or (values>1+1e-10).any()
        or (np.diff(values,axis=1)<-1e-12).any()
        or not np.allclose(values.sum(axis=0),[min(k,len(ids)) for k in (1,2,3)],atol=1e-8,rtol=0)
        or not np.allclose(values[:,0],q,atol=1e-12,rtol=0)):
        raise ValueError('124 three-head probability consistency failed')
    return out,{'below_legacy_clip_horses':int((q<g.DEFAULT_CLIP).sum()),'max_post_assembly_win_change':float(np.max(np.abs(values[:,0]-q)))}


class Predictor:
    is_leaky_reference=False
    def __init__(self,base,lookup,candidate,fold,audit):self.base,self.lookup,self.candidate,self.fold,self.audit=base,lookup,candidate,fold,audit
    def predict_race(self,context):
        if context.race_date.year!=self.fold['year']:raise ValueError('124 coefficient applied to wrong year')
        result,audit=assemble(self.base.predict_race(context),context,self.lookup,self.candidate,self.fold)
        self.audit[context.race_id]=audit
        return result


class Factory:
    def __init__(self,base,lookup,candidate,coef):
        if candidate not in CANDIDATES or coef['candidate_id']!=candidate or coef['state']!='COMPLETE':
            raise ValueError('124 candidate coefficient mismatch')
        self.base,self.lookup,self.candidate,self.coef=base,lookup,candidate,coef
        self.expected_columns=base.expected_columns;self.audit={}
        self.recipe_meta={'base_recipe':base.recipe_meta,'correction':{'candidate':candidate,'coefficient_source':'frozen121',
            'folds':coef['folds'],'assembly_eps':0.,'topk':'Harville','new_coefficient_fit':False}}
        self.recipe_hash=s.p.stable_hash(self.recipe_meta)
    def fit(self,train_races,*,num_threads=None):
        year=max(r.race_date.year for r in train_races)+1
        fold=next((v for v in self.coef['folds'] if v['year']==year),None)
        if fold is None:raise ValueError('Missing124 saved annual coefficient')
        return Predictor(self.base.fit(train_races,num_threads=1),self.lookup,self.candidate,fold,self.audit)


def verify():
    cfg=load_config();frozen=s.read_json(WORK/'run-freeze.json')
    if (frozen['source_hash']!=source_hash() or frozen['config_hash']!=s.p.gate_config_hash(cfg)
        or frozen['runtime']!=probe.runtime() or frozen['sources']!=source_state()):
        raise ValueError('124 frozen method/config/runtime/source changed')
    return cfg,frozen


def prepare():
    if (WORK/'run-freeze.json').exists():verify();return
    cfg=load_config();before,state=source_hash(),source_state()
    matrix,races,folds,lookup,population,audit=load_inputs()
    if any(population[str(y)][k]!=state['population'][str(y)][k] for y in probe.YEARS for k in ('all_races','eligible_races')):
        raise ValueError('124 input population differs')
    frozen={'source_hash':before,'config_hash':s.p.gate_config_hash(cfg),'runtime':probe.runtime(),'sources':state,'jobs':[]}
    gap_lookup={k:(dt.date.fromisoformat(day),gap) for k,(day,gap,prior) in lookup.items()}
    frozen['baseline_parity']=a.certify_baselines(cfg,frozen,matrix,races,folds,gap_lookup)
    frozen['input_audit']=audit
    del matrix,races,folds,lookup,gap_lookup;gc.collect()
    if before!=source_hash() or state!=source_state():raise ValueError('124 source changed during preparation')
    frozen.update(artifact_kind='extra_residual_quality_freeze',can_adopt=False,eligible_for_verdict=False,
                  prepared_at=dt.datetime.now(dt.timezone.utc).isoformat())
    write_json(WORK/'run-freeze.json',frozen)
    print('PREPARE124 fixed A/B saved coefficients, all inputs and old baselines; no fits',flush=True)


def area(seed,candidate):return WORK/candidate/f'seed-{seed}'
def result_path(seed,candidate,contrast):return SPEC/'evidence'/f'{candidate}-seed-{seed}-{contrast}.json'


def validate_rows(rows,seed,candidate,contrast):
    old=s.read_json(s.read_json(d.result_path(seed,'anchor'))['evidence_path'])['rows']
    screen=report121()['seed_results'][str(seed)][candidate]['rows']
    identity=lambda records,day:[(r['race_id'],r[day]) for r in records]
    if identity(rows,'race_day')!=identity(old,'race_day') or identity(rows,'race_day')!=identity(screen,'day'):
        raise ValueError('124 full eligible row population differs from121/116')
    key='active_winner_nll' if contrast=='anchor' else 'candidate_winner_nll'
    for new,base,previous in zip(rows,old,screen,strict=True):
        if new['active_winner_nll']!=base[key]:raise ValueError('124 baseline differs from116')
        if abs(new['candidate_winner_nll']-previous['candidate_nll'])>1e-12:
            raise ValueError('124 saved coefficient NLL differs from121')
        if abs(new['diff']-(new['candidate_winner_nll']-new['active_winner_nll']))>1e-12:
            raise ValueError('124 loss difference inconsistent')


def validate_full(report,seed):
    original=s.read_json(d.result_path(seed,'anchor'))
    if any(report[k]!=original[k] for k in ('n_races','n_eligible','race_id_set_hash')):
        raise ValueError('124 full scored population changed, including noneligible races')


def provenance(seed,candidate):
    return {'source121_report_sha256':s.p.digest(probe.SPEC/'evidence/residual-probes.json'),
            'coefficient_sha256':s.p.stable_hash(coefficients(seed,candidate)),
            'old_gap_coefficient_sha256':s.p.digest(a.old_coefficient_path(seed))}


def verified_result(seed,candidate,contrast,cfg,frozen):
    path=result_path(seed,candidate,contrast)
    if not path.exists():return False
    evidence=area(seed,candidate)/f'{contrast}-evidence.json';receipt=area(seed,candidate)/f'{contrast}-receipt.json'
    r=s.read_json(path)
    expected={'report_sha256':s.p.digest(path),'evidence_sha256':s.p.digest(evidence),'freeze_sha256':s.p.digest(WORK/'run-freeze.json')}
    if (s.read_json(receipt)!=expected or r.get('artifact_kind')!='extra_residual_quality_report'
        or r.get('can_adopt') is not False or r.get('eligible_for_verdict') is not False
        or r.get('training_seed')!=seed or r.get('candidate_id')!=candidate or r.get('contrast')!=contrast
        or r.get('study_config_hash')!=s.p.gate_config_hash(cfg)
        or r.get('seed_config_hash')!=s.p.gate_config_hash(a.seed_config(cfg,seed))
        or r.get('run_freeze_sha256')!=expected['freeze_sha256'] or r.get('coefficient_provenance')!=provenance(seed,candidate)
        or r.get('evidence_path')!=str(evidence) or r.get('evidence_sha256')!=expected['evidence_sha256']
        or r.get('research_disposition')!=s.research.assess_research(r)):
        raise ValueError('124 result receipt/scope/coefficient changed')
    validate_full(r,seed);validate_rows(s.read_json(evidence)['rows'],seed,candidate,contrast)
    return True


def summarize_candidate(reports):
    result=aggregation.summarize_reports(reports)
    states=[result['contrasts'][c['id']]['state'] for c in CONTRASTS]
    state='REVIEW_REQUIRED' if 'REVIEW_REQUIRED' in states else 'RETAIN' if all(v=='MEAN_IMPROVEMENT' for v in states) else 'DEFER'
    return {'state':state,'contrasts':result['contrasts'],'can_adopt':False,'eligible_for_verdict':False}


def summary():
    cfg,frozen=verify();candidates={};hashes={}
    for candidate in CANDIDATES:
        reports,evidence,legacy={},{},{};hashes[candidate]={}
        for seed in SEEDS:
            reports[seed],evidence[seed]={},{};hashes[candidate][str(seed)]={}
            legacy[seed]=s.read_json(s.read_json(d.result_path(seed,'anchor'))['evidence_path'])
            for contrast in CONTRASTS:
                name=contrast['id']
                if not verified_result(seed,candidate,name,cfg,frozen):raise ValueError('124 all12 full comparisons required')
                path=result_path(seed,candidate,name);r=s.read_json(path)
                reports[seed][name]=r;evidence[seed][name]=s.read_json(r['evidence_path'])
                hashes[candidate][str(seed)][name]={'report_sha256':s.p.digest(path),'evidence_sha256':s.p.digest(r['evidence_path'])}
        population=aggregation.validate_population(reports,evidence,legacy)
        candidates[candidate]={**summarize_candidate(reports),'population':population}
    result={'artifact_kind':'extra_residual_quality_summary','can_adopt':False,'eligible_for_verdict':False,
        'candidates':candidates,'retained_candidates':[c for c in CANDIDATES if candidates[c]['state']=='RETAIN'],
        'candidate_order_is_priority':False,'research_preference':'All passing candidates retained without choosing a winner.',
        'sources':hashes,'run_freeze_sha256':s.p.digest(WORK/'run-freeze.json'),'config_hash':s.p.gate_config_hash(cfg),
        'limitations':cfg['limitations']}
    path=SPEC/'verdict.json';verify()
    if path.exists():
        if s.read_json(path)!=result:raise ValueError('Existing124 summary changed')
    else:write_json(path,result)
    print({c:v['state'] for c,v in candidates.items()},flush=True)


def evaluate():
    cfg,frozen=verify()
    if all(verified_result(seed,candidate,c['id'],cfg,frozen) for candidate in CANDIDATES for seed in SEEDS for c in CONTRASTS):
        print('124 completed outputs verified; no evaluation repeat',flush=True);return
    matrix,races,folds,lookup,population,audit=load_inputs()
    if audit!=frozen['input_audit']:raise ValueError('124 frozen features changed')
    gap_lookup={k:(dt.date.fromisoformat(day),gap) for k,(day,gap,prior) in lookup.items()}
    for candidate in CANDIDATES:
        for seed in SEEDS:
            coef=coefficients(seed,candidate)
            for contrast in CONTRASTS:
                name=contrast['id']
                if verified_result(seed,candidate,name,cfg,frozen):continue
                evidence=area(seed,candidate)/f'{name}-evidence.json';receipt=area(seed,candidate)/f'{name}-receipt.json'
                if evidence.exists() or receipt.exists():raise ValueError('124 orphan evidence/receipt; preserve for diagnosis')
                cand=Factory(a.retained_factory(matrix,races,seed),lookup,candidate,coef)
                base=a.AnchorFactory(cfg,frozen,matrix,races,seed) if name=='anchor' else a.old_tilt(matrix,races,gap_lookup,seed)
                start=time.monotonic()
                report=s.p.paired_eval(cand,base,races,gate_config=a.seed_config(cfg,seed),first_valid_year=2020,
                    valid_from=dt.date(2020,1,1),subgroups=True,num_threads=1,
                    snapshot={'run_freeze_sha256':s.p.digest(WORK/'run-freeze.json'),'seed':seed,'candidate':candidate,'contrast':name,
                              'coefficient_provenance':provenance(seed,candidate),'evidence_regime':'historical_development_full_information'})
                rows=report.evidence.to_dict()['rows'];validate_rows(rows,seed,candidate,name)
                result=report.to_dict();validate_full(result,seed)
                result.pop('evidence',None);result.pop('diffs_by_day',None);result['gate_readout']=result.pop('decision')
                result.update(artifact_kind='extra_residual_quality_report',can_adopt=False,eligible_for_verdict=False,
                    candidate_id=candidate,training_seed=seed,deployment_seed=42,contrast=name,
                    study_config_hash=s.p.gate_config_hash(cfg),seed_config_hash=s.p.gate_config_hash(a.seed_config(cfg,seed)),
                    run_freeze_sha256=s.p.digest(WORK/'run-freeze.json'),coefficient_provenance=provenance(seed,candidate),
                    candidate_columns=cand.expected_columns,baseline_columns=base.expected_columns,
                    evidence_regime='historical_development_full_information',limitations=cfg['limitations'],
                    assembly_audit={'races':len(cand.audit),'assembly_eps':0.,
                        'below_legacy_clip_horses':sum(v['below_legacy_clip_horses'] for v in cand.audit.values()),
                        'max_post_assembly_win_change':max(v['max_post_assembly_win_change'] for v in cand.audit.values())},
                    elapsed_seconds=time.monotonic()-start)
                result['research_disposition']=s.research.assess_research(result);verify()
                write_json(evidence,report.evidence.to_dict());result.update(evidence_path=str(evidence),evidence_sha256=s.p.digest(evidence))
                path=result_path(seed,candidate,name);write_json(path,result)
                write_json(receipt,{'report_sha256':s.p.digest(path),'evidence_sha256':s.p.digest(evidence),'freeze_sha256':s.p.digest(WORK/'run-freeze.json')})
                print(f"RESULT124 {candidate} seed={seed} {name}: {result['periods']['all']['diff']:+.8f} {result['research_disposition']['state']}",flush=True)
                del cand,base,report;gc.collect()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['prepare','evaluate','summary'])
    {'prepare':prepare,'evaluate':evaluate,'summary':summary}[parser.parse_args().action]()
