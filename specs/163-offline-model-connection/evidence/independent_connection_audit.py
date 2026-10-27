"""Independent direct-file, native-booster and printed-payout audit (no main 163 imports)."""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import gzip
import hashlib
import json
import math
import pickle
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
for package in ('training', 'eval', 'features', 'db', 'probability'):
    sys.path.insert(0, str(ROOT/package/'src'))


def load(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def at(value):
    assert re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value)
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(dt.UTC)


def fields(rows):
    pairs = {(r['horse_id'], r['horse_number']) for r in rows}
    assert len(pairs) == len(rows) == len({h for h, _ in pairs}) == len({n for _, n in pairs})
    return pairs


def matrix(evidence):
    frame = pd.DataFrame(evidence['rows'], columns=evidence['columns'])
    for key, dtype in evidence['dtypes'].items():
        if key in evidence['categories']:
            frame[key] = pd.Categorical(frame[key], categories=evidence['categories'][key])
        elif dtype != 'object':
            frame[key] = frame[key].astype(dtype)
    return frame


def actual_native_predictions(bundle):
    """Direct native LightGBM plus independent isotonic/normalisation/correction math."""
    mev = bundle['predictions']['mev']
    evidence = mev['evidence']
    encoded = evidence['encoded_input']
    assert digest(encoded) == evidence['encoded_input_sha256']
    X = np.array([[np.nan if v is None else v for v in row] for row in encoded['rows']], dtype=np.float32)
    manifest_path = ROOT/'artifacts/market_ev/mev-ens15-v1/ensemble_2026.json'
    manifest = load(manifest_path)
    ids = encoded['horse_ids']
    spec = load(manifest_path.parent/'ensemble.spec.json')
    raw_features = {r['horse_id']: r['features'] for r in mev['rows']}
    reencoded = []
    for hid in ids:
        row = raw_features[hid]
        values = [np.nan if row[c] is None else float(row[c]) for c in spec['features']]
        for column in spec['cats']:
            value = row[column]
            values.append(float(spec['cat_maps'][column].get(str(value), np.nan)) if value is not None else np.nan)
        reencoded.append(values)
    assert np.allclose(X, np.array(reencoded, dtype=np.float32), atol=0, rtol=0, equal_nan=True)
    vectors = []
    for member in manifest['members']:
        path = manifest_path.parent/member['path']
        assert sha(path) == member['sha256']
        booster = lgb.Booster(model_file=str(path), params={'num_threads': 1})
        vector = np.asarray(booster.predict(X, num_threads=1), dtype=float)
        assert np.allclose(vector, [evidence['member_probabilities'][str(member['seed'])][h] for h in ids],
            atol=1e-15, rtol=0)
        vectors.append(vector)
    mean = np.mean(vectors, axis=0)
    by_id = {r['horse_id']: r['probability'] for r in mev['rows']}
    assert np.allclose(mean, [by_id[h] for h in ids], atol=1e-15, rtol=0)
    legacy = bundle['predictions']['legacy']
    e = legacy['evidence']
    legacy_path = ROOT/'artifacts/129-candidate-mixture-serving/bundle.json'
    legacy_manifest = load(legacy_path)
    consumed = matrix(e['consumed_input'])
    ids = consumed.horse_id.tolist()
    correction = matrix(e['correction_input'])
    historical = matrix(e['started_history'])
    target_day = dt.date.fromisoformat(bundle['race_date'])
    assert not historical.duplicated(['horse_id', 'race_id']).any()
    assert set(historical.horse_id) <= set(ids)
    assert historical.entry_status.eq('started').all()
    assert all(dt.date(2007, 1, 1) <= dt.date.fromisoformat(str(d)) < target_day for d in historical.race_date)
    theta = 2*math.pi*((target_day.timetuple().tm_yday-1)/(366 if calendar.isleap(target_day.year) else 365))
    for index, row in consumed.iterrows():
        dates = sorted(dt.date.fromisoformat(str(d)) for d in historical.loc[
            historical.horse_id.eq(row.horse_id), 'race_date'])
        assert row.career_starts == len(dates)
        if dates:
            assert row.days_since_last == (target_day-dates[-1]).days
        else:
            assert pd.isna(row.days_since_last)
        expected = dict(gap_log=math.log1p(row.days_since_last) if pd.notna(row.days_since_last) else np.nan,
            prior_gap_log=math.log1p((dates[-1]-dates[-2]).days) if len(dates) >= 2 else np.nan,
            female_sin=(row.sex == '牝')*math.sin(theta) if row.sex is not None else np.nan,
            female_cos=(row.sex == '牝')*math.cos(theta) if row.sex is not None else np.nan)
        for column, value in expected.items():
            actual = correction.iloc[index][column]
            assert (pd.isna(actual) and pd.isna(value)) or math.isclose(actual, value, abs_tol=1e-14, rel_tol=0)
    corrected = []
    for member in legacy_manifest['members']:
        directory = legacy_path.parent/member['artifact_dir']
        for filename, checksum in member['files'].items():
            assert sha(directory/filename) == checksum
        trace = e['members'][member['id']]
        encoded = trace['encoded_input']
        assert digest(encoded) == trace['encoded_input_sha256']
        X = matrix(encoded)
        with (directory/'preprocessor.pkl').open('rb') as handle:
            prep = pickle.load(handle)
        assert list(X.columns) == prep['feature_cols']
        originals = consumed.set_index('horse_id').loc[ids]
        for column in X.columns:
            if column in prep['encoders']:
                encoder = prep['encoders'][column]
                expected = np.array([encoder.mapping.get(v, encoder.prior) if v is not None else encoder.prior
                    for v in originals[column]], dtype=float)
                assert np.allclose(X[column].to_numpy(float), expected, atol=0, rtol=0, equal_nan=True)
            elif column in prep['categorical_cols']:
                assert list(X[column].astype(object).where(X[column].notna(), None)) == list(
                    originals[column].where(originals[column].notna(), None))
            else:
                expected = pd.to_numeric(originals[column], errors='coerce').to_numpy(float)
                assert np.allclose(X[column].to_numpy(float), expected, atol=0, rtol=0, equal_nan=True)
        booster = lgb.Booster(model_file=str(directory/'model.txt'), params={'num_threads': 1})
        margins = np.asarray(booster.predict(X, num_threads=1), dtype=float)
        exp = np.exp(margins-margins.max())
        raw = exp/exp.sum()
        # Pickle is a pinned existing artifact. Do not call the product calibrator transform.
        with (directory/'calibrator.pkl').open('rb') as handle:
            calibrator = pickle.load(handle)
        assert calibrator.method == 'isotonic' and not calibrator.identity
        bounded = np.clip(raw, calibrator.clip, 1-calibrator.clip)
        calibrated = np.clip(np.interp(bounded, calibrator._iso.X_thresholds_, calibrator._iso.y_thresholds_),
            calibrator.clip, 1-calibrator.clip)
        calibrated = np.clip(calibrated, 1e-6, 1-1e-6)
        p = calibrated/calibrated.sum()
        assert np.allclose(p, [trace['base_probabilities'][h] for h in ids], rtol=0, atol=1e-13)
        terms = member['correction']['terms']
        columns = []
        for term in terms:
            if term == 'centered_logp':
                columns.append(np.log(p)-np.log(p).mean())
            else:
                columns.append(pd.to_numeric(correction[term]).fillna(0).to_numpy(float))
        tilt = np.column_stack(columns)@np.asarray(member['correction']['coefficients'])
        q = p*np.exp(tilt-tilt.max())
        q /= q.sum()
        assert np.allclose(q, [trace['corrected_heads'][h][0] for h in ids], rtol=0, atol=1e-13)
        corrected.append(q)
    mean = np.mean(corrected, axis=0)
    actual = {r['horse_id']: r['probability'] for r in legacy['rows']}
    assert np.allclose(mean, [actual[h] for h in ids], atol=1e-13, rtol=0)
    return dict(mev_native_boosters=15, legacy_native_boosters=6,
        independent_legacy_isotonic_and_correction=True, main_model_adapter_imported=False)


