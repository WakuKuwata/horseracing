"""Feature 138: the 注目条件 (attention conditions) S1-S5 — one registry for training, api and the
freeze script.

Three parts, in the order they were built (plan 0.3):

* **Definitions** — what each condition selects. ``match_mask`` is the only implementation; the
  freeze script selects its backtest rows with it and ``definitions_sha256`` ties the frozen
  statistics to these exact definitions (a changed boundary changes the hash).
* **Prospective rules** — how a judged horse (a "pick") is classified, how the 300/600-point
  checkpoints are decided and how a stage follows from the recorded decisions. Selection policy
  v2 (feature 139) settles at the official win payout; v1 (judged odds) is kept as a reference.
* **Frozen statistics and levels** — the backtest / price-noise numbers shown on screen, the axis
  levels derived from them mechanically and the buy-time expectation (139 D13).

Nothing here reads a database or a model: training and api build ``PickFacts`` from their own
queries and call these pure functions, so both sides classify and decide identically.
Win probabilities are never exposed (constitution IV): calibration is reported in expected-return
units only.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Literal
from zoneinfo import ZoneInfo

import numpy as np

from horseracing_eval.bootstrap import (
    centered_one_sided_p_from_replicates,
    race_block_ratio_bootstrap_ci_v1,
)

# =============================================================================================
# Definitions (plan 0.3 「定義」・T004a)
# =============================================================================================

RULE_SET_VERSION = "attention-S1-S5-v1"
#: the version the 期待回収率 column shows (the 15-seed average) — the single definition api reads
DISPLAYED_MARKET_EV_MODEL_VERSION = "mev-ens15-v1"
#: the single-seed series kept for S5 and for the panel (137's model, unchanged)
SINGLE_SEED_MODEL_VERSION = "mev-binary-v2"

RuleId = Literal["S1", "S2", "S3", "S4", "S5"]

DEFINITION_SEMANTICS = (
    "EV > t (strict); band: 20 <= odds < 40 and 14 <= days_since_last <= 112 "
    "(NaN gap -> not matched)"
)


@dataclass(frozen=True)
class RuleDefinition:
    id: str
    rank: int
    definition_ja: str
    uses_ensemble: bool
    ev_gt: float
    #: ``lo <= odds < hi`` or None (no odds restriction)
    odds_band: tuple[float, float] | None
    #: ``lo <= days_since_last <= hi`` or None
    gap_days: tuple[int, int] | None
    #: found after looking at results (S1/S2) — shown with the 「探索後固定」 badge
    posthoc: bool
    #: the comparison condition (S5): never gets a chip
    control: bool


_BAND = (20.0, 40.0)
_GAP = (14, 112)

RULE_DEFINITIONS: tuple[RuleDefinition, ...] = (
    RuleDefinition(
        "S1", 1,
        "15 seed 平均の期待回収率が 120% 超、単勝 20 倍以上 40 倍未満、前走から 14〜112 日",
        True, 1.2, _BAND, _GAP, True, False,
    ),
    RuleDefinition(
        "S2", 2,
        "15 seed 平均の期待回収率が 130% 超、単勝 20 倍以上 40 倍未満、前走から 14〜112 日",
        True, 1.3, _BAND, _GAP, True, False,
    ),
    RuleDefinition(
        "S3", 3, "15 seed 平均の期待回収率が 120% 超(単勝オッズの制限なし)",
        True, 1.2, None, None, False, False,
    ),
    RuleDefinition(
        "S4", 4,
        "15 seed 平均の期待回収率が 110% 超、単勝 20 倍以上 40 倍未満、前走から 14〜112 日",
        True, 1.1, _BAND, _GAP, False, False,
    ),
    RuleDefinition(
        "S5", 5, "単 seed(137 のモデル)の期待回収率が 120% 超(対照)",
        False, 1.2, None, None, False, True,
    ),
)
RULE_IDS: tuple[str, ...] = tuple(d.id for d in RULE_DEFINITIONS)
_BY_ID: dict[str, RuleDefinition] = {d.id: d for d in RULE_DEFINITIONS}


def definition(rule_id: str) -> RuleDefinition:
    try:
        return _BY_ID[rule_id]
    except KeyError:
        raise ValueError(f"unknown attention rule {rule_id!r}") from None


def definitions_sha256() -> str:
    """sha256 of the canonical JSON of the definitions + semantics (frozen JSON must match)."""
    payload = {
        "rule_set_version": RULE_SET_VERSION,
        "semantics": DEFINITION_SEMANTICS,
        "rules": [asdict(d) for d in RULE_DEFINITIONS],
    }
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _as_float_array(x) -> np.ndarray:
    if x is None:
        return np.array(np.nan)
    return np.asarray(x, dtype=float)


def match_mask(defn: RuleDefinition, *, ens_ev, single_ev, odds, days_since_last) -> np.ndarray:
    """Vectorised selection — THE implementation (freeze, training and api all go through it).

    ``None`` / NaN in any input the rule uses means "not matched" (never an error)."""
    ev = _as_float_array(ens_ev if defn.uses_ensemble else single_ev)
    with np.errstate(invalid="ignore"):
        mask = ev > defn.ev_gt
        if defn.odds_band is not None:
            o = _as_float_array(odds)
            lo, hi = defn.odds_band
            mask = mask & (o >= lo) & (o < hi)
        if defn.gap_days is not None:
            g = _as_float_array(days_since_last)
            glo, ghi = defn.gap_days
            mask = mask & (g >= glo) & (g <= ghi)
    return np.asarray(mask, dtype=bool)


def matches(defn: RuleDefinition, *, ens_ev, single_ev, odds, days_since_last) -> bool:
    """One-element ``match_mask`` (not a second implementation)."""
    return bool(
        match_mask(
            defn,
            ens_ev=[np.nan if ens_ev is None else ens_ev],
            single_ev=[np.nan if single_ev is None else single_ev],
            odds=[np.nan if odds is None else odds],
            days_since_last=[np.nan if days_since_last is None else days_since_last],
        )[0]
    )


def applicable_rules(*, ens_ev, single_ev, odds, days_since_last) -> tuple[str, ...]:
    """Rule ids (rank order) the horse matches. Inclusion (S2⊂S1⊂S4, S1⊂S3) follows from the
    definitions themselves."""
    return tuple(
        d.id
        for d in RULE_DEFINITIONS
        if matches(
            d, ens_ev=ens_ev, single_ev=single_ev, odds=odds, days_since_last=days_since_last
        )
    )


def field_digest(horse_ids: Iterable[str]) -> str:
    """sha256 of the started field: sorted, de-duplicated ids joined by newlines (UTF-8).

    Training stores it on every judged row and api recomputes it from the current field, so the
    two sides MUST share this function (a separator/encoding drift would flag every horse)."""
    ids = sorted({str(h) for h in horse_ids})
    return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()


# =============================================================================================
# Prospective rules (plan 0.3 「前向き検証の規約」・T007)
# =============================================================================================

#: Selection policy = selection + classification + settlement, versioned together (139 D12).
#: v1 (2026-10-02..04): settled at the judged odds (``frozen_payout``). v2 (from 2026-10-05):
#: settled at the official win payout (``official_payout``) and two payout classes added.
SELECTION_POLICY_VERSION = "v2"
#: v1's go-live date (138 T045, the day the market-ev job started writing picks). Kept for the
#: record only: v1 counted picks computed 2026-10-02..04 (JST) and settled them at the judged odds.
V1_START_DATE = datetime.date(2026, 10, 2)
#: go-live date of the CURRENT policy (v2): picks computed on/after this JST date count. v2 counts
#: from its own start (139 D12) — the v1-period picks are before_start and never mixed in. Moving
#: it needs a new selection policy version. ``None`` means "before go-live" (all before_start).
PROSPECTIVE_START_DATE: datetime.date | None = datetime.date(2026, 10, 5)
LOCAL_TZ = ZoneInfo("Asia/Tokyo")
OBSERVING_MIN = 100
CHECKPOINTS = (300, 600)
CHECKPOINT_ORDER = ("post_time", "race_id", "horse_number", "pick_id")
#: a checkpoint decision only uses picks whose post time is at least this old (late results)
CHECKPOINT_SETTLEMENT_LAG = datetime.timedelta(days=3)
#: race-day block bootstrap used for the prospective CIs and checkpoint decisions
BOOTSTRAP = {
    "impl": "horseracing_eval.bootstrap.race_block_ratio_bootstrap_ci_v1",
    "b": 20000,
    "seed": 20260905,
    "block": "race_day",
    "block_universe": "race-days with >=1 counted pick of the rule",
    "rng": "numpy.default_rng(PCG64)",
}

Stage = Literal["researching", "observing", "passed", "failed", "undecided"]
Decision = Literal["passed", "failed", "continue", "undecided"]
PickClass = Literal[
    "voided_scratched",
    "before_start",
    "post_time_unknown",
    "computed_after_post",
    "result_known_at_compute",
    "observed_after_post",
    "pending_result",
    "payout_race_missing",
    "payout_inconsistent",
    "unsettled_horse",
    "dead_heat",
    "counted",
]
#: exclusion reasons in precedence order (the first that applies wins); "counted" otherwise
EXCLUSION_ORDER: tuple[str, ...] = (
    "voided_scratched",
    "before_start",
    "post_time_unknown",
    "computed_after_post",
    "result_known_at_compute",
    "observed_after_post",
    "pending_result",
    "payout_race_missing",
    "payout_inconsistent",
    "unsettled_horse",
    "dead_heat",
)
_FINAL: frozenset[str] = frozenset({"passed", "failed", "undecided"})
_UNSET = object()


def _aware(ts: datetime.datetime, name: str) -> datetime.datetime:
    if ts.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return ts


def local_date(ts: datetime.datetime) -> datetime.date:
    return _aware(ts, "timestamp").astimezone(LOCAL_TZ).date()


def day_key(post_time: datetime.datetime) -> str:
    """Race-day cluster key (JST date of the post time) — same unit as the frozen race_date."""
    return local_date(post_time).isoformat()


@dataclass(frozen=True)
class PickFacts:
    """One judged (horse, rule) row plus what is known about its race now.

    ``dead_heat`` is race level: the race's number of winners != 1 (zero winners included) — the
    same definition the frozen backtest used. ``stored_odds`` feeds only api's reference
    settlement; classification and checkpoint decisions never read it.

    ``official_payout_yen`` = this horse's official win payout per 100 yen (None when the race's
    payout row has no entry for it), ``race_payout_known`` = the race has at least one official
    win payout row and ``race_payout_consistent`` = the race's payout rows name exactly its
    winners: the set of 馬番 of its horses that FINISHED 1st equals the set of 馬番 with a payout
    row (policy v2, 139 D11 — race level, so a disagreement leaves the WHOLE race out, never only
    the picks it touches). All three default to "unknown" / "inconsistent" so a caller that does
    not load payouts can never count a pick (fail-closed).
    """

    pick_id: str
    race_id: str
    horse_number: int
    voided: bool
    computed_at: datetime.datetime
    post_time: datetime.datetime | None
    odds_observed_at: datetime.datetime | None
    result_pending_at_compute: bool
    has_race_result: bool
    horse_has_result: bool
    won: bool
    dead_heat: bool
    odds_used: float
    stored_odds: float | None = None
    official_payout_yen: float | None = None
    race_payout_known: bool = False
    race_payout_consistent: bool = False


def race_payout_consistent(
    winner_numbers: Iterable[int | None], paid_numbers: Iterable[int]
) -> bool:
    """``PickFacts.race_payout_consistent`` of one race: the 馬番 of its 1st-place finishers
    (``None`` for a winner whose 馬番 is unknown) equal, as a set, the 馬番 of its official win
    payout rows. A winner without a known 馬番 never matches (fail-closed). api computes the same
    set equality in SQL (``queries.attention_tally_rows``)."""
    winners = set(winner_numbers)
    return None not in winners and winners == set(paid_numbers)


def classify_pick(f: PickFacts, *, start_date=_UNSET) -> str:
    """The single exclusive classification (policy v2). See ``EXCLUSION_ORDER``.

    v2 = v1 plus two payout classes right after ``pending_result`` (139 D11), both RACE level:
    ``payout_race_missing`` — the race has results but no official win payout at all;
    ``payout_inconsistent`` — the race's payout rows disagree with its result (the 馬番 of its
    1st-place finishers are not exactly the 馬番 with a payout row): a data defect, counted
    separately so it stays visible. Excluding a whole race does not depend on which horse won, so
    it cannot bias the ROI; excluding only the picks a disagreement touches would drop the race's
    winner and keep its losers at 0 (biased down). The pick-level check (a winner without its own
    payout, a non-winner with one) stays as a backstop — e.g. a pick whose frozen 馬番 differs
    from the race's current one."""
    start = PROSPECTIVE_START_DATE if start_date is _UNSET else start_date
    if f.voided:
        return "voided_scratched"
    if start is None or local_date(f.computed_at) < start:
        return "before_start"
    if f.post_time is None:
        return "post_time_unknown"
    if _aware(f.computed_at, "computed_at") >= _aware(f.post_time, "post_time"):
        return "computed_after_post"
    if not f.result_pending_at_compute:
        return "result_known_at_compute"
    if f.odds_observed_at is None or _aware(f.odds_observed_at, "odds_observed_at") >= f.post_time:
        return "observed_after_post"
    if not f.has_race_result:
        return "pending_result"
    if not f.race_payout_known:
        return "payout_race_missing"
    if (
        not f.race_payout_consistent
        or (f.won and f.official_payout_yen is None)
        or (not f.won and f.official_payout_yen is not None)
    ):
        return "payout_inconsistent"
    if not f.horse_has_result:
        return "unsettled_horse"
    if f.dead_heat:
        return "dead_heat"
    return "counted"


