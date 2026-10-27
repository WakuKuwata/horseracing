"""Independent 160 source audit; imports no primary research selectors or settlement code."""
from decimal import Decimal
import gzip
import hashlib
import html
import json
from pathlib import Path
import re

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'artifacts/160-saved-timepoint-expansion'
SNAP = ROOT / 'artifacts/158-prerace-roi-comparison/snapshot'
EVIDENCE = Path(__file__).resolve().parent


def check(v, label):
    if not v:
        raise AssertionError(label)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ts(value):
    v = pd.Timestamp(value)
    check(pd.notna(v) and v.tzinfo is not None, 'Naive or missing time')
    return v.tz_convert('UTC')


def groups(frame, key='race_id'):
    return {str(k): v for k, v in frame.groupby(key)}


def printed_payout(content):
    # Extract only the official win dividend cells, never final odds.
    row = re.search(r'<tr\b[^>]*class="Tansho"[^>]*>(.*?)</tr>', content, re.S)
    check(row is not None, 'Missing official win payout row')
    nums = re.search(r'<td\b[^>]*class="Result"[^>]*>(.*?)</td>', row[1], re.S)
    pays = re.search(r'<td\b[^>]*class="Payout"[^>]*>(.*?)</td>', row[1], re.S)
    numbers = [int(v) for v in re.findall(r'<span\b[^>]*>\s*(\d+)\s*</span>', nums[1])]
    amounts = [int(v.replace(',', '')) for v in re.findall(r'([\d,]+)円', html.unescape(re.sub(r'<[^>]*>', '', pays[1])))]
    check(len(numbers) == len(set(numbers)) == len(amounts) > 0, 'Malformed payout row')
    return dict(zip(numbers, amounts, strict=True))


