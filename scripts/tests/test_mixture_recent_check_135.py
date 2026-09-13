"""135 recent input safeguards, using synthetic inputs only (no database reads)."""
from copy import deepcopy
from contextlib import contextmanager
import datetime as dt
import json
import pickle
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mixture_recent_check_135 as study
from horseracing_eval.predictor import HorseEntry, RaceContext
from horseracing_training.target_encoding import TargetEncoder
from horseracing_serving.mixture_model import predict_base


def test_recent_score_records_have_an_importable_pickle_identity():
    value = study.RecentRace(SimpleNamespace(context=SimpleNamespace(
        race_id='synthetic', race_date=study.START)), ('horse-1',))
    restored = pickle.loads(pickle.dumps(value))
    assert study.RecentRace.__module__ == 'mixture_recent_check_135'
    assert (restored.race_id, restored.race_date, restored.ids) == (
        'synthetic', study.START, ('horse-1',),
    )


def enc(mapping, prior=.1, col='jockey_id'):
    return TargetEncoder(col=col, prior=prior, mapping=mapping, smoothing=10.)


def test_equivalence_includes_new_only_and_both_unknown():
    old = enc({'a': .2, 'b': .2, 'c': .3})
    new = enc({'a': .4, 'b': .4, 'c': .5, 'new': .9}, prior=.6)
    convert = study.build_conversion({'jockey_id': old}, {'jockey_id': new})
    assert convert['jockey_id'][.2] == frozenset({.4})
    assert convert['jockey_id'][.1] == frozenset({.6, .9})
    rows = pd.DataFrame({'jockey_id': [.2, .3]})
    np.testing.assert_array_equal(study.convert_encoded(rows, convert).jockey_id, [.4, .5])
    with pytest.raises(study.InputExclusion, match='ambiguous'):
        study.convert_encoded(pd.DataFrame({'jockey_id': [.1]}), convert)


def test_known_value_equal_prior_must_join_unknown_group():
    conversion = study.build_conversion({'jockey_id': enc({'a': .1})},
                                        {'jockey_id': enc({'a': .3}, prior=.4)})
    assert conversion['jockey_id'][.1] == frozenset({.3, .4})


def test_unknown_group_safe_when_all_new_values_equal():
    conversion = study.build_conversion({'jockey_id': enc({'a': .2})},
        {'jockey_id': enc({'a': .3, 'new': .4}, prior=.4)})
    assert study.convert_encoded(pd.DataFrame({'jockey_id': [.1]}), conversion).iloc[0, 0] == .4


@pytest.mark.parametrize('value', [.2 + 1e-15, float('nan'), float('inf'), None, True, '0.2'])
def test_no_float_tolerance_coercion_or_missing_fallback(value):
    conversion = study.build_conversion({'jockey_id': enc({'a': .2})},
                                       {'jockey_id': enc({'a': .3})})
    with pytest.raises(study.InputExclusion):
        study.convert_encoded(pd.DataFrame({'jockey_id': [value]}), conversion)


def test_exact_json_float_roundtrip_supported():
    x = .23142314607191473
    conversion = study.build_conversion({'jockey_id': enc({'a': x})},
                                       {'jockey_id': enc({'a': .3})})
    assert study.convert_encoded(pd.DataFrame({'jockey_id': [json.loads(json.dumps(x))]}), conversion).iloc[0, 0] == .3


def test_encoder_comparison_checks_every_column_not_only_riders():
    sets = {'jockey_id': enc({'a': .2}), 'venue_code': enc({'v': .3}, col='venue_code')}
    study.assert_same_encoders([sets, deepcopy(sets)])
    broken = deepcopy(sets); broken['venue_code'].mapping['v'] += 1e-15
    with pytest.raises(ValueError, match='encoders differ'):
        study.assert_same_encoders([sets, broken])
    with pytest.raises(ValueError):
        study.build_conversion(sets, {'jockey_id': sets['jockey_id']})


