"""Model win-probability calibration p → p' for Kelly overconfidence correction (Feature 017).

The model win prob p (race_predictions.win_prob, 006) tends to be over/under-confident; Feature 016
Kelly amplifies that error (f*=edge/(O−1)). We calibrate p against realized 1st-place outcomes. This
is the MODEL p side — the mirror of Feature 013's MARKET q calibration — kept strictly separate
(p≠q): the p calibrator is NEVER applied to the market-odds path, and odds/q never enter here.

Canonical method is the power/temperature family p'_i ∝ p_i^γ (γ=1/T; γ<1 softens overconfidence),
with γ fit by the RACE-NORMALIZED conditional-logit winner likelihood (bounded golden-section MLE,
deterministic) — reusing 013's machinery on p. p' is run through the 009 engine's normalize+clip so
the evaluated vector equals what the engine consumes (idempotent). marginal calibration does NOT
guarantee joint (exacta/trifecta) calibration (PL/Harville is non-linear), so joint reliability is
evaluated separately and non-degradation is an adoption gate.

Leak boundary: the calibrator is fit train-only / walk-forward (strictly before the target race);
method/hyperparameter selection stays inside the training window; calibrated p' / haircut / edge /
Kelly fraction are NEVER fed back as model features. Dead heats are excluded from fitting.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import (
    PredictionRun,
    Race,
    RaceHorse,
    RacePrediction,
    RaceResult,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from .calibration import _realized_combo, _score  # combo loader + multiclass NLL/Brier
from .engine import joint_probabilities
from .fl_bias import GAMMA_MAX, GAMMA_MIN, _engine_normalize, _golden_min, race_before
from .market_calibration import DEFAULT_BINS, _reliability_and_ece

_EPS = 1e-12
_FLAT_TOL = 1e-9
_K = {"exacta": 2, "trifecta": 3}


@dataclass(frozen=True)
class PCalibrator:
    method: str                       # "power" (MVP) | "identity" (fallback)
    params: dict                      # power: {"gamma": float}
    train_window: tuple | None
    n_races: int
    n_samples: int                    # informative races (winner in-field, non-flat p)
    prob_range: tuple[float, float]   # (min p, max p) seen in training (extrapolation audit)
    select: str
    base_model_version: str | None
    logic_version: str
    sufficient: bool = True           # False → identity fallback


# --- normalize / apply ------------------------------------------------------
def _norm(p: dict[str, float]) -> dict[str, float]:
    """009 engine-consistent renormalize + clip (Σ=1); evaluated == engine-consumed vector."""
    return _engine_normalize(p)


def _apply_gamma(p: dict[str, float], gamma: float) -> dict[str, float]:
    g = {h: (max(ph, _EPS) ** gamma) for h, ph in p.items()}
    s = sum(g.values())
    if s <= 0.0:
        raise ValueError("Σ p^gamma <= 0")
    return _norm({h: gv / s for h, gv in g.items()})


#: Feature 048: pre-registered pivot for the asymmetric two-piece power (specs/048 — FIXED,
#: never fitted, never tuned after seeing results). Matches the 047 q_band boundary.
TWO_GAMMA_PIVOT = 0.15


def _two_gamma_weight(ph: float, gamma_lo: float, gamma_hi: float, pivot: float) -> float:
    """Continuous, monotone two-piece power: p^γlo below the pivot, matched at the pivot above."""
    ph = max(ph, _EPS)
    if ph <= pivot:
        return ph ** gamma_lo
    return (pivot ** (gamma_lo - gamma_hi)) * (ph ** gamma_hi)


def _apply_two_gamma(
    p: dict[str, float], gamma_lo: float, gamma_hi: float, pivot: float = TWO_GAMMA_PIVOT
) -> dict[str, float]:
    """Feature 048: asymmetric calibration on the race-normalized vector (017 canonical)."""
    pn = _norm(p)
    g = {h: _two_gamma_weight(ph, gamma_lo, gamma_hi, pivot) for h, ph in pn.items()}
    s = sum(g.values())
    if s <= 0.0:
        raise ValueError("Σ two_gamma weight <= 0")
    return _norm({h: gv / s for h, gv in g.items()})


def apply_p_calibrator(p: dict[str, float], calibrator: PCalibrator) -> dict[str, float]:
    """p → p' (race-normalized, engine-consistent). <2 horses returns engine-normalized p."""
    if len(p) < 2:
        return _norm(p) if p else {}
    if calibrator.method == "identity":
        return _norm(p)
    if calibrator.method == "two_gamma":
        return _apply_two_gamma(
            p, float(calibrator.params["gamma_lo"]), float(calibrator.params["gamma_hi"]),
            float(calibrator.params.get("pivot", TWO_GAMMA_PIVOT)),
        )
    if calibrator.method != "power":
        raise NotImplementedError(f"method '{calibrator.method}' not implemented (power/identity)")
    return _apply_gamma(p, float(calibrator.params["gamma"]))


