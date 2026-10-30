"""175: scoring the T−3 decision records (``decision_quotes``, 174) — pure functions.

Everything here is read-time arithmetic over plain records that the caller (live CLI, later the
read API) assembles from the DB. No DB, no training artifacts, no I/O: the module is
predictor-agnostic so a candidate is just "the EV it assigned to each horse of a reproduced race".

What is fixed before any result is read (spec FR-002/003/004/006/017):

* the funnel — one exclusive class per record, first failing rule wins, so the funnel sums to the
  record count;
* the two displayed columns: 120% mark = ``ev_ens > 1.2``, mixed 100% mark = ``ev_mix > 1.0`` when
  the mixture was available at capture time;
* the settlement: 100 yen on every marked horse, paid the official win payout of its 馬番 (a dead
  heat pays each winner its own figure), recovery = Σreturn / Σstake;
* the interval: the race-day block bootstrap of 138/B8 with ``display_update_rule.BOOTSTRAP``
  (impl / b / seed / block are read, never re-declared);
* the verdict: ``display_update_rule.decide`` on the candidate's settled points captured on/after
  its registration date, with the lower bound of candidate − incumbent from ONE synchronised
  resampling (both rows share the block draws; the difference is taken per replicate);
* the diagnostics (calibration by EV band × odds band, closing price drift, p̂-held retention):
  reported with ``diagnostic: true`` and never part of the verdict.

Vocabulary (FR-020): recovery_rate / marked / diff — never "adopt", "improve" or "profit".
"""

from __future__ import annotations

import datetime
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from horseracing_eval import attention_rules as ar
from horseracing_eval import display_update_rule as dur
from horseracing_eval import market_mix
from horseracing_eval.bootstrap import (
    centered_one_sided_p_from_replicates,
    race_block_ratio_bootstrap_ci_v1,
)

__all__ = [
    "CALIBRATION_EV_BANDS",
    "CALIBRATION_ODDS_BANDS",
    "COLUMNS",
    "EXPECTED_RETURN_THRESHOLD",
    "FUNNEL_ORDER",
    "JUMP_TRACK_TYPE",
    "STAKE_YEN",
    "SCHEMA",
    "WINDOW_TOLERANCE_S",
    "CandidateRace",
    "QuoteHorse",
    "QuoteRecord",
    "build_report",
    "calibration_cells",
    "classify",
    "column_tally",
    "marked_numbers",
    "normalize_148",
    "paired_tally",
    "price_drift",
    "ratio_ci",
    "retention",
    "verdict",
]

SCHEMA = "near-post-score-v1"
STAKE_YEN = 100
JUMP_TRACK_TYPE = "障"
#: the capture window of 174: |seconds_to_post − k×60| ≤ 60 for capture_target "t_minus_<k>"
WINDOW_TOLERANCE_S = 60
#: the displayed marks (api MARKET_EV_THRESHOLD=1.2 is asserted equal to this by the api tests)
EXPECTED_RETURN_THRESHOLD = 1.2
MIX_THRESHOLD = market_mix.MIX_EV_THRESHOLD
COLUMNS = ("expected_return", "expected_return_mix")
FUNNEL_ORDER = (
    "jump_race", "window_out", "official_after_capture", "captured_after_post", "field_mismatch",
    "invalid_values", "pending_result", "payout_race_missing", "payout_inconsistent", "eligible",
)
BOOTSTRAP = dict(dur.BOOTSTRAP)
MIN_POINTS = int(dur.MIN_POINTS)
#: diagnostics bands — lower bound exclusive, upper bound inclusive for EV; [low, high) for odds
CALIBRATION_EV_BANDS = ((-math.inf, 0.8), (0.8, 1.0), (1.0, 1.2), (1.2, math.inf))
CALIBRATION_ODDS_BANDS = ((1.0, 5.0), (5.0, 10.0), (10.0, 20.0), (20.0, 40.0), (40.0, math.inf))
POPULARITY_BANDS = (("pop_1_3", 1, 3), ("pop_4_8", 4, 8), ("pop_9p", 9, 10**9))
SYSTEMS = ("ens15", "single", "mix")
#: spec §2: the expected 2026 point counts at B8's observed densities over the 19 capture-only days
EXPECTED_POINTS_2026 = {"expected_return": 140, "expected_return_mix": 230}
FORBIDDEN_WORDS = ("採用", "改善", "利益", "adopt", "improve", "profit")


