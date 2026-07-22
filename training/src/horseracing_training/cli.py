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
    drop_features: tuple[str, ...] = (),
    register_as_candidate: bool = False,
) -> dict:
    eval_races = _load_eval_races(session)

    def _make() -> LightGBMPredictor:
        return LightGBMPredictor(
            session, seed=seed, calibration=calibration,
            hpo=hpo, target_encode_cols=target_encode_cols, te_smoothing=te_smoothing,
            objective=objective, drop_features=drop_features,
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
        register_as_candidate=register_as_candidate,
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


def _ev_weight_gate_eval(session: Session, args) -> int:
    """Feature 079: the single pre-registered retrospective EV-weight kill-test. Generates (or
    reuses) the frozen OOF bundle, refits baseline (unweighted) vs candidate (EV-weighted) on
    identical folds, and scores the paired recovery gate. Artifact-only: writes evidence JSON,
    never a model_version row."""
    import dataclasses
    import json

    from .ev_weight_run import run_ev_weight_gate

    bundle_payload = None
    if getattr(args, "oof_bundle", None):
        from horseracing_probability.oof_bundle import read_bundle
        bundle_payload = read_bundle(args.oof_bundle)
    rep = run_ev_weight_gate(
        session,
        active_dir=args.active_dir,
        out_root=args.out_root,
        bundle_payload=bundle_payload,
        date_from=args.from_,
        date_to=args.to,
        first_valid_year=args.first_valid_year,
        include_jump=args.include_jump,
    )
    print(f"ev-weight-gate-eval verdict={rep.verdict} cap={rep.cap} thr={rep.threshold} "
          f"races={rep.n_races} days={rep.n_days}")
    print(f"  baseline : n_bets={rep.base.n_bets:7d} recovery={rep.base.recovery:.4f} "
          f"winner_nll={rep.base.winner_nll:.4f}")
    print(f"  candidate: n_bets={rep.cand.n_bets:7d} recovery={rep.cand.recovery:.4f} "
          f"winner_nll={rep.cand.winner_nll:.4f}")
    ci = f"[{rep.ci_low:.4f}, {rep.ci_high:.4f}]" if rep.ci_low is not None else "undefined"
    print(f"  Δrecovery={rep.delta:+.4f}  95%CI={ci}  folds_improved="
          f"{rep.n_folds_improved}/{rep.n_folds}  worst_fold={rep.worst_fold_delta:+.4f}")
    print(f"  MUST guards: winner_nll_ok={rep.winner_nll_ok}  tail_ok={rep.tail_ok}")
    print(f"  NOTE: {rep.note}")
    if getattr(args, "out_json", None):
        with open(args.out_json, "w") as fh:
            json.dump(dataclasses.asdict(rep), fh, indent=2, default=str)
        print(f"  evidence artifact -> {args.out_json}")
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
    te.add_argument("--register-candidate", action="store_true",
                    help="060/069: pin the saved row to CANDIDATE (non-active) even if the gate "
                         "passes — for accuracy-first models kept out of the default p⊥q model")
    te.add_argument("--drop-groups", dest="te_drop_groups", default="",
                    help="069: FEATURE_GROUPS to drop (expanded to columns), comma-separated")
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

    # Feature 079: the single pre-registered retrospective EV-weight kill-test (artifact-only).
    ewg = sub.add_parser("ev-weight-gate-eval",
                         help="079: paired baseline vs EV-weighted candidate recovery gate")
    ewg.add_argument("--active-dir", required=True,
                     help="base model dir (legacy attestation) for OOF + recipe-faithful arms")
    ewg.add_argument("--out-root", required=True,
                     help="content-addressed OOF-bundle output root")
    ewg.add_argument("--oof-bundle", default=None,
                     help="reuse a pre-generated OOF bundle path (skip the long generation step)")
    ewg.add_argument("--from", dest="from_", type=_parse_date, default=None)
    ewg.add_argument("--to", type=_parse_date, default=None)
    ewg.add_argument("--first-valid-year", type=int, default=2008)
    ewg.add_argument("--include-jump", action="store_true",
                     help="include mis-labelled jump races (default: excluded)")
    ewg.add_argument("--out-json", default=None, help="write the evidence report JSON here")
    ewg.add_argument("--database-url", default=None)

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

    # Feature 066: fit dispersion band boundaries (frozen-window entropy quintiles, results never
    # consulted) + optional SECONDARY OOS realized-chaos diagnostic (never an adoption gate).
    dbd = sub.add_parser("dispersion-bands",
                         help="066: fit荒れ度 band boundaries (frozen quintiles)")
    dbd.add_argument("--fit-from", dest="fit_from", type=_parse_date, required=True)
    dbd.add_argument("--fit-to", dest="fit_to", type=_parse_date, required=True)
    dbd.add_argument("--field-buckets", choices=["global"], default="global",
                     help="v1=global; per-field-size quintiles (v2) deferred")
    dbd.add_argument("--version", default="dispbands-v1", help="artifact/logic version token")
    dbd.add_argument("--out", default="artifacts/dispersion_bands/dispbands-v1.json",
                     help="write the boundary artifact JSON here")
    dbd.add_argument("--diagnose-from", dest="diagnose_from", type=_parse_date, default=None,
                     help="SECONDARY: OOS realized-chaos window start (must be after --fit-to)")
    dbd.add_argument("--diagnose-to", dest="diagnose_to", type=_parse_date, default=None)
    dbd.add_argument("--database-url", default=None)

    # Feature 066 model_delta: fit + write the FROZEN two_gamma p-calibrator artifact (048 machinery
    # reuse) for the read-time calibrated-p vs q delta. Display-only; never a model feature.
    dpc = sub.add_parser("dispersion-pcal",
                         help="066/076: inspect the manifest two_gamma the API uses "
                              "(--inspect-manifest); the legacy --from/--to FIT path is DEPRECATED "
                              "(non-OOS, superseded by manifest activation — 076 T021)")
    dpc.add_argument("--inspect-manifest", dest="inspect_manifest", default=None,
                     help="076: absolute path to a 074 manifest — verify it + print the two_gamma "
                          "γ the api dispersion path will apply (read-only; no fit, no write)")
    dpc.add_argument("--from", dest="fit_from", type=_parse_date, default=None,
                     help="DEPRECATED (066 legacy fit): fit-window start")
    dpc.add_argument("--to", dest="fit_to", type=_parse_date, default=None,
                     help="DEPRECATED (066 legacy fit): fit-window end")
    dpc.add_argument("--version", default="pcal-v1",
                     help="artifact/logic version token (legacy fit)")
    dpc.add_argument("--out", default="artifacts/dispersion_bands/pcal-v1.json",
                     help="legacy fit: write the p-calibrator artifact JSON here")
    dpc.add_argument("--database-url", default=None)

    # Feature 068: paired candidate↔active evaluation (recipe-refit per fold, no saved booster).
    pe = sub.add_parser("paired-eval",
                        help="068: paired candidate vs active winner-NLL eval + adoption gate")
    pe.add_argument("--candidate", default="pl_topk:isotonic",
                    help="recipe spec 'objective:calibration' (e.g. pl_topk:isotonic)")
    pe.add_argument("--active", default="pl_topk:none",
                    help="baseline recipe spec 'objective:calibration'")
    pe.add_argument("--from", dest="from_", type=_parse_date, default=None)
    pe.add_argument("--to", dest="to", type=_parse_date, default=None)
    pe.add_argument("--first-valid-year", type=int, default=2008)
    pe.add_argument("--seed", type=int, default=20260712)
    pe.add_argument("--bootstrap-b", type=int, default=2000)
    pe.add_argument("--num-threads", type=int, default=None)
    pe.add_argument("--gate-config", default=None, help="pre-registered gate-config.json path")
    pe.add_argument("--subgroups", action="store_true",
                    help="069: report 2026/nk/coverage subgroup CIs + intersection-union guard")
    pe.add_argument("--confirmatory", action="store_true",
                    help="073: fail closed if gate-config is missing/unknown-version or its hash "
                         "mismatches --gate-config-hash (confirmatory-mode contract)")
    pe.add_argument("--gate-config-hash", default=None,
                    help="073: expected canonical gate-config hash for --confirmatory")
    pe.add_argument("--compute-sensitivity", action="store_true",
                    help="073: also compute diagnostic block-width bootstrap sensitivities")
    pe.add_argument("--json", dest="json_out", default=None, help="write PairedReport JSON here")
    pe.add_argument("--database-url", default=None)

    # Feature 068 US2: A/B/C/D calibration-split driver (screening + confirmation, disjoint).
    cse = sub.add_parser("calib-split-eval",
                         help="068 US2: A/B/C/D calib-split screening + confirmation")
    cse.add_argument("--objective", default="pl_topk",
                     choices=["binary", "cond_logit", "pl_topk"])
    cse.add_argument("--screen-from", dest="screen_from", type=_parse_date, required=True)
    cse.add_argument("--screen-to", dest="screen_to", type=_parse_date, required=True)
    cse.add_argument("--confirm-from", dest="confirm_from", type=_parse_date, required=True)
    cse.add_argument("--confirm-to", dest="confirm_to", type=_parse_date, required=True)
    cse.add_argument("--seed", type=int, default=20260712)
    cse.add_argument("--bootstrap-b", type=int, default=1000)
    cse.add_argument("--num-threads", type=int, default=None)
    cse.add_argument("--gate-config", default=None)
    cse.add_argument("--json", dest="json_out", default=None)
    cse.add_argument("--database-url", default=None)

    # Feature 069 (SC-005): past-market coverage audit (year × ID source × obs bands). Read-only.
    ca = sub.add_parser("coverage-audit",
                        help="069: F02 past-market coverage by year × ID source (canonical/nk:)")
    ca.add_argument("--from", dest="from_", type=_parse_date, default=None)
    ca.add_argument("--to", dest="to", type=_parse_date, default=None)
    ca.add_argument("--json", dest="json_out", default=None)
    ca.add_argument("--database-url", default=None)

    # Feature 074 US1: generate a recipe-faithful OOF prediction bundle (content-addressed disk).
    og = sub.add_parser("oof-generate",
                        help="074: generate OOF prediction bundle from a base model recipe")
    og.add_argument("--base-model-version", default="lgbm-063")
    og.add_argument("--active-dir", default="artifacts/model_versions/lgbm-063",
                    help="directory holding the base model's metadata.json (+073 freeze)")
    og.add_argument("--from", dest="from_", type=_parse_date, default=None)
    og.add_argument("--to", dest="to", type=_parse_date, default=None)
    og.add_argument("--first-valid-year", type=int, default=2008)
    og.add_argument("--num-threads", type=int, default=1)
    og.add_argument("--out", default="artifacts/oof", help="artifacts/oof root")
    og.add_argument("--smoke", action="store_true", help="small-fold gate (implementability)")
    og.add_argument("--database-url", default=None)

    # Feature 074 US3: OOF-faithful two-gamma re-validation (calibrated-stage ECE + 048 verdict).
    co = sub.add_parser("calibrate-oof",
                        help="074: re-validate two-gamma calibration on an OOF bundle")
    co.add_argument("--bundle", required=True, help="path to OOF bundle dir or bundle.json")
    co.add_argument("--base-model-version", default="lgbm-063")
    co.add_argument("--gate-config",
                    default="specs/074-oof-faithful-calibration/gate-config.json")
    co.add_argument("--json", dest="json_out", default=None,
                    help="write the append-only evaluation artifact here")
    co.add_argument("--database-url", default=None)

    # Feature 074 US4: verify a content-addressed calibration manifest (fail-closed).
    vm = sub.add_parser("verify-manifest", help="074: verify a content-addressed calib manifest")
    vm.add_argument("--manifest", required=True, help="path to manifest.json")

    # Feature 078: generate a REAL OOF calibration manifest (v3) — first build_manifest caller.
    gm = sub.add_parser("generate-manifest",
                        help="078: generate a REAL OOF calibration manifest (two-gamma + stage-λ)")
    gm.add_argument("--bundle", required=True, help="path to OOF bundle dir or bundle.json")
    gm.add_argument("--model-dir", required=True,
                    help="lgbm-063 model dir (metadata.json) for the recipe attestation")
    gm.add_argument("--out-root", required=True, help="artifact root (manifests written under it)")
    gm.add_argument("--gate-config",
                    default="specs/074-oof-faithful-calibration/gate-config.json")
    gm.add_argument("--seed", type=int, default=0)
    gm.add_argument("--num-threads", dest="num_threads", type=int, default=1)
    gm.add_argument("--allow-dirty", action="store_true",
                    help="build a NON-production (fixture-scope) manifest at a dirty/unknown SHA")
    gm.add_argument("--database-url", default=None)

    args = parser.parse_args(argv)
    if args.command == "oof-generate":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _oof_generate(session, args)
    if args.command == "calibrate-oof":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _calibrate_oof(session, args)
    if args.command == "verify-manifest":
        return _verify_manifest_cmd(args)
    if args.command == "generate-manifest":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _generate_manifest(session, args)
    if args.command == "paired-eval":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _paired_eval(session, args)
    if args.command == "coverage-audit":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _coverage_audit(session, args)
    if args.command == "calib-split-eval":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _calib_split_eval(session, args)
    if args.command == "dispersion-bands":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _dispersion_bands(session, args)
    if args.command == "dispersion-pcal":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _dispersion_pcal(session, args)
    if args.command == "market-gate-eval":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _market_gate_eval(session, args)
    if args.command == "policy-gate-eval":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _policy_gate_eval(session, args)
    if args.command == "ev-weight-gate-eval":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _ev_weight_gate_eval(session, args)
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
        drop_groups = tuple(g for g in getattr(args, "te_drop_groups", "").split(",") if g)
        drop_cols = _expand_group_drops(drop_groups) if drop_groups else ()
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
                drop_features=drop_cols,
                register_as_candidate=getattr(args, "register_candidate", False),
            )
        _print_summary(summary)
        return 0
    return 1


