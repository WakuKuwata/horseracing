"""126 independent scalar audit of seven-fold saved forecasts. No fitting."""
from __future__ import annotations
from collections import defaultdict
import datetime as dt
import gc
import math
from pathlib import Path
import statistics
import sys
import types

import numpy as np
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'scripts'))
import market_feature_full_quality as driver

HELPER=ROOT/'specs/122-market-feature-screen/evidence/independent-review.py'
scalar=types.ModuleType('scalar122_audit_primitives');scalar.__file__=str(HELPER)
exec(compile(HELPER.read_text(),str(HELPER),'exec'),scalar.__dict__)
close,loss,inspect=scalar.close,scalar.loss,scalar.inspect


def cluster_ci(by_day):
    """Independent sufficient-statistic implementation of the fixed day bootstrap."""
    days=sorted(by_day)
    assert len(days)>=2 and all(by_day[d] for d in days)
    sums=np.asarray([math.fsum(by_day[d]) for d in days]);counts=np.asarray([len(by_day[d]) for d in days])
    assert np.isfinite(sums).all()
    rng=np.random.default_rng(20260907);samples=np.empty(4000)
    for i in range(4000):
        selection=rng.integers(0,len(days),size=len(days))
        samples[i]=sums[selection].sum()/counts[selection].sum()
    lo,hi=np.percentile(samples,[.625,99.375])
    return {'point':float(sums.sum()/counts.sum()),'ci_low':float(lo),'ci_high':float(hi),
            'n_days':len(days),'n_items':int(counts.sum())}


def total_bounds(ci):
    # Seven outer years and one deployment seed. This is not122's single fold.
    point=ci['point'];pad=statistics.NormalDist().inv_cdf(.99375)*.001816/math.sqrt(7)
    return [point-math.hypot(point-ci['ci_low'],pad),point+math.hypot(ci['ci_high']-point,pad)]


def state(ci,margin):
    if ci['ci_high']<margin:return 'PASS'
    if ci['ci_low']>margin:return 'FAIL'
    return 'INCONCLUSIVE_LOW_PRECISION' if ci['ci_high']-ci['point']>=margin else 'NO_DECISION'


def risk(ci,decision):
    return ci['ci_high'] if decision in ('INCONCLUSIVE_LOW_PRECISION','NO_DECISION') else None


def check_ci(actual,expected):
    for k in ('point','ci_low','ci_high'):close(actual[k],expected[k])
    assert actual['n_days']==expected['n_days']
    if 'b' in expected:
        assert expected['b']==4000 and expected['seed']==20260907 and expected['block']=='race_day' and expected['no_decision'] is False


def grouped_losses(valid,candidate,baseline,candidate_scored,baseline_scored):
    race_diff,race_uniform,horse_diff,horse_uniform=({}, {}, {}, {})
    def append(mapping,name,day,value):mapping.setdefault(name,{}).setdefault(day,[]).append(value)
    for race in valid:
        ctx=race.context;rid=ctx.race_id;day=str(ctx.race_date);ids=[h.horse_id for h in ctx.started_horses]
        labels={label.horse_id:int(label.win) for label in race.labels}
        if rid in candidate_scored['winner_rows']:
            cand=candidate_scored['winner_rows'][rid];base=baseline_scored['winner_rows'][rid]
            if ctx.race_date.year==2026:
                names=['recent_year_only','2026_only']
                if any(str(h).startswith('nk:') for h in ids):names+=['recent_year_field_has_nk','2026_field_has_nk']
                for name in names:
                    append(race_diff,name,day,cand-base);append(race_uniform,name,day,cand-math.log(len(ids)))
        # Match the frozen subgroup estimand: all started horses, including the
        # incomplete-result races excluded from main winner/top/ECE metrics.
        for h in ids:
            y=labels.get(h,0);cand=loss(candidate[rid][h].win,y);base=loss(baseline[rid][h].win,y)
            names=['nk' if str(h).startswith('nk:') else 'canonical']
            if ctx.race_date.year==2026 and str(h).startswith('nk:'):names+=['recent_year_nk','2026_nk']
            for name in names:
                append(horse_diff,name,day,cand-base);append(horse_uniform,name,day,cand-loss(1./len(ids),y))
    return race_diff,race_uniform,horse_diff,horse_uniform