# --------------------------------------------------------------------------- records


@dataclass(frozen=True)
class QuoteHorse:
    horse_id: str
    horse_number: int
    win_odds: float | None
    popularity: int | None
    p_ens: float | None
    ev_ens: float | None
    p_single: float | None
    ev_single: float | None
    p_mix: float | None
    ev_mix: float | None
    #: race_date − the horse's most recent STARTED race_date strictly before it (138/market_ev
    #: ``days_since_last``); None = no earlier start known → gap rules do not match.
    days_since_last: float | None = None


@dataclass
class QuoteRecord:
    """One ``decision_quotes`` row joined with what settlement needs (data-model §1)."""

    race_id: str
    race_date: datetime.date | None
    post_time: datetime.datetime | None
    track_type: str | None
    captured_at: datetime.datetime
    official_at: datetime.datetime | None
    seconds_to_post: int
    capture_target: str
    horses: Sequence[QuoteHorse]
    mix_status: str
    field_digest: str
    #: the CURRENT started field: horse_id -> 馬番 (None when unknown)
    started: Mapping[str, int | None]
    has_results: bool
    winner_horse_ids: frozenset[str] = frozenset()
    #: official win payout per 100 yen by 馬番 (empty = none stored)
    payouts: Mapping[int, int] = field(default_factory=dict)
    #: FINAL win odds by 馬番 (None = provenance not final / no result)
    closing: Mapping[int, float] | None = None
    meta: Mapping[str, object] = field(default_factory=dict)

    def winner_numbers(self) -> list[int | None]:
        return [self.started.get(h) for h in self.winner_horse_ids]


def digest_of_started(started: Mapping[str, int | None]) -> str:
    """The 174 ``field_digest`` of a started set (same canonical form as near_post.field_digest)."""
    import hashlib
    import json

    payload = sorted((str(h), int(n)) for h, n in started.items() if n is not None)
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


# --------------------------------------------------------------------------- classification


def _target_offset_s(capture_target: str) -> int | None:
    if not capture_target.startswith("t_minus_"):
        return None
    try:
        return int(capture_target.removeprefix("t_minus_")) * 60
    except ValueError:
        return None


def _finite(x) -> bool:
    return x is not None and math.isfinite(float(x))


def classify(rec: QuoteRecord) -> str:
    """The single exclusive class of a record (FR-002; order = ``FUNNEL_ORDER``)."""
    if rec.track_type is not None and JUMP_TRACK_TYPE in rec.track_type:
        return "jump_race"
    target = _target_offset_s(rec.capture_target)
    if target is None or abs(int(rec.seconds_to_post) - target) > WINDOW_TOLERANCE_S:
        return "window_out"
    if rec.official_at is not None and rec.official_at > rec.captured_at:
        return "official_after_capture"
    if rec.post_time is None or rec.captured_at >= rec.post_time:
        return "captured_after_post"
    stored = {h.horse_id: int(h.horse_number) for h in rec.horses}
    started = {str(k): v for k, v in rec.started.items()}
    if any(v is None for v in started.values()) or stored != started:
        return "field_mismatch"
    if rec.field_digest != digest_of_started(started):
        return "field_mismatch"
    for h in rec.horses:
        if not _finite(h.win_odds) or float(h.win_odds) < 1.0:
            return "invalid_values"
        if not _finite(h.p_ens) or not (0.0 < float(h.p_ens) < 1.0) or not _finite(h.ev_ens):
            return "invalid_values"
        if rec.mix_status == "available" and (
            not _finite(h.p_mix) or not (0.0 < float(h.p_mix) < 1.0) or not _finite(h.ev_mix)
        ):
            return "invalid_values"
    if not rec.has_results:
        return "pending_result"
    if not rec.payouts:
        return "payout_race_missing"
    if not ar.race_payout_consistent(rec.winner_numbers(), rec.payouts.keys()):
        return "payout_inconsistent"
    return "eligible"


