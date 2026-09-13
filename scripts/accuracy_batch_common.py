"""132 research primitives: frozen inputs, immutable predictions, started-all scoring.

No DB connection or model fit is used. The only real-data source is the explicitly
pinned 111 snapshot (through 2026-08-23), plus its annual 130 prediction caches.
"""
from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import hashlib
import json
from pathlib import Path
import pickle
import sys

import numpy as np
import pandas as pd

from horseracing_eval.baselines import harville_topk
from horseracing_eval.dataset import EvalRace, population_masks
from horseracing_eval.metrics import ece_equal_mass

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'artifacts/132-accuracy-first-batch'
SOURCE = ROOT / 'artifacts/130-mixture-preweight-walkforward'
SNAPSHOT = ROOT / 'artifacts/111-ability-observation/snapshot.pkl'
SNAPSHOT_SHA256 = '58397ba94dc685c6e49b509619fb447106702d869adebc38889b021f3be6859a'
CUTOFF = dt.date(2026, 9, 6)
YEARS = tuple(range(2020, 2027))
MEMBER_IDS = tuple(f'{branch}-{seed}' for branch in ('joint', 'anchor') for seed in (42, 43, 44))
HEADS = ('win', 'top2', 'top3')
sys.path.insert(0, str(ROOT / 'serving/src'))
from horseracing_serving import mixture_correction as mc  # noqa: E402


def digest(path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def stable_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str,
                                     allow_nan=False).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, default=str, allow_nan=False) + '\n'
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as fh:
        fh.write(text)


def validate_dates(values, *, where='dates'):
    dates = pd.to_datetime(pd.Series(values), errors='raise').reset_index(drop=True)
    if dates.empty or dates.isna().any() or dates.dt.tz is not None:
        raise ValueError(f'{where}: missing/empty/timezone dates')
    if not dates.eq(dates.dt.normalize()).all():
        raise ValueError(f'{where}: non-calendar dates')
    if dates.max().date() > CUTOFF or dates.min().date() < dt.date(2007, 1, 1):
        raise ValueError(f'{where}: outside allowed real-data period')
    return dates


def validate_heads(heads, n=None):
    x = np.asarray(heads, dtype=np.float64)
    if x.ndim != 2 or x.shape[1] != 3 or len(x) == 0 or (n is not None and len(x) != n):
        raise ValueError('Probability head shape differs')
    if (not np.isfinite(x).all() or (x < 0).any() or (x > 1 + 1e-10).any()
            or (x[:, 0] <= 0).any() or (np.diff(x, axis=1) < -1e-10).any()
            or not np.allclose(x.sum(axis=0), [min(k, len(x)) for k in (1, 2, 3)], atol=1e-8, rtol=0)):
        raise ValueError('Probability domain/order/sums differ')
    return x


def heads_from_win(win, *, lambda2=1., lambda3=1.):
    win = np.asarray(win, dtype=float)
    if (win.ndim != 1 or not len(win) or not np.isfinite(win).all() or (win <= 0).any()
            or (win > 1).any() or not np.isclose(win.sum(), 1., atol=1e-8, rtol=0)):
        raise ValueError('Invalid win vector')
    top2, top3 = harville_topk(win.tolist(), lambda2=lambda2, lambda3=lambda3)
    return validate_heads(np.column_stack((win, top2, top3)))


@dataclass
class FrozenInputs:
    matrix: object
    races: list[EvalRace]
    freeze130: dict
    provenance: dict


@dataclass
class MixedRace:
    er: EvalRace
    ids: tuple[str, ...]
    members: np.ndarray
    heads: np.ndarray
    correction_inputs: pd.DataFrame

    @property
    def race_id(self): return self.er.context.race_id

    @property
    def race_date(self): return self.er.context.race_date

    @property
    def year(self): return self.race_date.year


