"""Feature 060: pre-registered gate / spike driver for the market-residual model.

Compares THREE predictors on the SAME fold structure and the SAME restricted race set
(races where every started horse has a valid win odds — the only races the offset model
can train on or predict, INV-M4):

- candidate: pl_topk + TE + calibration + market offset (LightGBMPredictor market_offset=True)
- acc:       the same config WITHOUT the offset (= the lgbm-058-acc line, re-evaluated on
             the restricted population — its published numbers cover all races and are not
             directly comparable)
- market:    StrictMarketBaseline — q straight through the shared assemble path (clip ->
             renormalize -> Harville). The existing eval MarketBaseline floor-imputes
             missing odds, which contradicts INV-M4, hence this strict variant.

The eval harness (`horseracing_eval.harness.evaluate`) derives expanding year-folds from
the race list it is given, so passing the SAME restricted list to all three yields
identical folds and valid sets (research D5). Restricting the TRAIN side too is
intentional: the offset model cannot train on odds-less races, so all comparators get the
same information base.

Pre-registered gates (contracts/market-offset.md, fixed before results):
  (a) candidate win LogLoss < market win LogLoss          — MUST
  (b) candidate win LogLoss < acc win LogLoss             — MUST
  (c) candidate top2/top3 LogLoss <= market top2/top3     — MUST

Spike (T009): ``tail_folds`` limits evaluation to the last N year-folds (training still
expands over all prior years). Go = gate (a) on the spike window.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .calibration import DEFAULT_CLIP
from .market_offset import q_from_odds, valid_odds_mask
from .predictor import LightGBMPredictor, assemble_predictions


def race_has_full_odds(context) -> bool:
    """True iff every started horse carries a valid (finite, > 0) win odds."""
    odds = [
        h.result_market.odds if h.result_market is not None else None
        for h in context.started_horses
    ]
    return len(odds) > 0 and bool(valid_odds_mask(odds).all())


def restrict_to_full_odds(eval_races) -> tuple[list, dict]:
    """Filter to fully odds-covered races; report exclusions per year (research D4)."""
    kept, excluded_by_year = [], {}
    for er in eval_races:
        if race_has_full_odds(er.context):
            kept.append(er)
        else:
            y = er.context.race_date.year
            excluded_by_year[y] = excluded_by_year.get(y, 0) + 1
    report = {
        "n_total_races": len(eval_races),
        "n_kept_races": len(kept),
        "n_excluded_races": len(eval_races) - len(kept),
        "excluded_by_year": dict(sorted(excluded_by_year.items())),
    }
    return kept, report


class StrictMarketBaseline:
    """q-only baseline on the restricted population — NO missing-odds imputation.

    q = (1/odds)/Σ(1/odds) over started horses, then the exact shared assemble path
    (clip -> Σ=1 renormalize -> Harville top2/top3), so the comparison against the
    candidate differs only in the score, never in the derivation tail.
    """

    is_leaky_reference = True  # reads result-time odds (reference line)

    def fit(self, train_races) -> None:  # market baseline needs no fitting
        del train_races

    def predict_race(self, race):
        started = [h.horse_id for h in race.started_horses]
        odds = [h.result_market.odds for h in race.started_horses]
        q = q_from_odds(odds)  # raises on invalid odds — the driver must restrict first
        return assemble_predictions(started, q, eps=DEFAULT_CLIP)


def market_gate_eval(
    session: Session,
    *,
    seed: int = 42,
    calibration: str = "isotonic",
    target_encode_cols: tuple[str, ...] = ("jockey_id", "trainer_id"),
    te_smoothing: float = 10.0,
    first_valid_year: int | None = None,
    tail_folds: int | None = None,
    use_materialized: bool = False,
    materialized_path: str | None = None,
) -> dict:
    """Run the 3-way comparison and the pre-registered gate decision. Returns a report dict."""
    from horseracing_eval.dataset import load_eval_races
    from horseracing_eval.harness import evaluate
    from horseracing_eval.splits import FIRST_VALID_YEAR

    eval_races = load_eval_races(session)
    kept, coverage = restrict_to_full_odds(eval_races)
    if not kept:
        raise SystemExit("market-gate-eval: no fully odds-covered races")

    fvy = first_valid_year if first_valid_year is not None else FIRST_VALID_YEAR
    if tail_folds is not None:
        last_year = max(er.context.race_date.year for er in kept)
        fvy = max(fvy, last_year - tail_folds + 1)

    def _lgbm(market_offset: bool) -> LightGBMPredictor:
        return LightGBMPredictor(
            session, seed=seed, calibration=calibration, objective="pl_topk",
            target_encode_cols=target_encode_cols, te_smoothing=te_smoothing,
            market_offset=market_offset,
            use_materialized=use_materialized, materialized_path=materialized_path,
        )

    predictors = {
        "market": StrictMarketBaseline(),
        "acc": _lgbm(False),
        "candidate": _lgbm(True),
    }
    results = {}
    for name, pred in predictors.items():
        res = evaluate(pred, kept, first_valid_year=fvy)
        results[name] = res.to_summary()["eval"]

    def _ll(name: str, label: str) -> float:
        return float(results[name]["overall"][label]["log_loss"])

    gates = {
        "a_beats_market_win": _ll("candidate", "win") < _ll("market", "win"),
        "b_beats_acc_win": _ll("candidate", "win") < _ll("acc", "win"),
        "c_top2_top3_no_regression": (
            _ll("candidate", "top2") <= _ll("market", "top2")
            and _ll("candidate", "top3") <= _ll("market", "top3")
        ),
    }
    return {
        "mode": "spike" if tail_folds is not None else "full",
        "first_valid_year": fvy,
        "coverage": coverage,
        "config": {
            "seed": seed, "calibration": calibration,
            "target_encode_cols": list(target_encode_cols),
            "objective": "pl_topk", "market_offset_kind": "log_q_devig",
        },
        "overall": {
            name: {
                label: {
                    "log_loss": results[name]["overall"][label]["log_loss"],
                    "ece": results[name]["overall"][label].get("ece"),
                }
                for label in ("win", "top2", "top3")
            }
            for name in predictors
        },
        "by_fold": {name: results[name].get("by_fold") for name in predictors},
        # full EvalResult summaries (scheme/tolerance/by_field_size_ece/reliability included)
        # so registration (T013) can rebuild the candidate's metrics_summary WITHOUT re-running
        # the multi-hour walk-forward evaluation.
        "eval_summaries": results,
        "gates": gates,
        "all_gates_pass": all(gates.values()),
    }
