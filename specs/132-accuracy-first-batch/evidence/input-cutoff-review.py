"""Independent parent audit; only the two explicitly pinned 111 inputs are read."""
import datetime as dt
import gc
import hashlib
import json
from pathlib import Path
import pickle

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
CUTOFF = dt.date(2026, 9, 6)
EXPECTED = {
    'snapshot.pkl': '58397ba94dc685c6e49b509619fb447106702d869adebc38889b021f3be6859a',
    'source-frames.pkl': '7dc539c4224fe4132c3892099f2c62790b47d1f3136fe8b79bbaeadd5c02ef11',
}


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def dates(values):
    x = pd.to_datetime(pd.Series(values), errors='raise')
    assert not x.empty and not x.isna().any()
    assert x.eq(x.dt.normalize()).all()
    assert dt.date(2007, 1, 1) <= x.min().date() <= x.max().date() <= CUTOFF
    return x


def run():
    folder = ROOT / 'artifacts/111-ability-observation'
    hashes = {name: sha(folder / name) for name in EXPECTED}
    assert hashes == EXPECTED  # Before pickle or any real-data inspection.
    meta = json.loads((folder / 'snapshot.json').read_text())
    assert dt.date.fromisoformat(meta['data_through']) <= CUTOFF
    with (folder / 'snapshot.pkl').open('rb') as fh:
        matrix, races = pickle.load(fh)
    frame_dates = dates(matrix.frame.race_date)
    label_dates = dates([r.context.race_date for r in races])
    matrix_ids = set(matrix.frame.race_id)
    label_ids = {r.context.race_id for r in races}
    assert len(label_ids) == len(races) and label_ids <= matrix_ids
    assert not matrix.frame.duplicated(['race_id', 'horse_id']).any()
    per_race = matrix.frame.groupby('race_id').race_date
    assert per_race.nunique().eq(1).all()
    day_lookup = per_race.first()
    assert all(pd.Timestamp(r.context.race_date) == pd.Timestamp(day_lookup[r.context.race_id]) for r in races)
    assert all(r.n_result_rows is not None for r in races)
    extras = sorted(matrix_ids - label_ids)
    result = {'status': 'PASS', 'allowed_data_through': str(CUTOFF),
              'actual_data_through': str(frame_dates.max().date()),
              'input_sha256': hashes, 'matrix_rows': len(matrix.frame),
              'matrix_races': len(matrix_ids), 'evaluation_races': len(races),
              'unknown_result_coverage_races': 0, 'matrix_only_race_ids': extras,
              'matrix_label_contract': 'Unique EvalRace IDs are a subset; shared dates match; only EvalRace IDs score.',
              'db_reads': 0, 'post_cutoff_data_reads': 0}
    assert frame_dates.max() == label_dates.max() == pd.Timestamp(meta['data_through'])
    del matrix, races, frame_dates, label_dates, per_race, day_lookup
    gc.collect()
    with (folder / 'source-frames.pkl').open('rb') as fh:
        frames = pickle.load(fh)
    source_dates = dates(frames.races.race_date)
    assert str(source_dates.max().date()) == meta['data_through']
    source_ids = set(frames.races.race_id)
    assert not frames.races.race_id.duplicated().any() and matrix_ids <= source_ids
    for source in (frames.race_horses, frames.race_results):
        assert set(source.race_id) <= source_ids
        assert not source.duplicated(['race_id', 'horse_id']).any()
    result['source_races'] = len(source_ids)
    result['matrix_only_source_diagnostics'] = {}
    for rid in extras:
        rr = frames.race_results.loc[frames.race_results.race_id.eq(rid)]
        rh = frames.race_horses.loc[frames.race_horses.race_id.eq(rid)]
        result['matrix_only_source_diagnostics'][rid] = {
            'appearance_rows': len(rh), 'result_rows': len(rr),
            'result_statuses': {str(k): int(v) for k, v in rr.result_status.value_counts(dropna=False).items()},
        }
    result['method_sha256'] = sha(Path(__file__))
    output = Path(__file__).with_suffix('.json')
    with output.open('x') as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
        fh.write('\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    run()
