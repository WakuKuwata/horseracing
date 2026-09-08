"""129 file-only build contracts. No database, smoke, or booster fit is run here."""
from copy import deepcopy
import datetime as dt
import json
import hashlib
import pickle
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import candidate_mixture_build as b
from horseracing_eval.predictor import HorseEntry, RaceContext
from horseracing_training.dataset import TrainingMatrix


def test_members_and_capacity_are_fixed():
    cfg = b.build_config()
    assert [m['id'] for m in cfg['members']] == ['joint-42', 'joint-43', 'joint-44', 'anchor-42', 'anchor-43', 'anchor-44']
    assert cfg['full_outer_fits'] == 6 and cfg['full_boosters'] == 48
    assert cfg['train_through'] == '2026-08-23'
    assert cfg['coefficient_train_through'] == '2025-12-31'
    assert cfg['db_registration'] is False


@pytest.mark.parametrize('name', [m['id'] for m in b.MEMBERS])
def test_recipe_is_original_except_seed_and_exact_registered_scope(name):
    member = b.member_for(name)
    recipe = b.make_recipe(member)
    cfg = deepcopy(b.s.load_config()); cfg['arms']['seed'] = member['seed']
    old = b.s.p.make_recipe(cfg, b.s.arm(cfg, member['branch'])['drop_features'])
    assert recipe == old
    assert recipe.resolved_params()['n_estimators'] == 900
    assert recipe.resolved_params()['colsample_bytree'] == 1.
    assert recipe.weight_mask_rate == .5 and recipe.weight_mask_seed == 20260810
    assert len(b.expected_columns(member)) == (125 if member['branch'] == 'pruning' else 138)


@pytest.mark.parametrize('bad', ['joint-41', '../joint-42', 'anchor-45', '', None])
def test_unknown_members_fail(bad):
    with pytest.raises(ValueError): b.member_for(bad)


def toy_matrix():
    cols = b.s.p.columns_from_model() + b.s.p.OBSERVATION_COLUMNS
    df = pd.DataFrame({c: [0., 1., 2.] for c in cols})
    df.insert(0, 'horse_id', ['H', 'J', 'K']); df.insert(0, 'race_id', ['r', 'r', 'extra'])
    df['race_date'] = [dt.date(2026, 8, 23)] * 3; df['win'] = [1., 0., 0.]; df['finish_rank'] = [1., 2., 0.]
    return TrainingMatrix(df, cols, [])


def toy_races():
    return [SimpleNamespace(context=RaceContext('r', dt.date(2026, 8, 23), (HorseEntry('H'), HorseEntry('J'))),
                            n_result_rows=2, labels=[SimpleNamespace(horse_id='H', win=1)])]


def test_population_excludes_and_describes_snapshot_only_rows():
    pop = b.population(toy_matrix(), toy_races())
    assert pop['n_train_rows'] == 2 and pop['n_train_races'] == 1
    assert pop['snapshot_only_rows'] == 1 and pop['snapshot_only_races'][0]['race_id'] == 'extra'


@pytest.mark.parametrize('fault', ['duplicate', 'missing_horse', 'extra_horse', 'wrong_day', 'future', 'inf', 'incomplete'])
def test_population_fails_bad_inputs(fault):
    matrix, races = toy_matrix(), toy_races()
    if fault == 'duplicate': matrix.frame.loc[1, 'horse_id'] = 'H'
    elif fault == 'missing_horse': matrix.frame.loc[1, 'race_id'] = 'other'
    elif fault == 'extra_horse': matrix.frame.loc[2, 'race_id'] = 'r'
    elif fault == 'wrong_day': matrix.frame.loc[0, 'race_date'] = dt.date(2026, 8, 22)
    elif fault == 'future': races[0].context = RaceContext('r', dt.date(2026, 8, 24), races[0].context.started_horses)
    elif fault == 'inf': matrix.frame.loc[0, matrix.feature_cols[0]] = np.inf
    elif fault == 'incomplete': races[0].n_result_rows = None
    with pytest.raises(ValueError): b.population(matrix, races)


