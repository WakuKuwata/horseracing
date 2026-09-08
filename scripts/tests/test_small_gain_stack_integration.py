"""Check the registered study against actual model columns and the existing gate."""
from pathlib import Path
import sys

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import small_gain_stack as s
from horseracing_training.dataset import TrainingMatrix
from horseracing_eval.gates import evaluate_core_gate


def test_registered_scopes_match_actual_shipped_model_and_exact_increment():
    cfg = s.load_config()
    original = s.p.columns_from_model()
    columns = original + s.p.OBSERVATION_COLUMNS
    matrix = TrainingMatrix(pd.DataFrame({c: [0.] for c in columns}), columns, [])
    scopes = {a['id']: s.p.validate_scope(matrix, s.p.make_recipe(cfg, a['drop_features']))
              for a in cfg['study_arms']}
    assert scopes['anchor'] == original
    assert {k: len(v) for k, v in scopes.items()} == {'anchor': 138, 'pruning': 125, 'stack': 127}
    assert scopes['stack'] == scopes['pruning'] + s.p.OBSERVATION_COLUMNS[:2]
    assert set(scopes['anchor']) - set(scopes['pruning']) == set(cfg['relative_drop_features'])
    assert 'asof_spdfig_sd' not in scopes['stack']


@pytest.mark.parametrize('ci_high,quality,expected', [(-.00001, 0., True), (.00001, 0., False),
                                                     (-.00001, .0006, False)])
def test_real_study_gate_allows_tiny_supported_gain_but_keeps_quality(ci_high, quality, expected):
    gate = evaluate_core_gate(diff=-.0001, ci_low=-.0002, ci_high=ci_high,
        recent={'pass': True}, top2_diff=quality, top3_diff=0., cand_ece=.001,
        act_ece=.001, cfg=s.load_config())
    assert gate.adopted is expected


def test_study_is_research_and_fixed_seed_followup():
    cfg = s.load_config()
    assert cfg['can_adopt'] is False
    assert cfg['artifact_scope'] == 'historical_development'
    assert cfg['seed_followup']['seeds'] == [42, 43, 44]
    assert cfg['seed_followup']['deployment_seed'] == 42
    assert cfg['contrasts'] == [
        {'id': 'increment', 'candidate': 'stack', 'baseline': 'pruning'},
        {'id': 'anchor', 'candidate': 'stack', 'baseline': 'anchor'}]
