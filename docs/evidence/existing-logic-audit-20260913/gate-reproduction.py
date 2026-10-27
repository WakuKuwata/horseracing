"""Read-only gate review; synthetic data, temporary artifacts, no DB or booster fits."""
import contextlib
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path('/Users/kuwatawaku/workspace/horseracing')
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'scripts/tests')]

import numpy as np
from horseracing_eval.dataset import EvalRace, ScoringLabel, population_masks
from horseracing_eval.paired import paired_eval
from horseracing_eval.predictor import HorseEntry, Prediction, RaceContext
from horseracing_features.registry import FEATURE_VERSION, model_input_features
from horseracing_training.adoption import AdoptionDecision, AdoptionGate, evaluate_promotion
from horseracing_training.artifacts import save_model_version
from horseracing_training.predictor import LightGBMPredictor


class Predictor:
    is_leaky_reference = False

    def __init__(self, p):
        self.p = p

    def predict_race(self, ctx):
        win = [self.p, (1 - self.p) / 2, (1 - self.p) / 2]
        return {h.horse_id: Prediction(win[i], [1., .9, .1][i], 1.)
                for i, h in enumerate(ctx.started_horses)}


class Factory:
    def __init__(self, p, tag):
        self.p = p
        self.recipe_meta = {'tag': tag}
        self.recipe_hash = tag

    def fit(self, races, *, num_threads=None):
        return Predictor(self.p)


def exploratory_promotes():
    races = []
    for y in (2020, 2021):
        for day in (1, 2):
            hs = tuple(HorseEntry(f'h{i}') for i in range(3))
            ctx = RaceContext(f'{y}0000000{day}', dt.date(y, 6, day), hs)
            labs = tuple(ScoringLabel(h.horse_id, int(i == 0), int(i <= 1), 1)
                         for i, h in enumerate(hs))
            races.append(EvalRace(ctx, labs, n_result_rows=3))
    rep = paired_eval(Factory(.99, 'candidate'), Factory(.8, 'active'), races,
                      first_valid_year=2020, bootstrap_b=100)
    promotion = evaluate_promotion(legacy=AdoptionDecision(True, {}), verdict=rep.to_dict())
    assert rep.decision == 'ADOPT' and promotion.promotable
    print('FINDING: exploratory report reaches ACTIVE:', json.dumps({
        'decision': rep.decision, 'contract': rep.evaluation_contract_version,
        'n_days': rep.bootstrap_ci['n_days'], 'seed_noise': rep.seed_noise,
        'subgroups': rep.subgroups, 'artifact_kind': rep.to_dict().get('artifact_kind'),
        'promotable': promotion.promotable,
    }))
    for name, verdict in {
        'explicit_ineligible_without_kind': {'evaluation_contract_version': 'v4',
            'decision': 'ADOPT', 'eligible_for_verdict': False, 'can_adopt': False},
        'unrelated_recipe': {'evaluation_contract_version': 'v4', 'decision': 'ADOPT',
            'decision_reason': {'subgroup_assurance': 'full'},
            'artifact_kind': 'full_walk_forward', 'eligible_for_verdict': True,
            'candidate_recipe_hash': 'unrelated-candidate',
            'active_recipe_hash': 'unrelated-old-active'},
    }.items():
        result = evaluate_promotion(legacy=AdoptionDecision(True, {}), verdict=verdict)
        assert result.promotable
        print('FINDING:', name, json.dumps(result.reasons))


class StubEval:
    valid_years = []

    def to_summary(self):
        return {'eval': {'overall': {}}}


class FakeSession:
    def __init__(self):
        self.rows = {'existing-active': SimpleNamespace(adoption_status='active')}
        self.calls = []

    def get(self, model, key):
        return self.rows.get(key)

    def execute(self, stmt):
        self.calls.append(type(stmt).__name__)
        params = stmt.compile().params
        self.rows[params['model_version']] = SimpleNamespace(adoption_status=params['adoption_status'])

    def commit(self):
        pass


