"""Race dispersion band primitives (Feature 066).

Pure, predictor-agnostic numerics for the "how open is this race" readout (axis A). These are the
SINGLE source for both the read-time API path (``api.dispersion``) and the offline boundary-fit /
OOS diagnostic (``training`` CLI) — no duplicated formulas.

Everything here is a function of the market vote-share q (010) over the canonical field (started
horses with valid win odds). q is NOT a true probability and NEVER re-enters model features
(constitution II). Concentration is summarised by the field-size-comparable NORMALISED entropy
``H = -Σ q·ln q / ln N`` (0 = one horse certain, 1 = uniform); the 5 display bands come from
QUINTILE edges fit on a FROZEN historical window's entropy distribution (results never consulted —
outcome-leak-free by construction, Feature 047/048 discipline).
"""

from __future__ import annotations

import datetime
import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

# Ascending concentration order: low entropy = firm/decided, high entropy = open/chaotic.
# Labels are NEUTRAL descriptions (no danger/value semantics). Display strings live in the front.
BANDS: tuple[str, ...] = ("firm", "somewhat_firm", "standard", "somewhat_open", "open")


def normalized_entropy(q_values: Sequence[float]) -> float | None:
    """``-Σ q·ln q / ln N`` over the canonical field. None when N < 2 (undefined / degenerate).

    q is renormalised defensively (market_implied_win_probs already sums to 1, but callers may pass
    a restricted set). Non-positive entries are ignored.
    """
    vals = [float(v) for v in q_values if v is not None and float(v) > 0.0]
    n = len(vals)
    if n < 2:
        return None
    total = math.fsum(vals)
    if total <= 0.0:
        return None
    h = -math.fsum((v / total) * math.log(v / total) for v in vals)
    return h / math.log(n)


def favorite_win_prob(q_values: Sequence[float]) -> float | None:
    """max(q) — the market's win prob for the favourite (raw numeric fact shown beside the band)."""
    vals = [float(v) for v in q_values if v is not None and float(v) > 0.0]
    return max(vals) if vals else None


def top3_cumulative(q_values: Sequence[float]) -> float | None:
    """Σ of the three largest q — how much of the outcome the top 3 by market account for."""
    vals = sorted((float(v) for v in q_values if v is not None and float(v) > 0.0), reverse=True)
    return math.fsum(vals[:3]) if vals else None


def fit_quintile_edges(entropies: Sequence[float]) -> list[float]:
    """4 edges splitting a FROZEN window's normalised-entropy distribution into 5 bands.

    Only the PREDICTOR distribution (entropy) is read — never race outcomes — so band boundaries
    carry no outcome leakage (Feature 047/048). Deterministic (linear-interpolated quantiles).
    """
    xs = sorted(float(e) for e in entropies if e is not None)
    if len(xs) < 5:
        raise ValueError(f"need >=5 entropy samples to fit quintiles, got {len(xs)}")

    def _quantile(p: float) -> float:
        # Linear interpolation between order statistics (numpy 'linear' method), no numpy dep here.
        idx = p * (len(xs) - 1)
        lo = math.floor(idx)
        hi = math.ceil(idx)
        if lo == hi:
            return xs[lo]
        return xs[lo] + (xs[hi] - xs[lo]) * (idx - lo)

    return [_quantile(p) for p in (0.2, 0.4, 0.6, 0.8)]


def assign_band(entropy: float | None, edges: Sequence[float] | None) -> str | None:
    """Map a normalised entropy to one of BANDS via frozen quintile ``edges``.

    Returns None when entropy is undefined OR no boundary artifact is available (F8: the panel still
    shows raw numbers, it just omits the band label — the read path never recomputes edges).
    """
    if entropy is None or edges is None:
        return None
    band_idx = 0
    for edge in edges:
        if entropy <= edge:
            break
        band_idx += 1
    return BANDS[min(band_idx, len(BANDS) - 1)]


# --- boundary fit (offline, results NEVER consulted) --------------------------

BOUNDARY_METRIC = "normalized_entropy"


