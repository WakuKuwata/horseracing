"""Diagnostic: does model-p calibration + edge haircut make Kelly safer? (Feature 017, US2/US3).

Runs the 016 bankroll backtest under the SAME conditions for raw / calibrated / calibrated+haircut
and compares risk (max drawdown, ruin probability, variance) and growth (log-growth). The adoption
rule is risk-adjusted: calibration must improve quality AND Kelly risk must NOT worsen (a bare
ROI>1, or growth alone, is insufficient — analyze F1 / codex). A 2×2 (raw/cal p × raw/cal q) grid
surfaces double-correction (edge over-shrink) when the Feature 013 market-q calibrator is also used;
the order is fixed q→O_est→p and the p calibrator is never applied to the market-odds path (p≠q).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from .kelly_backtest import BankrollSegment, run_bankroll_backtest
from .kelly_types import KellyConfig


@dataclass(frozen=True)
class ModeResult:
    mode: str                       # raw / cal / cal+haircut
    segment: BankrollSegment        # the kelly / "all" segment
    risk_not_worse: bool            # maxDD AND ruin not worse than raw (must-gate)
    over_conservative: bool         # growth dropped a lot vs raw (over-shrink flag)


@dataclass(frozen=True)
class CalibrationCompareReport:
    results: list[ModeResult]
    verdict: str                    # SUCCESS only if a cal mode improves growth AND risk_not_worse


def _kelly_all(report) -> BankrollSegment:
    return next(s for s in report.segments if s.strategy == "kelly" and s.segment == "all")


def compare_calibration_modes(
    session,
    *,
    date_from,
    date_to,
    cfg: KellyConfig | None = None,
    p_calibrator=None,
    modes=("raw", "cal", "cal+haircut"),
    over_conservative_drop: float = 0.5,
    **backtest_kwargs,
) -> CalibrationCompareReport:
    """Run the bankroll backtest per mode (same conditions) and compare risk/growth.

    ``cfg`` carries the haircut for the cal+haircut mode (haircut_type/haircut). raw/cal force
    haircut off; cal/cal+haircut pass ``p_calibrator``. The caller fits ``p_calibrator`` on a window
    strictly before ``date_from`` (walk-forward).
    """
    cfg = cfg or KellyConfig()
    cfg_no_haircut = dataclasses.replace(cfg, haircut_type="none", haircut=0.0)

    plans = {
        "raw": (None, cfg_no_haircut),
        "cal": (p_calibrator, cfg_no_haircut),
        "cal+haircut": (p_calibrator, cfg),
    }
    segs: dict[str, BankrollSegment] = {}
    for mode in modes:
        pcal, mode_cfg = plans[mode]
        rep = run_bankroll_backtest(
            session, date_from=date_from, date_to=date_to, cfg=mode_cfg,
            p_calibrator=pcal, **backtest_kwargs,
        )
        segs[mode] = _kelly_all(rep)

    raw = segs.get("raw")
    results: list[ModeResult] = []
    for mode in modes:
        s = segs[mode]
        if raw is None or mode == "raw":
            results.append(ModeResult(mode, s, risk_not_worse=True, over_conservative=False))
            continue
        risk_not_worse = (s.max_drawdown <= raw.max_drawdown + 1e-12
                          and s.ruin_probability <= raw.ruin_probability + 1e-12)
        over_conservative = s.log_growth_rate < raw.log_growth_rate - over_conservative_drop
        results.append(ModeResult(mode, s, risk_not_worse, over_conservative))

    # SUCCESS = some calibrated mode improves growth over raw AND does not worsen risk.
    success = raw is not None and any(
        r.mode != "raw" and r.risk_not_worse
        and r.segment.log_growth_rate > raw.log_growth_rate - 1e-12
        for r in results
    )
    verdict = ("SUCCESS(校正で Kelly リスク非悪化かつ成長維持)" if success
               else "NOT-ADOPTED(校正で Kelly が改善せず/リスク悪化)")
    return CalibrationCompareReport(results=results, verdict=verdict)


@dataclass(frozen=True)
class PQCell:
    p_cal: bool
    q_cal: bool
    segment: BankrollSegment


@dataclass(frozen=True)
class PQGridReport:
    cells: list[PQCell]
    double_correction_detected: bool   # both-on shrinks edge enough to drop bets / lower growth


def compare_pq_grid(
    session,
    *,
    date_from,
    date_to,
    cfg: KellyConfig | None = None,
    p_calibrator=None,
    q_calibrator=None,
    **backtest_kwargs,
) -> PQGridReport:
    """2×2 (raw/cal p × raw/cal q) Kelly risk grid (Feature 017 US3, SC-009).

    Order is fixed q→O_est→p inside the backtest: the q calibrator (013) corrects the estimated
    odds, the p calibrator (017) corrects model p; the p calibrator never touches the market path.
    Surfaces double-correction (edge over-shrink) when both are on.
    """
    cfg = cfg or KellyConfig()
    cells: list[PQCell] = []
    seg_by_key: dict[tuple[bool, bool], BankrollSegment] = {}
    for p_on in (False, True):
        for q_on in (False, True):
            rep = run_bankroll_backtest(
                session, date_from=date_from, date_to=date_to, cfg=cfg,
                p_calibrator=p_calibrator if p_on else None,
                q_calibrator=q_calibrator if q_on else None,
                **backtest_kwargs,
            )
            seg = _kelly_all(rep)
            cells.append(PQCell(p_cal=p_on, q_cal=q_on, segment=seg))
            seg_by_key[(p_on, q_on)] = seg

    both = seg_by_key[(True, True)]
    p_only = seg_by_key[(True, False)]
    q_only = seg_by_key[(False, True)]
    # double-correction: both-on bets fewer than each single correction (edge shrank past threshold)
    double = both.n_bets < min(p_only.n_bets, q_only.n_bets)
    return PQGridReport(cells=cells, double_correction_detected=double)