def test_prediction_checks_detect_nonfinite_and_bad_order():
    from horseracing_eval.predictor import Prediction
    valid = {'a': Prediction(.4, 1., 1.), 'b': Prediction(.6, 1., 1.)}
    assert b.prediction_array(['a', 'b'], valid).shape == (2, 3)
    with pytest.raises(ValueError): b.prediction_array(['b', 'a'], valid)
    for value in (float('nan'), float('inf'), -.1):
        bad = dict(valid); bad['a'] = Prediction(value, 1., 1.)
        with pytest.raises(ValueError): b.prediction_array(['a', 'b'], bad)


def test_json_is_exclusive_and_nonfinite_rejected(tmp_path):
    path = tmp_path / 'a.json'
    b.write_json(path, {'ok': 1})
    with pytest.raises(FileExistsError): b.write_json(path, {'ok': 2})
    assert json.loads(path.read_text()) == {'ok': 1}
    with pytest.raises(ValueError): b.write_json(tmp_path / 'bad.json', {'bad': np.inf})
    assert not (tmp_path / 'bad.json').exists()


def test_completed_absent_false_partial_preserved(tmp_path, monkeypatch):
    monkeypatch.setattr(b, 'WORK', tmp_path)
    member = b.member_for('joint-42')
    assert b.completed(member, {}, {}) is False
    partial = b.member_dir(member['id']); partial.mkdir(parents=True)
    (partial / 'model.txt').write_text('interrupted')
    with pytest.raises(ValueError, match='Incomplete'): b.completed(member, {}, {})
    assert (partial / 'model.txt').read_text() == 'interrupted'


def test_stage_or_orphan_receipt_is_not_restartable(tmp_path, monkeypatch):
    monkeypatch.setattr(b, 'WORK', tmp_path)
    m = b.MEMBERS[0]
    b.stage_dir(m['id']).mkdir(parents=True)
    with pytest.raises(ValueError, match='Incomplete'): b.completed(m, {}, {})


@pytest.mark.parametrize('workers', [0, 3, -1, True])
def test_resource_limit(workers):
    with pytest.raises(ValueError): b.check_workers(workers)


def test_other_training_lock_stops(tmp_path, monkeypatch):
    monkeypatch.setattr(b, 'ROOT', tmp_path)
    p = tmp_path / 'artifacts/128-extra-feature-full-quality/running.lock'
    p.parent.mkdir(parents=True); p.write_text('busy')
    with pytest.raises(ValueError, match='training'): b.assert_no_other_training()


def test_frozen_outcomes_only_requested_and_patch_restored():
    import horseracing_training.calib_split as c
    old = c._started_all_outcomes
    with b.frozen_outcomes(toy_races()):
        assert c._started_all_outcomes(None, ['r']) == {'r': (2, {'H'})}
        with pytest.raises(ValueError): c._started_all_outcomes(None, ['unknown'])
    assert c._started_all_outcomes is old


def test_frozen_outcomes_restored_on_exception():
    import horseracing_training.calib_split as c
    old = c._started_all_outcomes
    with pytest.raises(RuntimeError):
        with b.frozen_outcomes(toy_races()): raise RuntimeError('stop')
    assert c._started_all_outcomes is old


def test_build_dependency_hash_excludes_unrelated_new_serving_modules(monkeypatch):
    paths = b.build_source_paths()
    assert Path(b.__file__) in paths
    assert ROOT / 'scripts/tests/test_candidate_mixture_build.py' in paths
    assert not any('mixture_model.py' in str(p) for p in paths)
    assert ROOT / 'serving/src/horseracing_serving/model_loader.py' in paths


