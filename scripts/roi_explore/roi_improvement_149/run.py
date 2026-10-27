"""Freeze the single half-strength correction; reuse frozen148 source models."""
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
OUT=ROOT/'artifacts/149-half-normalization'
PREVIOUS=ROOT/'artifacts/148-postprediction-normalization'
CANDIDATE='half_normalized_weighted_baseline'
CONTRASTS=[[CANDIDATE, b] for b in ('baseline','weighted_baseline','normalized_weighted_baseline')]
CORRECTION=dict(parent='weighted_baseline', candidate=CANDIDATE, exponent=.5,
    formula='score_i = p_i / sqrt(sum_all_starters_in_race(p))',
    parent_ensemble='arithmetic raw probability mean of seeds1..15 FIRST', before_ev=True,
    denominator='all complete-field starters before selection; no odds or outcomes',
    clipping_before_correction=False, zero_sum='reject', fitted_parameters=0,
    score_semantics='purchase score; race sum is sqrt(S), not a normalized win distribution',
    exponent_search=False)
BOOTSTRAP=dict(repetitions=4000, seed=20261025, family_size=3,
    unit='all common eligible race dates including no-bet dates', adjustment='Bonferroni separately per metric')


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    obj=importlib.util.module_from_spec(spec)
    sys.modules[name]=obj
    spec.loader.exec_module(obj)
    return obj


legacy=module('roi149_source148_run',HERE.parent/'roi_improvement_148/run.py')
models=module('roi149_source143_models',HERE.parent/'roi_improvement_143/models.py')
POLICY=legacy.POLICY.copy()
write=legacy.write


def references():
    legacy.verify(PREVIOUS)
    frames,paths=legacy.references()
    path=PREVIOUS/'comparison/normalized_weighted_baseline_ensemble.parquet'
    full=pd.read_parquet(path)
    raw=frames['weighted_baseline']
    rebuilt=raw[models.PRED_KEYS].copy()
    rebuilt['p']=raw.p.to_numpy()/raw.groupby('race_id').p.transform('sum').to_numpy()
    pd.testing.assert_frame_equal(rebuilt,full,check_exact=True)
    frames['normalized_weighted_baseline']=full
    paths += [path, PREVIOUS/'comparison/normalized_weighted_baseline_bets.parquet',
        PREVIOUS/'comparison/normalized_weighted_baseline_races.parquet']
    return frames,paths


def verify(out):
    path=out/'protocol.json'
    if models.sha(path)!=path.with_suffix('.sha256').read_text().strip():
        raise ValueError('Protocol sidecar differs')
    doc=json.loads(path.read_text())
    expected=dict(feature='149-half-normalization',version=1,correction=CORRECTION,policy=POLICY,
        bootstrap=BOOTSTRAP,contrasts=CONTRASTS,seeds=list(range(1,16)),new_fits=0,reused_control_fits=240,
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
    old=legacy.verify(PREVIOUS)
    paths += [ROOT/name for name in old['inputs_and_source_sha256']]
    paths += [*HERE.glob('*.py'), PREVIOUS/'protocol.json',PREVIOUS/'protocol.sha256',
        PREVIOUS/'comparison/summary.json',
        ROOT/'specs/148-postprediction-normalization/evidence/independent_final.json',
        ROOT/'docs/roi-normalization-followup-20261025/report.md']
    doc=dict(feature='149-half-normalization',version=1,frozen_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        correction=CORRECTION,policy=POLICY,bootstrap=BOOTSTRAP,contrasts=CONTRASTS,
        seeds=list(range(1,16)),new_fits=0,reused_control_fits=240,
        window=dict(training_from=2007,through='2026-06-26',model_outer=list(range(2019,2027))),
        proposal2='held',can_adopt=False,eligible_for_verdict=False,untouched_holdout=False,
        sensitivity=dict(known_influence_race='202608030111',same_calendar_through='06-21',
            top_difference_race='retrospective sensitivity only; never a trading exclusion'),
        inputs_and_source_sha256={str(p.relative_to(ROOT)):models.sha(p) for p in sorted(set(paths))},
        limitations=[*old['limitations'],
            'Partial correction is a purchase score, not a sum-one calibrated win distribution.',
            'The sole exponent0.5 is fixed; no fitting, exponent/EV search or fixed100yen allocation.',
            'Winner NLL after within-race evaluation normalization is invariant within the same parent; tiny differences are floating arithmetic.',
            'Raw ECE/logloss and EV-band quantities on the partial score are descriptive, not proof of calibrated win probabilities.'])
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
