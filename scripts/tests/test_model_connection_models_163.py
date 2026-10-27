from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pytest

from scripts.roi_explore.model_connection_163 import models
from scripts.roi_explore.prospective_rehearsal_162 import contract as old_contract
from scripts.roi_explore.prospective_rehearsal_162 import fixtures as old_fixtures

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def fixtures():
    b = old_fixtures.bundle(old_contract.make_protocol())
    rows = {}
    for kind in ('mev', 'legacy'):
        rows[kind] = [dict(horse_id=r['horse_id'], horse_number=r['horse_number'], features=dict(r['features']))
            for r in b['predictions'][kind]['rows']]
        for r in rows[kind]:
            for c in models.input_columns(kind):
                r['features'].setdefault(c, None)
            if kind == 'legacy':
                r['features'].update(career_starts=0, days_since_last=None, sex='牡')
    return b, rows


def predict(kind, fixtures):
    b, rows = fixtures
    fn = models.infer_mev if kind == 'mev' else models.infer_legacy
    extra = dict(history=[]) if kind == 'legacy' else {}
    return fn(rows[kind], race_id=b['race_id'], race_date=b['race_date'], **extra)


def bound_prediction(kind, fixtures, result):
    return dict(rows=[dict(**row, probability=result['probabilities'][row['horse_id']])
        for row in fixtures[1][kind]], evidence=result['evidence'])


@pytest.mark.parametrize('kind,count', [('mev', 15), ('legacy', 6)])
def test_real_members_and_evidence_binding(fixtures, kind, count):
    b, _ = fixtures
    result = predict(kind, fixtures)
    evidence = result['evidence']
    assert evidence['actual_model_inference'] is True
    assert evidence['eligible_for_prospective'] is False
    assert evidence['new_fits'] == 0 and evidence['num_threads'] == 1
    assert len(evidence['member_probabilities'] if kind == 'mev' else evidence['members']) == count
    assert all(0 < p < 1 for p in result['probabilities'].values())
    if kind == 'legacy':
        assert sum(result['probabilities'].values()) == pytest.approx(1, abs=1e-12)
    assert models.validate_evidence_binding(kind, bound_prediction(kind, fixtures, result),
        race_id=b['race_id'], race_date=b['race_date'])
    assert predict(kind, fixtures)['probabilities'] == result['probabilities']


def test_mev_product_path_parity(fixtures):
    from horseracing_training.market_ev import predict_ensemble
    b, rows = fixtures
    frame, _ = models._input_frame(rows['mev'], b['race_id'], b['race_date'], 'mev')
    frame['year'], frame['race_ok'] = 2026, True
    frame['horse_row_updated_at'] = '2026-10-25T05:45:00Z'  # fixture metadata only, not an availability receipt
    frame['odds_observed_fallback'] = False
    model, _ = models._mev()
    product = predict_ensemble(model, frame).set_index('horse_id').win_prob.to_dict()
    assert product == pytest.approx(predict('mev', fixtures)['probabilities'], abs=1e-15)
    assert len(models.input_columns('mev')) == 77  # 66 numeric PLUS all 11 categories


def test_legacy_saved_capture_full_input_history_parity():
    path = ROOT/'artifacts/133-prospective-paired-comparison/records/capture/202609040611.json'
    saved = json.loads(path.read_text())
    preweight = saved['regimes']['preweight']
    columns = models.input_columns('legacy')
    rows = []
    for horse_id, values in preweight['inputs']['rows'].items():
        data = dict(zip(preweight['inputs']['columns'], values, strict=True))
        rows.append(dict(horse_id=horse_id, horse_number=int(data['horse_number']),
            features={c: data[c] for c in columns}))
    history = [dict(race_id=rid, horse_id=hid, race_date=day)
        for rid, hid, day in preweight['history']['rows']]
    result = models.infer_legacy(rows, race_id=saved['race_id'], race_date=saved['race_day'], history=history)
    expected = {h: heads[0] for h, heads in preweight['new_research_heads_not_served'].items()}
    assert result['probabilities'] == pytest.approx(expected, rel=0, abs=1e-14)
    assert len(result['evidence']['started_history']['rows']) == preweight['history']['n_rows'] == 340


@pytest.mark.parametrize('kind', ['mev', 'legacy'])
def test_probability_matrix_and_member_tamper_rejected(fixtures, kind):
    b, _ = fixtures
    result = predict(kind, fixtures)
    saved = bound_prediction(kind, fixtures, result)
    changed = copy.deepcopy(saved)
    changed['rows'][0]['probability'] += .01
    with pytest.raises(ValueError, match='bound_probability_sha'):
        models.validate_evidence_binding(kind, changed, race_id=b['race_id'], race_date=b['race_date'])
    changed = copy.deepcopy(saved)
    changed['evidence']['consumed_input']['rows'][0][0] = 'changed'
    with pytest.raises(ValueError, match='bound_evidence_mismatch'):
        models.validate_evidence_binding(kind, changed, race_id=b['race_id'], race_date=b['race_date'])
    changed = copy.deepcopy(saved)
    if kind == 'mev':
        changed['evidence']['member_probabilities'].pop('15')
    else:
        changed['evidence']['members'].pop('anchor-44')
    with pytest.raises(ValueError):
        models.validate_evidence_binding(kind, changed, race_id=b['race_id'], race_date=b['race_date'])


