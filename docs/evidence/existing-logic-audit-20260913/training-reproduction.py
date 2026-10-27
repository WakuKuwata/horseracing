"""Read-only numerical audit of current training primitives; no DB or full training.

Run from the repository with training/.venv/bin/python. Exactly one synthetic
five-tree model is fitted to check race alignment. Repository files are untouched.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

from horseracing_eval.dataset import EvalRace, ScoringLabel, population_masks
from horseracing_eval.predictor import HorseEntry, RaceContext
from horseracing_training.cond_logit import STAGE_WEIGHTS, pl_topk_objective
from horseracing_training.dataset import RANK_LABEL
from horseracing_training.hpo import _encode_fold
from horseracing_training.target_encoding import oof_target_encode
from horseracing_training.win_model import DEFAULT_PARAMS, WinModel


class DS:
    def get_weight(self):
        return None


def audit_pl():
    rng = np.random.default_rng(783)
    max_grad_error = max_hess_error = 0.0
    for _ in range(100):
        sizes = [3, 5, 9]
        ranks = np.r_[
            rng.permutation([1, 2, 3]),
            rng.permutation([1, 2, 3, 0, 0]),
            rng.permutation([1, 2, 3, 0, 0, 0, 0, 0, 0]),
        ]
        scores = rng.normal(0, 1, 17)

        def independently_written_loss(values):
            total = 0.0
            start = 0
            for size in sizes:
                score = values[start:start + size]
                rank = ranks[start:start + size]
                remaining = np.ones(size, dtype=bool)
                for stage, weight in enumerate(STAGE_WEIGHTS, 1):
                    target = rank == stage
                    if target.sum() != 1 or remaining.sum() < 2:
                        break
                    current = score[remaining]
                    maximum = current.max()
                    total += weight * (
                        np.log(np.exp(current - maximum).sum()) + maximum - score[target][0]
                    )
                    remaining &= ~target
                start += size
            return total

        grad, hess = pl_topk_objective(sizes, ranks)(scores, DS())
        epsilon = 1e-4
        numeric_grad, numeric_hess = [], []
        baseline = independently_written_loss(scores)
        for k in range(17):
            perturbation = np.zeros(17)
            perturbation[k] = epsilon
            plus = independently_written_loss(scores + perturbation)
            minus = independently_written_loss(scores - perturbation)
            numeric_grad.append((plus - minus) / (2 * epsilon))
            numeric_hess.append((plus + minus - 2 * baseline) / epsilon**2)
        max_grad_error = max(max_grad_error, float(np.max(np.abs(grad - numeric_grad))))
        max_hess_error = max(max_hess_error, float(np.max(np.abs(hess - numeric_hess))))

    # One actual five-tree model checks the production WinModel sort/restore path.
    n = 24
    features = pd.DataFrame({'x': rng.normal(size=n)})
    race_ids = np.repeat(['r3', 'r1', 'r4', 'r2'], 6)
    ranks = np.tile([1, 2, 3, 0, 0, 0], 4)
    labels = (ranks == 1).astype(int)
    model = WinModel(
        params={**DEFAULT_PARAMS, 'n_estimators': 5, 'min_child_samples': 2},
        objective='pl_topk',
    ).fit(features, labels, group_ids=race_ids, ranks=ranks)
    original = model.predict(features, group_ids=race_ids)
    order = rng.permutation(n)
    permuted = model.predict(features.iloc[order], group_ids=race_ids[order])
    restored = np.empty(n)
    restored[order] = permuted
    return {
        'fixture_count': 100,
        'seed': 783,
        'finite_difference_epsilon': 1e-4,
        'max_gradient_error': max_grad_error,
        'max_diagonal_hessian_error': max_hess_error,
        'permutation_max_error': float(np.max(np.abs(original - restored))),
        'race_probability_sums': pd.Series(original).groupby(race_ids).sum().tolist(),
        'synthetic_model_fits': 1,
        'synthetic_model_trees': 5,
    }


def audit_incomplete_results_count():
    # Hypothetical DB rows: winner has a result, cancelled has a stale result,
    # and missing-result has none. The API retained only the total count=2.
    race = EvalRace(
        RaceContext('202601010101', dt.date(2026, 1, 1),
                    (HorseEntry('winner'), HorseEntry('missing-result'))),
        (ScoringLabel('winner', 1, 1, 1),),
        n_result_rows=2,
    )
    masks = population_masks(race)
    return {
        'synthetic_started_horse_ids': ['winner', 'missing-result'],
        'synthetic_result_horse_ids': ['winner', 'cancelled'],
        'n_result_rows': 2,
        'complete_results_returned': masks.complete_results,
        'winner_nll_eligible_returned': masks.eligible,
        'missing_result_win_label_returned': masks.started_win['missing-result'],
        'scope': 'Synthetic guard defect; not proof that current frozen data exhibits it.',
    }


def audit_hpo_te():
    train = pd.DataFrame({
        'race_id': ['r1', 'r1', 'r2', 'r2', 'r3', 'r3'],
        'race_date': [dt.date(2025, 1, 1)] * 2 + [dt.date(2025, 1, 2)] * 2
                     + [dt.date(2025, 1, 3)] * 2,
        'win': [1, 0, 1, 0, 1, 0],
        'jockey_id': ['a', 'b', 'c', 'd', 'e', 'f'],
    })
    valid = pd.DataFrame({
        'race_id': ['r4', 'r4'], 'race_date': [dt.date(2025, 1, 4)] * 2,
        'win': [1, 0], 'jockey_id': ['g', 'h'],
    })
    hpo, _ = _encode_fold(train, valid, ['jockey_id'], ('jockey_id',), 'win', 10.0)
    final = oof_target_encode(
        train, 'jockey_id', race_id_col='race_id', race_date_col='race_date',
        label_col='win', prior=0.5, smoothing=10.0,
    )
    return {
        'hpo_training_TE': hpo.jockey_id.tolist(),
        'final_training_TE': final.tolist(),
        'active129_hpo': False,
        'scope': 'Confirmed HPO/final fitting inconsistency; inactive in current PL recipe.',
    }


if __name__ == '__main__':
    evidence = {
        'pl_and_race_alignment': audit_pl(),
        'incomplete_results_count': audit_incomplete_results_count(),
        'hpo_te': audit_hpo_te(),
        'training_rank_column': RANK_LABEL,
        'db_reads': 0,
        'db_writes': 0,
        'full_model_fits': 0,
    }
    path = Path('/tmp/horseracing_training_logic_audit.json')
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
    print(path)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