# --- power gamma MLE (normalized winner likelihood) -------------------------
def _informative(samples: list[tuple[dict[str, float], str | None]]):
    out = []
    for p, winner in samples:
        if winner is None or winner not in p or len(p) < 2:
            continue
        pn = _norm(p)
        if max(pn.values()) - min(pn.values()) < _FLAT_TOL:
            continue  # flat p → no gamma gradient
        out.append((pn, winner))
    return out


def _nll_gamma(gamma: float, races) -> float:
    total = 0.0
    for p, winner in races:
        denom = sum(ph ** gamma for ph in p.values())
        total += -math.log(max((p[winner] ** gamma) / denom, _EPS))
    return total


def fit_power_gamma(samples) -> tuple[float, int]:
    races = _informative(samples)
    if not races:
        return 1.0, 0
    gamma = _golden_min(lambda g: _nll_gamma(g, races), GAMMA_MIN, GAMMA_MAX)
    return gamma, len(races)


def _nll_two_gamma(gamma_lo: float, gamma_hi: float, races, pivot: float) -> float:
    total = 0.0
    for p, winner in races:
        w = {h: _two_gamma_weight(ph, gamma_lo, gamma_hi, pivot) for h, ph in p.items()}
        denom = sum(w.values())
        total += -math.log(max(w[winner] / denom, _EPS))
    return total


def fit_two_gamma(samples, *, pivot: float = TWO_GAMMA_PIVOT) -> tuple[float, float, int]:
    """Feature 048: fit (γlo, γhi) by winner-NLL on the normalized vector (train window only).

    Deterministic: coarse grid over [GAMMA_MIN, GAMMA_MAX]² then two rounds of coordinate-wise
    golden refinement. The pivot is PRE-REGISTERED (specs/048), never fitted.
    """
    races = _informative(samples)
    if not races:
        return 1.0, 1.0, 0
    grid = [GAMMA_MIN + i * (GAMMA_MAX - GAMMA_MIN) / 8 for i in range(9)]
    best = (1.0, 1.0)
    best_nll = _nll_two_gamma(1.0, 1.0, races, pivot)
    for glo in grid:
        for ghi in grid:
            nll = _nll_two_gamma(glo, ghi, races, pivot)
            if nll < best_nll - 1e-12:
                best, best_nll = (glo, ghi), nll
    glo, ghi = best
    for _ in range(2):  # coordinate-wise golden refinement (deterministic)
        glo = _golden_min(
            lambda g, _hi=ghi: _nll_two_gamma(g, _hi, races, pivot), GAMMA_MIN, GAMMA_MAX)
        ghi = _golden_min(
            lambda g, _lo=glo: _nll_two_gamma(_lo, g, races, pivot), GAMMA_MIN, GAMMA_MAX)
    return glo, ghi, len(races)


