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
    entropy_delta_direction,
    favorite_win_prob,
    normalized_entropy,
    top3_cumulative,
)
from horseracing_probability.model_calibration import (
    TWO_GAMMA_PIVOT,
    PCalibrator,
    apply_p_calibrator,
)

from .schemas import RaceDispersion, RaceDispersionDelta, RaceDivergence, UnderratedLongshot
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


def load_p_calibrator(path: str | os.PathLike[str] | None = None) -> PCalibrator | None:
    """Load the FROZEN two_gamma p-calibrator artifact (Feature 066 model_delta), else None.

    Path resolution: explicit arg → ``DISPERSION_PCAL_PATH`` env → none. Malformed/missing fails
    OPEN to None (model_delta is then omitted) — a missing calibrator is a benign display gap, not
    a correctness hazard (mirrors ``load_boundary``). The calibrator is a few floats (gamma_lo/hi/
    pivot + version); apply is read-only/pure and never touches odds/q (p⊥q).

    Provenance caveat (constitution II, 074 research D7): the artifact this loads is fit on
    full-history NON-OOS predictions (see ``training dispersion-pcal``), so its gamma is mildly
    optimistic. That is tolerable here because model_delta is a display-only read-out; the
    OOF-faithful fix (immutable calibration manifest) is deferred to pipeline-activation."""
    p = path or os.environ.get("DISPERSION_PCAL_PATH")
    if not p:
        return None
    try:
        data = json.loads(Path(p).read_text())
        method = str(data.get("method") or "two_gamma")
        params = {
            "gamma_lo": float(data["gamma_lo"]),
            "gamma_hi": float(data["gamma_hi"]),
            "pivot": float(data.get("pivot", TWO_GAMMA_PIVOT)),
        }
        version = str(data.get("version")) if data.get("version") is not None else ""
        return PCalibrator(
            method=method, params=params, train_window=None, n_races=int(data.get("n_races", 0)),
            n_samples=int(data.get("n_races", 0)), prob_range=(0.0, 1.0), select="mle",
            base_model_version=None, logic_version=version,
            sufficient=(method == "two_gamma"),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def load_activation_calibrator(
    *, active_model_version: str, target_date, manifest_path: str | os.PathLike[str] | None = None,
) -> PCalibrator | None:
    """Feature 076 (US3): the dispersion two-gamma from the IMMUTABLE 074 manifest, else None.

    Supersedes the derived-JSON ``load_p_calibrator``: the manifest is generation-bound (to the
    SELECTED run's ``active_model_version`` — 057 model-switching aware, FR-020) and temporally
    gated (``target_date > fit_through``, FR-021), so ``model_delta`` is shown ONLY for races the
    manifest may legitimately calibrate — a real improvement over the old path that applied a
    non-OOS calibrator to every race.

    Path resolution: explicit arg → ``DISPERSION_CALIB_MANIFEST`` env → none. Like ``load_boundary``
    this fails **OPEN** to None: a missing / invalid / out-of-scope / in-window manifest just omits
    ``model_delta`` — a display gap, never a broken read (contrast betting/serving, which fail
    CLOSED). ``attestation_verifier=None`` (D11: api has no ``training`` dependency; name + content-
    addressed digest binding, strong binding rides with 077)."""
    from horseracing_probability.calib_activation import (
        ActivationError,
        Profile,
        load_calibration,
    )
    from horseracing_probability.calib_manifest import ManifestError

    p = manifest_path or os.environ.get("DISPERSION_CALIB_MANIFEST")
    if not p or target_date is None:
        return None  # no manifest, or no race date to gate on → omit model_delta (fail-open)
    try:
        act = load_calibration(
            p, active_model_version=active_model_version, target_date=target_date,
            profile=Profile.PRODUCTION, attestation_verifier=None,
        )
    except (ActivationError, ManifestError, OSError):
        return None  # fail-open: the display instrument must never break the read API
    return act.two_gamma


def _build_model_delta(
    pmap: dict[int, float], q_entropy: float | None, calibrator: PCalibrator | None
) -> RaceDispersionDelta | None:
    """H(calibrated model p) − H(q) + neutral direction, over the canonical field. None when no
    calibrator is loaded (fail-open), or when either entropy is degenerate. The calibrated p is
    display-only — computed at read time, returned in the response, NEVER persisted and NEVER a
    model feature (constitution II)."""
    if calibrator is None:
        return None
    try:
        calibrated = apply_p_calibrator({str(n): pmap[n] for n in pmap}, calibrator)
        p_entropy = normalized_entropy(list(calibrated.values()))
    except (ValueError, ZeroDivisionError):
        return None  # display must never break serving — omit the delta on any numeric failure
    delta, direction = entropy_delta_direction(p_entropy, q_entropy)
    if delta is None:
        return None
    return RaceDispersionDelta(
        normalized_entropy_delta=delta,
        direction=direction,  # type: ignore[arg-type]  # entropy_delta_direction returns the literal
        calibrator_version=calibrator.logic_version or None,
    )


def build_race_dispersion(
    *,
    qmap: dict[int, float],
    pmap: dict[int, float],
    odds_as_of,
    odds_source: str | None,
    boundary: LoadedBoundary = _EMPTY_BOUNDARY,
    p_calibrator: PCalibrator | None = None,
) -> RaceDispersion:
    """Axis A read-out from market q over the canonical field.

    ``qmap`` is the 021 market vote-share ({horse_number -> q}) on the started population; ``pmap``
    is the model-p canonical population ({horse_number -> p}). Availability requires q to cover the
    p field exactly (else partial/no market odds → unavailable, and we do NOT fall back to model p).
    The BAND and raw numbers are functions of q ONLY — ``pmap`` feeds ``model_delta`` (the
    calibrated p vs q concentration difference) and nothing else, so the band is byte-identical
    regardless of p.
    """
    q_values = list(qmap.values())
    if not q_values:
        return RaceDispersion(available=False, unavailable_reason="no_market_odds")
    # q must cover the full model-p canonical field, else the concentration is over a partial field.
    if not pmap or set(qmap) != set(pmap):
        return RaceDispersion(available=False, unavailable_reason="partial_market_odds")

    entropy = normalized_entropy(q_values)
    return RaceDispersion(
        available=True,
        unavailable_reason=None,
        band=assign_band(entropy, boundary.edges),
        normalized_entropy=entropy,
        favorite_win_prob=favorite_win_prob(q_values),
        top3_cumulative=top3_cumulative(q_values),
        model_delta=_build_model_delta(pmap, entropy, p_calibrator),
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
