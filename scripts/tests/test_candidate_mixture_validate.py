"""Independent replay boundary tests; no model fitting or database."""
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pytest
from horseracing_eval.predictor import Prediction
from horseracing_training.predictor import assemble_predictions

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('candidate_validate_tested', ROOT / 'scripts/candidate_mixture_validate.py')
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


def test_all_heads_compared():
    p = assemble_predictions(['a', 'b', 'c'], [.2, .3, .5], eps=0)
    error, count = v.compare_heads(p, p, ['a', 'b', 'c'])
    assert count == 9
    assert list(error) == [0, 0, 0]
    q = dict(p)
    q['a'] = Prediction(p['a'].win, p['a'].top2 + 1e-5, p['a'].top3)
    q['b'] = Prediction(p['b'].win, p['b'].top2 - 1e-5, p['b'].top3)
    with pytest.raises(ValueError, match='parity failed'):
        v.compare_heads(q, p, ['a', 'b', 'c'])


def test_race_horse_order_fail_closed():
    p = assemble_predictions(['a', 'b', 'c'], [.2, .3, .5], eps=0)
    with pytest.raises(ValueError, match='order'):
        v.compare_heads(dict(reversed(list(p.items()))), p, ['a', 'b', 'c'])


def test_hash_tamper_and_missing(tmp_path):
    path = tmp_path / 'source'; path.write_text('before')
    hashes = {str(path): v.sha(path)}
    v.assert_hashes(hashes)
    path.write_text('after')
    with pytest.raises(ValueError): v.assert_hashes(hashes)
    path.unlink()
    with pytest.raises(FileNotFoundError): v.assert_hashes(hashes)


def report():
    return {'artifact_kind': 'candidate_annual_serving_parity', 'status': 'PASS',
            'can_adopt': False, 'eligible_for_verdict': False, 'method_sha256': v.sha(v.__file__),
            'helper_sha256': v.sha(v.new.__file__), 'n_races': 23030, 'n_eligible': 22990, 'n_days': 715,
            'additional_booster_fits': 0, 'additional_coefficient_fits': 0, 'atol': v.ATOL,
            'research_source_hash': 'source', 'runtime': 'runtime', 'max_abs_by_head': [0., 0., 0.],
            'input_hashes': {v.__file__: v.sha(v.__file__)},
            'years': {str(y): {'all_races': 23030 if y == 2020 else 0,
                                'eligible_races': 22990 if y == 2020 else 0,
                                'members': {f'{b}-{s}': {} for b, s in v.MEMBERS},
                                'all_six_annual_gammas_preserved': True} for y in range(2020, 2027)}}


def test_completed_resume_only_verifies(tmp_path, monkeypatch):
    path = tmp_path / 'result.json'; path.write_text(json.dumps(report()))
    monkeypatch.setattr(v.old, 'source_hash', lambda: 'source')
    monkeypatch.setattr(v.old.q.probe, 'runtime', lambda: 'runtime')
    monkeypatch.setattr(v.old, 'verify', lambda: pytest.fail('full verification must not repeat'))
    before = path.read_bytes(); v.run(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize('field,value', [('can_adopt', True), ('n_races', 22990),
                                        ('n_eligible', 23030), ('additional_coefficient_fits', 1),
                                        ('helper_sha256', 'bad'), ('years', {}),
                                        ('input_hashes', {}), ('max_abs_by_head', [0, np.nan, 0]),
                                        ('max_abs_by_head', [0, 1e-4, 0])])
def test_bad_resume_rejected(tmp_path, field, value):
    r = report(); r[field] = value
    path = tmp_path / 'bad.json'; path.write_text(json.dumps(r))
    with pytest.raises(ValueError): v.verify_existing(path)


def test_missing_annual_member_rejected(tmp_path):
    r = report(); r['years']['2026']['members'].pop('anchor-44')
    path = tmp_path / 'bad.json'; path.write_text(json.dumps(r))
    with pytest.raises(ValueError): v.verify_existing(path)