class Calibrator:
    def transform(self, p):
        return p ** .9


class Model:
    feature_cols = ['jockey_id', 'weight', 'sex', 'days_since_last']
    categorical_cols = ['sex']
    encoders = {'jockey_id': enc({'a': .2, 'b': .4, 'c': .5, 'd': .8})}
    calibrator = Calibrator()

    def raw_predict(self, x):
        # Deliberately depend on encoded value, category and the weight mask.
        z = x.jockey_id.to_numpy() + x.weight.fillna(0).to_numpy() / 1000
        z += (x.sex.astype(object) == '牝').to_numpy() * .1
        p = np.exp(z-z.max())
        return p / p.sum()


def raw_rows():
    return pd.DataFrame({'race_id': ['r']*4, 'horse_id': ['a','b','c','d'],
        'race_date': [study.START]*4, 'jockey_id': ['a','b','c','d'],
        'weight': [400., 450., 480., 510.], 'sex': ['牡','牝','牡','セ'], 'days_since_last':[20.]*4})


@pytest.mark.parametrize('regime', ['full', 'preweight'])
def test_encoded_adapter_all_three_heads_equal_normal_path_and_never_reencodes(regime, monkeypatch):
    model, rows = Model(), raw_rows()
    raw = study.regime_rows(rows, regime)
    expected, encoded = predict_base(model, raw)
    encoded.index = pd.Index(rows.horse_id)
    context = RaceContext('r', study.START, tuple(HorseEntry(h) for h in rows.horse_id))
    monkeypatch.setattr(TargetEncoder, 'transform', lambda *_: pytest.fail('double TE'))
    actual = study.predict_serving_encoded(model, context, encoded)
    np.testing.assert_array_equal(actual, study.prediction_array(expected, rows.horse_id))


@pytest.mark.parametrize('failure', ['index', 'column', 'dtype', 'infinity', 'future'])
def test_encoded_adapter_rejects_invalid_contract(failure):
    model, rows = Model(), raw_rows()
    _, encoded = predict_base(model, rows); encoded.index = pd.Index(rows.horse_id)
    context = RaceContext('r', study.START, tuple(HorseEntry(h) for h in rows.horse_id))
    if failure == 'index': encoded = encoded.iloc[::-1]
    elif failure == 'column': encoded = encoded[encoded.columns[::-1]]
    elif failure == 'dtype': encoded['jockey_id'] = encoded.jockey_id.astype(str)
    elif failure == 'infinity': encoded.iloc[0, 0] = np.inf
    else: context = RaceContext('r', dt.date(2026,9,12), context.started_horses)
    with pytest.raises(ValueError): study.predict_serving_encoded(model, context, encoded)


def test_gate_does_not_open_database_without_independent_review(tmp_path, monkeypatch):
    monkeypatch.setattr(study, 'SPEC', tmp_path/'spec')
    monkeypatch.setattr(study, 'REPRO', tmp_path/'repro')
    monkeypatch.setattr(study, 'WORK', tmp_path/'recent')
    monkeypatch.setattr(study, 'read_transaction', lambda: pytest.fail('database touched'))
    with pytest.raises((FileNotFoundError, ValueError)):
        study.capture()


def test_sql_boundaries_have_no_horse_master_or_unbounded_result_query():
    for query in (study.RUN_SELECTION_SQL, study.SNAPSHOT_SQL, study.APPEARANCE_SQL, study.RESULT_SQL):
        assert 'race_date >=' in query and 'race_date <=' in query
        assert 'JOIN horses ' not in query and 'FROM horses ' not in query
    assert 'race_results' not in study.SNAPSHOT_SQL + study.APPEARANCE_SQL + study.RUN_SELECTION_SQL
    assert 'features' not in study.RUN_SELECTION_SQL
    assert 'computed_at < r.post_time' in study.RUN_SELECTION_SQL
    assert 'updated_at < r.post_time' in study.RUN_SELECTION_SQL
    assert study.PARAMS['captured_before'].astimezone(dt.timezone.utc) == dt.datetime(2026,9,6,15,tzinfo=dt.timezone.utc)