def funnel(records: Iterable[QuoteRecord]) -> dict[str, int]:
    counts = {k: 0 for k in FUNNEL_ORDER}
    n = 0
    for r in records:
        counts[classify(r)] += 1
        n += 1
    return {"n_rows": n, **counts}


# --------------------------------------------------------------------------- marks & settlement


def marked_numbers(rec: QuoteRecord, column: str) -> list[int] | None:
    """馬番 marked by the displayed column at capture time; None when the mixed value was absent."""
    if column == "expected_return":
        return [h.horse_number for h in rec.horses if float(h.ev_ens) > EXPECTED_RETURN_THRESHOLD]
    if column == "expected_return_mix":
        if rec.mix_status != "available":
            return None
        return [h.horse_number for h in rec.horses
                if h.ev_mix is not None and market_mix.exceeds_mix_threshold(float(h.ev_mix))]
    raise ValueError(f"unknown column {column!r}")


def _bets(rec: QuoteRecord, numbers: Iterable[int]) -> list[tuple[int, int]]:
    return [(int(n), int(rec.payouts.get(int(n), 0))) for n in numbers]


@dataclass
class _Tally:
    n_races: int = 0
    n_races_mix_unavailable: int = 0
    n_races_with_bets: int = 0
    n_bets: int = 0
    n_hits: int = 0
    return_yen: int = 0
    by_day: dict[str, list[float]] = field(default_factory=lambda: defaultdict(lambda: [0.0, 0.0]))

    def add(self, race_date: datetime.date, bets: Sequence[tuple[int, int]]) -> None:
        self.n_races += 1
        if not bets:
            return
        self.n_races_with_bets += 1
        self.n_bets += len(bets)
        self.n_hits += sum(1 for _n, pay in bets if pay > 0)
        ret = sum(pay for _n, pay in bets)
        self.return_yen += ret
        acc = self.by_day[race_date.isoformat()]
        acc[0] += float(ret)
        acc[1] += float(STAKE_YEN * len(bets))


def ratio_ci(by_day: Mapping[str, Sequence[float]]) -> dict:
    """Σnum/Σden with the frozen race-day block bootstrap (B8's ``_ratio_ci``, as a dict)."""
    days = sorted(by_day)
    if not days:
        return {"point": None, "ci": None, "p_one_sided": None, "n_days": 0}
    num = np.array([by_day[d][0] for d in days], dtype=float)
    den = np.array([by_day[d][1] for d in days], dtype=float)
    res = race_block_ratio_bootstrap_ci_v1(
        num, den, days, block=str(BOOTSTRAP["block"]), b=int(BOOTSTRAP["b"]),
        seed=int(BOOTSTRAP["seed"]),
    )
    point = float(res.point[0]) if math.isfinite(float(res.point[0])) else None
    if point is None or res.no_decision or not math.isfinite(float(res.ci_low[0])):
        return {"point": point, "ci": None, "p_one_sided": None, "n_days": len(days)}
    return {
        "point": point,
        "ci": [float(res.ci_low[0]), float(res.ci_high[0])],
        "p_one_sided": centered_one_sided_p_from_replicates(res.replicates[0], point),
        "n_days": len(days),
    }


def _bootstrap_payload(block_universe: str) -> dict:
    return {
        "impl": str(BOOTSTRAP["impl"]), "b": int(BOOTSTRAP["b"]), "seed": int(BOOTSTRAP["seed"]),
        "block": str(BOOTSTRAP["block"]), "block_universe": block_universe,
    }


def _tally_payload(t: _Tally) -> dict:
    return {
        "n_races": t.n_races, "n_races_with_bets": t.n_races_with_bets, "n_bets": t.n_bets,
        "n_hits": t.n_hits, "stake_yen": STAKE_YEN * t.n_bets, "return_yen": t.return_yen,
        "recovery_rate": (t.return_yen / (STAKE_YEN * t.n_bets)) if t.n_bets else None,
    }


