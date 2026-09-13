"""135 recent confirmation with dated encoded inputs and separate outcome access.

No training entry exists. capture requires completed Step2 and its independent
review. predict seals source/models/input/predictions before score may read labels.
No mutable horse master, raw rider IDs, market values or latest feature builder.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import pickle
import sys

import numpy as np
import pandas as pd

import accuracy_batch_common as common
from horseracing_eval.dataset import EvalRace, ScoringLabel
from horseracing_eval.predictor import HorseEntry, RaceContext
from horseracing_serving import mixture_correction as mc
from horseracing_serving.mixture_model import load_mixture_bundle, predict_base
from horseracing_serving.predictor import SAME_DAY_WEIGHT_COLUMNS
from horseracing_training.predictor import assemble_predictions

ROOT = common.ROOT
SPEC = ROOT / 'specs/135-mixture-reproducibility'
WORK = ROOT / 'artifacts/135-mixture-reproducibility/recent'
REPRO = ROOT / 'artifacts/135-mixture-reproducibility/repro'
OLD = ROOT / 'artifacts/model_versions/lgbm-094-cap900'
BUNDLE = ROOT / 'artifacts/129-candidate-mixture-serving/bundle.json'
START, CUTOFF, TRAIN_END = dt.date(2026, 8, 29), dt.date(2026, 9, 6), dt.date(2026, 8, 23)
JST = dt.timezone(dt.timedelta(hours=9))
EXCLUSIVE_END = dt.datetime(2026, 9, 7, tzinfo=JST)
REGIMES = ('full', 'preweight')
LOCK_SHA = 'e4c5e9ffee2aea66091cfc80658a47e68a9f344ae38a4ec47dd6762741517dc8'
PARAMS = {'start': START, 'end': CUTOFF, 'history_start': TRAIN_END + dt.timedelta(days=1),
          'captured_before': EXCLUSIVE_END, 'old_model': 'lgbm-094-cap900', 'feature_version': 'features-021'}
digest, stable_hash, read_json, write_json = common.digest, common.stable_hash, common.read_json, common.write_json

# MATERIALIZED scopes prevent any other event dates entering joins. Only metadata
# is selected here. Run/snapshot timestamps must precede post time and JST cutoff.
RUN_SELECTION_SQL = """
WITH target AS MATERIALIZED (
 SELECT race_id, race_date, post_time FROM races
 WHERE race_date >= :start AND race_date <= :end
), eligible AS (
 SELECT p.race_id, p.prediction_run_id, p.computed_at, p.created_at, p.updated_at,
        r.race_date, r.post_time, count(s.horse_id) AS n_snapshots
 FROM target r JOIN prediction_runs p ON p.race_id = r.race_id
 JOIN feature_snapshots s ON s.prediction_run_id = p.prediction_run_id
 WHERE p.model_version = :old_model
 AND p.computed_at < r.post_time AND p.created_at < r.post_time AND p.updated_at < r.post_time
 AND p.computed_at < :captured_before AND p.created_at < :captured_before AND p.updated_at < :captured_before
 GROUP BY p.race_id,p.prediction_run_id,p.computed_at,p.created_at,p.updated_at,r.race_date,r.post_time
 HAVING bool_and(s.feature_version = :feature_version
   AND s.created_at < r.post_time AND s.updated_at < r.post_time
   AND s.created_at < :captured_before AND s.updated_at < :captured_before)
), picked AS (
 SELECT DISTINCT ON (race_id) * FROM eligible
 ORDER BY race_id, computed_at DESC, prediction_run_id
)
SELECT r.race_id,r.race_date,r.post_time,p.prediction_run_id,p.computed_at,
       p.created_at,p.updated_at,p.n_snapshots
FROM target r LEFT JOIN picked p USING(race_id)
ORDER BY r.race_date,r.race_id
"""
SNAPSHOT_SQL = """
WITH target AS MATERIALIZED (
 SELECT race_id,race_date,post_time FROM races WHERE race_date >= :start AND race_date <= :end
)
SELECT r.race_id,r.race_date,p.prediction_run_id,s.horse_id,s.feature_version,s.features,
       p.computed_at,p.created_at AS run_created_at,p.updated_at AS run_updated_at,
       s.created_at AS snapshot_created_at,s.updated_at AS snapshot_updated_at,r.post_time
