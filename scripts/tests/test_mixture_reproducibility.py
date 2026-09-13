from __future__ import annotations

from dataclasses import replace
import datetime as dt
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mixture_reproducibility as d
from horseracing_eval.dataset import EvalRace, ScoringLabel
from horseracing_eval.predictor import HorseEntry, Prediction, RaceContext
from horseracing_training.dataset import TrainingMatrix
from horseracing_training.predictor import LightGBMPredictor


def races(n=16):
    out = []
    for i in range(n):
        c = RaceContext(f'20240501{i + 1:02d}01', dt.date(2024, 1, 1) + dt.timedelta(days=i),
                        (HorseEntry('A'), HorseEntry('B')))
        out.append(EvalRace(c, (ScoringLabel('A', 1, 1, 1), ScoringLabel('B', 0, 1, 1)), 2))
    return out


def records(n=4):
    out = []
    for er in races(n):
        c = er.context
        corr = pd.DataFrame({'race_id': [c.race_id] * 2, 'horse_id': ['A', 'B'],
                             'race_date': [c.race_date] * 2,
                             **{k: [0., 0.] for k in d.common.mc.INPUT_TERMS}})
        heads = d.common.heads_from_win([.8, .2])
        out.append(d.common.MixedRace(er, ('A', 'B'), np.stack([heads] * 6), heads, corr))
    return out


def test_recipe_all_seeds_keep_native126_effective_parameters():
    for seed in d.SEEDS:
        r = d.recipe(seed)
        assert r.seed == seed and r.objective == 'pl_topk' and r.calib_frac == 0.
        assert r.resolved_params()['n_estimators'] == 900 and r.resolved_params()['colsample_bytree'] == .7
        assert r.weight_mask_rate == .5 and r.weight_mask_seed == 20260810
        assert r.te_smoothing == 10 and d.recipe_meta(seed)['n_oof_blocks'] == 8
        assert replace(r, seed=42) == d.recipe(42)


def test_partition_proves_eight_boosters_and_strict_prior():
    proof = d.partition_proof(races())
    assert proof['blocks'] == 8 and proof['booster_fits'] == 8
    assert len(proof['partitions']) == 7  # Native partition iterator omits the initial fit-only block.
    assert all(p['train_through'] < p['valid_from'] for p in proof['partitions'])
    assert d.partition_proof(races(), 2)['booster_fits'] == 2


def test_candidate_seed_and_matrix_reach_final_and_every_inner_booster(monkeypatch):
    rs = races(); seen = []
    frame = pd.DataFrame([{'race_id': r.context.race_id, 'horse_id': h, 'race_date': r.context.race_date,
                           'win': int(h == 'A'), 'marker': .7} for r in rs for h in ('A', 'B')])
    matrix = TrainingMatrix(frame, ['marker'], [])
    def fit(self, train):
        seen.append((self.seed, self.params['colsample_bytree'], self._data,
                     max(r.race_date for r in train)))
    monkeypatch.setattr(LightGBMPredictor, 'fit', fit)
    monkeypatch.setattr(LightGBMPredictor, 'raw_win_probs', lambda self, c: (['A', 'B'], np.array([.8, .2])))
    p = d.make_predictor(matrix, 43)
    # Exercise real final+all inner construction without fitting a real booster or calibrator.
    monkeypatch.setattr(p, '_fit_oof_isotonic', lambda rs: p._oof_isotonic_rows(rs))
    with d.frozen_outcomes(rs): p.fit([r.context for r in rs], num_threads=1)
    assert len(seen) == 8 and all(s == 43 and c == .7 for s, c, _, _ in seen)
    assert all(m.frame is frame and m.feature_cols == ['marker'] for _, _, m, _ in seen)
    assert all(day < rs[-1].context.race_date for _, _, _, day in seen[1:])


def test_oof_unknown_or_nonnull_session_fails_closed():
    import horseracing_training.calib_split as cs
    with d.frozen_outcomes(races()):
        with pytest.raises(ValueError, match='outside'): cs._started_all_outcomes(None, ['missing'])
        with pytest.raises(ValueError, match='outside'): cs._started_all_outcomes(object(), [])