def column_tally(records: Sequence[QuoteRecord], column: str) -> dict:
    """The displayed column's prospective tally over the ELIGIBLE records (US1)."""
    t = _Tally()
    for rec in records:
        if classify(rec) != "eligible":
            continue
        numbers = marked_numbers(rec, column)
        if numbers is None:
            t.n_races_mix_unavailable += 1
            t.add(rec.race_date, [])
            continue
        t.add(rec.race_date, _bets(rec, numbers))
    stats = ratio_ci(t.by_day)
    out = {
        "column": column,
        "threshold": EXPECTED_RETURN_THRESHOLD if column == "expected_return" else MIX_THRESHOLD,
        **_tally_payload(t),
        "n_races_mix_unavailable": t.n_races_mix_unavailable,
        "ci": stats["ci"], "p_one_sided": stats["p_one_sided"], "n_days": stats["n_days"],
        "min_points": MIN_POINTS, "remaining_to_min": max(0, MIN_POINTS - t.n_bets),
        "valuation_basis": "official_win_payout",
        "bootstrap": _bootstrap_payload("race-days with >=1 bet of the column"),
    }
    if stats["point"] is not None:
        out["recovery_rate"] = stats["point"]
    return out


# --------------------------------------------------------------------------- candidates


def normalize_148(p_ens: Sequence[float], odds: Sequence[float]) -> list[float]:
    """Candidate ``ens15-norm148-v1``: p_norm_i = p_i / Σ_started p, EV_norm_i = p_norm_i × odds_i
    (docs/roi-next-proposals-20261025 §148; a post-processing of the displayed model, no training).
    Returns the EVs; the mark is ``EV_norm > 1.2`` (same threshold as the displayed column)."""
    p = np.asarray(p_ens, dtype=float)
    o = np.asarray(odds, dtype=float)
    if p.ndim != 1 or p.shape != o.shape or p.size == 0:
        raise ValueError("p_ens and odds must be 1-D of the same positive length")
    ok = np.all(np.isfinite(p)) and np.all(p > 0.0) and np.all(np.isfinite(o)) and np.all(o >= 1.0)
    if not ok:
        raise ValueError("p_ens must be finite and > 0; odds finite and >= 1")
    return ((p / p.sum()) * o).tolist()


@dataclass(frozen=True)
class CandidateRace:
    """What the candidate assigned on one reproduced race (built by the live replay)."""

    race_id: str
    reproducible: bool
    max_abs_diff: float
    features_source: str  # "parquet" | "rebuilt" | "unavailable"
    features_sha_match: bool | None
    #: candidate EV by 馬番 (None when not reproducible)
    ev_by_number: Mapping[int, float] | None
    #: incumbent EV by 馬番 at the same quote (the stored ev_ens, for the paired incumbent arm)
    incumbent_ev_by_number: Mapping[int, float] | None


def _paired_diff(
    inc_by_day: Mapping[str, Sequence[float]], cand_by_day: Mapping[str, Sequence[float]]
) -> dict:
    """candidate − incumbent recovery with ONE synchronised bootstrap (same block draws; the
    difference is taken per replicate; replicates with a zero denominator in either arm are dropped
    and counted)."""
    days = sorted(set(inc_by_day) | set(cand_by_day))
    if not days:
        return {"diff": None, "diff_ci": None, "n_days": 0, "n_replicates_dropped": None}
    num = np.array([[inc_by_day.get(d, (0.0, 0.0))[0] for d in days],
                    [cand_by_day.get(d, (0.0, 0.0))[0] for d in days]], dtype=float)
    den = np.array([[inc_by_day.get(d, (0.0, 0.0))[1] for d in days],
                    [cand_by_day.get(d, (0.0, 0.0))[1] for d in days]], dtype=float)
    res = race_block_ratio_bootstrap_ci_v1(
        num, den, days, block=str(BOOTSTRAP["block"]), b=int(BOOTSTRAP["b"]),
        seed=int(BOOTSTRAP["seed"]),
    )
    pts = res.point
    finite = math.isfinite(float(pts[0])) and math.isfinite(float(pts[1]))
    diff = float(pts[1] - pts[0]) if finite else None
    if res.no_decision or diff is None:
        return {"diff": diff, "diff_ci": None, "n_days": len(days), "n_replicates_dropped": None}
    reps = res.replicates[1] - res.replicates[0]
    ok = np.isfinite(reps)
    if not ok.any():
        return {"diff": diff, "diff_ci": None, "n_days": len(days),
                "n_replicates_dropped": int((~ok).sum())}
    lo, hi = np.percentile(reps[ok], [2.5, 97.5])
    return {"diff": diff, "diff_ci": [float(lo), float(hi)], "n_days": len(days),
            "n_replicates_dropped": int((~ok).sum())}


