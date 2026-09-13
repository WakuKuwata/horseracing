import datetime as dt
from pathlib import Path
import sys
import pickle
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import accuracy_batch_common as c
from horseracing_eval.dataset import EvalRace, ScoringLabel
from horseracing_eval.predictor import HorseEntry, RaceContext


def record(rid='a', day=dt.date(2020, 1, 1), n=4, *, complete=True, dead_heat=False):
    ids = tuple(str(i) for i in range(n))
    ctx = RaceContext(rid, day, tuple(HorseEntry(h) for h in ids))
    labels = [ScoringLabel(ids[0], 1, 1, 1), ScoringLabel(ids[1], int(dead_heat), 1, 1),
              ScoringLabel(ids[2], 0, 0, 1)]
    er = EvalRace(ctx, tuple(labels), n_result_rows=n if complete else n-1)
    heads = c.heads_from_win(np.full(n, 1/n))
    return c.MixedRace(er, ids, np.stack([heads] * 6), heads, pd.DataFrame())


def test_cutoff_and_missing_dates_fail_closed():
    c.validate_dates([dt.date(2026, 9, 6)])
    for dates in ([dt.date(2026, 9, 7)], [None], [], [dt.datetime(2020, 1, 1, 1)]):
        with pytest.raises(ValueError): c.validate_dates(dates)


def test_started_all_dnf_and_deadheat_are_scored_but_partial_race_is_not():
    rows = [record(), record('b', complete=False), record('c', dead_heat=True)]
    out = c.summarize_predictions(rows, {r.race_id: r.heads for r in rows})
    assert out['n_races'] == 3 and out['n_complete_races'] == 2
    assert out['n_eligible_races'] == 1
    assert out['heads']['top2']['n_rows'] == 8  # includes both DNF rows
    assert out['winner_nll'] == pytest.approx(np.log(4))
    assert out['heads']['top2']['logloss'] == pytest.approx(np.log(2))


def test_paired_ratio_matches_pooled_loss_not_mean_of_race_means():
    rows = [record('a', n=4), record('b', n=8)]
    base = {r.race_id: r.heads for r in rows}
    cand = {r.race_id: c.heads_from_win(np.array([.5] + [.5/(len(r.ids)-1)]*(len(r.ids)-1))) for r in rows}
    result = c.paired_metrics(rows, base, cand, b=20)
    b, p = c.summarize_predictions(rows, base), c.summarize_predictions(rows, cand)
    expected = p['heads']['top2']['logloss'] - b['heads']['top2']['logloss']
    assert result['top2_logloss']['point'] == pytest.approx(expected, abs=1e-14)
    assert result['top2_logloss']['ci'] == pytest.approx([expected, expected], abs=1e-14)
    assert result['top2_logloss']['n_rows'] == 12


def test_invalid_probabilities_or_population_fail():
    r = record()
    for bad in (np.full((4, 3), np.nan), np.ones((4, 3)), np.array([[.5, .4, .7]] * 4)):
        with pytest.raises(ValueError): c.summarize_predictions([r], {'a': bad})
    with pytest.raises(ValueError): c.summarize_predictions([r], {})


def test_snapshot_is_not_unpickled_when_metadata_violates_cutoff(tmp_path, monkeypatch):
    snapshot = tmp_path / 'snapshot.pkl'
    snapshot.write_bytes(b'never load')
    c.write_json(snapshot.with_suffix('.json'), {'data_through': '2026-09-07', 'snapshot_sha256': c.SNAPSHOT_SHA256})
    monkeypatch.setattr(c, 'SNAPSHOT', snapshot)
    monkeypatch.setattr(c.pickle, 'load', lambda *a, **k: pytest.fail('forbidden unpickle'))
    with pytest.raises(ValueError, match='cutoff'): c.load_frozen_inputs()


def test_json_evidence_is_append_only(tmp_path):
    p = tmp_path / 'result.json'
    c.write_json(p, {'x': 1})
    with pytest.raises(FileExistsError): c.write_json(p, {'x': 2})