def fitted_info(member, pop, smoke=False):
    recipe = b.make_recipe(member, smoke)
    oof = dict(sufficient=True, calibrator_degenerate=False, n_oof_rows=2500, n_oof_races=250,
               n_positives=250, n_distinct_scores=800, score_min=.001, score_max=.9,
               oof_pred_from='2008-01-01', oof_pred_through=pop['train_through'])
    protocol = {k: oof[k] for k in ('n_oof_rows', 'n_oof_races', 'n_positives', 'n_distinct_scores', 'oof_pred_from', 'oof_pred_through')}
    protocol.update(protocol='strict_past_oof_isotonic_v1', n_oof_blocks=2 if smoke else 8,
                    score_space='raw_race_softmax', booster_calib_frac=0., threshold_checksum='threshold')
    info = dict(feature_cols=b.expected_columns(member), seed=member['seed'], objective='pl_topk',
        postprocess='group_softmax', params=recipe.resolved_params(), race_class_representation='raw',
        model_degenerate=False, calibrator_degenerate=False, calibration='isotonic_strict_past_oof',
        calibration_split_unit=None, calib_from=None, calib_through=None, calib_frac=0.,
        n_train_rows=pop['n_train_rows'], n_model_rows=pop['n_train_rows'], n_calib_rows=2500,
        train_through=pop['train_through'], model_fit_through=pop['train_through'],
        target_encode_cols=list(recipe.target_encode_cols), te_smoothing=recipe.te_smoothing,
        categorical_cols=[c for c in pop['categorical_cols'] if c not in recipe.target_encode_cols and c not in recipe.drop_features],
        calibration_protocol=protocol,
        weight_mask={'rate': .5, 'seed': 20260810, 'unit': 'race', 'columns': ['weight', 'weight_diff', 'carried_weight_ratio']})
    return info, oof


@pytest.mark.parametrize('name', [m['id'] for m in b.MEMBERS])
@pytest.mark.parametrize('smoke', [False, True])
def test_actual_fit_metadata_has_expected_recipe_capacity_oof(name, smoke):
    member = b.member_for(name)
    pop = dict(n_train_rows=6000, train_through='2007-12-31' if smoke else '2026-08-23',
               categorical_cols=['sex', 'jockey_id', 'trainer_id', 'venue_code'])
    info, oof = fitted_info(member, pop, smoke)
    b.validate_fit(info, oof, member, pop, smoke)


@pytest.mark.parametrize('fault', ['seed', 'columns', 'trees', 'mask', 'model_rows', 'cutoff', 'identity',
                                  'insufficient', 'nan', 'protocol', 'calib_count', 'categorical', 'te'])
def test_actual_fit_fails_closed(fault):
    member = b.MEMBERS[0]
    pop = dict(n_train_rows=6000, train_through='2026-08-23', categorical_cols=['sex', 'race_class'])
    info, oof = fitted_info(member, pop)
    if fault == 'seed': info['seed'] = 43
    elif fault == 'columns': info['feature_cols'].reverse()
    elif fault == 'trees': info['params']['n_estimators'] = 300
    elif fault == 'mask': info['weight_mask']['seed'] = 42
    elif fault == 'model_rows': info['n_model_rows'] = 3000
    elif fault == 'cutoff': info['model_fit_through'] = '2025-12-31'
    elif fault == 'identity': info['calibrator_degenerate'] = True
    elif fault == 'insufficient': oof['sufficient'] = False
    elif fault == 'nan': oof['score_min'] = float('nan')
    elif fault == 'protocol': info['calibration_protocol']['n_positives'] = 249
    elif fault == 'calib_count': info['n_calib_rows'] = 1
    elif fault == 'categorical': info['categorical_cols'].reverse()
    elif fault == 'te': info['te_smoothing'] = 1.
    with pytest.raises(ValueError): b.validate_fit(info, oof, member, pop)


