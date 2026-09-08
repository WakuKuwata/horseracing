"""129 six-member prospective confirmation; no fitting, DB writes or activation.

Only explicit reserve consumes the shared112 annual ledger. Draft/power/preflight
never reserve. Missing reviewed evidence means NOT_READY, never assumed approval.
"""
from __future__ import annotations
import argparse
import copy
import datetime as dt
import fcntl
import hashlib
import json
import math
from pathlib import Path
import statistics

import small_gain_budget as budget
import small_gain_policy as policy_api

ROOT=Path(__file__).resolve().parents[1]
LEDGER=policy_api.LEDGER
SCHEMA='mixture_confirmation_v1'
MEMBERS=[{'id':f'{label}-{seed}','branch':branch,'seed':seed} for label,branch in [('joint','pruning'),('anchor','anchor')] for seed in (42,43,44)]
NOISE={'method':'conservative_single_model_transfer','sd_fold':.001816,'k_seeds':1,'n_effective_folds':1,'independent_bundle_replicates':1,'variance_reduction_claimed':False}
EFFECTS=[.00025,.0005,.001,.002,.005,.01]


class ConfirmationError(ValueError):pass


def now():return dt.datetime.now(dt.timezone.utc)
def read(path):return json.loads(Path(path).read_text())
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def require(value,message):
    if not value:raise ConfirmationError(message)
def finite(value):return type(value) in (int,float) and math.isfinite(value)
def utc(value):
    value=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    require(value.tzinfo is not None and value.utcoffset()==dt.timedelta(0),'Timestamp must be UTC')
    return value


