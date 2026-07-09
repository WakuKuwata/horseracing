"""EV selection core (contracts/recommend.md, INV-B1..B4).

Population = started horses (cancelled/excluded removed). win_prob is renormalized over the
**started** population so a horse scratched after prediction no longer distorts Σ (constitution
IV). Odds-missing started horses STAY in the probability denominator (they can still win — codex
fix) but receive no bet. A bet is placed on every started horse with valid odds, a positive
renormalized prob, and EV = win_prob × odds >= threshold. Race results are never read here.

All math is float; callers convert at the DB boundary with Decimal(str(x)).
"""

from __future__ import annotations

from dataclasses import dataclass

from horseracing_db.enums import EntryStatus

#: small tolerance so an EV that equals the threshold (modulo float renormalization error) is
#: inclusively selected — "EV >= threshold" intent without boundary flakiness.
_EPS = 1e-9


@dataclass(frozen=True)
class Bet:
    horse_id: str
    horse_number: int | None
    win_prob: float | None   # renormalized (None for odds-only baselines that ignore prob)
    odds: float
    ev: float | None         # win_prob * odds (None when prob not used)
    stake: float


def _is_started(h: dict) -> bool:
    return h.get("entry_status") == EntryStatus.STARTED


def _valid_odds(h: dict) -> bool:
    o = h.get("odds")
    return o is not None and float(o) > 0.0


def renormalized_started_probs(horses: list[dict]) -> dict[str, float]:
    """win_prob renormalized to Σ=1 over started horses with a positive prob (INV-B1).

    A no-op when the input already sums to 1 with no scratches. Returns {} if the denominator
    is non-positive (degenerate)."""
    started = [
        h for h in horses
        if _is_started(h) and h.get("win_prob") is not None and float(h["win_prob"]) > 0.0
    ]
    total = sum(float(h["win_prob"]) for h in started)
    if total <= 0.0:
        return {}
    return {h["horse_id"]: float(h["win_prob"]) / total for h in started}


def eligible_started(horses: list[dict]) -> list[dict]:
    """Started horses with valid (>0) odds — the bettable population for any strategy."""
    return [h for h in horses if _is_started(h) and _valid_odds(h)]


def select_ev_bets(
    horses: list[dict], *, threshold: float, stake: float, odds_cap: float | None = None
) -> list[Bet]:
    """All started, odds-valid horses whose renormalized EV >= threshold (INV-B2..B4).

    Feature 064: ``odds_cap`` (win upper cap) excludes horses whose odds >= cap from BETTING —
    they stay in the probability denominator (renormalized_started_probs is untouched: a capped
    horse can still win, mirroring the odds-missing handling) so win_prob is byte-identical.
    ``odds_cap is None`` reproduces the pre-064 behaviour exactly (byte parity). The cap lives HERE
    (inside the EV loop) and NOT in eligible_started(), so the odds-only ROI baselines
    (FavoriteROIBaseline / UniformROIBaseline) are unaffected.
    """
    probs = renormalized_started_probs(horses)
    bets: list[Bet] = []
    for h in eligible_started(horses):
        p = probs.get(h["horse_id"], 0.0)
        if p <= 0.0:
            continue
        odds = float(h["odds"])
        if odds_cap is not None and odds >= odds_cap:  # Feature 064: over-cap → no bet (in denom)
            continue
        ev = p * odds
        if ev >= threshold - _EPS:
            bets.append(
                Bet(horse_id=h["horse_id"], horse_number=h.get("horse_number"),
                    win_prob=p, odds=odds, ev=ev, stake=stake)
            )
    return bets