def test_captured_race_population_or_te_failure_drops_whole_race():
    rows = raw_rows(); rows['jockey_id'] = [.2,.2,.2,.1]
    conversion = study.build_conversion({'jockey_id': enc({'a': .2})},
        {'jockey_id': enc({'a': .3, 'new': .8}, prior=.4)})
    with pytest.raises(study.InputExclusion):
        study.reconstruct_race(rows, tuple('abcd'), Model.feature_cols, conversion)
    rows['jockey_id'] = .2
    with pytest.raises(study.InputExclusion, match='population'):
        study.reconstruct_race(rows, tuple('abcde'), Model.feature_cols, conversion)


def test_outputs_are_exclusive_and_frozen_hash_changes_fail(tmp_path):
    path = tmp_path/'proof.json'
    study.write_json(path, {'a': 1})
    expected = {str(path): study.digest(path)}
    study.assert_hashes(expected)
    with pytest.raises(FileExistsError): study.write_json(path, {'a': 2})
    path.write_text('{}')
    with pytest.raises(ValueError): study.assert_hashes(expected)


def test_snapshot_time_rule_uses_jst_day_end_and_prestart():
    post = dt.datetime(2026,9,6,15,30,tzinfo=study.JST)
    good = post - dt.timedelta(seconds=1)
    assert study.timestamp_eligible(good, good, good, post)
    assert not study.timestamp_eligible(post, good, good, post)
    assert not study.timestamp_eligible(good, good, study.EXCLUSIVE_END, post)
    assert not study.timestamp_eligible(good.replace(tzinfo=None), good, good, post)


def test_head_ece_guard_cannot_hide_one_bad_head_in_mean():
    baseline = {'heads':{h:{'ece':.01} for h in ('win','top2','top3')}}
    candidate = deepcopy(baseline)
    candidate['heads']['win']['ece']=0
    candidate['heads']['top2']['ece']=.012
    pairs = {h+'_logloss':{'ci':[-.001,.0001]} for h in ('top2','top3')}
    report = study.quality_report(candidate,baseline,pairs)
    assert report['ece']['top2']['within_descriptive_guard'] is False
    assert report['ece']['win']['within_descriptive_guard'] is True
    assert report['topk']['top2']['status']=='SUPPORTED'


def test_unmeasured_head_and_ci_are_unresolved_not_pass():
    pairs = {h+'_logloss':{'ci':None} for h in ('top2','top3')}
    report = study.quality_report({'heads':{}},{'heads':{}},pairs)
    assert all(v['status']=='UNRESOLVED' for v in report['topk'].values())
    assert all(v['status']=='UNRESOLVED' and not v['within_descriptive_guard'] for v in report['ece'].values())


