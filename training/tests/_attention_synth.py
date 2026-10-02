"""Feature 138: shared fixtures for the ensemble market-ev / attention-pick integration tests.

* ``seed_race`` — one race (horses, jockeys, entries, optional results, post time) in the shape
  ``market_ev.ROWS_SQL`` reads.
* ``seed_card`` — a card dated in the future (so a computation always happens before the post
  time) with a history race 28 days earlier (``days_since_last`` = 28 inside the 14-112 band).
* ``model_dirs`` — the single-seed and the ensemble directory under the registry's version names
  (an ensemble run refuses any other pair).
* ``ForcedEv`` — wraps the real predictors and overrides the expected return per (race, horse) so
  a test decides exactly which horses match which rules (default: nothing matches).
"""

from __future__ import annotations

import datetime
import pathlib
from decimal import Decimal

from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import Horse, Jockey, Race, RaceHorse, RaceResult, Trainer
from horseracing_eval import attention_rules as ar

from horseracing_training import market_ev
from tests._market_ev_synth import write_ensemble_dir, write_model_dir

ENS = ar.DISPLAYED_MARKET_EV_MODEL_VERSION
SINGLE = ar.SINGLE_SEED_MODEL_VERSION

DAY = datetime.date(2030, 6, 2)
HISTORY_DAY = datetime.date(2030, 5, 5)  # 28 days before DAY
NEXT_DAY = datetime.date(2030, 6, 3)

HIST = "203005050501"
A = "203006020501"  # pending, all priced; H4 (21.4) ran HIST → inside the odds/gap band
B = "203006020502"  # pending, all priced
C = "203006020503"  # H9 unpriced → not race_ok → never computed until priced
D = "203006020504"  # results already in
E = "203006030501"  # the next day


def post_time(day: datetime.date, number: int) -> datetime.datetime:
    return datetime.datetime(day.year, day.month, day.day, 3 + number, tzinfo=datetime.UTC)


def seed_race(session, rid: str, day: datetime.date, number: int, horses: list[tuple], *,
              results: bool, post: datetime.datetime | None = None) -> None:
    """horses: (horse_id, jockey_id, odds) in horse-number order."""
    session.add(Race(race_id=rid, race_date=day, race_number=number, venue_code="05",
                     distance=1600, track_type="芝", going="良", weather="晴",
                     race_class="未勝利", prize_money=500, post_time=post))
    for i, (hid, jid, odds) in enumerate(horses):
        session.merge(Horse(horse_id=hid, horse_name=hid, sire_line="ナスルーラ系"))
        session.merge(Jockey(jockey_id=jid, jockey_name=jid))
        session.merge(Trainer(trainer_id="T1", trainer_name="T1"))
        session.flush()
        session.add(RaceHorse(race_id=rid, horse_id=hid, horse_number=i + 1, frame=i + 1,
                              sex="牡", age=4, jockey_id=jid, trainer_id="T1",
                              jockey_weight=Decimal("55.0"), running_style="先行",
                              odds=None if odds is None else Decimal(str(odds)),
                              popularity=i + 1, entry_status=EntryStatus.STARTED))
        if results:
            session.add(RaceResult(race_id=rid, horse_id=hid, finish_order=i + 1,
                                   result_status=ResultStatus.FINISHED,
                                   last_3f=Decimal("34.5"),
                                   finish_time_diff=datetime.timedelta(seconds=0.1 * i)))
    session.commit()


def seed_card(session) -> None:
    seed_race(session, HIST, HISTORY_DAY, 1,
              [("H1", "J1", 2.4), ("H2", "J2", 3.1), ("H3", "J3", 7.8), ("H4", "J1", 15.2),
               ("H10", "J2", 9.9), ("H13", "J3", 12.5)],
              results=True, post=post_time(HISTORY_DAY, 1))
    seed_race(session, A, DAY, 1,
              [("H1", "J1", 2.8), ("H2", "J2", 3.5), ("H3", "J3", 9.1), ("H4", "J4", 21.4)],
              results=False, post=post_time(DAY, 1))
    seed_race(session, B, DAY, 2, [("H5", "J1", 1.9), ("H6", "J2", 4.4), ("H7", "J3", 12.0)],
              results=False, post=post_time(DAY, 2))
    seed_race(session, C, DAY, 3, [("H8", "J1", 3.3), ("H9", "J2", None), ("H10", "J3", 25.0)],
              results=False, post=post_time(DAY, 3))
    seed_race(session, D, DAY, 4, [("H11", "J1", 2.2), ("H12", "J2", 5.0), ("H13", "J3", 30.5)],
              results=True, post=post_time(DAY, 4))
    seed_race(session, E, NEXT_DAY, 1, [("H14", "J1", 2.0), ("H15", "J2", 3.0)],
              results=False, post=post_time(NEXT_DAY, 1))


def model_dirs(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """(single, ensemble) — 2025 boosters only: later races fall back to them."""
    single = write_model_dir(tmp_path / SINGLE, years=(2025,))
    ensemble = write_ensemble_dir(tmp_path / ENS, years=(2025,))
    return single, ensemble


class ForcedEv:
    """Override the expected return of the real predictions: ``ens`` / ``single`` map
    (race_id, horse_id) → EV; every other horse gets ``default`` (matches no rule). The tables
    are read at call time, so a test can change them between runs."""

    def __init__(self, monkeypatch, default: float = 0.5):
        self.ens: dict[tuple[str, str], float] = {}
        self.single: dict[tuple[str, str], float] = {}
        self.default = default
        real_ens, real_single = market_ev.predict_ensemble, market_ev.predict
        monkeypatch.setattr(market_ev, "predict_ensemble",
                            lambda m, f: self._apply(real_ens(m, f), self.ens))
        monkeypatch.setattr(market_ev, "predict",
                            lambda m, f: self._apply(real_single(m, f), self.single))

    def _apply(self, out, table):
        out = out.copy()
        ev = [
            table.get((str(r), str(h)), self.default)
            for r, h in zip(out["race_id"], out["horse_id"], strict=True)
        ]
        out["expected_return"] = ev
        out["win_prob"] = out["expected_return"] / out["odds_used"].astype(float)
        return out


def run(session, single: pathlib.Path, ensemble: pathlib.Path | None, *,
        day_from: datetime.date = DAY, day_to: datetime.date = DAY, **kw) -> dict:
    extra = {} if ensemble is None else {"ensemble_dir": ensemble}
    return market_ev.compute_and_persist(session, race_date_from=day_from, race_date_to=day_to,
                                         model_dir=single, **extra, **kw)