def frozen_payout(f: PickFacts) -> float:
    """Settlement at the judged odds (100 yen stake) — policy v1's basis (138 D13), kept as the
    v1 reference. Reads only ``odds_used`` (frozen on the pick), never the stored/official odds."""
    return 100.0 * float(f.odds_used) if f.won else 0.0


def official_payout(f: PickFacts) -> float:
    """Settlement at the official win payout (100 yen stake) — policy v2's basis for checkpoint
    decisions and stages (139 D3). A win pays what the parimutuel pool paid, not the judged odds.

    Only counted picks are settled; a winner without a payout is ``payout_inconsistent`` and never
    reaches here, so it raises instead of inventing a number."""
    if not f.won:
        return 0.0
    if f.official_payout_yen is None:
        raise ValueError(f"pick {f.pick_id}: a winner without an official payout (classify first)")
    return float(f.official_payout_yen)


#: valuation basis recorded with every checkpoint decision (same names as api's valuation_basis)
SETTLEMENT_BASES = {
    "official_win_payout": official_payout,
    "frozen_pick_odds": frozen_payout,
}


def settlement_basis(payout_of) -> str:
    """Name of a known settlement function; an unknown one raises (a record never mislabels)."""
    for name, fn in SETTLEMENT_BASES.items():
        if payout_of is fn:
            return name
    raise ValueError(f"unknown settlement function {payout_of!r}")