def test_native_prediction_objects_are_handled_without_order_coercion():
    p = {'A': Prediction(.8, 1., 1.), 'B': Prediction(.2, 1., 1.)}
    assert np.array_equal(d.prediction_array(p, ['A', 'B']), [[.8, 1, 1], [.2, 1, 1]])
    with pytest.raises(ValueError, match='order'): d.prediction_array(p, ['B', 'A'])


def test_candidate_centered_logp_and_same_lambda_preserve_mixed_win():
    r = records(1)[0]; raw = d.common.heads_from_win([.6, .4]); beta = [0, 0, 0, 0, .2]
    baseline, candidate, mixed = d.construct(r, raw, beta, {'lambda2': .85, 'lambda3': .7})
    q = raw[:, 0] ** 1.2; q /= q.sum()
    assert np.allclose(mixed[:, 0], (1 - 1 / 7) * r.heads[:, 0] + q / 7, rtol=0, atol=1e-15)
    assert np.array_equal(baseline[:, 0], r.heads[:, 0]) and np.array_equal(candidate[:, 0], mixed[:, 0])


def test_seed_aggregate_averages_losses_not_probabilities_and_keeps_distinct_n():
    rs = records(); base = {r.race_id: r.heads for r in rs}
    cand = [{r.race_id: d.common.heads_from_win([p, 1 - p]) for r in rs} for p in (.7, .8, .9)]
    got = d.seed_mean_metrics(rs, base, cand, b=40, seed=7, alpha=.05)['winner_nll']
    want = float(np.mean([np.log(.8 / p) for p in (.7, .8, .9)]))
    assert got['point'] == pytest.approx(want) and got['point'] > 0
    assert got['n_races'] == 4 and got['n_rows'] == 4 and got['n_days'] == 4
    assert got['ci'] == pytest.approx([want, want])
    assert got['seed_sd'] == pytest.approx(np.std([np.log(.8 / p) for p in (.7, .8, .9)], ddof=1))


@pytest.mark.parametrize(('ci', 'expected'), [([-.001, .0004], 'SUPPORTED'),
    ([.0006, .001], 'WORSENING_SUPPORTED'), ([-.001, .001], 'UNRESOLVED')])
def test_noninferiority_has_three_values(ci, expected):
    assert d.noninferiority({'ci': ci}, .0005) == expected


def test_stage_lambda_rejects_same_year_training():
    row = {'year': 2024, 'fit_through': '2024-01-01', 'fallback': False, 'lambda2': .8, 'lambda3': .7}
    report = {'regimes': {r: {'annual_stage_fits': [row]} for r in d.REGIMES}}
    with pytest.raises(ValueError, match='prior-year'): d.stage_lambdas(report)


def test_model_sha_is_verified_before_unpickling(tmp_path, monkeypatch):
    for name in d.MODEL_FILES: (tmp_path / name).write_text('synthetic')
    monkeypatch.setattr(d, 'load', lambda p: pytest.fail('No unpickle before SHA validation'))
    with pytest.raises(ValueError, match='SHA'): d.load_model(tmp_path, {n: 'wrong' for n in d.MODEL_FILES})


def test_encoded_entry_uses_no_encoders_no_coercion_and_exact_order():
    calls = []
    class Model:
        def predict(self, X, **kw): calls.append(X.copy()); return np.array([.8, .2])
    class Encoder:
        def transform(self, value): pytest.fail('Already-encoded input must not transform TE again')
    calib = SimpleNamespace(transform=lambda x: x)
    model = d.ResearchModel(Model(), calib, {'jockey_id': Encoder()},
                           {'feature_columns': ['jockey_id', 'venue'], 'categorical_columns': ['venue']})
    X = pd.DataFrame({'jockey_id': [.08, .09], 'venue': pd.Categorical(['A', 'B'])}, index=['A', 'B'])
    original = X.copy(deep=True)
    p = model.predict_encoded(races(1)[0].context, X)
    pd.testing.assert_frame_equal(X, original, check_exact=True)
    assert np.array_equal(p[:, 0], [.8, .2]) and len(calls) == 1
    with pytest.raises(ValueError, match='order'): model.predict_encoded(races(1)[0].context, X.iloc[::-1])
    with pytest.raises(ValueError, match='coercion'):
        model.predict_encoded(races(1)[0].context, X.assign(venue=['A', 'B']))
    with pytest.raises(ValueError, match='Infinite'):
        model.predict_encoded(races(1)[0].context, X.assign(jockey_id=[np.inf, .2]))
    model.model.predict = lambda *a, **kw: np.array([np.nan, .2])
    with pytest.raises(ValueError, match='Raw model output'):
        model.predict_encoded(races(1)[0].context, X)
    forbidden = replace(races(1)[0].context, race_date=dt.date(2026, 9, 7))  # Synthetic boundary fixture.
    with pytest.raises(ValueError, match='date'): model.predict_encoded(forbidden, X)