def _expand_group_drops(group_names: tuple[str, ...]) -> tuple[str, ...]:
    """Feature 069 (codex F1): expand FEATURE_GROUPS names → their column names.

    ``drop_features`` is a tuple of COLUMN names (predictor filters ``c not in drop_features``);
    passing a bare GROUP name would drop nothing (fail-open → active arm == candidate, wrong F02
    verdict + p⊥q leak). So a group-drop is expanded to the group's columns here."""
    from horseracing_features.registry import FEATURE_GROUPS
    want = set(group_names)
    cols = tuple(c for c, g in FEATURE_GROUPS.items() if g in want)
    missing = want - {g for g in FEATURE_GROUPS.values()}
    if missing:
        raise ValueError(f"unknown feature group(s): {sorted(missing)}")
    return cols


def _recipe_from_spec(spec: str):
    """Parse 'objective:calibration[:calib_frac][:drop=g1,g2]' → ModelRecipe.

    The optional 3rd field is the calibration holdout fraction (068 A/B: 'pl_topk:isotonic:0.3').
    A trailing 'drop=<groups>' segment (069) drops those FEATURE_GROUPS (expanded to columns), e.g.
    active arm 'pl_topk:isotonic:0.3:drop=pm_core_strength' for the F02 paired-eval baseline.
    """
    from .calibration import DEFAULT_CALIB_FRAC
    from .recipe import ModelRecipe
    parts = spec.split(":")
    objective = parts[0]
    calibration = parts[1] if len(parts) > 1 else "isotonic"
    calib_frac = DEFAULT_CALIB_FRAC
    drop_features: tuple[str, ...] = ()
    for seg in parts[2:]:
        if seg.startswith("drop="):
            groups = tuple(g for g in seg[len("drop="):].split(",") if g)
            drop_features = _expand_group_drops(groups)
        elif seg:
            calib_frac = float(seg)
    return ModelRecipe(
        objective=objective, calibration=calibration, calib_frac=calib_frac,
        drop_features=drop_features, label=spec,
    )