def fit_p_calibrator(
    samples: list[tuple[dict[str, float], str | None]],
    *,
    method: str = "power",
    select: str = "mle",
    min_races: int = 50,
    min_wins: int = 30,
    train_window: tuple | None = None,
    base_model_version: str | None = None,
    version: str = "pcal-0.1.0",
) -> PCalibrator:
    """Fit a p calibrator on past (p, winner) samples (caller guarantees walk-forward).

    Method/hyperparameter selection happens INSIDE the training window only (no selection leak).
    Insufficient data (min_races / min_wins / no informative races) → identity (γ=1) fallback.
    """
    if method not in ("power", "identity", "two_gamma"):
        raise NotImplementedError(
            f"method '{method}' not implemented (power/identity/two_gamma)")
    if method == "two_gamma":
        glo, ghi, n_info = fit_two_gamma(samples)
        params = {"gamma_lo": glo, "gamma_hi": ghi, "pivot": TWO_GAMMA_PIVOT}
    else:
        gamma, n_info = fit_power_gamma(samples) if method == "power" else (1.0, 0)
        params = {"gamma": gamma}
    # prob_range over non-empty races only (races without predictions contribute nothing).
    ps = [ph for p, _ in samples if len(p) >= 2 for ph in _norm(p).values()]
    prob_range = (min(ps), max(ps)) if ps else (0.0, 1.0)
    sufficient = (
        method != "identity" and len(samples) >= min_races and n_info >= min_wins and n_info >= 1
    )
    eff_method = method if sufficient else "identity"
    if not sufficient:
        params = {"gamma": 1.0}
    # lv: power/identity keep the pre-048 byte format; two_gamma gets its own descriptor.
    if eff_method == "two_gamma":
        desc = (f"pcal=two_gamma;gamma_lo={params['gamma_lo']:.5f};"
                f"gamma_hi={params['gamma_hi']:.5f};pivot={TWO_GAMMA_PIVOT}")
    else:
        desc = f"pcal={eff_method}(p^gamma);gamma={params['gamma']:.5f}"
    logic_version = (
        f"{desc};select={select};"
        f"window={train_window};n_races={len(samples)};n_info={n_info};"
        f"base_mv={base_model_version};v={version}"
    )
    return PCalibrator(
        method=eff_method, params=params, train_window=train_window,
        n_races=len(samples), n_samples=n_info, prob_range=prob_range, select=select,
        base_model_version=base_model_version, logic_version=logic_version, sufficient=sufficient,
    )


# --- walk-forward sample loader ---------------------------------------------
def _latest_run_predictions(session: Session, race_id: str) -> dict[str, float]:
    run = session.scalars(
        select(PredictionRun)
        .where(PredictionRun.race_id == race_id)
        .order_by(PredictionRun.computed_at.desc())
    ).first()
    if run is None:
        return {}
    rows = session.execute(
        select(RacePrediction.horse_id, RacePrediction.win_prob).where(
            RacePrediction.prediction_run_id == run.prediction_run_id
        )
    ).all()
    started = set(
        session.scalars(
            select(RaceHorse.horse_id)
            .where(RaceHorse.race_id == race_id)
            .where(RaceHorse.entry_status == EntryStatus.STARTED)
        )
    )
    return {hid: float(wp) for hid, wp in rows if wp is not None and hid in started}


def _winner(session: Session, race_id: str) -> tuple[str | None, bool]:
    """(winner_horse_id|None, is_dead_heat). Dead heat (multiple 1st) → (None, True)."""
    winners = list(
        session.scalars(
            select(RaceResult.horse_id)
            .where(RaceResult.race_id == race_id)
            .where(RaceResult.result_status == ResultStatus.FINISHED)
            .where(RaceResult.finish_order == 1)
        )
    )
    if len(winners) == 1:
        return winners[0], False
    return None, len(winners) > 1


