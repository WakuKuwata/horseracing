"""Post-evaluation independent arithmetic/provenance check and research summary."""
import sys,json,math,hashlib,datetime
from pathlib import Path
ROOT=Path('/Users/kuwatawaku/workspace/horseracing');sys.path.insert(0,str(ROOT/'scripts'))
import small_gain_stack as s
cfg,frozen=s.verify()
results={c['id']:s.read_json(s.SPEC/'evidence'/f"full-{c['id']}.json") for c in cfg['contrasts']}
for c in cfg['contrasts']:
 assert s.verified_result(s.SPEC/'evidence'/f"full-{c['id']}.json",cfg,c)
increment,total=results['increment'],results['anchor']
old=s.read_json(ROOT/'specs/110-feature-pruning/evidence/full-relative_ability.json')
def eq(a,b):assert math.isclose(a,b,rel_tol=0,abs_tol=1e-12),(a,b)
for period in ['all','recent_3y','recent_5y']:
 a=increment['periods'][period];b=total['periods'][period];o=old['periods'][period]
 eq(a['candidate'],b['candidate']);eq(a['active'],o['candidate']);eq(b['active'],o['active'])
 eq(b['diff']-a['diff'],o['diff'])
assert increment['n_eligible']==total['n_eligible']==old['n_eligible']
receipts=[]
for j in frozen['jobs']:
 assert s.completed(j)
 q=s.receipt_path(j['key']);r=s.read_json(q)
 receipts.append(dict(year=j['year'],current_fit_seconds=r['current_fit_seconds'],peak_rss_bytes=r['peak_rss_bytes'],receipt_sha256=s.p.digest(q),cache_sha256=r['cache_sha256'],receipt_mtime=q.stat().st_mtime))
retained={'RETAIN_UNCERTAIN','RETAIN_SUPPORTED'}
keep=all(r['research_disposition']['state'] in retained for r in results.values())
out={'artifact_kind':'small_gain_stack_summary','eligible_for_verdict':False,'can_adopt':False,'recorded_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'research_decision':'STACK_RETAINED' if keep else 'STACK_DEFERRED_PRUNING_RETAINED','rule':'Both increment and total contrasts must be retained under small-gains-v1; otherwise preserve pruning candidate.','config_hash':s.p.gate_config_hash(cfg),'run_freeze_sha256':s.p.digest(s.WORK/'run-freeze.json'),'cross_contrast_arithmetic':'all/recent3/recent5 shared candidate and archived anchor/pruning metrics exact within 1e-12','summarizer_sha256':s.p.digest(__file__),'contrasts':{},'new_fit_jobs':receipts,'current_fit_elapsed_sum_seconds':sum(r['current_fit_seconds'] for r in receipts),'historical_reused_fit_elapsed_sum_seconds':sum(r['historical_fit_seconds'] for r in frozen['source_caches']),'note':'Summed per-job elapsed durations are not wall clock or CPU seconds. Historical fitting excluded from current work. This is development evidence, not activation.'}
for k,r in results.items():
 out['contrasts'][k]={key:r.get(key) for key in ['periods','total_ci','gate_readout','research_disposition','n_races','n_eligible','subgroups']}
 out['contrasts'][k]['report_sha256']=s.p.digest(s.SPEC/'evidence'/f'full-{k}.json')
p=s.SPEC/'verdict.json'
s.p.write_json(p,out)
print(json.dumps({'decision':out['research_decision'],'contrasts':{k:(v['periods']['all']['diff'],v['research_disposition']['state']) for k,v in results.items()},'fit_sum_minutes':out['current_fit_elapsed_sum_seconds']/60},ensure_ascii=False))