def _factory_from_spec(session, spec: str):
    """Build the right PredictorFactory for a recipe spec.

    ``objective:oof_power`` → C/D arm (full-history booster + strict-past OOF power calibrator);
    anything else → A/B arm (train-internal calibration holdout via RecipeFactory)."""
    parts = spec.split(":")
    if len(parts) > 1 and parts[1] == "oof_power":
        from .calib_split import CalibSplitFactory
        from .recipe import ModelRecipe
        return CalibSplitFactory(
            session, ModelRecipe(objective=parts[0], calibration="none", label=spec)
        )
    from .recipe import RecipeFactory
    return RecipeFactory(session, _recipe_from_spec(spec))


def _oof_generate(session: Session, args) -> int:
    """Feature 074 US1: generate + publish a recipe-faithful OOF bundle (content-addressed disk)."""
    from .oof_generate import generate_oof_bundle

    first_valid = 2024 if getattr(args, "smoke", False) else args.first_valid_year
    path, payload = generate_oof_bundle(
        session,
        active_dir=args.active_dir,
        out_root=args.out,
        date_from=args.from_,
        date_to=args.to,
        first_valid_year=first_valid,
        num_threads=args.num_threads,
    )
    print(f"oof-generate base={args.base_model_version} smoke={getattr(args, 'smoke', False)}")
    print(f"  races={len(payload['predictions'])} folds={payload['fold_boundaries']}")
    print(f"  bundle_digest={payload.get('bundle_digest', '(stamped on write)')}")
    print(f"  wrote {path}")
    return 0


