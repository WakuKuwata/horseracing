"""Contract and inference tests for the explicit six-member shadow profile."""
from copy import deepcopy
from dataclasses import replace
import datetime as dt
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from horseracing_features.registry import model_input_features
from horseracing_training.artifacts import feature_hash
from horseracing_training.predictor import assemble_predictions

from horseracing_serving import mixture_model as m


def manifest():
    return {
        'schema_version': 1, 'artifact_kind': 'candidate_mixture_bundle',
        'profile': m.PROFILE, 'mode': 'shadow', 'can_adopt': False, 'eligible_for_verdict': False,
        'bundle_id': 'test-six', 'created_at': '2026-09-08T00:00:00+00:00',
        'train_through': '2026-08-23', 'coefficient_train_through': '2025-12-31',
        'feature_profile': m.feature_profile(), 'inference': dict(m.INFERENCE),
        'members': [dict(identity, weight=1 / 6, artifact_dir='members/' + identity['id'],
                         files={n: 'a' * 64 for n in m.FILES},
                         correction={'terms': m.JOINT_TERMS if identity['branch'] == 'pruning' else ['gap_log'],
                                     'coefficients': [0.] * (5 if identity['branch'] == 'pruning' else 1),
                                     'source_study': 125 if identity['branch'] == 'pruning' else 118,
                                     'source_sha256': 'b' * 64, 'valid_year': 2026, 'fit_through': '2025-12-31'})
                    for identity in m.IDENTITIES],
        'sources': {'files': {}, 'runtime': {}}
    }


def test_profile_is_exact_registered_raw_columns():
    profile = m.feature_profile()
    assert len(profile['full_columns']) == 138
    assert feature_hash(profile['full_columns']) == m.FULL_HASH
    assert len(m.columns_for('pruning')) == 125
    assert feature_hash(m.columns_for('pruning')) == m.PRUNED_HASH
    m.validate_manifest(manifest())


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'order', 'weight', 'seed', 'columns',
                                  'drop', 'term_order', 'temperature', 'nan', 'year', 'fit_day',
                                  'source_study', 'mode', 'adopt', 'representation', 'eps', 'stage', 'path'])
def test_invalid_manifest_fails_before_artifact_loading(fault):
    d = deepcopy(manifest())
    if fault == 'missing': d['members'].pop()
    elif fault == 'duplicate': d['members'][1] = deepcopy(d['members'][0])
    elif fault == 'order': d['members'] = d['members'][::-1]
    elif fault == 'weight': d['members'][0]['weight'] = .5
    elif fault == 'seed': d['members'][0]['seed'] = 41
    elif fault == 'columns': d['feature_profile']['full_columns'].reverse()
    elif fault == 'drop': d['feature_profile']['pruning_drops'].pop()
    elif fault == 'term_order': d['members'][0]['correction']['terms'] = m.JOINT_TERMS[::-1]
    elif fault == 'temperature': d['members'][0]['correction']['coefficients'][-1] = -1.
    elif fault == 'nan': d['members'][0]['correction']['coefficients'][0] = np.nan
    elif fault == 'year': d['members'][0]['correction']['valid_year'] = 2025
    elif fault == 'fit_day': d['members'][0]['correction']['fit_through'] = '2026-01-01'
    elif fault == 'source_study': d['members'][3]['correction']['source_study'] = 125
    elif fault == 'mode': d['mode'] = 'active'
    elif fault == 'adopt': d['can_adopt'] = True
    elif fault == 'representation': d['feature_profile']['raw_representation'] = 'canonical-v1'
    elif fault == 'eps': d['inference']['assembly_eps'] = 1e-6
    elif fault == 'stage': d['inference']['postprocess'] = 'stage_discount'
    else: d['members'][0]['artifact_dir'] = '../outside'
    with pytest.raises(ValueError): m.validate_manifest(d)


def test_manifest_sha_and_file_integrity_precede_deserialization(tmp_path, monkeypatch):
    p = tmp_path / 'bundle.json'; p.write_text(json.dumps(manifest()))
    with pytest.raises(ValueError, match='SHA'): m.load_mixture_bundle(p, expected_sha256='0' * 64)
    calls = []
    monkeypatch.setattr(m, '_load_member', lambda *a: calls.append(a))
    with pytest.raises(ValueError): m.load_mixture_bundle(p)
    assert calls == []


