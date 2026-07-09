"""Operator CLI: train-evaluate (quickstart.md).

Flow: load eval races -> walk-forward evaluate the LightGBM predictor (per-fold retrain +
train-only calibration) -> fit a final serving predictor on the full history -> adoption gate
vs a stored baseline -> persist model_versions row + artifacts -> print a summary.
"""

from __future__ import annotations

import argparse
import datetime
import subprocess
import sys

from horseracing_db.models import ModelVersion
from horseracing_db.session import create_db_engine
from horseracing_eval.harness import evaluate
from horseracing_features.registry import FEATURE_GROUPS, FEATURE_VERSION
from sqlalchemy.orm import Session

from .adoption import AdoptionGate, evaluate_gate
from .artifacts import save_model_version
from .dataset import build_training_matrix  # noqa: F401  (re-exported convenience)
from .predictor import LightGBMPredictor


def _parse_date(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def _load_eval_races(session: Session):
    from horseracing_eval.dataset import load_eval_races

    return load_eval_races(session)


def train_evaluate(
    session: Session,
    *,
    first_valid_year: int,
    calibration: str,
    ece_threshold: float,
    baseline: str,
    model_version: str,
    artifacts_dir: str,
    seed: int = 42,
    hpo: bool = False,
    target_encode_cols: tuple[str, ...] = (),
    te_smoothing: float = 10.0,
    objective: str = "binary",
    use_materialized: bool = False,
    materialized_path: str | None = None,
) -> dict:
    eval_races = _load_eval_races(session)

    def _make() -> LightGBMPredictor:
        return LightGBMPredictor(
            session, seed=seed, calibration=calibration,
            hpo=hpo, target_encode_cols=target_encode_cols, te_smoothing=te_smoothing,
            objective=objective,
            use_materialized=use_materialized, materialized_path=materialized_path,
        )

    predictor = _make()
    result = evaluate(predictor, eval_races, first_valid_year=first_valid_year)

    # final serving model: fit on the full available history
    final = _make()
    final.fit([er.context for er in eval_races])

    baseline_row = session.get(ModelVersion, baseline)
    if baseline_row is None or baseline_row.metrics_summary is None:
        raise SystemExit(
            f"baseline '{baseline}' not found in model_versions; run the eval baseline first"
        )
    gate = AdoptionGate(ece_threshold=ece_threshold)
    decision = evaluate_gate(result.to_summary(), baseline_row.metrics_summary, gate)

    save_model_version(
        session,
        model_version=model_version,
        predictor=final,
        eval_result=result,
        decision=decision,
        gate=gate,
        artifacts_root=artifacts_dir,
        feature_version=FEATURE_VERSION,
        git_sha=_git_sha(),
    )

    overall = result.to_summary()["eval"]["overall"]
    return {
        "valid_years": result.valid_years,
        "overall": overall,
        "adopted": decision.adopted,
        "reasons": decision.reasons,
        "model_version": model_version,
    }


def _print_summary(summary: dict) -> None:
    print(f"model_version={summary['model_version']} valid_years={summary['valid_years']}")
    for label in ("win", "top2", "top3"):
        m = summary["overall"].get(label, {})
        print(
            f"  {label}: log_loss={m.get('log_loss'):.5f} ece={m.get('ece'):.5f} "
            f"brier={m.get('brier')}"
        )
    print(f"adopted={'active' if summary['adopted'] else 'candidate'}")
    for name, r in summary["reasons"].items():
        print(f"  - {name}: {'PASS' if r['pass'] else 'FAIL'} {r}")


def _add_window(p) -> None:
    import datetime as _dt

    p.add_argument("--from", dest="from_", type=_dt.date.fromisoformat, default=None,
                   help="start race_date (YYYY-MM-DD)")
    p.add_argument("--to", dest="to", type=_dt.date.fromisoformat, default=None,
                   help="end race_date (YYYY-MM-DD)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--database-url", default=None)


def _group_columns() -> dict[str, list[str]]:
    """FEATURE_GROUPS maps column -> group; invert to group -> [columns] for ablation."""
    groups: dict[str, list[str]] = {}
    for col, grp in FEATURE_GROUPS.items():
        groups.setdefault(grp, []).append(col)
    return groups


def _run_feature_command(session: Session, args) -> int:
    if args.command == "feature-eval":
        from horseracing_eval.feature_eval import evaluate_feature_adoption

        # baseline = candidate MINUS --drop-groups. Default = Feature 030 groups, so baseline is
        # features-007 and feature-eval measures 030's marginal value. --candidate-drop-groups also
        # drops from the CANDIDATE (Feature 030 per-group protocol: candidate=features-007+g via
        # candidate-drop = all-030-except-g, baseline-drop = all-030).
        gcols = _group_columns()
        # Feature 056: default baseline drops the four raw-column groups (baseline=features-012,
        # candidate=full features-013). Prior groups reachable via explicit --drop-groups.
        _DEF_056 = "pace_first3f,owner_breeder,race_level,sire_line"
        drop_groups = (args.drop_groups or _DEF_056).split(",")
        cand_drop_groups = (args.candidate_drop_groups.split(",")
                            if args.candidate_drop_groups else [])
        drop = tuple(c for g in drop_groups for c in gcols.get(g, []))
        cand_drop = tuple(c for g in cand_drop_groups for c in gcols.get(g, []))
        candidate = LightGBMPredictor(session, seed=args.seed, drop_features=cand_drop)
        baseline = LightGBMPredictor(session, seed=args.seed, drop_features=drop)
        r = evaluate_feature_adoption(
            session, candidate=candidate, baseline=baseline,
            ece_tol=args.ece_tol, worst_fold_ece_tol=args.worst_fold_ece_tol,
            start_date=args.from_, end_date=args.to,
        )
        print(f"feature-eval fv={FEATURE_VERSION} drop_groups={drop_groups} "
              f"cand_drop={cand_drop_groups or '-'} folds={r.n_folds} adopted={r.adopted}")
        print(f"  LogLoss base={r.mean_logloss_base:.5f} cand={r.mean_logloss_cand:.5f}")
        print(f"  Brier   base={r.mean_brier_base:.5f} cand={r.mean_brier_cand:.5f}")
        print(f"  AUC     base={r.mean_auc_base:.5f} cand={r.mean_auc_cand:.5f}")
        print(f"  ECE     base={r.mean_ece_base:.5f} cand={r.mean_ece_cand:.5f}")
        print(f"  winning_folds={r.n_winning_folds}/{r.n_folds} "
              f"worst_dLogLoss={r.worst_fold_dlogloss:+.5f} worst_dECE={r.worst_fold_dece:+.5f}")
        print(f"  primary_pass(LogLoss改善 かつ ECE非悪化)={r.primary_pass}  ADOPTED={r.adopted}")
        print("  ※ pseudo-ROI/Kelly は採用ゲートにしない（betting 側の SECONDARY 診断）")
        return 0
    if args.command == "feature-ablation":
        from horseracing_eval.ablation import evaluate_group_ablation

        all_groups = _group_columns()
        if args.groups:
            wanted = set(args.groups.split(","))
            all_groups = {g: c for g, c in all_groups.items() if g in wanted}
        def _make(drop, _s=session, _seed=args.seed):
            return LightGBMPredictor(_s, seed=_seed, drop_features=drop)

        r = evaluate_group_ablation(
            session, make_predictor=_make,
            groups=all_groups, start_date=args.from_, end_date=args.to,
        )
        print(f"feature-ablation full_logloss={r.full_logloss:.5f} (正=その group を抜くと悪化)")
        for grp, c in sorted(r.group_contribution.items()):
            print(f"  {grp:<14} contribution={c:+.5f}")
        return 0
    if args.command == "feature-diagnostic":
        from horseracing_eval.market_edge import evaluate_market_edge

        r = evaluate_market_edge(
            session, predictor=LightGBMPredictor(session, seed=args.seed),
            start_date=args.from_, end_date=args.to,
        )
        print(f"feature-diagnostic n={r.n_horses}  {r.note}")
        print(f"  summary={r.summary}")
        print(f"  pq_logloss={r.pq_logloss}")
        for b in r.edge_buckets:
            print(f"  edge[{b['edge_lo']:+.2f},{b['edge_hi']:+.2f}) n={b['n']} "
                  f"win_rate={b['win_rate']:.4f} mean_edge={b['mean_edge']:+.4f}")
        return 0
    if args.command == "segment-diagnostic":
        from horseracing_eval.segment_edge import evaluate_segment_edge

        r = evaluate_segment_edge(
            session, predictor=LightGBMPredictor(session, seed=args.seed),
            start_date=args.from_, end_date=args.to,
        )
        print(f"segment-diagnostic n={r.n_horses}  {r.note}")
        print(f"  {'axis':<12} {'segment':<16} {'n':>8} {'win%':>7} "
              f"{'LL(p)':>8} {'LL(q)':>8} {'gap':>8} {'mean_p':>7} {'mean_q':>7}")
        for row in r.rows:
            print(f"  {row.axis:<12} {row.segment:<16} {row.n:>8} {row.win_rate:>7.4f} "
                  f"{row.logloss_p:>8.5f} {row.logloss_q:>8.5f} {row.gap:>+8.5f} "
                  f"{row.mean_p:>7.4f} {row.mean_q:>7.4f}")
        if getattr(args, "persist", False):
            # Feature 054: append the run to diagnostic_runs (verbatim transcription) so the
            # admin console can display it (021 discipline). Display output above is unchanged.
            from horseracing_eval.diagnostics_store import save_segment_edge_run
            lv = (f"diag=segment_edge;axes=047-preregistered;from={args.from_};to={args.to};"
                  f"seed={args.seed};v=diag-0.1.0")
            run = save_segment_edge_run(
                session, r, date_from=args.from_, date_to=args.to, logic_version=lv,
            )
            session.commit()
            print(f"  persisted: diagnostic_run={run.diagnostic_run_id} (kind=segment_edge)")
        return 0
    if args.command == "stage-discount-eval":
        # Feature 049: derivation-layer A/B. Uses the PRODUCTION predictor config (pl_topk +
        # OOF-TE + isotonic) so top2/top3 reflect the real lgbm-042 serving derivation; win is
        # identical across baseline/candidate by construction (only the tail is discounted).
        from horseracing_eval.dataset import load_eval_races
        from horseracing_eval.stage_discount_eval import evaluate_stage_discount

        te_cols = tuple(c for c in (args.target_encode or "").split(",") if c)
        predictor = LightGBMPredictor(
            session, seed=args.seed, target_encode_cols=te_cols,
            te_smoothing=args.te_smoothing, calibration=args.calibration,
            objective=args.objective,
        )
        eval_races = load_eval_races(session, start_date=args.from_, end_date=args.to)
        r = evaluate_stage_discount(
            predictor, eval_races, first_valid_year=args.first_valid_year,
            min_races=args.min_races,
        )
        print(f"stage-discount-eval objective={args.objective} calib={args.calibration} "
              f"target_encode={list(te_cols)}")
        print(r.summary())
        print("  fold λ̂ (from prior OOS):")
        for fl in r.fold_lambdas:
            print(f"    {fl['valid_year']}: l2={fl['lambda2']:.4f} l3={fl['lambda3']:.4f} "
                  f"n_fit={fl['n_fit']} fallback={fl['fallback']}")
        print(f"  ADOPTED={r.adopted} (primary={r.primary_pass} guard={r.guard_pass} "
              f"win_identical={r.win_identical})")
        return 0
    if args.command == "model-eval":
        # Feature 036: modeling change (OOF target encoding) — NOT a feature-group change, so the
        # candidate has the SAME feature columns as the baseline (FEATURE_VERSION unchanged); it
        # differs only by an internal OOF-TE transform of high-cardinality categoricals.
        from horseracing_eval.feature_eval import evaluate_feature_adoption

        te_cols = tuple(c for c in (args.target_encode or "").split(",") if c)
        objective = getattr(args, "objective", "binary")
        # Feature 055: materialized reads are a pure input-path swap (bit-parity) — safe for both
        # sides of the A/B (identical matrices either way).
        mat = dict(
            use_materialized=args.use_materialized,
            materialized_path=args.materialized_path if args.use_materialized else None,
        )
        candidate = LightGBMPredictor(
            session, seed=args.seed, target_encode_cols=te_cols,
            te_smoothing=args.te_smoothing, calibration=args.calibration,
            objective=objective, **mat,
        )
        # baseline = current production shape (binary). Feature 039 candidate = cond_logit.
        baseline = LightGBMPredictor(session, seed=args.seed, calibration=args.calibration, **mat)
        r = evaluate_feature_adoption(
            session, candidate=candidate, baseline=baseline,
            ece_tol=args.ece_tol, worst_fold_ece_tol=args.worst_fold_ece_tol,
            start_date=args.from_, end_date=args.to,
        )
        print(f"model-eval fv={FEATURE_VERSION} objective={objective} "
              f"target_encode={list(te_cols)} calib={args.calibration} "
              f"folds={r.n_folds} adopted={r.adopted}")
        print(f"  LogLoss base={r.mean_logloss_base:.5f} cand={r.mean_logloss_cand:.5f}")
        print(f"  Brier   base={r.mean_brier_base:.5f} cand={r.mean_brier_cand:.5f}")
        print(f"  AUC     base={r.mean_auc_base:.5f} cand={r.mean_auc_cand:.5f}")
        print(f"  ECE     base={r.mean_ece_base:.5f} cand={r.mean_ece_cand:.5f}")
        print(f"  winning_folds={r.n_winning_folds}/{r.n_folds} "
              f"worst_dLogLoss={r.worst_fold_dlogloss:+.5f} worst_dECE={r.worst_fold_dece:+.5f}")
        print(f"  primary_pass(LogLoss改善 かつ ECE非悪化)={r.primary_pass}  ADOPTED={r.adopted}")
        return 0
    return 1


def _market_gate_eval(session: Session, args) -> int:
    """Feature 060: 3-way pre-registered gate (candidate vs market-q vs acc) on the
    odds-restricted population. --tail-folds N = spike mode (FR-009 go/no-go)."""
    import json
    from pathlib import Path

    from .market_gate import market_gate_eval

    te_cols = tuple(c for c in (args.target_encode or "").split(",") if c)
    report = market_gate_eval(
        session,
        seed=args.seed,
        calibration=args.calibration,
        target_encode_cols=te_cols,
        te_smoothing=args.te_smoothing,
        first_valid_year=args.first_valid_year,
        tail_folds=args.tail_folds,
        use_materialized=args.use_materialized,
        materialized_path=args.materialized_path if args.use_materialized else None,
    )
    cov = report["coverage"]
    print(f"market-gate-eval mode={report['mode']} first_valid_year={report['first_valid_year']}")
    print(f"  coverage: kept={cov['n_kept_races']}/{cov['n_total_races']} "
          f"excluded={cov['n_excluded_races']} by_year={cov['excluded_by_year']}")
    for name in ("market", "acc", "candidate"):
        m = report["overall"][name]
        print(f"  {name:9s} win={m['win']['log_loss']:.5f} top2={m['top2']['log_loss']:.5f} "
              f"top3={m['top3']['log_loss']:.5f} win_ece={m['win']['ece']}")
    for g, ok in report["gates"].items():
        print(f"  gate {g}: {'PASS' if ok else 'FAIL'}")
    print(f"  ALL_GATES_PASS={report['all_gates_pass']}")
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
        print(f"  report written to {args.out}")
    return 0


def _register_market_model(session: Session, args) -> int:
    """Feature 060 T013: train the final market-offset model and register it as CANDIDATE.

    The walk-forward metrics come from the market-gate-eval JSON report (no re-evaluation);
    the training CONFIG is read from the same report so the registered model can never
    diverge from the gate-evaluated configuration. Registration requires all_gates_pass
    unless --allow-gate-fail (an explicit, recorded user decision — 023/039 precedent)."""
    import json
    from pathlib import Path

    from horseracing_eval.dataset import load_eval_races
    from horseracing_eval.harness import EvalResult

    from .adoption import AdoptionDecision
    from .market_gate import restrict_to_full_odds

    report = json.loads(Path(args.gate_report).read_text())
    if report.get("mode") != "full":
        print("gate report is not a FULL run (spike reports cannot register)", file=sys.stderr)
        return 1
    override = False
    if not report.get("all_gates_pass"):
        if not args.allow_gate_fail:
            print("gates not all passed; refusing to register "
                  "(--allow-gate-fail records an explicit user-decision override)",
                  file=sys.stderr)
            return 1
        override = True

    summ = report["eval_summaries"]["candidate"]
    eval_result = EvalResult(
        scheme=summ["scheme"], valid_years=summ["valid_years"], tolerance=summ["tolerance"],
        ece_bins=summ["ece_bins"], overall=summ["overall"], by_fold=summ["by_fold"],
        by_field_size_ece=summ["by_field_size_ece"], reliability=summ.get("reliability", {}),
    )
    cfg = report["config"]
    final = LightGBMPredictor(
        session, seed=int(cfg["seed"]), calibration=cfg["calibration"],
        objective=cfg["objective"], target_encode_cols=tuple(cfg["target_encode_cols"]),
        market_offset=True,
    )
    eval_races = load_eval_races(session)
    kept, coverage = restrict_to_full_odds(eval_races)
    final.fit([er.context for er in kept])

    decision = AdoptionDecision(
        adopted=False,  # accuracy-first model: never active via this path (FR-006)
        reasons={
            "market_gates": report["gates"],
            "all_gates_pass": report["all_gates_pass"],
            "gate_report": str(args.gate_report),
            "user_override": override,
            "registration_coverage": coverage,
        },
    )
    art_dir = save_model_version(
        session,
        model_version=args.model_version,
        predictor=final,
        eval_result=eval_result,
        decision=decision,
        gate=AdoptionGate(ece_threshold=0.0),  # unused for 060; market gates live in reasons
        artifacts_root=args.artifacts_dir,
        feature_version=FEATURE_VERSION,
        git_sha=_git_sha(),
        register_as_candidate=True,
    )
    print(f"registered {args.model_version} as CANDIDATE (never auto-active) at {art_dir}")
    print(f"  gates={report['gates']} all_pass={report['all_gates_pass']} override={override}")
    return 0


def _set_model_label(session: Session, args) -> int:
    """Feature 057: write display_name/purpose on a model_versions row (display-only metadata).

    Omitted arg (None) leaves the field unchanged; explicit empty string clears it to NULL. Never
    mutates adoption_status (用途設定 ≠ 昇格, FR-009). Idempotent overwrite."""
    mv = session.get(ModelVersion, args.model_version)
    if mv is None:
        print(f"model_version not found: {args.model_version}", file=sys.stderr)
        return 1
    if args.display_name is not None:
        mv.display_name = args.display_name or None  # "" → NULL
    if args.purpose is not None:
        mv.purpose = args.purpose or None
    session.commit()
    print(f"updated {mv.model_version}: display_name={mv.display_name!r} purpose={mv.purpose!r}")
    return 0


def _policy_gate_eval(session: Session, args) -> int:
    """Feature 064: walk-forward betting-policy adoption gate. Collects genuine OOS per-horse rows
    (each fold fit on strictly-prior years, predict the valid year) with CLOSING odds + result, then
    hands them to the PURE eval scorer (evaluate_policy_gate): current EV vs odds-cap policy (plus
    favorite/uniform/no-bet baselines). cap is a FIXED pre-registered arg."""
    from horseracing_eval.dataset import load_eval_races
    from horseracing_eval.policy_gate import evaluate_policy_gate
    from horseracing_eval.splits import expanding_folds
    from sqlalchemy import text

    te_cols = tuple(c for c in (args.target_encode or "").split(",") if c)
    predictor = LightGBMPredictor(
        session, seed=args.seed, calibration=args.calibration,
        target_encode_cols=te_cols, te_smoothing=args.te_smoothing, objective=args.objective,
    )
    races = load_eval_races(session, start_date=args.from_, end_date=args.to)
    jump = set() if args.include_jump else {
        r[0] for r in session.execute(text(
            "SELECT race_id FROM races WHERE track_type='障' OR race_name LIKE '%障害%'"))
    }
    rows: list[dict] = []
    for fold in expanding_folds(races, args.first_valid_year):
        predictor.fit([er.context for er in fold.train])
        for er in fold.valid:
            if er.context.race_id in jump:
                continue
            preds = predictor.predict_race(er.context)
            winners = {sl.horse_id for sl in er.labels if sl.win == 1}
            for h in er.context.started_horses:
                o = h.result_market.odds
                pr = preds.get(h.horse_id)
                if pr is None or o is None or o <= 0:
                    continue
                rows.append({
                    "race_id": er.context.race_id, "year": er.context.race_date.year,
                    "p": float(pr.win), "odds": float(o),
                    "won": 1 if h.horse_id in winners else 0,
                })
    rep = evaluate_policy_gate(rows, cap=args.cap, threshold=args.threshold)
    print(f"policy-gate-eval objective={args.objective} cap={args.cap} thr={args.threshold} "
          f"rows={rep.n_rows} races={rep.n_races} folds={rep.n_folds}")
    for name, r in rep.policies.items():
        ref = " (=×1.00 no-loss ref)" if name == "no_bet" else ""
        print(f"  {name:14s} n_bets={r.n_bets:7d} hit={r.hit_rate:6.4f} "
              f"recovery={r.recovery:.4f}{ref}")
    print("  by fold (year: ev → cap, Δ):")
    for f in rep.by_fold:
        print(f"    {f['year']}: {f['ev']:.4f} → {f['cap']:.4f}  Δ={f['delta']:+.4f}")
    print("  ev recovery by odds band:")
    for b in rep.by_odds_band:
        print(f"    {b['band']:>6s}: n={b['n']:6d} recovery={b['ev_recovery']:.4f}")
    print(f"  folds_improved={rep.n_folds_improved}/{rep.n_folds} "
          f"worst_fold_delta={rep.worst_fold_delta:+.4f}")
    print(f"  ADOPTED={rep.adopted}  (relative recovery↑ + majority folds↑ + worst fold ≥ −tol)")
    print(f"  NOTE: {rep.note}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="horseracing_training")
    sub = parser.add_subparsers(dest="command", required=True)

    te = sub.add_parser("train-evaluate", help="walk-forward train + calibrate + adopt + save")
    te.add_argument("--first-valid-year", type=int, default=2008)
    te.add_argument("--calibration", choices=["platt", "isotonic", "none"], default="platt")
    te.add_argument("--objective", choices=["binary", "cond_logit", "pl_topk"],
                    default="binary",
                    help="039/042: win objective (binary | cond_logit | pl_topk=PL top-3)")
    te.add_argument("--ece-threshold", type=float, default=0.05)
    te.add_argument("--baseline", default="uniform")
    te.add_argument("--model-version", default="lightgbm-win-v1")
    te.add_argument("--artifacts-dir", default="artifacts")
    te.add_argument("--seed", type=int, default=42)
    te.add_argument(
        "--hpo", action="store_true", help="US4: train-internal CV hyperparameter search"
    )
    te.add_argument(
        "--target-encode",
        nargs="?",
        const="jockey_id,trainer_id,venue_code",
        default="",
        help="US4: OOF target-encode these columns (comma-separated; bare flag uses defaults)",
    )
    te.add_argument("--te-smoothing", type=float, default=10.0,
                    help="Feature 036: OOF TE smoothing (shrinkage toward prior)")
    te.add_argument("--use-materialized", action="store_true",
                    help="055: read as-of features from the 025 parquet (bit-parity, fail-closed)")
    te.add_argument("--materialized-path", default="../artifacts/features.parquet")
    te.add_argument("--database-url", default=None)

    # Feature 020 — walk-forward adoption gate / ablation / market diagnostic.
    # eval is predictor-agnostic; we inject the concrete LightGBMPredictor + FEATURE_GROUPS here.
    fe = sub.add_parser("feature-eval", help="candidate vs baseline (groups-under-test dropped)")
    _add_window(fe)
    fe.add_argument("--ece-tol", type=float, default=1e-3, help="mean ECE non-degradation tol")
    fe.add_argument("--worst-fold-ece-tol", type=float, default=2e-3,
                    help="looser per-fold worst ECE tol (single-fold blip should not veto)")
    fe.add_argument("--drop-groups", default=None,
                    help="comma-separated groups the BASELINE drops (default: 030 groups → "
                         "baseline=features-007)")
    fe.add_argument("--candidate-drop-groups", dest="candidate_drop_groups", default=None,
                    help="comma-separated groups the CANDIDATE drops too (030 per-group: set to "
                         "all-030-except-g so candidate=features-007+g)")
    fa = sub.add_parser("feature-ablation", help="020: per-group LogLoss contribution (diagnostic)")
    _add_window(fa)
    fa.add_argument("--groups", default=None, help="comma-separated group subset (default: all)")
    fd = sub.add_parser("feature-diagnostic", help="020: market p−q edge diagnostic (SECONDARY)")
    _add_window(fd)
    sd = sub.add_parser("segment-diagnostic",
                        help="047: segment-wise p vs q diagnostic (SECONDARY, pre-registered)")
    _add_window(sd)
    sd.add_argument("--persist", action="store_true",
                    help="054: append the result to diagnostic_runs for the admin console")

    # Feature 036: OOF target encoding (modeling change; same feature columns as baseline).
    me = sub.add_parser("model-eval", help="036: OOF target-encode candidate vs no-TE baseline")
    _add_window(me)
    me.add_argument("--ece-tol", type=float, default=1e-3)
    me.add_argument("--worst-fold-ece-tol", type=float, default=2e-3)
    me.add_argument("--target-encode", default="jockey_id,trainer_id",
                    help="comma-separated high-cardinality columns to OOF target-encode")
    me.add_argument("--te-smoothing", type=float, default=10.0,
                    help="TE smoothing (higher = more shrinkage toward prior = less overconfident)")
    me.add_argument("--calibration", choices=["platt", "isotonic", "none"], default="platt")
    me.add_argument("--objective", choices=["binary", "cond_logit", "pl_topk"],
                    default="binary",
                    help="039/042: candidate win objective (baseline stays binary)")
    me.add_argument("--use-materialized", action="store_true",
                    help="055: read as-of features from the 025 parquet (bit-parity, fail-closed)")
    me.add_argument("--materialized-path", default="../artifacts/features.parquet")

    # Feature 049: stage-discount A/B (derivation layer). Production predictor config defaults.
    sde = sub.add_parser("stage-discount-eval",
                         help="049: top2/top3 stage-discount A/B (λ=1 vs walk-forward λ̂)")
    _add_window(sde)
    sde.add_argument("--first-valid-year", type=int, default=2008)
    sde.add_argument("--min-races", type=int, default=300,
                     help="min prior-OOS races to fit a non-identity λ (else identity fallback)")
    sde.add_argument("--objective", choices=["binary", "cond_logit", "pl_topk"],
                     default="pl_topk", help="production win objective (default pl_topk=lgbm-042)")
    sde.add_argument("--calibration", choices=["platt", "isotonic", "none"], default="isotonic")
    sde.add_argument("--target-encode", default="jockey_id,trainer_id",
                     help="OOF target-encode columns (production default)")
    sde.add_argument("--te-smoothing", type=float, default=10.0)

    # Feature 060: market-residual model — pre-registered 3-way gate on the odds-restricted
    # population. --tail-folds = spike (go/no-go before full implementation, FR-009).
    mge = sub.add_parser("market-gate-eval",
                         help="060: candidate(pl_topk+offset) vs market-q vs acc gate eval")
    mge.add_argument("--first-valid-year", type=int, default=2008)
    mge.add_argument("--tail-folds", type=int, default=None,
                     help="spike mode: evaluate only the last N year-folds (train still expands)")
    mge.add_argument("--calibration", choices=["platt", "isotonic", "none"], default="isotonic")
    mge.add_argument("--target-encode", default="jockey_id,trainer_id",
                     help="OOF target-encode columns (production default)")
    mge.add_argument("--te-smoothing", type=float, default=10.0)
    mge.add_argument("--seed", type=int, default=42)
    mge.add_argument("--out", default=None, help="write the full JSON report to this path")
    mge.add_argument("--use-materialized", action="store_true",
                     help="055: read as-of features from the 025 parquet (bit-parity, fail-closed)")
    mge.add_argument("--materialized-path", default="../artifacts/features.parquet")
    mge.add_argument("--database-url", default=None)

    # Feature 060: register the market-offset model as CANDIDATE from a full gate report.
    rmm = sub.add_parser("register-market-model",
                         help="060: train final market-offset model + register as candidate")
    rmm.add_argument("--gate-report", required=True,
                     help="JSON written by market-gate-eval --out (must be a FULL run)")
    rmm.add_argument("--model-version", default="lgbm-060-mkt")
    rmm.add_argument("--artifacts-dir", default="artifacts")
    rmm.add_argument("--allow-gate-fail", action="store_true",
                     help="explicit user-decision override when gates did not all pass")
    rmm.add_argument("--database-url", default=None)

    # Feature 064: walk-forward betting-policy adoption gate (current EV vs odds-cap).
    pge = sub.add_parser("policy-gate-eval",
                         help="064: walk-forward current-EV vs odds-cap betting policy comparison")
    pge.add_argument("--from", dest="from_", type=_parse_date, default=None)
    pge.add_argument("--to", type=_parse_date, default=None)
    pge.add_argument("--first-valid-year", type=int, default=2008)
    pge.add_argument("--cap", type=float, default=21.0,
                     help="PRE-REGISTERED win odds cap (fixed; never chosen from results)")
    pge.add_argument("--threshold", type=float, default=1.0)
    pge.add_argument("--objective", choices=["binary", "cond_logit", "pl_topk"], default="binary",
                     help="binary = fast proxy; pl_topk = production-faithful (long job)")
    pge.add_argument("--calibration", choices=["platt", "isotonic", "none"], default="isotonic")
    pge.add_argument("--target-encode", default="jockey_id,trainer_id")
    pge.add_argument("--te-smoothing", type=float, default=20.0)
    pge.add_argument("--include-jump", action="store_true",
                     help="include mis-labelled jump races (default: excluded)")
    pge.add_argument("--seed", type=int, default=42)
    pge.add_argument("--database-url", default=None)

    # Feature 057: set human-readable purpose metadata on a model (display-only; NOT adoption).
    # Omitted arg = leave unchanged; empty string = clear to NULL. Never touches adoption_status.
    sml = sub.add_parser("set-model-label",
                         help="057: set display_name/purpose on a model (omit=keep, ''=clear)")
    sml.add_argument("--model-version", required=True)
    sml.add_argument("--display-name", default=None,
                     help="human name; omit to keep current, pass '' to clear to NULL")
    sml.add_argument("--purpose", default=None,
                     help="purpose note; omit to keep current, pass '' to clear to NULL")
    sml.add_argument("--database-url", default=None)

    args = parser.parse_args(argv)
    if args.command == "market-gate-eval":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _market_gate_eval(session, args)
    if args.command == "policy-gate-eval":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _policy_gate_eval(session, args)
    if args.command == "register-market-model":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _register_market_model(session, args)
    if args.command == "set-model-label":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _set_model_label(session, args)
    if args.command in ("feature-eval", "feature-ablation", "feature-diagnostic",
                        "segment-diagnostic", "model-eval", "stage-discount-eval"):
        engine = create_db_engine(getattr(args, "database_url", None))
        with Session(engine) as session:
            return _run_feature_command(session, args)
    if args.command == "train-evaluate":
        engine = create_db_engine(args.database_url)
        te_cols = tuple(c for c in args.target_encode.split(",") if c)
        with Session(engine) as session:
            summary = train_evaluate(
                session,
                first_valid_year=args.first_valid_year,
                calibration=args.calibration,
                ece_threshold=args.ece_threshold,
                baseline=args.baseline,
                model_version=args.model_version,
                artifacts_dir=args.artifacts_dir,
                seed=args.seed,
                hpo=args.hpo,
                target_encode_cols=te_cols,
                te_smoothing=args.te_smoothing,
                objective=args.objective,
                use_materialized=args.use_materialized,
                materialized_path=args.materialized_path if args.use_materialized else None,
            )
        _print_summary(summary)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