def load_frozen_inputs() -> FrozenInputs:
    meta_path = SNAPSHOT.with_suffix('.json')
    meta = read_json(meta_path)
    # Validate provenance BEFORE unpickling real values. Never discover latest files/DB.
    through = dt.date.fromisoformat(meta['data_through'])
    if through > CUTOFF or meta['snapshot_sha256'] != SNAPSHOT_SHA256:
        raise ValueError('Snapshot metadata violates pinned cutoff/identity')
    actual = digest(SNAPSHOT)
    if actual != SNAPSHOT_SHA256:
        raise ValueError('Snapshot SHA mismatch')
    freeze_path = SOURCE / 'run-freeze.json'
    frozen = read_json(freeze_path)
    if (frozen['snapshot_sha256'] != actual or frozen['config']['years'] != list(YEARS)
            or set(frozen['coefficients']) != set(MEMBER_IDS)):
        raise ValueError('130 freeze identity differs')
    for member in MEMBER_IDS:
        coefficients = frozen['coefficients'][member]
        if set(coefficients) != {str(y) for y in YEARS}:
            raise ValueError('Annual coefficient years differ')
        for beta in coefficients.values():
            if len(beta) != (5 if member.startswith('joint') else 1) or not np.isfinite(beta).all():
                raise ValueError('Invalid annual coefficients')
    with SNAPSHOT.open('rb') as fh:
        matrix, races = pickle.load(fh)
    frame = matrix.frame
    dates = validate_dates(frame.race_date, where='snapshot matrix')
    race_dates = validate_dates([r.context.race_date for r in races], where='snapshot labels')
    if dates.max().date() != through or race_dates.max().date() != through:
        raise ValueError('Actual snapshot end differs from metadata')
    if len(frame) != meta['n_rows'] or len(races) != meta['n_races']:
        raise ValueError('Snapshot population count differs')
    if frame.duplicated(['race_id', 'horse_id']).any():
        raise ValueError('Duplicate matrix identities')
    by_id = {r.context.race_id: r for r in races}
    matrix_ids = set(frame.race_id)
    if len(by_id) != len(races) or not set(by_id).issubset(matrix_ids):
        raise ValueError('Matrix/label race identities differ')
    if (frame.groupby('race_id', sort=False).race_date.nunique() > 1).any():
        raise ValueError('Conflicting matrix race dates')
    expected_dates = frame.race_id.map({rid: pd.Timestamp(r.context.race_date) for rid, r in by_id.items()})
    expected_dates = expected_dates.reset_index(drop=True)
    # One historical started race has no finished labels in 111. It remains valid
    # strict-prior history; all dates above are bounded, and only EvalRace rows score.
    shared = expected_dates.notna()
    if not dates[shared].eq(expected_dates[shared]).all():
        raise ValueError('Matrix/label dates differ')
    for er in races:
        ids = [h.horse_id for h in er.context.started_horses]
        if not ids or len(ids) != len(set(ids)) or er.n_result_rows is None:
            raise ValueError('Unknown result coverage or ambiguous started field')
    return FrozenInputs(matrix, list(races), frozen, {
        'allowed_data_through': str(CUTOFF), 'actual_data_through': str(through),
        'snapshot_path': str(SNAPSHOT), 'snapshot_sha256': actual,
        'snapshot_metadata_sha256': digest(meta_path), 'freeze130_sha256': digest(freeze_path),
        'matrix_only_unscored_race_ids': sorted(matrix_ids - set(by_id)),
        'additional_booster_fits': 0, 'cache_sha256': {},
    })


def _load_annual_cache(inputs, member, year, year_races):
    path = SOURCE / 'cache' / f'{member}-{year}.pkl'
    receipt_path = SOURCE / 'receipts' / f'{member}-{year}.json'
    receipt = read_json(receipt_path)
    sha = digest(path)
    if (receipt.get('cache_sha256') != sha or receipt.get('source_hash') != inputs.freeze130['source_hash']
            or receipt.get('member') != member or receipt.get('year') != year or receipt.get('smoke') is not False):
        raise ValueError(f'130 cache receipt mismatch: {member}-{year}')
    with path.open('rb') as fh:
        cache = pickle.load(fh)
    if (cache['member'] != member or cache['year'] != year or cache['smoke'] is not False
            or cache['n_valid'] != len(year_races) or not cache['oof_info']['sufficient']
            or set(cache['predictions']) != {'full', 'preweight'}):
        raise ValueError('130 cache payload metadata differs')
    expected = {r.context.race_id for r in year_races}
    for predictions in cache['predictions'].values():
        if set(predictions) != expected:
            raise ValueError('130 cache race population differs')
        for er in year_races:
            ids = tuple(h.horse_id for h in er.context.started_horses)
            heads = predictions[er.context.race_id]
            if tuple(heads) != ids:
                raise ValueError('130 cache horse order differs')
            validate_heads(list(heads.values()), len(ids))
    inputs.provenance['cache_sha256'][str(path)] = sha
    return cache


def iter_mixed_records(inputs: FrozenInputs, regime='full'):
    if regime not in ('full', 'preweight'):
        raise ValueError('Unknown prediction regime')
    frame = inputs.matrix.frame
    years = pd.to_datetime(frame.race_date).dt.year
    target = frame.loc[years.isin(YEARS), mc.KEYS + ['days_since_last', 'sex']]
    corrections = mc.build_correction_inputs(target, frame[mc.KEYS])
    per_race = {rid: g.set_index('horse_id', drop=False)
                for rid, g in corrections.groupby('race_id', sort=False)}
    for year in YEARS:
        year_races = [er for er in inputs.races if er.context.race_date.year == year]
        frozen_pop = inputs.freeze130['population'][str(year)]
        if len(year_races) != frozen_pop['n_valid_races']:
            raise ValueError('130 annual population differs')
        caches = {member: _load_annual_cache(inputs, member, year, year_races) for member in MEMBER_IDS}
        for er in year_races:
            ids = tuple(h.horse_id for h in er.context.started_horses)
            corr = per_race[er.context.race_id].loc[list(ids)].reset_index(drop=True)
            members = []
            for member in MEMBER_IDS:
                saved = caches[member]['predictions'][regime][er.context.race_id]
                base_p = np.array([saved[h][0] for h in ids])
                terms = mc.JOINT_TERMS if member.startswith('joint') else ['gap_log']
                beta = inputs.freeze130['coefficients'][member][str(year)]
                corrected = mc.correct_member_predictions(ids, base_p, corr, terms, beta)
                members.append(np.array([[corrected[h].win, corrected[h].top2, corrected[h].top3] for h in ids]))
            members = np.stack(members)
            heads = validate_heads(members.mean(axis=0), len(ids))
            yield MixedRace(er, ids, members, heads, corr)


