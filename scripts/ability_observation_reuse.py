"""Certify exact baseline equivalence before importing historical full-stage caches."""
from __future__ import annotations
import argparse
import dataclasses
import importlib.metadata
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import ability_observation as p
import feature_pruning as old
from horseracing_eval.splits import expanding_folds

CERT = p.WORK / 'baseline-equivalence.json'


def load(path):
    with path.open('rb') as f:
        return pickle.load(f)


def verify_freeze(driver):
    cfg = driver.load_config()
    meta = json.loads((driver.WORK / 'snapshot.json').read_text())
    frozen = json.loads((driver.WORK / 'run-freeze.json').read_text())
    current = {'snapshot_sha256': driver.digest(driver.WORK / 'snapshot.pkl'),
               'snapshot_metadata_sha256': driver.digest(driver.WORK / 'snapshot.json'),
               'config_hash': driver.gate_config_hash(cfg), 'source_hash': driver.source_hash(),
               'model_sha256': driver.digest(driver.MODEL / 'model.txt')}
    if any(frozen[k] != v for k, v in current.items()):
        raise ValueError('Source experiment freeze mismatch')
    return cfg, meta, frozen


def key(driver, meta, frozen, cfg, matrix, races, fold, stage, drops):
    factory = driver.CachedFactory(cfg, matrix, races,
        [meta['snapshot_sha256'], frozen['source_hash'], stage], drops=drops)
    train = [r.context for r in fold.train]
    train_hash = p.stable_hash([(r.race_id, str(r.race_date), [h.horse_id for h in r.started_horses]) for r in train])
    cache_key = p.stable_hash([[meta['snapshot_sha256'], frozen['source_hash'], stage],
        factory.recipe_hash, train_hash, fold.valid_year])
    return cache_key, train_hash, factory


def check_payload(cache, cache_key, train_hash, factory, races):
    if (cache['key'] != cache_key or cache['train_hash'] != train_hash
        or cache['recipe_meta'] != factory.recipe_meta
        or cache['feature_columns'] != factory.expected_columns
        or not cache['oof_info']['sufficient']):
        raise ValueError('Source cache identity/scope/calibration mismatch')
    if set(cache['predictions']) != {r.context.race_id for r in races}:
        raise ValueError('Source cached race population mismatch')
    for er in races:
        pred = cache['predictions'][er.context.race_id]
        if set(pred) != {h.horse_id for h in er.context.started_horses}:
            raise ValueError('Source cached horse population mismatch')
        x = np.array([[v.win, v.top2, v.top3] for v in pred.values()])
        if (not np.isfinite(x).all() or (x < -1e-10).any() or (x > 1+1e-10).any()
            or (np.diff(x, axis=1) < -1e-10).any()
            or not np.allclose(x.sum(axis=0), [min(k, len(x)) for k in (1,2,3)], atol=1e-8)):
            raise ValueError('Source cached probabilities invalid')


