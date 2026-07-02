"""Estimated (010/011) vs REAL exotic odds divergence (Feature 012, constitution III eval-first).

For each race the 010/011 estimated odds O_est (priced from WIN odds via PL) are matched to the real
exotic_odds by the SAME canonical selection, and the signed log-ratio log(real / estimated) is
summarized per bet type: coverage_rate (what fraction of estimated candidates have a real price),
median, MAE, and P90 of |log-ratio|. The estimated side is the baseline and is double-pseudo; real
is the measured truth. Coverage is always reported so partial coverage is never read as full. This
quantifies how trustworthy the 011 estimated fallback is — it never feeds the model (leak boundary).
"""

from __future__ import annotations

import datetime
import math

from horseracing_db.enums import EntryStatus
from horseracing_db.models import Race, RaceHorse
from horseracing_features.builder import build_feature_matrix
from horseracing_serving.model_loader import load_serving_model
from horseracing_serving.predictor import predict_race
from sqlalchemy import select
from sqlalchemy.orm import Session

from .exotic_ev import candidate_bets, canonical_field
from .exotic_market import load_real_exotic_odds
from .exotic_types import ALL_EXOTIC, DivergenceReport


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, math.ceil(q * len(s)) - 1))
    return s[idx]


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def summarize_divergence(
    bet_type: str, n_estimated: int, log_ratios: list[float]
) -> DivergenceReport:
    """Pure summary: coverage + log(real/est) median/MAE/P90 (estimated=baseline, double-pseudo)."""
    abs_lrs = [abs(x) for x in log_ratios]
    return DivergenceReport(
        bet_type=bet_type,
        n_estimated=n_estimated,
        n_pairs=len(log_ratios),
        coverage_rate=(len(log_ratios) / n_estimated) if n_estimated else 0.0,
        log_ratio_median=_median(log_ratios),
        log_ratio_mae=(sum(abs_lrs) / len(abs_lrs)) if abs_lrs else 0.0,
        log_ratio_p90=_percentile(abs_lrs, 0.90),
    )


def _race_field(session: Session, model, race_id: str, feature_rows):
    preds, _, _ = predict_race(model, race_id, feature_rows)
    if not preds:
        return None
    predictions: dict[int, float | None] = {}
    odds: dict[int, float | None] = {}
    scratched: dict[int, str] = {}
    number_to_id: dict[int, str] = {}
    for rh in session.scalars(select(RaceHorse).where(RaceHorse.race_id == race_id)):
        if rh.horse_number is None:
            continue
        n = int(rh.horse_number)
        number_to_id[n] = rh.horse_id
        if rh.entry_status in EntryStatus.NON_STARTERS:
            scratched[n] = rh.entry_status
            continue
        predictions[n] = preds[rh.horse_id].win if rh.horse_id in preds else None
        odds[n] = float(rh.odds) if rh.odds is not None else None
    return canonical_field(race_id, predictions, odds, scratched=scratched,
                           number_to_id=number_to_id)


def exotic_divergence(
    session: Session,
    *,
    date_from: datetime.date,
    date_to: datetime.date,
    bet_types=ALL_EXOTIC,
    payout_rates: dict[str, float] | None = None,
    odds_cap: float = 10000.0,
    model_version: str | None = None,
    calibrator=None,
) -> dict[str, DivergenceReport]:
    """Per-bet-type divergence of estimated O_est vs real exotic_odds over the period.

    ``calibrator`` (Feature 013, opt-in) FL-bias-corrects the market q before O_est (compare
    raw-q vs corrected-q' divergence); None = raw q."""
    model = load_serving_model(session, model_version)
    feature_rows = build_feature_matrix(session, end_date=date_to)
    present = set(feature_rows["race_id"].unique())
    races = session.execute(
        select(Race.race_id)
        .where(Race.race_date >= date_from)
        .where(Race.race_date <= date_to)
        .order_by(Race.race_id)
    ).all()

    n_est: dict[str, int] = {}
    log_ratios: dict[str, list[float]] = {}
    for (race_id,) in races:
        if race_id not in present:
            continue
        field = _race_field(session, model, race_id, feature_rows)
        if field is None or not field.p_norm:
            continue
        est = candidate_bets(field, bet_types=bet_types, payout_rates=payout_rates,
                             odds_cap=odds_cap, calibrator=calibrator)
        real = load_real_exotic_odds(session, race_id)
        for bt, bets in est.items():
            for b in bets:
                n_est[bt] = n_est.get(bt, 0) + 1
                r = real.get((b.bet_type, tuple(b.selection)))
                if r is not None and r > 0 and b.o_est > 0:
                    log_ratios.setdefault(bt, []).append(math.log(r / b.o_est))

    return {bt: summarize_divergence(bt, n_est[bt], log_ratios.get(bt, [])) for bt in n_est}


def compare_divergence(
    session: Session,
    *,
    date_from: datetime.date,
    date_to: datetime.date,
    calibrator,
    bet_types=ALL_EXOTIC,
    payout_rates: dict[str, float] | None = None,
    odds_cap: float = 10000.0,
    model_version: str | None = None,
):
    """Estimated-vs-real exotic divergence BEFORE (raw q) vs AFTER (FL-corrected q') — 013.

    Runs exotic_divergence twice over the same period; returns {bet_type: DivergenceDeltaReport}.
    DIAGNOSTIC only — the FL adoption gate is the win-rate calibration
    (probability.evaluate_q_vs_qprime); real exotic pools carry their own takeout/bias."""
    from horseracing_probability.market_calibration import DivergenceDeltaReport

    raw = exotic_divergence(
        session, date_from=date_from, date_to=date_to, bet_types=bet_types,
        payout_rates=payout_rates, odds_cap=odds_cap, model_version=model_version,
    )
    corr = exotic_divergence(
        session, date_from=date_from, date_to=date_to, bet_types=bet_types,
        payout_rates=payout_rates, odds_cap=odds_cap, model_version=model_version,
        calibrator=calibrator,
    )
    out: dict[str, DivergenceDeltaReport] = {}
    for bt in raw:
        rq, cq = raw[bt], corr.get(bt)
        out[bt] = DivergenceDeltaReport(
            bet_type=bt,
            coverage_rate=rq.coverage_rate,
            logratio_median_q=rq.log_ratio_median,
            logratio_mae_q=rq.log_ratio_mae,
            logratio_p90_q=rq.log_ratio_p90,
            logratio_median_qp=(cq.log_ratio_median if cq else 0.0),
            logratio_mae_qp=(cq.log_ratio_mae if cq else 0.0),
            logratio_p90_qp=(cq.log_ratio_p90 if cq else 0.0),
        )
    return out
