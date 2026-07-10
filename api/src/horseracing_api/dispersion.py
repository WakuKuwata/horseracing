"""Feature 066 read-time dispersion / divergence helpers (axis A implemented; axis B TODO US2).

Pure, read-only. Reuses the SAME canonical field as 021 (started horses with valid win odds), and
the SAME dispersion numerics as the offline path (``horseracing_eval.dispersion_bands``) — no
duplicated formulas. Nothing here re-enters model features (constitution II): these are functions of
the already-served p and the market q only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from horseracing_eval.dispersion_bands import (
    assign_band,
    favorite_win_prob,
    normalized_entropy,
    top3_cumulative,
)

from .schemas import RaceDispersion, RaceDivergence, UnderratedLongshot
from .selection import divergence_band


class LoadedBoundary:
    """Frozen quintile edges + version, loaded once from a JSON artifact (never recomputed at read
    time — 054/021 discipline). None edges/version when no artifact is available (F8)."""

    __slots__ = ("edges", "version")

    def __init__(self, edges: list[float] | None, version: str | None) -> None:
        self.edges = edges
        self.version = version


_EMPTY_BOUNDARY = LoadedBoundary(None, None)


def load_boundary(path: str | os.PathLike[str] | None = None) -> LoadedBoundary:
    """Load the dispersion-band boundary artifact if present, else an empty boundary (band omitted).

    Path resolution: explicit arg → ``DISPERSION_BOUNDARY_PATH`` env → none. Malformed/missing
    artifact fails OPEN to "no band" (the display instrument must not break serving) — not
    fail-closed, because a missing band is a benign display gap, not a correctness hazard.
    """
    p = path or os.environ.get("DISPERSION_BOUNDARY_PATH")
    if not p:
        return _EMPTY_BOUNDARY
    try:
        data = json.loads(Path(p).read_text())
        edges = [float(x) for x in data["quintile_edges"]]
        version = str(data.get("version")) if data.get("version") is not None else None
        if len(edges) != 4:
            return _EMPTY_BOUNDARY
        return LoadedBoundary(edges, version)
    except (OSError, ValueError, KeyError, TypeError):
        return _EMPTY_BOUNDARY


def build_race_dispersion(
    *,
    qmap: dict[int, float],
    p_numbers: set[int],
    odds_as_of,
    odds_source: str | None,
    boundary: LoadedBoundary = _EMPTY_BOUNDARY,
) -> RaceDispersion:
    """Axis A read-out from market q over the canonical field.

    ``qmap`` is the 021 market vote-share ({horse_number -> q}) on the same started population.
    ``p_numbers`` is the model-p canonical population: availability requires q to cover it exactly
    (else partial/no market odds → unavailable, and we do NOT fall back to model p). model_delta is
    DEFERRED (see RaceDispersionDelta) — null here.
    """
    q_values = list(qmap.values())
    if not q_values:
        return RaceDispersion(available=False, unavailable_reason="no_market_odds")
    # q must cover the full model-p canonical field, else the concentration is over a partial field.
    if not p_numbers or set(qmap) != p_numbers:
        return RaceDispersion(available=False, unavailable_reason="partial_market_odds")

    entropy = normalized_entropy(q_values)
    return RaceDispersion(
        available=True,
        unavailable_reason=None,
        band=assign_band(entropy, boundary.edges),
        normalized_entropy=entropy,
        favorite_win_prob=favorite_win_prob(q_values),
        top3_cumulative=top3_cumulative(q_values),
        model_delta=None,  # DEFERRED until a two_gamma calibrator is exposed to the read path
        odds_as_of=odds_as_of,
        odds_source=odds_source,  # type: ignore[arg-type]  # 021 already constrains to final/prerace
        is_pseudo=True,
        boundary_version=boundary.version,
    )


# --- axis B: p vs q divergence (race-level neutral summary) --------------------

# F2: divergence_band vocabulary (market/model) -> favourite-direction vocabulary (model-centric).
_FAVDIR = {"market_higher": "model_lower", "model_higher": "model_higher", "similar": "similar"}


def _popularity_ranks(qmap: dict[int, float]) -> dict[int, int]:
    """1-based market popularity rank (1 = favourite = highest q). Ties broken by horse_number."""
    order = sorted(qmap, key=lambda n: (-qmap[n], n))
    return {n: i + 1 for i, n in enumerate(order)}


def build_race_divergence(
    *,
    pmap: dict[int, float],
    qmap: dict[int, float],
    canonical_consistent: bool,
    model_version: str,
) -> RaceDivergence:
    """Axis B: where do model p and market q disagree — a NEUTRAL fact for the human's favourite-vs-
    longshot call, NOT a verdict that the model is right (047: q predicts better). Reuses the
    existing per-horse ``divergence_band`` (040, unchanged). Suppressed (available=false, all null)
    when p and q populations differ (canonical_consistent=false) or q is missing.
    """
    if not canonical_consistent or not pmap or not qmap:
        return RaceDivergence(available=False, model_version=model_version)

    ranks = _popularity_ranks(qmap)
    fav = min(ranks, key=lambda n: ranks[n])  # popularity rank 1 (favourite)
    band = divergence_band(pmap.get(fav), qmap.get(fav))
    favorite_direction = _FAVDIR.get(band) if band else None

    # model's top-3 by p that the MARKET does NOT rank top-3 (popularity_rank > 3) — factual only.
    model_top3 = sorted(pmap, key=lambda n: (-pmap[n], n))[:3]
    longshots = [
        UnderratedLongshot(
            horse_number=n, popularity_rank=ranks[n], p=pmap[n], q=qmap.get(n)
        )
        for n in model_top3
        if ranks.get(n, 10**9) > 3
    ]

    # F6: top-3 set agreement = |model_top3 ∩ market_top3| / 3.
    market_top3 = sorted(qmap, key=lambda n: (-qmap[n], n))[:3]
    rank_agreement = len(set(model_top3) & set(market_top3)) / 3.0

    fav_text = {
        "model_lower": "本命(1番人気)をモデルは市場より低く評価",
        "model_higher": "本命(1番人気)をモデルは市場より高く評価",
        "similar": "本命(1番人気)の評価はモデルと市場でほぼ一致",
    }.get(favorite_direction or "", "本命の評価差は判定できません")
    summary = fav_text + ("・モデル上位に人気薄あり" if longshots else "")

    return RaceDivergence(
        available=True,
        summary=summary,
        favorite_direction=favorite_direction,  # type: ignore[arg-type]
        underrated_longshots=longshots,
        rank_agreement=rank_agreement,
        model_version=model_version,
    )
