"""Kelly staking config + value objects (Feature 016, research.md R1/R3/R7, data-model.md §2).

KellyConfig carries every knob that affects bet sizing (λ_real/λ_est, cap_bet, cap_total, o_min,
min_edge, bankroll, allocation method, enable_estimated) so the run is reproducible: the config is
encoded into recommendations.logic_version and absolute stake = stake_fraction × bankroll. Defaults
follow research.md R3 (quarter Kelly real, conservative estimated). Nothing here reads race results
or model features (leak boundary).
"""

from __future__ import annotations

from dataclasses import dataclass

from . import BETTING_LOGIC_VERSION


@dataclass(frozen=True)
class KellyConfig:
    lambda_real: float = 0.25      # fractional Kelly for real exotic odds (quarter Kelly)
    lambda_est: float = 0.10       # conservative fraction for estimated odds (double-pseudo)
    cap_bet: float = 0.05          # per-bet bankroll-fraction cap
    cap_total: float = 0.10        # per-(race,bet_type) total fraction cap
    o_min: float = 1.5             # minimum decimal odds (avoids 1/(O-1) blow-up)
    min_edge: float = 0.0          # real-odds edge floor (edge = P_model·O − 1)
    min_edge_est: float = 0.05     # estimated-odds edge floor (stricter)
    bankroll: float = 100.0        # current bankroll the stake is proportional to
    allocation: str = "exact"      # "exact" (expected-log-growth max) | "heuristic"
    enable_estimated: bool = True  # if False, estimated-odds bets get no Kelly stake

    def lam(self, *, is_estimated: bool) -> float:
        return self.lambda_est if is_estimated else self.lambda_real

    def min_edge_for(self, *, is_estimated: bool) -> float:
        return self.min_edge_est if is_estimated else self.min_edge


def kelly_logic_version(cfg: KellyConfig, *, odds_cap: float, threshold: float) -> str:
    """Structured, reproducible logic_version string (data-model.md §1 example)."""
    return (
        "kelly-v1;"
        f"alloc={cfg.allocation};"
        f"lam_real={cfg.lambda_real};lam_est={cfg.lambda_est};"
        f"cap_bet={cfg.cap_bet};cap_tot={cfg.cap_total};"
        f"omin={cfg.o_min};min_edge={cfg.min_edge};min_edge_est={cfg.min_edge_est};"
        f"bank={cfg.bankroll};est={int(cfg.enable_estimated)};"
        f"thr={threshold};cap={odds_cap};"
        f"prob=P_model(009;p);odds=real_exotic>est(010;q);"
        f"v={BETTING_LOGIC_VERSION}"
    )


@dataclass(frozen=True)
class KellyBet:
    """A sized Kelly bet on the canonical field. odds_used = real (012) or estimated O_est (010)."""

    bet_type: str
    selection: list[int]
    p_model: float
    odds_used: float
    is_estimated: bool       # True → estimated odds (double-pseudo); False → real exotic odds
    edge: float              # P_model·odds_used − 1
    raw_fraction: float      # f* = edge / (odds_used − 1) (pre-λ/cap)
    stake_fraction: float    # effective fraction after λ, cap, allocation
    stake: float             # stake_fraction × bankroll

    @property
    def pseudo_odds(self) -> float:
        return 1.0 / self.p_model