def check_subgroups(valid,candidate,baseline,cs,bs,report):
    race_diffs,race_uniform,horse_diffs,horse_uniform=grouped_losses(valid,candidate,baseline,cs,bs)
    result={};declared=report['subgroups']
    for kind,diffs,uniform,margin in [('race_subgroups',race_diffs,race_uniform,.005),('horse_subgroups',horse_diffs,horse_uniform,.001)]:
        assert set(diffs)==set(declared[kind])
        for name,by_day in diffs.items():
            ci=cluster_ci(by_day);decision=state(ci,margin);residual=risk(ci,decision);saved=declared[kind][name]
            check_ci(ci,saved['bootstrap_ci'])
            assert saved['decision']==decision and saved['margin']==margin and saved['n_days']==ci['n_days']
            if residual is None:assert saved['residual_risk'] is None
            else:close(residual,saved['residual_risk'])
            values=[v for rows in uniform[name].values() for v in rows]
            close(math.fsum(values)/len(values),saved['cand_minus_uniform'])
            result[name]={'ci':ci,'decision':decision,'residual_risk':residual}
    critical=['canonical','nk','recent_year_only']
    assert declared['critical']==critical
    states={name:result[name]['decision'] for name in critical}
    status='FAIL' if 'FAIL' in states.values() else 'PASS' if all(v=='PASS' for v in states.values()) else 'NOT_PROVEN'
    assert declared['subgroup_decisions']==states and declared['subgroup_guard_status']==status
    assert declared['subgroup_guard'] is all(v=='PASS' for v in states.values())
    assert set(declared['critical_residual_risk'])==set(critical)
    for name in critical:
        residual=result[name]['residual_risk']
        if residual is None:assert declared['critical_residual_risk'][name] is None
        else:close(residual,declared['critical_residual_risk'][name])
    assert declared['target_year']==report['target_year']==2026
    return result,status


