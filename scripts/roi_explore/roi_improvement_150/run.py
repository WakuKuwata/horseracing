"""Freeze fixed100-yen stakes on the two already selected148 ticket sets."""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
OUT=ROOT/'artifacts/150-flat-stake'
PREVIOUS=ROOT/'artifacts/148-postprediction-normalization'
FROZEN_PREVIOUS=ROOT/'artifacts/149-half-normalization'
PARENTS=['weighted_baseline','normalized_weighted_baseline']
CANDIDATES=['flat_'+parent for parent in PARENTS]
CONTROLS=['baseline','weighted_baseline','normalized_weighted_baseline']
CONTRASTS=[[a,b] for a in CANDIDATES for b in CONTROLS]+[[CANDIDATES[1],CANDIDATES[0]]]
ALLOCATION=dict(parents=PARENTS,candidates=CANDIDATES,stake_yen_per_selected_horse=100,
    selection='exact frozen148 parent ticket keys, including EV>1.2/max10/tie order; no new selection',
    budget_yen_per_race=1000,max_horses=10,unspent_cash='all uninvested budget stays cash',
    prediction_change=False,fitted_parameters=0,stake_search=False)
BOOTSTRAP=dict(repetitions=4000,seed=20261025,family_size=7,
    unit='all common eligible race dates including no-bet dates',adjustment='Bonferroni separately per metric')


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    obj=importlib.util.module_from_spec(spec)
    sys.modules[name]=obj
    spec.loader.exec_module(obj)
    return obj


legacy=module('roi150_source149_run',HERE.parent/'roi_improvement_149/run.py')
models=legacy.models
POLICY=legacy.POLICY.copy()
write=legacy.write


def references():
    legacy.verify(FROZEN_PREVIOUS)
    frames,paths=legacy.references()
    for arm in ('current_reference',*CONTROLS):
        paths.extend(PREVIOUS/f'comparison/{arm}_{part}.parquet' for part in ('ensemble','bets','races'))
    audit_path=ROOT/'specs/148-postprediction-normalization/evidence/independent_final.json'
    audit=json.loads(audit_path.read_text())
    if audit['summary_sha256']!=models.sha(PREVIOUS/'comparison/summary.json') or audit['blockers']:
        raise ValueError('Frozen148 audit differs')
    return frames,paths


def verify(out):
    path=out/'protocol.json'
    if models.sha(path)!=path.with_suffix('.sha256').read_text().strip():
        raise ValueError('Protocol sidecar differs')
    doc=json.loads(path.read_text())
    expected=dict(feature='150-flat-stake',version=1,allocation=ALLOCATION,policy=POLICY,
        bootstrap=BOOTSTRAP,contrasts=CONTRASTS,seeds=list(range(1,16)),new_fits=0,reused_control_fits=240,
        window=dict(training_from=2007,through='2026-06-26',model_outer=list(range(2019,2027))),
        can_adopt=False,eligible_for_verdict=False,untouched_holdout=False,proposal2='executed')
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
    old=legacy.verify(FROZEN_PREVIOUS)
    paths += [ROOT/name for name in old['inputs_and_source_sha256']]
    paths += [*HERE.glob('*.py'),FROZEN_PREVIOUS/'protocol.json',FROZEN_PREVIOUS/'protocol.sha256']
    # Raw148 limitations apply; no partial-score experiment or prediction change here.
    old148=legacy.legacy.verify(PREVIOUS)
    doc=dict(feature='150-flat-stake',version=1,frozen_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        allocation=ALLOCATION,policy=POLICY,bootstrap=BOOTSTRAP,contrasts=CONTRASTS,
        seeds=list(range(1,16)),new_fits=0,reused_control_fits=240,
        window=dict(training_from=2007,through='2026-06-26',model_outer=list(range(2019,2027))),
        proposal2='executed',can_adopt=False,eligible_for_verdict=False,untouched_holdout=False,
        sensitivity=dict(known_influence_race='202608030111',same_calendar_through='06-21',
            top_difference_race='retrospective sensitivity only; never a trading exclusion',
            allocation_examples=['202609010807','202610011209','202608030111']),
        inputs_and_source_sha256={str(p.relative_to(ROOT)):models.sha(p) for p in sorted(set(paths))},
        limitations=[*old148['limitations'],
            'Only stakes change to100yen for each already selected ticket; prediction, EV selection and max10 cap stay fixed.',
            'Profit differences versus floor-allocation controls include different deployed capital; no equal-investment rescaling.',
            'Flat normalized minus flat raw isolates normalization at the same per-ticket amount; it does not prove future profits.',
            'Quality and NLL are unchanged from each corresponding prediction parent.'])
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
