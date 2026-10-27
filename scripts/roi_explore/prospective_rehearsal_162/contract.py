"""Offline-only immutable comparison contract. No fetch, database or inference adapters."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MODE = 'offline_rehearsal'
ARMS = ('roi_only', 'plus_prediction')
WEIGHTS = ('weight', 'weight_diff', 'carried_weight_ratio')


class Rejected(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise Rejected(reason)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def time(value):
    require(isinstance(value, str), 'timestamp_missing')
    require(re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})',
        value) is not None, 'timestamp_timezone_or_precision_invalid')
    try:
        stamp = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise Rejected('timestamp_invalid') from exc
    require(stamp.tzinfo is not None and stamp.utcoffset() is not None, 'timestamp_naive')
    return stamp.astimezone(dt.UTC)


def number(value, minimum=None, maximum=None):
    require(not isinstance(value, bool) and isinstance(value, (int, float, str)), 'invalid_number')
    try:
        n = Decimal(str(value))
    except Exception as exc:
        raise Rejected('invalid_number') from exc
    require(n.is_finite(), 'invalid_number')
    try:
        floating = float(n)
    except (ValueError, OverflowError) as exc:
        raise Rejected('invalid_number_range') from exc
    require(math.isfinite(floating) and (n == 0 or floating != 0), 'invalid_number_range')
    require(minimum is None or n >= minimum, 'number_below_minimum')
    require(maximum is None or n <= maximum, 'number_above_maximum')
    return n


def field(rows):
    require(isinstance(rows, list) and len(rows) >= 2, 'incomplete_field')
    ids, nums, result = set(), set(), set()
    for r in rows:
        require(isinstance(r, dict), 'invalid_field_row')
        h, n = r.get('horse_id'), r.get('horse_number')
        require(isinstance(h, str) and bool(h), 'invalid_horse_id')
        require(type(n) is int and n > 0, 'invalid_horse_number')
        require(h not in ids and n not in nums, 'duplicate_field')
        ids.add(h)
        nums.add(n)
        result.add((h, n))
    return result


def keys(value, expected, name):
    require(isinstance(value, dict) and set(value) == set(expected), 'invalid_' + name + '_schema')


def make_protocol():
    mevdir = ROOT/'artifacts/market_ev/mev-ens15-v1'
    olddir = ROOT/'artifacts/129-candidate-mixture-serving'
    mev = json.loads((mevdir/'ensemble_2026.json').read_text())
    spec = json.loads((mevdir/'ensemble.spec.json').read_text())
    old = json.loads((olddir/'bundle.json').read_text())
    coeffpath = ROOT/'artifacts/151-roi-prediction-ablation/comparison/summary.json'
    coeffs = json.loads(coeffpath.read_text())['coefficients']['2026']
    paths = [mevdir/'ensemble_2026.json', mevdir/'ensemble.spec.json', olddir/'bundle.json', coeffpath]
    for m in mev['members']:
        path = mevdir/m['path']
        require(sha_bytes(path.read_bytes()) == m['sha256'], 'mev_model_file_changed')
        paths.append(path)
    for m in old['members']:
        for name, checksum in m['files'].items():
            path = olddir/m['artifact_dir']/name
            require(sha_bytes(path.read_bytes()) == checksum, 'legacy_model_file_changed')
            paths.append(path)
    return dict(schema_version=1, mode=MODE, simulated_clock=True, eligible_for_prospective=False,
        collection_enabled=False, database_enabled=False, inference_enabled=False,
        year=2026, prepare_seconds_before_post=900, window_open_seconds=600,
        window_close_seconds=540, max_quote_age_seconds=1800, max_guard_age_seconds=5,
        policy=dict(threshold=1, stake_yen=100, max_tickets=10, budget_yen=1000),
        coefficients={a: coeffs[a] for a in ARMS},
        model_versions=dict(mev='mev-ens15-v1', legacy='mix-129-nj6'),
        model_shas=dict(mev=sha_bytes((mevdir/'ensemble_2026.json').read_bytes()),
            legacy=sha_bytes((olddir/'bundle.json').read_bytes())),
        train_through=dict(mev='2025-12-31', legacy=old['train_through']),
        feature_columns=dict(mev=spec['features'], legacy=old['feature_profile']['full_columns']),
        model_files={str(p.relative_to(ROOT)): sha_bytes(p.read_bytes()) for p in paths})


def check_protocol(protocol):
    # Pinned code-defined policy: changing a saved protocol cannot enable a live path or tune policy.
    require(protocol == make_protocol(), 'protocol_changed')


def market_features(rows):
    ordered = sorted(rows, key=lambda r: (float(r['odds']), r['horse_number']))
    odds = [float(r['odds']) for r in rows]
    inv = [1/o for o in odds]
    q = [v/math.fsum(inv) for v in inv]
    favourite, second = float(ordered[0]['odds']), float(ordered[1]['odds'])
    ranks = {r['horse_id']: i+1 for i, r in enumerate(ordered)}
    return {r['horse_id']: dict(odds=float(r['odds']), popularity=r['popularity'], q=p,
        odds_rank=ranks[r['horse_id']], field_size=len(rows), horse_number=r['horse_number'],
        fav_odds=favourite, second_odds=second, odds_gap12=second-favourite, fav_q=max(q),
        q_share_of_fav=p/max(q), q_entropy_norm=-math.fsum(v*math.log(v) for v in q)/math.log(len(rows)),
        n_fav_under_2=sum(o < 2 for o in odds), n_odds_under_10=sum(o < 10 for o in odds),
        is_fav=ranks[r['horse_id']] == 1) for r, p in zip(rows, q, strict=True)}


def validate_inputs(bundle, protocol):
    keys(bundle, ['schema_version', 'mode', 'simulated_clock', 'eligible_for_prospective',
        'race_id', 'session_id', 'post_time', 'race_date', 'field', 'guard_before', 'quote', 'predictions'], 'bundle')
    require(bundle['schema_version'] == 1 and bundle['mode'] == MODE and bundle['simulated_clock'] is True
        and bundle['eligible_for_prospective'] is False, 'offline_mode_required')
    require(isinstance(bundle['race_id'], str) and re.fullmatch(r'\d{12}', bundle['race_id']) is not None, 'invalid_race_id')
    require(isinstance(bundle['session_id'], str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', bundle['session_id']) is not None, 'invalid_session_id')
    post = time(bundle['post_time'])
    day = dt.date.fromisoformat(bundle['race_date'])
    require(day.year == protocol['year'] and post.astimezone(dt.timezone(dt.timedelta(hours=9))).date() == day,
        'unsupported_model_year_or_date')
    expected = field(bundle['field'])
    keys(bundle['guard_before'], ['at', 'pending', 'field', 'post_time'], 'guard')
    before = bundle['guard_before']
    require(before['pending'] is True and field(before['field']) == expected
        and before['post_time'] == bundle['post_time'], 'preflight_failed')
    q = bundle['quote']
    keys(q, ['format', 'source', 'fetch_started_at', 'received_at', 'provider_at', 'cache',
        'raw_text', 'raw_sha256', 'rows'], 'quote')
    require(q['format'] == 'synthetic_quote_v1' and q['source'] == 'synthetic_fixture'
        and q['cache'] == 'bypass', 'offline_quote_adapter_required')
    require(sha_bytes(q['raw_text'].encode()) == q['raw_sha256'], 'quote_raw_sha_mismatch')
    raw = json.loads(q['raw_text'])
    keys(raw, ['race_id', 'horses'], 'raw_quote')
    require(raw['race_id'] == bundle['race_id'] and raw['horses'] == q['rows'], 'quote_raw_rows_mismatch')
    require(field(q['rows']) == expected, 'quote_field_mismatch')
    received, started = time(q['received_at']), time(q['fetch_started_at'])
    require(time(before['at']) <= started <= received < post, 'quote_time_order')
    if q['provider_at'] is not None:
        provider = time(q['provider_at'])
        require(provider <= received and received-provider <= dt.timedelta(seconds=1800), 'provider_quote_stale')
    quote_by_id = {}
    for r in q['rows']:
        keys(r, ['horse_id', 'horse_number', 'odds', 'popularity'], 'quote_row')
        number(r['odds'], 1)
        require(type(r['popularity']) is int and r['popularity'] >= 1, 'invalid_popularity')
        quote_by_id[r['horse_id']] = r
    derived = market_features(q['rows'])
    keys(bundle['predictions'], ['mev', 'legacy'], 'predictions')
    latest = received
    for kind in ('mev', 'legacy'):
        prediction = bundle['predictions'][kind]
        keys(prediction, ['run_id', 'producer', 'model_version', 'model_sha256', 'input_available_at',
            'started_at', 'computed_at', 'rows'], 'prediction')
        require(prediction['producer'] == 'synthetic_fixture', 'not_actual_inference')
        require(prediction['model_version'] == protocol['model_versions'][kind]
            and prediction['model_sha256'] == protocol['model_shas'][kind], 'model_identity_mismatch')
        require(prediction['run_id'] and isinstance(prediction['run_id'], str), 'missing_prediction_run')
        require(protocol['train_through'][kind] < bundle['race_date'], 'training_overlaps_target')
        available, began, computed = [time(prediction[c]) for c in
            ('input_available_at', 'started_at', 'computed_at')]
        require(received <= available <= began <= computed < post, 'prediction_time_order')
        require(field(prediction['rows']) == expected, 'prediction_field_mismatch')
        probabilities = []
        for row in prediction['rows']:
            keys(row, ['horse_id', 'horse_number', 'probability', 'features', 'input_asof',
                'created_at', 'updated_at'], 'prediction_row')
            require(time(row['input_asof']) <= available and time(row['input_asof']) >= received,
                'future_input_features')
            require(began <= time(row['created_at']) <= time(row['updated_at']) <= computed,
                'prediction_row_unavailable')
            p = number(row['probability'], 0, 1)
            require(0 < p < 1, 'invalid_probability')
            probabilities.append(float(p))
            require(set(row['features']) == set(protocol['feature_columns'][kind]), 'incomplete_input_evidence')
            canonical(row['features'])  # Reject NaN/Inf; retain explicit unknowns.
            if kind == 'mev':
                price = quote_by_id[row['horse_id']]
                require(number(row['features']['odds']) == number(price['odds'])
                    and row['features']['popularity'] == price['popularity'], 'mev_quote_input_mismatch')
                for key, expected_value in derived[row['horse_id']].items():
                    if key in row['features']:
                        value = row['features'][key]
                        require(value is not None and math.isclose(float(number(value) if type(value) is not bool else int(value)),
                            expected_value, rel_tol=1e-12, abs_tol=1e-12), 'mev_market_feature_mismatch')
            else:
                require(all(row['features'].get(w, 'absent') is None for w in WEIGHTS), 'not_preweight')
        if kind == 'legacy':
            require(math.isclose(math.fsum(probabilities), 1, abs_tol=1e-8, rel_tol=0), 'legacy_not_normalized')
        latest = max(latest, computed)
    return latest


def gate(bundle, guard, ready_at, at, protocol):
    # A direct caller cannot relax admission constants through an untrusted protocol dict.
    require((protocol['max_quote_age_seconds'], protocol['window_open_seconds'],
        protocol['window_close_seconds'], protocol['max_guard_age_seconds']) == (1800, 600, 540, 5),
        'protocol_changed')
    validate_inputs(bundle, protocol)
    now, post = time(at), time(bundle['post_time'])
    keys(guard, ['at', 'pending', 'field', 'post_time'], 'guard')
    require(guard['pending'] is True and guard['post_time'] == bundle['post_time']
        and field(guard['field']) == field(bundle['field']), 'final_guard_failed')
    require(time(guard['at']) <= now and now-time(guard['at']) <= dt.timedelta(seconds=protocol['max_guard_age_seconds']),
        'final_guard_stale_or_future')
    require(time(ready_at) <= now, 'predictions_not_durably_ready')
    age = now-time(bundle['quote']['received_at'])
    require(dt.timedelta(0) <= age <= dt.timedelta(seconds=protocol['max_quote_age_seconds']), 'stale_quote')
    require(post-dt.timedelta(seconds=protocol['window_open_seconds']) <= now
        < post-dt.timedelta(seconds=protocol['window_close_seconds']), 'outside_decision_window')
    require(now < post, 'not_prestart')


def decide(bundle, at, protocol):
    """Only the validated price and probability vectors enter this pure calculation."""
    quotes = sorted(bundle['quote']['rows'], key=lambda r: (r['horse_number'], r['horse_id']))
    maps = {k: {r['horse_id']: float(r['probability']) for r in v['rows']}
        for k, v in bundle['predictions'].items()}
    inv = [1/float(r['odds']) for r in quotes]
    denominator = math.fsum(inv)
    arms = {}
    for arm in ARMS:
        coefficients = protocol['coefficients'][arm]
        require(coefficients['fit_through'] < bundle['race_date'], 'coefficient_training_overlap')
        beta = coefficients['coefficients']
        scores = []
        for r, iq in zip(quotes, inv, strict=True):
            components = dict(q=iq/denominator, p_mev=min(1-1e-6, max(1e-6, maps['mev'][r['horse_id']])),
                p_pred_preweight=maps['legacy'][r['horse_id']])
            scores.append(math.fsum(beta[k]*math.log(components[k]) for k in beta))
        exps = [math.exp(s-max(scores)) for s in scores]
        norm = math.fsum(exps)
        rows = [dict(horse_id=r['horse_id'], horse_number=r['horse_number'], probability=p/norm,
            ev=p/norm*float(r['odds'])) for r, p in zip(quotes, exps, strict=True)]
        require(all(0 < r['probability'] < 1 and math.isfinite(r['ev']) for r in rows), 'blend_degenerate')
        selected = sorted([r for r in rows if r['ev'] > 1],
            key=lambda r: (-r['ev'], r['horse_number'], r['horse_id']))[:10]
        arms[arm] = dict(rows=rows, tickets=[dict(horse_id=r['horse_id'], horse_number=r['horse_number'],
            stake_yen=100) for r in selected], budget_yen=1000)
    return dict(schema_version=1, mode=MODE, simulated_clock=True, eligible_for_prospective=False,
        race_id=bundle['race_id'], session_id=bundle['session_id'], decision_at=at,
        post_time=bundle['post_time'], field=bundle['field'], protocol_sha256=digest(protocol), arms=arms)