def test_snapshot_digest_mismatch_stops_before_unpickling(tmp_path, monkeypatch):
    snapshot = tmp_path / 'snapshot.pkl'
    snapshot.write_bytes(b'not the pinned snapshot')
    c.write_json(snapshot.with_suffix('.json'), {'data_through': '2020-02-02',
                                               'snapshot_sha256': c.SNAPSHOT_SHA256})
    monkeypatch.setattr(c, 'SNAPSHOT', snapshot)
    monkeypatch.setattr(c.pickle, 'load', lambda *a, **k: pytest.fail('unpickle after SHA mismatch'))
    with pytest.raises(ValueError, match='SHA mismatch'): c.load_frozen_inputs()


def synthetic_snapshot(tmp_path, monkeypatch, mutate=lambda frame: frame):
    races = [record('a', dt.date(2020, 2, 1)).er, record('b', dt.date(2020, 2, 2)).er]
    frame = pd.DataFrame([{'race_id': r.context.race_id, 'horse_id': h.horse_id,
                           'race_date': r.context.race_date}
                          for r in races for h in r.context.started_horses])
    frame = mutate(frame)
    snapshot = tmp_path / 'snapshot.pkl'
    snapshot.write_bytes(pickle.dumps((SimpleNamespace(frame=frame), races)))
    sha = c.digest(snapshot)
    c.write_json(snapshot.with_suffix('.json'), {'data_through': '2020-02-02',
                'snapshot_sha256': sha, 'n_rows': len(frame), 'n_races': len(races)})
    source = tmp_path / '130'
    c.write_json(source / 'run-freeze.json', {'snapshot_sha256': sha,
                'config': {'years': list(c.YEARS)}, 'coefficients': {
                    m: {str(y): [0.] * (5 if m.startswith('joint') else 1) for y in c.YEARS}
                    for m in c.MEMBER_IDS}})
    monkeypatch.setattr(c, 'SNAPSHOT', snapshot)
    monkeypatch.setattr(c, 'SNAPSHOT_SHA256', sha)
    monkeypatch.setattr(c, 'SOURCE', source)


def test_same_year_matrix_label_date_mismatch_is_rejected(tmp_path, monkeypatch):
    def mutate(frame):
        frame.loc[frame.race_id == 'a', 'race_date'] = dt.date(2020, 1, 31)
        return frame
    synthetic_snapshot(tmp_path, monkeypatch, mutate)
    with pytest.raises(ValueError, match='dates differ'): c.load_frozen_inputs()


def test_eval_races_must_be_contained_in_matrix(tmp_path, monkeypatch):
    def mutate(frame):
        frame.loc[frame.race_id == 'a', 'race_id'] = 'other'
        return frame
    synthetic_snapshot(tmp_path, monkeypatch, mutate)
    with pytest.raises(ValueError, match='identities differ'): c.load_frozen_inputs()


def test_python_date_matrix_values_are_normalized_before_comparison(tmp_path, monkeypatch):
    synthetic_snapshot(tmp_path, monkeypatch)
    x = c.load_frozen_inputs()
    assert len(x.races) == 2 and x.provenance['actual_data_through'] == '2020-02-02'


def test_cache_receipt_sha_mismatch_stops_before_unpickling(tmp_path, monkeypatch):
    source = tmp_path / '130'
    path = source / 'cache/joint-42-2020.pkl'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'never unpickle')
    c.write_json(source / 'receipts/joint-42-2020.json', {'cache_sha256': 'wrong',
        'source_hash': 'source', 'member': 'joint-42', 'year': 2020, 'smoke': False})
    monkeypatch.setattr(c, 'SOURCE', source)
    monkeypatch.setattr(c.pickle, 'load', lambda *a, **k: pytest.fail('unpickle after receipt mismatch'))
    inputs = SimpleNamespace(freeze130={'source_hash': 'source'})
    with pytest.raises(ValueError, match='receipt mismatch'):
        c._load_annual_cache(inputs, 'joint-42', 2020, [])