def _scoring_records(records, predictions):
    records = list(records)
    if set(predictions) != {r.race_id for r in records} or len(predictions) != len(records):
        raise ValueError('Scoring race population differs')
    for r in records:
        if r.race_date > CUTOFF:
            raise ValueError('Scoring beyond allowed cutoff')
        if r.ids != tuple(h.horse_id for h in r.er.context.started_horses):
            raise ValueError('Scoring horse order differs')
        p = validate_heads(predictions[r.race_id], len(r.ids))
        pop = population_masks(r.er)
        y = np.array([[getattr(pop, f'started_{head}')[h] for head in HEADS] for h in r.ids], dtype=int)
        yield r, p, pop, y


def summarize_predictions(records, predictions):
    scores, labels, nll = [], [], []
    n_complete = n_races = n_days = 0
    days, race_dates = set(), []
    for r, p, pop, y in _scoring_records(records, predictions):
        n_races += 1
        days.add(str(r.race_date)); race_dates.append(str(r.race_date))
        if pop.complete_results:
            n_complete += 1
            scores.append(p); labels.append(y)
        if pop.eligible:
            nll.append(-np.log(p[r.ids.index(pop.winner_horse_id), 0]))
    out = {'n_races': n_races, 'n_days': len(days), 'n_complete_races': n_complete,
           'n_incomplete_races': n_races - n_complete, 'n_eligible_races': len(nll),
           'from': min(race_dates) if race_dates else None, 'to': max(race_dates) if race_dates else None,
           'winner_nll': float(np.mean(nll)) if nll else None, 'heads': {}}
    if scores:
        p, y = np.concatenate(scores), np.concatenate(labels)
        clip = np.clip(p, 1e-15, 1 - 1e-15)
        loss = -(y * np.log(clip) + (1 - y) * np.log1p(-clip))
        for j, head in enumerate(HEADS):
            out['heads'][head] = {'logloss': float(loss[:, j].mean()),
                'brier': float(((p[:, j] - y[:, j]) ** 2).mean()),
                'ece': ece_equal_mass(p[:, j], y[:, j])['ece'], 'n_rows': len(p)}
    return out


def _day_statistics(records, predictions):
    metrics = {name: {} for name in ('winner_nll', 'top2_logloss', 'top3_logloss', 'top2_brier', 'top3_brier')}
    counts = {name: {'n_races': 0, 'n_rows': 0} for name in metrics}
    for r, p, pop, y in _scoring_records(records, predictions):
        day = str(r.race_date)
        values = {}
        if pop.eligible:
            values['winner_nll'] = (-float(np.log(p[r.ids.index(pop.winner_horse_id), 0])), 1)
        if pop.complete_results:
            q = np.clip(p, 1e-15, 1 - 1e-15)
            loss = -(y * np.log(q) + (1 - y) * np.log1p(-q))
            for j, head in ((1, 'top2'), (2, 'top3')):
                values[f'{head}_logloss'] = (float(loss[:, j].sum()), len(p))
                values[f'{head}_brier'] = (float(((p[:, j] - y[:, j]) ** 2).sum()), len(p))
        for metric, (total, n) in values.items():
            acc = metrics[metric].setdefault(day, [0., 0])
            acc[0] += total; acc[1] += n
            counts[metric]['n_races'] += 1; counts[metric]['n_rows'] += n
    return metrics, counts


def paired_metrics(records, baseline, candidate, *, b=4000, seed=20260907, alpha=.0125):
    if b < 1 or not 0 < alpha < 1:
        raise ValueError('Invalid bootstrap configuration')
    records = list(records)
    base, counts = _day_statistics(records, baseline)
    cand, candidate_counts = _day_statistics(records, candidate)
    if counts != candidate_counts:
        raise ValueError('Paired outcome population differs')
    out = {}
    for metric in base:
        days = sorted(base[metric])
        if days != sorted(cand[metric]):
            raise ValueError('Paired day population differs')
        if not days:
            out[metric] = {'point': None, 'ci': None, 'n_days': 0, **counts[metric]}
            continue
        den = np.array([base[metric][day][1] for day in days], dtype=float)
        if not np.array_equal(den, [cand[metric][day][1] for day in days]):
            raise ValueError('Paired ratio denominators differ')
        num = np.array([cand[metric][day][0] - base[metric][day][0] for day in days])
        rng = np.random.default_rng(seed)
        reps = np.empty(b)
        for start in range(0, b, 128):
            take = rng.integers(0, len(days), size=(min(128, b-start), len(days)))
            reps[start:start+len(take)] = num[take].sum(axis=1) / den[take].sum(axis=1)
        out[metric] = {'point': float(num.sum() / den.sum()),
                       'ci': np.quantile(reps, [alpha / 2, 1 - alpha / 2]).tolist(),
                       'n_days': len(days), **counts[metric]}
    return out
