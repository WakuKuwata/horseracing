"""119 vector shape, component missingness, identification, chronology and provenance."""
from copy import deepcopy
import datetime as dt
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import season_gap_recheck as r
from horseracing_eval.predictor import HorseEntry, RaceContext


@pytest.fixture
def cfg():
    return r.load_config()


def context(year=2020):
    return RaceContext('race', dt.date(year, 7, 1), tuple(HorseEntry(f'h{i}') for i in range(3)))


def predictions():
    return r.g.assemble_predictions(['h0', 'h1', 'h2'], np.array([.2, .3, .5]), eps=0.)


def lookup(h, ctx=None):
    ctx = ctx or context()
    return {(ctx.race_id, horse.horse_id): (ctx.race_date, tuple(values))
            for horse, values in zip(ctx.started_horses, h, strict=True)}


def probabilities(pred):
    return np.array([[p.win, p.top2, p.top3] for p in pred.values()])


def test_candidate_matrix_uses_gap_and_registered_leap_safe_season():
    frame = pd.DataFrame({'race_id': ['r'] * 3, 'horse_id': ['a', 'b', 'c'],
        'race_date': [dt.date(2020, 3, 1)] * 3, 'days_since_last': [10., 20., np.nan], 'sex': ['牝', '牡', None]})
    h = r.candidate_matrix(frame)
    theta = 2 * np.pi * 60 / 366
    assert h.shape == (3, 3)
    np.testing.assert_allclose(h[0], [np.log1p(10), np.sin(theta), np.cos(theta)], atol=1e-15)
    np.testing.assert_array_equal(h[1, 1:], [0., 0.])
    assert np.isnan(h[2]).all()


@pytest.mark.parametrize('values,expected', [(['牡', 'セ'], 'no_female'), (['牝', '牡'], 'mixed'), (['牝', '牝'], 'all_female'), (['牝', None], 'missing_sex')])
def test_fixed_sex_groups(values, expected):
    assert r.sex_group(values) == expected


@pytest.mark.parametrize('bad', [[], ['unknown']])
def test_invalid_sex_groups_rejected(bad):
    with pytest.raises(ValueError):
        r.sex_group(bad)


@pytest.mark.parametrize('female', [True, False])
def test_seasonal_terms_cancel_in_homogeneous_known_sex_with_same_gap_coefficient(female):
    h = np.array([[1., .4, -.8], [2., .4, -.8], [3., .4, -.8]])
    if not female:
        h[:, 1:] = 0.
    l = lookup(h)
    base = predictions()
    q, _ = r.tilt_predictions(base, context(), l, [.17, 2., -3.])
    control, _ = r.tilt_predictions(base, context(), l, [.17, 0., 0.])
    np.testing.assert_allclose(probabilities(q), probabilities(control), atol=1e-12, rtol=0)


def test_mixed_field_season_can_change_prediction():
    h = [[1., .4, -.8], [2., 0., 0.], [3., 0., 0.]]
    q, _ = r.tilt_predictions(predictions(), context(), lookup(h), [.1, 1., 1.])
    control, _ = r.tilt_predictions(predictions(), context(), lookup(h), [.1, 0., 0.])
    assert not np.allclose(probabilities(q), probabilities(control), atol=1e-10)


def test_missing_component_only_has_zero_exponent_while_other_terms_remain():
    h = [[np.nan, .7, -.2], [2., np.nan, np.nan], [1., 0., 0.]]
    q, _ = r.tilt_predictions(predictions(), context(), lookup(h), [.2, .3, -.1])
    exponent = np.array([.7 * .3 + .02, 2 * .2, .2])
    expected = np.array([.2, .3, .5]) * np.exp(exponent)
    expected /= expected.sum()
    np.testing.assert_allclose([x.win for x in q.values()], expected, atol=1e-12, rtol=0)