FROM target r JOIN prediction_runs p ON p.race_id=r.race_id
JOIN feature_snapshots s ON s.prediction_run_id=p.prediction_run_id
WHERE CAST(p.prediction_run_id AS text) = ANY(:run_ids)
AND p.model_version=:old_model AND s.feature_version=:feature_version
AND p.computed_at < r.post_time AND p.created_at < r.post_time AND p.updated_at < r.post_time
AND s.created_at < r.post_time AND s.updated_at < r.post_time
AND p.computed_at < :captured_before AND p.created_at < :captured_before AND p.updated_at < :captured_before
AND s.created_at < :captured_before AND s.updated_at < :captured_before
ORDER BY r.race_date,r.race_id,s.horse_id
"""
APPEARANCE_SQL = """
WITH target AS MATERIALIZED (
 SELECT race_id,race_date FROM races WHERE race_date >= :history_start AND race_date <= :end
)
SELECT r.race_id,r.race_date,h.horse_id
FROM target r JOIN race_horses h ON h.race_id=r.race_id
WHERE h.entry_status='started' ORDER BY r.race_date,r.race_id,h.horse_id
"""
RESULT_SQL = """
WITH target AS MATERIALIZED (
 SELECT race_id,race_date FROM races WHERE race_date >= :start AND race_date <= :end
 AND race_id=ANY(:race_ids)
)
SELECT r.race_id,r.race_date,o.horse_id,o.result_status,o.finish_order
FROM target r JOIN race_results o ON o.race_id=r.race_id
ORDER BY r.race_date,r.race_id,o.horse_id
"""


class InputExclusion(ValueError):
    """Registered deterministic whole-race input exclusion, never score-based."""


def assert_hashes(files):
    for name, expected in files.items():
        path = Path(name)
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise ValueError('Frozen source/input changed: ' + str(path))


def dump(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as fh:
        pickle.dump(value, fh, protocol=5); fh.flush(); os.fsync(fh.fileno())


def load(path):
    with Path(path).open('rb') as fh:
        return pickle.load(fh)


def canonical(value):
    return json.loads(json.dumps(value, default=str, allow_nan=False))


def validate_day(day, *, history=False):
    if type(day) is not dt.date or not (dt.date(2007,1,1) if history else START) <= day <= CUTOFF:
        raise ValueError('Race date outside registered boundary')


def timestamp_eligible(computed, created, updated, post):
    values = (computed, created, updated, post)
    return all(isinstance(v, dt.datetime) and v.utcoffset() is not None for v in values) and all(
        v < post and v < EXCLUSIVE_END for v in (computed, created, updated))


def _encoder_state(enc):
    if not isinstance(enc.col, str) or not isinstance(enc.mapping, dict):
        raise ValueError('Invalid target encoder')
    values = [enc.prior, enc.smoothing, *enc.mapping.values()]
    if not all(isinstance(x, (int,float,np.number)) and not isinstance(x, (bool,np.bool_))
               and np.isfinite(x) for x in values):
        raise ValueError('Invalid encoder numeric domain')
    return enc.col, float(enc.prior), float(enc.smoothing), dict(enc.mapping)


def assert_same_encoders(encoder_sets):
    if not encoder_sets or not encoder_sets[0]:
        raise ValueError('Missing encoder columns')
    first = {c: _encoder_state(e) for c,e in encoder_sets[0].items()}
    if any(c != state[0] for c,state in first.items()):
        raise ValueError('Encoder column metadata differs')
    for encoders in encoder_sets[1:]:
        if {c: _encoder_state(e) for c,e in encoders.items()} != first:
            raise ValueError('Target encoders differ across baseline/candidate')


def build_conversion(old, new):
    """Exact old float -> all possible new floats, including unknown categories.

    Inverting to a guessed ID is forbidden. New-only IDs and an ID absent in
    both encoders belong to the old-prior group even if no old ID maps there.
    """
    if set(old) != set(new) or not old:
        raise ValueError('Old/new target-encoding columns differ')
    result = {}
    for col in old:
        _, oldprior, _, oldmap = _encoder_state(old[col])
        _, newprior, _, newmap = _encoder_state(new[col])
        if old[col].col != col or new[col].col != col:
            raise ValueError('Encoder column mismatch')
        groups = defaultdict(set)
        for identity in oldmap.keys() | newmap.keys():
            groups[float(oldmap.get(identity, oldprior))].add(float(newmap.get(identity, newprior)))
        groups[oldprior].add(newprior)
        result[col] = {key: frozenset(values) for key, values in groups.items()}
    return result


def conversion_report(conversion):
    return {col: {'old_value_groups': len(groups),
                  'unique_new_value_groups': sum(len(v) == 1 for v in groups.values()),
                  'ambiguous_groups': sum(len(v) != 1 for v in groups.values()),
                  'exact_float_only': True, 'unknown_fallback_included': True}
            for col, groups in conversion.items()}


def convert_encoded(rows, conversion):
    out = rows.copy()
    for col, groups in conversion.items():
        if col not in out:
            raise InputExclusion('missing encoded column: ' + col)
        converted = []
        for value in out[col]:
            if (not isinstance(value, (int, float, np.number)) or isinstance(value, (bool,np.bool_))
                or not np.isfinite(value) or float(value) not in groups):
                raise InputExclusion('absent/nonfinite encoded value: ' + col)
            choices = groups[float(value)]
            if len(choices) != 1:
                raise InputExclusion('ambiguous encoded value: ' + col)
            converted.append(next(iter(choices)))
        out[col] = np.asarray(converted, dtype=np.float64)
    return out


def reconstruct_race(rows, started_ids, feature_cols, conversion):
    if (rows.columns.duplicated().any() or not {'race_id','race_date','horse_id',*feature_cols}.issubset(rows)
        or rows.empty or rows.horse_id.duplicated().any() or rows.horse_id.isna().any()):
        raise InputExclusion('missing/duplicate snapshot columns or IDs')
    if not started_ids or len(set(started_ids)) != len(started_ids) or set(rows.horse_id) != set(started_ids):
        raise InputExclusion('snapshot/started population mismatch')
    if rows.race_id.nunique() != 1 or rows.race_date.nunique() != 1:
        raise InputExclusion('ambiguous race identity')
    validate_day(rows.race_date.iloc[0])
    return convert_encoded(rows.sort_values('horse_id', kind='stable').reset_index(drop=True), conversion)


def regime_rows(rows, regime):
    if regime not in REGIMES:
        raise ValueError('Unregistered recent regime')
    out = rows.copy()
    if regime == 'preweight':
        out[[c for c in SAME_DAY_WEIGHT_COLUMNS if c in out]] = np.nan
    return out


def encoded_frame(rows, feature_cols, categorical_cols):
    """Explicit dtype preparation, without applying an encoder or inventing IDs."""
    if rows.columns.duplicated().any() or not set(feature_cols).issubset(rows):
        raise ValueError('Missing encoded model columns')
    x = rows[feature_cols].copy()
    for col in feature_cols:
        x[col] = x[col].astype('category') if col in categorical_cols else pd.to_numeric(x[col], errors='raise')
    x.index = pd.Index(rows.horse_id)
    return x


def prediction_array(predictions, ids):
    if list(predictions) != list(ids):
        raise ValueError('Prediction horse order differs')
    return common.validate_heads([[p.win,p.top2,p.top3] for p in predictions.values()], len(ids))


def predict_serving_encoded(model, context, x):
    """Pure129 adapter. TE was proved externally; never call enc.transform here."""
    validate_day(context.race_date)
    ids = tuple(h.horse_id for h in context.started_horses)
    if (not ids or len(ids) != len(set(ids)) or tuple(x.index) != ids
        or list(x.columns) != model.feature_cols):
        raise ValueError('Already-encoded feature identity/order differs')
    for col in x:
        if col in model.categorical_cols:
            if not isinstance(x[col].dtype, pd.CategoricalDtype):
                raise ValueError('Encoded category dtype differs')
        elif not pd.api.types.is_numeric_dtype(x[col].dtype):
            raise ValueError('Encoded numeric dtype differs')
    if np.isinf(x.select_dtypes(include='number').to_numpy(dtype=float)).any():
        raise ValueError('Infinite encoded input')
    raw = np.asarray(model.raw_predict(x), dtype=float)
    calibrated = np.asarray(model.calibrator.transform(raw), dtype=float)
    if (raw.shape != (len(ids),) or calibrated.shape != raw.shape
        or not np.isfinite(raw).all() or not np.isfinite(calibrated).all()):
        raise ValueError('Invalid raw/calibrated prediction')
    return prediction_array(assemble_predictions(list(ids), calibrated, eps=1e-6), ids)


def gate():
    """No recent value may be queried until all immutable Step2 bindings pass."""
    lock_path = SPEC/'evidence/recent-lock.json'
    if digest(lock_path) != LOCK_SHA:
        raise ValueError('Recent protocol lock changed')
    lock, cfg = read_json(lock_path), read_json(SPEC/'experiment.json')
    if (lock['experiment_sha256'] != digest(SPEC/'experiment.json')
        or lock['recent'] != cfg['recent'] or lock['cutoff'] != str(CUTOFF)
        or lock['can_adopt'] is not False or lock['eligible_for_verdict'] is not False):
        raise ValueError('Recent experiment/lock differs')
    summary_path, freeze_path = REPRO/'summary.json', REPRO/'run-freeze.json'
    review_path = SPEC/'evidence/repro-independent-review.json'
    summary, review = read_json(summary_path), read_json(review_path)
    if (summary['status'] != 'RESEARCH_COMPLETE' or summary['data_through'] != str(TRAIN_END)
        or summary['freeze_sha256'] != digest(freeze_path)
        or summary['prediction_sha256'] != digest(REPRO/'mixture-predictions.pkl')
        or summary['can_adopt'] is not False or summary['eligible_for_verdict'] is not False
        or review['status'] != 'PASS' or review['summary_sha256'] != digest(summary_path)
        or review['freeze_sha256'] != digest(freeze_path)
        or review['can_adopt'] is not False or review['eligible_for_verdict'] is not False):
        raise ValueError('Step2 completion/independent review gate failed')
    return {str(p): digest(p) for p in (lock_path,SPEC/'experiment.json',summary_path,freeze_path,review_path)}


@contextmanager
def read_transaction():
    """Credentials are used for connection only and never included in artifacts."""
    from sqlalchemy import create_engine, text
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise ValueError('DATABASE_URL must be supplied by the caller')
    engine = create_engine(url, isolation_level='REPEATABLE READ', hide_parameters=True)
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(text('SET TRANSACTION READ ONLY'))
            connection.execute(text('SET LOCAL statement_timeout = 30000'))
            yield connection
    finally:
        engine.dispose()


def select(connection, sql, extra=None):
    from sqlalchemy import text
    return [dict(row) for row in connection.execute(text(sql), {**PARAMS, **(extra or {})}).mappings()]


def source_files():
    # Deliberately exclude any unrelated/externally replaced calibration runner.
    import mixture_reproducibility as repro
    paths = [Path(__file__).resolve(), Path(repro.__file__).resolve(), Path(common.__file__).resolve()]
    for package in ('db','features','training','eval','probability','serving'):
        paths.extend((ROOT/package/'src').rglob('*.py'))
    return {str(p): digest(p) for p in sorted(set(paths))}


def preserve_sources(files):
    copies = {}
    for name, expected in files.items():
        path = Path(name); data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('Source changed before preservation')
        destination = WORK/'source'/path.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open('xb') as fh:
            fh.write(data); fh.flush(); os.fsync(fh.fileno())
        copies[str(destination)] = expected
    return copies


def load_models():
    """Only pinned historical model artifacts; missing candidate never triggers fit."""
    import mixture_reproducibility as repro
    inventory_path = SPEC/'evidence/recent-input-inventory.json'
    inventory = read_json(inventory_path)
    assert_hashes(inventory['model_files'])
    bundle = load_mixture_bundle(BUNDLE, expected_sha256=inventory['model_files'][str(BUNDLE)])
    job = REPRO/'jobs/recent-42'
    receipt = read_json(job/'receipt.json')
    frozen = read_json(REPRO/'run-freeze.json')
    if (receipt['job'] != 'recent-42' or receipt['freeze_sha256'] != digest(REPRO/'run-freeze.json')
        or receipt['recent_inputs_loaded'] is not False or receipt['booster_fits'] != 8
        or receipt['roundtrip']['status'] != 'PASS' or receipt['roundtrip']['max_abs_diff'] != 0
        or receipt['prediction_sha256'] != digest(job/'predictions.pkl')
        or receipt['can_adopt'] is not False or receipt['eligible_for_verdict'] is not False):
        raise ValueError('Recent fixed model receipt differs')
    candidate = repro.load_model(job/'model', receipt['model_files'])
    recent_job = frozen['recent_job']
    if (candidate.metadata['seed'] != 42 or candidate.metadata['train_through'] != str(TRAIN_END)
        or candidate.metadata['train_hash'] != recent_job['train_hash']
        or candidate.metadata['recipe'] != recent_job['recipe']
        or candidate.metadata['oof_partition'] != recent_job['oof']
        or candidate.metadata['kind'] != 'recent-model-only' or candidate.metadata['smoke'] is not False
        or candidate.metadata['snapshot_sha256'] != common.SNAPSHOT_SHA256
        or candidate.metadata['freeze_sha256'] != digest(REPRO/'run-freeze.json')):
        raise ValueError('Candidate seed or training endpoint differs')
    with (OLD/'preprocessor.pkl').open('rb') as fh:
        old = pickle.load(fh)
    encoder_sets = [m.model.encoders for m in bundle.members] + [candidate.encoders]
    assert_same_encoders(encoder_sets)
    conversion = build_conversion(old['encoders'], encoder_sets[0])
    old_columns = old.get('feature_cols')
    if not old_columns:
        with (OLD/'model.txt').open() as fh:
            old_columns = next(line.split('=',1)[1].strip().split() for line in fh if line.startswith('feature_names='))
    full = next(m.model.feature_cols for m in bundle.members if m.id == 'anchor-42')
    if set(old_columns) != set(full):
        raise ValueError('094 saved feature scope does not cover baseline138')
    lambdas = {r: frozen['lambdas'][r]['2026'] for r in REGIMES}
    if (any(member.model.metadata['train_through'] != str(TRAIN_END) for member in bundle.members)
        or list(bundle.members[0].coefficients) != frozen['coefficients']['42']['2026']):
        raise ValueError('Baseline training endpoint or joint42/125 coefficient transfer differs')
    for stage in lambdas.values():
        if stage['fallback'] or dt.date.fromisoformat(stage['fit_through']) >= dt.date(2026,1,1):
            raise ValueError('Recent stage lambda is not prior2026 baseline fit')
    files = dict(inventory['model_files'])
    files.update({str(job/'model'/name): sha for name,sha in receipt['model_files'].items()})
    files.update({str(p): digest(p) for p in (job/'receipt.json', inventory_path)})
    assert_hashes(files)
    return bundle, candidate, conversion, lambdas, files


def adapter_parity(bundle, candidate):
    """Raw synthetic known/unknown categories -> normal route == encoded route."""
    ids = tuple('synthetic-' + str(i) for i in range(4))
    context = RaceContext('synthetic', START, tuple(HorseEntry(h) for h in ids))
    reports = {}
    for member in bundle.members:
        model = member.model
        rows = pd.DataFrame({c: [np.nan]*4 for c in model.feature_cols})
        for col in model.categorical_cols:
            values = model.categorical_vocab.get(col, [])
            rows[col] = pd.Categorical([values[0] if values else None]*4, categories=values or None)
        for col, encoder in model.encoders.items():
            known = list(encoder.mapping)
            rows[col] = [known[0], known[-1], '__135_unknown__', None]
        rows['race_id'], rows['horse_id'], rows['race_date'] = context.race_id, ids, START
        for regime in REGIMES:
            prepared = regime_rows(rows, regime)
            expected, x = predict_base(model, prepared); x.index = pd.Index(ids)
            actual = predict_serving_encoded(model, context, x)
            if not np.array_equal(actual, prediction_array(expected, ids)):
                raise ValueError('Synthetic129 encoded adapter parity failed')
            reports[member.id + '/' + regime] = {'status':'PASS','max_abs_diff':0.}
        if member.id == 'joint-42':
            # Both entry points use the candidate itself, not a different booster.
            for regime in REGIMES:
                prepared = regime_rows(rows, regime)
                transformed = prepared.copy()
                for col, encoder in candidate.encoders.items():
                    transformed[col] = encoder.transform(transformed[col])
                x = encoded_frame(transformed, candidate.metadata['feature_columns'], candidate.metadata['categorical_columns'])
                a, b = candidate.predict(context, rows, regime), candidate.predict_encoded(context, x)
                if not np.array_equal(a,b):
                    raise ValueError('Synthetic candidate encoded adapter parity failed')
                reports['candidate/'+regime] = {'status':'PASS','max_abs_diff':0.}
    return reports


def capture():
    """Freeze dated input values only after Step2. Does not query any outcomes."""
    bindings = gate()
    if WORK.exists():
        raise FileExistsError('Recent work directory exists; preserve it and inspect the receipt')
    bundle, candidate, conversion, lambdas, model_files = load_models()
    sources = source_files()
    parity = adapter_parity(bundle,candidate)
    copies = preserve_sources(sources)
    full = next(m.model.feature_cols for m in bundle.members if m.id == 'anchor-42')
    with read_transaction() as connection:
        selected = select(connection,RUN_SELECTION_SQL)
        for row in selected:
            validate_day(row['race_date'])
        if len(selected) != 144 or len({r['race_id'] for r in selected}) != 144:
            raise ValueError('Previously registered144-race metadata scope changed')
        chosen = [r for r in selected if r['prediction_run_id'] is not None]
        write_json(WORK/'metadata-selection.json', {'rows':canonical(selected),
            'rule':'latest entire preserved094 run strictly before post time and JST cutoff',
            'no_outcomes_read':True,'scope':'previously referenced129 rehearsal, not untouched holdout'})
        # This is the first feature-value query. Model choice and Step2 gate precede it.
        snapshots = select(connection,SNAPSHOT_SQL,{'run_ids':[str(r['prediction_run_id']) for r in chosen]})
        appearances = select(connection,APPEARANCE_SQL)
    for row in snapshots:
        validate_day(row['race_date'])
        if not timestamp_eligible(row['computed_at'],row['run_created_at'],row['run_updated_at'],row['post_time']):
            raise ValueError('Snapshot run time invalid')
        if not timestamp_eligible(row['computed_at'],row['snapshot_created_at'],row['snapshot_updated_at'],row['post_time']):
            raise ValueError('Snapshot preservation time invalid')
    for row in appearances:
        validate_day(row['race_date'], history=True)
        if row['race_date'] <= TRAIN_END:
            raise ValueError('Appearance delta overlaps frozen111')
    raw_snapshots = []
    for row in snapshots:
        filtered = {key:value for key,value in row.items() if key != 'features'}
        feature = row['features']
        filtered['features'] = {c:feature[c] for c in full if c in feature} if isinstance(feature,dict) else {}
        filtered['features_was_dict'] = isinstance(feature,dict)
        raw_snapshots.append(filtered)
    # Preserve the exact required old encoded values for independent conversion
    # and whole-race exclusion replay. Stored model-output JSON extras are omitted.
    dump(WORK/'raw-inputs.pkl',{'snapshots':raw_snapshots,'selection':selected,
        'appearances':appearances,'feature_columns':full,'no_outcomes_read':True})
    by_race = defaultdict(list)
    for row in snapshots:
        by_race[row['race_id']].append(row)
    appearances_frame = pd.DataFrame(appearances, columns=mc.KEYS)
    if appearances_frame.duplicated(mc.KEYS[:2]).any():
        raise ValueError('Duplicate recent appearance identity')
    started = {rid:tuple(g.horse_id) for rid,g in appearances_frame.groupby('race_id',sort=False)}
    included, excluded = {}, []
    for row in selected:
        rid = row['race_id']
        group = by_race.get(rid,[])
        if row['prediction_run_id'] is None:
            excluded.append({'race_id':rid,'race_date':str(row['race_date']),'reason':'no preserved prestart094 snapshot',
                             'n_started':len(started.get(rid,()))}); continue
        try:
            if len(group) != row['n_snapshots']:
                raise InputExclusion('selected snapshot row count changed')
            data = []
            for s in group:
                if str(s['prediction_run_id']) != str(row['prediction_run_id']):
                    raise ValueError('Unexpected snapshot run ID')
                feature = s['features']
                if not isinstance(feature, dict) or not set(full).issubset(feature):
                    raise InputExclusion('missing saved full138 feature column')
                # Omit _raw/_calibrated outputs and any unrelated JSON fields.
                data.append({**{c:feature[c] for c in full},'race_id':rid,'horse_id':s['horse_id'],
                             'race_date':row['race_date']})
            reconstructed = reconstruct_race(pd.DataFrame(data),started.get(rid,()),full,conversion)
            # Fail closed on non-TE numeric/category schema too, before predicting.
            for member in bundle.members:
                try:
                    encoded_frame(reconstructed,member.model.feature_cols,member.model.categorical_cols)
                except (ValueError,TypeError) as exc:
                    raise InputExclusion('invalid saved numeric/category schema') from exc
            included[rid] = reconstructed
        except (InputExclusion, TypeError) as exc:
            excluded.append({'race_id':rid,'race_date':str(row['race_date']),'reason':str(exc),
                             'n_started':len(started.get(rid,()))})
    if not included:
        raise ValueError('No complete reconstructible recent races')
    assert_hashes({**bindings,**model_files,**sources,**copies})
    if gate() != bindings:
        raise ValueError('Step2 gate changed during capture')
    dump(WORK/'inputs.pkl',{'rows':included,'appearances':appearances_frame,'lambdas':lambdas})
    files = {**bindings,**model_files,**sources,**copies,
             str(WORK/'metadata-selection.json'):digest(WORK/'metadata-selection.json'),
             str(WORK/'raw-inputs.pkl'):digest(WORK/'raw-inputs.pkl'),
             str(WORK/'inputs.pkl'):digest(WORK/'inputs.pkl')}
    write_json(WORK/'input-receipt.json',{'status':'INPUTS_FROZEN','files':files,
        'n_target_races':len(selected),'n_prestart_snapshot_races':len(chosen),
        'n_included_races':len(included),'n_included_horses':sum(len(x) for x in included.values()),
        'excluded':excluded,'conversion':conversion_report(conversion),'adapter_parity':parity,
        'input_window':{'from':str(START),'through':str(CUTOFF)},'train_through':str(TRAIN_END),
        'no_outcomes_read':True,'additional_fits':0,'can_adopt':False,'eligible_for_verdict':False,
        'limitations':['Window previously referenced by129 rehearsal; not untouched holdout.',
            'Current dated appearance identities/status and current scheduled post times are assumed unchanged.',
            'Snapshot timestamps establish saved-input timing, not complete original information provenance.',
            'Full uses selected prestart available weight values; missing weights are not reconstructed.']})
    return {'status':'INPUTS_FROZEN','included':len(included),'excluded':len(excluded),'outcomes_read':False}


def verify_inputs():
    bindings = gate()
    receipt = read_json(WORK/'input-receipt.json')
    if receipt['status'] != 'INPUTS_FROZEN' or receipt['no_outcomes_read'] is not True:
        raise ValueError('Invalid input freeze')
    assert_hashes(receipt['files'])
    if any(receipt['files'].get(p) != sha for p,sha in bindings.items()):
        raise ValueError('Step2 gate no longer matches input freeze')
    return receipt


def frozen_history():
    meta = read_json(common.SNAPSHOT.with_suffix('.json'))
    if meta['data_through'] != str(TRAIN_END) or digest(common.SNAPSHOT) != common.SNAPSHOT_SHA256:
        raise ValueError('Frozen111 history source differs')
    matrix, _ = load(common.SNAPSHOT)
    history = matrix.frame[mc.KEYS].copy()
    dates = pd.to_datetime(history.race_date)
    if dates.max().date() != TRAIN_END or dates.min().date() < dt.date(2007,1,1):
        raise ValueError('Frozen111 history dates differ')
    history['race_date'] = dates.dt.date
    return history


def predict():
    input_receipt = verify_inputs()
    if (WORK/'execution-receipt.json').exists() or (WORK/'predictions.pkl').exists():
        raise FileExistsError('Recent predictions already exist')
    bundle,candidate,_,lambdas,files = load_models()
    if any(input_receipt['files'].get(p) != sha for p,sha in files.items()):
        raise ValueError('Predictor differs from captured-input model')
    data = load(WORK/'inputs.pkl')
    history = pd.concat([frozen_history(),data['appearances']],ignore_index=True)
    predictions = {regime:{'baseline':{},'candidate':{}} for regime in REGIMES}
    for rid,original in data['rows'].items():
        ids = tuple(original.horse_id)
        context = RaceContext(rid,original.race_date.iloc[0],tuple(HorseEntry(h) for h in ids))
        for regime in REGIMES:
            rows = regime_rows(original,regime)
            corr = mc.build_correction_inputs(rows,history.loc[history.horse_id.isin(ids)])
            members = []
            for member in bundle.members:
                x = encoded_frame(rows,member.model.feature_cols,member.model.categorical_cols)
                base = predict_serving_encoded(member.model,context,x)
                corrected = mc.correct_member_predictions(ids,base[:,0],corr,member.terms,member.coefficients)
                members.append(prediction_array(corrected,ids))
            candidate_x = encoded_frame(rows,candidate.metadata['feature_columns'],candidate.metadata['categorical_columns'])
            candidate_base = candidate.predict_encoded(context,candidate_x)
            joint42 = bundle.members[0]
            if joint42.id != 'joint-42':
                raise ValueError('Expected fixed joint42 transfer coefficients')
            candidate_p = prediction_array(mc.correct_member_predictions(ids,candidate_base[:,0],corr,
                joint42.terms,joint42.coefficients),ids)
            baseline = np.stack(members).mean(axis=0)
            weight = 1/7
            mixture = baseline*(1-weight) + candidate_p*weight
            stage = lambdas[regime]
            for arm,heads in [('baseline',baseline),('candidate',mixture)]:
                predictions[regime][arm][rid] = common.heads_from_win(heads[:,0],lambda2=stage['lambda2'],lambda3=stage['lambda3'])
    verify_inputs()
    dump(WORK/'predictions.pkl',{'predictions':predictions,'ids':{r:tuple(x.horse_id) for r,x in data['rows'].items()},
        'dates':{r:x.race_date.iloc[0] for r,x in data['rows'].items()},'lambdas':lambdas})
    files = {**input_receipt['files'],str(WORK/'input-receipt.json'):digest(WORK/'input-receipt.json'),
             str(common.SNAPSHOT):common.SNAPSHOT_SHA256,
             str(common.SNAPSHOT.with_suffix('.json')):digest(common.SNAPSHOT.with_suffix('.json')),
             str(WORK/'predictions.pkl'):digest(WORK/'predictions.pkl')}
    assert_hashes(files)
    write_json(WORK/'execution-receipt.json',{'status':'PREDICTIONS_FROZEN_BEFORE_OUTCOMES','files':files,
        'n_races':len(data['rows']),'regimes':list(REGIMES),'primary_regime':'preweight',
        'baseline':'129 final6 trained2026-08-23; fixed2026 correction and stage fit through2025',
        'candidate':'fixed seed42 colsample.7 plus same baseline; 1/7 added probability',
        'no_outcomes_read':True,'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),
        'can_adopt':False,'eligible_for_verdict':False})
    return {'status':'PREDICTIONS_FROZEN_BEFORE_OUTCOMES','n_races':len(data['rows'])}


@dataclass(frozen=True)
class RecentRace:
    er: EvalRace
    ids: tuple
    @property
    def race_id(self): return self.er.context.race_id
    @property
    def race_date(self): return self.er.context.race_date


# The score command runs this file as ``__main__``. Give serialized records a stable,
# importable module name so the independent auditor can unpickle them in another process.
if __name__ == '__main__':
    sys.modules.setdefault('mixture_recent_check_135', sys.modules[__name__])
RecentRace.__module__ = 'mixture_recent_check_135'


def quality_report(candidate, baseline, paired):
    """Step2-compatible descriptive guards; absent labels never mean PASS."""
    topk, ece = {}, {}
    for head in ('top2','top3'):
        ci = paired[head+'_logloss']['ci']
        status = 'UNRESOLVED' if ci is None else (
            'SUPPORTED' if ci[1] <= .0005 else ('WORSENING_SUPPORTED' if ci[0] > .0005 else 'UNRESOLVED'))
        topk[head] = {'margin':.0005,'status':status}
    for head in ('win','top2','top3'):
        point = candidate['heads'].get(head,{}).get('ece')
        base = baseline['heads'].get(head,{}).get('ece')
        measured = point is not None and base is not None and np.isfinite(point) and np.isfinite(base)
        diff = float(point-base) if measured else None
        within = bool(point <= .05 and diff <= .001) if measured else False
        ece[head] = {'point':point,'diff':diff,'within_descriptive_guard':within,
                     'status':'UNRESOLVED' if not measured else ('SUPPORTED' if within else 'WORSENING_SUPPORTED')}
    return {'topk':topk,'ece':ece}


def score():
    """Only this phase may read bounded labels, after prediction/execution freeze."""
    verify_inputs()
    receipt = read_json(WORK/'execution-receipt.json')
    if receipt['status'] != 'PREDICTIONS_FROZEN_BEFORE_OUTCOMES' or receipt['no_outcomes_read'] is not True:
        raise ValueError('Predictions must be frozen before outcomes')
    assert_hashes(receipt['files'])
    if (WORK/'summary.json').exists() or (WORK/'outcomes.pkl').exists():
        raise FileExistsError('Recent outcome scoring already exists')
    saved = load(WORK/'predictions.pkl')
    with read_transaction() as connection:
        outcomes = select(connection,RESULT_SQL,{'race_ids':list(saved['ids'])})
    grouped = defaultdict(list)
    for row in outcomes:
        validate_day(row['race_date'])
        if row['race_id'] not in saved['ids'] or row['race_date'] != saved['dates'][row['race_id']]:
            raise ValueError('Unexpected outcome race/date')
        grouped[row['race_id']].append(row)
    records = []
    for rid,ids in saved['ids'].items():
        result_rows = {r['horse_id']:r for r in grouped[rid] if r['horse_id'] in ids}
        if len(result_rows) != sum(r['horse_id'] in ids for r in grouped[rid]):
            raise ValueError('Duplicate result identity')
        labels = []
        for hid in ids:
            row = result_rows.get(hid)
            if row is not None and row['result_status'] == 'finished':
                order = row['finish_order']
                if not isinstance(order,int) or order <= 0:
                    raise ValueError('Invalid finished result label')
                labels.append(ScoringLabel(hid,int(order==1),int(order<=2),int(order<=3)))
        context = RaceContext(rid,saved['dates'][rid],tuple(HorseEntry(h) for h in ids))
        records.append(RecentRace(EvalRace(context,tuple(labels),len(result_rows)),ids))
    reports = {}
    for regime,predictions in saved['predictions'].items():
        baseline = common.summarize_predictions(records,predictions['baseline'])
        candidate = common.summarize_predictions(records,predictions['candidate'])
        paired = common.paired_metrics(records,predictions['baseline'],predictions['candidate'],
                                       b=4000,seed=20260907,alpha=.0125)
        mean_ece = {}
        for arm,report in [('baseline',baseline),('candidate',candidate)]:
            values = [report['heads'].get(h,{}).get('ece') for h in ('win','top2','top3')]
            mean_ece[arm] = float(np.mean(values)) if all(v is not None for v in values) else None
        quality = quality_report(candidate,baseline,paired)
        nll_ci = paired['winner_nll']['ci']
        reports[regime] = {'baseline':baseline,'candidate':candidate,'paired':paired,
            'mean_ece':mean_ece,'quality':quality,
            'descriptive_nll':'NOT_PROVEN' if nll_ci is None else ('IMPROVED' if nll_ci[1]<0 else ('WORSE' if nll_ci[0]>0 else 'NOT_PROVEN')),
            'interpretation':'conditional recent consistency only; four dates and previously selected models/window'}
    assert_hashes(receipt['files'])
    dump(WORK/'outcomes.pkl',{'records':records,'source_rows':outcomes})
    write_json(WORK/'summary.json',{'status':'RECENT_CONFIRMATION_COMPLETE','reports':reports,
        'actual_from':str(min(r.race_date for r in records)),'actual_through':str(max(r.race_date for r in records)),
        'n_included_races':len(records),'input_coverage':read_json(WORK/'input-receipt.json'),
        'execution_receipt_sha256':digest(WORK/'execution-receipt.json'),
        'outcomes_sha256':digest(WORK/'outcomes.pkl'),'additional_fits':0,
        'baseline_is_annual130':False,'untouched_holdout':False,'primary_regime':'preweight',
        'can_adopt':False,'eligible_for_verdict':False})
    return {'status':'RECENT_CONFIRMATION_COMPLETE','n_races':len(records),'can_adopt':False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['capture','predict','score'])
    args = parser.parse_args()
    print(json.dumps(globals()[args.command](),allow_nan=False),flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # DB driver exceptions can embed connection details. Emit type only.
        print('135 recent command failed: '+type(error).__name__,file=sys.stderr,flush=True)
        raise SystemExit(1)
