"""Independent 135 recent input/prediction audit. No SQL, results, or fitting.

Run inputs only after Step2 and capture receipts exist; run predictions only
after inputs audit and execution receipt. All pickles are SHA checked first.
TE inverse sets, prior-gap/season correction, softmax and ordered top-k sums
are implemented here without calling recent/common correction or scorers.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import datetime as dt
import hashlib
import json
from pathlib import Path
import pickle
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
for package in ('db', 'features', 'training', 'eval', 'probability', 'serving'):
    sys.path.insert(0, str(ROOT / package / 'src'))
WORK = ROOT / 'artifacts/135-mixture-reproducibility/recent'
SPEC = ROOT / 'specs/135-mixture-reproducibility'
REPRO = ROOT / 'artifacts/135-mixture-reproducibility/repro'
OLD = ROOT / 'artifacts/model_versions/lgbm-094-cap900'
BUNDLE = ROOT / 'artifacts/129-candidate-mixture-serving/bundle.json'
END = dt.date(2026, 9, 6)
START = dt.date(2026, 8, 29)
TRAIN_END = dt.date(2026, 8, 23)
KEYS = ['race_id', 'horse_id', 'race_date']
SNAPSHOT_SHA = '58397ba94dc685c6e49b509619fb447106702d869adebc38889b021f3be6859a'


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def verify(files):
    for p, expected in files.items():
        assert not Path(p).is_symlink() and sha(p) == expected, p


def sealed(path, files):
    path = Path(path)
    verify({str(path): files[str(path)]})
    with path.open('rb') as fh:
        return pickle.load(fh)


def output_path(phase):
    return SPEC / 'evidence' / ('recent-' + phase + '-independent-review.json')


def gate():
    receipt = read(WORK / 'input-receipt.json')
    assert receipt['status'] == 'INPUTS_FROZEN' and receipt['no_outcomes_read'] is True
    assert receipt['can_adopt'] is False and receipt['eligible_for_verdict'] is False
    assert receipt['train_through'] == TRAIN_END.isoformat()
    assert receipt['input_window'] == {'from': START.isoformat(), 'through': END.isoformat()}
    verify(receipt['files'])
    summary_path = REPRO / 'summary.json'
    review_path = SPEC / 'evidence/repro-independent-review.json'
    summary, review = read(summary_path), read(review_path)
    assert summary['status'] == 'RESEARCH_COMPLETE' and summary['data_through'] == TRAIN_END.isoformat()
    assert review['status'] == 'PASS' and review['summary_sha256'] == sha(summary_path)
    assert review['freeze_sha256'] == summary['freeze_sha256'] == sha(REPRO / 'run-freeze.json')
    return receipt


def models(receipt):
    # Native loaders are used solely to verify/deserialize pinned historical
    # boosters/calibrators. No model entry point or TE transform is used below.
    from horseracing_serving.mixture_model import load_mixture_bundle
    import mixture_reproducibility as repro
    bundle = load_mixture_bundle(BUNDLE, expected_sha256=receipt['files'][str(BUNDLE)])
    job = REPRO / 'jobs/recent-42'
    model_receipt = read(job / 'receipt.json')
    assert model_receipt['freeze_sha256'] == sha(REPRO / 'run-freeze.json')
    assert model_receipt['booster_fits'] == 8 and model_receipt['recent_inputs_loaded'] is False
    candidate = repro.load_model(job / 'model', model_receipt['model_files'])
    assert candidate.metadata['seed'] == 42 and candidate.metadata['train_through'] == TRAIN_END.isoformat()
    old = sealed(OLD / 'preprocessor.pkl', receipt['files'])['encoders']
    new = bundle.members[0].model.encoders
    assert set(old) == set(new)
    for encoders in [m.model.encoders for m in bundle.members] + [candidate.encoders]:
        assert set(encoders) == set(new)
        for col in new:
            a, b = encoders[col], new[col]
            assert (a.col, a.prior, a.smoothing, a.mapping) == (b.col, b.prior, b.smoothing, b.mapping)
    return bundle, candidate, old, new


def possible_new_values(value, old, new):
    """Enumerate all possible raw identities independently for one stored value."""
    if not isinstance(value, (int, float, np.number)) or isinstance(value, (bool, np.bool_)) or not np.isfinite(value):
        return set()
    possible = set()
    for identity in set(old.mapping) | set(new.mapping):
        if float(old.mapping.get(identity, old.prior)) == float(value):
            possible.add(float(new.mapping.get(identity, new.prior)))
    if float(value) == float(old.prior):
        possible.add(float(new.prior))  # an identity absent from both mappings
    return possible


def check_timestamp(row, *, snapshot):
    exclusive = dt.datetime(2026, 9, 7, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    keys = ('computed_at', 'run_created_at', 'run_updated_at', 'snapshot_created_at', 'snapshot_updated_at') if snapshot else ('computed_at', 'created_at', 'updated_at')
    post = row['post_time']
    assert isinstance(post, dt.datetime) and post.utcoffset() is not None
    for key in keys:
        value = row[key]
        assert isinstance(value, dt.datetime) and value.utcoffset() is not None
        assert value < post and value < exclusive


def reconstructed(raw, stored, old, new, bundle):
    assert raw['no_outcomes_read'] is True
    full = next(m.model.feature_cols for m in bundle.members if m.id == 'anchor-42')
    assert raw['feature_columns'] == full
    appearance = pd.DataFrame(raw['appearances'], columns=KEYS)
    assert not appearance.duplicated(KEYS[:2]).any()
    assert all(type(v) is dt.date and TRAIN_END < v <= END for v in appearance.race_date)
    pd.testing.assert_frame_equal(appearance, stored['appearances'])
    started = {r: tuple(g.horse_id) for r, g in appearance.groupby('race_id', sort=False)}
    selection = {r['race_id']: r for r in raw['selection']}
    assert len(selection) == len(raw['selection']) == 144
    by_race = defaultdict(list)
    for row in raw['snapshots']:
        assert type(row['race_date']) is dt.date and START <= row['race_date'] <= END
        assert row['race_id'] in selection and row['feature_version'] == 'features-021'
        assert set(row['features']).issubset(full)
        check_timestamp(row, snapshot=True)
        by_race[row['race_id']].append(row)
    included, excluded, reasons, values = {}, {}, Counter(), 0
    for rid, selected in selection.items():
        day = selected['race_date']
        assert type(day) is dt.date and START <= day <= END
        group = by_race[rid]
        reason = None
        if selected['prediction_run_id'] is None:
            assert not group
            reason = 'no preserved prestart094 snapshot'
        else:
            check_timestamp(selected, snapshot=False)
            if len(group) != selected['n_snapshots']:
                reason = 'selected snapshot row count changed'
            elif any(not s['features_was_dict'] or set(s['features']) != set(full) for s in group):
                reason = 'missing saved full138 feature column'
            else:
                for s in group:
                    assert s['race_date'] == day and str(s['prediction_run_id']) == str(selected['prediction_run_id'])
                ids = [s['horse_id'] for s in group]
                if not ids or len(set(ids)) != len(ids) or set(ids) != set(started.get(rid, ())):
                    reason = 'snapshot/started population mismatch'
                else:
                    frame = pd.DataFrame([{**s['features'], 'race_id': rid, 'horse_id': s['horse_id'], 'race_date': day} for s in group])
                    frame = frame.sort_values('horse_id', kind='stable').reset_index(drop=True)
                    for col in old:
                        column = []
                        for value in frame[col]:
                            options = possible_new_values(value, old[col], new[col])
                            values += 1
                            if len(options) != 1:
                                reason = ('absent/nonfinite encoded value: ' if not options else 'ambiguous encoded value: ') + col
                                break
                            column.append(next(iter(options)))
                        if reason:
                            break
                        frame[col] = np.asarray(column, dtype=np.float64)
                    if not reason:
                        try:
                            for member in bundle.members:
                                for col in member.model.feature_cols:
                                    if col in member.model.categorical_cols:
                                        frame[col].astype('category')
                                    else:
                                        pd.to_numeric(frame[col], errors='raise')
                        except (ValueError, TypeError):
                            reason = 'invalid saved numeric/category schema'
                    if not reason:
                        included[rid] = frame
        if reason:
            excluded[rid] = reason
            reasons[reason] += 1
    assert set(included) == set(stored['rows'])
    for rid, frame in included.items():
        pd.testing.assert_frame_equal(frame, stored['rows'][rid])
    return included, excluded, dict(reasons), values


def corrected_win(base, rows, history, terms, coefficients):
    """Independent row-by-row strict-past gap/season design and log-space tilt."""
    p = np.asarray(base, dtype=float)
    lp = np.log(p)
    design = []
    for index, row in rows.iterrows():
        previous = sorted({v for v in history.loc[history.horse_id == row.horse_id, 'race_date']
                           if dt.date(2007, 1, 1) <= v < row.race_date})
        prior = np.log1p((previous[-1] - previous[-2]).days) if len(previous) >= 2 else np.nan
        gap = np.nan if pd.isna(row.days_since_last) else np.log1p(float(row.days_since_last))
        year_days = (dt.date(row.race_date.year + 1, 1, 1) - dt.date(row.race_date.year, 1, 1)).days
        angle = 2 * np.pi * ((row.race_date - dt.date(row.race_date.year, 1, 1)).days / year_days)
        female = np.nan if pd.isna(row.sex) else float(row.sex == '牝')
        values = dict(gap_log=gap, prior_gap_log=prior, female_sin=female*np.sin(angle),
                      female_cos=female*np.cos(angle), centered_logp=lp[len(design)]-lp.mean())
        design.append([values[t] for t in terms])
    z = lp + np.nan_to_num(np.asarray(design), nan=0.) @ np.asarray(coefficients)
    q = np.exp(z-z.max())
    return q/q.sum()


def ordered_heads(win, lambda2, lambda3):
    """Enumerate disjoint first, second, third positions; no production topk call."""
    p = np.asarray(win, dtype=float)
    second, third = np.zeros(len(p)), np.zeros(len(p))
    w2, w3 = p**lambda2, p**lambda3
    for first in range(len(p)):
        remaining = np.arange(len(p)) != first
        if not remaining.any():
            continue
        for runner in np.flatnonzero(remaining):
            pair = p[first] * w2[runner]/w2[remaining].sum()
            second[runner] += pair
            rest = remaining.copy(); rest[runner] = False
            if rest.any():
                third[rest] += pair * w3[rest]/w3[rest].sum()
    return np.column_stack((p, np.minimum(p+second, 1.), np.minimum(p+second+third, 1.)))


def booster_win(booster, calibrator, rows, columns, categoricals):
    x = rows[columns].copy()
    for col in columns:
        x[col] = x[col].astype('category') if col in categoricals else pd.to_numeric(x[col], errors='raise')
    score = np.asarray(booster.predict(x, raw_score=True, num_threads=1), dtype=float)
    raw = np.exp(score-score.max()); raw /= raw.sum()
    calibrated = np.clip(np.asarray(calibrator.transform(raw), dtype=float), 1e-6, 1-1e-6)
    return calibrated/calibrated.sum()


def prediction_envelope(saved, race_ids):
    expected = set(race_ids)
    assert set(saved['ids']) == set(saved['dates']) == expected
    assert set(saved['predictions']) == set(saved['lambdas']) == {'full', 'preweight'}
    for arms in saved['predictions'].values():
        assert set(arms) == {'baseline', 'candidate'}
        for predictions in arms.values():
            assert set(predictions) == expected


def run(phase):
    out = output_path(phase)
    assert not out.exists(), 'Independent evidence is append-only'
    receipt = gate()
    bundle, candidate, old, new = models(receipt)
    raw = sealed(WORK/'raw-inputs.pkl', receipt['files'])
    stored = sealed(WORK/'inputs.pkl', receipt['files'])
    rows, excluded, reasons, value_checks = reconstructed(raw, stored, old, new, bundle)
    assert excluded == {r['race_id']: r['reason'] for r in receipt['excluded']}
    assert len(rows) == receipt['n_included_races'] and len(rows)+len(excluded) == receipt['n_target_races']
    assert sum(len(r) for r in rows.values()) == receipt['n_included_horses']
    report = {'status': 'PASS', 'phase': phase, 'input_receipt_sha256': sha(WORK/'input-receipt.json'),
              'source_sha256': sha(__file__), 'n_included_races': len(rows), 'n_excluded_races': len(excluded),
              'n_encoded_values_checked': value_checks, 'exclusion_reasons': reasons,
              'methods': ['Explicit identity union plus both-unknown TE enumeration for each stored value',
                          'Exact included/excluded race sets, ordered horse IDs and converted dataframe equality'],
              'recent_results_read': False, 'database_queries': 0, 'additional_fits': 0,
              'can_adopt': False, 'eligible_for_verdict': False}
    if phase == 'predictions':
        prior_review = read(output_path('inputs'))
        assert prior_review['status'] == 'PASS' and prior_review['input_receipt_sha256'] == report['input_receipt_sha256']
        execution = read(WORK/'execution-receipt.json')
        assert execution['status'] == 'PREDICTIONS_FROZEN_BEFORE_OUTCOMES' and execution['no_outcomes_read'] is True
        verify(execution['files'])
        saved = sealed(WORK/'predictions.pkl', execution['files'])
        snapshot_paths = [p for p, digest in execution['files'].items() if digest == SNAPSHOT_SHA]
        assert len(snapshot_paths) == 1
        matrix, _ = sealed(snapshot_paths[0], execution['files'])
        history = matrix.frame[KEYS].copy()
        history['race_date'] = pd.to_datetime(history.race_date).dt.date
        assert history.race_date.max() == TRAIN_END and history.race_date.min() >= dt.date(2007, 1, 1)
        history = pd.concat([history, stored['appearances']], ignore_index=True)
        assert history.race_date.max() <= END and not history.duplicated(KEYS[:2]).any()
        frozen = read(REPRO/'run-freeze.json')
        joint = bundle.members[0]
        assert joint.id == 'joint-42' and list(joint.coefficients) == frozen['coefficients']['42']['2026']
        from horseracing_serving.predictor import SAME_DAY_WEIGHT_COLUMNS
        prediction_envelope(saved, rows)
        max_error, head_checks = 0., 0
        for regime in ('full', 'preweight'):
            stage = frozen['lambdas'][regime]['2026']
            assert saved['lambdas'][regime] == stored['lambdas'][regime] == stage
            assert stage['fit_through'] < '2026-01-01' and not stage['fallback']
            for rid, frame in rows.items():
                ids = tuple(frame.horse_id)
                assert saved['ids'][rid] == ids and saved['dates'][rid] == frame.race_date.iloc[0]
                current = frame.copy()
                if regime == 'preweight':
                    current[[c for c in SAME_DAY_WEIGHT_COLUMNS if c in current]] = np.nan
                h = history.loc[history.horse_id.isin(ids)]
                members = []
                for member in bundle.members:
                    m = member.model
                    p = booster_win(m.booster, m.calibrator, current, m.feature_cols, m.categorical_cols)
                    members.append(corrected_win(p, current, h, member.terms, member.coefficients))
                baseline = np.stack(members).mean(axis=0)
                cp = booster_win(candidate.model.booster_, candidate.calibrator, current,
                                 candidate.metadata['feature_columns'], candidate.metadata['categorical_columns'])
                cp = corrected_win(cp, current, h, joint.terms, joint.coefficients)
                mixture = baseline*(1-1/7)+cp/7
                for arm, win in (('baseline', baseline), ('candidate', mixture)):
                    expect = ordered_heads(win, stage['lambda2'], stage['lambda3'])
                    actual = np.asarray(saved['predictions'][regime][arm][rid])
                    assert actual.shape == expect.shape
                    difference = float(np.abs(actual-expect).max())
                    assert difference < 5e-12, (regime, rid, arm, difference)
                    max_error = max(max_error, difference); head_checks += 1
        report.update(execution_receipt_sha256=sha(WORK/'execution-receipt.json'),
                      input_review_sha256=sha(output_path('inputs')), max_abs_prediction_error=max_error,
                      n_race_arm_regime_checks=head_checks)
        report['methods'].append('Native saved booster and calibrator only; independent softmax, clipping, strict-past correction, mean win and exhaustive ordered topk')
        verify(execution['files'])
    verify(receipt['files'])
    with out.open('x') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, allow_nan=False); fh.write('\n')
    print(json.dumps(report, ensure_ascii=False, allow_nan=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['inputs', 'predictions'])
    run(parser.parse_args().phase)