def test_zero_gamma_identity_and_eps0_harville_consistency():
    q, audit = r.tilt_predictions(predictions(), context(), lookup([[1., 1., 0.]] * 3), [0., 0., 0.])
    np.testing.assert_allclose(probabilities(q), probabilities(predictions()), atol=1e-12, rtol=0)
    np.testing.assert_allclose(probabilities(q).sum(axis=0), [1., 2., 3.], atol=1e-12)
    assert audit['max_post_assembly_win_change'] == 0.


@pytest.mark.parametrize('bad', [1., [1.], [1., 2.], [[1., 2., 3.]], [1., 2., np.inf], [1., 2., np.nan], ['1', '2', '3'], [True, False, True]])
def test_scalar_or_bad_joint_gamma_rejected(bad):
    with pytest.raises(ValueError):
        r.gamma_array(bad)


def test_lookup_identity_and_infinity_rejected():
    l = lookup([[1., 0., 0.]] * 3)
    l[('race', 'h0')] = (dt.date(2021, 7, 1), (1., 0., 0.))
    with pytest.raises(ValueError, match='horse/date'):
        r.h_values(context(), l)
    with pytest.raises(ValueError, match='n-by3'):
        r.h_values(context(), lookup([[np.inf, 0., 0.]] * 3))


def test_extreme_gamma_fails_instead_of_probability_repair():
    with pytest.raises(ValueError, match='interior'):
        r.tilt_predictions(predictions(), context(), lookup([[1000., 0., 0.], [0., 0., 0.], [-1000., 0., 0.]]), [1000., 0., 0.])


def test_joint_factory_records_order_and_rejects_scalar_recipe():
    base = SimpleNamespace(expected_columns=['a'], recipe_meta={'seed': 42}, recipe_hash='base')
    c = {'coefficient_order': r.ORDER, 'gammas': {'2020': [0., 0., 0.]}}
    factory = r.JointFactory(base, {}, c)
    assert factory.recipe_meta['correction']['coefficient_order'] == r.ORDER
    assert factory.recipe_meta['correction']['assembly_eps'] == 0.
    with pytest.raises(ValueError, match='order'):
        r.JointFactory(base, {}, {'coefficient_order': ['gap_log'], 'gammas': {}})


def test_joint_predictor_refuses_wrong_year():
    p = r.JointPredictor(None, {}, 2020, [0., 0., 0.], {})
    with pytest.raises(ValueError, match='wrong calendar'):
        p.predict_race(context(2021))


def test_coefficient_fit_only_uses_strict_prior_years(monkeypatch):
    folds, l = {}, {}
    for year in range(2019, 2027):
        ctx = context(year)
        ctx = RaceContext(str(year), ctx.race_date, ctx.started_horses)
        folds[year] = SimpleNamespace(train=[SimpleNamespace(context=context(year - 1))], valid=[SimpleNamespace(context=ctx)])
        l.update(lookup([[1., .3, -.4], [2., 0., 0.], [3., 0., 0.]], ctx))
    monkeypatch.setattr(r, 'population_masks', lambda _: SimpleNamespace(eligible=True, started_horse_ids=['h0', 'h1', 'h2'], winner_horse_id='h2'))
    calls = []
    def fit(prior, **kwargs):
        calls.append(([x.day for x in prior], kwargs))
        return np.zeros(3)
    monkeypatch.setattr(r, 'fit_gamma', fit)
    monkeypatch.setattr(r.g.screen, 'fit_diagnostics', lambda blocks, gammas: [{'years': len(blocks), 'gammas': len(gammas)}])
    base = SimpleNamespace(recipe_hash='base', fit=lambda *args, **kwargs: SimpleNamespace(predict_race=lambda _: predictions()))
    c = r.fit_coefficients(base, folds, l, 43)
    assert c['coefficient_order'] == r.ORDER and c['training_seed'] == 43
    assert len(c['gammas']) == len(calls) == 7
    for year, (days, kwargs) in zip(range(2020, 2027), calls, strict=True):
        assert len(days) == year - 2019 and max(days) < f'{year}-01-01'
        assert kwargs == {'k': 3, 'ridge': 1e-6, 'max_iter': 50, 'tol': 1e-9}