def _calibrate_oof(session: Session, args) -> int:
    """Feature 074 US3: OOF-faithful two-gamma re-validation → append-only evaluation artifact."""
    import json

    from horseracing_probability.oof_bundle import read_bundle
    from horseracing_probability.oof_calibration import calibrate_oof

    bundle = read_bundle(args.bundle)
    gate_cfg: dict = {}
    if args.gate_config:
        with open(args.gate_config) as fh:
            gate_cfg = json.load(fh)
    art = calibrate_oof(
        session, bundle, gate_config=gate_cfg, base_model_version=args.base_model_version
    )
    print(f"calibrate-oof stage={art['stage']} base={art['base_model_version']}")
    print(f"  ECE raw={art['ece']['raw']:.6f} calibrated={art['ece']['calibrated']:.6f} "
          f"delta={art['ece']['delta']:+.6f}")
    print(f"  transfer_ks={art['transfer_check']['ks']:.4f} n_days={art['n_eval_days']}")
    print(f"  VERDICT={art['verdict']} (cause={art['verdict_reason'].get('cause')}) "
          f"contract={art['evaluation_contract_version']}")
    if args.json_out:  # append-only evidence (073 verdicts are never rewritten)
        with open(args.json_out, "w") as fh:
            json.dump(art, fh, indent=2, default=str)
        print(f"  wrote {args.json_out}")
    return 0