def write_new(path,value):
    """Exclusive append-only output; ledger persistence has its own atomic writer."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as stream:json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False);stream.write('\n')


def checked_ref(ref):
    require(isinstance(ref,dict) and isinstance(ref.get('path'),str),'Evidence reference requires path/hash')
    path=Path(ref['path']);require(path.is_absolute(),'Evidence path must be absolute')
    require(path.is_file() and digest(path)==ref.get('sha256'),'Evidence file missing or hash changed')
    return read(path)


def validate_bundle(path,expected_sha):
    from horseracing_serving.mixture_model import load_mixture_bundle
    load_mixture_bundle(path,expected_sha256=expected_sha)
    return read(path)


def validate_anchor(path,expected_sha):
    require(digest(path)==expected_sha,'Anchor file hash changed')
    anchor=read(path)
    require(anchor.get('schema_version')==1 and anchor.get('artifact_kind')=='mixture_shadow_anchor' and anchor.get('can_adopt') is False,'Invalid frozen serving anchor')
    require(bool(anchor.get('model_version')) and bool(anchor.get('files')),'Anchor artifacts required')
    require(utc(anchor['created_at'])<=now(),'Anchor cannot be created in the future')
    for name,sha in anchor['files'].items():
        require(Path(name).is_absolute() and Path(name).is_file() and digest(name)==sha,'Anchor artifact missing or changed')
    calibration=anchor.get('calibration',{})
    require(calibration.get('mode')=='frozen_stage_discount','Anchor must bind frozen actual stage discount')
    path=Path(calibration['artifact_path'])
    require(path.is_absolute() and path.is_file() and digest(path)==calibration.get('sha256'),'Anchor stage discount changed')
    require(dt.date.fromisoformat(calibration['asof_date'])<=utc(anchor['created_at']).date(),'Anchor calibration lookahead')
    return anchor


def draft(bundle_path,anchor_path,*,selection_used_through,start=None,end=None,min_days=None,regime='preweight'):
    bundle_path=Path(bundle_path).resolve();anchor_path=Path(anchor_path).resolve()
    bundle=read(bundle_path) if bundle_path.exists() else {}
    return {'schema_version':SCHEMA,'confirmation_method_sha256':digest(__file__),'bundle_id':bundle.get('bundle_id'),'bundle_path':str(bundle_path),
        'bundle_manifest_sha256':digest(bundle_path) if bundle_path.exists() else None,'anchor_path':str(anchor_path),
        'anchor_model_sha256':digest(anchor_path) if anchor_path.exists() else None,'selection_used_through':selection_used_through,
        'frozen_at':now().isoformat(),'member_seed_pairs':copy.deepcopy(MEMBERS),'distinct_component_seeds':3,
        'independent_bundle_replicates':1,'noise':copy.deepcopy(NOISE),'primary_regime':regime,
        'eval_window':{'from':start,'to':end,'min_eval_days':min_days},'power_effect':None,
        'evidence':{'power':None,'noise':None,'compatibility':None},'single_final_look':True,
        'estimand':'fixed_deployment_bundle_future_races_with_conservative_retraining_noise_transfer',
        'can_adopt':False,'eligible_for_verdict':False}


def required_days(day_sd,effect,*,confidence=.9875,target_power=.8,max_days=730):
    require(finite(day_sd) and day_sd>0 and finite(effect) and effect>0,'Positive day SD and power effect required')
    require(confidence in (.95,.9875) and finite(target_power) and .5<target_power<1,'Invalid confidence/power')
    require(type(max_days) is int and max_days>=2,'Positive practical day cap required')
    z=statistics.NormalDist().inv_cdf((1+confidence)/2);zp=statistics.NormalDist().inv_cdf(target_power)
    def meets(n):
        se=day_sd/math.sqrt(n)
        return effect>=z*math.hypot(se,NOISE['sd_fold'])+zp*se
    if effect<=z*NOISE['sd_fold']:
        return {'required_days':None,'practical':False,'reason':'BELOW_TRANSFERRED_NOISE_FLOOR'}
    hi=2
    while not meets(hi) and hi<10**9:hi*=2
    if not meets(hi):return {'required_days':None,'practical':False,'reason':'BEYOND_SEARCH_RANGE'}
    lo=2
    while lo<hi:
        mid=(lo+hi)//2
        if meets(mid):hi=mid
        else:lo=mid+1
    return {'required_days':lo,'practical':lo<=max_days,'reason':'FEASIBLE' if lo<=max_days else 'BEYOND_PRACTICAL_DAY_CAP'}


def power_plan(development_ref,*,effects=None,max_days=730):
    dev=checked_ref(development_ref);rows=dev.get('rows',[])
    require(dev.get('artifact_kind')=='mixture_power_development' and dev.get('primary_regime') in ('preweight','serving'),'Serving-regime development evidence required')
    require(rows and len({r['race_id'] for r in rows})==len(rows),'Unique development races required')
    days={}
    for r in rows:
        dt.date.fromisoformat(r['race_day'])
        a,b=r['candidate_winner_nll'],r['anchor_winner_nll']
        require(finite(a) and finite(b) and min(a,b)>=0,'Finite nonnegative saved NLL required')
        days.setdefault(r['race_day'],[]).append(a-b)
    require(len(days)>=2,'At least two development race days required')
    mean=math.fsum(v for values in days.values() for v in values)/len(rows);average_count=len(rows)/len(days)
    influence=[(math.fsum(values)-mean*len(values))/average_count for values in days.values()]
    sd=statistics.stdev(influence);require(sd>0,'Zero empirical day variance cannot establish power')
    effects=list(EFFECTS if effects is None else effects)
    require(effects and len(set(effects))==len(effects),'Unique power scenarios required')
    scenarios=[{'effect':effect,'confidence':confidence,'target_power':.8,**required_days(sd,effect,confidence=confidence,max_days=max_days)} for effect in effects for confidence in (.95,.9875)]
    return {'artifact_kind':'mixture_power_plan','bundle_manifest_sha256':dev['bundle_manifest_sha256'],
        'anchor_model_sha256':dev['anchor_model_sha256'],'primary_regime':dev['primary_regime'],'development':copy.deepcopy(development_ref),
        'n_days':len(days),'n_races':len(rows),'development_used_through':max(days),'day_cluster_influence_sd':sd,
        'observed_difference':mean,'noise':copy.deepcopy(NOISE),'max_practical_days':max_days,'scenarios':scenarios,
        'method':'Normal power approximation from unequal-cluster influence SD; not an achieved bootstrap power guarantee',
        'adoption_min_effect_delta':0.,'can_adopt':False,'eligible_for_verdict':False}


def validate_manifest(manifest,*,prospective=True):
    require(manifest.get('schema_version')==SCHEMA,'Dedicated mixture confirmation schema required')
    require(manifest.get('confirmation_method_sha256')==digest(__file__),'Confirmation method changed after manifest creation')
    require(not any(k in manifest for k in ('seed_check','selected_seed','deployment_seed')),'A selected seed cannot represent the six-member deployment')
    require(manifest.get('member_seed_pairs')==MEMBERS and manifest.get('distinct_component_seeds')==3,'Ordered six member seed pairs required')
    require(type(manifest.get('independent_bundle_replicates')) is int and manifest['independent_bundle_replicates']==1,'Components are not independent bundle replicates')
    require(manifest.get('noise')==NOISE,'Noise must retain k1 and one effective fold; no historical sqrt7 or sqrt6')
    require(all(type(manifest['noise'][k]) is int for k in ('k_seeds','n_effective_folds','independent_bundle_replicates')) and manifest['noise']['variance_reduction_claimed'] is False,'Noise counts must be integers and reduction claim false')
    require(manifest.get('primary_regime') in ('preweight','serving'),'Diagnostic replay cannot be a primary prospective regime')
    require(manifest.get('single_final_look') is True and manifest.get('can_adopt') is False and manifest.get('eligible_for_verdict') is False,'Fixed final look and unadopted registration required')
    frozen=utc(manifest['frozen_at']);require(frozen<=now(),'Freeze cannot be in the future')
    used=dt.date.fromisoformat(manifest['selection_used_through']);w=manifest['eval_window']
    start,end=dt.date.fromisoformat(w['from']),dt.date.fromisoformat(w['to'])
    require(start>used and start>frozen.date() and end>=start,'Confirmation must follow selection/freeze with fixed end')
    if prospective:require(start>now().date(),'Confirmation must follow actual reservation day')
    require(type(w['min_eval_days']) is int and 0<w['min_eval_days']<=(end-start).days+1,'Invalid minimum evaluation days')
    require(isinstance(manifest.get('bundle_id'),str) and bool(manifest['bundle_id'].strip()),'Bundle ID required')
    for path_key,sha_key in [('bundle_path','bundle_manifest_sha256'),('anchor_path','anchor_model_sha256')]:
        require(Path(manifest[path_key]).is_absolute(),'Artifact path must be absolute')
        require(isinstance(manifest.get(sha_key),str) and len(manifest[sha_key])==64 and all(c in '0123456789abcdef' for c in manifest[sha_key]),'Valid artifact SHA required')
    budget._json_bytes(manifest)


def reviewed_evidence(ref,manifest,kind):
    evidence=checked_ref(ref);review=checked_ref(ref['review'])
    method=review.get('method',{});path=Path(method.get('path',''))
    require(path.is_absolute() and path.is_file() and digest(path)==method.get('sha256'),'Independent review method changed')
    require(review.get('status')=='PASS' and review.get('reviewer_role')=='independent' and review.get('adequate_for_confirmation') is True and isinstance(review.get('justification'),str) and review['justification'].strip(),'Independent adequacy review required')
    require(review.get('evidence')=={k:ref[k] for k in ('path','sha256')},'Review does not bind this evidence')
    require(evidence.get('artifact_kind')==kind,'Wrong evidence kind')
    for key in ('bundle_manifest_sha256','anchor_model_sha256','primary_regime'):
        require(review.get(key)==manifest[key],'Review identity/regime differs')
    return evidence


def preflight(manifest):
    """Return NOT_READY for absent prerequisites, reject contradictory/tampered ones."""
    missing=[]
    for key in ('bundle_id','bundle_manifest_sha256','anchor_model_sha256'):
        if not manifest.get(key):missing.append(key)
    for key in ('from','to','min_eval_days'):
        if not manifest.get('eval_window',{}).get(key):missing.append('eval_window.'+key)
    for key in ('power','noise','compatibility'):
        ref=manifest.get('evidence',{}).get(key)
        if not ref or not ref.get('review'):missing.append('reviewed_'+key)
        else:
            for label,item in [('evidence',ref),('review',ref['review'])]:
                if not Path(item.get('path','')).is_file():missing.append(key+'.'+label)
            if Path(ref['review'].get('path','')).is_file():
                review=checked_ref(ref['review'])
                if review.get('status')!='PASS' or review.get('adequate_for_confirmation') is not True:
                    missing.append(key+'.independent_adequacy')
    if missing:return {'state':'NOT_READY','reasons':missing,'can_adopt':False,'eligible_for_verdict':False}
    validate_manifest(manifest)
    bundle=validate_bundle(manifest['bundle_path'],manifest['bundle_manifest_sha256'])
    require(bundle.get('bundle_id')==manifest['bundle_id'],'Candidate bundle ID differs')
    anchor=validate_anchor(manifest['anchor_path'],manifest['anchor_model_sha256'])
    require(utc(anchor['created_at'])<=utc(manifest['frozen_at']),'Anchor created after manifest freeze')
    if 'created_at' in bundle:require(utc(bundle['created_at'])<=utc(manifest['frozen_at']),'Bundle created after manifest freeze')
    compatibility=reviewed_evidence(manifest['evidence']['compatibility'],manifest,'mixture_serving_compatibility')
    for key in ('bundle_manifest_sha256','anchor_model_sha256','primary_regime'):require(compatibility.get(key)==manifest[key],'Parity evidence identity differs')
    require(all(compatibility.get(k) is True for k in ('all_six_members','all_heads_roundtrip','same_asof_inputs','no_postaverage_transform','pre_result_capture_supported')),'Operational compatibility not established')
    noise=reviewed_evidence(manifest['evidence']['noise'],manifest,'mixture_noise_justification')
    require(noise.get('noise')==NOISE and noise.get('bundle_variance_reduction_claimed') is False and bool(noise.get('justification')),'Noise transfer justification required')
    power=reviewed_evidence(manifest['evidence']['power'],manifest,'mixture_power_plan')
    expected=power_plan(power['development'],effects=list(dict.fromkeys(r['effect'] for r in power['scenarios'])),max_days=power['max_practical_days'])
    require(power==expected,'Power plan differs from saved development evidence')
    for key in ('bundle_manifest_sha256','anchor_model_sha256','primary_regime'):require(power[key]==manifest[key],'Power evidence identity differs')
    require(dt.date.fromisoformat(power['development_used_through'])<=dt.date.fromisoformat(manifest['selection_used_through']),'Power development extends beyond declared used date')
    scenarios=[r for r in power['scenarios'] if r['effect']==manifest.get('power_effect') and r['confidence']==.9875]
    if len(scenarios)!=1 or not scenarios[0]['practical'] or scenarios[0]['required_days']>manifest['eval_window']['min_eval_days']:
        return {'state':'NOT_READY','reasons':['Power scenario cannot support fixed evaluation days'],'can_adopt':False,'eligible_for_verdict':False}
    return {'state':'READY','registration_sha256':budget._hash(manifest),'can_adopt':False,'eligible_for_verdict':False,
        'power_required_days':scenarios[0]['required_days'],'limits':['Approximate power and reviewed single-model noise transfer; no measured six-bundle variance reduction.']}


def reserve(manifest):
    require(preflight(manifest)['state']=='READY','Confirmation NOT_READY; no reservation')
    policy=policy_api.load_policy();policy_hash=budget._hash(policy);path=Path(LEDGER);path.parent.mkdir(parents=True,exist_ok=True)
    with path.with_name(path.name+'.lock').open('a+b') as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
        require(preflight(manifest)['state']=='READY','Readiness changed before reservation')
        ledger=budget._read_ledger(path,policy_hash);entries=ledger['reservations']
        require(not any(e['bundle_id']==manifest['bundle_id'] or e['bundle_manifest_sha256']==manifest['bundle_manifest_sha256'] for e in entries),'Bundle already reserved')
        year=dt.date.fromisoformat(manifest['eval_window']['from']).year;slot=sum(e['year']==year for e in entries)+1
        require(slot<=policy['confirmation']['max_submissions_per_year'],'Annual budget exhausted')
        instant=now();require(dt.date.fromisoformat(manifest['eval_window']['from'])>instant.date(),'Reservation start must be future')
        entry={'bundle_id':manifest['bundle_id'],'bundle_manifest_sha256':manifest['bundle_manifest_sha256'],'registration_sha256':budget._hash(manifest),'year':year,'slot':slot,'reserved_at':instant.isoformat(),'manifest':copy.deepcopy(manifest)}
        entries.append(entry);budget._atomic_write(path,budget._json_bytes(ledger)+b'\n')
        return {'state':'RESERVED','ensemble_contract':SCHEMA,'policy_sha256':policy_hash,'registration_sha256':entry['registration_sha256'],'year':year,'slot':slot,'reserved_at':entry['reserved_at'],'ledger_sha256_at_reservation':budget._hash(ledger),'can_adopt':False,'eligible_for_verdict':False}


def verified_reservation(manifest,receipt):
    ledger=budget._read_ledger(LEDGER,budget._hash(policy_api.load_policy()))
    require(receipt.get('ensemble_contract')==SCHEMA and receipt.get('registration_sha256')==budget._hash(manifest),'Reservation identity changed')
    matches=[e for e in ledger['reservations'] if e['registration_sha256']==receipt['registration_sha256']]
    require(len(matches)==1,'Reserved manifest missing from shared ledger')
    require(all(matches[0][k]==receipt[k] for k in ('year','slot','reserved_at')),'Reservation slot/time changed')
    require(receipt.get('policy_sha256')==ledger['policy_sha256'] and receipt.get('state')=='RESERVED' and receipt.get('can_adopt') is False,'Reservation policy/state changed')
    prefix=copy.deepcopy(ledger);prefix['reservations']=ledger['reservations'][:ledger['reservations'].index(matches[0])+1]
    require(receipt.get('ledger_sha256_at_reservation')==budget._hash(prefix),'Reservation ledger prefix changed')
    require(dt.date.fromisoformat(manifest['eval_window']['from'])>utc(receipt['reserved_at']).date(),'Prediction window precedes reservation')


def final_readout(manifest,receipt,final_evidence_ref):
    """Final quality must be independently audited; this function never activates."""
    validate_manifest(manifest,prospective=False);verified_reservation(manifest,receipt)
    if now().date()<=dt.date.fromisoformat(manifest['eval_window']['to']):return {'state':'COLLECTING','can_adopt':False,'eligible_for_verdict':False}
    if final_evidence_ref is None:return {'state':'NO_DECISION','reasons':['Prospective outcomes and audited full quality missing'],'can_adopt':False,'eligible_for_verdict':False}
    validate_bundle(manifest['bundle_path'],manifest['bundle_manifest_sha256'])
    validate_anchor(manifest['anchor_path'],manifest['anchor_model_sha256'])
    report=reviewed_evidence(final_evidence_ref,manifest,'mixture_confirmation_final_evidence')
    require(report.get('registration_sha256')==budget._hash(manifest),'Final evidence registration differs')
    require(report.get('eval_window')==manifest['eval_window'] and report.get('noise')==NOISE,'Final period/noise changed')
    records=checked_ref(report['prospective_records'])
    require(isinstance(records,list) and records,'All prospective pair records required')
    ids=set();days=set();start,end=[dt.date.fromisoformat(manifest['eval_window'][k]) for k in ('from','to')]
    for record in records:
        require(record['race_id'] not in ids,'Duplicate prospective race');ids.add(record['race_id'])
        day=dt.date.fromisoformat(record['race_day']);require(start<=day<=end,'Race outside fixed period');days.add(day)
        require(utc(record['input_captured_at'])<=utc(record['predicted_at'])<utc(record['scheduled_start']),'Prediction/input is not pre-start')
        require(utc(record['predicted_at'])>utc(receipt['reserved_at']),'Prediction predates reservation')
        require(record.get('pre_result') is True and record.get('classification')=='prospective','Rehearsal/result-known record cannot confirm')
        require(record.get('candidate_input_sha256')==record.get('anchor_input_sha256') and isinstance(record.get('candidate_input_sha256'),str) and len(record['candidate_input_sha256'])==64,'Paired input identity missing or different')
        require(record.get('candidate_started_ids')==record.get('anchor_started_ids') and isinstance(record.get('candidate_started_ids'),list) and record['candidate_started_ids'] and len(set(record['candidate_started_ids']))==len(record['candidate_started_ids']),'Paired started horse population differs')
        for key in ('bundle_manifest_sha256','anchor_model_sha256','primary_regime'):require(record.get(key)==manifest[key],'Prospective identity/regime changed')
    require(report.get('all_scheduled_eligible_race_ids')==sorted(ids) and report.get('all_pair_coverage') is True,'Incomplete paired opportunity set')
    if len(days)<manifest['eval_window']['min_eval_days']:return {'state':'NO_DECISION','reasons':['Insufficient completed race days'],'can_adopt':False,'eligible_for_verdict':False}
    metrics=report['metrics'];keys=('winner_nll_diff','sample_ci_low','sample_ci_high','total_ci_low','total_ci_high','top2_diff','top3_diff','candidate_ece','anchor_ece')
    require(all(finite(metrics.get(k)) for k in keys),'Invalid final quality metrics')
    require(metrics['sample_ci_low']<=metrics['winner_nll_diff']<=metrics['sample_ci_high'],'Invalid sample CI')
    point=metrics['winner_nll_diff'];pad=statistics.NormalDist().inv_cdf(.99375)*NOISE['sd_fold']
    expected=(point-math.hypot(point-metrics['sample_ci_low'],pad),point+math.hypot(metrics['sample_ci_high']-point,pad))
    require(all(math.isclose(a,b,rel_tol=0,abs_tol=1e-12) for a,b in zip(expected,[metrics['total_ci_low'],metrics['total_ci_high']])),'Incorrect one-fold total CI')
    require(report.get('bootstrap')=={'b':4000,'alpha':.0125,'seed':20260907,'block':'race_day'},'Final bootstrap contract changed')
    groups=report.get('critical_subgroups',{});require(set(groups)=={'canonical','nk','recent_year_only'},'All critical groups required')
    for name,group in groups.items():
        margin=.005 if name=='recent_year_only' else .001
        require(group.get('margin')==margin and all(finite(group.get(k)) for k in ('point','ci_low','ci_high')),'Invalid subgroup CI or margin')
        require(group['ci_low']<=group['point']<=group['ci_high'] and type(group.get('n_days')) is int and 2<=group['n_days']<=len(days),'Invalid subgroup bounds/day count')
        decision=('PASS' if group['ci_high']<margin else 'FAIL' if group['ci_low']>margin else
                  'INCONCLUSIVE_LOW_PRECISION' if group['ci_high']-group['point']>=margin else 'NO_DECISION')
        require(group.get('decision')==decision,'Subgroup status differs from saved interval')
    quality=metrics['top2_diff']<=.0005 and metrics['top3_diff']<=.0005 and 0<=metrics['candidate_ece']<.05 and 0<=metrics['anchor_ece']<=1 and metrics['candidate_ece']-metrics['anchor_ece']<=.001
    state='REJECT' if not quality or any(g['decision']=='FAIL' for g in groups.values()) or point>=0 else 'CONFIRMATION_PASS' if metrics['total_ci_high']<0 and all(g['decision']=='PASS' for g in groups.values()) else 'NO_DECISION'
    return {'state':state,'can_adopt':False,'eligible_for_verdict':state=='CONFIRMATION_PASS','requires_separate_activation_compatibility_check':True,'n_days':len(days),'n_races':len(ids),'registration_sha256':budget._hash(manifest)}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    draft_parser=sub.add_parser('draft');draft_parser.add_argument('--bundle',type=Path,required=True);draft_parser.add_argument('--anchor',type=Path,required=True)
    draft_parser.add_argument('--used-through',required=True);draft_parser.add_argument('--start');draft_parser.add_argument('--end');draft_parser.add_argument('--min-days',type=int);draft_parser.add_argument('--regime',default='preweight',choices=['preweight','serving'])
    power=sub.add_parser('power');power.add_argument('--development',type=Path,required=True);power.add_argument('--max-days',type=int,default=730)
    for name in ('preflight','reserve','readout'):
        q=sub.add_parser(name);q.add_argument('--manifest',type=Path,required=True)
        if name=='readout':q.add_argument('--receipt',type=Path,required=True);q.add_argument('--final-evidence',type=Path)
    for q in (draft_parser,power,*[sub.choices[n] for n in ('preflight','reserve','readout')]):q.add_argument('--output',type=Path,required=True)
    args=p.parse_args(argv);require(not args.output.exists(),'Output must be new; no overwrite')
    if args.command=='draft':result=draft(args.bundle,args.anchor,selection_used_through=args.used_through,start=args.start,end=args.end,min_days=args.min_days,regime=args.regime)
    elif args.command=='power':result=power_plan({'path':str(args.development.resolve()),'sha256':digest(args.development)},max_days=args.max_days)
    elif args.command=='preflight':result=preflight(read(args.manifest))
    elif args.command=='reserve':result=reserve(read(args.manifest))
    else:result=final_readout(read(args.manifest),read(args.receipt),read(args.final_evidence) if args.final_evidence else None)
    write_new(args.output,result);print(json.dumps(result,ensure_ascii=False,allow_nan=False))


if __name__=='__main__':main()