def complete_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(b, 'WORK', tmp_path)
    b.write_json(b.freeze_path(), {'frozen': True})
    m = b.MEMBERS[0]; pop = {'n_train_rows': 6000, 'train_through': '2026-08-23', 'categorical_cols': []}
    frozen = {'source_hash': 'source', 'runtime': {'v': 1}, 'population': pop,
              'oof_partition_hash': 'partition', 'probe_race_ids': ['r']}
    info, oof = fitted_info(m, pop)
    meta = dict(source_hash='source', runtime=frozen['runtime'], run_freeze_sha256=b.digest(b.freeze_path()),
        smoke=False, recipe=b.recipe_metadata(m), population=pop, actual_params=b.make_recipe(m).resolved_params(),
        coefficient_train_through='2025-12-31', oof_partition_hash='partition', fit_info=info, oof_info=oof)
    path = b.member_dir(m['id']); path.mkdir(parents=True)
    for file in b.FILES:
        if file == 'metadata.json': b.write_json(path / file, meta)
        else: (path / file).write_text('fixture')
    receipt = dict(member=m, smoke=False, source_hash='source', runtime=frozen['runtime'],
                   run_freeze_sha256=b.digest(b.freeze_path()), model_threads=1, fit_seconds=10.,
                   can_adopt=False, eligible_for_verdict=False, db_registration=False, population=pop,
                   parity=dict(status='PASS', max_abs_diff=0., n_head_values=6, race_ids=['r']),
                   files={file: b.digest(path / file) for file in b.FILES})
    b.write_json(b.receipt_path(m['id']), receipt)
    monkeypatch.setattr(b, 'load_member_artifact', lambda p, expected_metadata=None: object())
    return m, frozen, meta, receipt


def test_completed_resume_verifies_all_artifacts(tmp_path, monkeypatch):
    m, frozen, _, _ = complete_fixture(tmp_path, monkeypatch)
    assert b.completed(m, b.build_config(), frozen)
    (b.member_dir(m['id']) / 'calibrator.pkl').write_text('replaced')
    with pytest.raises(ValueError, match='SHA'): b.completed(m, b.build_config(), frozen)


@pytest.mark.parametrize('fault', ['missing', 'source', 'population', 'recipe', 'oof', 'parity', 'runtime', 'coefficient_cutoff'])
def test_resume_rejects_contradictions_even_with_reissued_file_hash(tmp_path, monkeypatch, fault):
    m, frozen, meta, receipt = complete_fixture(tmp_path, monkeypatch)
    if fault == 'missing':
        (b.member_dir(m['id']) / 'preprocessor.pkl').unlink()
    elif fault == 'source': receipt['source_hash'] = 'changed'
    elif fault == 'population': meta['population'] = dict(meta['population'], n_train_rows=5999)
    elif fault == 'recipe': meta['recipe']['seed'] = 43
    elif fault == 'oof': meta['oof_info']['sufficient'] = False
    elif fault == 'parity': receipt['parity']['race_ids'] = ['other']
    elif fault == 'runtime': meta['runtime'] = {'v': 2}
    elif fault == 'coefficient_cutoff': meta['coefficient_train_through'] = '2026-08-23'
    if fault != 'missing':
        p = b.member_dir(m['id']) / 'metadata.json'; p.write_text(json.dumps(meta))
        receipt['files']['metadata.json'] = b.digest(p)
    b.receipt_path(m['id']).write_text(json.dumps(receipt))
    with pytest.raises(ValueError): b.completed(m, b.build_config(), frozen)


def test_serialization_parity_checks_every_head_and_restores_data(monkeypatch):
    from horseracing_eval.predictor import Prediction
    matrix, races = toy_matrix(), toy_races()
    values = {'H': Prediction(.4, 1., 1.), 'J': Prediction(.6, 1., 1.)}
    pred = SimpleNamespace(_data=matrix, predict_race=lambda c: values)
    monkeypatch.setattr(b, 'predict_loaded', lambda model, ctx, frame: values)
    result = b.serialization_parity(pred, object(), matrix, races)
    assert result['n_head_values'] == 6 and result['max_abs_diff'] == 0.
    assert pred._data is matrix
    bad = {'H': Prediction(.400000001, 1., 1.), 'J': Prediction(.599999999, 1., 1.)}
    monkeypatch.setattr(b, 'predict_loaded', lambda model, ctx, frame: bad)
    with pytest.raises(ValueError, match='Non-exact'): b.serialization_parity(pred, object(), matrix, races)
    assert pred._data is matrix