def _generate_manifest(session: Session, args) -> int:
    """Feature 078 (T011): generate + publish a REAL OOF calibration manifest (v3). The FIRST
    production caller of build_manifest — orchestrates the two OOF verdicts + all-OOF deployment
    fits into a content-addressed, verifier-recomputed-eligibility manifest. Activates nothing."""
    import json

    from horseracing_probability.oof_bundle import read_bundle

    from .legacy_attest import attestation_from_model_dir
    from .oof_generate import code_sha
    from .oof_manifest import build_oof_manifest

    sha = code_sha()
    # D7: a dirty tree / unknown code SHA is not reproducible → refuse a production artifact.
    if not args.allow_dirty and ("dirty" in sha or sha == "unknown"):
        print(f"ERROR: refusing to build a production manifest at code_sha={sha!r} "
              f"(pass --allow-dirty to override for a NON-production build)")
        return 2
    bundle = read_bundle(args.bundle)
    attestation = attestation_from_model_dir(args.model_dir, code_sha=sha)
    gate_cfg: dict = {}
    if args.gate_config:
        with open(args.gate_config) as fh:
            gate_cfg = json.load(fh)
    scope = "fixture" if args.allow_dirty else "production"
    path, manifest = build_oof_manifest(
        session, bundle, attestation=attestation, code_sha=sha, out_root=args.out_root,
        seed=args.seed, num_threads=args.num_threads, gate_config=gate_cfg, artifact_scope=scope,
    )
    se = manifest["stages_evaluation"]
    print(f"generate-manifest schema_v={manifest['schema_version']} "
          f"scope={manifest['artifact_scope']}")
    print(f"  two_gamma verdict={se['two_gamma_win']['verdict']} "
          f"identity={se['two_gamma_win']['identity']}")
    print(f"  stage     verdict={se['stage_discount_topk']['verdict']} "
          f"identity={se['stage_discount_topk']['identity']}")
    print(f"  activation_eligible={manifest['activation_eligible']} "
          f"fit_through={manifest['fit_through']}")
    print(f"  manifest_digest={manifest['manifest_digest']}")
    print(f"  wrote {path}")
    return 0


def _verify_manifest_cmd(args) -> int:
    """Feature 074 US4: verify a content-addressed calibration manifest (fail-closed)."""
    from .calib_manifest import ManifestError, verify_manifest
    try:
        verify_manifest(args.manifest)
    except ManifestError as exc:
        print(f"verify-manifest FAIL: {exc}")
        return 1
    print(f"verify-manifest OK: {args.manifest}")
    return 0


def _paired_eval(session: Session, args) -> int:
    """Feature 068 (T018): build two RecipeFactory arms, run paired_eval, print + optional JSON.

    Both arms are re-fit per fold from their ModelRecipe (never a saved booster, codex C1) and
    scored on the same model-blind valid race set. eval owns the orchestration; the CLI only
    injects the training-side factories (020 boundary)."""
    import json

    from horseracing_eval.dataset import load_eval_races
    from horseracing_eval.paired import paired_eval

    gate_cfg = None
    if args.gate_config:
        with open(args.gate_config) as fh:
            gate_cfg = json.load(fh)

    # Feature 073 US1 (FR-002): confirmatory mode fails closed on a missing / wrong-version /
    # tampered gate-config BEFORE any eval runs.
    if getattr(args, "confirmatory", False):
        from horseracing_eval.decision import assert_confirmatory
        assert_confirmatory(gate_cfg, expected_hash=getattr(args, "gate_config_hash", None))

    eval_races = load_eval_races(session, start_date=args.from_, end_date=args.to)
    cand = _factory_from_spec(session, args.candidate)
    act = _factory_from_spec(session, args.active)
    report = paired_eval(
        cand, act, eval_races,
        gate_config=gate_cfg,
        first_valid_year=args.first_valid_year,
        bootstrap_seed=args.seed,
        bootstrap_b=args.bootstrap_b,
        num_threads=args.num_threads,
        snapshot={"git_sha": _git_sha(), "feature_version": FEATURE_VERSION,
                  "candidate_spec": args.candidate, "active_spec": args.active},
        subgroups=getattr(args, "subgroups", False),
        compute_sensitivity=getattr(args, "compute_sensitivity", False),
    )
    g = report.gate
    print(f"paired-eval candidate={args.candidate} active={args.active} "
          f"n_races={report.n_races} n_eligible={report.n_eligible}")
    _u = report.uniform_baseline_winner_nll
    print(f"  winner_nll: cand={report.periods['all']['candidate']:.6f} "
          f"active={report.periods['all']['active']:.6f} "
          f"diff={report.periods['all']['diff']:+.6f} (uniform={_u:.4f})")
    ci = report.bootstrap_ci
    print(f"  bootstrap CI(95%): [{ci['ci_low']}, {ci['ci_high']}] "
          f"point={ci['point']:+.6f} days={ci['n_days']} no_decision={ci['no_decision']}")
    print(f"  gate: primary={g.primary} stat_guard={g.stat_guard} recent={g.recent_guard} "
          f"top_ni={g.top_noninferior} calib={g.calibration} -> ADOPTED={g.adopted}")
    # Feature 073 US1 (FR-001): single machine-decided tri-value verdict (operator judgement=0).
    print(f"  DECISION={report.decision} "
          f"(cause={report.decision_reason.get('cause')}) "
          f"contract={report.evaluation_contract_version} gate_hash={report.gate_config_hash[:12]}")
    if report.subgroups:  # Feature 069 US1
        sg = report.subgroups
        for grain in ("race_subgroups", "horse_subgroups"):
            for lab, v in sg[grain].items():
                cci = v["bootstrap_ci"]
                print(f"  subgroup[{lab}]: decision={v['decision']} "
                      f"CI[{cci['ci_low']},{cci['ci_high']}] days={v['n_days']} "
                      f"cand_minus_uniform={v['cand_minus_uniform']}")
        print(f"  subgroup_guard(critical={sg['critical']}): {sg['subgroup_guard']} "
              f"decisions={sg['subgroup_decisions']}")
    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump(report.to_dict(), fh, indent=2, default=str)
        print(f"  wrote {args.json_out}")
    return 0