def load_p_samples(session: Session, *, date_from, date_to):
    """[(race_id, race_date, p_dict, winner|None, is_dead_heat)] ordered by (race_date, race_id)."""
    rows = session.execute(
        select(Race.race_id, Race.race_date)
        .where(Race.race_date >= date_from)
        .where(Race.race_date <= date_to)
        .order_by(Race.race_date, Race.race_id)
    ).all()
    out = []
    for race_id, race_date in rows:
        p = _latest_run_predictions(session, race_id)
        winner, dead_heat = _winner(session, race_id)
        out.append((race_id, race_date, p, winner, dead_heat))
    return out


def split_before(samples, target_date, target_id):
    """Walk-forward: samples strictly before (target_date, target_id). race_before tie-break."""
    return [s for s in samples if race_before(s[1], s[0], target_date, target_id)]


# --- Feature 049: top2/top3 fitting sample loader ---------------------------
def _placed_finishers(session: Session, race_id: str) -> tuple[str | None, str | None, str | None]:
    """(1st, 2nd, 3rd) horse_ids; a position is None when missing or a dead heat (non-unique).

    Results are used ONLY as fit labels here (never selection/features, 憲法 II).
    """
    rows = list(
        session.execute(
            select(RaceResult.horse_id, RaceResult.finish_order)
            .where(RaceResult.race_id == race_id)
            .where(RaceResult.result_status == ResultStatus.FINISHED)
            .where(RaceResult.finish_order.in_((1, 2, 3)))
        ).all()
    )
    by_pos: dict[int, list[str]] = {1: [], 2: [], 3: []}
    for hid, order in rows:
        by_pos[order].append(hid)
    return tuple(  # type: ignore[return-value]
        (by_pos[pos][0] if len(by_pos[pos]) == 1 else None) for pos in (1, 2, 3)
    )


def load_topk_samples(session: Session, *, date_from, date_to):
    """[(race_id, race_date, p_dict, (id1|None, id2|None, id3|None))] by (race_date, race_id).

    Mirrors load_p_samples' run selection (latest run per race) for audit consistency
    (analyze U2). The p_dict is the persisted win_prob over started horses; callers
    normalize it through the engine before fitting. Non-unique finishers (dead heats)
    yield None for that position so a race contributes only to the stages it can label.

    Bulk-loaded (4 set-based queries, no per-race N+1) — byte-identical to the per-race
    ``_latest_run_predictions``/``_placed_finishers`` path (test_load_topk_samples parity),
    just far faster on the full-history fit window used by ``fit_product_stage_discount``
    (perf: ~34k round-trips -> 4 queries). computed_at DESC + prediction_run_id DESC ties the
    "latest run" deterministically (mirrors ``.order_by(computed_at.desc()).first()``).
    """
    from collections import defaultdict

    races = session.execute(
        select(Race.race_id, Race.race_date)
        .where(Race.race_date >= date_from)
        .where(Race.race_date <= date_to)
        .order_by(Race.race_date, Race.race_id)
    ).all()
    if not races:
        return []

    # latest prediction_run per race (DISTINCT ON), scoped to the same date window
    latest = (
        select(
            PredictionRun.race_id.label("race_id"),
            PredictionRun.prediction_run_id.label("run_id"),
        )
        .join(Race, Race.race_id == PredictionRun.race_id)
        .where(Race.race_date >= date_from)
        .where(Race.race_date <= date_to)
        .distinct(PredictionRun.race_id)
        .order_by(
            PredictionRun.race_id,
            PredictionRun.computed_at.desc(),
            PredictionRun.prediction_run_id.desc(),
        )
        .subquery()
    )

    started_by_race: dict[str, set[str]] = defaultdict(set)
    for rid, hid in session.execute(
        select(RaceHorse.race_id, RaceHorse.horse_id)
        .join(Race, Race.race_id == RaceHorse.race_id)
        .where(Race.race_date >= date_from)
        .where(Race.race_date <= date_to)
        .where(RaceHorse.entry_status == EntryStatus.STARTED)
    ).all():
        started_by_race[rid].add(hid)

    # predictions of each race's latest run, kept only for STARTED horses with a non-null prob
    preds_by_race: dict[str, dict[str, float]] = defaultdict(dict)
    for rid, hid, wp in session.execute(
        select(latest.c.race_id, RacePrediction.horse_id, RacePrediction.win_prob).join(
            RacePrediction, RacePrediction.prediction_run_id == latest.c.run_id
        )
    ).all():
        if wp is not None and hid in started_by_race.get(rid, ()):
            preds_by_race[rid][hid] = float(wp)

    by_pos_by_race: dict[str, dict[int, list[str]]] = defaultdict(
        lambda: {1: [], 2: [], 3: []}
    )
    for rid, hid, order in session.execute(
        select(RaceResult.race_id, RaceResult.horse_id, RaceResult.finish_order)
        .join(Race, Race.race_id == RaceResult.race_id)
        .where(Race.race_date >= date_from)
        .where(Race.race_date <= date_to)
        .where(RaceResult.result_status == ResultStatus.FINISHED)
        .where(RaceResult.finish_order.in_((1, 2, 3)))
    ).all():
        by_pos_by_race[rid][order].append(hid)

    def _placed(rid: str) -> tuple[str | None, str | None, str | None]:
        bp = by_pos_by_race.get(rid)
        if bp is None:
            return (None, None, None)
        return tuple(  # type: ignore[return-value]
            (bp[pos][0] if len(bp[pos]) == 1 else None) for pos in (1, 2, 3)
        )

    return [
        (rid, rdate, dict(preds_by_race.get(rid, {})), _placed(rid))
        for rid, rdate in races
    ]


