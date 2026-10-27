"""Read-only coverage audit of saved quotes and prediction clocks; never scores returns."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.roi_explore.prerace_compare_158 import inputs as source  # noqa: E402

OUT = ROOT / 'artifacts/160-saved-timepoint-expansion'
BASE = ROOT / 'artifacts/159-prerace-fresh30'


def stamp(value):
    value = pd.Timestamp(value)
    if pd.isna(value) or value.tzinfo is None:
        raise ValueError('missing_or_naive_timestamp')
    return value.tz_convert('UTC')


def decision_time(quote, available, computed, old_available, post):
    quote, available, computed, old_available, post = map(
        stamp, (quote, available, computed, old_available, post))
    decision = max(quote, available, computed, old_available)
    if not decision < post:
        return None, 'not_before_post'
    if not pd.Timedelta(0) <= decision - quote <= pd.Timedelta(minutes=30):
        return None, 'quote_older_than_30_minutes'
    return decision, None


def same_prices(field, group):
    if len(field) != len(group) or len({x['horse_id'] for x in field}) != len(field):
        return False
    by_id = {x['horse_id']: x for x in field}
    if set(by_id) != set(group.horse_id):
        return False
    return all(by_id[h]['horse_number'] == n and source._decimal(by_id[h]['odds']) is not None
        and source._decimal(by_id[h]['odds']) == source._decimal(o)
        for h, n, o in zip(group.horse_id, group.horse_number, group.odds_used, strict=True))


def cached_mev_flags(group, flags):
    retained = [f for f in flags if f not in
        ('mev_quote_or_logic_provenance_unverified', 'quote_not_available_by_decision')]
    # Independent price evidence can replace quote provenance, never model logic.
    required = source.MEV_TOKENS - {'oddsobs=quote'}
    if group is not None and not group.empty and not group.logic_version.map(
        lambda x: isinstance(x, str) and required <= set(x.split(';'))).all():
        retained.append('cached_mev_logic_unverified')
    return retained


def chaos_flags(record, entries):
    field = json.loads(record['field_json'])
    digest = hashlib.sha256(json.dumps(field, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    active = entries.loc[entries.entry_status.eq('started')]
    expected = set(zip(active.horse_id, active.horse_number, strict=True))
    actual = [(x['horse_id'], x['horse_number']) for x in field]
    flags = []
    if record['status'] != 'active' or record['source'] != 'netkeiba':
        flags.append('invalid_quote_status_or_source')
    if record['capture_strength'] != 'confirmatory' or record['capture_policy_version'] != 'capture_policy_v1':
        flags.append('unverified_capture_policy')
    if digest != record['content_digest']:
        flags.append('quote_digest_mismatch')
    if len(field) != record['n'] or len(actual) != len(set(actual)) or set(actual) != expected:
        flags.append('quote_full_field_mismatch')
    if any(source._decimal(x['odds']) is None or source._decimal(x['odds']) < 1 for x in field):
        flags.append('invalid_quote_prices')
    return field, flags


def verify():
    manifest = json.loads((OUT / 'input-sha256.json').read_text())
    for relative, checksum in manifest['files'].items():
        source.require(source.sha(ROOT / relative) == checksum, f'Input changed: {relative}')
    output = json.loads((OUT / 'output-sha256.json').read_text())
    for relative, checksum in output['files'].items():
        source.require(source.sha(ROOT / relative) == checksum, f'Output changed: {relative}')
    return {'verified_inputs': len(manifest['files']), 'verified_outputs': len(output['files'])}


def run():
    tables = source._read_snapshot(source.SNAPSHOT)
    bundle, bundle_sha, mev_sha = source._validate_models(tables['model'])
    baseline_path = BASE / 'prepared/judgments.parquet'
    prior = json.loads((BASE / 'output-sha256.json').read_text())['files']
    source.require(source.sha(baseline_path) == prior[str(baseline_path.relative_to(ROOT))], 'Baseline changed')
    baseline = pd.read_parquet(baseline_path)
    baseline_ids = set(baseline.race_id)
    source.require(len(baseline) == 129 and len(baseline_ids) == 9, 'Unexpected baseline')
    races = tables['races'].set_index('race_id').to_dict('index')
    entries, results, payouts, mev, runs = [source._groups(tables[k]) for k in
        ('entries', 'results', 'payouts', 'mev', 'runs')]
    predictions = source._groups(tables['predictions'], 'prediction_run_id')
    features = source._groups(tables['features'], 'prediction_run_id')
    chaos = source._groups(tables['chaos'])
    pages, archived = source._archive_tables(set(races))
    race_rows, quote_rows, choices = [], [], []
    for rid, race in sorted(races.items()):
        settled = source.settlement_rows(entries[rid], results.get(rid), payouts.get(rid), archived.get(rid))
        source.require(rid in pages.index, 'Official source absent')
        g = mev.get(rid)
        flags = source.mev_flags(g, entries[rid], race, manifest_sha=mev_sha)
        common_flags = cached_mev_flags(g, flags)
        if race['track_type'] == '障':
            common_flags.append('jump_not_in_fixed_model_scope')
        if str(race['race_date'])[:4] != '2026':
            common_flags.append('outside_fixed_model_year')
        if settled.dead_heat.any():
            common_flags.append('dead_heat_outside_151_primary')
        race_rows.append(dict(race_id=rid, race_date=str(race['race_date']),
            baseline=rid in baseline_ids, official_settlement_ready=True,
            mev_flags=';'.join(flags), common_flags=';'.join(common_flags)))
        quotes = []
        if not flags:
            observed = stamp(g.odds_observed_at.iloc[0])
            quotes.append(dict(kind='mev_recorded_quote', quote_id=str(g.run_id.iloc[0]),
                observed=observed, available=observed, flags=[], same=True))
        for ch in chaos.get(rid, pd.DataFrame()).to_dict('records'):
            field, qflags = chaos_flags(ch, entries[rid])
            quotes.append(dict(kind='chaos', quote_id=str(ch['chaos_snapshot_id']),
                observed=stamp(ch['captured_at']),
                available=max(stamp(ch['captured_at']), stamp(ch['created_at'])),
                flags=qflags, same=False if g is None else same_prices(field, g)))
        for quote in quotes:
            row = dict(race_id=rid, race_date=str(race['race_date']), quote_source=quote['kind'],
                quote_id=quote['quote_id'], quote_observed_at=quote['observed'].isoformat(),
                quote_available_at=quote['available'].isoformat(), full_prices_match=quote['same'],
                quote_flags=';'.join(quote['flags']), common_flags=';'.join(common_flags),
                baseline=rid in baseline_ids, eligible=False, supporting_old_run_choices=0)
            # Time-only diagnostic is kept even for snapshots rejected for model provenance.
            if g is not None and g.computed_at.nunique() == 1 and g.computed_at.notna().all():
                earliest = max(quote['available'], stamp(g.computed_at.iloc[0]))
                row.update(mev_computed_at=str(stamp(g.computed_at.iloc[0])),
                    earliest_without_old=str(earliest), age_without_old_seconds=(earliest-quote['observed']).total_seconds(),
                    before_post_without_old=earliest < stamp(race['post_time']))
            if quote['flags'] or not quote['same'] or common_flags:
                row['reason'] = ';'.join(quote['flags'] + ([] if quote['same'] else ['prices_or_field_differ']) + common_flags)
                quote_rows.append(row)
                continue
            eligible = []
            reasons = []
            for old in runs.get(rid, pd.DataFrame()).to_dict('records'):
                run_id = old['prediction_run_id']
                ps, fs = predictions.get(run_id), features.get(run_id)
                if ps is None or fs is None:
                    reasons.append('missing_old_prediction_or_features')
                    continue
                available = source._available_at(old, ps, fs)
                decision, reason = decision_time(quote['observed'], quote['available'],
                    g.computed_at.iloc[0], available, race['post_time'])
                if reason:
                    reasons.append(reason)
                    continue
                bad = source.legacy_flags(old, ps, fs, entries[rid], race, decision=decision,
                    bundle_sha=bundle_sha, train_through=bundle['train_through'],
                    coefficient_train_through=bundle['coefficient_train_through'])
                if bad:
                    reasons.extend(bad)
                else:
                    eligible.append((decision, stamp(old['computed_at']), run_id, available))
            if eligible:
                # Earliest feasible use; then latest eligible old computation. No outcomes enter selection.
                earliest = min(x[0] for x in eligible)
                chosen = max((x for x in eligible if x[0] == earliest), key=lambda x: (x[1], x[2]))
                decision, old_compute, run_id, old_available = chosen
                row.update(eligible=True, reason='', decision_at=str(decision),
                    old_prediction_run_id=run_id, old_computed_at=str(old_compute),
                    old_available_at=str(old_available),
                    quote_age_seconds=(decision-quote['observed']).total_seconds(),
                    supporting_old_run_choices=len(eligible))
                choices.append(row.copy())
            else:
                row['reason'] = ';'.join(sorted(set(reasons))) or 'no_saved_old_runs'
            quote_rows.append(row)
    races_frame = pd.DataFrame(race_rows)
    quotes_frame = pd.DataFrame(quote_rows)
    eligible_frame = pd.DataFrame(choices)
    found = set(eligible_frame.race_id) if len(eligible_frame) else set()
    source.require(found >= baseline_ids, 'Baseline lost')
    # For retained own-quote choices, require exact identity with the frozen baseline.
    own = eligible_frame.loc[eligible_frame.quote_source.eq('mev_recorded_quote')]
    baseline_runs = baseline.groupby('race_id').old_prediction_run_id.first().to_dict()
    source.require(dict(zip(own.race_id, own.old_prediction_run_id, strict=True)) == baseline_runs,
        'Baseline old run differs')
    matching = quotes_frame.loc[quotes_frame.quote_source.eq('chaos') & quotes_frame.full_prices_match]
    ready = matching.loc[matching.before_post_without_old.fillna(False) & matching.age_without_old_seconds.le(1800)]
    summary = dict(official_complete_races=len(races), baseline_races=len(baseline_ids), baseline_horses=len(baseline),
        saved_mev_rows=len(tables['mev']), saved_mev_races=tables['mev'].race_id.nunique(),
        saved_old_runs=len(tables['runs']), saved_old_prediction_rows=len(tables['predictions']),
        saved_chaos_snapshots=len(tables['chaos']),
        exact_price_matching_chaos_races=matching.race_id.nunique(),
        price_matching_chaos_fresh_and_prestart_before_old_check=ready.race_id.nunique(),
        eligible_races=len(found), added_races=len(found-baseline_ids),
        eligible_decision_times=len(eligible_frame.drop_duplicates(['race_id', 'decision_at'])),
        added_decision_times=len(eligible_frame.drop_duplicates(['race_id', 'decision_at']))-len(baseline_ids),
        eligible_old_run_choices=int(eligible_frame.supporting_old_run_choices.sum()),
        eligible_days=sorted(set(eligible_frame.race_date)),
        can_adopt=False, eligible_for_verdict=False, untouched_holdout=False,
        new_collection=False, database_access=False, training=False, inference=False, pnl_scored=False,
        decision_rule='max(quote captured/created, saved MEV computed, all old run/prediction/feature availability); earliest feasible, then latest eligible old run',
        max_quote_age_seconds=1800, settlement='saved official netkeiba_result, archive-confirmed; no odds multiplication',
        interpretation='Coverage audit only. Cached MEV p and odds_used kept together. Quote price match is observation evidence, not proof the MEV consumed that capture.')
    OUT.mkdir(parents=True, exist_ok=True)
    for name, frame in [('races', races_frame), ('quote_candidates', quotes_frame), ('eligible_decisions', eligible_frame)]:
        frame.to_parquet(OUT / f'{name}.parquet', index=False)
        frame.to_csv(OUT / f'{name}.csv', index=False)
    (OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    input_paths = set(source.input_paths()) | {Path(__file__), baseline_path, BASE/'output-sha256.json',
        ROOT/'scripts/tests/test_saved_timepoints_160.py',
        ROOT/'specs/160-saved-timepoint-expansion/protocol.json'}
    (OUT / 'input-sha256.json').write_text(json.dumps(dict(files={str(p.relative_to(ROOT)): source.sha(p)
        for p in sorted(input_paths)}), indent=2)+'\n')
    output_paths = [p for p in OUT.iterdir() if p.name not in ('output-sha256.json',)]
    (OUT / 'output-sha256.json').write_text(json.dumps(dict(files={str(p.relative_to(ROOT)): source.sha(p)
        for p in sorted(output_paths)}), indent=2)+'\n')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'verify':
        print(json.dumps(verify()))
    else:
        run()