def test_missing_cache_read_never_fits(tmp_path, monkeypatch):
    monkeypatch.setattr(d, 'WORK', tmp_path)
    monkeypatch.setattr(d, 'fit', lambda *a, **kw: pytest.fail('Reader must not train'))
    with pytest.raises(FileNotFoundError): d.read_job(42, 2024, {})


def test_step_gate_requires_receipt_before_any_training(tmp_path, monkeypatch):
    monkeypatch.setattr(d, 'WORK', tmp_path / 'repro')
    with pytest.raises(FileNotFoundError): d.step_gate()


def test_step_gate_allows_only_exact_recovered_source_and_binds_independent_review(tmp_path, monkeypatch):
    monkeypatch.setattr(d, 'ROOT', tmp_path)
    monkeypatch.setattr(d, 'WORK', tmp_path / 'artifacts/repro')
    monkeypatch.setattr(d, 'SPEC', tmp_path / 'spec')
    area = d.WORK.parent / 'calibration'; area.mkdir(parents=True)
    (d.SPEC / 'evidence').mkdir(parents=True)
    for path in (d.SPEC / 'experiment.json', area / 'summary.json', area / 'design.json'):
        path.write_text('{}')
    original = tmp_path / 'scripts/calibration_serving_recheck.py'; original.parent.mkdir()
    original.write_text('external replacement; never execute')
    recovered = area / 'execution-source.py'; recovered.write_text('verified executed source')
    receipt = {'status': 'STEP1_COMPLETE', 'can_adopt': False, 'eligible_for_verdict': False,
        'additional_booster_fits': 0, 'experiment_sha256': d.digest(d.SPEC / 'experiment.json'),
        'summary_sha256': d.digest(area / 'summary.json'), 'design_sha256': d.digest(area / 'design.json'),
        'source_sha256': {str(original): d.digest(recovered)}, 'input_sha256': {}}
    d.write_json(area / 'receipt.json', receipt)
    review = {k: receipt[k] for k in ('can_adopt', 'eligible_for_verdict', 'additional_booster_fits',
                                    'experiment_sha256', 'summary_sha256', 'design_sha256')}
    review.update(status='PASS', original_source_recovery_sha256=d.digest(recovered))
    d.write_json(d.SPEC / 'evidence/calibration-independent-review.json', review)
    assert d.step_gate()['step1_independent_review_sha256'] == d.digest(d.SPEC / 'evidence/calibration-independent-review.json')
    recovered.write_text('changed')
    with pytest.raises(ValueError, match='recorded'): d.step_gate()


def test_prepare_rejects_forbidden_metadata_before_native_verify(tmp_path, monkeypatch):
    (tmp_path / 'snapshot.json').write_text(json.dumps({'data_through': '2026-09-07', 'snapshot_sha256': 'fake'}))
    monkeypatch.setattr(d, 'SOURCE111', tmp_path)
    monkeypatch.setattr(d, 'WORK', tmp_path / 'output')
    monkeypatch.setattr(d, 'config', lambda: {})
    monkeypatch.setattr(d, 'source_hash', lambda: 'source')
    monkeypatch.setattr(d, 'runtime', lambda: {})
    monkeypatch.setattr(d.native, 'verify', lambda: pytest.fail('Forbidden data must stop before other sources'))
    with pytest.raises(ValueError, match='metadata'): d.prepare()