def _coverage_audit(session: Session, args) -> int:
    """Feature 069 SC-005 (D7): F02 past-market coverage + odds-provenance quality, by year × ID
    source (canonical / nk:). Read-only; NEVER flows to features (II). Surfaces the 2026 nk: ID
    gap so a low-coverage nk: horse is not mistaken for a market-less 新馬."""
    import json

    import numpy as np
    import pandas as pd
    from horseracing_features.loader import load_frames
    from horseracing_features.pm_core_strength import build_pm_core_strength_features

    frames = load_frames(session, end_date=args.to)
    feat = build_pm_core_strength_features(frames)
    races = frames.races[["race_id", "race_date"]].copy()
    races["year"] = races["race_date"].astype("datetime64[ns]").dt.year
    df = feat.merge(races, on="race_id", how="left")
    if args.from_ is not None:
        df = df[df["race_date"] >= np.datetime64(args.from_)]
    df["source"] = np.where(df["horse_id"].astype(str).str.startswith("nk:"), "nk", "canonical")

    report: dict = {}
    for (yr, src), grp in df.groupby(["year", "source"]):
        n = len(grp)
        oc = grp["asof_pm_obs_count"].to_numpy()
        report[f"{int(yr)}/{src}"] = {
            "started": int(n),
            "cov_ge1": round(float((oc >= 1).mean()), 4),
            "cov_ge3": round(float((oc >= 3).mean()), 4),
            "cov_ge5": round(float((oc >= 5).mean()), 4),
        }
    # odds-provenance quality (boundary values that gate complete-field q)
    odds = pd.to_numeric(frames.race_horses.get("odds"), errors="coerce")
    prov = {
        "odds_present": round(float(odds.notna().mean()), 4),
        "odds_eq_1_0": int((odds == 1.0).sum()),
        "odds_eq_999_9": int((odds == 999.9).sum()),
        "odds_le_0": int((odds <= 0).sum()),
    }
    print("coverage-audit (F02 past-market, year × ID source):")
    for k in sorted(report):
        v = report[k]
        print(f"  {k:14s} started={v['started']:6d} "
              f"cov>=1={v['cov_ge1']:.3f} cov>=3={v['cov_ge3']:.3f} cov>=5={v['cov_ge5']:.3f}")
    print(f"  provenance: {prov}")
    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump({"coverage": report, "provenance": prov}, fh, indent=2, default=str)
        print(f"  wrote {args.json_out}")
    return 0


