"""Independent scalar audit of all 17 registered 127 feature columns.

No feature builder, rolling, expanding or merge_asof is used for reconstruction.
Tolerances are fixed before any real-data comparison: ordinary columns 1e-10;
SD5 requires both absolute SD error <=1e-7 and variance error <=1e-14.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
import gc
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))

ORDINARY_ATOL = 1e-10
SD_ATOL = 1e-7
VARIANCE_ATOL = 1e-14
SD_INDEX = 4


def finite(value):
    try:
        return math.isfinite(float(value))
    except (ValueError, TypeError):
        return False


def mean(values):
    return math.fsum(values) / len(values) if values else math.nan


def full_mean(values, size):
    return mean(values[-size:]) if len(values) >= size and all(finite(x) for x in values[-size:]) else math.nan


def scalar_history_features(finish_residuals, win_residuals, finished, weights, targets):
    """All histories use source order for date ties; weight input is unique-day markers.

    Each target is (day,key). Finished rows are (day,raw_order,normalized_order).
    Residual/weight rows are (day,value); a weight NaN is an ambiguous valid day.
    The latest weight is compared only to unambiguous days strictly BEFORE its day.
    """
    sources = [sorted(rows, key=lambda x: x[0]) for rows in
               (finish_residuals, win_residuals, finished, weights)]
    positions = [0] * 4
    accumulated = [[] for _ in sources]
    for day, key in sorted(targets, key=lambda x: x[0]):
        for i, source in enumerate(sources):
            while positions[i] < len(source) and source[positions[i]][0] < day:
                accumulated[i].append(source[positions[i]])
                positions[i] += 1
        es = [row[1] for row in accumulated[0]]
        ws = [row[1] for row in accumulated[1]]
        out = [mean(es[-5:]), mean(es)] if len(es) >= 3 else [math.nan] * 2
        out += [mean(ws[-10:]), mean(ws)] if len(ws) >= 3 else [math.nan] * 2
        tail = ws[-5:]
        sd = math.sqrt(math.fsum((x - mean(tail)) ** 2 for x in tail) / (len(tail) - 1)) if len(tail) >= 2 else math.nan
        out += [sd, float(len(ws))]
        orders = [row[1] for row in accumulated[2]]
        pcts = [row[2] for row in accumulated[2]]
        lag = lambda values, k: values[-k] if len(values) >= k else math.nan
        trend = math.fsum((i - 2) * x for i, x in enumerate(pcts[-5:])) / 10 if len(pcts) >= 5 and all(finite(x) for x in pcts[-5:]) else math.nan
        out += [lag(pcts, 1), lag(orders, 2), lag(orders, 3), lag(pcts, 2), lag(pcts, 3),
                full_mean(pcts, 3), full_mean(orders, 5), full_mean(pcts, 5),
                min((x for x in pcts if finite(x)), default=math.nan), trend]
        prior_weights = accumulated[3]
        previous = prior_weights[-1][1] if prior_weights else math.nan
        earlier = [row[1] for row in prior_weights[:-1] if finite(row[1])]
        out.append(previous - mean(earlier) if finite(previous) and len(earlier) >= 3 else math.nan)
        yield key, out


def reconstruct_histories(frames):
    """Independent complete-field primitives; preserve each original source table order."""
    from horseracing_db.enums import EntryStatus, ResultStatus
    days = {r.race_id: pd.Timestamp(r.race_date).date() for r in frames.races.itertuples()}
    raw = list(frames.race_horses[['race_id', 'horse_id', 'entry_status', 'popularity', 'odds', 'weight']].itertuples(index=False, name=None))
    results = {}
    finished_source = []
    for rid, hid, order, status in frames.race_results[['race_id', 'horse_id', 'finish_order', 'result_status']].itertuples(index=False, name=None):
        assert (rid, hid) not in results, ('duplicate_result', rid, hid)
        results[rid, hid] = (float(order) if finite(order) else math.nan, status)
        if status == ResultStatus.FINISHED:
            finished_source.append((rid, hid, float(order) if finite(order) else math.nan))
    fields = defaultdict(list)
    statuses = {}
    weights = defaultdict(lambda: defaultdict(list))
    for rid, hid, status, popularity, odds, weight in raw:
        assert (rid, hid) not in statuses, ('duplicate_entry', rid, hid)
        statuses[rid, hid] = status
        if status == EntryStatus.STARTED:
            fields[rid].append((hid, popularity, odds))
            if finite(weight) and 200 <= float(weight) <= 800:
                weights[hid][days[rid]].append(float(weight))
    audit = defaultdict(int)
    primitives = {}
    for rid, rows in fields.items():
        n = len(rows)
        pp = [float(row[1]) for row in rows] if all(finite(row[1]) and float(row[1]) >= 1 for row in rows) else None
        oo = [float(row[2]) for row in rows] if all(finite(row[2]) and 1 <= float(row[2]) < 999.9 for row in rows) else None
        if pp is not None:
            audit['complete_popularity_source_races'] += 1
            ordered = sorted(pp)
        if oo is not None:
            audit['complete_odds_source_races'] += 1
            denominator = math.fsum(1 / value for value in oo)
        for i, (hid, _, _) in enumerate(rows):
            order, status = results.get((rid, hid), (math.nan, None))
            e = w = None
            if pp is not None and status == ResultStatus.FINISHED and finite(order):
                rank = bisect_left(ordered, pp[i]) + 1
                u = 1 - (rank - 1) / (n - 1) if n > 1 else 1.
                v = 1 - (order - 1) / (n - 1) if n > 1 else 1.
                e = v - u
            if oo is not None:
                w = float(status == ResultStatus.FINISHED and order == 1) - (1 / oo[i]) / denominator
            primitives[rid, hid] = (e, w)
    histories = [defaultdict(list) for _ in range(4)]
    for rid, hid, status, _, _, _ in raw:
        if status != EntryStatus.STARTED:
            continue
        e, w = primitives[rid, hid]
        if e is not None:
            histories[0][hid].append((days[rid], e))
        if w is not None:
            histories[1][hid].append((days[rid], w))
    for rid, hid, order in finished_source:
        n = len(fields.get(rid, []))
        valid = n > 1 and finite(order) and 1 <= order <= n
        pct = (order - 1) / (n - 1) if valid else math.nan
        audit['finished_rows'] += 1
        audit['finished_not_started_rows'] += statuses.get((rid, hid)) != EntryStatus.STARTED
        audit['finished_without_entry_rows'] += (rid, hid) not in statuses
        audit['finished_with_nonstarted_entry_rows'] += (rid, hid) in statuses and statuses[rid, hid] != EntryStatus.STARTED
        audit['finished_invalid_pct_rows'] += not valid
        histories[2][hid].append((days[rid], order, pct))
    for hid, byday in weights.items():
        for day, values in byday.items():
            ambiguous = len(values) > 1
            audit['weight_ambiguous_days'] += ambiguous
            audit['weight_ambiguous_measurements'] += len(values) if ambiguous else 0
            audit['weight_unambiguous_days'] += not ambiguous
            histories[3][hid].append((day, math.nan if ambiguous else values[0]))
    return days, histories, dict(audit)


def main():
    import extra_feature_screen as m
    cfg, frozen = m.verify()
    method_hash = m.s.p.digest(__file__)
    freeze_hash = m.s.p.digest(m.WORK / 'run-freeze.json')
    source_path = m.s.p.WORK / 'source-frames.pkl'
    source_hash = m.s.p.digest(source_path)
    frames = m.s.reuse.load(source_path)
    days, histories, source_audit = reconstruct_histories(frames)
    prepared_audit = m.s.read_json(m.WORK / 'feature-audit.json')['source_population_audit']
    for expected_name, scalar_name in (
        ('weight_ambiguous_horse_days', 'weight_ambiguous_days'),
        ('weight_ambiguous_measurements', 'weight_ambiguous_measurements'),
        ('finished_result_with_nonstarted_entry', 'finished_with_nonstarted_entry_rows'),
        ('finished_result_without_entry', 'finished_without_entry_rows'),
    ):
        assert prepared_audit[expected_name] == source_audit.get(scalar_name, 0), (expected_name, prepared_audit, source_audit)
    del frames
    gc.collect()
    matrix, _ = m.s.reuse.load(m.WORK / 'matrix.pkl')
    columns = list(m.ADDITIONS)
    assert len(columns) == 17 and columns[SD_INDEX] == 'asof_pm_resid_sd5'
    expected = matrix.frame[columns].to_numpy(dtype=float, copy=True)
    targets = defaultdict(list)
    for i, (rid, hid) in enumerate(matrix.frame[['race_id', 'horse_id']].itertuples(index=False, name=None)):
        targets[hid].append((days[rid], i))
    assert len(expected) == 958011
    del matrix, days
    gc.collect()
    compared = missing = rows = 0
    errors = {c: 0. for c in columns}
    variance_error = 0.
    for hid, ts in targets.items():
        for key, values in scalar_history_features(*(h.get(hid, []) for h in histories), ts):
            rows += 1
            for j, (column, x, y) in enumerate(zip(columns, values, expected[key], strict=True)):
                compared += 1
                if math.isnan(x) or math.isnan(y):
                    assert math.isnan(x) and math.isnan(y), (hid, key, column, x, y)
                    missing += 1
                    continue
                assert finite(x) and finite(y), (hid, key, column, x, y)
                error = abs(x - y)
                errors[column] = max(errors[column], error)
                if column == 'asof_pm_result_obs_count':
                    assert x == y and x >= 0 and x == math.floor(x), (hid, key, column, x, y)
                if j == SD_INDEX:
                    ve = abs(x * x - y * y)
                    variance_error = max(variance_error, ve)
                    assert x >= 0 and y >= 0 and error <= SD_ATOL and ve <= VARIANCE_ATOL, (hid, key, column, x, y, error, ve)
                else:
                    assert error <= ORDINARY_ATOL, (hid, key, column, x, y, error)
    assert rows == len(expected) and compared == rows * 17
    m.verify()
    assert m.s.p.digest(__file__) == method_hash
    assert m.s.p.digest(source_path) == source_hash
    assert m.s.p.digest(m.WORK / 'run-freeze.json') == freeze_hash
    result = dict(artifact_kind='extra_feature_scalar_reconstruction', status='PASS',
        can_adopt=False, eligible_for_verdict=False, additional_fits=0,
        method_sha256=method_hash, run_freeze_sha256=freeze_hash,
        matrix_sha256=frozen['matrix_sha256'], source_frames_sha256=source_hash,
        horse_rows=rows, feature_cells=compared, missing_cells=missing,
        max_error_by_column=errors, max_sd_variance_error=variance_error,
        tolerances=dict(ordinary_absolute=ORDINARY_ATOL, sd_absolute=SD_ATOL, sd_variance_absolute=VARIANCE_ATOL),
        source_audit=source_audit, strict_prior_dates=True, same_day_excluded=True,
        notes=['F04 separate FINISHED popularity-complete and STARTED odds-complete populations; ddof1 SD.',
               '088 original FINISHED source order, full started denominator; windows propagate NaN, career best skips NaN.',
               'Weight latest ambiguous day is NaN; earlier mean excludes every ambiguous day and requires three unambiguous days.',
               'No feature builder, merge_asof, rolling, expanding or fitting used.'])
    path = m.SPEC / 'evidence/independent-feature-review.json'
    if path.exists():
        assert m.s.read_json(path) == result
    else:
        m.write_json(path, result)
    print('127 FEATURE RECONSTRUCTION PASS', compared, max(errors.values()), flush=True)


if __name__ == '__main__':
    main()