def main():
    # Only saved local sources are opened; no DB or network client is used.
    for manifest in ['input-sha256.json', 'output-sha256.json']:
        for path, digest in json.loads((OUT / manifest).read_text())['files'].items():
            check(sha(ROOT / path) == digest, '160 frozen file differs: ' + path)
    saved = json.loads((SNAP / 'manifest.json').read_text())
    db = {}
    for name, item in saved['tables'].items():
        path = ROOT / item['path']
        check(sha(path) == item['sha256'], '158 snapshot differs: ' + name)
        db[name] = pd.read_parquet(path)
        check(len(db[name]) == item['rows'], '158 rows differ: ' + name)
    check('odds' not in db['entries'], 'Final odds unexpectedly present in entries')
    baseline_path = ROOT / 'artifacts/159-prerace-fresh30/prepared/judgments.parquet'
    bm = json.loads((ROOT / 'artifacts/159-prerace-fresh30/output-sha256.json').read_text())
    check(sha(baseline_path) == bm['files'][str(baseline_path.relative_to(ROOT))], '159 baseline differs')
    baseline = pd.read_parquet(baseline_path)
    race = db['races'].set_index('race_id')
    entries, results, payouts, mev, runs = [groups(db[n]) for n in ('entries', 'results', 'payouts', 'mev', 'runs')]
    predictions, features = [groups(db[n], 'prediction_run_id') for n in ('predictions', 'features')]
    page = pd.read_parquet(ROOT / 'artifacts/155-saved-odds-settlement-audit/audit/archive_pages.parquet')
    page = page.loc[page.selected].set_index('race_id')
    official = 0
    dead_heats = set()
    for rid in race.index:
        e = entries[rid]
        a = e.loc[e.entry_status.eq('started')]
        r, p = results[rid], payouts[rid]
        check(not e.horse_id.duplicated().any() and not e.horse_number.duplicated().any(), 'Duplicate field')
        check(set(a.horse_id) == set(r.horse_id) and len(a) == len(r), 'Incomplete result')
        check(r.result_status.isin(['finished', 'stopped', 'disqualified']).all(), 'Unknown result status')
        f = r.loc[r.result_status.eq('finished')]
        check(f.finish_order.between(1, len(a)).all() and np.equal(f.finish_order, np.floor(f.finish_order)).all(), 'Invalid finish rank')
        byid = a.set_index('horse_id').horse_number.to_dict()
        winners = {int(byid[h]) for h in f.loc[f.finish_order.eq(1), 'horse_id']}
        check(winners and winners == set(p.horse_number) and len(winners) == len(p), 'Payout winner set differs')
        if len(winners) > 1:
            dead_heats.add(rid)
        check(p.source.eq('netkeiba_result').all() and p.payout_yen.ge(100).all(), 'Payout source/value')
        pg = page.loc[rid]
        archive = ROOT / pg.archive_path
        check(sha(archive) == pg.archive_gz_sha256, 'Official source gzip differs')
        raw = gzip.decompress(archive.read_bytes())
        check(hashlib.sha256(raw).hexdigest() == pg.archive_raw_sha256, 'Official source bytes differ')
        content = raw.decode(pg.decode_encoding, errors='replace')
        check(hashlib.sha256(content.encode()).hexdigest() == pg.html_utf8_sha256, 'Official source text differs')
        check(printed_payout(content) == dict(zip(p.horse_number, p.payout_yen, strict=True)), 'Official printed dividend differs')
        official += 1

    bundle_path = ROOT / 'artifacts/129-candidate-mixture-serving/bundle.json'
    bundle = json.loads(bundle_path.read_text())
    check(bundle['train_through'] == '2026-08-23' and bundle['coefficient_train_through'] == '2025-12-31', 'Old model training cutoff')
    for member in bundle['members']:
        for name, digest in member['files'].items():
            check(sha(bundle_path.parent / member['artifact_dir'] / name) == digest, 'Old model member differs')
    registration = db['model'].iloc[0]
    check(json.loads(registration.metrics_summary)['registration']['bundle_sha256'] == sha(bundle_path), 'Old registered SHA')
    check(sha(Path(registration.weights_uri)) == sha(bundle_path), 'Old registered bundle')
    mevdir = ROOT / 'artifacts/market_ev/mev-ens15-v1'
    spec = json.loads((mevdir / 'ensemble.spec.json').read_text())
    ensemble = json.loads((mevdir / 'ensemble_2026.json').read_text())
    check(spec['training_cutoff_by_year']['2026']['train_through'] == 2025, 'MEV cutoff')
    check([m['seed'] for m in ensemble['members']] == list(range(1, 16)), 'MEV seeds')
    for m in ensemble['members']:
        check(sha(mevdir / m['path']) == m['sha256'], 'MEV member differs')
    required = {'mev-ens15-v1', 'seeds=1-15', 'threads=1', 'deterministic', 'features=roi-explore-2026-09', 'drop=sameday,weightlive', 'data>=2007'}
    old_required = {'feat=features-021', 'mix=6:' + sha(bundle_path)[:12], 'coef=2026', 'wregime=serving'}
    base_missing = [rid for rid, g in mev.items() if not all(required <= set(s.split(';')) for s in g.logic_version)]
    exact = []
    for q in db['chaos'].to_dict('records'):
        rid = q['race_id']
        field = json.loads(q['field_json'])
        digest = hashlib.sha256(json.dumps(field, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        check(digest == q['content_digest'], 'Chaos digest differs')
        g = mev.get(rid)
        if g is None:
            continue
        byid = {x['horse_id']: x for x in field}
        same = len(byid) == len(field) == len(g) and set(byid) == set(g.horse_id) and all(
            byid[h]['horse_number'] == n and Decimal(str(byid[h]['odds'])) == Decimal(str(o))
            for h, n, o in zip(g.horse_id, g.horse_number, g.odds_used, strict=True))
        if same:
            moment = max(ts(q['captured_at']), ts(q['created_at']), max(map(ts, g.computed_at)))
            exact.append((rid, moment, moment < ts(race.loc[rid, 'post_time']), (moment-ts(q['captured_at'])).total_seconds()))
    earliest_fresh = [r for r in exact if r[2] and 0 <= r[3] <= 1800]
    independently_chosen = []
    supported = 0
    for rid, g in mev.items():
        row = race.loc[rid]
        e = entries[rid].loc[entries[rid].entry_status.eq('started')]
        ids = set(e.horse_id)
        common_ok = (row.track_type != '障' and rid not in dead_heats and str(row.race_date).startswith('2026')
            and len(g) == len(e) and not g.horse_id.duplicated().any() and set(g.horse_id) == ids
            and dict(zip(g.horse_id, g.horse_number)) == dict(zip(e.horse_id, e.horse_number))
            and g.model_version.eq('mev-ens15-v1').all()
            and all(required | {'oddsobs=quote'} <= set(s.split(';')) for s in g.logic_version)
            and all(g[c].notna().all() and g[c].nunique() == 1 for c in ('run_id', 'computed_at', 'odds_observed_at', 'booster', 'booster_sha256'))
            and g.result_pending_at_compute.eq(True).all()
            and g.booster.eq('ensemble_2026.json').all() and g.booster_sha256.eq(sha(mevdir / 'ensemble_2026.json')).all())
        if not common_ok:
            continue
        probs = pd.to_numeric(g.win_prob).to_numpy(float)
        odds = pd.to_numeric(g.odds_used).to_numpy(float)
        check(np.isfinite(probs).all() and ((probs > 0) & (probs < 1)).all(), 'Invalid stored MEV probabilities')
        check(np.isfinite(odds).all() and (odds >= 1).all(), 'Invalid stored MEV prices')
        quote, compute, post = ts(g.odds_observed_at.iloc[0]), ts(g.computed_at.iloc[0]), ts(row.post_time)
        if not quote <= compute < post:
            continue
        check(all(Decimal(p)*Decimal(o) == Decimal(ev) for p, o, ev in zip(g.win_prob, g.odds_used, g.expected_return)), 'MEV expected return identity')
        eligible = []
        for old in runs.get(rid, pd.DataFrame()).to_dict('records'):
            key = old['prediction_run_id']
            p, f = predictions.get(key), features.get(key)
            if p is None or f is None or old['model_version'] != 'mix-129-nj6' or not old_required <= set(old['logic_version'].split(';')):
                continue
            if not all(len(v) == len(e) and set(v.horse_id) == ids and not v.horse_id.duplicated().any() for v in (p, f)):
                continue
            if not f.feature_version.eq('features-021').all():
                continue
            payloads = [json.loads(v) for v in f.features_json]
            if not all(all(k in v and v[k] is None for k in ('weight', 'weight_diff', 'carried_weight_ratio')) for v in payloads):
                continue
            probs = pd.to_numeric(p.win_prob).to_numpy(float)
            if not (np.isfinite(probs).all() and ((probs > 0) & (probs < 1)).all() and abs(probs.sum()-1) <= 1e-8):
                continue
            clocks = [ts(old[k]) for k in ('computed_at', 'created_at', 'updated_at')]
            clocks += [ts(x) for v in (p, f) for k in ('created_at', 'updated_at') for x in v[k]]
            available = max(clocks)
            decision = max(quote, compute, available)
            if decision < post and pd.Timedelta(0) <= decision - quote <= pd.Timedelta(minutes=30) and bundle['train_through'] < str(row.race_date):
                eligible.append((decision, ts(old['computed_at']), key, available))
        if eligible:
            earliest = min(v[0] for v in eligible)
            choice = max((v for v in eligible if v[0] == earliest), key=lambda v: (v[1], v[2]))
            independently_chosen.append((rid, choice[0], choice[2], len(eligible)))
            supported += len(eligible)
    actual = pd.read_parquet(OUT / 'eligible_decisions.parquet')
    chosen = {rid: (d, key, n) for rid, d, key, n in independently_chosen}
    check(set(chosen) == set(baseline.race_id) == set(actual.race_id), 'Eligible race set differs')
    for v in actual.itertuples():
        check(chosen[v.race_id] == (ts(v.decision_at), v.old_prediction_run_id, v.supporting_old_run_choices), 'Choice differs')
    prior = baseline.groupby('race_id').first()
    for rid, (d, key, n) in chosen.items():
        check(d == ts(prior.loc[rid, 'decision_at']) and key == prior.loc[rid, 'old_prediction_run_id'], 'Baseline decision/run differs')
    result = dict(status='passed', official_complete_races=official, saved_mev_rows=len(db['mev']),
        saved_mev_races=len(mev), saved_old_runs=len(db['runs']), saved_old_prediction_rows=len(db['predictions']),
        saved_chaos_snapshots=len(db['chaos']), exact_price_matching_chaos_races=len(exact),
        price_matching_chaos_fresh_and_prestart_before_old_check=len(earliest_fresh), eligible_races=len(chosen),
        eligible_horses=len(baseline), eligible_days=sorted(set(baseline.race_date)), eligible_day_count=len(set(baseline.race_date)), added_races=0,
        added_decision_times=0, eligible_old_run_choices=supported, mev_races_missing_base_logic_tokens=base_missing,
        original_baseline_decisions_and_old_runs_preserved=True, official_html_payouts_reparsed=True,
        primary_selector_imported=False, database_access=False, new_collection=False, pnl_scored=False,
        can_adopt=False, eligible_for_verdict=False,
        note='11 old run alternatives support 9 identical decision points; they are not 11 independent observations.',
        reviewed_output_sha256=sha(OUT / 'output-sha256.json'))
    summary = json.loads((OUT / 'summary.json').read_text())
    for k, v in result.items():
        if k in summary:
            check(v == summary[k], 'Summary differs: ' + k)
    (EVIDENCE / 'independent-review.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
