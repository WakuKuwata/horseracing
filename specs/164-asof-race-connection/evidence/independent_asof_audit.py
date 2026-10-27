"""Independent preserved-source/as-of audit; imports no main 164 adapter or validator."""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import hashlib
import importlib.util
import json
import math
import pickle
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[3]
OLD_AUDIT = ROOT/'specs/163-offline-model-connection/evidence/independent_connection_audit.py'
spec = importlib.util.spec_from_file_location('independent163_support', OLD_AUDIT)
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
load, sha, digest, matrix, at = support.load, support.sha, support.digest, support.matrix, support.at


def legacy_native(bundle):
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
    return dict(legacy_native_boosters=6, independent_isotonic_and_correction=True)


def audit(root):
    manifest = load(root/'manifest.json')
    assert set(manifest['files']) | {'manifest.json'} == {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()}
    for section in ('files', 'source_files', 'inputs'):
        for name, checksum in manifest[section].items():
            assert sha((root if section == 'files' else ROOT)/name) == checksum, name
    report = load(root/'report.json')
    assert report['mode'] == 'offline_asof_connection' and report['eligible_for_prospective'] is False
    assert report['new_collection'] is report['database_access'] is False
    assert report['contract_ready'] == report['new_comparison_races'] == report['historical_seals_created'] == 0
    assert not list((root/'journal').glob('decisions/*/accepted.json'))
    ledger = load(root/'ledger.json')
    assert len(ledger) == len({r['race_id'] for r in ledger}) == 46
    found_horses = 0
    before_target = 0
    for row in ledger:
        rid = row['race_id']
        raw_path = ROOT/'artifacts/133-prospective-paired-comparison/records/capture'/f'{rid}.json'
        raw = load(raw_path)
        primary = raw['regimes']['preweight']
        expected_field = {(h, values[primary['inputs']['columns'].index('horse_number')])
            for h, values in primary['inputs']['rows'].items()}
        assert len(expected_field) == len(primary['started_ids'])
        found_horses += len(expected_field)
        decision = at(raw['scheduled_start'])-dt.timedelta(seconds=600)
        assert at(row['reference_decision_at']) == decision
        assert at(row['original_feature_available_at']) == at(raw['timeline']['acquisition_finished_at'])
        available = at(raw['timeline']['acquisition_finished_at']) <= decision
        assert row['legacy_memory_available_by_target'] == available
        before_target += available
        candidate = load(root/'journal/candidates'/rid/'reference.json')
        checks = load(root/'journal/candidates'/rid/'checks.json')
        assert checks['candidate_sha256'] == digest(candidate) and checks['contract_ready'] is False
        assert candidate['features']['mev'] is None and candidate['predictions'] == dict(legacy=None, mev=None)
        assert checks['feature_packets']['legacy'] is None  # historical durable-ready unavailable
        packet = load(root/'components'/rid/'legacy-features.json')
        assert packet['source_ref']['source_sha256'] == sha(raw_path)
        assert packet['durable_available_at'] is None and packet['kind'] == 'legacy'
        assert at(packet['available_by_at']) == at(raw['timeline']['acquisition_finished_at'])
        assert packet['input_sha256'] == digest(dict(rows=packet['rows'], history=packet['history']))
        unsigned = dict(packet)
        unsigned.pop('packet_sha256')
        assert packet['packet_sha256'] == digest(unsigned)
        assert {(r['horse_id'],r['horse_number']) for r in packet['rows']} == expected_field
        original = {h:dict(zip(primary['inputs']['columns'],values,strict=True))
            for h,values in primary['inputs']['rows'].items()}
        for r in packet['rows']:
            assert all(r['features'][k] == original[r['horse_id']][k] for k in r['features'])
            assert all(r['features'][k] is None for k in ('weight','weight_diff','carried_weight_ratio'))
        guards = load(root/'components'/rid/'guard-components.json')
        for stage, guard in guards.items():
            unsigned = dict(guard)
            unsigned.pop('evidence_sha256')
            assert guard['evidence_sha256'] == digest(unsigned)
            assert guard['source_sha256'] == sha(raw_path) and guard['available_by_at'] is None
            assert guard['pending'] is True
            assert guard['field_reobserved'] == (stage == 'before')
            assert {(r['horse_id'],r['horse_number']) for r in guard['field']} == expected_field
            if stage == 'decision':
                assert 'decision_guard_not_saved' in guard['incomplete_reasons']
            if stage in ('after','decision'):
                assert 'field_not_reobserved' in guard['incomplete_reasons']
    assert found_horses == report['horses'] == 621 and before_target == report['legacy_memory_available_by_fixed_target'] == 45
    inventory = load(root/'source-inventory.json')
    assert inventory['races'] == 46 and inventory['horses'] == 621 and inventory['strict_pairs_eligible'] == 0
    assert all(x['asof_availability_proven'] is False and x['values_used_for_decision'] is False
        for x in inventory['mev_materialized_tables'])
    model_spec = load(ROOT/'artifacts/market_ev/mev-ens15-v1/ensemble.spec.json')
    required_mev = set(model_spec['features']+model_spec['cats'])
    complete_mev = 0
    for table in inventory['mev_materialized_tables']:
        parquet = pq.ParquetFile(ROOT/table['path'])
        schema = parquet.schema_arrow
        assert table['rows'] == parquet.metadata.num_rows
        assert table['schema_sha256'] == hashlib.sha256(schema.serialize().to_pybytes()).hexdigest()
        complete = required_mev <= set(schema.names)
        assert table['complete_mev_columns'] == complete
        complete_mev += complete
    assert complete_mev == report['complete_mev_materialized_tables']
    replay_path = root/'journal/component-replays/202609040611/legacy/prediction.json'
    replay = load(replay_path)
    original = load(ROOT/replay['source_ref']['source_path'])
    packet = load(root/'components/202609040611/legacy-features.json')
    probabilities = replay['result']['probabilities']
    assert len(probabilities) == 16 and len(packet['history']) == 340
    assert replay['historical_ready_receipt_created'] is replay['usable_as_original_prediction'] is False
    assert at(replay['original_feature_available_at']) == at(packet['available_by_at'])
    assert at(replay['started_at']) <= at(replay['computed_at']) and at(replay['started_at']) > at(original['scheduled_start'])
    assert replay['input_sha256'] == packet['input_sha256']
    heads = original['regimes']['preweight']['new_research_heads_not_served']
    maximum = max(abs(probabilities[h]-v[0]) for h,v in heads.items())
    assert maximum <= 1e-14 and math.isclose(maximum,report['current_replay']['max_abs_original_probability_difference'],abs_tol=1e-15)
    ready = load(replay_path.with_name('current-ready.json'))
    assert ready['historical'] is False and ready['source_sha256'] == digest(replay)
    assert at(ready['available_by_at']) >= at(replay['computed_at'])
    bundle = dict(race_date=original['race_day'], predictions=dict(legacy=dict(
        evidence=replay['result']['evidence'], rows=[dict(horse_id=h,probability=p) for h,p in probabilities.items()])))
    native = legacy_native(bundle)
    official = support.historical_replay(root)
    clocks = load(root/'clock.json')
    observed = {o['at'] for o in clocks['observations']}
    assert replay['started_at'] in observed and replay['computed_at'] in observed and ready['available_by_at'] in observed
    for before,after in zip(clocks['observations'],clocks['observations'][1:]):
        assert before['epoch_id'] == after['epoch_id']
        wall=(at(after['at'])-at(before['at'])).total_seconds()
        mono=(after['monotonic_ns']-before['monotonic_ns'])/1e9
        assert wall >= 0 and mono >= 0 and abs(wall-mono) <= 1
    return dict(status='passed',mode='offline_asof_connection',eligible_for_prospective=False,
        source_records=46,source_horses=621,legacy_memory_available_by_fixed_target=45,
        contract_ready=0,historical_seals_created=0,new_comparison_races=0,
        main_164_adapters_imported=False,native_saved_legacy_replay=native,
        actual_saved_legacy_horses=16,actual_saved_history_rows=340,official_replay=official,
        manifest_sha256=sha(root/'manifest.json'))


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    result=audit(args.out)
    (Path(__file__).parent/'independent-asof-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
