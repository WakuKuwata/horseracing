from __future__ import annotations

import datetime as dt
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gap_log_quality as g
from horseracing_eval.dataset import EvalRace, ScoringLabel
from horseracing_eval.decision import assert_verdict_eligible, VerdictSourceError
from horseracing_eval.predictor import HorseEntry, RaceContext
from horseracing_eval.splits import expanding_folds


def reports(a='RETAIN_UNCERTAIN', b='RETAIN_SUPPORTED'):
    return {k: {'research_disposition': {'state': v}} for k, v in [('increment', a), ('anchor', b)]}


@pytest.mark.parametrize('a', ['RETAIN_UNCERTAIN', 'RETAIN_SUPPORTED', 'DEFER', 'BLOCKED'])
@pytest.mark.parametrize('b', ['RETAIN_UNCERTAIN', 'RETAIN_SUPPORTED', 'DEFER', 'BLOCKED'])
def test_prespecified_selection(a, b):
    assert g.select_arm(reports(a, b)) == ('stack' if a in g.RETAINED and b in g.RETAINED else 'pruning')


def test_selection_requires_both_comparisons():
    with pytest.raises(ValueError):
        g.select_arm({'increment': reports()['increment']})


def race_lookup(n=3, year=2020, gaps=None):
    context = RaceContext('r', dt.date(year, 1, 2), tuple(HorseEntry(str(i)) for i in range(n)))
    vals = gaps if gaps is not None else [float(i) for i in range(n)]
    lookup = {('r', str(i)): (context.race_date, v) for i, v in enumerate(vals)}
    return context, lookup


def test_zero_gamma_does_not_reclip_small_win_probability():
    context, lookup = race_lookup()
    base = g.assemble_predictions(['0', '1', '2'], [1e-8, .2, .79999999], eps=0.)
    pred, audit = g.tilt_predictions(base, context, lookup, 0.)
    np.testing.assert_allclose([p.win for p in pred.values()], [p.win for p in base.values()], atol=1e-12, rtol=0)
    assert audit['below_legacy_clip_horses'] == 1
    assert audit['max_post_assembly_win_change'] < 1e-12
    for key in base:
        np.testing.assert_allclose([pred[key].win, pred[key].top2, pred[key].top3], [base[key].win, base[key].top2, base[key].top3], atol=1e-12, rtol=0)


def test_tilt_uses_context_order_and_missing_offset():
    context, lookup = race_lookup(gaps=[np.nan, 1., 2.])
    base = g.assemble_predictions(['2', '0', '1'], [.5, .2, .3], eps=0.)
    pred, audit = g.tilt_predictions(base, context, lookup, .2)
    expected = np.array([.2, .3, .5]) * np.exp(np.array([0., 1., 2.]) * .2)
    expected /= expected.sum()
    np.testing.assert_allclose([pred[str(i)].win for i in range(3)], expected, atol=1e-12)
    assert pred['0'].win != base['0'].win
    x = np.array([[p.win, p.top2, p.top3] for p in pred.values()])
    np.testing.assert_allclose(x.sum(axis=0), [1, 2, 3], atol=1e-8)


@pytest.mark.parametrize('gamma', [np.nan, np.inf, -np.inf, 1e9])
def test_bad_gamma_or_endpoint_fails_closed(gamma):
    context, lookup = race_lookup()
    base = g.assemble_predictions(['0', '1', '2'], [.2, .3, .5], eps=0.)
    with pytest.raises(ValueError):
        g.tilt_predictions(base, context, lookup, gamma)


def test_single_horse_identity():
    context, lookup = race_lookup(1)
    base = g.assemble_predictions(['0'], [1.], eps=0.)
    pred, _ = g.tilt_predictions(base, context, lookup, .7)
    assert pred == base


def test_wrong_date_or_missing_horse_rejected():
    context, lookup = race_lookup()
    lookup[('r', '0')] = (dt.date(2019, 1, 1), 2.)
    with pytest.raises(ValueError, match='date'):
        g.gap_values(context, lookup)
    lookup.pop(('r', '0'))
    with pytest.raises(ValueError, match='population'):
        g.gap_values(context, lookup)


def test_wrong_year_predictor_rejected():
    context, lookup = race_lookup()
    pred = g.TiltPredictor(None, lookup, 2021, .1, {})
    with pytest.raises(ValueError, match='wrong year'):
        pred.predict_race(context)


