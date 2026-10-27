"""Post-hoc descriptive top1 counts; never used to change the registered selection."""
import hashlib
import json
from pathlib import Path
import pickle

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
WORK = ROOT / 'artifacts/132-accuracy-first-batch'
HASHES = {}


def read(path):
    return json.loads(path.read_text())


def checked_pickle(path, expected):
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    assert sha == expected
    HASHES[str(path)] = sha
    with path.open('rb') as fh:
        return pickle.load(fh)


def measure(rows, predictions):
    n = hits = ties = changed = 0
    credit = 0.
    for r in rows:
        assert '2020-01-01' <= r['race_date'] <= '2026-08-23' <= '2026-09-06'
        labels = {sl[0]: sl[1] for sl in r['labels']}
        winners = [i for i, h in enumerate(r['ids']) if labels.get(h, 0) == 1]
        if r['n_result_rows'] < len(r['ids']) or len(winners) != 1:
            continue
        p = predictions[r['race_id']][:, 0]
        top = np.flatnonzero(p == p.max())
        n += 1
        hits += int(top[0] == winners[0])
        credit += float(winners[0] in top) / len(top)
        ties += int(len(top) > 1)
        changed += int(p.argmax() != r['arms']['head_mean'][:, 0].argmax())
    return {'n_eligible': n, 'first_index_hits': hits, 'first_index_hit_rate': hits / n,
            'fractional_tie_hit_rate': credit / n, 'tied_top_races': ties, 'top1_changed_races': changed}


def main():
    topk = read(WORK / 'topk/summary.json')
    rows = checked_pickle(WORK / 'topk/full-predictions.pkl', topk['regimes']['full']['predictions_sha256'])
    cs = read(WORK / 'colsample/summary.json')
    result = {'kind': 'post_hoc_descriptive_diagnostic_not_selection_criterion',
              'tie_policy': 'First saved started-horse order; also report fractional credit among exact ties.',
              'can_adopt': False, 'eligible_for_verdict': False, 'cutoff': '2026-09-06',
              'actual_end': '2026-08-23', 'arms': {}}
    result['arms']['baseline'] = measure(rows, {r['race_id']: r['arms']['head_mean'] for r in rows})
    for arm, report in cs['reports'].items():
        payload = checked_pickle(Path(report['prediction_path']), report['prediction_sha256'])
        assert payload['race_ids'] == [r['race_id'] for r in rows]
        assert all(tuple(payload['horse_ids'][r['race_id']]) == tuple(r['ids']) for r in rows)
        result['arms'][arm] = measure(rows, payload['predictions'])
    result['source_sha256'] = HASHES
    result['method_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    with Path(__file__).with_suffix('.json').open('x') as fh:
        json.dump(result, fh, indent=2)
        fh.write('\n')
    print(json.dumps(result['arms']))


if __name__ == '__main__':
    main()