def autoactivation_keeps_old_active():
    predictor = LightGBMPredictor(None)
    predictor.fit_info_ = {'feature_cols': model_input_features(), 'model_degenerate': True}
    session = FakeSession()
    with tempfile.TemporaryDirectory(prefix='gate-audit-model-') as temp:
        save_model_version(session, model_version='new-candidate', predictor=predictor,
            eval_result=StubEval(), decision=AdoptionDecision(True, {}),
            gate=AdoptionGate(.05), artifacts_root=temp, feature_version=FEATURE_VERSION,
            verdict={'evaluation_contract_version': 'v4', 'decision': 'ADOPT',
                     'decision_reason': {'subgroup_assurance': 'full'}})
    active = [key for key, row in session.rows.items() if row.adoption_status == 'active']
    assert active == ['existing-active', 'new-candidate']
    print('FINDING: automatic activation leaves both active:', active, 'SQL:', session.calls)


def independent_135_ratio_check():
    import mixture_reproducibility as m
    from test_accuracy_batch_common import record
    records = [record('a', dt.date(2024, 1, 1), 4), record('b', dt.date(2024, 1, 1), 8),
               record('c', dt.date(2024, 1, 2), 6, complete=False),
               record('d', dt.date(2024, 1, 2), 5),
               record('e', dt.date(2024, 1, 3), 10, dead_heat=True)]
    base = {r.race_id: r.heads for r in records}
    cands = [{r.race_id: m.common.heads_from_win([pw] + [(1 - pw) / (len(r.ids) - 1)] * (len(r.ids) - 1))
              for r, pw in zip(records, [.2 + .03 * s + .01 * j for j in range(len(records))])}
             for s in range(3)]
    got = m.seed_mean_metrics(records, base, cands, b=400, seed=9, alpha=.0125)
    max_error = 0
    for metric in ('winner_nll', 'top2_logloss', 'top3_logloss', 'top2_brier', 'top3_brier'):
        day_rows = {}
        for r in records:
            pop = population_masks(r.er)
            if ((metric == 'winner_nll' and not pop.eligible)
                    or (metric != 'winner_nll' and not pop.complete_results)):
                continue
            if metric == 'winner_nll':
                i = r.ids.index(pop.winner_horse_id)
                values = [np.mean([-np.log(p[r.race_id][i, 0]) for p in cands])
                          + np.log(base[r.race_id][i, 0])]
            else:
                j = 1 if metric.startswith('top2') else 2
                head = 'top2' if j == 1 else 'top3'
                y = np.array([getattr(pop, 'started_' + head)[h] for h in r.ids])

                def loss(p):
                    a = p[:, j]
                    if metric.endswith('brier'):
                        return (a - y) ** 2
                    clipped = np.clip(a, 1e-15, 1 - 1e-15)
                    return -(y * np.log(clipped) + (1 - y) * np.log1p(-clipped))

                values = np.mean([loss(p[r.race_id]) for p in cands], axis=0) - loss(base[r.race_id])
            day_rows.setdefault(str(r.race_date), []).extend(values)
        vals = [day_rows[d] for d in sorted(day_rows)]
        rng = np.random.default_rng(9)
        reps = [np.mean([v for i in rng.integers(0, len(vals), size=len(vals)) for v in vals[i]])
                for _ in range(400)]
        expected = [np.mean([v for day in vals for v in day]), *np.quantile(reps, [.00625, .99375])]
        actual = [got[metric]['point'], *got[metric]['ci']]
        error = float(np.max(np.abs(np.array(expected) - actual)))
        max_error = max(max_error, error)
        print('135 independent ratio:', metric, 'n_races', got[metric]['n_races'],
              'n_rows', got[metric]['n_rows'], 'max_abs_error', error)
    assert max_error < 1e-14
    print('135 PASS: uneven day/horse counts, incomplete results, dead heat, per-seed loss averaging')


if __name__ == '__main__':
    exploratory_promotes()
    autoactivation_keeps_old_active()
    independent_135_ratio_check()