@dataclass(frozen=True)
class DispersionBoundary:
    """Reproducible band-boundary artifact (Feature 066 E3, constitution V). All fields audit the
    fit: what metric, which field-size buckets, the frozen window, as_of, version, the 4 quintile
    edges, and how many races fed the fit. NO schema/DB — a JSON file, regenerable from the DB."""

    metric: str
    field_size_buckets: str
    fit_from: str  # ISO date
    fit_to: str  # ISO date
    as_of: str  # ISO date (= fit_to; deterministic, no wall-clock)
    version: str
    quintile_edges: list[float]
    n_races_fit: int

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"

    def write(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_json())
        return p


def _race_entropy_from_odds(odds: Sequence[float | None]) -> float | None:
    """Normalised entropy of the market vote-share implied by win ``odds`` (010). Passing the raw
    inverse-odds is fine: normalized_entropy renormalises internally, so entropy is unchanged."""
    inv = [1.0 / float(o) for o in odds if o is not None and float(o) > 0.0]
    return normalized_entropy(inv)


HIGH_PAYOUT_ODDS = 10.0  # F5: pre-registered — winner's realised win odds >= 10.0 = "high payout".


@dataclass(frozen=True)
class DispersionBandDiagnostic:
    """SECONDARY OOS realised-chaos per band (Feature 066 US3). NEVER an adoption gate (047). n_void
    are races dropped from the outcome denominator (cancellation/no-result). separated_from_prev is
    False when this band's favourite-loss Wilson CI overlaps the previous band's (disclosed, never
    merged — FR-014)."""

    band: str
    n: int
    n_void: int
    favorite_loss_rate: float | None
    high_payout_rate: float | None
    ci_low: float | None
    ci_high: float | None
    separated_from_prev: bool | None


def _wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion. Races are the cluster unit (one favourite-loss
    obs per race), so the per-race interval respects clustering (no intra-race pooling)."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def fit_boundary(
    session,
    *,
    fit_from: datetime.date,
    fit_to: datetime.date,
    field_buckets: str = "global",
    version: str = "dispbands-v1",
) -> DispersionBoundary:
    """Fit 5 bands = quintiles of the FROZEN window's normalised-entropy distribution.

    Only market odds (predictor) are read per race to compute entropy — race RESULTS are never
    consulted for the edges (outcome-leak-free, Feature 047/048). The caller is responsible for
    choosing a window strictly before any display/eval target (FR-016). v1 = ``global`` buckets;
    per-field-size quintiles (v2) are deferred. ``as_of`` = ``fit_to`` (deterministic, no clock).
    """
    if field_buckets != "global":
        raise ValueError(f"only 'global' buckets supported in v1, got {field_buckets!r}")
    from .dataset import load_eval_races

    races = load_eval_races(session, start_date=fit_from, end_date=fit_to)
    entropies: list[float] = []
    for er in races:
        h = _race_entropy_from_odds([hz.result_market.odds for hz in er.context.started_horses])
        if h is not None:
            entropies.append(h)
    edges = fit_quintile_edges(entropies)
    return DispersionBoundary(
        metric=BOUNDARY_METRIC,
        field_size_buckets=field_buckets,
        fit_from=fit_from.isoformat(),
        fit_to=fit_to.isoformat(),
        as_of=fit_to.isoformat(),
        version=version,
        quintile_edges=edges,
        n_races_fit=len(entropies),
    )