@pytest.mark.parametrize('field,value', [('new_fit_jobs', 1), ('selected_columns', 138), ('coefficient_order', ['female_sin', 'gap_log', 'female_cos'])])
def test_frozen_joint_scope_refuses_change(tmp_path, monkeypatch, cfg, field, value):
    cfg[field] = value
    (tmp_path / 'gate-config.json').write_text(json.dumps(cfg))
    (tmp_path / 'gate-config.hash.txt').write_text(r.s.p.gate_config_hash(cfg))
    monkeypatch.setattr(r, 'SPEC', tmp_path)
    with pytest.raises(ValueError, match='scope'):
        r.load_config()


def test_seed_config_preserves_single_seed_ci(cfg):
    changed = r.seed_config(cfg, 44)
    assert changed['arms']['seed'] == 44
    assert changed['seed_noise']['k_seeds'] == 1
    assert cfg['arms']['seed'] == 42


def test_source_hash_binds_summary(tmp_path, monkeypatch):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    summary = scripts / 'season_gap_summary.py'
    summary.write_text('original')
    monkeypatch.setattr(r, 'ROOT', tmp_path)
    monkeypatch.setattr(r.a, 'source_hash', lambda: 'source118')
    before = r.source_hash()
    summary.write_text('changed')
    assert r.source_hash() != before


def test_vector_new_and_scalar_retained_provenance_are_separate(tmp_path, monkeypatch):
    monkeypatch.setattr(r, 'WORK', tmp_path)
    new = tmp_path / 'seed-43/coefficients.json'
    r.write_json(new, {'base_recipe_hash': 'raw125'})
    old = tmp_path / 'old-gap.json'
    old.write_text('{}')
    monkeypatch.setattr(r.a, 'old_coefficient_path', lambda _: old)
    p = r.coefficient_provenance(43, {'id': 'retained'})
    assert p['candidate']['coefficient_order'] == r.ORDER
    assert p['baseline']['coefficient_order'] == ['gap_log']
    assert p['candidate']['path'] != p['baseline']['path']


def test_full_population_hash_rejects_noneligible_replacement(tmp_path, monkeypatch):
    old = tmp_path / 'old-report.json'
    old.write_text(json.dumps({'race_id_set_hash': 'original_full23030'}))
    monkeypatch.setattr(r.d, 'result_path', lambda *_: old)
    value = {'n_races': 23030, 'n_eligible': 22990, 'race_id_set_hash': 'original_full23030'}
    r.validate_full_population(value, 43)
    value['race_id_set_hash'] = 'same_eligible_but40_noneligible_changed'
    with pytest.raises(ValueError, match='including noneligible'):
        r.validate_full_population(value, 43)


def test_existing_newton_and_diagnostics_accept_joint_three_dimensions():
    h = np.array([[1., .2, .3], [3., 0., 0.]])
    prior = [r.RaceProbe('2019-01-01', np.array([.5, .5]), h, winner) for winner in (0, 1)]
    following = [r.RaceProbe('2020-01-01', np.array([.5, .5]), h, winner) for winner in (0, 1)]
    gamma = r.fit_gamma(prior, k=3, ridge=1e-6, max_iter=50, tol=1e-9)
    np.testing.assert_allclose(gamma, [0., 0., 0.], atol=1e-12)
    audit = r.g.screen.fit_diagnostics([prior, following], [gamma.tolist()])
    assert audit[0]['gradient_inf'] <= 1e-5 and audit[0]['regularized_fit_objective'] <= 1e-8