class PayoutParser(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.active = False
        self.cell = None
        self.numbers, self.amounts, self.versions, self.canonical = [], [], [], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'link' and attrs.get('rel') == 'canonical':
            self.canonical.append(attrs['href'])
        if tag == 'tr' and 'Tansho' in attrs.get('class', '').split():
            self.active, self.numbers, self.amounts = True, [], []
        if self.active and tag == 'td':
            self.cell = attrs.get('class')

    def handle_data(self, value):
        if self.active:
            value = value.strip()
            if self.cell == 'Result' and value:
                assert value.isdigit()
                self.numbers.append(int(value))
            if self.cell == 'Payout' and value:
                match = re.fullmatch(r'([0-9,]+)円', value)
                assert match
                self.amounts.append(int(match.group(1).replace(',', '')))

    def handle_endtag(self, tag):
        if tag == 'td':
            self.cell = None
        if tag == 'tr' and self.active:
            assert len(self.numbers) == len(self.amounts) and len(self.numbers) > 0
            self.versions.append(tuple(zip(self.numbers, self.amounts, strict=True)))
            self.active = False


def historical_replay(root):
    replay = load(root/'historical-official-replay.json')
    assert replay['mode'] == 'historical_settlement_only' and replay['current_session_linked'] is False
    assert replay['fabricated_past_receipts'] is False and replay['original_timestamps_preserved'] is True
    base = ROOT/'artifacts/159-prerace-fresh30'
    frozen = load(base/'output-sha256.json')['files']
    tables = {}
    for name in ['comparison/decisions.parquet', 'comparison/settled.parquet']:
        p = base/name
        assert sha(p) == frozen[str(p.relative_to(ROOT))]
        tables[name] = pd.read_parquet(p)
    decisions, labels = tables.values()
    assert len(decisions) == len(labels) == 129 and decisions.race_id.nunique() == 9
    pages = pd.read_parquet(ROOT/'artifacts/155-saved-odds-settlement-audit/audit/archive_pages.parquet')
    for source in replay['official_sources']:
        rid = source['race_id']
        selected = pages.loc[pages.selected & pages.race_id.eq(rid)]
        assert len(selected) == 1
        page = selected.iloc[0]
        assert str(page.fetched_at) == source['original_observed_at']
        raw = gzip.decompress((ROOT/source['archive_path']).read_bytes())
        assert hashlib.sha256(raw).hexdigest() == source['raw_sha256'] == page.archive_raw_sha256
        html = raw.decode(page.decode_encoding, errors='replace')
        assert hashlib.sha256(html.encode()).hexdigest() == source['html_sha256']
        parsed = PayoutParser(html)
        assert len(parsed.canonical) == 1
        url = urlsplit(parsed.canonical[0])
        assert url.hostname == 'race.netkeiba.com' and url.path == '/race/result.html'
        assert parse_qs(url.query)['race_id'] == [rid]
        assert len(set(parsed.versions)) == 1
        payouts = {str(n): v for n, v in parsed.versions[0]}
        assert payouts == source['official_payouts']
        group = labels.loc[labels.race_id.eq(rid)]
        assert {str(r.horse_number): int(r.official_payout_yen) for r in group.itertuples() if r.won} == payouts
    joined = decisions.merge(labels[['race_id', 'horse_id', 'official_payout_yen']],
        on=['race_id', 'horse_id'], validate='one_to_one')
    for arm in ['roi_only', 'plus_prediction']:
        selected = joined[arm+'_selected']
        account = replay['accounts'][arm]
        stake, gross = int(selected.sum())*100, int(joined.loc[selected, 'official_payout_yen'].sum())
        assert account == dict(tickets=int(selected.sum()), stake_yen=stake, payout_yen=gross, profit_yen=gross-stake)
    assert replay['races'] == 9 and replay['horses'] == 129
    return dict(races=9, horses=129, official_html_reparsed=True, current_session_linked=False,
        accounts=replay['accounts'])


def audit(root):
    manifest = load(root/'manifest.json')
    assert set(manifest['files']) | {'manifest.json'} == {
        str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()}
    for path, checksum in manifest['files'].items():
        assert sha(root/path) == checksum, path
    for path, checksum in manifest['source_files'].items():
        assert sha(ROOT/path) == checksum, path
    protocol, report = load(root/'protocol.json'), load(root/'report.json')
    assert protocol == load(root/'sessions/protocol.json')
    assert protocol['mode'] == report['mode'] == 'offline_model_connection'
    assert protocol['simulated_clock'] is report['simulated_clock'] is False
    assert protocol['eligible_for_prospective'] is report['eligible_for_prospective'] is False
    assert report['new_collection'] is False and report['database_access'] is False
    assert report['actual_model_inference'] is True and report['comparison_data_added'] == 0
    for name, checksum in protocol['model_files'].items():
        assert sha(ROOT/name) == checksum
    p = root/'sessions/races'/report['race_id']
    bundle, ready, decision, seal, admitted = [load(p/n) for n in
        ['inputs.json', 'ready.json', 'decision.json', 'seal.json', 'accepted.json']]
    assert ready['input_sha256'] == decision['input_sha256'] == digest(bundle)
    assert decision['ready_sha256'] == digest(ready)
    assert seal['decision_sha256'] == digest(decision) and admitted['seal_sha256'] == digest(seal)
    assert at(ready['available_by_at']) <= at(decision['decision_at']) <= at(seal['observed_saved_at']) <= at(admitted['acknowledged_at'])
    post, received = at(bundle['post_time']), at(bundle['quote']['received_at'])
    assert bundle['synthetic_post'] is decision['synthetic_post'] is True
    assert post-dt.timedelta(seconds=600) <= at(decision['decision_at']) <= at(admitted['acknowledged_at']) < post-dt.timedelta(seconds=540)
    assert dt.timedelta(0) <= at(admitted['acknowledged_at'])-received <= dt.timedelta(seconds=1800)
    assert dt.timedelta(0) <= at(admitted['acknowledged_at'])-at(decision['guard']['at']) <= dt.timedelta(seconds=5)
    clock = load(root/'clock.json')
    observations = clock['observations']
    assert len(observations) >= 8 and clock['simulated_clock'] is False
    stamps = {o['at'] for o in observations}
    for before, after in zip(observations, observations[1:]):
        assert before['epoch_id'] == after['epoch_id'] == clock['epoch_id']
        wall = (at(after['at'])-at(before['at'])).total_seconds()
        mono = (after['monotonic_ns']-before['monotonic_ns'])/1e9
        assert wall >= 0 and mono >= 0 and abs(wall-mono) <= 1
    for stamp in [bundle['quote']['fetch_started_at'], bundle['quote']['received_at'], ready['available_by_at'],
        decision['decision_at'], seal['observed_saved_at'], admitted['acknowledged_at']]:
        assert stamp in stamps
    assert fields(bundle['field']) == fields(bundle['quote']['rows']) == fields(decision['field'])
    quote = bundle['quote']
    assert quote['source'] == quote['origin'] == 'local_fixture' and quote['eligible_for_prospective'] is False
    assert hashlib.sha256(quote['raw_text'].encode()).hexdigest() == quote['raw_sha256']
    raw = json.loads(quote['raw_text'])['data']['odds']['1']
    qmap = {r['horse_id']: r for r in quote['rows']}
    for row in quote['rows']:
        original = raw[str(row['horse_number']).zfill(2)]
        assert float(original[0]) == float(row['odds']) and int(original[2]) == row['popularity']
    maps = {}
    for kind, prediction in bundle['predictions'].items():
        assert fields(prediction['rows']) == fields(bundle['field'])
        assert prediction['producer'] == 'actual_model' and prediction['model_sha256'] == protocol['model_shas'][kind]
        assert received <= at(prediction['input_available_at']) <= at(prediction['started_at']) <= at(prediction['computed_at']) <= at(ready['available_by_at'])
        for key in ['input_available_at', 'started_at', 'computed_at']:
            assert prediction[key] in stamps
        for row in prediction['rows']:
            assert set(row['features']) == set(protocol['feature_columns'][kind])
            assert at(prediction['started_at']) <= at(row['created_at']) <= at(row['updated_at']) <= at(prediction['computed_at'])
            if kind == 'legacy':
                assert all(row['features'][k] is None for k in ['weight', 'weight_diff', 'carried_weight_ratio'])
            else:
                assert float(row['features']['odds']) == float(qmap[row['horse_id']]['odds'])
                assert row['features']['popularity'] == qmap[row['horse_id']]['popularity']
        maps[kind] = {r['horse_id']: r['probability'] for r in prediction['rows']}
    native = actual_native_predictions(bundle)
    rows = sorted(quote['rows'], key=lambda r: (r['horse_number'], r['horse_id']))
    inverse = [1/float(r['odds']) for r in rows]
    for arm in ['roi_only', 'plus_prediction']:
        coefficients = protocol['coefficients'][arm]['coefficients']
        logits = []
        for row, inv in zip(rows, inverse, strict=True):
            values = dict(q=inv/sum(inverse), p_mev=min(1-1e-6, max(1e-6, maps['mev'][row['horse_id']])),
                p_pred_preweight=maps['legacy'][row['horse_id']])
            logits.append(sum(weight*math.log(values[key]) for key, weight in coefficients.items()))
        exp = [math.exp(v-max(logits)) for v in logits]
        probs = [v/sum(exp) for v in exp]
        candidates = []
        for row, probability, actual in zip(rows, probs, decision['arms'][arm]['rows'], strict=True):
            ev = probability*float(row['odds'])
            assert math.isclose(probability, actual['probability'], abs_tol=1e-13, rel_tol=0)
            assert math.isclose(ev, actual['ev'], abs_tol=1e-13, rel_tol=0)
            if ev > 1:
                candidates.append((ev, row))
        selected = sorted(candidates, key=lambda pair: (-pair[0], pair[1]['horse_number'], pair[1]['horse_id']))[:10]
        assert decision['arms'][arm]['tickets'] == [dict(horse_id=r['horse_id'], horse_number=r['horse_number'], stake_yen=100) for _, r in selected]
    pending = load(root/'pending-settlement.json')
    assert pending['status'] == 'pending' and pending['eligible_for_prospective'] is False
    assert all(v['payout_yen'] is None and v['profit_yen'] is None for v in pending['arms'].values())
    historical = historical_replay(root)
    return dict(status='passed', mode='offline_model_connection', eligible_for_prospective=False,
        simulated_clock=False, current_session_horses=len(bundle['field']), actual_model_checks=native,
        all_receipt_times_and_sha_verified=True, probability_ev_and_tickets_verified=True,
        current_session_settlement='pending', historical_replay=historical,
        main_selector_store_settler_imported=False, new_collection=False, database_access=False,
        manifest_sha256=sha(root/'manifest.json'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.out)
    Path(__file__).with_name('independent-connection-audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
