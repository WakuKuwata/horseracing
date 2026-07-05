"""Operator CLI: train-evaluate (quickstart.md).

Flow: load eval races -> walk-forward evaluate the LightGBM predictor (per-fold retrain +
train-only calibration) -> fit a final serving predictor on the full history -> adoption gate
vs a stored baseline -> persist model_versions row + artifacts -> print a summary.
"""

from __future__ import annotations

import argparse
import subprocess

from horseracing_db.models import ModelVersion
from horseracing_db.session import create_db_engine
from horseracing_eval.harness import evaluate
from horseracing_features.registry import FEATURE_GROUPS, FEATURE_VERSION
from sqlalchemy.orm import Session

from .adoption import AdoptionGate, evaluate_gate
from .artifacts import save_model_version
from .dataset import build_training_matrix  # noqa: F401  (re-exported convenience)
from .predictor import LightGBMPredictor


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
        # Feature 041: default baseline drops the corner_trajectory group (baseline=features-011,
        # candidate=full features-012). Prior groups reachable via explicit --drop-groups.
        _DEF_041 = "corner_trajectory"
        drop_groups = (args.drop_groups or _DEF_041).split(",")
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

    args = parser.parse_args(argv)
    if args.command in ("feature-eval", "feature-ablation", "feature-diagnostic",
                        "segment-diagnostic", "model-eval"):
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
