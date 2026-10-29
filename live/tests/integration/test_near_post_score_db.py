"""175: scoring against a migrated PostgreSQL — loader joins, settlement by hand, read-only CLI."""

from __future__ import annotations

import datetime
import json
import uuid

import pytest
from horseracing_db.models import DecisionQuote, OfficialWinPayout, Race
from horseracing_eval import decision_quote_scoring as dqs
from sqlalchemy import func, select

from horseracing_live import cli
from horseracing_live import near_post as np_
from horseracing_live import near_post_score as nps
from tests._capture_support import POST_TIME, RACE_DATE, RACE_ID, seed_race, settle_race

pytestmark = pytest.mark.integration

OTHER = "202607260102"


def _seed_quote(session, race_id, *, post_time, p_ens, mix_status="no_coefficients", n=4):
    """A 174 row: started field 1..n with the capture-time quote odds 2+number, 15-seed p_ens."""
    field = [{"horse_id": f"H{i:02d}", "horse_number": i, "win_odds": 2.0 + i, "popularity": i,
              "place_low": None, "place_high": None} for i in range(1, n + 1)]
    pred = [{"horse_id": f"H{i:02d}", "horse_number": i, "p_ens": p, "ev_ens": p * (2.0 + i),
             "p_single": p, "ev_single": p * (2.0 + i), "p_mix": None, "ev_mix": None}
            for i, p in zip(range(1, n + 1), p_ens, strict=True)]
    started = [(f"H{i:02d}", i) for i in range(1, n + 1)]
    captured = post_time - datetime.timedelta(seconds=181)
    session.add(DecisionQuote(
        race_id=race_id, captured_at=captured, official_at=captured - datetime.timedelta(seconds=20),
        post_time=post_time, seconds_to_post=181, capture_target="t_minus_3", source="netkeiba", n=n,
        field=field, pred=pred, quote_digest=np_.quote_digest(field, None),
        field_digest=np_.field_digest(started), features_sha256="f" * 64,
        ensemble_version="mev-ens15-v1", ensemble_manifest="ensemble_2026.json",
        ensemble_manifest_sha256="e" * 64, single_version="mev-binary-v2", single_booster="model_2026.txt",
        single_booster_sha256="b" * 64, mix_version=None, mix_status=mix_status, mix_coefficient_year=None,
        logic_version=np_.LOGIC_VERSION, policy_version=np_.POLICY_VERSION, run_id=uuid.uuid4(),
    ))
    session.flush()


def _seed_race_reusing_horses(session, race_id, *, post_time, n=4):
    from decimal import Decimal

    from horseracing_db.enums import EntryStatus
    from horseracing_db.models import RaceHorse

    session.add(Race(race_id=race_id, race_number=2, race_date=RACE_DATE, post_time=post_time))
    session.flush()
    for i in range(1, n + 1):
        session.add(RaceHorse(race_id=race_id, horse_id=f"H{i:02d}", horse_number=i,
                              odds=Decimal(str(2.0 + i)), popularity=i, entry_status=EntryStatus.STARTED))
    session.flush()


def _seed_two_races(session):
    # race 1: marks 馬番 2 (p .3 × 4.0 = 1.2 → not strictly above) and 馬番 3 (.3 × 5.0 = 1.5) and 4 (.25×6=1.5)
    seed_race(session, race_id=RACE_ID, post_time=POST_TIME, n_started=4)
    _seed_quote(session, RACE_ID, post_time=POST_TIME, p_ens=(0.15, 0.3, 0.3, 0.25))
    settle_race(session, race_id=RACE_ID, horse_id="H03")           # winner = 馬番 3
    session.add(OfficialWinPayout(race_id=RACE_ID, horse_number=3, payout_yen=480,
                                  observed_at=POST_TIME + datetime.timedelta(hours=1)))
    race = session.get(Race, RACE_ID)
    race.win_odds_observed_at = POST_TIME + datetime.timedelta(hours=1)   # FINAL odds provenance
    race.win_odds_official_at = None
    # race 2 (same horses re-entered): result stored but no official payout → excluded race-wise
    _seed_race_reusing_horses(session, OTHER, post_time=POST_TIME + datetime.timedelta(minutes=30))
    _seed_quote(session, OTHER, post_time=POST_TIME + datetime.timedelta(minutes=30), p_ens=(0.5, 0.2, 0.2, 0.1))
    settle_race(session, race_id=OTHER, horse_id="H01")
    session.commit()


def test_loader_joins_and_settlement_match_hand_calculation(session):
    _seed_two_races(session)
    recs = nps.load_quote_records(session, date_from=RACE_DATE, date_to=RACE_DATE)
    assert [r.race_id for r in recs] == [RACE_ID, OTHER]
    r1 = recs[0]
    assert r1.started == {f"H{i:02d}": i for i in range(1, 5)} and r1.has_results
    assert r1.winner_horse_ids == frozenset({"H03"}) and r1.payouts == {3: 480}
    assert r1.closing == {1: 3.0, 2: 4.0, 3: 5.0, 4: 6.0}      # race_horses.odds under final provenance
    assert recs[1].closing is None and recs[1].payouts == {}
    assert dqs.classify(r1) == "eligible" and dqs.classify(recs[1]) == "payout_race_missing"
    report = dqs.build_report(recs, tz=np_.JST)
    col = report["columns"][0]
    # marks of race 1: 馬番 3 (1.5) and 4 (1.5); 馬番 2 is exactly 1.2 → not marked
    assert col["n_races"] == 1 and col["n_bets"] == 2 and col["n_hits"] == 1
    assert col["stake_yen"] == 200 and col["return_yen"] == 480 and col["recovery_rate"] == 2.4
    assert report["funnel"] == {"n_rows": 2, **{k: 0 for k in dqs.FUNNEL_ORDER},
                                "eligible": 1, "payout_race_missing": 1}
    d = report["diagnostics"]["price_drift"]
    assert d["all"]["n"] == 4 and d["all"]["p50"] == 0.0 and d["n_races_closing_unavailable"] == 0


def test_cli_is_read_only_and_writes_one_new_json(session, database_url, tmp_path):
    _seed_two_races(session)
    before = {t: session.scalar(select(func.count()).select_from(t))
              for t in (DecisionQuote, OfficialWinPayout, Race)}
    out = tmp_path / "score.json"
    rc = cli.main(["near-post-score", "--from", RACE_DATE.isoformat(), "--to", RACE_DATE.isoformat(),
                   "--database-url", database_url, "--out", str(out), "--features-dir", str(tmp_path)])
    assert rc == 0 and out.is_file()
    doc = json.loads(out.read_text())
    assert doc["schema"] == dqs.SCHEMA and doc["inputs"]["n_rows"] == 2
    assert doc["columns"][0]["n_bets"] == 2 and doc["verdict_overall"] == "no_candidate"
    assert doc["rule"]["json_sha256"] == nps.RULE_SHA.read_text().split()[0]
    assert dqs.forbidden_vocabulary(doc) == []
    after = {t: session.scalar(select(func.count()).select_from(t))
             for t in (DecisionQuote, OfficialWinPayout, Race)}
    assert after == before
    # append-only: a second run to the same path is refused before touching the DB
    assert cli.main(["near-post-score", "--from", RACE_DATE.isoformat(), "--to", RACE_DATE.isoformat(),
                     "--database-url", database_url, "--out", str(out)]) == 2