@pytest.mark.parametrize('changed', ['candidate', 'baseline'])
def test_completed_result_resume_revalidates_vector_and_old_scalar_coefficients(tmp_path, monkeypatch, cfg, changed):
    monkeypatch.setattr(r, 'WORK', tmp_path)
    monkeypatch.setattr(r, 'SPEC', tmp_path / 'spec')
    (tmp_path / 'run-freeze.json').write_text('{}')
    contrast = {'id': 'retained', 'baseline': 'retained'}
    evidence = r.seed_area(43) / 'retained-evidence.json'
    r.write_json(evidence, {'rows': []})
    provenance = {'candidate': {'sha256': 'joint3'}, 'baseline': {'sha256': 'oldscalar'}}
    value = {'artifact_kind': 'season_gap_research_report', 'can_adopt': False, 'eligible_for_verdict': False,
        'training_seed': 43, 'contrast': contrast, 'study_config_hash': r.s.p.gate_config_hash(cfg),
        'seed_config_hash': r.s.p.gate_config_hash(r.seed_config(cfg, 43)),
        'run_freeze_sha256': r.s.p.digest(tmp_path / 'run-freeze.json'),
        'coefficient_provenance': deepcopy(provenance), 'evidence_path': str(evidence), 'evidence_sha256': r.s.p.digest(evidence)}
    value['research_disposition'] = r.s.research.assess_research(value)
    out = r.result_path(43, 'retained')
    r.write_json(out, value)
    r.write_json(r.seed_area(43) / 'retained-receipt.json', {'report_sha256': r.s.p.digest(out),
        'evidence_sha256': r.s.p.digest(evidence), 'freeze_sha256': r.s.p.digest(tmp_path / 'run-freeze.json'), 'seed': 43})
    calls = []
    monkeypatch.setattr(r, 'coefficient_provenance', lambda *_: provenance)
    monkeypatch.setattr(r, 'verified_coefficients', lambda *_: calls.append('joint'))
    monkeypatch.setattr(r.a, 'old_coefficients', lambda *_: calls.append('scalar'))
    monkeypatch.setattr(r, 'validate_rows', lambda *_: None)
    monkeypatch.setattr(r, 'validate_full_population', lambda *_: None)
    assert r.verified_result(43, contrast, cfg, {})
    assert calls == ['joint', 'scalar']
    provenance[changed]['sha256'] = 'changed_coefficient'
    with pytest.raises(ValueError, match='coefficients changed'):
        r.verified_result(43, contrast, cfg, {})


@pytest.mark.parametrize('change', ['none', 'order', 'scalar', 'missing_year'])
def test_coefficient_receipt_requires_registered_seven_three_component_vectors(tmp_path, monkeypatch, cfg, change):
    monkeypatch.setattr(r, 'WORK', tmp_path)
    (tmp_path / 'run-freeze.json').write_text('{}')
    recipe = r.s.p.CalibSplitFactory(None, r.s.p.make_recipe(r.seed_config(cfg, 43), r.s.arm(r.s.load_config(), 'pruning')['drop_features']),
                                    n_oof_blocks=8, method='isotonic', require_sufficient=True)
    value = {'artifact_kind': 'season_gap_joint_coefficients', 'training_seed': 43, 'can_adopt': False,
        'eligible_for_verdict': False, 'base_arm': 'pruning125_raw', 'base_recipe_hash': recipe.recipe_hash,
        'coefficient_order': r.ORDER.copy(), 'gammas': {str(y): [0., 0., 0.] for y in range(2020, 2027)},
        'warmup_eligible_races': 1, 'evaluated_eligible_races': 7}
    if change == 'order':
        value['coefficient_order'].reverse()
    elif change == 'scalar':
        value['gammas']['2020'] = 0.
    elif change == 'missing_year':
        del value['gammas']['2020']
    path = r.seed_area(43) / 'coefficients.json'
    r.write_json(path, value)
    r.write_json(path.with_name('coefficients-receipt.json'), {'sha256': r.s.p.digest(path),
        'freeze_sha256': r.s.p.digest(tmp_path / 'run-freeze.json'), 'seed': 43, 'coefficient_order': r.ORDER})
    frozen = {'sources': {'population': {str(y): {'eligible_races': 1} for y in range(2019, 2027)}}}
    if change == 'none':
        assert r.verified_coefficients(43, frozen) == value
    else:
        with pytest.raises(ValueError):
            r.verified_coefficients(43, frozen)