def test_probe_selection_is_outcome_free_and_covers_every_year():
    races = [SimpleNamespace(context=RaceContext(str(y * 100 + i), dt.date(y, 1, i + 1), (HorseEntry('H'),)))
             for y in [2007, 2018, 2026] for i in range(10)]
    probes = b.select_probes(races)
    assert len(probes) == 9
    assert [r.context.race_date.day for r in probes] == [1, 6, 10] * 3


def test_strict_oof_partition_fixed_eight_blocks():
    races = [SimpleNamespace(context=RaceContext(str(i), dt.date(2007, 1, 1) + dt.timedelta(days=i), (HorseEntry('H'),))) for i in range(24)]
    assert b.partition_hash(races, 8) == b.partition_hash(races, 8)
    assert b.partition_hash(races, 8) != b.partition_hash(races, 2)
    with pytest.raises(ValueError): b.partition_hash(races[:2], 8)


def test_real_native_artifact_serialization_without_any_fit(tmp_path):
    """Roundtrip a previously fitted booster/calibrator/TE state, never fit a test model."""
    import lightgbm as lgb
    source = b.s.p.MODEL
    booster = lgb.Booster(model_file=str(source / 'model.txt'))
    with (source / 'calibrator.pkl').open('rb') as fh: calib = pickle.load(fh)
    with (source / 'preprocessor.pkl').open('rb') as fh: prep = pickle.load(fh)
    prep.update(race_class_representation='raw')
    member = b.member_for('anchor-42'); cols = b.expected_columns(member)
    vocab = b.categorical_vocab_from_booster(booster, cols, prep['categorical_cols'])
    checksum = hashlib.sha256(json.dumps(calib.params_dict(), sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()
    meta = dict(member_id='anchor-42', branch='anchor', seed=42, artifact_kind='candidate_mixture_member', mode='shadow',
        feature_cols=cols, feature_hash=b.feature_hash(cols), categorical_cols=prep['categorical_cols'],
        feature_version=prep['feature_version'], race_class_representation='raw',
        can_adopt=False, eligible_for_verdict=False, db_registration=False,
        fit_info={'target_encode_cols': prep['target_encode_cols'], 'calibration_protocol': {'threshold_checksum': checksum}},
        actual_params={'n_estimators': 900}, categorical_vocab=vocab, categorical_vocab_hash=b.vocab_hash(vocab))
    model_path = tmp_path / 'member'; model_path.mkdir()
    b._write_model(SimpleNamespace(win_model_=SimpleNamespace(booster_=booster)), model_path / 'model.txt')
    for name, obj in [('calibrator.pkl', calib), ('preprocessor.pkl', prep)]:
        with (model_path / name).open('xb') as fh: pickle.dump(obj, fh, protocol=5)
    b.write_json(model_path / 'metadata.json', meta)
    loaded = b.load_member_artifact(model_path, b.clean(meta))
    before = b.ServingModel('anchor-42', booster, 0., calib, cols, prep['categorical_cols'],
        encoders=prep['encoders'], objective='pl_topk')
    rows = pd.DataFrame({c: [1., 2.] for c in cols})
    for c in prep['categorical_cols']:
        values = vocab[c]
        rows[c] = pd.Categorical([values[0], None], categories=values)
    for c in prep['encoders']: rows[c] = pd.Series(['unseen_a', 'unseen_b'], dtype='category')
    rows['race_id'] = 'r'; rows['horse_id'] = ['H', 'J']
    ctx = toy_races()[0].context
    first = b.prediction_array(['H', 'J'], b.predict_loaded(before, ctx, rows))
    second = b.prediction_array(['H', 'J'], b.predict_loaded(loaded, ctx, rows))
    np.testing.assert_array_equal(first, second)
    meta['fit_info']['calibration_protocol']['threshold_checksum'] = 'swapped'
    (model_path / 'metadata.json').write_text(json.dumps(meta))
    with pytest.raises(ValueError, match='threshold'): b.load_member_artifact(model_path)
