"""Explicit synthetic fixtures: actual model files are pinned, never executed."""
from __future__ import annotations

import datetime as dt
import json

from .contract import MODE, market_features, sha_bytes, time


def bundle(protocol, race_id='202605040907'):
    post = time('2026-10-25T06:00:00+00:00')
    def at(seconds):
        return (post+dt.timedelta(seconds=seconds)).isoformat()
    field = [dict(horse_id=f'synthetic-{i}', horse_number=i) for i in range(1, 4)]
    quote_rows = [dict(**r, odds=str(o), popularity=i) for i, (r, o) in enumerate(
        zip(field, (2, 4, 8), strict=True), 1)]
    raw = json.dumps(dict(race_id=race_id, horses=quote_rows), ensure_ascii=False)
    derived = market_features(quote_rows)
    predictions = {}
    for kind, probs in [('mev', [.4, .3, .15]), ('legacy', [.65, .25, .10])]:
        rows = []
        for r, p in zip(field, probs, strict=True):
            features = dict.fromkeys(protocol['feature_columns'][kind])
            if kind == 'mev':
                features.update({k: v for k, v in derived[r['horse_id']].items() if k in features})
            else:
                features.update(horse_number=r['horse_number'], field_size=len(field))
            rows.append(dict(**r, probability=p, features=features, input_asof=at(-899),
                created_at=at(-895), updated_at=at(-890)))
        predictions[kind] = dict(run_id='fixture-'+kind, producer='synthetic_fixture',
            model_version=protocol['model_versions'][kind], model_sha256=protocol['model_shas'][kind],
            input_available_at=at(-899), started_at=at(-895), computed_at=at(-890), rows=rows)
    return dict(schema_version=1, mode=MODE, simulated_clock=True, eligible_for_prospective=False,
        race_id=race_id, session_id='synthetic-session', post_time=at(0), race_date='2026-10-25',
        field=field, guard_before=dict(at=at(-901), pending=True, field=field, post_time=at(0)),
        quote=dict(format='synthetic_quote_v1', source='synthetic_fixture', cache='bypass',
            fetch_started_at=at(-900), received_at=at(-899), provider_at=None,
            raw_text=raw, raw_sha256=sha_bytes(raw.encode()), rows=quote_rows), predictions=predictions)


def final_guard(b):
    post = time(b['post_time'])
    return dict(at=(post-dt.timedelta(seconds=600)).isoformat(), pending=True,
        field=b['field'], post_time=b['post_time'])


def official(b, *, revision=1, winners=None, missing=False, pending=False):
    winners = {1: 210} if winners is None else winners
    results = [] if pending else [dict(**r, result_status='finished',
        finish_order=1 if r['horse_number'] in winners else 1+len(winners)) for r in b['field']]
    rows = ''.join('<tr><td>{}</td><td>1</td><td>{}</td><td><a href="/horse/{}/">馬</a></td></tr>'.format(
        r['finish_order'], r['horse_number'], r['horse_id']) for r in results)
    payout = '' if missing or pending else '<tr class="Tansho"><td class="Result">'+''.join(
        f'<div><span>{n}</span></div>' for n in winners)+'</td><td class="Payout">'+ '<br/>'.join(
        f'{v:,}円' for v in winners.values())+'</td></tr>'
    html = f'<html><head><link rel="canonical" href="https://race.netkeiba.com/race/result.html?race_id={b["race_id"]}"/></head><body><table class="RaceTable01">{rows}</table><table class="Payout_Detail_Table">{payout}</table></body></html>'
    return dict(race_id=b['race_id'], revision=revision, source='netkeiba_result',
        observed_at=(time(b['post_time'])+dt.timedelta(hours=revision)).isoformat(),
        raw_html=html, raw_sha256=sha_bytes(html.encode()), results=results,
        payouts=[] if missing or pending else [dict(horse_number=n, payout_yen=v) for n, v in winners.items()])
