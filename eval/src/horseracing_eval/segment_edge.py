"""Feature 047: segment-wise model-p vs market-q diagnostics (SECONDARY — never a gate).

Splits the 020 market-edge comparison (walk-forward OOS p vs q vs realized win) by
PRE-REGISTERED segment axes fixed in specs/047 BEFORE looking at results (constitution III):
surface / dist_band (020 bins) / q_band / race_class group / field_size band / debut.

Attributes come from race statics (races) + strictly-before entries (debut) + market q only —
NEVER from the race's own result (win is the evaluation label, not a segment input). Same
predictor-agnostic discipline as market_edge (eval does not import training).
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .dataset import load_eval_races
from .market_edge import _market_q, pq_logloss
from .splits import FIRST_VALID_YEAR, expanding_folds

#: 020 dist bins (features/extra_features._DIST_BINS) — kept identical, pre-registered.
_DIST_EDGES = (1400, 1800, 2200)
_Q_EDGES = (0.05, 0.15, 0.30)


def dist_band(distance) -> str:
    if distance is None:
        return "unknown"
    d = float(distance)
    if d <= _DIST_EDGES[0]:
        return "sprint(<=1400)"
    if d <= _DIST_EDGES[1]:
        return "mile(<=1800)"
    if d <= _DIST_EDGES[2]:
        return "mid(<=2200)"
    return "long(>2200)"


def surface_band(track_type) -> str:
    if track_type is None:
        return "unknown"
    t = str(track_type)
    if t.startswith("芝"):
        return "芝"
    if t.startswith("ダ"):
        return "ダート"
    return "その他"


def q_band(q: float) -> str:
    if q < _Q_EDGES[0]:
        return "q<0.05(穴)"
    if q < _Q_EDGES[1]:
        return "0.05-0.15"
    if q < _Q_EDGES[2]:
        return "0.15-0.30"
    return "q>=0.30(本命)"


def class_group(race_class) -> str:
    if race_class is None:
        return "unknown"
    c = str(race_class)
    if "新馬" in c:
        return "新馬"
    if "未勝利" in c:
        return "未勝利"
    if "オープン" in c or "G1" in c or "G2" in c or "G3" in c or "OP" in c:
        return "OP系"
    return "条件"


def field_band(n_started: int) -> str:
    if n_started <= 8:
        return "small(<=8)"
    if n_started <= 13:
        return "mid(9-13)"
    return "large(>=14)"


@dataclass(frozen=True)
class SegmentRow:
    axis: str
    segment: str
    n: int
    win_rate: float
    logloss_p: float
    logloss_q: float
    gap: float          # logloss_p − logloss_q; positive = the market is better here
    mean_p: float
    mean_q: float


@dataclass(frozen=True)
class SegmentEdgeReport:
    n_horses: int
    rows: list[SegmentRow]
    note: str = ("SECONDARY diagnostic (047). Segment definitions are PRE-REGISTERED in "
                 "specs/047 and must not be tuned after seeing results. Not a buy signal.")


def _race_attrs(session: Session, race_ids: set[str]) -> dict[str, tuple]:
    """{race_id -> (distance, track_type, race_class, race_date)} — race statics only."""
    from horseracing_db.models import Race

    rows = session.execute(
        select(Race.race_id, Race.distance, Race.track_type, Race.race_class, Race.race_date)
        .where(Race.race_id.in_(list(race_ids)))
    ).all()
    return {r[0]: (r[1], r[2], r[3], r[4]) for r in rows}


def _first_start_dates(session: Session, horse_ids: set[str]) -> dict[str, datetime.date]:
    """{horse_id -> earliest STARTED race_date} (debut = sample race_date <= this)."""
    from horseracing_db.enums import EntryStatus
    from horseracing_db.models import Race, RaceHorse

    rows = session.execute(
        select(RaceHorse.horse_id, func.min(Race.race_date))
        .join(Race, Race.race_id == RaceHorse.race_id)
        .where(RaceHorse.horse_id.in_(list(horse_ids)))
        .where(RaceHorse.entry_status == EntryStatus.STARTED)
        .group_by(RaceHorse.horse_id)
    ).all()
    return {hid: d for hid, d in rows if d is not None}


def _segment_rows(axis: str, keys: list[str], p, q, win) -> list[SegmentRow]:
    out: list[SegmentRow] = []
    for seg in sorted(set(keys)):
        idx = [i for i, k in enumerate(keys) if k == seg]
        sp = [p[i] for i in idx]
        sq = [q[i] for i in idx]
        sw = [win[i] for i in idx]
        ll = pq_logloss(sp, sq, sw)
        out.append(SegmentRow(
            axis=axis, segment=seg, n=len(idx),
            win_rate=sum(sw) / len(idx),
            logloss_p=ll["logloss_p"], logloss_q=ll["logloss_q"],
            gap=ll["logloss_p"] - ll["logloss_q"],
            mean_p=sum(sp) / len(idx), mean_q=sum(sq) / len(idx),
        ))
    return out


def evaluate_segment_edge(
    session: Session,
    *,
    predictor,
    first_valid_year: int = FIRST_VALID_YEAR,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
) -> SegmentEdgeReport:
    races = load_eval_races(session, start_date=start_date, end_date=end_date)
    # walk-forward OOS collection — identical loop to market_edge, plus per-sample identity
    samples: list[tuple[str, str, datetime.date, int, float, float, int]] = []
    for fold in expanding_folds(races, first_valid_year):
        predictor.fit([er.context for er in fold.train])
        for er in fold.valid:
            preds = predictor.predict_race(er.context)
            horses = er.context.started_horses
            qmap = _market_q([h.result_market.odds if h.result_market else None for h in horses])
            winners = {sl.horse_id for sl in er.labels if sl.win == 1}
            for i, h in enumerate(horses):
                if i not in qmap or h.horse_id not in preds:
                    continue
                samples.append((
                    er.context.race_id, h.horse_id, er.context.race_date, len(horses),
                    float(preds[h.horse_id].win), qmap[i],
                    1 if h.horse_id in winners else 0,
                ))
    if not samples:
        return SegmentEdgeReport(n_horses=0, rows=[])

    attrs = _race_attrs(session, {s[0] for s in samples})
    first_start = _first_start_dates(session, {s[1] for s in samples})

    p = [s[4] for s in samples]
    q = [s[5] for s in samples]
    win = [s[6] for s in samples]
    keys: dict[str, list[str]] = {"surface": [], "dist_band": [], "q_band": [],
                                  "race_class": [], "field_size": [], "debut": []}
    for rid, hid, rdate, n_started, _p, _q, _w in samples:
        distance, track_type, race_class, _rd = attrs.get(rid, (None, None, None, None))
        keys["surface"].append(surface_band(track_type))
        keys["dist_band"].append(dist_band(distance))
        keys["q_band"].append(q_band(_q))
        keys["race_class"].append(class_group(race_class))
        keys["field_size"].append(field_band(n_started))
        fs = first_start.get(hid)
        keys["debut"].append("debut" if fs is None or fs >= rdate else "non-debut")

    rows: list[SegmentRow] = []
    for axis in ("surface", "dist_band", "q_band", "race_class", "field_size", "debut"):
        rows.extend(_segment_rows(axis, keys[axis], p, q, win))
    return SegmentEdgeReport(n_horses=len(samples), rows=rows)
