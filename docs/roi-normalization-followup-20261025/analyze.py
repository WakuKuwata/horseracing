"""Describe frozen148 bet changes only; no fitting, threshold search or new policy."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / 'artifacts/148-postprediction-normalization/comparison'
OUT = ROOT / 'artifacts/roi-normalization-followup-20261025'
META = ['race_id', 'horse_id', 'horse_number', 'race_date', 'year', 'odds', 'won', 'field_size']
KEYS = ['race_id', 'horse_id']
ARMS = ['baseline', 'weighted_baseline', 'normalized_baseline', 'normalized_weighted_baseline']
KNOWN = '202608030111'


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def group_record(sub):
    result = dict(rows=len(sub), wins=int(sub.won.sum()), delta_yen=float(sub.delta.sum()))
    for label in ('raw', 'normalized'):
        stake = sub['stake_'+label]
        payout = stake * sub.won * sub.odds
        result[label] = dict(n_bets=int((stake>0).sum()), stake_yen=float(stake.sum()),
            payout_yen=float(payout.sum()), profit_yen=float((payout-stake).sum()),
            roi=float(payout.sum()/stake.sum()) if stake.sum() else None)
    return result


def details(d):
    race = d.groupby(['race_date','race_id']).delta.sum().reset_index()
    known = race.race_id.eq(KNOWN)
    groups = {name:group_record(sub) for name,sub in d.groupby('group', sort=True)}
    total = float(d.delta.sum())
    assert abs(sum(r['delta_yen'] for r in groups.values())-total) < 1e-8
    return dict(population=dict(races=d.race_id.nunique(), horses=len(d), days=d.race_date.nunique()),
        totals=group_record(d), groups=groups, known_race_delta_yen=float(race.loc[known,'delta'].sum()),
        without_known_race_delta_yen=float(race.loc[~known,'delta'].sum()),
        top_negative_races=race.nsmallest(10,'delta').to_dict('records'),
        top_positive_races=race.nlargest(10,'delta').to_dict('records'),
        by_mass={name:group_record(sub) for name,sub in d.groupby('mass_side')},
        raw_mass_summary=dict(mean=float(d.groupby('race_id').mass.first().mean()),
            quantiles={str(q):float(d.groupby('race_id').mass.first().quantile(q)) for q in (.01,.25,.5,.75,.99)}))


def run():
    paths = [Path(__file__).resolve(), FOLDER/'summary.json',
        ROOT/'specs/148-postprediction-normalization/evidence/independent_final.json',
        FOLDER.parent/'protocol.json', FOLDER.parent/'protocol.sha256']
    for arm in ARMS:
        paths.extend(FOLDER/f'{arm}_{part}.parquet' for part in ('ensemble','bets','races'))
    hashes = {str(p.relative_to(ROOT)):sha(p) for p in sorted(paths)}
    audit = json.loads((ROOT/'specs/148-postprediction-normalization/evidence/independent_final.json').read_text())
    assert audit['summary_sha256'] == sha(FOLDER/'summary.json') and not audit['blockers']
    assert audit['protocol_sha256'] == sha(FOLDER.parent/'protocol.json')
    summary = json.loads((FOLDER/'summary.json').read_text())
    frames = {}
    for arm in ARMS:
        pred = pd.read_parquet(FOLDER/f'{arm}_ensemble.parquet').reset_index(drop=True)
        if frames:
            pd.testing.assert_frame_equal(pred[META],frames['baseline'][META],check_exact=True)
        bets = pd.read_parquet(FOLDER/f'{arm}_bets.parquet').set_index(KEYS)
        assert not bets.index.duplicated().any()
        pred['stake'] = bets.stake_yen.reindex(pd.MultiIndex.from_frame(pred[KEYS]),fill_value=0).to_numpy()
        pred['profit'] = pred.stake*(pred.won*pred.odds-1)
        expected=summary['arms'][arm]['policy']
        assert abs(pred.profit.sum()-expected['profit_yen']) < 1e-8
        assert abs(pred.stake.sum()-expected['stake_yen']) < 1e-8
        frames[arm]=pred
    results = {}
    for parent in ('baseline','weighted_baseline'):
        raw,new = frames[parent], frames['normalized_'+parent]
        mass = raw.groupby('race_id').p.transform('sum')
        np.testing.assert_array_equal(raw.p.to_numpy()/mass.to_numpy(),new.p)
        d = raw[META].copy()
        d['p_raw'],d['p_normalized'],d['mass'] = raw.p,new.p,mass
        d['raw_ev'],d['normalized_ev'] = raw.p*raw.odds,new.p*new.odds
        d['stake_raw'],d['stake_normalized'] = raw.stake,new.stake
        d['delta'] = (new.stake-raw.stake)*(raw.won*raw.odds-1)
        bought_raw,bought_new = raw.stake>0,new.stake>0
        d['group'] = np.select([bought_new & ~bought_raw,bought_raw & ~bought_new,
            bought_new & bought_raw & (raw.stake != new.stake),bought_new & bought_raw],
            ['added','removed','common_stake_changed','common_same_stake'],default='neither')
        d['mass_side'] = np.where(mass>1,'above1',np.where(mass<1,'below1','equal1'))
        records = dict(all=details(d), before2026=details(d[d.year.lt(2026)]), year2026=details(d[d.year.eq(2026)]),
            by_year={str(year):details(sub) for year,sub in d.groupby('year')},
            jan1_jun21_by_year={str(year):details(sub) for year,sub in d[d.race_date.str[5:].le('06-21')].groupby('year')},
            known_race=d[d.race_id.eq(KNOWN)].to_dict('records'))
        actual = records['all']['totals']['delta_yen']
        expected = summary['arms']['normalized_'+parent]['policy']['profit_yen']-summary['arms'][parent]['policy']['profit_yen']
        assert abs(actual-expected) < 1e-8
        results[parent]=records
    result=dict(new_fits=0,new_policies_scored=0,purpose='Post-result attribution of frozen selections; no causal or tradable subgroup claim',
        parents=results,source_sha256=hashes,can_adopt=False,untouched_holdout=False)
    for name,digest in hashes.items():
        assert sha(ROOT/name)==digest
    OUT.mkdir(exist_ok=False,parents=True)
    (OUT/'attribution.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps({p:{period:r[period]['totals']['delta_yen'] for period in ('all','before2026','year2026')} for p,r in results.items()}))


if __name__ == '__main__':
    run()