def test_binding_does_not_run_booster_prediction(fixtures, monkeypatch):
    b, _ = fixtures
    result = predict('legacy', fixtures)
    def denied(*args, **kwargs):
        raise AssertionError('booster prediction forbidden in binding validator')
    monkeypatch.setattr(models, 'predict_base', denied)
    monkeypatch.setattr(models, 'predict_mixture', denied)
    assert models.validate_evidence_binding('legacy', bound_prediction('legacy', fixtures, result),
        race_id=b['race_id'], race_date=b['race_date'])


@pytest.mark.parametrize('kind', ['mev', 'legacy'])
def test_missing_consumed_columns_rejected(fixtures, kind):
    b, source = fixtures
    rows = copy.deepcopy(source[kind])
    rows[0]['features'].pop('sex' if kind == 'mev' else 'distance')
    with pytest.raises(ValueError, match='incomplete_model_input'):
        fn = models.infer_mev if kind == 'mev' else models.infer_legacy
        fn(rows, race_id=b['race_id'], race_date=b['race_date'], **({'history': []} if kind == 'legacy' else {}))


def test_legacy_requires_actual_started_history(fixtures):
    b, source = fixtures
    rows = copy.deepcopy(source['legacy'])
    rows[0]['features'].update(career_starts=1, days_since_last=20)
    with pytest.raises(ValueError, match='history disagrees'):
        models.infer_legacy(rows, race_id=b['race_id'], race_date=b['race_date'], history=[])
    history = [dict(race_id='202605040001', horse_id=rows[0]['horse_id'], race_date='2026-10-01')]
    with pytest.raises(ValueError, match='history_gap_mismatch'):
        models.infer_legacy(rows, race_id=b['race_id'], race_date=b['race_date'], history=history)


def test_legacy_future_history_and_weight_rejected(fixtures):
    b, source = fixtures
    with pytest.raises(ValueError, match='history_not_strict_past'):
        models.infer_legacy(source['legacy'], race_id=b['race_id'], race_date=b['race_date'], history=[
            dict(race_id='202605040001', horse_id=source['legacy'][0]['horse_id'], race_date='2026-10-25')])
    rows = copy.deepcopy(source['legacy'])
    rows[0]['features']['weight'] = 480
    with pytest.raises(ValueError, match='legacy_not_preweight'):
        models.infer_legacy(rows, race_id=b['race_id'], race_date=b['race_date'], history=[])


def test_year_and_identity_fail_closed(fixtures):
    b, source = fixtures
    with pytest.raises(ValueError, match='unsupported_model_year'):
        models.infer_mev(source['mev'], race_id=b['race_id'], race_date='2027-01-01')
    rows = copy.deepcopy(source['mev'])
    rows[0]['features']['horse_number'] = 999
    with pytest.raises(ValueError, match='model_horse_number'):
        models.infer_mev(rows, race_id=b['race_id'], race_date=b['race_date'])


def test_encoded_mev_is_actual_15_member_mean(fixtures):
    result = predict('mev', fixtures)
    evidence = result['evidence']
    ids = evidence['encoded_input']['horse_ids']
    values = np.array([[evidence['member_probabilities'][str(i)][h] for h in ids] for i in range(1, 16)])
    assert np.array_equal(values.mean(axis=0), [result['probabilities'][h] for h in ids])
    assert len(evidence['encoded_input']['columns']) == 77


def test_mev_consumes_declared_category_mapping(fixtures):
    b, source = fixtures
    rows = copy.deepcopy(source['mev'])
    rows[0]['features'].update(venue_code='05', track_type='芝', sex='牡')
    rows[1]['features']['track_type'] = 'unseen-category'
    result = models.infer_mev(rows, race_id=b['race_id'], race_date=b['race_date'])
    encoded = result['evidence']['encoded_input']
    spec = json.loads((models.MEV_DIR/'ensemble.spec.json').read_text())
    first, second = encoded['rows'][:2]
    for key in ('venue_code', 'track_type', 'sex'):
        assert first[encoded['columns'].index(key)] == spec['cat_maps'][key][rows[0]['features'][key]]
    assert second[encoded['columns'].index('track_type')] is None


@pytest.mark.parametrize('kind', ['mev', 'legacy'])
def test_evidence_binding_survives_canonical_publication(fixtures, kind):
    b, _ = fixtures
    result = predict(kind, fixtures)
    payload = json.loads(models.canonical(bound_prediction(kind, fixtures, result)))
    assert models.validate_evidence_binding(kind, payload, race_id=b['race_id'], race_date=b['race_date'])
