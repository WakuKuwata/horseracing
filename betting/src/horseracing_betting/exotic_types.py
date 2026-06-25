"""Exotic EV value objects (data-model.md §1-6).

Shared, non-persistent dataclasses for exotic recommendation/backtest. ``selection`` is a plain
JSON array of horse_numbers (NO frozenset/tuple): ordered for exacta/trifecta, ascending-sorted
for quinella/wide/trio, single-element for place. Order-ness is derived from ``bet_type`` — not
stored. EV = p_model (009 on model p) × o_est (010 on market q); p and q are never mixed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from horseracing_db.enums import BetType

#: exotic bet types (single-win is Feature 007). place is field-size dependent (009 rule).
ALL_EXOTIC: tuple[str, ...] = (
    BetType.PLACE,
    BetType.QUINELLA,
    BetType.EXACTA,
    BetType.WIDE,
    BetType.TRIO,
    BetType.TRIFECTA,
)

#: bet types whose selection preserves finishing order (others are set-style / inclusion).
ORDERED_BET_TYPES: frozenset[str] = frozenset({BetType.EXACTA, BetType.TRIFECTA})

#: deterministic default seed for the uniform baseline (no Date/Random in derivation).
DEFAULT_SEED = 11011


@dataclass(frozen=True)
class ExcludedHorse:
    horse_number: int | None
    horse_id: str
    reason: str  # no_prob / no_odds / scratched / cancelled / excluded / no_number


@dataclass(frozen=True)
class CanonicalField:
    """Horses with BOTH a valid model prob AND valid market odds (the shared EV population)."""

    race_id: str
    horse_numbers: list[int]
    p_norm: dict[int, float]       # renormalized model win prob (009 input); Σ=1 (empty if ≤1)
    odds_norm: dict[int, float]    # market win odds restricted to the population (010 input)
    field_size: int                # len(horse_numbers); drives 009/010 field rule
    excluded: list[ExcludedHorse] = field(default_factory=list)
    number_to_id: dict[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ExoticBet:
    """One exotic bet candidate. p_model from 009(p), o_est from 010(q), ev = p_model·o_est."""

    bet_type: str
    selection: list[int]   # JSONB-safe array (ordered-ness implied by bet_type)
    p_model: float
    o_est: float
    ev: float

    @property
    def pseudo_odds(self) -> float:
        return 1.0 / self.p_model

    @property
    def pseudo_roi(self) -> float:
        return self.ev - 1.0


@dataclass(frozen=True)
class ScoredBet:
    bet: ExoticBet
    stake: float
    hit: bool
    payout: float       # stake * odds_used if hit else 0
    pseudo: bool = True  # True = estimated O_est payout (double-pseudo); False = real dividend

    @property
    def profit(self) -> float:
        return self.payout - self.stake


@dataclass(frozen=True)
class ExoticRaceOutcome:
    race_id: str
    finish_pos: dict[int, int]  # horse_number -> finishing rank (1-based), finished horses only
    field_size: int             # canonical field_size used at generation (NOT actual starters)


@dataclass(frozen=True)
class DivergenceReport:
    """Estimated (010/011) vs REAL exotic odds divergence per bet type (Feature 012, eval-first)."""

    bet_type: str
    n_estimated: int          # estimated candidates priced this bet type
    n_pairs: int              # candidates that ALSO have a real odds (matched)
    coverage_rate: float      # n_pairs / n_estimated
    log_ratio_median: float   # median of log(real / estimated) over matched pairs
    log_ratio_mae: float      # mean |log(real / estimated)|
    log_ratio_p90: float      # 90th percentile of |log(real / estimated)|
    baseline: str = "estimated(010/011)"
    pseudo_baseline: bool = True  # the estimated side is double-pseudo


@dataclass(frozen=True)
class ExoticRoiReport:
    strategy: str
    bet_type: str  # a single bet type, or "__total__" for the aggregate row
    n_bets: int
    n_hits: int
    hit_rate: float
    total_stake: float
    total_payout: float
    roi: float           # total_payout / total_stake (pseudo)
    skip_rate: float     # skipped (race,bet_type) opportunities / total opportunities
    max_drawdown: float
    max_consecutive_losses: int
    pseudo: bool = True  # DOUBLE-pseudo (estimated odds + PL extrapolation); always True here
