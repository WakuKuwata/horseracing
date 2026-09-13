from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import calibration_serving_recheck135 as d
from horseracing_eval.predictor import Prediction
from horseracing_eval.stage_discount import StageDiscount, fit_stage_discount


def row(year=2020, number=1, *, incomplete=False, win=None):
    ids = ('z', 'b', 'a', 'c')
    win = np.array([.4, .3, .2, .1] if win is None else win)
    p = d.common.heads_from_win(win)
    return {'race_id': f'{year}0101{number:02d}01', 'race_date': f'{year}-01-{number:02d}',
            'ids': ids, 'n_result_rows': 3 if incomplete else 4,
            'labels': [('z', 1, 1, 1), ('b', 0, 1, 1), ('a', 0, 0, 1)],
            'arms': {'head_mean': p, 'annual_stage_discount': p, 'mean_win_harville': p}}


def stored_fits(records):
    fits = []
    for year in sorted({r.year for r in records}):
        prior = [(r, s) for r in records if r.year < year and (s := d.c132_sample(r)) is not None]
        sd = fit_stage_discount([s for _, s in prior], min_races=300)
        fits.append({'year': year, 'lambda2': sd.lambda2, 'lambda3': sd.lambda3,
                     'n_stage2': sd.n_races_l2, 'n_stage3': sd.n_races_l3, 'fallback': sd.fallback,
                     'fit_through': str(prior[-1][0].race_date) if prior else None,
                     'sample_identity_sha256': d.common.stable_hash([(r.race_id, str(r.race_date)) for r, s in prior]),
                     'sample_values_sha256': d.common.stable_hash([(r.race_id, s.win, s.i1, s.i2, s.i3) for r, s in prior])})
    return fits


def test_same_lambda_adapter_matches_actual_131_function(monkeypatch):
    from horseracing_serving import mixture_serving as native
    ids = ['z', 'b', 'a']
    win = np.array([.65, .3499999, .0000001])
    rows = pd.DataFrame({'horse_id': ids})
    mock = SimpleNamespace(bundle=object(), coefficient_grace_years=1, feature_cols=[],
                           race_class_representation='raw', feature_version='features-021')
    upstream = SimpleNamespace(predictions={h: Prediction(p, p, p) for h, p in zip(ids, win)}, audit={})
    monkeypatch.setattr(native, 'prepare_race_inputs', lambda *a: (rows, {}))
    monkeypatch.setattr(native, 'history_for', lambda *a: pd.DataFrame())
    monkeypatch.setattr(native, 'assert_history_matches_features', lambda *a: None)
    monkeypatch.setattr(native, 'predict_mixture', lambda *a, **k: upstream)
    for sd in (None, StageDiscount(), StageDiscount(.85, .71)):
        got = native.predict_mixture_race(mock, '202001010101', rows, pd.DataFrame(), stage_discount=sd)[0]
        expected = np.array([[got[h].win, got[h].top2, got[h].top3] for h in ids])
        actual = d.native_display(tuple(ids), win, sd)
        assert np.array_equal(actual, expected)
        assert np.array_equal(actual[:, 0], win)  # tiny valid win must remain below 1e-6


def test_native_conversion_preserves_rank_under_id_sort_and_clips_at_engine_epsilon():
    r = d.decode_rows([row(win=[.5, .3, .2 - 5e-13, 5e-13])])[0]
    sample = d.to_topk_samples([d.native_raw_sample(r)])[0]
    order = sorted(r.ids)
    assert [order[i] for i in (sample.i1, sample.i2, sample.i3)] == ['z', 'b', 'a']
    assert sample.win[order.index('c')] > 9.9e-10
    assert d.c132_sample(r).win[-1] == 5e-13


def test_incomplete_input_is_isolated_from_complete_control():
    records = d.decode_rows([row(incomplete=True), row(2021)])
    predictions, fits, calls = d.controlled_annual(records, stored_fits(records))
    audit = fits[-1]
    assert audit['n_prior_complete'] == 0 and audit['n_prior_races'] == 1
    assert audit['controlled_fits']['native131_complete_annual']['n_races_l3'] == 0
    assert audit['controlled_fits']['native131_available_annual']['n_races_l3'] == 1
    assert len(audit['incomplete_prior_ids']) == 1
    assert all(np.array_equal(p[records[1].race_id][:, 0], records[1].heads[:, 0]) for p in predictions.values())