def order_key(p: PickFacts) -> tuple:
    """Sort key for the checkpoint order — derived from CHECKPOINT_ORDER (the single definition)."""
    return tuple(getattr(p, name) for name in CHECKPOINT_ORDER)


def _ordered(picks: Iterable[PickFacts]) -> list[PickFacts]:
    out = list(picks)
    if any(p.post_time is None for p in out):
        raise ValueError("ordered picks need a post_time (classify first)")
    return sorted(out, key=order_key)


def ratio_ci(
    picks: Iterable[PickFacts], payout_of, *, b: int | None = None, seed: int | None = None
) -> dict:
    """Pooled ROI, race-day block CI and centred one-sided p for already-counted picks.

    The block universe is the race days that hold at least one of these picks (A7)."""
    rows = list(picks)
    if not rows:
        return {"roi": None, "ci_low": None, "ci_high": None, "p_one_sided": None,
                "n": 0, "hits": 0, "n_days": 0}
    by_day: dict[str, list[float]] = {}
    for p in rows:
        acc = by_day.setdefault(day_key(p.post_time), [0.0, 0.0])
        acc[0] += payout_of(p)
        acc[1] += 100.0
    days = sorted(by_day)
    num = np.array([by_day[d][0] for d in days])
    den = np.array([by_day[d][1] for d in days])
    res = race_block_ratio_bootstrap_ci_v1(
        num, den, days, block=BOOTSTRAP["block"],
        b=int(BOOTSTRAP["b"] if b is None else b),
        seed=int(BOOTSTRAP["seed"] if seed is None else seed),
    )
    point = float(res.point[0])
    undecidable = res.no_decision or not math.isfinite(float(res.ci_low[0]))
    return {
        "roi": point,
        "ci_low": None if undecidable else float(res.ci_low[0]),
        "ci_high": None if undecidable else float(res.ci_high[0]),
        "p_one_sided": None if undecidable
        else centered_one_sided_p_from_replicates(res.replicates[0], point),
        "n": len(rows),
        "hits": sum(1 for p in rows if p.won),
        "n_days": len(days),
        "day_keys_sha256": hashlib.sha256("\n".join(days).encode("utf-8")).hexdigest(),
    }