def test_paths_cannot_escape_or_use_symlinks(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + '-outside'); outside.mkdir()
    (tmp_path / 'link').symlink_to(outside, target_is_directory=True)
    for rel in ('../outside/model.txt', str(outside), 'link/model.txt'):
        with pytest.raises(ValueError): m.contained_path(tmp_path, rel)


class Identity:
    def transform(self, x): return x


class FakeModel:
    feature_cols = model_input_features()
    categorical_cols = []
    encoders = {}
    calibrator = Identity()
    def raw_predict(self, x):
        weight = x['weight'].fillna(400).to_numpy()
        raw = np.exp((weight - weight.max()) / 100.)
        return raw / raw.sum()


def bundle():
    d = manifest()
    members = tuple(m.MixtureMember(item['id'], FakeModel(), tuple(item['correction']['terms']),
                                   tuple(item['correction']['coefficients'])) for item in d['members'])
    return m.MixtureBundle(None, 'c' * 64, d, members)


def rows():
    frame = pd.DataFrame({c: [0., 0., 0., 0.] for c in model_input_features()})
    frame['race_id'] = ['r'] * 4; frame['horse_id'] = ['d', 'b', 'a', 'c']
    frame['race_date'] = [dt.date(2026, 9, 8)] * 4
    frame['sex'] = ['牡', '牝', 'セ', '牡']; frame['days_since_last'] = [30., 50., np.nan, 90.]
    frame['weight'] = [400., 500., 450., 480.]
    return frame


def history():
    return pd.DataFrame({'race_id': ['h1', 'h2'], 'horse_id': ['b', 'b'],
                         'race_date': [dt.date(2025, 1, 1), dt.date(2025, 3, 1)]})


def test_identity_bundle_full_information_matches_direct_all_heads_and_preserves_input():
    data = rows(); before = data.copy(deep=True)
    result = m.predict_mixture(bundle(), 'r', data, history(), 'full_information_replay')
    ordered = data.sort_values('horse_id')
    expected = assemble_predictions(ordered.horse_id.tolist(), FakeModel().raw_predict(ordered), eps=1e-6)
    assert list(result.predictions) == ['a', 'b', 'c', 'd']
    assert np.allclose([[p.win, p.top2, p.top3] for p in result.predictions.values()],
                       [[p.win, p.top2, p.top3] for p in expected.values()], rtol=0, atol=1e-15)
    assert result.audit['diagnostic_only'] is True
    pd.testing.assert_frame_equal(data, before)


def test_partial_weights_serving_equals_preweight_and_differs_from_raw_replay():
    data = rows(); data.loc[0, 'weight'] = np.nan
    a = m.predict_mixture(bundle(), 'r', data, history(), 'serving')
    b = m.predict_mixture(bundle(), 'r', data, history(), 'preweight')
    c = m.predict_mixture(bundle(), 'r', data, history(), 'full_information_replay')
    assert a.predictions == b.predictions
    assert a.predictions != c.predictions
    assert a.audit['weight_normalised'] is True
    assert a.inputs['weight'].isna().all()
    assert a.audit['n_weighed_before'] == 3


def test_full_serving_matches_raw_replay():
    data = rows()
    assert m.predict_mixture(bundle(), 'r', data, history(), 'serving').predictions == m.predict_mixture(
        bundle(), 'r', data, history(), 'full_information_replay').predictions


@pytest.mark.parametrize('fault', ['duplicate', 'missing_feature', 'mixed_day', 'wrong_year', 'regime', 'infinity'])
def test_bad_inference_input_fails(fault):
    data = rows(); regime = 'preweight'
    if fault == 'duplicate': data.loc[0, 'horse_id'] = 'a'
    elif fault == 'missing_feature': data = data.drop(columns=['weight'])
    elif fault == 'mixed_day': data.loc[0, 'race_date'] = dt.date(2026, 9, 9)
    elif fault == 'wrong_year': data['race_date'] = dt.date(2027, 1, 1)
    elif fault == 'regime': regime = 'unknown'
    else: data.loc[0, 'days_since_last'] = np.inf
    with pytest.raises(ValueError): m.predict_mixture(bundle(), 'r', data, history(), regime)