@pytest.mark.parametrize('complete_results',[True,False])
def test_synthetic_capture_predict_score_integration_no_live_database(tmp_path,monkeypatch,complete_results):
    area=tmp_path/'recent';monkeypatch.setattr(study,'WORK',area)
    monkeypatch.setattr(study,'gate',lambda:{})
    monkeypatch.setattr(study,'source_files',lambda:{})
    monkeypatch.setattr(study,'adapter_parity',lambda *_:{'synthetic':{'status':'PASS'}})
    model=Model()
    member_ids=('joint-42','joint-43','joint-44','anchor-42','anchor-43','anchor-44')
    members=tuple(SimpleNamespace(id=identity,model=model,terms=tuple(study.mc.JOINT_TERMS),coefficients=(0.,)*5)
                  for identity in member_ids)
    bundle=SimpleNamespace(members=members)
    candidate=SimpleNamespace(metadata={'feature_columns':model.feature_cols,'categorical_columns':model.categorical_cols},
        predict_encoded=lambda context,x:study.predict_serving_encoded(model,context,x))
    conversion=study.build_conversion(model.encoders,model.encoders)
    stages={r:{'lambda2':1.1,'lambda3':.9} for r in study.REGIMES}
    monkeypatch.setattr(study,'load_models',lambda:(bundle,candidate,conversion,stages,{}))
    historical=pd.DataFrame({'race_id':['old1','old2'],'horse_id':['a','a'],
        'race_date':[dt.date(2026,7,1),dt.date(2026,8,1)]})
    monkeypatch.setattr(study,'frozen_history',lambda:historical)
    snapshot=tmp_path/'snapshot.pkl';snapshot.write_bytes(b'synthetic-history')
    snapshot.with_suffix('.json').write_text('{}')
    monkeypatch.setattr(study.common,'SNAPSHOT',snapshot)
    monkeypatch.setattr(study.common,'SNAPSHOT_SHA256',study.digest(snapshot))
    post=dt.datetime(2026,8,29,15,tzinfo=study.JST);saved=post-dt.timedelta(hours=1)
    selected=[{'race_id':'r','race_date':study.START,'post_time':post,'prediction_run_id':'run',
               'computed_at':saved,'created_at':saved,'updated_at':saved,'n_snapshots':4}]
    selected += [{'race_id':f'missing-{i}','race_date':study.START,'post_time':post,
                  'prediction_run_id':None} for i in range(143)]
    rows=raw_rows()
    rows['jockey_id']=model.encoders['jockey_id'].transform(rows.jockey_id)
    snapshots=[{'race_id':'r','race_date':study.START,'post_time':post,'prediction_run_id':'run',
        'horse_id':r.horse_id,'feature_version':'features-021','computed_at':saved,
        'run_created_at':saved,'run_updated_at':saved,'snapshot_created_at':saved,'snapshot_updated_at':saved,
        'features':{**{c:r[c] for c in model.feature_cols},'_raw_win':.123,'extra_market':99}}
        for _,r in rows.iterrows()]
    appearances=rows[study.mc.KEYS].to_dict('records')
    outcomes=[{'race_id':'r','race_date':study.START,'horse_id':h,'result_status':'finished','finish_order':i+1}
              for i,h in enumerate(rows.horse_id)] if complete_results else []
    calls=[]
    @contextmanager
    def fake_transaction():
        yield object()
    monkeypatch.setattr(study,'read_transaction',fake_transaction)
    def fake_select(connection,sql,extra=None):
        calls.append(sql)
        if sql==study.RUN_SELECTION_SQL:return selected
        if sql==study.SNAPSHOT_SQL:return snapshots
        if sql==study.APPEARANCE_SQL:return appearances
        if sql==study.RESULT_SQL:
            proof=study.read_json(area/'execution-receipt.json')
            assert proof['status']=='PREDICTIONS_FROZEN_BEFORE_OUTCOMES'
            assert study.digest(area/'predictions.pkl')==proof['files'][str(area/'predictions.pkl')]
            return outcomes
        pytest.fail('unregistered SQL')
    monkeypatch.setattr(study,'select',fake_select)
    assert study.capture()['included']==1
    assert study.RESULT_SQL not in calls
    raw=study.load(area/'raw-inputs.pkl')['snapshots'][0]['features']
    assert set(raw)==set(model.feature_cols) and '_raw_win' not in raw
    assert study.predict()['n_races']==1
    assert study.RESULT_SQL not in calls
    assert study.score()['n_races']==1
    report=study.read_json(area/'summary.json')
    assert report['untouched_holdout'] is False and report['can_adopt'] is False
    assert report['reports']['preweight']['baseline']['n_complete_races']==int(complete_results)
    if not complete_results:
        assert report['reports']['preweight']['descriptive_nll']=='NOT_PROVEN'
        assert report['reports']['preweight']['quality']['topk']['top2']['status']=='UNRESOLVED'