def decide_checkpoint(
    counted: Iterable[PickFacts],
    checkpoint: int,
    *,
    b: int | None = None,
    seed: int | None = None,
    payout_of=official_payout,
) -> tuple[str, dict]:
    """Decide one checkpoint from counted picks (any order; sorted here by CHECKPOINT_ORDER).

    Takes the first ``checkpoint`` picks in post order, settles them with ``payout_of`` (the
    official win payout under policy v2; ``frozen_payout`` reproduces v1) and bootstraps the
    race-day CI: lower bound > 1 → passed, upper bound < 1 → failed, otherwise continue (300) /
    undecided (600). Returns the decision and everything the record stores.

    The record key ``roi_frozen`` is the 0019 column name and holds the ROI under ``payout_of``;
    which settlement it was is recorded as ``bootstrap["settlement"]``."""
    if checkpoint not in CHECKPOINTS:
        raise ValueError(f"checkpoint must be one of {CHECKPOINTS}")
    settlement = settlement_basis(payout_of)
    ordered = _ordered(counted)
    if len(ordered) < checkpoint:
        raise ValueError(f"need {checkpoint} counted picks, got {len(ordered)}")
    head = ordered[:checkpoint]
    stats = ratio_ci(head, payout_of, b=b, seed=seed)
    lo, hi = stats["ci_low"], stats["ci_high"]
    if lo is not None and lo > 1.0:
        decision = "passed"
    elif hi is not None and hi < 1.0:
        decision = "failed"
    else:
        decision = "continue" if checkpoint == CHECKPOINTS[0] else "undecided"
    ids = "\n".join(p.pick_id for p in head)
    record = {
        "checkpoint": checkpoint,
        "n_counted": len(head),
        "n_hits": stats["hits"],
        "roi_frozen": stats["roi"],
        "ci_low": lo,
        "ci_high": hi,
        "p_one_sided": stats["p_one_sided"],
        "counted_pick_ids_sha256": hashlib.sha256(ids.encode("utf-8")).hexdigest(),
        "last_pick_id": head[-1].pick_id,
        "bootstrap": {
            **BOOTSTRAP,
            "b": int(BOOTSTRAP["b"] if b is None else b),
            "seed": int(BOOTSTRAP["seed"] if seed is None else seed),
            "settlement": settlement,
            "n_days": stats["n_days"],
            "day_keys_sha256": stats["day_keys_sha256"],
            "numpy_version": np.__version__,
        },
    }
    return decision, record