def fit_product_stage_discount(session, *, before_date, min_races=300, calibrator=None):
    """Walk-forward product fit: λ_2/λ_3 from persisted predictions STRICTLY before ``before_date``
    (research D3/D4). ``calibrator`` (e.g. two_gamma) is applied to fit-sample win vectors so fit
    and apply share one distribution. Returns an eval StageDiscount (identity if under-sampled)."""
    import datetime as _dt

    from horseracing_eval.stage_discount import fit_stage_discount

    end = before_date - _dt.timedelta(days=1)  # strictly before (date-level; race_id tie-break n/a)
    raw = load_topk_samples(session, date_from=_dt.date(2007, 1, 1), date_to=end)
    samples = to_topk_samples(raw, calibrator=calibrator)
    return fit_stage_discount(samples, min_races=min_races)


def to_topk_samples(raw, *, calibrator=None):
    """Convert load_topk_samples rows -> eval TopkSample list (engine-normalized win vector +
    finisher indices). ``calibrator`` (a p-calibrator, e.g. two_gamma) is applied to the win
    vector BEFORE normalization so fit and apply share one p distribution (research D4).
    Rows with no unique winner or a winner absent from p are skipped (can't index stage 1)."""
    from horseracing_eval.stage_discount import TopkSample

    samples = []
    for _rid, _rdate, p, placed in raw:
        i1_id, i2_id, i3_id = placed
        if i1_id is None or not p or i1_id not in p:
            continue
        pd = calibrator(p) if calibrator is not None else p  # calibrator maps dict->dict
        pd = _engine_normalize(pd)
        ids = sorted(pd)
        pos = {h: k for k, h in enumerate(ids)}
        samples.append(
            TopkSample(
                win=tuple(pd[h] for h in ids),
                i1=pos.get(i1_id),
                i2=pos.get(i2_id) if i2_id in pos else None,
                i3=pos.get(i3_id) if i3_id in pos else None,
            )
        )
    return samples


# --- p vs p' calibration evaluation (US1 adoption gate) ---------------------
@dataclass(frozen=True)
class PCalibrationReport:
    scope: str
    n_races: int
    n_dead_heat_excluded: int
    nll_p: float
    brier_p: float
    ece_p: float
    nll_pp: float
    brier_pp: float
    ece_pp: float
    reliability_p: list
    reliability_pp: list
    reliability_slope_p: float
    reliability_slope_pp: float
    over_under_top_p: float
    over_under_top_pp: float
    cal_in_large_p: float
    cal_in_large_pp: float
    improved: bool          # p' beats p on NLL (primary adoption signal)


