"""Semantic golden case for frozen Feature 084 outcome ranks (SC-008 / FR-017)."""

from __future__ import annotations

import datetime
import json
from decimal import Decimal
from pathlib import Path

import pytest
from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import ChaosSnapshot, Horse, Race, RaceHorse, RaceResult
from horseracing_probability.chaos_events import CHAOS_EVENTS_V1
from sqlalchemy import select

from horseracing_live.chaos_capture import capture_chaos, load_current_chaos_artifact

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE_METADATA_PATH = (
    _REPO_ROOT / "training" / "tests" / "fixtures" / "chaos_outcome_fixture.json"
)
_PLACEHOLDER_SHA256 = "0" * 64
_RACE_ID = "202607260201"
_RACE_DATE = datetime.date(2026, 7, 26)
_CAPTURED_AT = datetime.datetime(2026, 7, 26, 5, 30, tzinfo=datetime.UTC)
_POST_TIME = datetime.datetime(2026, 7, 26, 6, 0, tzinfo=datetime.UTC)


def _fixture_skip_reason() -> str | None:
    try:
        metadata = json.loads(_FIXTURE_METADATA_PATH.read_text(encoding="utf-8"))
        digest = str(metadata["sha256"])
        parquet_path = _FIXTURE_METADATA_PATH.parent / str(metadata["parquet"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return f"SC-008 frozen fixture metadata is unavailable: {exc}"
    if digest == _PLACEHOLDER_SHA256:
        return "SC-008 frozen fixture SHA-256 is the ORCHESTRATOR placeholder"
    if not parquet_path.is_file():
        return f"SC-008 frozen parquet fixture is absent: {parquet_path}"
    return None


_FIXTURE_SKIP_REASON = _fixture_skip_reason()
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        _FIXTURE_SKIP_REASON is not None,
        reason=_FIXTURE_SKIP_REASON or "SC-008 frozen fixture is ready",
    ),
]


class StubFetcher:
    source = "fixture-adapter"

    def get(self, _url: str, *, use_cache: bool = True) -> str:
        assert use_cache is False
        rows = {
            f"{number:02d}": [str(number + 1.0), "0.0", str(number)]
            for number in range(1, 19)
        }
        return json.dumps({"data": {"odds": {"1": rows}}})


def _seed_pending_race(session) -> None:
    session.add(
        Race(
            race_id=_RACE_ID,
            race_number=1,
            race_date=_RACE_DATE,
            post_time=_POST_TIME,
        )
    )
    for number in range(1, 19):
        session.add(Horse(horse_id=f"H{number:02d}", horse_name=f"Horse {number}"))
    # Repository convention: the parent race and horses must exist before child rows.
    session.flush()
    for number in range(1, 19):
        session.add(
            RaceHorse(
                race_id=_RACE_ID,
                horse_id=f"H{number:02d}",
                horse_number=number,
                odds=Decimal(str(number + 1.0)),
                popularity=number,
                entry_status=EntryStatus.STARTED,
            )
        )
    session.flush()


def test_semantic_golden_case_uses_frozen_popularity_after_live_rows_mutate(session) -> None:
    _seed_pending_race(session)
    artifact = load_current_chaos_artifact(_RACE_DATE)
    capture = capture_chaos(
        session,
        race_id=_RACE_ID,
        fetcher=StubFetcher(),
        artifact=artifact,
        clock=lambda: _CAPTURED_AT,
    )
    assert capture.captured
    session.flush()

    # Reverse every current popularity after capture.  Reading live DB state would
    # now classify the winner as an 18th favourite and produce the wrong semantics.
    race_horses = list(
        session.scalars(
            select(RaceHorse)
            .where(RaceHorse.race_id == _RACE_ID)
            .order_by(RaceHorse.horse_number)
        )
    )
    for horse in race_horses:
        horse.popularity = 19 - int(horse.horse_number)
    session.add_all(
        [
            RaceResult(
                race_id=_RACE_ID,
                horse_id="H01",
                finish_order=1,
                result_status=ResultStatus.FINISHED,
            ),
            RaceResult(
                race_id=_RACE_ID,
                horse_id="H17",
                finish_order=2,
                result_status=ResultStatus.FINISHED,
            ),
            RaceResult(
                race_id=_RACE_ID,
                horse_id="H18",
                finish_order=3,
                result_status=ResultStatus.FINISHED,
            ),
        ]
    )
    session.flush()

    current_ranks = {
        horse.horse_id: int(horse.popularity) for horse in race_horses
    }
    current_top3_ranks = (
        current_ranks["H01"],
        current_ranks["H17"],
        current_ranks["H18"],
    )
    assert current_top3_ranks == (18, 2, 1)
    events = {event.key: event for event in CHAOS_EVENTS_V1}
    assert sum(current_top3_ranks) == 21
    assert events["himo_are"].predicate(*current_top3_ranks, 18) is False
    assert events["total_collapse"].predicate(*current_top3_ranks, 18) is True

    snapshot = session.get(ChaosSnapshot, capture.chaos_snapshot_id)
    assert snapshot is not None
    frozen_ranks = {
        str(row["horse_id"]): int(row["popularity"]) for row in snapshot.field
    }
    finishers = session.execute(
        select(RaceResult.horse_id, RaceResult.finish_order)
        .where(RaceResult.race_id == _RACE_ID)
        .where(RaceResult.result_status == ResultStatus.FINISHED)
        .where(RaceResult.finish_order.in_((1, 2, 3)))
        .order_by(RaceResult.finish_order)
    ).all()
    assert [finish_order for _horse_id, finish_order in finishers] == [1, 2, 3]
    top3_ranks = tuple(frozen_ranks[str(horse_id)] for horse_id, _finish_order in finishers)
    assert top3_ranks == (1, 17, 18)

    field_size = len(snapshot.field)
    assert sum(top3_ranks) == 36
    assert events["himo_are"].predicate(*top3_ranks, field_size) is True
    assert events["total_collapse"].predicate(*top3_ranks, field_size) is False