def stage_from(decisions: Mapping[int, str], n_counted: int) -> tuple[str, int | None, bool]:
    """(stage, decided checkpoint, checkpoint_pending) — the recorded decisions are the truth.

    Only the part with no record falls back to the live count (researching / observing).
    ``checkpoint_pending`` = the count reached a checkpoint whose decision is not recorded yet."""
    d300 = decisions.get(300)
    d600 = decisions.get(600)
    if d600 is not None and d300 != "continue":
        raise ValueError("a 600-point decision requires a 300-point 'continue'")
    if d300 in ("passed", "failed"):
        return d300, 300, False
    if d300 == "continue":
        if d600 in _FINAL:
            return d600, 600, False
        return "observing", 300, n_counted >= CHECKPOINTS[1]
    if d300 is not None:
        raise ValueError(f"invalid 300-point decision {d300!r}")
    stage = "researching" if n_counted < OBSERVING_MIN else "observing"
    return stage, None, n_counted >= CHECKPOINTS[0]


def next_checkpoint(stage: str, checkpoint: int | None) -> int | None:
    if stage in _FINAL:
        return None
    return CHECKPOINTS[1] if checkpoint == CHECKPOINTS[0] else CHECKPOINTS[0]


def prospective_level(stage: str, point_roi: float | None) -> int:
    """前向き検証 axis: 3 = passed; 2 = observing with point ROI > 100%; 1 otherwise."""
    if stage == "passed":
        return 3
    if stage == "observing" and point_roi is not None and point_roi > 1.0:
        return 2
    return 1


# =============================================================================================
# Frozen statistics and levels (plan 0.3・T007). Values = evidence/rules_S1_S5_freeze.json.
# =============================================================================================


@dataclass(frozen=True)
class FrozenStats:
    roi: float
    ci_low: float
    ci_high: float
    p_one_sided: float
    n: int
    hits: int


@dataclass(frozen=True)
class PriceNoise:
    sigma: float
    roi: float
    n: int
    overlap: float


@dataclass(frozen=True)
class SelectedCalibration:
    """Expected-return units only (p̂ and win rates stay in the evidence JSON)."""

    n: int
    mean_ev: float
    realized_roi: float


@dataclass(frozen=True)
class AttentionRule:
    definition: RuleDefinition
    backtest_all: FrozenStats
    backtest_c: FrozenStats
    bets_2024_25_26: tuple[int, int, int]
    price_noise: tuple[PriceNoise, ...]
    selected_all: SelectedCalibration
    selected_c: SelectedCalibration

    @property
    def id(self) -> str:
        return self.definition.id


#: refrozen from the production 15-seed fits (T006, 2026-10-02): evidence/rules_S1_S5_freeze.json
FROZEN_SOURCE = "production-ens15-refreeze-2026-10-02"
#: how the frozen backtest CIs / p-values were computed (= the evidence JSON's bootstrap / pvalue)
FROZEN_BOOTSTRAP = {
    "impl": "horseracing_eval.bootstrap.race_block_ratio_bootstrap_ci_v1",
    "b": 20000,
    "seed": 20260905,
    "block": "race_day",
    "block_universe": "race-days with >=1 selected bet of the rule in the window",
    "day_key_order": "ascending ISO race_date",
    "rng": "numpy.default_rng(PCG64)",
    "pvalue": "centered_one_sided_p_from_replicates (same draws as the CI)",
}