def verdict(n_points: int, diff_ci_low: float | None) -> str:
    return dur.decide(n_points=n_points, diff_ci_low=diff_ci_low)


def _in_verdict_window(
    rec: QuoteRecord, registered_on: datetime.date, *, tz: datetime.tzinfo
) -> bool:
    return rec.captured_at.astimezone(tz).date() >= registered_on


def paired_tally(
    records: Sequence[QuoteRecord],
    candidate_races: Mapping[str, CandidateRace],
    *,
    name: str,
    threshold: float,
    registered_on: datetime.date,
    tz: datetime.tzinfo,
) -> dict:
    """Incumbent (120% column at the stored quote) vs candidate on the same reproduced races (US3).

    The verdict uses only the pairs captured on/after ``registered_on`` (local date in ``tz``);
    earlier pairs are reported apart under ``pre_registration``. ``n_points`` = the candidate's
    settled marks inside the verdict window (research D5)."""
    by_rec = {r.race_id: r for r in records}
    n_eligible = n_repro = n_not = 0
    max_abs = 0.0
    sources: dict[str, int] = defaultdict(int)
    sha_match = 0
    windows = {"verdict": (_Tally(), _Tally()), "pre_registration": (_Tally(), _Tally())}
    for race_id, cr in candidate_races.items():
        rec = by_rec.get(race_id)
        if rec is None or classify(rec) != "eligible":
            continue
        n_eligible += 1
        sources[cr.features_source] += 1
        if cr.features_sha_match:
            sha_match += 1
        if not cr.reproducible or cr.ev_by_number is None:
            n_not += 1
            max_abs = max(max_abs, float(cr.max_abs_diff))
            continue
        n_repro += 1
        key = "verdict" if _in_verdict_window(rec, registered_on, tz=tz) else "pre_registration"
        inc, cand = windows[key]
        inc.add(rec.race_date, _bets(rec, marked_numbers(rec, "expected_return") or []))
        cand_marks = [n for n, ev in cr.ev_by_number.items() if float(ev) > threshold]
        cand.add(rec.race_date, _bets(rec, cand_marks))

    def _window(key: str) -> dict:
        inc, cand = windows[key]
        d = _paired_diff(inc.by_day, cand.by_day)
        return {"incumbent": _tally_payload(inc), "candidate": _tally_payload(cand), **d,
                "n_points": cand.n_bets}

    main = _window("verdict")
    pre = _window("pre_registration")
    low = main["diff_ci"][0] if main["diff_ci"] else None
    if main["n_points"] >= MIN_POINTS and low is None:
        v, note = "insufficient", "interval undecidable (fewer than 2 race-day blocks with stake)"
    else:
        v, note = verdict(main["n_points"], low), None
    return {
        "name": name, "threshold": threshold, "registered_on": registered_on.isoformat(),
        "n_races_eligible": n_eligible, "n_races_reproducible": n_repro,
        "n_races_not_reproducible": n_not, "max_abs_diff_p_ens": max_abs,
        "features_source": dict(sources), "features_sha_match": sha_match,
        **main, "verdict": v, "verdict_note": note, "min_points": MIN_POINTS,
        "pre_registration": pre,
        "bootstrap": _bootstrap_payload(
            "race-days with >=1 bet of either arm among the paired races"
        ),
    }


# --------------------------------------------------------------------------- diagnostics