def test_frozen_coefficients_strict_prior_year_and_warmup():
    races, lookup, predictions, calls = [], {}, {}, []
    for year in range(2018, 2027):
        for d in range(1, 5):
            ctx = RaceContext(f'{year}-{d}', dt.date(year, 1, d), (HorseEntry('a'), HorseEntry('b')))
            labels = (ScoringLabel('a', int(d < 4), 1, 1), ScoringLabel('b', int(d == 4), 1, 1))
            races.append(EvalRace(ctx, labels, 2))
            lookup[(ctx.race_id, 'a')] = (ctx.race_date, 1.)
            lookup[(ctx.race_id, 'b')] = (ctx.race_date, 0.)
            predictions[ctx.race_id] = g.assemble_predictions(['a', 'b'], [.5, .5], eps=0.)
    class Factory:
        def fit(self, train, **kw):
            calls.append(max(r.race_date.year for r in train))
            return SimpleNamespace(predict_race=lambda r: predictions[r.race_id])
    fs = {f.valid_year: f for f in expanding_folds(races, 2019)}
    coeff = g.fit_coefficients(Factory(), fs, lookup)
    assert calls == list(range(2018, 2026))
    assert coeff['warmup_eligible_races'] == 4
    assert coeff['evaluated_eligible_races'] == 28
    assert set(coeff['gammas']) == {str(y) for y in range(2020, 2027)}
    assert all(r['fit_last_day'] < r['eval_first_day'] for r in coeff['fit_diagnostics'])
    assert coeff['fit_diagnostics'][0]['fit_races'] == 4
    with pytest.raises(VerdictSourceError):
        assert_verdict_eligible(coeff)


def test_wrapper_preserves_original_train_population():
    contexts = [RaceContext('old', dt.date(2007, 1, 1), ()), RaceContext('recent', dt.date(2019, 1, 1), ())]
    calls = []
    base = SimpleNamespace(expected_columns=['x'], recipe_meta={'kind':'fixture'}, fit=lambda train, **kw: calls.append((train, kw)) or None)
    wrapper = g.TiltFactory(base, {}, {'gammas': {'2020': .1}})
    pred = wrapper.fit(contexts, num_threads=4)
    assert calls[0][0] is contexts
    assert calls[0][1] == {'num_threads': 1}
    assert pred.year == 2020 and pred.gamma == .1


def test_unknown_result_or_state_cannot_be_production_verdict():
    with pytest.raises(VerdictSourceError):
        assert_verdict_eligible({'artifact_kind': 'gap_log_quality_research_report', 'eligible_for_verdict': False, 'can_adopt': False})


def test_registered_config():
    cfg = g.load_config()
    assert cfg['correction']['assembly_eps'] == 0.
    assert cfg['gamma_fit']['first_year'] == 2019
    assert cfg['seed_noise']['sd_fold'] == .001816
    assert cfg['eval_window']['from'] == '2020-01-01'


def test_stored_output_tamper_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(g, 'WORK', tmp_path)
    result = tmp_path / 'result.json'
    result.write_text('{}')
    (tmp_path / 'increment-receipt.json').write_text('{}')
    (tmp_path / 'increment-evidence.json').write_text('{}')
    (tmp_path / 'run-freeze.json').write_text('{}')
    with pytest.raises(ValueError, match='receipt changed'):
        g.verified_output(result, {'id': 'increment', 'baseline': 'selected'}, {'sources': {'selected_arm': 'stack'}})


@pytest.mark.parametrize('a,b,expected', [('RETAIN_UNCERTAIN','RETAIN_SUPPORTED','CORRECTION_RETAINED'), ('DEFER','RETAIN_SUPPORTED','CORRECTION_DEFERRED_BASE_RETAINED'), ('RETAIN_SUPPORTED','BLOCKED','CORRECTION_DEFERRED_BASE_RETAINED')])
def test_correction_requires_both_quality_comparisons(a,b,expected):
    assert g.research_progression(reports(a,b)) == expected


def test_summary_rejects_selection_reversal(tmp_path, monkeypatch):
    monkeypatch.setattr(g.s, 'WORK', tmp_path)
    monkeypatch.setattr(g.s, 'SPEC', tmp_path)
    (tmp_path / 'run-freeze.json').write_text('{}')
    (tmp_path / 'evidence').mkdir()
    (tmp_path / 'evidence/summarize.py').write_text('# frozen')
    cfg = {'test': 'config'}
    summary = {'artifact_kind': 'small_gain_stack_summary', 'can_adopt':False, 'eligible_for_verdict':False,
               'research_decision':'STACK_DEFERRED_PRUNING_RETAINED',
               'config_hash':g.s.p.gate_config_hash(cfg), 'run_freeze_sha256':g.s.p.digest(tmp_path/'run-freeze.json'),
               'summarizer_sha256':g.s.p.digest(tmp_path/'evidence/summarize.py')}
    with pytest.raises(ValueError, match='selection mismatch'):
        g.verify_summary(summary, reports(), cfg, {'jobs': []})


