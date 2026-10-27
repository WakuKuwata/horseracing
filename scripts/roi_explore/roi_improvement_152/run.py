"""Freeze unweighted pace91 full normalization using saved source predictions."""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sys

import pandas as pd

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
OUT=ROOT/'artifacts/152-normalized-pace'
SOURCE=ROOT/'artifacts/143-existing-feature-roi'
PARENTS_OUT=ROOT/'artifacts/147-fixed-half-blend'
NORMALIZED_OUT=ROOT/'artifacts/148-postprediction-normalization'
CANDIDATE='normalized_pace'
CONTROLS=['baseline','weighted_baseline','normalized_weighted_baseline','pace']
CONTRASTS=[[CANDIDATE,b] for b in CONTROLS]
NORMALIZATION=dict(parent='pace',candidate=CANDIDATE,parent_features=91,parent_weights=None,
    formula='p_i / sum_all_starters_in_race(p)',
    parent_ensemble='arithmetic raw probability mean of seeds1..15 FIRST',before_ev=True,
    denominator='all complete-field starters before selection; no odds or outcomes',
    clipping_before_normalization=False,zero_sum='reject',fitted_parameters=0)
BOOTSTRAP=dict(repetitions=4000,seed=20261025,family_size=4,
    unit='all common eligible race dates including no-bet dates',adjustment='Bonferroni separately per metric')


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    obj=importlib.util.module_from_spec(spec)
    sys.modules[name]=obj
    spec.loader.exec_module(obj)
    return obj


parents=module('roi152_source147_run',HERE.parent/'roi_improvement_147/run.py')
legacy=module('roi152_source148_run',HERE.parent/'roi_improvement_148/run.py')
models=module('roi152_source143_models',HERE.parent/'roi_improvement_143/models.py')
POLICY=legacy.POLICY.copy()
write=legacy.write
CONTROL_SOURCES={a:(SOURCE/'features_comparison' if a=='pace' else NORMALIZED_OUT/'comparison')
                 for a in ('current_reference',*CONTROLS)}


def references():
    parents.verify(PARENTS_OUT)
    legacy.verify(NORMALIZED_OUT)
    raw,paths=parents.references()
    full_path=NORMALIZED_OUT/'comparison/normalized_weighted_baseline_ensemble.parquet'
    full=pd.read_parquet(full_path)
    weighted=raw['weighted_baseline']
    rebuilt=weighted[models.PRED_KEYS].copy()
    rebuilt['p']=weighted.p.to_numpy()/weighted.groupby('race_id').p.transform('sum').to_numpy()
    pd.testing.assert_frame_equal(full,rebuilt,check_exact=True)
    frames={a:raw[a] for a in ('current_reference','baseline','weighted_baseline')}
    frames['normalized_weighted_baseline']=full
    frames['pace']=raw['pace']
    for arm,folder in CONTROL_SOURCES.items():
        for part in ('ensemble','bets','races'):
            paths.append(folder/f'{arm}_{part}.parquet')
        paths.append(folder/'summary.json')
        pd.testing.assert_frame_equal(frames[arm],pd.read_parquet(folder/f'{arm}_ensemble.parquet'),check_exact=True)
    audit_path=ROOT/'specs/148-postprediction-normalization/evidence/independent_final.json'
    audit=json.loads(audit_path.read_text())
    if audit['summary_sha256']!=models.sha(NORMALIZED_OUT/'comparison/summary.json') or audit['blockers']:
        raise ValueError('Frozen148 audit differs')
    paths.append(audit_path)
    return frames,paths


def verify(out):
    path=out/'protocol.json'
    if models.sha(path)!=path.with_suffix('.sha256').read_text().strip():
        raise ValueError('Protocol sidecar differs')
    doc=json.loads(path.read_text())
    expected=dict(feature='152-normalized-pace',version=1,normalization=NORMALIZATION,policy=POLICY,
        bootstrap=BOOTSTRAP,contrasts=CONTRASTS,seeds=list(range(1,16)),new_fits=0,reused_control_fits=360,
        window=dict(training_from=2007,through='2026-06-26',model_outer=list(range(2019,2027))),
        can_adopt=False,eligible_for_verdict=False,untouched_holdout=False,proposal2='held')
    for key,value in expected.items():
        if models.canonical(doc.get(key))!=models.canonical(value):
            raise ValueError(f'Protocol constraint differs: {key}')
    hashes=doc['inputs_and_source_sha256']
    for name in ('run.py','report.py'):
        if str((HERE/name).relative_to(ROOT)) not in hashes:
            raise ValueError('Protocol source binding missing')
    for name,digest in hashes.items():
        dependency=(ROOT/name).resolve()
        if not dependency.is_relative_to(ROOT) or models.sha(dependency)!=digest:
            raise ValueError(f'Input/source changed: {name}')
    return doc


def freeze(out):
    _,paths=references()
    proto147=parents.verify(PARENTS_OUT)
    proto148=legacy.verify(NORMALIZED_OUT)
    for old,folder in ((proto147,PARENTS_OUT),(proto148,NORMALIZED_OUT)):
        paths += [ROOT/name for name in old['inputs_and_source_sha256']]
        paths += [folder/'protocol.json',folder/'protocol.sha256']
    paths += [*HERE.glob('*.py'),ROOT/'docs/roi-after-149-150-20261025/report.md']
    doc=dict(feature='152-normalized-pace',version=1,frozen_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        normalization=NORMALIZATION,policy=POLICY,bootstrap=BOOTSTRAP,contrasts=CONTRASTS,
        seeds=list(range(1,16)),new_fits=0,reused_control_fits=360,
        window=dict(training_from=2007,through='2026-06-26',model_outer=list(range(2019,2027))),
        proposal2='held',can_adopt=False,eligible_for_verdict=False,untouched_holdout=False,
        sensitivity=dict(known_influence_race='202608030111',same_calendar_through='06-21',
            top_difference_race='retrospective sensitivity only; never a trading exclusion'),
        inputs_and_source_sha256={str(p.relative_to(ROOT)):models.sha(p) for p in sorted(set(paths))},
        limitations=[*proto148['limitations'],
            'The only candidate is unweighted pace91 full normalization; no weighted pace, raw blend, partial correction or flat stakes.',
            'Winner NLL is invariant within the pace parent after race evaluation normalization; tiny differences are floating arithmetic.',
            'Improving over raw pace and surpassing normalized weighted baseline are distinct historical comparisons.',
            'Market3-column ablation remains held; no new fitting or model/threshold search.'])
    out.mkdir(exist_ok=True,parents=True)
    write(out/'protocol.json',doc)
    (out/'protocol.sha256').write_text(models.sha(out/'protocol.json')+'\n')
    return verify(out)


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('command',choices=['freeze','verify'])
    parser.add_argument('--out',type=Path,default=OUT)
    args=parser.parse_args()
    freeze(args.out) if args.command=='freeze' else verify(args.out)
    print(json.dumps(dict(command=args.command,out=str(args.out),status='complete')))


if __name__=='__main__':
    main()