def test_current_year_labels_never_enter_that_year_fit():
    records = d.decode_rows([row(), row(2021)])
    expected, fits, calls = d.controlled_annual(records, stored_fits(records))
    changed = row(2021)
    changed['labels'] = [('a', 1, 1, 1), ('b', 0, 1, 1), ('z', 0, 0, 1)]
    mutant = d.decode_rows([row(), changed])
    actual, altered, _ = d.controlled_annual(mutant, stored_fits(records))
    assert fits == altered
    for arm in expected:
        assert np.array_equal(expected[arm][records[1].race_id], actual[arm][records[1].race_id])


def test_wrong_stored_c_sample_sha_or_future_boundary_rejected():
    records = d.decode_rows([row(), row(2021)])
    fits = stored_fits(records)
    fits[-1]['sample_values_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='identity or boundary'):
        d.controlled_annual(records, fits)
    fits = stored_fits(records)
    fits[-1]['fit_through'] = '2021-01-01'
    with pytest.raises(ValueError, match='identity or boundary'):
        d.controlled_annual(records, fits)


def test_snapshot_cutoff_duplicate_ids_and_missing_result_coverage_rejected():
    forbidden = row(2026)
    forbidden['race_date'] = '2026-09-07'  # synthetic only
    with pytest.raises(ValueError, match='date or population'):
        d.decode_rows([forbidden])
    duplicate = row()
    duplicate['ids'] = ('z', 'z', 'a', 'c')
    with pytest.raises(ValueError, match='date or population'):
        d.decode_rows([duplicate])
    missing = row()
    missing['n_result_rows'] = None
    with pytest.raises(ValueError, match='date or population'):
        d.decode_rows([missing])


def test_digest_rejects_before_unpickle(tmp_path, monkeypatch):
    p = tmp_path / 'fake.pkl'
    p.write_bytes(b'not a pickle')
    monkeypatch.setattr(d.pickle, 'load', lambda *a: pytest.fail('Unpickle must not run'))
    with pytest.raises(ValueError, match='SHA mismatch before pickle'):
        d.checked_load(p, '0' * 64)


def test_forbidden_summary_metadata_stops_before_binary_read(tmp_path, monkeypatch):
    p = tmp_path / 'summary.json'
    p.write_text(json.dumps({'provenance': {'actual_data_through': '2026-09-07',
        'allowed_data_through': '2026-09-06', 'snapshot_sha256': d.common.SNAPSHOT_SHA256}}))
    monkeypatch.setattr(d, 'SOURCE', tmp_path)
    monkeypatch.setattr(d, 'SUMMARY_SHA', d.common.digest(p))
    monkeypatch.setattr(d.pickle, 'load', lambda *a: pytest.fail('Binary read forbidden'))
    with pytest.raises(ValueError, match='metadata cutoff'):
        d.source_summary()


def test_all_runtime_and_booster_calls_are_guarded():
    from horseracing_probability import model_calibration
    from horseracing_training.win_model import WinModel
    from sqlalchemy.orm import Session
    with d.guarded_execution():
        for call in (lambda: model_calibration.load_topk_samples(None, date_from=None, date_to=None),
                     lambda: model_calibration.fit_product_stage_discount(None, before_date=None),
                     lambda: WinModel.fit(None), lambda: Session.execute(None, None)):
            with pytest.raises(RuntimeError, match='prohibits'):
                call()


def test_under_sampled_2020_fallback_matches_plain_display():
    record = d.decode_rows([row()])[0]
    predictions, fits, calls = d.controlled_annual([record], stored_fits([record]))
    assert fits[0]['fit_through'] is None
    for p in predictions.values():
        assert np.allclose(p[record.race_id], record.heads, atol=1e-14, rtol=0)