def _slope(reliability) -> float:
    """Weighted least-squares slope of empirical_rate vs mean_pred over non-empty bins (1=ideal)."""
    pts = [(mp, er, n) for (mp, er, n) in reliability if n > 0]
    if len(pts) < 2:
        return 0.0
    w = sum(n for _, _, n in pts)
    mx = sum(mp * n for mp, _, n in pts) / w
    my = sum(er * n for _, er, n in pts) / w
    sxx = sum(n * (mp - mx) ** 2 for mp, _, n in pts)
    sxy = sum(n * (mp - mx) * (er - my) for mp, er, n in pts)
    return sxy / sxx if sxx > 0 else 0.0


def _top_over_under(reliability) -> float:
    """Highest non-empty bin: mean_pred − empirical (positive = overconfident at the top)."""
    for mp, er, n in reversed(reliability):
        if n > 0:
            return mp - er
    return 0.0


def evaluate_p_vs_pprime(samples, calibrator, *, bins=DEFAULT_BINS) -> PCalibrationReport:
    """samples: [(p_dict, winner|None)]. Score raw p vs calibrated p' on realized winners. Pure.

    NLL/Brier use the winner's prob; ECE/reliability bin EVERY horse's prob vs its win indicator on
    the race-normalized vectors (evaluated == engine vector). Dead-heat / no-winner races excluded.
    """
    nll_p = brier_p = nll_pp = brier_pp = 0.0
    n = excluded = 0
    pairs_p: list[tuple[float, bool]] = []
    pairs_pp: list[tuple[float, bool]] = []
    cl_pred_p = cl_pred_pp = cl_won = 0.0
    for p, winner in samples:
        if winner is None or winner not in p or len(p) < 2:
            excluded += 1
            continue
        pn = _norm(p)
        pp = apply_p_calibrator(p, calibrator)
        n += 1
        nll_p += -math.log(max(pn[winner], _EPS))
        nll_pp += -math.log(max(pp[winner], _EPS))
        brier_p += 1.0 - 2.0 * pn[winner] + sum(v * v for v in pn.values())
        brier_pp += 1.0 - 2.0 * pp[winner] + sum(v * v for v in pp.values())
        for h in pn:
            won = h == winner
            pairs_p.append((pn[h], won))
            pairs_pp.append((pp[h], won))
            cl_pred_p += pn[h]
            cl_pred_pp += pp[h]
            cl_won += 1.0 if won else 0.0
    if n == 0:
        raise ValueError("no informative races for p-vs-p' evaluation (insufficient data)")
    rel_p, ece_p = _reliability_and_ece(pairs_p, bins)
    rel_pp, ece_pp = _reliability_and_ece(pairs_pp, bins)
    npairs = max(len(pairs_p), 1)
    return PCalibrationReport(
        scope="overall", n_races=n, n_dead_heat_excluded=excluded,
        nll_p=nll_p / n, brier_p=brier_p / n, ece_p=ece_p,
        nll_pp=nll_pp / n, brier_pp=brier_pp / n, ece_pp=ece_pp,
        reliability_p=rel_p, reliability_pp=rel_pp,
        reliability_slope_p=_slope(rel_p), reliability_slope_pp=_slope(rel_pp),
        over_under_top_p=_top_over_under(rel_p), over_under_top_pp=_top_over_under(rel_pp),
        cal_in_large_p=(cl_pred_p - cl_won) / npairs,
        cal_in_large_pp=(cl_pred_pp - cl_won) / npairs,
        improved=(nll_pp < nll_p),
    )


# --- 009-after joint reliability (marginal calibration does NOT guarantee joint) -------
@dataclass(frozen=True)
class JointReliabilityReport:
    bet_type: str
    n_races: int
    nll_p: float
    brier_p: float
    nll_pp: float
    brier_pp: float
    not_degraded: bool      # p' joint NLL not worse than raw p beyond tolerance (adoption gate)