def _band_label(lo: float, hi: float, *, ev: bool) -> str:
    f = lambda x: ("inf" if x == math.inf else "-inf" if x == -math.inf else f"{x:g}")  # noqa: E731
    return f"({f(lo)},{f(hi)}]" if ev else f"[{f(lo)},{f(hi)})"


def _ev_band(ev: float) -> str:
    for lo, hi in CALIBRATION_EV_BANDS:
        if lo < ev <= hi:
            return _band_label(lo, hi, ev=True)
    return "nan"


def _odds_band(odds: float) -> str:
    for lo, hi in CALIBRATION_ODDS_BANDS:
        if lo <= odds < hi:
            return _band_label(lo, hi, ev=False)
    return "nan"


def _system_values(h: QuoteHorse, system: str) -> tuple[float | None, float | None]:
    if system == "ens15":
        return h.p_ens, h.ev_ens
    if system == "single":
        return h.p_single, h.ev_single
    return h.p_mix, h.ev_mix


def calibration_cells(records: Sequence[QuoteRecord]) -> dict:
    """Σwon/Σp̂ by system × EV band × odds band over the eligible records (diagnostic)."""
    cells: dict[tuple[str, str, str], dict] = defaultdict(
        lambda: {"n": 0, "sum_won": 0, "sum_p": 0.0, "by_day": defaultdict(lambda: [0.0, 0.0])}
    )
    for rec in records:
        if classify(rec) != "eligible":
            continue
        winners = {n for n in rec.winner_numbers() if n is not None}
        day = rec.race_date.isoformat()
        for h in rec.horses:
            for system in SYSTEMS:
                p, ev = _system_values(h, system)
                if system == "mix" and rec.mix_status != "available":
                    continue
                if not (_finite(p) and _finite(ev)):
                    continue
                c = cells[(system, _ev_band(float(ev)), _odds_band(float(h.win_odds)))]
                won = 1 if h.horse_number in winners else 0
                c["n"] += 1
                c["sum_won"] += won
                c["sum_p"] += float(p)
                c["by_day"][day][0] += float(won)
                c["by_day"][day][1] += float(p)
    out: dict = {}
    for (system, evb, ob), c in sorted(cells.items()):
        stats = ratio_ci(c["by_day"])
        out.setdefault(system, {}).setdefault(evb, {})[ob] = {
            "n": c["n"], "sum_won": c["sum_won"], "sum_p": c["sum_p"],
            "ratio": (c["sum_won"] / c["sum_p"]) if c["sum_p"] > 0 else None,
            "ci": stats["ci"],
        }
    return {
        "diagnostic": True,
        "definition": "sum_won / sum_p_hat per cell; EV bands (lo,hi], odds bands [lo,hi)",
        "systems": out,
    }


def _dist(values: Sequence[float]) -> dict:
    if not values:
        return {"n": 0, "p10": None, "p50": None, "p90": None, "sd": None, "mean": None}
    v = np.asarray(values, dtype=float)
    p10, p50, p90 = np.percentile(v, [10, 50, 90])
    return {"n": int(v.size), "p10": float(p10), "p50": float(p50), "p90": float(p90),
            "sd": float(v.std(ddof=1)) if v.size > 1 else None, "mean": float(v.mean())}


def price_drift(records: Sequence[QuoteRecord]) -> dict:
    """log(O_F / O_J) per horse over eligible records with a FINAL closing price (diagnostic)."""
    groups: dict[str, list[float]] = defaultdict(list)
    n_unavailable = 0
    for rec in records:
        if classify(rec) != "eligible":
            continue
        if rec.closing is None:
            n_unavailable += 1
            continue
        marked = set(marked_numbers(rec, "expected_return") or [])
        for h in rec.horses:
            of = rec.closing.get(h.horse_number)
            if not (_finite(of) and float(of) >= 1.0 and _finite(h.win_odds)):
                continue
            d = math.log(float(of) / float(h.win_odds))
            groups["all"].append(d)
            if h.horse_number in marked:
                groups["marked"].append(d)
            if h.popularity is not None:
                for label, lo, hi in POPULARITY_BANDS:
                    if lo <= int(h.popularity) <= hi:
                        groups[label].append(d)
    keys = ["all", "marked"] + [b[0] for b in POPULARITY_BANDS]
    return {
        "diagnostic": True, "definition": "log(closing_win_odds / quoted_win_odds)",
        "n_races_closing_unavailable": n_unavailable,
        **{k: _dist(groups.get(k, [])) for k in keys},
    }


