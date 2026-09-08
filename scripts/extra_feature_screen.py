"""127: three raw-model 2018 screens; immutable historical inputs, no adoption."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import asdict, replace
import datetime as dt
import gc
from pathlib import Path
import resource
import subprocess
import sys
import time
from unittest.mock import patch

import numpy as np
import pandas as pd
import small_gain_stack as s
from horseracing_features.pm_expectation_residual import (PM_EXPECTATION_RESIDUAL_COLUMNS,
    build_pm_expectation_residual_features)
from horseracing_features.finish_decomposition_features import (FINISH_DECOMP_COLUMNS,
    build_finish_decomposition_features)
from horseracing_features.pm_core_strength import PM_CORE_STRENGTH_COLUMNS
from horseracing_db.enums import EntryStatus, ResultStatus
import market_feature_screen as prior
from horseracing_features.loader import Frames
from horseracing_training.dataset import TrainingMatrix
from horseracing_training.calib_split import CalibSplitFactory, OofCalibratedPredictor
from horseracing_eval.dataset import population_masks
from horseracing_eval.hashing import race_set_hash
from horseracing_eval.paired import _clip_nll, _score_arm, DEFAULT_BAND_EDGES

ROOT = s.ROOT
SPEC = ROOT / 'specs/127-extra-feature-screen'
WORK = ROOT / 'artifacts/127-extra-feature-screen'
F04 = list(PM_EXPECTATION_RESIDUAL_COLUMNS)
FINISH = list(FINISH_DECOMP_COLUMNS)
WEIGHT = ['asof_weight_deviation']
ADDITIONS = F04 + FINISH + WEIGHT
NAMES = ['baseline', 'f04', 'finish_decomp', 'weight_deviation']
COUNTS = dict(zip(NAMES, [125, 131, 135, 126]))
CONTRASTS = [{'id': name, 'candidate': name, 'baseline': 'baseline'} for name in NAMES[1:]]
FEATURE_DEFINITIONS = {
    'f04': 'Existing070 six expectation-residual columns; distinct FINISHED popularity and STARTED odds populations, original min_obs/rolling/ddof unchanged',
    'finish_decomp': 'Existing088 ten-column finished-only bundle; started-field denominator; original windows, NaN propagation and best skip-NaN unchanged',
    'weight_deviation': 'Latest strictly prior source-day valid STARTED weight200..800 minus mean of at least3 earlier unambiguous valid days; latest ambiguous day yields NaN without fallback, ambiguous days excluded from mean; target day excluded',
    'F02': 'Keep all nine existing pm_core_strength columns in every arm',
}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as f:
        import json
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')


def registered_arms():
    relative = s.arm(s.load_config(), 'pruning')['drop_features']
    selected = {'baseline': [], 'f04': F04, 'finish_decomp': FINISH, 'weight_deviation': WEIGHT}
    return [{'id': n, 'drop_features': relative + [c for c in ADDITIONS if c not in selected[n]],
             'colsample_bytree': 1.0, 'columns': COUNTS[n]} for n in NAMES]


def load_config():
    cfg = s.read_json(SPEC / 'gate-config.json')
    sha = (SPEC / 'gate-config.hash.txt').read_text().strip()
    if s.p.gate_config_hash(cfg) != sha:
        raise ValueError('127 config hash changed')
    s.p.assert_confirmatory(cfg, expected_hash=sha, eval_window=cfg['eval_window'])
    s.p.assert_delta_provenance(cfg, root=ROOT)
    previous = s.load_config()
    for k in ('arms', 'bootstrap', 'seed_noise', 'min_effect_delta', 'top_noninferior', 'calibration', 'recent_guard'):
        if cfg[k] != previous[k]:
            raise ValueError(f'127 fixed recipe/gate changed: {k}')
    subgroup = {**previous['subgroup_guard'], 'critical_subgroups': ['canonical']}
    if (cfg['study_arms'] != registered_arms() or cfg['contrasts'] != CONTRASTS
        or cfg['eval_window'] != {'from': '2018-01-01', 'to': '2018-12-31', 'min_eval_days': 100}
        or cfg['smoke'] != {'eval_window': {'from': '2008-01-01', 'to': '2008-01-31', 'min_eval_days': 1}, 'n_estimators': 5, 'n_oof_blocks': 2}
        or cfg['subgroup_guard'] != subgroup or cfg['can_adopt'] is not False or cfg['eligible_for_verdict'] is not False
        or cfg['new_outer_jobs'] != {'cache_reused': 3, 'cache_absent': 0, 'booster_fits_per_outer': 8, 'max_workers': 2, 'model_threads': 1}
        or cfg['fallback'] != 'Native baseline cache required; absence or any integrity/recipe/runtime mismatch stops'
        or cfg['feature_builder_definitions'] != FEATURE_DEFINITIONS
        or cfg['training_overlap_policy'] != 'Never overlap126 and127 full-training workers'
        or cfg['screen_rule'] != {'primary_diff_strict_max': 0.0, 'top2_diff_max': .0005, 'top3_diff_max': .0005,
                                'candidate_ece_strict_max': .05, 'ece_diff_max': .001, 'ci_is_gate': False,
                                'recent_subgroup_is_gate': False}):
        raise ValueError('127 registered scope/progression changed')
    return cfg


def source_hash():
    return s.p.stable_hash({'driver': s.p.digest(__file__), '113_source': s.source_hash(),
        '122_source': prior.source_hash(), 'tests': s.p.digest(ROOT / 'scripts/tests/test_extra_feature_screen.py'),
        'spec': s.p.digest(SPEC / 'spec.md'), 'plan': s.p.digest(SPEC / 'plan.md')})


def source_state():
    _, prior_frozen = prior.verify()
    if prior_frozen['baseline']['mode'] != 'native_cache':
        raise ValueError('Original122 baseline must be certified native')
    _, _, of = s.reuse.verify_freeze(s.old)
    _, meta, nf = s.reuse.verify_freeze(s.p)
    s.verify_historical_certificate()
    s.verify()
    if (meta['source_frames_sha256'] != s.p.digest(s.p.WORK / 'source-frames.pkl')
        or meta['prepare_source_hash'] != s.p.source_hash()):
        raise ValueError('111 original Frames/build source changed')
    report_path = s.old.SPEC / 'evidence/screen-relative_ability.json'
    report = s.read_json(report_path)
    evidence_path = s.old.WORK / 'screen-relative_ability-evidence.json'
    if (report['snapshot']['run_freeze'] != of or report['snapshot']['snapshot_sha256'] != of['snapshot_sha256']
        or report['study_config_hash'] != of['config_hash'] or report['stage'] != 'screen'
        or report['candidate'] != next(c for c in s.old.load_config()['candidates'] if c['id'] == 'relative_ability')
        or report['evidence_path'] != str(evidence_path) or report['evidence_sha256'] != s.p.digest(evidence_path)
        or report['can_adopt'] is not False):
        raise ValueError('110 historical screen report provenance changed')
    evidence = s.read_json(evidence_path)
    if (evidence['candidate_recipe_hash'] != report['candidate_recipe_hash']
        or evidence['active_recipe_hash'] != report['active_recipe_hash']
        or evidence['race_id_set_hash'] != report['race_id_set_hash']
        or evidence['gate_config_hash'] != report['gate_config_hash']):
        raise ValueError('110 screen report/evidence identity mismatch')
    paths = [s.old.WORK / 'run-freeze.json', s.old.WORK / 'snapshot.pkl', s.old.WORK / 'snapshot.json',
             s.p.WORK / 'run-freeze.json', s.p.WORK / 'snapshot.pkl', s.p.WORK / 'snapshot.json',
             s.p.WORK / 'source-frames.pkl', s.p.WORK / 'baseline-equivalence.json',
             s.WORK / 'run-freeze.json', report_path, evidence_path,
             prior.WORK / 'run-freeze.json', prior.WORK / 'feature-audit.json']
    return {'files': {str(p): s.p.digest(p) for p in paths}, 'snapshot_sha256': nf['snapshot_sha256'],
            '122_baseline_certificate': prior_frozen['baseline']}


def verify():
    cfg = load_config()
    frozen = s.read_json(WORK / 'run-freeze.json')
    if (frozen['source_hash'] != source_hash() or frozen['config_hash'] != s.p.gate_config_hash(cfg)
        or frozen['runtime'] != s.runtime() or frozen['sources'] != source_state()
        or frozen['matrix_sha256'] != s.p.digest(WORK / 'matrix.pkl')
        or frozen['feature_audit_sha256'] != s.p.digest(WORK / 'feature-audit.json')):
        raise ValueError('127 frozen input/source/runtime changed')
    row = frozen['baseline']
    if row['mode'] == 'native_cache':
        if (s.p.digest(row['path']) != row['sha256']
            or s.p.digest(row['anchor_cache_path']) != row['anchor_cache_sha256']):
            raise ValueError('Original baseline cache changed')
    else:
        raise ValueError('Unregistered baseline mode')
    return cfg, frozen


def subset_frames(frames, end):
    races = frames.races.loc[pd.to_datetime(frames.races.race_date).dt.date <= end].copy()
    ids = set(races.race_id)
    return Frames(races, frames.race_horses.loc[frames.race_horses.race_id.isin(ids)].copy(),
                  frames.race_results.loc[frames.race_results.race_id.isin(ids)].copy(), frames.horses)


def build_weight_deviation(frames):
    """Strict prior-day, unambiguous valid bodyweight history; target order preserved."""
    keys = ['race_id', 'horse_id']
    dates = frames.races[['race_id', 'race_date']].copy()
    if dates.race_id.duplicated().any():
        raise ValueError('Duplicate race dates')
    dates['race_date'] = pd.to_datetime(dates.race_date).dt.normalize()
    runs = frames.race_horses[keys + ['entry_status', 'weight']].merge(dates, on='race_id', how='left', validate='many_to_one')
    if runs.duplicated(keys).any() or runs[['horse_id', 'race_date']].isna().any().any():
        raise ValueError('Missing/duplicate weight target identity')
    runs['weight'] = pd.to_numeric(runs.weight, errors='coerce').astype('float64')
    eligible = runs.loc[runs.entry_status.eq(EntryStatus.STARTED) & runs.weight.between(200, 800)]
    days = eligible.groupby(['horse_id', 'race_date'], as_index=False).agg(value=('weight', 'first'), count=('weight', 'size'))
    days.loc[days['count'] != 1, 'value'] = np.nan
    days = days.sort_values(['horse_id', 'race_date'], kind='stable')
    # Cumulative counts/sums use only unambiguous valid days. Subtract D1 itself.
    n = days['value'].notna().groupby(days.horse_id, sort=False).cumsum() - days['value'].notna()
    total = days['value'].fillna(0).groupby(days.horse_id, sort=False).cumsum() - days['value'].fillna(0)
    days[WEIGHT[0]] = (days['value'] - total / n.where(n >= 3)).astype('float64')
    targets = runs[keys + ['race_date']].copy()
    targets['_order'] = np.arange(len(targets))
    merged = pd.merge_asof(targets.sort_values('race_date', kind='stable'),
        days[['horse_id', 'race_date'] + WEIGHT].sort_values('race_date', kind='stable'),
        on='race_date', by='horse_id', direction='backward', allow_exact_matches=False)
    return merged.sort_values('_order', kind='stable')[keys + WEIGHT].reset_index(drop=True)


def feature_additions(frames):
    keys = ['race_id', 'horse_id']
    sources = [(build_pm_expectation_residual_features(frames), F04),
               (build_finish_decomposition_features(frames), FINISH), (build_weight_deviation(frames), WEIGHT)]
    out = None
    for frame, columns in sources:
        if list(frame.columns) != keys + columns or frame.duplicated(keys).any():
            raise ValueError('Builder output schema/keys changed')
        if out is None:
            out = frame
        else:
            out = out.merge(frame, on=keys, how='outer', validate='one_to_one', indicator=True, sort=False)
            if not out['_merge'].eq('both').all():
                raise ValueError('Feature builders target different horse populations')
            out = out.drop(columns='_merge')
    return out


def source_feature_audit(frames):
    raw_finish = pd.to_numeric(frames.race_results.finish_order, errors='coerce').to_numpy(dtype=float)
    if np.isinf(raw_finish).any():
        raise ValueError('Infinite raw finish_order cannot enter legacy lag history')
    keys = ['race_id', 'horse_id']
    rh = frames.race_horses.merge(frames.races[['race_id', 'race_date']], on='race_id', validate='many_to_one')
    w = pd.to_numeric(rh.weight, errors='coerce')
    valid = rh.loc[rh.entry_status.eq(EntryStatus.STARTED) & w.between(200, 800)]
    counts = valid.groupby(['horse_id', 'race_date']).size()
    rr = frames.race_results.loc[frames.race_results.result_status.eq(ResultStatus.FINISHED), keys]
    joined = rr.merge(frames.race_horses[keys + ['entry_status']], on=keys, how='left', validate='one_to_one')
    return {'weight_ambiguous_horse_days': int((counts > 1).sum()),
        'weight_ambiguous_measurements': int(counts[counts > 1].sum()),
        'finished_result_with_nonstarted_entry': int((joined.entry_status.notna() & ~joined.entry_status.eq(EntryStatus.STARTED)).sum()),
        'finished_result_without_entry': int(joined.entry_status.isna().sum()),
        'legacy_finished_population_preserved': True}


def augment_matrix(matrix, additions):
    if matrix.feature_cols != s.p.columns_from_model() + s.p.OBSERVATION_COLUMNS:
        raise ValueError('111 matrix scope changed')
    keys = ['race_id', 'horse_id']
    if list(additions.columns) != keys + ADDITIONS or set(ADDITIONS) & set(matrix.frame):
        raise ValueError('Registered addition scope/overwrite mismatch')
    if matrix.frame.duplicated(keys).any() or additions.duplicated(keys).any():
        raise ValueError('Duplicate matrix/feature keys')
    joined = matrix.frame.merge(additions, on=keys, how='left', validate='one_to_one', indicator=True, sort=False)
    if not joined['_merge'].eq('both').all():
        raise ValueError('Missing started horse addition rows')
    joined = joined.drop(columns='_merge')
    pd.testing.assert_frame_equal(joined[matrix.frame.columns], matrix.frame.reset_index(drop=True), check_exact=True)
    for c in ADDITIONS:
        x = joined[c].to_numpy(dtype=float)
        if joined[c].dtype != np.dtype('float64') or np.isinf(x).any():
            raise ValueError('Addition dtype/infinite values invalid')
        finite = x[np.isfinite(x)]
        if (c.endswith('_pct') and ((finite < 0).any() or (finite > 1).any())):
            raise ValueError('Finish percentile range invalid')
        if c in F04[:4] and ((finite < -1).any() or (finite > 1).any()):
            raise ValueError('Market expectation residual range invalid')
        if c == 'asof_pm_resid_sd5' and (finite < 0).any():
            raise ValueError('Residual standard deviation negative')
        if 'count' in c and (np.isnan(x).any() or (finite < 0).any() or (finite != np.floor(finite)).any()):
            raise ValueError('Observation count must be finite nonnegative integers')
    return TrainingMatrix(joined, matrix.feature_cols + ADDITIONS, matrix.categorical_cols.copy(), matrix.build_audit)


def arm(cfg, name):
    if name not in NAMES:
        raise ValueError('Unknown127 arm')
    return next(a for a in cfg['study_arms'] if a['id'] == name)


def make_recipe(cfg, name, smoke=False):
    return s.p.make_recipe(cfg, arm(cfg, name)['drop_features'], smoke)


def scope(matrix, cfg, name, smoke=False):
    recipe = make_recipe(cfg, name, smoke)
    if matrix.feature_cols != s.p.columns_from_model() + s.p.OBSERVATION_COLUMNS + ADDITIONS:
        raise ValueError('127 expanded matrix feature order changed')
    drops = recipe.drop_features
    if len(set(drops)) != len(drops) or not set(drops) <= set(matrix.feature_cols):
        raise ValueError('Unknown or duplicate dropped feature')
    expected = [c for c in matrix.feature_cols if c not in drops]
    scoped = s.p.LightGBMPredictor(None, drop_features=drops)._scope_columns(matrix)
    if (scoped.feature_cols != expected or len(expected) != COUNTS[name]
        or not set(PM_CORE_STRENGTH_COLUMNS) <= set(expected)):
        raise ValueError('127 effective column scope/F02 dependency changed')
    return expected


class Factory:
    def __init__(self, cfg, frozen, matrix, races, name, smoke=False):
        self.name, self.smoke = name, smoke
        self.factory = CalibSplitFactory(None, make_recipe(cfg, name, smoke),
            n_oof_blocks=2 if smoke else 8, method='isotonic', require_sufficient=not smoke)
        self.factory._shared = matrix
        self.expected_columns = scope(matrix, cfg, name, smoke)
        self.recipe_meta, self.recipe_hash = self.factory.recipe_meta, self.factory.recipe_hash
        self.identity = [frozen['matrix_sha256'], frozen['source_hash'], frozen['config_hash'], 'smoke' if smoke else 'screen']
        self.races, self.frozen = races, frozen
        if any(r.n_result_rows is None for r in races):
            raise ValueError('Frozen result completeness required')
        self.outcomes = {r.context.race_id: (r.n_result_rows, {l.horse_id for l in r.labels if l.win == 1}) for r in races}

    def fit(self, train_races, *, num_threads=None):
        year = max(r.race_date.year for r in train_races) + 1
        th = s.train_identity(train_races)
        key = s.p.stable_hash([self.identity, self.recipe_hash, th, year])
        path = WORK / 'cache' / f'{key}.pkl'
        if path.exists():
            # Smoke may share a previously checked tiny baseline; full replay has
            # a separate ReadOnlyFactory and never enters this training method.
            if not self.smoke:
                raise ValueError('Fresh fit refuses any existing cache')
            cached = s.reuse.load(path)
            check_payload(cached, key, th, self, [r for r in self.races if r.context.race_date.year == year])
            return s.p.ReplayPredictor(cached['predictions'])
        t0 = time.monotonic()
        with patch('horseracing_training.calib_split._started_all_outcomes',
                   side_effect=lambda session, ids: {i: self.outcomes[i] for i in ids if i in self.outcomes}):
            pred = self.factory.fit(train_races, num_threads=1)
        if pred._base.feature_cols_ != self.expected_columns:
            raise ValueError('Actual fitted column order differs')
        actual_params = pred._base.win_model_.params
        if actual_params != self.factory.recipe.resolved_params():
            raise ValueError('Actual fitted model parameters differ from recipe')
        valid = [r for r in self.races if r.context.race_date.year == year]
        cache = {'key': key, 'train_hash': th, 'recipe_meta': self.recipe_meta,
            'feature_columns': pred._base.feature_cols_, 'predictions': {r.context.race_id: pred.predict_race(r.context) for r in valid},
            'oof_info': pred.oof_info_, 'elapsed_seconds': time.monotonic() - t0,
            'actual_params': actual_params, 'feature_dtypes': self.dtypes()}
        check_payload(cache, key, th, self, valid)
        s.p.save_pickle(path, cache)
        print(f'FIT OK {self.name} {year} seconds={cache["elapsed_seconds"]:.1f}', flush=True)
        return s.p.ReplayPredictor(cache['predictions'])

    def dtypes(self):
        return {c: str(self.factory._shared.frame[c].dtype) for c in self.expected_columns}


def check_payload(cache, key, th, factory, races):
    # Legacy validator requires sufficient OOF; smoke explicitly permits only its
    # five-tree identity fallback while recording the real OOF metadata unchanged.
    checked = cache if not factory.smoke else {**cache, 'oof_info': {**cache['oof_info'], 'sufficient': True}}
    s.reuse.check_payload(checked, key, th, factory, races)
    if cache.get('actual_params') != factory.factory.recipe.resolved_params() or cache.get('feature_dtypes') != factory.dtypes():
        raise ValueError('Fresh cache actual parameter/dtype scope changed')


def inputs(cfg, smoke=False):
    matrix, all_races = s.reuse.load(WORK / 'matrix.pkl')
    window = cfg['smoke']['eval_window'] if smoke else cfg['eval_window']
    start, end = [dt.date.fromisoformat(window[k]) for k in ('from', 'to')]
    races = [r for r in all_races if r.context.race_date <= end]
    folds = list(s.expanding_folds(races, start.year, valid_from=start))
    if len(folds) != 1 or folds[0].valid_year != start.year:
        raise ValueError('One registered outer fold required')
    return matrix, races, folds[0]


def native_matrix(matrix):
    return TrainingMatrix(matrix.frame.drop(columns=s.p.OBSERVATION_COLUMNS + ADDITIONS),
        s.p.columns_from_model(), matrix.categorical_cols, matrix.build_audit)


def native_source(matrix, races, fold, drops):
    oc, om, of = s.reuse.verify_freeze(s.old)
    key, th, f = s.reuse.key(s.old, om, of, oc, matrix, races, fold, 'screen', drops)
    return key, th, f, s.old.WORK / 'cache' / f'{key}.pkl'


def certify_baseline(matrix, races, fold):
    drops = next(c['drop_features'] for c in s.old.load_config()['candidates'] if c['id'] == 'relative_ability')
    key, th, f, path = native_source(matrix, races, fold, drops)
    if not path.exists():
        raise ValueError('Required native baseline cache absent; no fresh fallback')
    cache = s.reuse.load(path)
    s.reuse.check_payload(cache, key, th, f, list(fold.valid))
    report = s.read_json(s.old.SPEC / 'evidence/screen-relative_ability.json')
    evidence = s.read_json(report['evidence_path'])
    import json
    if report['candidate_recipe_hash'] != f.recipe_hash or report['candidate_recipe_meta'] != json.loads(json.dumps(f.recipe_meta)):
        raise ValueError('Native125 recipe differs from original screen report')
    ak, ath, af, apath = native_source(matrix, races, fold, [])
    active = s.reuse.load(apath)
    s.reuse.check_payload(active, ak, ath, af, list(fold.valid))
    if ath != th or report['active_recipe_hash'] != af.recipe_hash or report['active_recipe_meta'] != json.loads(json.dumps(af.recipe_meta)):
        raise ValueError('Original screen anchor recipe/train mismatch')
    expected_rows = []
    for er in fold.valid:
        pop = population_masks(er)
        if pop.eligible:
            rid = er.context.race_id
            expected_rows.append((rid, str(er.context.race_date), _clip_nll(cache['predictions'][rid][pop.winner_horse_id].win),
                                  _clip_nll(active['predictions'][rid][pop.winner_horse_id].win)))
    if expected_rows != [(r['race_id'], r['race_day'], r['candidate_winner_nll'], r['active_winner_nll']) for r in evidence['rows']]:
        raise ValueError('Original screen all eligible winner NLL differs')
    cand = _score_arm(list(fold.valid), cache['predictions'], band_edges=DEFAULT_BAND_EDGES)
    act = _score_arm(list(fold.valid), active['predictions'], band_edges=DEFAULT_BAND_EDGES)
    reasons = report['gate']['reasons']
    pairs = [(cand.winner_nll, report['periods']['all']['candidate']), (act.winner_nll, report['periods']['all']['active']),
        (cand.top2_logloss - act.top2_logloss, reasons['top2_diff']), (cand.top3_logloss - act.top3_logloss, reasons['top3_diff']),
        (cand.ece_equal_width_like['ece'], reasons['cand_ece']), (act.ece_equal_width_like['ece'], reasons['act_ece'])]
    if any(a != b for a, b in pairs) or race_set_hash(r.context.race_id for r in fold.valid) != report['race_id_set_hash']:
        raise ValueError('Original screen full population/quality differs')
    return {'mode': 'native_cache', 'key': key, 'train_hash': th, 'path': str(path), 'sha256': s.p.digest(path),
        'native_recipe_hash': f.recipe_hash, 'original_receipt_exists': False,
        'anchor_cache_path': str(apath), 'anchor_cache_sha256': s.p.digest(apath),
        'all_started_three_head_probabilities_valid': True, 'all_eligible_old_losses_exact': True,
        'old_full_population_and_quality_exact': True, 'historical_outer_fit_seconds': cache['elapsed_seconds'],
        'scores': asdict(cand), 'n_eligible': len(expected_rows), 'n_races': len(fold.valid),
        'note': 'Original screen had no prefill receipt. This new certificate binds native source identity and old report/evidence; it is not a fresh125 retraining-parity claim.'}


def job_for(factory, fold):
    key, th = s.key_for(factory, fold)
    return {'key': key, 'train_hash': th, 'year': fold.valid_year, 'arm': factory.name,
            'feature_columns': factory.expected_columns, 'feature_dtypes': factory.dtypes(), 'recipe_hash': factory.recipe_hash}


def prepare():
    if (WORK / 'run-freeze.json').exists():
        verify(); return
    cfg = load_config()
    before, sources = source_hash(), source_state()
    a, ar = s.reuse.load(s.old.WORK / 'snapshot.pkl')
    b, br = s.reuse.load(s.p.WORK / 'snapshot.pkl')
    s.check_matrix_parity(a, ar, b, br)
    end, start = dt.date(2018, 12, 31), dt.date(2018, 1, 1)
    races = [r for r in br if r.context.race_date <= end]
    old_races = [r for r in ar if r.context.race_date <= end]
    folds = list(s.expanding_folds(races, 2018, valid_from=start))
    if len(folds) != 1 or folds != list(s.expanding_folds(old_races, 2018, valid_from=start)):
        raise ValueError('Source2018 actual train/fold parity failed')
    fold = folds[0]
    baseline = certify_baseline(a, old_races, fold)
    if baseline != sources['122_baseline_certificate']:
        raise ValueError('Independent127 native proof differs from original122 certificate')
    del a, ar, old_races
    gc.collect()
    frames = s.reuse.load(s.p.WORK / 'source-frames.pkl')
    source_audit = source_feature_audit(frames)
    additions = feature_additions(frames)
    prefix = feature_additions(subset_frames(frames, end))
    keys = ['race_id', 'horse_id']
    prefix_index = pd.MultiIndex.from_frame(prefix[keys])
    pd.testing.assert_frame_equal(additions.set_index(keys).loc[prefix_index].sort_index(),
                                  prefix.set_index(keys).sort_index(), check_exact=True)
    matrix = augment_matrix(b, additions)
    del frames, additions, prefix, b
    gc.collect()
    path = WORK / 'matrix.pkl'
    if path.exists():
        raise ValueError('Unfrozen matrix already exists; preserve for independent diagnosis')
    s.p.save_pickle(path, (matrix, br))
    frozen = {'source_hash': before, 'config_hash': s.p.gate_config_hash(cfg), 'runtime': s.runtime(),
        'sources': sources, 'matrix_sha256': s.p.digest(path), 'baseline': baseline,
        'artifact_kind': 'extra_feature_screen_freeze', 'can_adopt': False, 'eligible_for_verdict': False}
    factories = {n: Factory(cfg, frozen, matrix, races, n) for n in NAMES}
    frozen['arm_recipe_hashes'] = {n: f.recipe_hash for n, f in factories.items()}
    frozen['arm_columns'] = {n: f.expected_columns for n, f in factories.items()}
    # Source native drops differ because127 scopes unused additions; every other
    # effective setting and original training value must be identical.
    orig = s.old.CachedFactory(s.old.load_config(), native_matrix(matrix), races, [],
        drops=next(c['drop_features'] for c in s.old.load_config()['candidates'] if c['id'] == 'relative_ability'))
    if (orig.expected_columns != factories['baseline'].expected_columns
        or s.strip_drops(orig.recipe_meta) != s.strip_drops(factories['baseline'].recipe_meta)):
        raise ValueError('Source/target effective baseline recipe differs; no fallback')
    names = NAMES[1:]
    frozen['jobs'] = [job_for(factories[n], fold) for n in names]
    valid = list(fold.valid)
    population = [(r.context.race_id, str(r.context.race_date), population_masks(r).eligible) for r in valid]
    frozen['population'] = {'race_id_set_hash': race_set_hash(r[0] for r in population),
        'ordered_population_hash': s.p.stable_hash(population), 'n_races': len(valid),
        'n_eligible': sum(r[2] for r in population), 'n_days': len({r[1] for r in population}),
        'eligible_id_days': [[r[0], r[1]] for r in population if r[2]]}
    if (len(valid), frozen['population']['n_eligible'], frozen['population']['n_days']) != (3454, 3448, 109):
        raise ValueError('Registered2018 population differs')
    audit = {'all_shared_frame_values_exact': True, 'all_categories_and_build_audit_exact': True,
        'all_eval_races_and_folds_exact': True, 'future_prefix_features_exact': True,
        'n_matrix_rows': len(matrix.frame), 'feature_columns': matrix.feature_cols,
        'arm_columns': {n: f.expected_columns for n, f in factories.items()},
        'arm_dtypes': {n: f.dtypes() for n, f in factories.items()},
        'added_feature_missing': {c: {'all': int(matrix.frame[c].isna().sum()),
            '2018': int(matrix.frame.loc[matrix.frame.race_id.isin({r.context.race_id for r in valid}), c].isna().sum())} for c in ADDITIONS},
        'source_population_audit': source_audit,
        'feature_definitions': FEATURE_DEFINITIONS,
        'builders': {str(Path(sys.modules[func.__module__].__file__)): s.p.digest(sys.modules[func.__module__].__file__)
                     for func in (build_pm_expectation_residual_features, build_finish_decomposition_features, build_weight_deviation)}}
    write_json(WORK / 'feature-audit.json', audit)
    frozen['feature_audit_sha256'] = s.p.digest(WORK / 'feature-audit.json')
    if source_hash() != before or source_state() != sources:
        raise ValueError('Sources changed during preparation')
    write_json(WORK / 'run-freeze.json', frozen)
    verify()
    print(f'PREPARE PASS {len(names)} fresh OOF-inclusive outer jobs', flush=True)


def receipt_path(key):
    return WORK / 'prefill' / f'{key}.json'


def completed(job):
    receipt = receipt_path(job['key'])
    if not receipt.exists():
        return False
    row = s.read_json(receipt)
    if (row.get('job') != job or row.get('cache_sha256') != s.p.digest(WORK / 'cache' / f"{job['key']}.pkl")
        or row.get('run_freeze_sha256') != s.p.digest(WORK / 'run-freeze.json')
        or row.get('model_threads') != 1 or row.get('imported') is not False):
        raise ValueError('Fresh127 completion receipt changed')
    return True


def worker(key):
    check_training_overlap()
    cfg, frozen = verify()
    job = next(j for j in frozen['jobs'] if j['key'] == key)
    if completed(job):
        return
    path = WORK / 'cache' / f'{key}.pkl'
    if path.exists():
        raise ValueError('Unreceipted cache exists; preserve for independent diagnosis')
    matrix, races, fold = inputs(cfg)
    f = Factory(cfg, frozen, matrix, races, job['arm'])
    if job_for(f, fold) != job:
        raise ValueError('Frozen job train/recipe/column/dtype changed')
    try:
        f.fit([r.context for r in fold.train], num_threads=1)
        cache = s.reuse.load(path)
        check_payload(cache, key, job['train_hash'], f, list(fold.valid))
        verify()
        write_json(receipt_path(key), {'job': job, 'cache_sha256': s.p.digest(path),
            'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'model_threads': 1, 'imported': False,
            'actual_params': cache['actual_params'], 'current_outer_fit_seconds': cache['elapsed_seconds'],
            'peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)})
    except BaseException:
        if path.exists() and not receipt_path(key).exists():
            dest = WORK / 'prefill' / f'unverified-{key}-{time.time_ns()}.pkl'
            dest.parent.mkdir(parents=True, exist_ok=True)
            path.rename(dest)
        raise


def launch(job, run_id):
    log = WORK / 'prefill' / f"{run_id}-{job['key']}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('x') as output:
        subprocess.run([sys.executable, str(Path(__file__).resolve()), 'train', '--worker', job['key']],
            cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, check=True)
    if not completed(job):
        raise ValueError('Worker exited without verified receipt')


def check_training_overlap():
    if (ROOT / 'artifacts/126-market-feature-full-quality/running.lock').exists():
        raise ValueError('126 full-training lock exists;127 training overlap forbidden')


def train(workers):
    check_training_overlap()
    cfg, frozen = verify()
    smoke = s.read_json(SPEC / 'evidence/smoke.json')
    if (smoke.get('structure') != 'PASS' or smoke.get('run_freeze_sha256') != s.p.digest(WORK / 'run-freeze.json')
        or smoke.get('arm_names') != NAMES or workers not in (1, 2)):
        raise ValueError('Frozen four-arm smoke and at most2 workers required')
    jobs = [j for j in frozen['jobs'] if not completed(j)]
    lock = WORK / 'running.lock'
    lock.mkdir()
    run_id = str(time.time_ns())
    try:
        write_json(WORK / 'prefill' / f'{run_id}-run.json', {'jobs': jobs, 'workers': workers,
            'smoke_sha256': s.p.digest(SPEC / 'evidence/smoke.json'), 'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json')})
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(launch, j, run_id) for j in jobs]
            try:
                for future in as_completed(futures):
                    future.result()
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
        verify()
    finally:
        lock.rmdir()


class ReadOnlyFactory(Factory):
    def fit(self, train_races, *, num_threads=None):
        th = s.train_identity(train_races)
        year = max(r.race_date.year for r in train_races) + 1
        if year != 2018:
            raise ValueError('Only2018 registered replay')
        valid = [r for r in self.races if r.context.race_date.year == year]
        baseline = self.frozen['baseline']
        if self.name == 'baseline' and baseline['mode'] == 'native_cache':
            if th != baseline['train_hash'] or s.p.digest(baseline['path']) != baseline['sha256']:
                raise ValueError('Native baseline train/hash changed')
            native = s.old.CachedFactory(s.old.load_config(), native_matrix(self.factory._shared), self.races, [],
                drops=next(c['drop_features'] for c in s.old.load_config()['candidates'] if c['id'] == 'relative_ability'))
            if native.expected_columns != self.expected_columns or s.strip_drops(native.recipe_meta) != s.strip_drops(self.recipe_meta):
                raise ValueError('Native replay effective recipe/columns mismatch')
            cache = s.reuse.load(Path(baseline['path']))
            s.reuse.check_payload(cache, baseline['key'], th, native, valid)
        else:
            job = next(j for j in self.frozen['jobs'] if j['arm'] == self.name)
            key = s.p.stable_hash([self.identity, self.recipe_hash, th, year])
            if key != job['key'] or th != job['train_hash'] or not completed(job):
                raise ValueError('Fresh replay job/train/receipt missing')
            cache = s.reuse.load(WORK / 'cache' / f'{key}.pkl')
            check_payload(cache, key, th, self, valid)
        return s.p.ReplayPredictor(cache['predictions'])


def smoke():
    cfg, frozen = verify()
    matrix, races, fold = inputs(cfg, smoke=True)
    records = []
    for name in NAMES:
        f = Factory(cfg, frozen, matrix, races, name, smoke=True)
        f.fit([r.context for r in fold.train], num_threads=1)
        key, th = s.key_for(f, fold)
        cache = s.reuse.load(WORK / 'cache' / f'{key}.pkl')
        check_payload(cache, key, th, f, list(fold.valid))
        records.append({'arm': name, 'columns': f.expected_columns, 'feature_dtypes': f.dtypes(),
            'recipe_hash': f.recipe_hash, 'actual_params': cache['actual_params'],
            'cache_sha256': s.p.digest(WORK / 'cache' / f'{key}.pkl'), 'oof_info': cache['oof_info']})
        del f, cache
        gc.collect()
    verify()
    write_json(SPEC / 'evidence/smoke.json', {'structure': 'PASS', 'can_adopt': False, 'eligible_for_verdict': False,
        'arm_names': NAMES, 'n_estimators': 5, 'n_oof_blocks': 2, 'records': records,
        'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'note': 'Scope/parameter/three-head probability structure only; no efficacy score.'})


def progression(report):
    reasons = report['gate']['reasons']
    values = [report['periods']['all']['diff'], reasons['top2_diff'], reasons['top3_diff'], reasons['cand_ece'], reasons['act_ece']]
    if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not np.isfinite(v) for v in values):
        raise ValueError('Invalid127 screen numerical evidence')
    diff, top2, top3, cand_ece, act_ece = values
    if min(cand_ece, act_ece) < 0 or max(cand_ece, act_ece) > 1:
        raise ValueError('Invalid ECE range')
    if top2 > .0005 or top3 > .0005 or cand_ece >= .05 or cand_ece - act_ece > .001:
        return 'QUALITY_REVIEW_REQUIRED'
    return 'ADVANCE_TO_FULL_RESEARCH' if diff < 0 else 'DEFER'


def result_path(name):
    if name not in NAMES[1:]:
        raise ValueError('Unknown screen contrast')
    return SPEC / 'evidence' / f'screen-{name}.json'


def result_receipt(name):
    return WORK / f'{name}-receipt.json'


def validate_result_population(result, evidence, frozen):
    population = frozen['population']
    if (result['race_id_set_hash'] != population['race_id_set_hash']
        or evidence['race_id_set_hash'] != population['race_id_set_hash']
        or result['n_races'] != population['n_races'] or result['n_eligible'] != population['n_eligible']
        or [[r['race_id'], r['race_day']] for r in evidence['rows']] != population['eligible_id_days']):
        raise ValueError('127 report full/eligible population changed')


def validate_losses(result, evidence):
    rows = evidence['rows']
    if not rows or [r['seq'] for r in rows] != list(range(len(rows))):
        raise ValueError('127 evidence empty or sequence differs')
    for row in rows:
        values = [row[k] for k in ('candidate_winner_nll', 'active_winner_nll', 'diff')]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v) for v in values):
            raise ValueError('127 nonfinite/nonnumeric loss evidence')
        c, a, diff = values
        if c < 0 or a < 0 or diff != c - a:
            raise ValueError('127 negative loss or row difference inconsistent')
    c = float(np.mean([r['candidate_winner_nll'] for r in rows]))
    a = float(np.mean([r['active_winner_nll'] for r in rows]))
    period = result['periods']['all']
    pairs = [(period['candidate'], c), (period['active'], a), (period['diff'], c - a),
             (result['gate']['reasons']['winner_nll_diff'], c - a)]
    # Official scalar evidence uses math.log, aggregate metric uses np.log;
    # only their floating-point rounding difference is tolerated.
    if (any(isinstance(x, bool) or not isinstance(x, (int, float)) or not np.isfinite(x)
            or abs(x - y) > 1e-12 for x, y in pairs)
        or result.get('primary_loss_changed') is not any(r['diff'] != 0 for r in rows)):
        raise ValueError('127 report mean/primary-change flag differs from evidence')


def validate_result_identity(row, evidence, name, cfg, frozen):
    if (row.get('candidate_recipe_hash') != frozen['arm_recipe_hashes'][name]
        or row.get('active_recipe_hash') != frozen['arm_recipe_hashes']['baseline']
        or evidence.get('candidate_recipe_hash') != row['candidate_recipe_hash']
        or evidence.get('active_recipe_hash') != row['active_recipe_hash']
        or row.get('candidate_columns') != frozen['arm_columns'][name]
        or row.get('baseline_columns') != frozen['arm_columns']['baseline']
        or row.get('gate_config_hash') != s.p.gate_config_hash(cfg)
        or evidence.get('gate_config_hash') != s.p.gate_config_hash(cfg)):
        raise ValueError('127 report recipe/scope/gate identity changed')
    if frozen['baseline']['mode'] == 'native_cache':
        original = s.read_json(s.old.WORK / 'screen-relative_ability-evidence.json')['rows']
        if [r['active_winner_nll'] for r in evidence['rows']] != [r['candidate_winner_nll'] for r in original]:
            raise ValueError('New baseline NLL differs from native110 source')


def verified_result(name, cfg, frozen):
    path, evidence_path, receipt_path_ = result_path(name), WORK / f'{name}-evidence.json', result_receipt(name)
    present = [p.exists() for p in (path, evidence_path, receipt_path_)]
    if not any(present):
        return False
    if not all(present):
        raise ValueError('Incomplete127 report/evidence/receipt; preserve for diagnosis')
    if not all(completed(j) for j in frozen['jobs']):
        raise ValueError('Completed report requires all three valid training receipts')
    row, receipt = s.read_json(path), s.read_json(receipt_path_)
    if (receipt != {'report_sha256': s.p.digest(path), 'evidence_sha256': s.p.digest(evidence_path),
                   'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'contrast': name}
        or row.get('study_config_hash') != s.p.gate_config_hash(cfg)
        or row.get('run_freeze_sha256') != s.p.digest(WORK / 'run-freeze.json')
        or row.get('can_adopt') is not False or row.get('eligible_for_verdict') is not False
        or row.get('contrast') != next(c for c in CONTRASTS if c['id'] == name)
        or row.get('evidence_path') != str(evidence_path) or row.get('evidence_sha256') != s.p.digest(evidence_path)
        or row.get('progression') != progression(row)):
        raise ValueError('Existing127 result provenance changed')
    evidence = s.read_json(evidence_path)
    validate_result_population(row, evidence, frozen)
    validate_losses(row, evidence)
    validate_result_identity(row, evidence, name, cfg, frozen)
    return True


def evaluate():
    cfg, frozen = verify()
    if (WORK / 'running.lock').exists() or not all(completed(j) for j in frozen['jobs']):
        raise ValueError('All registered fresh jobs must finish with valid receipts')
    matrix, races, fold = inputs(cfg)
    for contrast in CONTRASTS:
        name = contrast['id']
        if verified_result(name, cfg, frozen):
            continue
        cand = ReadOnlyFactory(cfg, frozen, matrix, races, name)
        base = ReadOnlyFactory(cfg, frozen, matrix, races, 'baseline')
        report = s.p.paired_eval(cand, base, races, gate_config=cfg, first_valid_year=2018,
            valid_from=dt.date(2018, 1, 1), subgroups=True, num_threads=1,
            snapshot={'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'source_snapshot_sha256': frozen['sources']['snapshot_sha256'],
                      'evidence_regime': 'historical_development', 'contrast': contrast})
        evidence = report.evidence.to_dict()
        row = report.to_dict()
        row.pop('evidence', None); row.pop('diffs_by_day', None)
        row['gate_readout'] = row.pop('decision')
        evidence_path = WORK / f'{name}-evidence.json'
        row.update(artifact_kind='extra_feature_screen_report', stage='screen', contrast=contrast,
            can_adopt=False, eligible_for_verdict=False, evidence_regime='historical_development',
            study_config_hash=s.p.gate_config_hash(cfg), run_freeze_sha256=s.p.digest(WORK / 'run-freeze.json'),
            candidate_columns=cand.expected_columns, baseline_columns=base.expected_columns,
            evidence_path=str(evidence_path), primary_loss_changed=any(r.diff != 0 for r in report.evidence.rows))
        validate_result_population(row, evidence, frozen)
        validate_losses(row, evidence)
        validate_result_identity(row, evidence, name, cfg, frozen)
        row['progression'] = progression(row)
        verify()
        write_json(evidence_path, evidence)
        row['evidence_sha256'] = s.p.digest(evidence_path)
        write_json(result_path(name), row)
        write_json(result_receipt(name), {'report_sha256': s.p.digest(result_path(name)),
            'evidence_sha256': s.p.digest(evidence_path), 'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json'), 'contrast': name})
        print(f'RESULT {name} diff={report.periods["all"]["diff"]:+.8f} {row["progression"]}', flush=True)
        del cand, base, report
        gc.collect()
    verify()
    summary_path = SPEC / 'verdict.json'
    summary = {'artifact_kind': 'extra_feature_screen_summary', 'can_adopt': False, 'eligible_for_verdict': False,
        'run_freeze_sha256': s.p.digest(WORK / 'run-freeze.json'),
        'reports': {n: {'sha256': s.p.digest(result_path(n)), 'progression': s.read_json(result_path(n))['progression']} for n in NAMES[1:]},
        'note': 'Three independent2018 raw-model screens. ADVANCE retains only a full-quality research candidate; no selection winner, production, gap or ensemble increment claim.'}
    if summary_path.exists():
        if s.read_json(summary_path) != summary:
            raise ValueError('Existing127 summary changed')
    else:
        write_json(summary_path, summary)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('mode', choices=['prepare', 'smoke', 'train', 'evaluate'])
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--worker')
    args = ap.parse_args()
    if args.worker and args.mode != 'train':
        raise ValueError('Worker is train-only')
    if args.mode == 'prepare': prepare()
    elif args.mode == 'smoke': smoke()
    elif args.mode == 'train': worker(args.worker) if args.worker else train(args.workers)
    else: evaluate()


if __name__ == '__main__':
    main()