RULES: tuple[AttentionRule, ...] = (
    AttentionRule(
        _BY_ID["S1"],
        FrozenStats(1.210735, 1.053376, 1.371353, 0.0006, 5235, 225),
        FrozenStats(1.176839, 0.854896, 1.517019, 0.114644, 1183, 47),
        (97, 105, 102),
        (
            PriceNoise(0.1, 1.166235, 6432, 0.586),
            PriceNoise(0.2, 1.044695, 9981, 0.3),
            PriceNoise(0.3, 0.966821, 14786, 0.165),
        ),
        SelectedCalibration(5235, 1.405512, 1.210735),
        SelectedCalibration(1183, 1.32409, 1.176839),
    ),
    AttentionRule(
        _BY_ID["S2"],
        FrozenStats(1.291924, 1.08847, 1.506363, 0.00015, 3108, 141),
        FrozenStats(1.401758, 0.886217, 1.972972, 0.026799, 512, 24),
        (43, 32, 46),
        (
            PriceNoise(0.1, 1.227577, 3899, 0.568),
            PriceNoise(0.2, 1.076371, 6620, 0.264),
            PriceNoise(0.3, 0.983593, 10844, 0.132),
        ),
        SelectedCalibration(3108, 1.515389, 1.291924),
        SelectedCalibration(512, 1.430179, 1.401758),
    ),
    AttentionRule(
        _BY_ID["S3"],
        FrozenStats(1.064064, 0.9828, 1.151261, 0.059297, 22384, 1315),
        FrozenStats(1.112789, 0.932525, 1.309569, 0.100795, 4848, 273),
        (422, 413, 301),
        (
            PriceNoise(0.1, 1.033016, 28914, 0.62),
            PriceNoise(0.2, 0.956798, 50134, 0.316),
            PriceNoise(0.3, 0.907118, 81229, 0.18),
        ),
        SelectedCalibration(22384, 1.381646, 1.064064),
        SelectedCalibration(4848, 1.309934, 1.112789),
    ),
    AttentionRule(
        _BY_ID["S4"],
        FrozenStats(1.14284, 1.029091, 1.258316, 0.00275, 8987, 371),
        FrozenStats(1.034672, 0.832284, 1.250163, 0.361282, 2515, 93),
        (226, 240, 224),
        (
            PriceNoise(0.1, 1.099928, 10611, 0.615),
            PriceNoise(0.2, 1.00851, 14899, 0.349),
            PriceNoise(0.3, 0.956266, 19986, 0.213),
        ),
        SelectedCalibration(8987, 1.296997, 1.14284),
        SelectedCalibration(2515, 1.229117, 1.034672),
    ),
    AttentionRule(
        _BY_ID["S5"],
        FrozenStats(1.008277, 0.943703, 1.076621, 0.39458, 30036, 1820),
        FrozenStats(0.995144, 0.863715, 1.137162, 0.523174, 7682, 438),
        (659, 689, 543),
        (
            PriceNoise(0.1, 0.984019, 36553, 0.669),
            PriceNoise(0.2, 0.941295, 56691, 0.381),
            PriceNoise(0.3, 0.890056, 85718, 0.233),
        ),
        SelectedCalibration(30036, 1.401886, 1.008277),
        SelectedCalibration(7682, 1.334943, 0.995144),
    ),
)
_RULE_BY_ID: dict[str, AttentionRule] = {r.id: r for r in RULES}


#: Buy-time expectation (139 D6/D13): the ROI of buying, at the judged (pre-race) odds, the horses
#: that matched there. The prospective record is read against THIS, not against the frozen table —
#: and only until it accumulates: once the official-payout record is long enough it replaces this
#: conversion (it is a conversion from past data, never a realized figure).
#:
#: Version ``buy-time-v2`` (2026-10-04) after the independent adversarial re-derivation
#: ``specs/139-official-payout-settlement/evidence/r02_verification.md`` (confirmed with caveats):
#:
#: * not one 3-digit point — two estimators disagree by about ±0.06 and that spread lies outside
#:   every CI: E_S = frozen ALL ROI x rho (rho = judged-odds vs closing-odds selection on the
#:   closing calibration table g) and G_J = g applied to the judged selection's closing state.
#:   ``range_low``/``range_high`` are the two point values, each rounded to the nearest 0.05;
#: * ``ci_low``/``ci_high`` = the envelope of both estimators' 95% CIs, rounded OUTWARD to 0.01.
#:   S1's interval includes 1 (100%); S3/S4/S5's are below 1 — ``interval_includes_one`` says which;
#: * S2's interval is invalid (its denominator is 2 closing-selected horses) so S2 shows no value of
#:   its own: S2 ⊂ S1 (same odds band and gap, EV > 1.3 vs EV > 1.2) — ``included_in="S1"``;
#: * no time-of-day values (the pre-registered contrast was null), version and period attached.
#:
#: ``buy-time-v1`` (single point 0.895/... + CI) stays recorded in the evidence file but is no
#: longer served. Versioned (D13): ``BUY_TIME_EXPECTATION_VERSION`` names exactly these numbers —
#: the pair is recorded in the evidence file
#: ``specs/139-official-payout-settlement/evidence/buy_time_expectation.json`` and a test pins
#: the registry to it, so a changed number without a new version fails. Served only when
#: ``BUY_TIME_EXPECTATION_VERIFIED`` is True (D6); otherwise ``buy_time_expectation`` returns None
#: and api/front show nothing but "awaiting verification".
_BUY_TIME_RANGE_STEP = 0.05