def retention(records: Sequence[QuoteRecord]) -> dict:
    """Of the 120%-marked horses, the share still above 1.2 when the stored p̂ is re-priced at the
    closing odds (p̂ held fixed — an approximation, labelled as such)."""
    n_marked = n_still = 0
    for rec in records:
        if classify(rec) != "eligible" or rec.closing is None:
            continue
        for h in rec.horses:
            if float(h.ev_ens) > EXPECTED_RETURN_THRESHOLD:
                of = rec.closing.get(h.horse_number)
                if not _finite(of):
                    continue
                n_marked += 1
                if float(h.p_ens) * float(of) > EXPECTED_RETURN_THRESHOLD:
                    n_still += 1
    return {"diagnostic": True, "approximation": "p_fixed", "n_marked": n_marked,
            "n_still_marked_at_closing": n_still,
            "rate": (n_still / n_marked) if n_marked else None}


# --------------------------------------------------------------------------- attention (138 S1〜S5)

ATTENTION_RULE_IDS: tuple[str, ...] = tuple(ar.RULE_IDS)
GAP_DEFINITION = (
    "days_since_last = race_date − the horse's most recent STARTED race_date strictly before it "
    "(the same lag market_ev/138 picks use; None → the gap rules do not match)"
)
ATTENTION_NOTE = (
    "138 の S1〜S5(凍結定義)を T−3 の判断記録に当てた集計。138 の前向き記録(attention_picks)"
    "とは母集団も時刻も別で、138 のチェックポイント判定にも本 spec の三値にも使わない。"
)


def _nan_if_none(x) -> float:
    return math.nan if x is None else float(x)


def attention_marks(rec: QuoteRecord, rule_id: str) -> list[int]:
    """馬番 the frozen rule marks on the capture-time quote (``attention_rules.match_mask`` is THE
    implementation; nothing is re-derived here)."""
    defn = ar.definition(rule_id)
    hs = rec.horses
    mask = ar.match_mask(
        defn,
        ens_ev=[_nan_if_none(h.ev_ens) for h in hs],
        single_ev=[_nan_if_none(h.ev_single) for h in hs],
        odds=[_nan_if_none(h.win_odds) for h in hs],
        days_since_last=[_nan_if_none(h.days_since_last) for h in hs],
    )
    return [h.horse_number for h, m in zip(hs, mask, strict=True) if bool(m)]


def attention_tally(records: Sequence[QuoteRecord], rule_id: str) -> dict:
    """One rule's T−3 tally over the ELIGIBLE records: 100 yen per marked horse at the official
    win payout (same settlement/bootstrap as the columns) + the marked horses' price drift."""
    defn = ar.definition(rule_id)
    t = _Tally()
    drift: list[float] = []
    n_closing_unavailable = 0
    for rec in records:
        if classify(rec) != "eligible":
            continue
        numbers = attention_marks(rec, rule_id)
        t.add(rec.race_date, _bets(rec, numbers))
        if not numbers:
            continue
        if rec.closing is None:
            n_closing_unavailable += 1
            continue
        marked = set(numbers)
        for h in rec.horses:
            if h.horse_number not in marked:
                continue
            of = rec.closing.get(h.horse_number)
            if _finite(of) and float(of) >= 1.0 and _finite(h.win_odds):
                drift.append(math.log(float(of) / float(h.win_odds)))
    stats = ratio_ci(t.by_day)
    out = {
        "rule_id": defn.id, "rank": int(defn.rank), "definition_ja": defn.definition_ja,
        "uses_ensemble": bool(defn.uses_ensemble), "ev_gt": float(defn.ev_gt),
        "odds_band": (list(defn.odds_band) if defn.odds_band is not None else None),
        "gap_days": (list(defn.gap_days) if defn.gap_days is not None else None),
        **_tally_payload(t),
        "ci": stats["ci"], "p_one_sided": stats["p_one_sided"], "n_days": stats["n_days"],
        "valuation_basis": "official_win_payout",
        "bootstrap": _bootstrap_payload("race-days with >=1 bet of the rule"),
        "price_drift_marked": {"definition": "log(closing_win_odds / quoted_win_odds)",
                               **_dist(drift)},
        "n_races_with_bets_closing_unavailable": n_closing_unavailable,
    }
    if stats["point"] is not None:
        out["recovery_rate"] = stats["point"]
    return out


