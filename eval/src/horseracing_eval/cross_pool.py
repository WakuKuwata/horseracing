"""Cross-pool diagnostic: is the PLACE pool mispriced relative to the WIN pool?

The win market beats our model everywhere, so a model-vs-market edge is closed. Hausch–Ziemba–
Rubinstein (1981) found positive expectation without any fundamental prediction at all, by
exploiting inconsistency BETWEEN pools: what the win pool implies about a horse versus what the
place pool charges for it. That question survives `α≈0` because it compares `q` to another `q`.

What this module measures, precisely:

    a policy selected using WIN-pool information only, settled at the REAL place dividend.

If the place pool simply re-expressed the win pool, every such policy would return `1 − takeout`
(0.80 for JRA place) regardless of which horses it picks — exactly the flat 0.79 the win pool
shows between 1.5x and 11x. A rank profile that is NOT flat is cross-pool structure.

Two things this deliberately does NOT do:

* It does not compute `EV = P_place × O_place` and bet when `EV ≥ 1`. That needs the place pool's
  PRE-RACE prices, which are not stored (only dividends, i.e. payouts of the horses that placed).
  A dividend is knowable only after the race, so an EV rule built on it would be selecting with
  the outcome in hand.
* It does not claim to reproduce Dr.Z. HZR used the place pool's actual bet fractions to find
  WHICH place prices were too high; without pre-race place prices we can only ask whether
  win-pool information beats the place pool on average over a policy.

**Rank policies are λ-invariant.** Deriving place probabilities from `q` needs a Harville stage
discount, and 049 showed the raw λ=1 derivation is badly calibrated (top3 ECE 34× the win ECE).
Ranking horses within a race by place probability is monotone in `q` for any λ, so rank-based
policies do not depend on that choice at all — the primary readout cannot be an artefact of our
own derivation. Threshold policies do depend on λ and are reported as secondary, with λ recorded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .bootstrap import RatioBootstrapCI, race_day_cluster_ratio_bootstrap_ci_v1

#: JRA place takeout → the return a policy gets if the place pool holds no exploitable structure.
PLACE_PAYOUT_RATE = 0.80

#: A CI that sits within +-this of zero is NOT evidence of a direction. Sums of payouts carry
#: float noise around exact cancellation, and a degenerate (zero-width) interval at -1e-17 must
#: read as NO_DECISION rather than as a significant negative effect.
ZERO_TOL = 1e-12


@dataclass(frozen=True)
class PlaceRace:
    """One race: the win pool's view of the field, and what the place pool actually paid.

    ``numbers`` is the started field's 馬番 sorted by DESCENDING win-pool support, so index 0 is
    the favourite. ``dividends`` maps 馬番 → real place dividend (倍率) for the horses that placed;
    a horse absent from it paid nothing. Absence is therefore the settlement, and no result join
    is needed — the payout table already encodes who placed.
    """

    race_id: str
    day: str
    field_size: int
    numbers: tuple[int, ...]
    q: tuple[float, ...]            #: win-pool vote share, aligned with ``numbers``
    place_prob: tuple[float, ...]   #: derived P(place), aligned with ``numbers``
    dividends: dict[int, float]
    #: final win odds by 馬番 and the winning 馬番(s) — only needed by the paired win-vs-place
    #: comparison. Empty means "win side not supplied", and paired scoring refuses to run.
    win_odds: dict[int, float] = field(default_factory=dict)
    winner_numbers: frozenset[int] = frozenset()

    def payout(self, number: int) -> float:
        return float(self.dividends.get(number, 0.0))

    def win_payout(self, number: int) -> float:
        """Pari-mutuel win settlement: the FINAL odds are what a winning ticket pays."""
        return float(self.win_odds.get(number, 0.0)) if number in self.winner_numbers else 0.0


@dataclass(frozen=True)
class PolicyResult:
    name: str
    n_bets: int
    n_races: int
    n_hits: int
    roi: float
    ci: RatioBootstrapCI

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "n_bets": self.n_bets, "n_races": self.n_races,
            "n_hits": self.n_hits, "roi": self.roi,
            "ci_low": self.ci.ci_low, "ci_high": self.ci.ci_high,
            "n_days": self.ci.n_days, "no_decision": self.ci.no_decision,
        }


# --- pre-registered policy set ------------------------------------------------------------------
# Fixed BEFORE looking at any result. Rank policies are the primary readout (λ-invariant); the
# blind policy is the structural reference (it must land on 1 − takeout if the pool is plain).

def _select_rank(race: PlaceRace, rank: int) -> list[int]:
    """Bet the horse at 1-based ``rank`` of win-pool support (rank 1 = favourite)."""
    return [race.numbers[rank - 1]] if len(race.numbers) >= rank else []


def _rank_selector(rank: int):
    """Bind ``rank`` at definition time — a closure over the loop variable would make every
    policy in the frozen list select the same rank."""
    def sel(race: PlaceRace) -> list[int]:
        return _select_rank(race, rank)
    return sel


def _select_all(race: PlaceRace) -> list[int]:
    return list(race.numbers)


def _select_place_prob_at_least(race: PlaceRace, threshold: float) -> list[int]:
    return [n for n, pp in zip(race.numbers, race.place_prob, strict=True) if pp >= threshold]


#: (name, selector). Ranks 1..6 give the profile; `all` is the structural reference.
PRIMARY_POLICIES: tuple[tuple[str, Any], ...] = tuple(
    [("all_started", _select_all)]
    + [(f"win_rank_{k}", _rank_selector(k)) for k in range(1, 7)]
)

#: λ-dependent, reported as secondary.
SECONDARY_THRESHOLDS: tuple[float, ...] = (0.3, 0.5, 0.7, 0.9)


def score_policy(races: list[PlaceRace], name: str, selector, *, b: int, seed: int) -> PolicyResult:
    """Flat 1 unit per ticket. ROI = Σpayout / Σstake — a RATIO, so the day-cluster bootstrap
    recomputes both sums per replicate (a fixed denominator understates the spread when the
    number of tickets per day varies)."""
    pay: dict[str, list[float]] = {}
    stake: dict[str, list[float]] = {}
    n_bets = n_hits = 0
    bet_races = 0
    for r in races:
        picks = selector(r)
        if not picks:
            continue
        bet_races += 1
        for number in picks:
            p = r.payout(number)
            pay.setdefault(r.day, []).append(p)
            stake.setdefault(r.day, []).append(1.0)
            n_bets += 1
            n_hits += int(p > 0)
    if not pay:
        empty = RatioBootstrapCI(float("nan"), None, None, b, seed, "race_day", 0, True)
        return PolicyResult(name, 0, 0, 0, float("nan"), empty)
    ci = race_day_cluster_ratio_bootstrap_ci_v1(pay, stake, b=b, seed=seed)
    return PolicyResult(name, n_bets, bet_races, n_hits, ci.point, ci)


def rank_profile(races: list[PlaceRace], *, b: int, seed: int) -> list[dict[str, Any]]:
    """Place ROI by win-pool rank — the direct analogue of the win pool's odds-band profile.

    Flat at ``1 − takeout`` ⇒ the place pool is the win pool re-expressed. A hump or a tilt ⇒
    cross-pool structure that a win-pool-only policy can act on.
    """
    out = []
    for k in range(1, 7):
        res = score_policy(races, f"win_rank_{k}", _rank_selector(k), b=b, seed=seed)
        d = res.to_dict()
        d["rank"] = k
        d["hit_rate"] = (res.n_hits / res.n_bets) if res.n_bets else float("nan")
        d["mean_q"] = float(np.mean([r.q[k - 1] for r in races if len(r.q) >= k]))
        d["mean_place_prob"] = float(
            np.mean([r.place_prob[k - 1] for r in races if len(r.place_prob) >= k])
        )
        out.append(d)
    return out


def evaluate_cross_pool(
    races: list[PlaceRace], *, b: int = 2000, seed: int = 20260729,
) -> dict[str, Any]:
    if not races:
        raise ValueError("no eligible races")
    primary = [score_policy(races, name, sel, b=b, seed=seed).to_dict()
               for name, sel in PRIMARY_POLICIES]
    secondary = [
        score_policy(races, f"place_prob_ge_{t}",
                     lambda r, t=t: _select_place_prob_at_least(r, t), b=b, seed=seed).to_dict()
        for t in SECONDARY_THRESHOLDS
    ]
    return {
        "reference_return": PLACE_PAYOUT_RATE,
        "n_races": len(races),
        "n_days": len({r.day for r in races}),
        "primary_policies": primary,
        "rank_profile": rank_profile(races, b=b, seed=seed),
        "secondary_threshold_policies": secondary,
    }


# --- paired win vs place (pre-registered: docs/plan/prereg-win-vs-place-paired.md) ---------------

def paired_win_vs_place(
    races: list[PlaceRace], *, b: int = 2000, seed: int = 20260729,
) -> dict[str, Any]:
    """Same race, same horse: is the PLACE ticket cheaper than the WIN ticket?

    §5.2a showed the place pool is not flat, but not whether that structure is the place pool's
    own or just the win pool's favourite–longshot bias showing through. Pairing the two tickets
    on the same horse separates them: a pure reflection gives ΔROI ≈ 0 at every rank.

    Both sides bet 1 unit on the same selection, so the denominators are identical and
    ``ΔROI = ROI_place − ROI_win`` is exactly the mean paired payout difference. The CI is still
    taken with the ratio bootstrap so the day-cluster resampling matches every other readout here.

    Six ranks are examined and NOT corrected for multiplicity — the claim is about the shape of
    the profile, not the significance of any single rank (pre-registration §判定規則).
    """
    if not races:
        raise ValueError("no eligible races")
    missing = [r.race_id for r in races if not r.win_odds or not r.winner_numbers]
    if missing:
        raise ValueError(f"win side not supplied for {len(missing)} races (e.g. {missing[0]})")

    rows = []
    for k in range(1, 7):
        num: dict[str, list[float]] = {}
        den: dict[str, list[float]] = {}
        win_pay: dict[str, list[float]] = {}
        place_pay: dict[str, list[float]] = {}
        n_bets = n_win_hits = n_place_hits = 0
        for r in races:
            picks = _select_rank(r, k)
            if not picks:
                continue
            for number in picks:
                wp, pp = r.win_payout(number), r.payout(number)
                num.setdefault(r.day, []).append(pp - wp)
                den.setdefault(r.day, []).append(1.0)
                win_pay.setdefault(r.day, []).append(wp)
                place_pay.setdefault(r.day, []).append(pp)
                n_bets += 1
                n_win_hits += int(wp > 0)
                n_place_hits += int(pp > 0)
        if not num:
            continue
        diff = race_day_cluster_ratio_bootstrap_ci_v1(num, den, b=b, seed=seed)
        roi_win = race_day_cluster_ratio_bootstrap_ci_v1(win_pay, den, b=b, seed=seed)
        roi_place = race_day_cluster_ratio_bootstrap_ci_v1(place_pay, den, b=b, seed=seed)
        if diff.no_decision or diff.ci_low is None or diff.ci_high is None:
            verdict = "NO_DECISION"
        elif diff.ci_low > ZERO_TOL:
            verdict = "place_cheaper"
        elif diff.ci_high < -ZERO_TOL:
            verdict = "win_cheaper"
        else:
            verdict = "NO_DECISION"
        rows.append({
            "rank": k, "n_bets": n_bets,
            "win_hit_rate": n_win_hits / n_bets, "place_hit_rate": n_place_hits / n_bets,
            "roi_win": roi_win.point, "roi_place": roi_place.point,
            "delta_roi": diff.point, "ci_low": diff.ci_low, "ci_high": diff.ci_high,
            "n_days": diff.n_days, "verdict": verdict,
        })
    return {
        "preregistration": "docs/plan/prereg-win-vs-place-paired.md",
        "multiplicity": "6 ranks examined, NOT corrected — read the profile shape, not one rank",
        "n_races": len(races),
        "n_dead_heat_win_races": sum(1 for r in races if len(r.winner_numbers) > 1),
        "by_rank": rows,
    }