def _calib_split_eval(session: Session, args) -> int:
    """Feature 068 US2 (T026/T027/T028): drive A/B/C/D screening + confirmation."""
    import json

    from .calib_split_eval import run_calib_split_eval

    gate_cfg = None
    if args.gate_config:
        with open(args.gate_config) as fh:
            gate_cfg = json.load(fh)

    report = run_calib_split_eval(
        session,
        make_factory=lambda spec: _factory_from_spec(session, spec),
        objective=args.objective,
        screen_window=(args.screen_from, args.screen_to),
        confirm_window=(args.confirm_from, args.confirm_to),
        gate_config=gate_cfg,
        seed=args.seed,
        bootstrap_b=args.bootstrap_b,
        num_threads=args.num_threads,
    )
    print(f"calib-split-eval objective={report.objective} ref={report.reference}")
    print(f"  screen={args.screen_from}..{args.screen_to} "
          f"confirm={args.confirm_from}..{args.confirm_to}")
    for a in report.arms:
        if a.name == report.reference:
            print(f"  {a.name:4s} [{a.spec}] = REFERENCE")
            continue
        sci = a.screen_ci or {}
        line = (f"  {a.name:4s} [{a.spec}] screen_diff={a.screen_diff:+.5f} "
                f"CI[{sci.get('ci_low')},{sci.get('ci_high')}] go={a.go} ({a.go_reason})")
        print(line)
        if a.confirm is not None:
            g = a.confirm.gate
            cci = a.confirm.bootstrap_ci
            print(f"       CONFIRM diff={a.confirm.periods['all']['diff']:+.5f} "
                  f"CI[{cci['ci_low']},{cci['ci_high']}] ADOPTED={g.adopted}")
    if args.json_out:
        import dataclasses
        with open(args.json_out, "w") as fh:
            json.dump(dataclasses.asdict(report), fh, indent=2, default=str)
        print(f"  wrote {args.json_out}")
    return 0


def _dispersion_bands(session: Session, args) -> int:
    """Feature 066: fit + write the band-boundary artifact. Results are NEVER consulted for the
    edges (Feature 047/048). Bands are a decision-support display readout — NOT an adoption gate."""
    from horseracing_eval.dispersion_bands import fit_boundary

    if args.fit_from > args.fit_to:
        print(f"error: --fit-from {args.fit_from} is after --fit-to {args.fit_to}", file=sys.stderr)
        return 2
    boundary = fit_boundary(
        session, fit_from=args.fit_from, fit_to=args.fit_to,
        field_buckets=args.field_buckets, version=args.version,
    )
    path = boundary.write(args.out)
    print(f"dispersion-bands: fit {boundary.n_races_fit} races "
          f"[{boundary.fit_from}..{boundary.fit_to}] metric={boundary.metric}")
    print(f"  quintile_edges = {[round(e, 4) for e in boundary.quintile_edges]}")
    print(f"  version={boundary.version}  -> {path}")
    print("  NOTE: bands are a SECONDARY decision-support readout, not an adoption gate (047).")

    if args.diagnose_from is not None and args.diagnose_to is not None:
        from horseracing_eval.dispersion_bands import diagnose_bands
        rows = diagnose_bands(session, boundary=boundary,
                              diagnose_from=args.diagnose_from, diagnose_to=args.diagnose_to)
        print(f"\nOOS realized-chaos diagnostic [{args.diagnose_from}..{args.diagnose_to}] "
              "(SECONDARY — NOT a gate):")
        hdr = f"  {'band':<14} {'n':>6} {'void':>5} {'fav_loss':>9} {'CI':>15}"
        print(hdr + f" {'high_payout':>11} {'sep':>4}")
        for r in rows:
            fl = f"{r.favorite_loss_rate:.3f}" if r.favorite_loss_rate is not None else "  -"
            ci = (f"[{r.ci_low:.2f},{r.ci_high:.2f}]"
                  if r.ci_low is not None else "  -")
            hp = f"{r.high_payout_rate:.3f}" if r.high_payout_rate is not None else "  -"
            sep = ("" if r.separated_from_prev is None
                   else ("yes" if r.separated_from_prev else "NO"))
            print(f"  {r.band:<14} {r.n:>6} {r.n_void:>5} {fl:>9} {ci:>15}"
                  f" {hp:>11} {sep:>4}")
        print("  'sep=NO' = adjacent bands not separated by CI — disclosed, not merged (047)")
    return 0


