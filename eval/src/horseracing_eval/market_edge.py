"""Feature 020 US3: market-edge diagnostics (SECONDARY — never the adoption gate).

Measures whether model p has discrimination BEYOND the market vote-share q (= (1/odds)/Σ(1/odds)).
codex #D: a better absolute calibration (LogLoss/ECE, the US2 gate) does NOT imply beating the
market, because public-information features are largely priced in. These are diagnostics only:
- p_minus_q_summary: mean p vs mean q vs realized win rate (calibration-in-the-large).
- edge_bucket_winrate: realized win rate by (p − q) edge bucket — does positive model edge pay off?
- pq_logloss: model-p LogLoss vs market-q LogLoss on the same finished horses.
pseudo-ROI / Kelly are a further SECONDARY signal but live in betting (011/016); eval does NOT
import betting (would create a dependency cycle), so run those backtests there, not here.
"""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .dataset import load_eval_races
from .splits import FIRST_VALID_YEAR, expanding_folds

_EPS = 1e-12


def _market_q(odds: list[float | None]) -> dict[int, float]:
    inv = {i: (1.0 / o) for i, o in enumerate(odds) if o is not None and o > 0.0}
    s = sum(inv.values())
    return {i: v / s for i, v in inv.items()} if s > 0 else {}


def p_minus_q_summary(p: list[float], q: list[float], win: list[int]) -> dict:
    n = len(p)
    if n == 0:
        return {"n": 0, "mean_p": 0.0, "mean_q": 0.0, "realized": 0.0}
    return {"n": n, "mean_p": sum(p) / n, "mean_q": sum(q) / n, "realized": sum(win) / n}


def edge_bucket_winrate(
    p: list[float], q: list[float], win: list[int], *, bins=(-1.0, -0.02, 0.0, 0.02, 1.0)
) -> list[dict]:
    out: list[dict] = []
    for lo, hi in zip(bins[:-1], bins[1:], strict=True):
        idx = [i for i in range(len(p)) if lo <= (p[i] - q[i]) < hi]
        if not idx:
            out.append({"edge_lo": lo, "edge_hi": hi, "n": 0, "win_rate": 0.0, "mean_edge": 0.0})
            continue
        out.append({
            "edge_lo": lo, "edge_hi": hi, "n": len(idx),
            "win_rate": sum(win[i] for i in idx) / len(idx),
            "mean_edge": sum(p[i] - q[i] for i in idx) / len(idx),
        })
    return out


def pq_logloss(p: list[float], q: list[float], win: list[int]) -> dict:
    """Per-horse binary LogLoss of model p vs market q on the same rows (lower = better)."""
    if not p:
        return {"logloss_p": 0.0, "logloss_q": 0.0}

    def _ll(pr):
        return -sum(
            w * math.log(max(x, _EPS)) + (1 - w) * math.log(max(1 - x, _EPS))
            for x, w in zip(pr, win, strict=True)
        ) / len(pr)

    return {"logloss_p": _ll(p), "logloss_q": _ll(q)}


@dataclass(frozen=True)
class MarketEdgeReport:
    n_horses: int
    summary: dict
    edge_buckets: list[dict]
    pq_logloss: dict
    note: str = ("SECONDARY diagnostic. Better absolute calibration ≠ market excess. "
                 "pseudo-ROI/Kelly are run in betting (011/016), not here (no betting dep).")


def evaluate_market_edge(
    session: Session,
    *,
    predictor,
    first_valid_year: int = FIRST_VALID_YEAR,
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
) -> MarketEdgeReport:
    races = load_eval_races(session, start_date=start_date, end_date=end_date)
    p_all: list[float] = []
    q_all: list[float] = []
    win_all: list[int] = []

    for fold in expanding_folds(races, first_valid_year):
        predictor.fit([er.context for er in fold.train])
        for er in fold.valid:
            preds = predictor.predict_race(er.context)
            horses = er.context.started_horses
            q = _market_q([h.result_market.odds for h in horses])
            winners = {sl.horse_id for sl in er.labels if sl.win == 1}
            for i, h in enumerate(horses):
                if i not in q or h.horse_id not in preds:
                    continue
                p_all.append(float(preds[h.horse_id].win))
                q_all.append(q[i])
                win_all.append(1 if h.horse_id in winners else 0)

    return MarketEdgeReport(
        n_horses=len(p_all),
        summary=p_minus_q_summary(p_all, q_all, win_all),
        edge_buckets=edge_bucket_winrate(p_all, q_all, win_all),
        pq_logloss=pq_logloss(p_all, q_all, win_all),
    )
