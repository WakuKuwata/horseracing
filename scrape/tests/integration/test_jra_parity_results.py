"""176 T079 (SC-003): netkeiba (archive, netkeiba alone) vs official results for Tokyo 2026-10-31.

The netkeiba side is ``parity/netkeiba_20261031_tokyo_post_result.json`` (the archived result
pages, before any official write). It is written first through the netkeiba route; the official
route then runs on top and must find nothing to disagree with: every race is
``results_existing_races`` with zero ``results_mismatch``, and every 単勝 payout is a same-value
confirmation (no row added or removed).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from horseracing_db.enums import ResultStatus, Source
from horseracing_db.models import OfficialWinPayout
from sqlalchemy import select

from horseracing_scrape.jra.upsert_jra import upsert_jra_payouts, upsert_jra_results
from horseracing_scrape.models import ResolvedResultRow, ScrapedWinPayout
from horseracing_scrape.upsert import PER_HORSE_LEGACY, _insert_results, upsert_official_win_payouts
from tests._seed_results176 import results_observed, seed_from_results

pytestmark = pytest.mark.integration
FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "jra"
    / "parity"
    / "netkeiba_20261031_tokyo_post_result.json"
)


def test_results_parity_tokyo_20261031(session):
    import datetime

    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    page = seed_from_results(session)
    nk = {rec["race_id"]: rec for rec in doc["races"]}
    # 1) netkeiba alone
    for rid, rec in nk.items():
        rows = [
            ResolvedResultRow(
                r["netkeiba_horse_id"],
                r["finish_order"],
                r["result_status"],
                r["finish_time"],
                r["last_3f"],
                tuple(r["corner_orders"]) if r["corner_orders"] else None,
            )
            for r in rec["rows"]
        ]
        _insert_results(session, rid, rows, mode=PER_HORSE_LEGACY, source=Source.NETKEIBA)
        upsert_official_win_payouts(
            session,
            rid,
            [ScrapedWinPayout(p["horse_number"], p["payout_yen"]) for p in rec["win_payouts"]],
            observed_at=datetime.datetime.fromisoformat(rec["archived_at"]),
        )
    session.commit()

    # 2) field-level comparison on comparable races
    comparable = matched = 0
    examples = []
    for b in page.published():
        rec = nk.get(b.race_id)
        if rec is None:
            continue
        by_id = {r["netkeiba_horse_id"]: r for r in rec["rows"]}
        for r in b.payload.rows:
            if r.result_status not in ResultStatus.ALL:
                continue
            n = by_id.get(r.horse_id)
            assert n is not None, (b.race_id, r.horse_id)
            for f, official in (
                ("finish_order", r.finish_order),
                ("result_status", r.result_status),
                ("finish_time", r.finish_time),
                ("last_3f", r.last_3f),
            ):
                comparable += 1
                if n[f] == official:
                    matched += 1
                else:
                    examples.append((b.race_id, r.horse_number, f, n[f], official))
        official_win = {ln.numbers[0]: ln.payout_yen for ln in b.payload.payouts.block("win").lines}
        assert official_win == {p["horse_number"]: p["payout_yen"] for p in rec["win_payouts"]}, (
            b.race_id
        )
    print(f"\nSC-003: races {len(nk)}/12 fields {matched}/{comparable} examples {examples[:10]}")
    assert len(nk) == 12 and comparable > 0
    assert matched == comparable, examples[:20]

    # 3) the official route on top finds nothing to change
    c = upsert_jra_results(session, page, observed_at=results_observed())
    session.commit()
    assert c.extra.get("results_existing_races") == 12
    assert not c.extra.get("results_mismatch"), c.error_messages
    before = {
        (w.race_id, w.horse_number): w.payout_yen
        for w in session.scalars(select(OfficialWinPayout))
    }
    confirmed = 0
    for b in page.published():
        pc = upsert_jra_payouts(
            session, b.race_id, b.payload.payouts, observed_at=results_observed()
        )
        confirmed += pc.extra.get("win_payout_confirmed", 0)
        assert not pc.extra.get("payout_rows_removed")
    session.commit()
    after = {
        (w.race_id, w.horse_number): w.payout_yen
        for w in session.scalars(select(OfficialWinPayout))
    }
    assert after == before and confirmed == len(before)