def _dispersion_pcal_inspect(session: Session, manifest_path: str) -> int:
    """Feature 076 (T021): verify a 074 manifest and print the two_gamma the api dispersion applies.

    Read-only: runs the SAME ``load_calibration`` the api uses (bound to the ACTIVE model, temporal
    check skipped for inspection) and reports γ_lo/γ_hi/pivot + digest + fit_through + scope. Any
    structural / generation / scope failure prints the typed error and exits non-zero."""
    from horseracing_db.enums import AdoptionStatus
    from horseracing_db.models import ModelVersion
    from horseracing_probability.calib_activation import (
        ActivationError,
        Profile,
        load_calibration,
    )
    from horseracing_probability.calib_manifest import ManifestError
    from sqlalchemy import select

    active = session.scalar(
        select(ModelVersion.model_version)
        .where(ModelVersion.adoption_status == AdoptionStatus.ACTIVE)
    )
    if active is None:
        print("error: no ACTIVE model to bind the manifest against", file=sys.stderr)
        return 2
    try:
        act = load_calibration(
            manifest_path, active_model_version=active, target_date=None,
            profile=Profile.PRODUCTION, attestation_verifier=None,
        )
    except (ActivationError, ManifestError) as exc:
        print(f"error: manifest not usable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    tg = act.two_gamma.params
    print(f"dispersion-pcal inspect: digest={act.manifest_digest[:12]} "
          f"fit_through={act.fit_through.isoformat()} active_model={active}")
    print(f"  two_gamma: gamma_lo={tg['gamma_lo']:.6f} gamma_hi={tg['gamma_hi']:.6f} "
          f"pivot={tg['pivot']}")
    print("  the api applies this only to races AFTER fit_through (model_delta), fail-open.")
    return 0


def _dispersion_pcal(session: Session, args) -> int:
    """Feature 066 model_delta: fit + write the FROZEN two_gamma p-calibrator artifact.

    Reuses the 048 machinery (probability.load_p_samples + fit_p_calibrator method=two_gamma) on a
    frozen window that should sit strictly BEFORE the display/serving target (same frozen discipline
    as the band boundary). The calibrator is just a few floats (gamma_lo/hi/pivot); the API loads it
    read-time to show H(calibrated p) − H(q). The calibrated p is display-only — never persisted,
    never a model feature (II). Under-sampled → identity fallback (delta from raw p).

    KNOWN LEAK (diagnostic disclosure, constitution II — 074 research D7): the fit samples come from
    ``load_p_samples`` → ``_latest_run_predictions``, i.e. the latest full-history PredictionRun,
    which SAW each fit race's own outcome in training = NOT out-of-sample. So the gamma params are
    mildly optimistic. The impact is confined to the display-only ``model_delta`` read-out (no
    betting/serving/recommendation/feature consequence — the band is a function of q only). The
    OOF-faithful fix (fit from ``load_p_samples_from_oof`` / read an immutable calibration manifest)
    is deferred to the probability-pipeline-activation feature, once that manifest infra exists; see
    specs/074-oof-faithful-calibration/{spec.md:100, research.md D7}."""
    # Feature 076 (T021): the api dispersion path now reads the immutable manifest directly
    # (dispersion.load_activation_calibrator), so this command's role is INSPECT/VERIFY. The legacy
    # fit below is DEPRECATED (its samples are non-OOS — 074 D7) and kept only for back-compat.
    if getattr(args, "inspect_manifest", None):
        return _dispersion_pcal_inspect(session, args.inspect_manifest)

    from horseracing_eval.dispersion_bands import DispersionPCalibrator
    from horseracing_probability.model_calibration import (
        TWO_GAMMA_PIVOT,
        fit_p_calibrator,
        load_p_samples,
    )

    if args.fit_from is None or args.fit_to is None:
        print("error: legacy fit needs --from/--to; prefer --inspect-manifest (076 T021)",
              file=sys.stderr)
        return 2
    print("WARNING: the dispersion-pcal FIT path is DEPRECATED (non-OOS samples, 074 D7). The api "
          "reads the manifest directly now; use --inspect-manifest.", file=sys.stderr)
    if args.fit_from > args.fit_to:
        print(f"error: --from {args.fit_from} is after --to {args.fit_to}", file=sys.stderr)
        return 2
    samples = load_p_samples(session, date_from=args.fit_from, date_to=args.fit_to)
    cal = fit_p_calibrator(
        [(p, w) for (_rid, _d, p, w, _dh) in samples], method="two_gamma"
    )
    params = cal.params or {}
    art = DispersionPCalibrator(
        method=cal.method,
        gamma_lo=float(params.get("gamma_lo", 1.0)),
        gamma_hi=float(params.get("gamma_hi", 1.0)),
        pivot=float(params.get("pivot", TWO_GAMMA_PIVOT)),
        fit_from=args.fit_from.isoformat(),
        fit_to=args.fit_to.isoformat(),
        as_of=args.fit_to.isoformat(),
        version=args.version,
        n_races=cal.n_races,
    )
    path = art.write(args.out)
    print(f"dispersion-pcal: method={art.method} n_races={art.n_races} "
          f"[{art.fit_from}..{art.fit_to}]")
    print(f"  gamma_lo={art.gamma_lo:.5f} gamma_hi={art.gamma_hi:.5f} pivot={art.pivot}")
    print(f"  version={art.version}  -> {path}")
    if cal.method != "two_gamma":
        print("  NOTE: under-sampled -> identity fallback; model_delta will use raw p.")
    print("  NOTE: display-only calibrator; the calibrated p is never a model feature (II).")
    print("  NOTE: fit samples are the latest full-history run = NOT out-of-sample (known leak,")
    print("        074 research D7). Gamma is mildly optimistic; impact confined to model_delta.")
    print("        OOF-faithful fix deferred to pipeline-activation (immutable calib manifest).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
