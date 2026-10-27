"""Historical official settlement replay, deliberately separate from current-clock sessions."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

from scripts.roi_explore.prospective_rehearsal_162.settlement import parse_official
from scripts.roi_explore.prospective_rehearsal_162.contract import require

ROOT = Path(__file__).resolve().parents[3]


def historical_official_replay():
    import pandas as pd
    base = ROOT/'artifacts/159-prerace-fresh30'
    manifest = json.loads((base/'output-sha256.json').read_text())['files']
    def frozen(name):
        path = base/name
        require(hashlib.sha256(path.read_bytes()).hexdigest() == manifest[str(path.relative_to(ROOT))],
            'historical_source_changed')
        return pd.read_parquet(path)
    decisions = frozen('comparison/decisions.parquet')
    settlements = frozen('comparison/settled.parquet')
    require(decisions.race_id.nunique() == 9 and len(decisions) == 129, 'historical_population_changed')
    # Historical result mappings were already frozen as canonical IDs; do not derive new ID aliases.
    keys = ['race_id', 'horse_id', 'horse_number']
    require(set(map(tuple, decisions[keys].to_numpy())) == set(map(tuple,
        settlements[keys].to_numpy())), 'historical_field_mismatch')
    require(settlements.official_payout_yen.gt(0).eq(settlements.won).all(), 'historical_winner_payout_mismatch')
    joined = decisions.merge(settlements[['race_id', 'horse_id', 'official_payout_yen', 'won']],
        on=['race_id', 'horse_id'], validate='one_to_one')
    archive_manifest = json.loads((ROOT/'artifacts/155-saved-odds-settlement-audit/output-sha256.json').read_text())['files']
    pagepath = ROOT/'artifacts/155-saved-odds-settlement-audit/audit/archive_pages.parquet'
    require(hashlib.sha256(pagepath.read_bytes()).hexdigest() == archive_manifest[str(pagepath.relative_to(ROOT))], 'archive_index_changed')
    pages = pd.read_parquet(pagepath)
    checked = []
    for rid, group in settlements.groupby('race_id'):
        selected = pages.loc[pages.selected & pages.race_id.eq(rid)]
        require(len(selected) == 1, 'official_source_ambiguous')
        page = selected.iloc[0]
        path = ROOT/page.archive_path
        compressed = path.read_bytes()
        require(hashlib.sha256(compressed).hexdigest() == page.archive_gz_sha256, 'official_archive_changed')
        raw = gzip.decompress(compressed)
        require(hashlib.sha256(raw).hexdigest() == page.archive_raw_sha256, 'official_raw_changed')
        text = raw.decode(page.decode_encoding, errors='replace')
        require(hashlib.sha256(text.encode()).hexdigest() == page.html_utf8_sha256, 'official_text_changed')
        # The raw parser also returns source horse IDs. Ignore that vector here rather than
        # stripping nk: or silently claiming an unavailable contemporaneous alias mapping.
        _, payouts = parse_official(text, rid)
        expected = {int(r.horse_number): int(r.official_payout_yen) for r in group.itertuples() if r.won}
        require(expected == payouts, 'official_payout_replay_mismatch')
        require(set(group.loc[group.won, 'horse_number']) == set(payouts), 'official_winner_mismatch')
        checked.append(dict(race_id=rid, archive_path=page.archive_path,
            raw_sha256=page.archive_raw_sha256, html_sha256=page.html_utf8_sha256,
            original_observed_at=str(page.fetched_at), official_payouts={str(k): v for k, v in payouts.items()}))
    accounts = {}
    for arm in ('roi_only', 'plus_prediction'):
        selected = joined[arm+'_selected']
        stake = int(selected.sum())*100
        gross = int(joined.loc[selected, 'official_payout_yen'].sum())
        accounts[arm] = dict(tickets=int(selected.sum()), stake_yen=stake, payout_yen=gross,
            profit_yen=gross-stake)
    require(accounts['roi_only'] == accounts['plus_prediction'] == dict(tickets=8, stake_yen=800,
        payout_yen=2590, profit_yen=1790), 'historical_accounting_changed')
    return dict(mode='historical_settlement_only', eligible_for_prospective=False, races=9, horses=129,
        current_session_linked=False, fabricated_past_receipts=False, original_timestamps_preserved=True,
        new_collection=False, accounts=accounts, official_sources=checked,
        limitation='Payout bytes/amounts and frozen canonical settlement/decision linkage checked; no fabricated past seal or reconstructed source-to-canonical ID mapping.')