def attention_section(records: Sequence[QuoteRecord]) -> dict:
    return {
        "diagnostic": True, "not_used_for_verdict": True,
        "rule_set_version": ar.RULE_SET_VERSION,
        "definitions_sha256": ar.definitions_sha256(),
        "match_impl": "horseracing_eval.attention_rules.match_mask",
        "gap_definition": GAP_DEFINITION,
        "note": ATTENTION_NOTE,
        "rules": [attention_tally(records, r) for r in ATTENTION_RULE_IDS],
    }


# --------------------------------------------------------------------------- report


def power_note() -> dict:
    return {
        "min_points": MIN_POINTS,
        "verdict_blocked_below_min": True,
        "expected_points_2026_estimate": dict(EXPECTED_POINTS_2026),
        "basis": ("B8 densities (8 points / 38 races; 4 / 12 for the mix) × ~680 races over "
                  "19 capture-only days"),
        "text": (
            f"{MIN_POINTS} 点未満のあいだ三値は insufficient(表示は不変)。"
            f"2026 年内の見込み点数は 120% 列 ≈{EXPECTED_POINTS_2026['expected_return']}・"
            f"混合 ≈{EXPECTED_POINTS_2026['expected_return_mix']} で、"
            "年内に読めるのは較正と価格ずれ。回収率の判定は 2027 年に持ち越し。"
        ),
    }


def build_report(
    records: Sequence[QuoteRecord],
    *,
    candidates: Mapping[str, dict] | None = None,
    candidate_races: Mapping[str, Mapping[str, CandidateRace]] | None = None,
    tz: datetime.tzinfo,
) -> dict:
    """The report body (contracts/report.md) minus run/input metadata the CLI adds."""
    candidates = candidates or {}
    candidate_races = candidate_races or {}
    paired = []
    for name, reg in candidates.items():
        paired.append(paired_tally(
            records, candidate_races.get(name, {}), name=name,
            threshold=float(reg.get("mark_rule", {}).get("threshold", EXPECTED_RETURN_THRESHOLD)),
            registered_on=datetime.date.fromisoformat(str(reg["registered_on"])), tz=tz,
        ))
    return {
        "schema": SCHEMA,
        "power_note": power_note(),
        "rule": {"version": dur.RULE_VERSION, "settlement": dur.SETTLEMENT},
        "funnel": funnel(records),
        "columns": [column_tally(records, c) for c in COLUMNS],
        "paired": paired,
        "verdict_overall": (paired[0]["verdict"] if len(paired) == 1 else
                            ("no_candidate" if not paired else "per_candidate")),
        "attention": attention_section(records),
        "diagnostics": {
            "diagnostic": True, "not_used_for_verdict": True,
            "calibration": calibration_cells(records),
            "price_drift": price_drift(records),
            "retention": retention(records),
        },
    }


def forbidden_vocabulary(obj) -> list[str]:
    """Keys/strings of a report that use a forbidden word (FR-020); empty when clean."""
    hits: list[str] = []

    def walk(x, path):
        if isinstance(x, dict):
            for k, v in x.items():
                if any(w in str(k) for w in FORBIDDEN_WORDS):
                    hits.append(f"{path}.{k}")
                walk(v, f"{path}.{k}")
        elif isinstance(x, (list, tuple)):
            for i, v in enumerate(x):
                walk(v, f"{path}[{i}]")
        elif isinstance(x, str) and any(w in x for w in FORBIDDEN_WORDS):
            hits.append(path)

    walk(obj, "$")
    return hits