def _load_diagnostic_races(session, start_date, end_date):
    """Per race in the window: started (horse_id, odds) + winners (finish_order==1, FINISHED).

    Loads results separately from started horses so cancellation/void races (no finished result) are
    still visible for n_void accounting (F9) — unlike load_eval_races which drops them.
    """
    from collections import defaultdict

    from horseracing_db.enums import EntryStatus, ResultStatus
    from horseracing_db.models import Race, RaceHorse, RaceResult
    from sqlalchemy import select

    races = session.execute(
        select(Race.race_id).where(Race.race_date >= start_date).where(Race.race_date <= end_date)
        .order_by(Race.race_id)
    ).scalars().all()
    race_set = set(races)
    started: dict[str, list] = defaultdict(list)
    for rid, oz in session.execute(
        select(RaceHorse.race_id, RaceHorse.odds).join(Race, Race.race_id == RaceHorse.race_id)
        .where(Race.race_date >= start_date).where(Race.race_date <= end_date)
        .where(RaceHorse.entry_status == EntryStatus.STARTED)
    ):
        started[rid].append(float(oz) if oz is not None else None)
    winners_odds: dict[str, list] = defaultdict(list)
    for rid, oz in session.execute(
        select(Race.race_id, RaceHorse.odds)
        .join(RaceResult, RaceResult.race_id == Race.race_id)
        .join(RaceHorse, (RaceHorse.race_id == RaceResult.race_id)
              & (RaceHorse.horse_id == RaceResult.horse_id))
        .where(Race.race_date >= start_date).where(Race.race_date <= end_date)
        .where(RaceResult.finish_order == 1)
        .where(RaceResult.result_status == ResultStatus.FINISHED)
    ):
        winners_odds[rid].append(float(oz) if oz is not None else None)
    return [(rid, started[rid], winners_odds.get(rid, [])) for rid in races if rid in race_set]


def diagnose_bands(
    session, *, boundary: DispersionBoundary,
    diagnose_from: datetime.date, diagnose_to: datetime.date,
) -> list[DispersionBandDiagnostic]:
    """SECONDARY (never a gate): apply the FROZEN boundary to a LATER OOS window, measuring realised
    chaos per band. The OOS window must be strictly after ``boundary.fit_to`` (caller's duty, FR-016
    — asserted here). Reserved rules (F5/F9, fixed before looking at results): favourite = market q1
    (min odds); favourite_loss = q1 not a winner (dead heat where q1 is a co-winner is NOT a loss);
    high_payout = any winner's realised win odds >= 10.0; cancellation/void (no finished winner) is
    excluded from the denominator and counted in n_void.
    """
    if diagnose_from <= datetime.date.fromisoformat(boundary.fit_to):
        raise ValueError(
            f"OOS window {diagnose_from} must start strictly after fit_to {boundary.fit_to}"
        )
    edges = boundary.quintile_edges
    agg: dict[str, dict] = {b: {"loss": 0, "n": 0, "high": 0, "void": 0} for b in BANDS}
    for _rid, odds, winner_odds in _load_diagnostic_races(session, diagnose_from, diagnose_to):
        valid = [(i, o) for i, o in enumerate(odds) if o is not None and o > 0.0]
        if len(valid) < 2:
            continue
        band = assign_band(_race_entropy_from_odds([o for _, o in valid]), edges)
        if band is None:
            continue
        cell = agg[band]
        if not winner_odds:  # cancellation / void / no finished winner (F9)
            cell["void"] += 1
            continue
        cell["n"] += 1
        fav_odds = min(o for _, o in valid)  # market favourite = lowest odds (highest q)
        # favourite_loss: the favourite's odds are NOT among the winners' odds (dead-heat aware).
        if not any(w is not None and abs(w - fav_odds) < 1e-9 for w in winner_odds):
            cell["loss"] += 1
        if any(w is not None and w >= HIGH_PAYOUT_ODDS for w in winner_odds):
            cell["high"] += 1

    out: list[DispersionBandDiagnostic] = []
    prev_ci: tuple[float, float] | None = None
    for band in BANDS:
        c = agg[band]
        n = c["n"]
        loss_rate = c["loss"] / n if n else None
        high_rate = c["high"] / n if n else None
        ci = _wilson_ci(c["loss"], n) if n else (None, None)
        separated = None
        if prev_ci is not None and ci[0] is not None:
            separated = ci[0] > prev_ci[1] or ci[1] < prev_ci[0]  # non-overlapping intervals
        out.append(DispersionBandDiagnostic(
            band=band, n=n, n_void=c["void"], favorite_loss_rate=loss_rate,
            high_payout_rate=high_rate, ci_low=ci[0], ci_high=ci[1], separated_from_prev=separated,
        ))
        if ci[0] is not None:
            prev_ci = ci
    return out