def evaluate_joint_reliability(
    samples, calibrator, *, bet_type: str, tol: float = 1e-3
) -> JointReliabilityReport:
    """samples: [(p_dict, realized_combo)]. Runs 009 on raw p vs p' and scores the realized combo.

    marginal calibration may not improve (or may hurt) exotic joint calibration; non-degradation of
    the joint winner NLL (within ``tol``) is a MUST adoption gate (analyze F1, FR-005).
    """
    k = _K[bet_type]
    nll_p = brier_p = nll_pp = brier_pp = 0.0
    n = 0
    for p, combo in samples:
        if combo is None or len(combo) != k or any(h not in p for h in combo) or len(p) < 2:
            continue
        jp_p = joint_probabilities(_norm(p))
        jp_pp = joint_probabilities(apply_p_calibrator(p, calibrator))
        dist_p = dict(jp_p.exacta) if bet_type == "exacta" else dict(jp_p.trifecta)
        dist_pp = dict(jp_pp.exacta) if bet_type == "exacta" else dict(jp_pp.trifecta)
        a, b = _score(dist_p, combo)
        c, d = _score(dist_pp, combo)
        nll_p += a
        brier_p += b
        nll_pp += c
        brier_pp += d
        n += 1
    m = max(n, 1)
    nll_p_m, nll_pp_m = nll_p / m, nll_pp / m
    return JointReliabilityReport(
        bet_type=bet_type, n_races=n,
        nll_p=nll_p_m, brier_p=brier_p / m, nll_pp=nll_pp_m, brier_pp=brier_pp / m,
        not_degraded=(nll_pp_m <= nll_p_m + tol),   # F1: joint must not worsen beyond tol
    )


# --- walk-forward DB orchestration (CLI entry, US1) -------------------------
def evaluate_calibration_db(
    session: Session,
    *,
    date_from,
    date_to,
    method: str = "power",
    min_races: int = 50,
    min_wins: int = 30,
    train_frac: float = 0.5,
    base_model_version: str | None = None,
    joint_bet_types: tuple[str, ...] = ("exacta", "trifecta"),
):
    """Fit on the earlier train_frac of [from,to], evaluate on the strictly-later remainder.

    Walk-forward: the eval races are strictly after every train race (race_before tie-break), and
    γ-selection happens only inside the train window (no selection leak). Returns
    (calibrator, PCalibrationReport, {bet_type: JointReliabilityReport}).
    """
    samples = load_p_samples(session, date_from=date_from, date_to=date_to)
    if not samples:
        raise ValueError("no races in window")
    cut = max(1, int(len(samples) * train_frac))
    train, evalset = samples[:cut], samples[cut:]
    if not evalset:
        raise ValueError("empty eval window (increase range or lower train_frac)")
    # train window boundary = last train race; eval must be strictly after it.
    _, t_date, _, _, _ = train[-1]
    t_id = train[-1][0]
    evalset = [s for s in evalset if race_before(t_date, t_id, s[1], s[0])]

    train_pw = [(p, w) for (_rid, _d, p, w, _dh) in train]
    calibrator = fit_p_calibrator(
        train_pw, method=method, min_races=min_races, min_wins=min_wins,
        train_window=(date_from, t_date), base_model_version=base_model_version,
    )
    eval_pw = [(p, w) for (_rid, _d, p, w, _dh) in evalset]
    report = evaluate_p_vs_pprime(eval_pw, calibrator)

    joint: dict[str, JointReliabilityReport] = {}
    for bt in joint_bet_types:
        k = _K[bt]
        pc = [
            (p, _realized_combo(session, rid, k))
            for (rid, _d, p, _w, _dh) in evalset
            if len(p) >= 2
        ]
        joint[bt] = evaluate_joint_reliability(pc, calibrator, bet_type=bt)
    return calibrator, report, joint