def test_actual_paired_eval_accepts_correction_factory_and_distinct_recipe():
    from copy import deepcopy
    races, lookup = [], {}
    for year in range(2019, 2027):
        for day in range(1, 4):
            ctx = RaceContext(f'{year}{day:04}', dt.date(year, 1, day), tuple(HorseEntry(str(i)) for i in range(3)))
            winner = str(day - 1)
            labs = tuple(ScoringLabel(str(i), int(str(i) == winner), int(i < 2), 1) for i in range(3))
            races.append(EvalRace(ctx, labs, 3))
            lookup.update({(ctx.race_id, str(i)): (ctx.race_date, float(i)) for i in range(3)})
    class Base:
        expected_columns = ['fixture']
        recipe_meta = {'kind': 'fixed_test_base'}
        recipe_hash = g.s.p.stable_hash(recipe_meta)
        def fit(self, train, **kw):
            return SimpleNamespace(is_leaky_reference=False, predict_race=lambda ctx: g.assemble_predictions([h.horse_id for h in ctx.started_horses], [.2, .3, .5], eps=0.))
    base = Base()
    coeff = {'gammas': {str(y): .02 for y in range(2020, 2027)}}
    cand = g.TiltFactory(base, lookup, coeff)
    assert cand.recipe_hash != base.recipe_hash
    assert cand.recipe_meta['correction']['assembly_eps'] == 0.
    cfg = deepcopy(g.load_config())
    cfg['bootstrap']['b'] = 20
    cfg['eval_window']['min_eval_days'] = 1
    report = g.s.p.paired_eval(cand, base, races, gate_config=cfg, first_valid_year=2020,
        valid_from=dt.date(2020, 1, 1), subgroups=True, num_threads=1)
    assert report.n_races == 21 and report.n_eligible == 21
    assert report.candidate_recipe_meta == cand.recipe_meta
    assert report.candidate_recipe_hash == cand.recipe_hash
    assert report.active_recipe_hash == base.recipe_hash
    assert len(cand.audit) == 21


def test_coefficient_resume_checks_saved_population(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(g, 'WORK', tmp_path)
    (tmp_path/'run-freeze.json').write_text('{}')
    coefficient = {'artifact_kind':'gap_log_coefficients','can_adopt':False,'eligible_for_verdict':False,
                   'gammas':{str(y):.1 for y in range(2020,2027)},'warmup_eligible_races':4,'evaluated_eligible_races':28}
    path=tmp_path/'coefficients.json'
    path.write_text(json.dumps(coefficient))
    (tmp_path/'coefficients-receipt.json').write_text(json.dumps({'sha256':g.s.p.digest(path),'freeze_sha256':g.s.p.digest(tmp_path/'run-freeze.json')}))
    frozen={'population':{str(y):{'eligible_races':4} for y in range(2019,2027)}}
    assert g.verified_coefficients(frozen)==coefficient
    frozen['population']['2019']['eligible_races']=5
    with pytest.raises(ValueError,match='scope/population'):
        g.verified_coefficients(frozen)


@pytest.mark.parametrize('damage', ['coefficient_tamper','missing_receipt','missing_coefficients'])
def test_complete_output_resume_revalidates_coefficients(tmp_path, monkeypatch, damage):
    import json
    monkeypatch.setattr(g,'WORK',tmp_path)
    monkeypatch.setattr(g.s.research,'assess_research',lambda r:{'state':'RETAIN_UNCERTAIN'})
    def write(name,value):
        p=tmp_path/name;p.write_text(json.dumps(value));return p
    freeze_path=write('run-freeze.json',{})
    freeze_hash=g.s.p.digest(freeze_path)
    evidence=write('increment-evidence.json',{})
    coeff=write('coefficients.json',{'artifact_kind':'gap_log_coefficients','can_adopt':False,'eligible_for_verdict':False,
                'gammas':{str(y):.1 for y in range(2020,2027)},'warmup_eligible_races':4,'evaluated_eligible_races':28})
    cr=write('coefficients-receipt.json',{'sha256':g.s.p.digest(coeff),'freeze_sha256':freeze_hash})
    contrast={'id':'increment','baseline':'selected'}
    frozen={'config_hash':'cfg','sources':{'selected_arm':'stack'},'population':{str(y):{'eligible_races':4} for y in range(2019,2027)}}
    report=write('result.json',{'artifact_kind':'gap_log_quality_research_report','can_adopt':False,'eligible_for_verdict':False,
                 'contrast':contrast,'selected_arm':'stack','study_config_hash':'cfg','run_freeze_sha256':freeze_hash,
                 'evidence_path':str(evidence),'evidence_sha256':g.s.p.digest(evidence),'coefficient_sha256':g.s.p.digest(coeff),
                 'research_disposition':{'state':'RETAIN_UNCERTAIN'}})
    write('increment-receipt.json',{'report_sha256':g.s.p.digest(report),'evidence_sha256':g.s.p.digest(evidence),'freeze_sha256':freeze_hash})
    assert g.verified_output(report,contrast,frozen)
    if damage=='coefficient_tamper':coeff.write_text('{}')
    elif damage=='missing_receipt':cr.unlink()
    else:coeff.unlink()
    with pytest.raises((ValueError,FileNotFoundError)):
        g.verified_output(report,contrast,frozen)