@dataclass(frozen=True)
class BuyTimeExpectation:
    """One rule's buy-time expectation: either a range with its interval (all four numbers set,
    ``included_in`` None) or no value of its own (all four None) and the id of the rule whose value
    covers its horses (``included_in``)."""

    range_low: float | None
    range_high: float | None
    ci_low: float | None
    ci_high: float | None
    included_in: str | None

    def __post_init__(self) -> None:
        numbers = (self.range_low, self.range_high, self.ci_low, self.ci_high)
        if all(v is None for v in numbers):
            if self.included_in is None:
                raise ValueError("a buy-time expectation without numbers must name included_in")
            if self.included_in not in RULE_IDS:
                raise ValueError(f"included_in names an unknown rule {self.included_in!r}")
            return
        if any(v is None for v in numbers):
            raise ValueError("range and interval are shown whole or not at all")
        if self.included_in is not None:
            raise ValueError("a buy-time expectation with its own numbers has no included_in")
        lo, hi, ci_lo, ci_hi = (float(v) for v in numbers)  # type: ignore[arg-type]
        if not all(math.isfinite(v) for v in (lo, hi, ci_lo, ci_hi)):
            raise ValueError("buy-time expectation numbers must be finite")
        if lo > hi:
            raise ValueError("range_low must not exceed range_high")
        if ci_lo > lo or hi > ci_hi:
            raise ValueError("the interval must contain the range")
        for v in (lo, hi):
            steps = v / _BUY_TIME_RANGE_STEP
            if abs(steps - round(steps)) > 1e-9:
                raise ValueError(f"range values are multiples of {_BUY_TIME_RANGE_STEP}: {v!r}")

    @property
    def interval_includes_one(self) -> bool | None:
        """Does the interval reach 1 (100%)? None when the rule shows no value of its own."""
        if self.ci_high is None:
            return None
        return self.ci_high >= 1.0


BUY_TIME_EXPECTATION_VERSION = "buy-time-v2"
BUY_TIME_EXPECTATION: dict[str, BuyTimeExpectation] = {
    # E_S 0.895 [0.735, 1.088] / G_J 0.833 [0.74, 0.93]
    "S1": BuyTimeExpectation(0.85, 0.90, 0.73, 1.09, None),
    # E_S 0.891 (interval invalid: 2-horse denominator) / G_J 0.883 [0.72, 1.07] — S2 ⊂ S1
    "S2": BuyTimeExpectation(None, None, None, None, "S1"),
    # E_S 0.856 [0.763, 0.977] / G_J 0.830 [0.76, 0.90]
    "S3": BuyTimeExpectation(0.85, 0.85, 0.76, 0.98, None),
    # E_S 0.831 [0.726, 0.948] / G_J 0.798 [0.73, 0.88]
    "S4": BuyTimeExpectation(0.80, 0.85, 0.72, 0.95, None),
    # E_S 0.786 [0.687, 0.898] / G_J 0.848 [0.78, 0.92]
    "S5": BuyTimeExpectation(0.80, 0.85, 0.68, 0.92, None),
}
#: True only after an independent (adversarial) re-derivation reproduced the numbers above; the
#: verification is then named in ``BUY_TIME_EXPECTATION_SOURCE["status"]`` ("verified — ...") and
#: a changed number gets a new version instead.
BUY_TIME_EXPECTATION_VERIFIED = True
BUY_TIME_EXPECTATION_SOURCE = {
    "version": BUY_TIME_EXPECTATION_VERSION,
    "report": "docs/roi-missed-patterns-20261004/report.md R02",
    "period": "2026-08-02..2026-10-04",
    "pairs": 564,
    "races": 444,
    "race_days": 17,
    "method": (
        "range of two estimators (frozen ALL ROI x rho; closing table g applied to the judged "
        "selection) rounded to 5%; interval = envelope of both 95% CIs rounded outward"
    ),
    "computed_on": "2026-10-04",
    "status": (
        "verified — independent adversarial re-derivation 2026-10-04 "
        "(specs/139-official-payout-settlement/evidence/r02_verification.md): "
        "confirmed with caveats"
    ),
    "verification": "specs/139-official-payout-settlement/evidence/r02_verification.md",
}