def main():
    output=Path(__file__).with_suffix('.json')
    assert not output.exists(),'Preserve previous independent126 audit'
    assert (driver.SPEC/'verdict.json').exists(),'Wait for parent126 full training/evaluation/summary'
    cfg,frozen=driver.verify()
    assert not (driver.WORK/'running.lock').exists() and all(driver.completed(job) for job in frozen['jobs'])
    selected=frozen['selected_candidates']
    assert len(frozen['jobs'])==7*len(selected)
    summary_sha=driver.s.p.digest(driver.SPEC/'verdict.json');freeze_sha=driver.s.p.digest(driver.WORK/'run-freeze.json')
    summary=driver.s.read_json(driver.SPEC/'verdict.json')
    reports,evidence,hashes,start_files={},{},{},dict(frozen['sources']['files'])
    start_files[str(Path(__file__))]=driver.s.p.digest(__file__)
    start_files[str(HELPER)]=driver.s.p.digest(HELPER)
    for name in selected:
        assert driver.verified_result(name,cfg,frozen)
        p=driver.result_path(name);r=reports[name]=driver.s.read_json(p);evidence[name]=driver.s.read_json(r['evidence_path'])
        hashes[name]={'report_sha256':driver.s.p.digest(p),'evidence_sha256':driver.s.p.digest(r['evidence_path'])}
        start_files.update({str(p):hashes[name]['report_sha256'],r['evidence_path']:hashes[name]['evidence_sha256'],
                            str(driver.result_receipt(name)):driver.s.p.digest(driver.result_receipt(name))})
    matrix,races,folds=driver.inputs(cfg);valid=[r for year,fold in sorted(folds.items()) for r in fold.valid]
    assert sorted(folds)==list(range(2020,2027)) and len(valid)==23030
    base_factory=driver.BaselineFactory(cfg,frozen,matrix,races)
    baseline,cache_hashes,cache_records={},{},[]
    for year,fold in sorted(folds.items()):
        record=next(row for row in base_factory.native.frozen['source_caches'] if row['arm']=='pruning' and row['year']==year)
        driver.s.validate_receipt(record)
        path=Path(record['path']);payload=driver.s.reuse.load(path)
        # Original110 caches retain their native recipe metadata. Verify that
        # exact recipe, then separately prove equivalence to the113 wrapper.
        native110=driver.s.old.CachedFactory(driver.s.old.load_config(),driver.m.native_matrix(matrix),races,[],
            drops=record['source_job']['arm']['drop_features'],label='pruning')
        assert native110.expected_columns==base_factory.native.expected_columns
        assert driver.s.strip_drops(native110.recipe_meta)==driver.s.strip_drops(base_factory.native.recipe_meta)
        driver.s.reuse.check_payload(payload,record['key'],record['train_hash'],native110,list(fold.valid))
        assert driver.s.train_identity([r.context for r in fold.train])==record['train_hash']
        baseline.update(payload['predictions']);cache_hashes[f'baseline:{year}']=driver.s.p.digest(path)
        start_files[str(path)]=driver.s.p.digest(path)
        start_files[record['receipt_path']]=driver.s.p.digest(record['receipt_path'])
        cache_records.append({'arm':'baseline','year':year,'key':record['key'],'cache_sha256':driver.s.p.digest(path),
            'receipt_sha256':record['receipt_sha256'],'feature_columns':len(payload['feature_columns']),'oof_sufficient':payload['oof_info']['sufficient']})
        del native110,payload
    baseline_scored=inspect(valid,baseline)
    assert len(baseline_scored['winner_rows'])==22990
    comparison_checks={}
    for name in selected:
        factory=driver.FreshFactory(cfg,frozen,matrix,races,name);candidate={}
        for year,fold in sorted(folds.items()):
            job=next(v for v in frozen['jobs'] if v['arm']==name and v['year']==year)
            assert driver.m.job_for(factory,fold)==job and driver.completed(job)
            path=driver.WORK/'cache'/f"{job['key']}.pkl";payload=driver.s.reuse.load(path)
            driver.m.check_payload(payload,job['key'],job['train_hash'],factory,list(fold.valid))
            assert payload['feature_columns']==frozen['arm_columns'][name]
            assert payload['oof_info']['sufficient'] is True
            candidate.update(payload['predictions']);cache_hashes[f'{name}:{year}']=driver.s.p.digest(path)
            receipt=driver.receipt_path(job['key']);start_files[str(path)]=driver.s.p.digest(path);start_files[str(receipt)]=driver.s.p.digest(receipt)
            cache_records.append({'arm':name,'year':year,'key':job['key'],'cache_sha256':driver.s.p.digest(path),
                'receipt_sha256':driver.s.p.digest(receipt),'feature_columns':len(payload['feature_columns']),
                'actual_params':payload['actual_params'],'oof_sufficient':True})
        scored=inspect(valid,candidate);r=reports[name];rows=evidence[name]['rows'];bs=baseline_scored;cs=scored
        assert list(cs['winner_rows'])==list(bs['winner_rows'])==[x['race_id'] for x in rows]
        by_day=defaultdict(list)
        for row in rows:
            rid=row['race_id'];c=cs['winner_rows'][rid];b=bs['winner_rows'][rid]
            close(c,row['candidate_winner_nll']);close(b,row['active_winner_nll']);close(c-b,row['diff'])
            by_day[row['race_day']].append(c-b)
        ci=cluster_ci(by_day);total=total_bounds(ci);check_ci(ci,r['bootstrap_ci'])
        close(ci['point'],r['total_ci']['point']);close(total[0],r['total_ci']['ci_low']);close(total[1],r['total_ci']['ci_high'])
        assert ci['n_days']==r['total_ci']['n_days']==715
        assert r['seed_noise']['n_folds']==7 and r['seed_noise']['k_seeds']==1 and r['seed_noise']['sd_fold']==.001816
        q=r['gate']['reasons'];diff=cs['nll']-bs['nll'];top2=cs['top2']-bs['top2'];top3=cs['top3']-bs['top3']
        for v,expected in [(cs['nll'],r['periods']['all']['candidate']),(bs['nll'],r['periods']['all']['active']),
            (diff,r['periods']['all']['diff']),(top2,q['top2_diff']),(top3,q['top3_diff']),
            (cs['ece'],q['cand_ece']),(bs['ece'],q['act_ece'])]:close(v,expected)
        recent_checks={}
        for years in (3,5):
            start=dt.date(2026-years,8,23);label=f'recent_{years}y'
            subset=[race for race in valid if race.context.race_date>=start]
            ids={race.context.race_id for race in subset};eligible_ids=[rid for rid in cs['winner_rows'] if rid in ids]
            c=math.fsum(cs['winner_rows'][rid] for rid in eligible_ids)/len(eligible_ids)
            b=math.fsum(bs['winner_rows'][rid] for rid in eligible_ids)/len(eligible_ids)
            for v,expected in [(c,r['periods'][label]['candidate']),(b,r['periods'][label]['active']),(c-b,r['periods'][label]['diff'])]:close(v,expected)
            assert r['periods'][label]['n_races']==len(subset)
            part=cluster_ci({d:v for d,v in by_day.items() if d>=str(start)});window=q['recent']['windows'][label]
            close(part['point'],window['diff']);close(part['ci_low'],window['ci_low']);close(part['ci_high'],window['ci_high'])
            decision=state(part,.005);residual=risk(part,decision)
            assert window['n_days']==part['n_days'] and window['n_races']==len(eligible_ids) and window['decision']==decision
            assert window['point_estimate_degraded'] is (part['point']>0)
            if residual is None:assert window['residual_risk'] is None
            else:close(residual,window['residual_risk'])
            recent_checks[label]={'ci':part,'decision':decision,'residual_risk':residual}
        subgroup_checks,subgroup_status=check_subgroups(valid,candidate,baseline,cs,bs,r)
        recent_ok=all(v['decision']!='FAIL' for v in recent_checks.values())
        quality=top2<=.0005 and top3<=.0005 and cs['ece']-bs['ece']<=.001 and cs['ece']<.05
        flags={'primary':diff<0,'stat_guard':total[1]<0,'recent_guard':recent_ok,
               'top_noninferior':top2<=.0005 and top3<=.0005,'calibration':cs['ece']-bs['ece']<=.001 and cs['ece']<.05}
        for key,value in flags.items():assert r['gate'][key] is value
        assert r['gate']['adopted'] is all(flags.values()) and q['recent']['pass'] is recent_ok
        expected_state=('BLOCKED' if not quality or not recent_ok or subgroup_status=='FAIL' else
                        'DEFER' if diff>=0 else 'RETAIN_SUPPORTED' if total[1]<0 else 'RETAIN_UNCERTAIN')
        assert r['research_disposition']['state']==expected_state
        formal=('REJECT' if subgroup_status=='FAIL' else 'ADOPT' if all(flags.values()) else
                'REJECT' if any(not flags[k] for k in ('primary','recent_guard','top_noninferior','calibration')) else 'NO_DECISION')
        assert r['gate_readout']==formal
        comparison_checks[name]={'nll':cs['nll'],'diff':diff,'top2_diff':top2,'top3_diff':top3,'candidate_ece':cs['ece'],
            'baseline_ece':bs['ece'],'primary_sample_ci':ci,'primary_total_bounds':total,'recent':recent_checks,
            'subgroups':subgroup_checks,'research_state':expected_state,'gate_readout_diagnostic_only':formal}
        del candidate,scored,factory;gc.collect()
        print(f'126 independent scalar metrics/all subgroup CIs PASS {name}; no fit',flush=True)
    assert summary['run_freeze_sha256']==freeze_sha and summary['selected_candidates']==selected
    assert summary['can_adopt'] is False and summary['eligible_for_verdict'] is False
    assert summary['new_outer_jobs']==len(frozen['jobs']) and summary['new_booster_fits']==8*len(frozen['jobs'])
    assert summary['research_decision']==('FULL_QUALITY_COMPLETED' if selected else 'NO_ADVANCING_CANDIDATES')
    assert summary['reports']=={name:{**hashes[name],'research_disposition':reports[name]['research_disposition']} for name in selected}
    driver.verify();assert all(driver.completed(job) for job in frozen['jobs'])
    assert all(driver.verified_result(name,cfg,frozen) for name in selected)
    assert driver.s.p.digest(driver.SPEC/'verdict.json')==summary_sha and driver.s.p.digest(driver.WORK/'run-freeze.json')==freeze_sha
    assert all(driver.s.p.digest(path)==sha for path,sha in start_files.items())
    driver.write_json(output,{'artifact_kind':'market_feature_full_quality_independent_review','status':'PASS','can_adopt':False,
        'eligible_for_verdict':False,'additional_fits':0,'method_sha256':driver.s.p.digest(__file__),'helper_method_sha256':driver.s.p.digest(HELPER),
        'run_freeze_sha256':freeze_sha,'summary_sha256':summary_sha,'report_hashes':hashes,'cache_hashes':cache_hashes,
        'source_hashes_start_end_equal':True,'cache_records':cache_records,'comparisons':comparison_checks,
        'population':{'n_races':23030,'n_eligible':22990,'n_days':715,'complete_started_horses':baseline_scored['complete_started_horses'],
            'incomplete_races':baseline_scored['incomplete_races']},'checks':scalar.CHECKS,'max_numeric_error':scalar.MAX_ERROR,
        'limitations':['Cached seven-fold seed42 forecasts audited; no independent model retraining.',
            'Primary CI uses seven-fold/k1 transferred noise; recent and subgroup CIs remain conditional sample CIs.',
            'Horse subgroup losses include all started horses under the frozen contract, including incomplete-result races.',
            'Research only; no gap, ensemble, unused-holdout or production improvement claim.']})
    print(f'126 independent PASS {output}',flush=True)


if __name__=='__main__':main()