def certify():
    oc, om, of = verify_freeze(old)
    nc, nm, nf = verify_freeze(p)
    a, ar = load(old.WORK/'snapshot.pkl')
    b, br = load(p.WORK/'snapshot.pkl')
    if list(b.frame.columns) != list(a.frame.columns) + p.OBSERVATION_COLUMNS:
        raise ValueError('Unexpected frame column difference')
    pd.testing.assert_frame_equal(a.frame, b.frame[a.frame.columns], check_exact=True)
    assert a.feature_cols == p.columns_from_model()
    assert a.categorical_cols == b.categorical_cols and a.build_audit == b.build_audit
    assert len(ar) == len(br) and all(x == y for x,y in zip(ar,br))
    records = []
    for stage in ('screen','full'):
        win = nc['eval_window'] if stage == 'full' else nc[stage]['eval_window']
        owin = oc['eval_window'] if stage == 'full' else oc[stage]['eval_window']
        assert win == owin
        import datetime as dt
        start,end = dt.date.fromisoformat(win['from']),dt.date.fromisoformat(win['to'])
        ra = [r for r in ar if r.context.race_date <= end]
        rb = [r for r in br if r.context.race_date <= end]
        fa,fb = list(expanding_folds(ra,start.year,valid_from=start)),list(expanding_folds(rb,start.year,valid_from=start))
        assert fa == fb
        for fold in fa:
            ok,th,oa = key(old,om,of,oc,a,ra,fold,stage,())
            nk,nth,na = key(p,nm,nf,nc,b,rb,fold,stage,nc['baseline_drop_features'])
            assert th == nth and oa.expected_columns == na.expected_columns
            old_recipe,new_recipe = dict(oa.recipe_meta),dict(na.recipe_meta)
            # CalibSplitFactory nests the base recipe under its recipe field.
            def strip_drops(value):
                if isinstance(value,dict):
                    return {k:strip_drops(v) for k,v in value.items() if k != 'drop_features'}
                if isinstance(value,(list,tuple)):
                    return [strip_drops(v) for v in value]
                return value
            assert strip_drops(old_recipe) == strip_drops(new_recipe)
            assert oa.factory.recipe.drop_features == ()
            assert na.factory.recipe.drop_features == tuple(p.OBSERVATION_COLUMNS)
            op = old.WORK/'cache'/f'{ok}.pkl'; cache=load(op)
            vr = [r for r in ra if r.context.race_date.year == fold.valid_year]
            check_payload(cache,ok,th,oa,vr)
            if stage == 'screen':
                fresh=load(p.WORK/'cache'/f'{nk}.pkl')
                check_payload(fresh,nk,th,na,vr)
                assert cache['predictions'] == fresh['predictions']
                assert cache['oof_info'] == fresh['oof_info']
                screen={'year':fold.valid_year,'n_races':len(vr),'all_probabilities_exact':True,
                        'oof_info_exact':True,'old_cache_sha256':p.digest(op),
                        'fresh_cache_sha256':p.digest(p.WORK/'cache'/f'{nk}.pkl')}
            else:
                receipt_path=old.WORK/'prefill'/f'{ok}.json'
                receipt=json.loads(receipt_path.read_text())
                assert receipt['job']['key']==ok and receipt['job']['year']==fold.valid_year
                assert receipt['job']['arm']=={'id':'baseline','drop_features':[]}
                assert receipt['cache_sha256']==p.digest(op)
                records.append({'year':fold.valid_year,'old_key':ok,'new_key':nk,'train_hash':th,
                    'old_path':str(op),'old_sha256':p.digest(op),'old_receipt_path':str(receipt_path),
                    'old_receipt_sha256':p.digest(receipt_path),'new_recipe_meta':na.recipe_meta,
                    'historical_fit_seconds':cache['elapsed_seconds']})
    verify_freeze(old); verify_freeze(p)
    p.write_json(CERT,{'method_sha256':p.digest(__file__),'old_freeze_sha256':p.digest(old.WORK/'run-freeze.json'),
        'new_freeze_sha256':p.digest(p.WORK/'run-freeze.json'),'full_frame_projection_exact':True,
        'all_eval_races_and_folds_exact':True,'effective_recipe_exact':True,'fresh_screen_parity':screen,
        'runtime':{'python':sys.version,'packages':{v:importlib.metadata.version(v) for v in ('numpy','pandas','lightgbm','scikit-learn')}},
        'runtime_evidence':'Same configured venv; freshly refitted900tree/8OOF 2018 baseline is exactly equal in all probabilities and OOF metadata. Historical package versions were not separately captured.',
        'records':records,'note':'Only exact baseline reused; candidate is freshly fit. Historical fitting durations must not be counted as current training.'})
    print('CERTIFIED exact baseline reuse',len(records),flush=True)


def import_caches():
    import ability_observation_prefill as run
    run.verify(); m=run.manifest()
    cert=json.loads(CERT.read_text()); ch=p.digest(CERT)
    assert m['baseline_equivalence_sha256']==ch and cert['method_sha256']==p.digest(__file__)
    verify_freeze(old)
    assert cert['old_freeze_sha256']==p.digest(old.WORK/'run-freeze.json')
    assert cert['new_freeze_sha256']==p.digest(p.WORK/'run-freeze.json')
    for row in cert['records']:
        job=next(j for j in m['jobs'] if j['key']==row['new_key'])
        assert job['arm']['id']=='baseline' and job['year']==row['year']
        op=Path(row['old_path']); receipt=Path(row['old_receipt_path'])
        assert p.digest(op)==row['old_sha256'] and p.digest(receipt)==row['old_receipt_sha256']
        cache=load(op)
        imported={**cache,'key':row['new_key'],'recipe_meta':row['new_recipe_meta'],
            'elapsed_seconds':0.0,'historical_fit_seconds':cache['elapsed_seconds'],
            'import_certificate_sha256':ch,'source_cache_sha256':row['old_sha256']}
        target=p.WORK/'cache'/f"{row['new_key']}.pkl"
        if target.exists() or run.receipt_path(job['key']).exists():
            raise FileExistsError('Import destination already exists')
        p.save_pickle(target,imported)
        p.write_json(run.receipt_path(job['key']),{'job':job,'cache_sha256':p.digest(target),
            'peak_rss_bytes':None,'model_threads':1,'imported':True,
            'import_certificate_sha256':ch,'historical_fit_seconds':row['historical_fit_seconds']})
    run.verify();run.manifest()
    print('IMPORTED baseline caches',len(cert['records']),flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['certify','import'])
    args=ap.parse_args()
    certify() if args.mode=='certify' else import_caches()