def _validate_buy_time_registry() -> None:
    """Every rule has an entry; an ``included_in`` points at ANOTHER rule that shows its own value
    (never at itself, never at a rule that is itself only included)."""
    if tuple(BUY_TIME_EXPECTATION) != RULE_IDS:
        raise ValueError("BUY_TIME_EXPECTATION must list every rule in rank order")
    for rid, exp in BUY_TIME_EXPECTATION.items():
        if exp.included_in is None:
            continue
        if exp.included_in == rid:
            raise ValueError(f"{rid}: included_in names the rule itself")
        if BUY_TIME_EXPECTATION[exp.included_in].included_in is not None:
            raise ValueError(f"{rid}: included_in names a rule without a value of its own")


_validate_buy_time_registry()


def buy_time_expectation(rule_id: str) -> BuyTimeExpectation | None:
    """The rule's buy-time expectation to SERVE, or None while it is not independently verified
    (D6). Read at call time (the flag flips with a registry change, never at runtime)."""
    if not BUY_TIME_EXPECTATION_VERIFIED:
        return None
    if not str(BUY_TIME_EXPECTATION_SOURCE["status"]).startswith("verified"):
        raise ValueError("BUY_TIME_EXPECTATION_VERIFIED is set but the source status is not")
    try:
        return BUY_TIME_EXPECTATION[rule_id]
    except KeyError:
        raise ValueError(f"unknown attention rule {rule_id!r}") from None


def rule(rule_id: str) -> AttentionRule:
    try:
        return _RULE_BY_ID[rule_id]
    except KeyError:
        raise ValueError(f"unknown attention rule {rule_id!r}") from None


def backtest_level(r: AttentionRule) -> int:
    """過去検証 axis: 3 = 通算 CI lower > 100% and 確認窓 ≥ 110%; 2 = both points > 100%; else 1."""
    if r.backtest_all.ci_low > 1.0 and r.backtest_c.roi >= 1.10:
        return 3
    if r.backtest_all.roi > 1.0 and r.backtest_c.roi > 1.0:
        return 2
    return 1


def price_noise_level(r: AttentionRule) -> int:
    """価格ずれ試験 axis: 3 = σ=0.2 still > 100% AND keeps half the edge; 2 = σ=0.1 > 100%;
    else 1."""
    by_sigma = {round(n.sigma, 6): n for n in r.price_noise}
    s2 = by_sigma.get(0.2)
    s1 = by_sigma.get(0.1)
    edge = r.backtest_all.roi - 1.0
    if s2 is not None and s2.roi > 1.0 and edge > 0 and (s2.roi - 1.0) >= 0.5 * edge:
        return 3
    if s1 is not None and s1.roi > 1.0:
        return 2
    return 1


def chip_rule(
    applicable: Iterable[str], stages: Mapping[str, str]
) -> tuple[str | None, bool, str | None]:
    """(chip rule, S2 sub-chip, chip stage).

    Chip = the highest-ranked applicable non-control rule whose stage is neither failed nor
    undecided; if every such rule is failed/undecided, the highest-ranked of them (the chip never
    disappears — FR-012). The S2 sub-chip only accompanies an S1 chip when S2 also applies and is
    still alive (a main S2 chip gets no sub-chip)."""
    applied = set(applicable)
    candidates = [d.id for d in RULE_DEFINITIONS if d.id in applied and not d.control]
    if not candidates:
        return None, False, None
    alive = [r for r in candidates if stages.get(r, "researching") not in ("failed", "undecided")]
    chip = alive[0] if alive else candidates[0]
    s2 = chip == "S1" and "S2" in applied and stages.get("S2", "researching") not in (
        "failed", "undecided")
    return chip, s2, stages.get(chip, "researching")


def chip_now(defn: RuleDefinition, *, current_ens_ev, current_single_ev, current_odds,
             days_since_last) -> str:
    """Does the chip's condition still hold on the current values? matches / no_longer / unknown.

    ``unknown`` when the current row is missing (EV the rule uses or the odds is None)."""
    ev = current_ens_ev if defn.uses_ensemble else current_single_ev
    if ev is None or current_odds is None:
        return "unknown"
    ok = matches(defn, ens_ev=current_ens_ev, single_ev=current_single_ev, odds=current_odds,
                 days_since_last=days_since_last)
    return "matches" if ok else "no_longer"
