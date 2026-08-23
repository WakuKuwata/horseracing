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
from horseracing_features.registry import (
    FEATURE_GROUPS,
    FEATURE_VERSION,
    RACE_CLASS_REPRESENTATION,
)
from sqlalchemy.orm import Session

from .adoption import AdoptionGate, evaluate_gate
from .artifacts import save_model_version
from .dataset import build_training_matrix  # noqa: F401  (re-exported convenience)
from .predictor import LightGBMPredictor


def _parse_date(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


def _require_subgroups(args) -> bool:
    """Feature 091: confirmatory runs MUST compute subgroups when the gate declares them.

    `--subgroups` is opt-in, but `decision.assert_confirmatory` fails closed when the gate-config
    declares critical_subgroups and none were computed. Left to the operator, that surfaces as a
    NO_DECISION after a multi-hour walk-forward. Turn it on implicitly instead.
    """
    if getattr(args, "subgroups", False):
        return True
    if not getattr(args, "confirmatory", False):
        return False
    cfg_path = getattr(args, "gate_config", None)
    if not cfg_path:
        return False
    import json as _json
    from pathlib import Path

    try:
        cfg = _json.loads(Path(cfg_path).read_text())
    except Exception:
        return False
    declared = (cfg.get("subgroup_guard") or {}).get("critical_subgroups")
    if declared:
        print("[091] --confirmatory with declared critical_subgroups -> enabling --subgroups")
        return True
    return False


def _load_opportunity_races(path: str | None) -> tuple[set | None, dict]:
    """適用集合の race_id 一覧を読み、由来を記録する。

    マスクは呼び出し側(= 特徴を知っている側)が作る。eval は features を import しないので
    中身の as-of 安全性は検証できず、代わりに凍結された expected_coverage との照合で守る。
    ここでは**どのファイルを読んだか**を残す — 事後にマスクを差し替えられては意味が無い。
    """
    if not path:
        return None, {}
    import hashlib
    from pathlib import Path as _Path

    raw = _Path(path).read_text()
    ids = {
        ln.strip() for ln in raw.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    }
    if not ids:
        raise SystemExit(f"error: opportunity race list is empty: {path}")
    return ids, {
        "path": str(path),
        "sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "n_race_ids": len(ids),
    }


def _load_verdict(path: str | None) -> dict | None:
    """Read a v3 evaluation report for the promotion boundary. Missing file fails loudly: an
    operator who mistypes the path must not silently get a CANDIDATE they think is ACTIVE."""
    if not path:
        return None
    import json as _json
    from pathlib import Path as _Path

    return _json.loads(_Path(path).read_text())


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
    verdict: dict | None = None,
    weight_mask_rate: float | None = None,
    weight_mask_seed: int | None = None,
) -> dict:
    eval_races = _load_eval_races(session)

    # Feature 091: race-atomic masking of the same-day weight columns during the fit AND the
    # calibration holdout. None keeps every pre-091 run byte-identical.
    fit_mask = None
    if weight_mask_rate is not None:
        from horseracing_features.weight_mask import MaskSpec

        fit_mask = MaskSpec(rate=weight_mask_rate, seed=weight_mask_seed, unit="race")

    def _make() -> LightGBMPredictor:
        return LightGBMPredictor(
            session, seed=seed, calibration=calibration,
            hpo=hpo, target_encode_cols=target_encode_cols, te_smoothing=te_smoothing,
            objective=objective, drop_features=drop_features,
            use_materialized=use_materialized, materialized_path=materialized_path,
            fit_weight_mask=fit_mask,
            race_class_representation=RACE_CLASS_REPRESENTATION,
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
        verdict=verdict,
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
            objective=objective, race_class_representation=RACE_CLASS_REPRESENTATION, **mat,
        )
        # baseline = current production shape (binary). Feature 039 candidate = cond_logit.
        baseline = LightGBMPredictor(
            session,
            seed=args.seed,
            calibration=args.calibration,
            race_class_representation=RACE_CLASS_REPRESENTATION,
            **mat,
        )
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


def _promote_model(session: Session, args) -> int:
    """active の切り替え。既定は dry-run — 本番の予測を差し替える操作なので明示を要求する。"""
    import datetime as _dt

    from .promote import PromoteError, apply_promotion, plan_promotion

    try:
        plan = plan_promotion(
            session,
            model_version=args.model_version,
            override_reason=args.override_reason,
            verdict=_load_verdict(args.verdict),
            current_fv=FEATURE_VERSION,
        )
    except PromoteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"promote {plan.model_version}  (現行 active: {plan.previous_active})")
    print(f"  根拠: {plan.basis}" + (f" — {plan.override_reason}" if plan.override_reason else ""))
    if plan.verdict_summary:
        print(f"  v3 verdict: {plan.verdict_summary}")
    if plan.problems:
        print("  昇格前確認 NG:", file=sys.stderr)
        for p in plan.problems:
            print(f"    - {p}", file=sys.stderr)
        return 1
    print("  昇格前確認: OK(artifact 実在・feature schema が serving に乗る)")
    if not args.apply:
        print("  dry-run。実行するには --apply を付ける")
        return 0
    rec = apply_promotion(
        session, plan, at=args.at or _dt.datetime.now().isoformat(timespec="seconds"),
        git_sha=_git_sha(),
    )
    print(f"  ACTIVE = {plan.model_version}"
          + (f" / {plan.previous_active} は candidate へ降格" if plan.previous_active else ""))
    print(f"  rollback: {rec['rollback_command']}")
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
    import json

    from .ev_weight_run import run_ev_weight_gate

    bundle_payload = None
    if getattr(args, "oof_bundle", None):
        from horseracing_probability.oof_bundle import read_bundle
        bundle_payload = read_bundle(args.oof_bundle)
    evidence = run_ev_weight_gate(
        session,
        active_dir=args.active_dir,
        out_root=args.out_root,
        bundle_payload=bundle_payload,
        date_from=args.from_,
        date_to=args.to,
        first_valid_year=args.first_valid_year,
        include_jump=args.include_jump,
    )
    rep = evidence["report"]
    base, cand = rep["base"], rep["cand"]
    print(f"ev-weight-gate-eval verdict={rep['verdict']} cap={rep['cap']} thr={rep['threshold']} "
          f"races={rep['n_races']} days={rep['n_days']} b_used={rep['b_used']}")
    print(f"  baseline : n_bets={base['n_bets']:7d} days={base['n_bet_days']:4d} "
          f"recovery={base['recovery']:.4f} winner_nll={base['winner_nll']:.4f}")
    print(f"  candidate: n_bets={cand['n_bets']:7d} days={cand['n_bet_days']:4d} "
          f"recovery={cand['recovery']:.4f} winner_nll={cand['winner_nll']:.4f}")
    ci = (f"[{rep['ci_low']:.4f}, {rep['ci_high']:.4f}]"
          if rep["ci_low"] is not None else "undefined")
    print(f"  Δrecovery={rep['delta']:+.4f}  95%CI={ci}  folds_improved="
          f"{rep['n_folds_improved']}/{rep['n_folds']}  worst_fold={rep['worst_fold_delta']:+.4f}")
    print(f"  MUST guards: winner_nll_ok={rep['winner_nll_ok']}  tail_ok={rep['tail_ok']}  "
          f"selection_jaccard={rep['selection_jaccard']:.3f}")
    print(f"  deferred diagnostics: {', '.join(rep['deferred_diagnostics'])}")
    print(f"  NOTE: {rep['note']}")
    if getattr(args, "out_json", None):
        with open(args.out_json, "w") as fh:
            json.dump(evidence, fh, indent=2, default=str)
        print(f"  evidence artifact -> {args.out_json}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="horseracing_training")
    sub = parser.add_subparsers(dest="command", required=True)

    te = sub.add_parser("train-evaluate", help="walk-forward train + calibrate + adopt + save")
    te.add_argument("--first-valid-year", type=int, default=2008)
    te.add_argument("--calibration", choices=["platt", "isotonic", "none"], default="platt")
    te.add_argument("--weight-mask-rate", type=float, default=None,
                    help="091: fraction of races whose same-day weight columns are masked during "
                         "the fit and the calibration holdout (race-atomic). Omit = no masking")
    te.add_argument("--weight-mask-seed", type=int, default=None,
                    help="091: deterministic seed for the mask selection "
                         "(required together with --weight-mask-rate)")
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
    te.add_argument("--verdict", default=None,
                    help="path to a v3 evaluation report (paired-eval / regime JSON). REQUIRED to "
                         "reach adoption_status=active: the legacy 4-metric gate has no paired "
                         "design, CI, subgroup guard or artifact isolation, so on its own it "
                         "cannot justify activating a model. Without it the row is a CANDIDATE.")
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
    pm = sub.add_parser("promote-model",
                        help="active を切り替える(単一 active 不変条件 + 記録される override)")
    pm.add_argument("--model-version", required=True)
    pm.add_argument("--verdict", default=None,
                    help="v3 評価レポート。昇格要件を満たすならこれだけで足りる")
    pm.add_argument("--override-reason", default=None,
                    help="v3 verdict が無い/満たさない場合に必須。理由は metrics_summary に残る")
    pm.add_argument("--apply", action="store_true",
                    help="これが無いと dry-run(計画を出すだけで DB を触らない)")
    pm.add_argument("--at", default=None, help="ISO タイムスタンプ(既定=現在時刻)")
    pm.add_argument("--database-url", default=None)

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

    # Feature 084: fit/publish and secondary OOS diagnosis stay separate so diagnosis
    # cannot re-cut the frozen bands.
    cb = sub.add_parser("chaos-bands",
                        help="084: fit top-3 chaos bands or run secondary OOS diagnostics")
    cb_sub = cb.add_subparsers(dest="chaos_bands_command", required=True)
    cb_fit = cb_sub.add_parser("fit",
                               help="fit market-q lambdas and P(S>=20) quintiles")
    cb_fit.add_argument("--fit-from", dest="fit_from", type=_parse_date, required=True)
    cb_fit.add_argument("--fit-to", dest="fit_to", type=_parse_date, required=True)
    cb_fit.add_argument("--valid-from", dest="valid_from", type=_parse_date, required=True)
    cb_fit.add_argument("--out-dir", default="artifacts/chaos_bands")
    cb_fit.add_argument("--database-url", default=None)
    # The window is REQUIRED at fit time: the loader rejects an artifact without one, so a fit
    # that omitted it would produce an artifact that is unreadable from birth. The long names
    # keep it distinct from capture's operational floor `--min-seconds-to-post` (FR-004a).
    cb_fit.add_argument(
        "--primary-horizon-min-seconds-to-post", type=int, required=True
    )
    cb_fit.add_argument(
        "--primary-horizon-max-seconds-to-post", type=int, required=True
    )
    cb_fit.add_argument("--primary-horizon-basis", required=True)
    cb_add_horizon = cb_sub.add_parser(
        "add-horizon",
        help="create a new approved-legacy artifact with a preregistered horizon",
    )
    cb_add_horizon.add_argument(
        "--artifact",
        required=True,
        help="approved legacy artifact digest",
    )
    cb_add_horizon.add_argument(
        "--primary-horizon-min-seconds-to-post",
        type=int,
        required=True,
    )
    cb_add_horizon.add_argument(
        "--primary-horizon-max-seconds-to-post",
        type=int,
        required=True,
    )
    cb_add_horizon.add_argument(
        "--primary-horizon-basis",
        required=True,
    )
    cb_add_horizon.add_argument(
        "--primary-horizon-measured-coverage",
        type=float,
        default=None,
        help="optional audit assertion; only 0.956 for [600, 86400] is established",
    )
    cb_diagnose = cb_sub.add_parser(
        "diagnose",
        help="SECONDARY OOS report; never an adoption gate and never re-cuts edges",
    )
    cb_diagnose.add_argument(
        "--from",
        dest="diagnose_from",
        type=_parse_date,
        required=True,
    )
    cb_diagnose.add_argument(
        "--to",
        dest="diagnose_to",
        type=_parse_date,
        required=True,
    )
    cb_diagnose.add_argument(
        "--artifact",
        required=True,
        help="approved artifact digest or JSON path",
    )
    cb_diagnose.add_argument(
        "--bootstrap-b",
        type=int,
        default=2000,
        help="race-day clustered bootstrap replicates (fixed seed)",
    )
    cb_diagnose.add_argument(
        "--export-fixture",
        default=None,
        help="write the SC-008 frozen fixture to this .parquet path plus .sha256",
    )
    cb_diagnose.add_argument(
        "--persist",
        action="store_true",
        help="append the completed report verbatim to diagnostic_runs",
    )
    cb_diagnose.add_argument("--database-url", default=None)
    cb_coverage = cb_sub.add_parser(
        "coverage",
        help="US6 capture/post-time coverage and selection-bias report",
    )
    cb_coverage.add_argument(
        "--from",
        dest="coverage_from",
        type=_parse_date,
        required=True,
    )
    cb_coverage.add_argument(
        "--to",
        dest="coverage_to",
        type=_parse_date,
        required=True,
    )
    cb_coverage.add_argument("--database-url", default=None)
    cb_prospective = cb_sub.add_parser(
        "prospective-report",
        help="US5 preregistered confirmation report",
    )
    cb_prospective.add_argument(
        "--artifact",
        required=True,
        help="approved artifact digest or JSON path",
    )
    cb_prospective.add_argument(
        "--bootstrap-b",
        type=int,
        default=2000,
        help="race-day clustered bootstrap replicates (fixed seed)",
    )
    cb_prospective.add_argument("--database-url", default=None)

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
    pe.add_argument("--opportunity-races", default=None,
                    help="事前登録した適用集合の race_id 一覧(1 行 1 件・# はコメント)。"
                         "gate-config の opportunity_set 宣言とセットでのみ有効。"
                         "scripts/build_opportunity_mask.py で作る")
    pe.add_argument("--subgroups", action="store_true",
                    help="069: report 2026/nk/coverage subgroup CIs + intersection-union guard")
    pe.add_argument("--confirmatory", action="store_true",
                    help="073: fail closed if gate-config is missing/unknown-version or its hash "
                         "mismatches --gate-config-hash (confirmatory-mode contract)")
    pe.add_argument("--gate-config-hash", default=None,
                    help="073: expected canonical gate-config hash for --confirmatory")
    # Feature 091: which input regime(s) to score under. The PRIMARY measurement is `serving`
    # (same-day weight masked on BOTH arms) because that is the condition predictions are made in;
    # `full_info` is the non-inferiority guard. `both` produces the regime report + verdict.
    pe.add_argument("--out", default=None,
                    help="091: write the regime report JSON (artifact_kind / verdict.adopt) here")
    pe.add_argument("--weight-regime", choices=("serving", "full_info", "both"), default=None,
                    help="091: evaluate under the serving regime (weight masked on both arms), "
                         "full-info, or both (both => regime report with a materialised verdict)")
    pe.add_argument("--acceptance-recent-folds", type=int, default=None,
                    help="091: outcome-blind wiring acceptance over the most recent N folds. "
                         "Stamps artifact_kind=acceptance / eligible_for_verdict=false — its folds "
                         "are inside the confirmatory window, so its NUMBERS must never gate")
    # Feature 091 (research D16): pin the input. The database moves under a multi-hour evaluation
    # — 4.8% of the window's rows were rewritten between two confirmatory runs — so a run that
    # reads it live cannot be repeated, and the frozen `determinism.tolerance` cannot be honoured.
    pe.add_argument("--use-materialized", action="store_true",
                    help="091: read the as-of block from a materialised parquet instead of the "
                         "live DB, so the run is repeatable")
    pe.add_argument("--materialized-path", default=None,
                    help="091: parquet to read (required with --use-materialized)")
    pe.add_argument("--pin-snapshot", action="store_true",
                    help="091: read the parquet even though the DB has moved past it. For a "
                         "paired comparison that is the point; the parquet identity is recorded "
                         "in the report so a pinned run is never mistaken for a fresh one")
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
    cse.add_argument("--arms", default=None,
                     help="comma-separated candidate arms to run (e.g. 'C/D'); default = all")
    cse.add_argument("--json", dest="json_out", default=None)
    cse.add_argument("--database-url", default=None)

    # Feature 085 §9-4: build arm E in the standard artifact shape and register it as CANDIDATE.
    # Registration is NOT promotion — §7's prospective holdout gates that.
    ae = sub.add_parser("register-arm-e",
                        help="085: train arm E full-history and register it as a CANDIDATE")
    ae.add_argument("--model-version", required=True)
    ae.add_argument("--artifacts-dir", required=True,
                    help="MUST be absolute; a bare relative path breaks ops predict (cwd=serving)")
    ae.add_argument("--n-oof-blocks", type=int, default=8)
    ae.add_argument("--seed", type=int, default=42)
    ae.add_argument("--weight-mask-rate", type=float, default=None,
                    help="091 fit-scope mask rate; must match the recipe being reproduced")
    ae.add_argument("--weight-mask-seed", type=int, default=None)
    ae.add_argument("--n-estimators", type=int, default=None,
                    help="094: booster の本数。確認で通した値を渡す(未指定=既定 300)")
    ae.add_argument("--num-threads", type=int, default=None)
    ae.add_argument("--json", dest="json_out", default=None)
    ae.add_argument("--database-url", default=None)

    # Feature 081 Phase 0: folklore residual-offset SCREENING probe (can_adopt=false). Read-only.
    fp = sub.add_parser("folklore-probe",
                        help="081 Phase 0: residual-offset screening probe (SCREENING ONLY)")
    fp.add_argument("--spec", default="pl_topk:isotonic:0.3",
                    help="active recipe spec (lgbm-065 = pl_topk:isotonic:0.3)")
    fp.add_argument("--from", dest="from_", type=_parse_date, required=True)
    fp.add_argument("--to", dest="to", type=_parse_date, required=True)
    fp.add_argument("--first-valid-year", type=int, default=2019)
    fp.add_argument("--seed", type=int, default=20260724)
    fp.add_argument("--bootstrap-b", type=int, default=2000)
    fp.add_argument("--num-threads", type=int, default=None)
    fp.add_argument("--gate-config", required=True)
    fp.add_argument("--confirmatory", action="store_true")
    fp.add_argument("--gate-config-hash", default=None)
    fp.add_argument("--cache", default="out/081-oof-cache.parquet")
    fp.add_argument("--reuse-cache", action="store_true",
                    help="reuse an existing OOF cache parquet instead of re-running the OOF")
    fp.add_argument("--json", dest="json_out", default="out/081-probe-report.json")
    fp.add_argument("--database-url", default=None)

    # Feature 082: segment accuracy readout (verification instrument, SECONDARY). Read-only
    # except the opt-in --persist append to diagnostic_runs.
    ar = sub.add_parser("accuracy-readout",
                        help="082: active-recipe historical OOF accuracy per frozen mask axis")
    ar.add_argument("--eval-from", dest="eval_from", type=_parse_date, required=True,
                    help="eval-window start (training always uses the FULL prior history)")
    ar.add_argument("--to", dest="to", type=_parse_date, required=True)
    ar.add_argument("--first-valid-year", type=int, default=None,
                    help="default: eval-from's year")
    ar.add_argument("--bundle", default=None,
                    help="existing OOF bundle to reuse (verified; digest must match active)")
    ar.add_argument("--out-root", default="artifacts/oof")
    ar.add_argument("--seed", type=int, default=20260725)
    ar.add_argument("--bootstrap-b", type=int, default=2000)
    ar.add_argument("--num-threads", type=int, default=1)
    ar.add_argument("--json", dest="json_out", default="out/082-segment-accuracy.json")
    ar.add_argument("--persist", action="store_true",
                    help="append the payload to diagnostic_runs (kind=segment_accuracy)")
    ar.add_argument("--database-url", default=None)

    # Pre-registered derivation-layer calibration diagnostic.  Local DB reads only; the runner has
    # no fetch/scrape fallback.  Defaults are the frozen rev2 window.
    jc = sub.add_parser(
        "joint-calibration",
        help="joint PL/Harville calibration from closing WIN q (SECONDARY)",
    )
    jc.add_argument("--from", dest="from_", type=_parse_date,
                    default=datetime.date(2019, 1, 1),
                    help="frozen window start; only 2019-01-01 is accepted")
    jc.add_argument("--to", dest="to", type=_parse_date,
                    default=datetime.date(2026, 7, 12),
                    help="frozen window end; only 2026-07-12 is accepted")
    jc.add_argument("--seed", type=int, default=20260731)
    jc.add_argument("--bootstrap-b", type=int, default=2000)
    jc.add_argument("--json", dest="json_out", default="out/joint-calibration.json")
    jc.add_argument("--database-url", default=None)

    # Bet the WIN pool on the EXOTIC pool's opinion — HZR's arrow reversed. Uses only data already
    # held; no fetching.
    cw = sub.add_parser("cross-pool-win",
                        help="back horses the exotic pool rates above the win pool")
    cw.add_argument("--seed", type=int, default=20260731)
    cw.add_argument("--bootstrap-b", type=int, default=2000)
    cw.add_argument("--json", dest="json_out", default="out/cross-pool-win.json")
    cw.add_argument("--database-url", default=None)

    # Pool information: does the EXOTIC pool know anything the WIN pool does not? Separates
    # "our PL derivation is lossy" from "combination bettors hold information" — only the second
    # leaves a route to profit.
    pi = sub.add_parser("pool-information",
                        help="delta-R2 of the exotic pool's own marginal over the win pool")
    pi.add_argument("--seed", type=int, default=20260730)
    pi.add_argument("--bootstrap-b", type=int, default=2000)
    pi.add_argument("--json", dest="json_out", default="out/pool-information.json")
    pi.add_argument("--database-url", default=None)

    # Real-price exotic edge: buy the combinations the exotic pool prices below what the WIN pool
    # implies. Needs exotic_quotes (the pool's own price grid) — the one measurement that was
    # impossible while only synthesised prices existed.
    pe = sub.add_parser("exotic-price-edge",
                        help="EV = P_market x O_real on captured exotic price grids")
    pe.add_argument("--seed", type=int, default=20260730)
    pe.add_argument("--bootstrap-b", type=int, default=2000)
    pe.add_argument("--json", dest="json_out", default="out/exotic-price-edge.json")
    pe.add_argument("--database-url", default=None)

    # Exotic combination portfolio: the structure the documented JRA profits actually used
    # (multi-ticket, odds-inverse staking, 馬連/馬単/三連複). Evidence instrument.
    ep = sub.add_parser("exotic-portfolio-eval",
                        help="combination portfolios settled at REAL exotic dividends")
    ep.add_argument("--from", dest="from_", type=_parse_date, required=True)
    ep.add_argument("--to", dest="to", type=_parse_date, required=True)
    ep.add_argument("--bundle", default=None,
                    help="OOF bundle: adds the SECONDARY model-p selection arm on the same "
                         "(bundle-covered) races, so market vs model is paired")
    ep.add_argument("--seed", type=int, default=20260729)
    ep.add_argument("--bootstrap-b", type=int, default=2000)
    ep.add_argument("--json", dest="json_out", default="out/exotic-portfolio.json")
    ep.add_argument("--database-url", default=None)

    # Cross-pool (win -> place): is the PLACE pool mispriced relative to the WIN pool?
    # Evidence instrument (can_adopt=false); rank profile is the λ-invariant primary readout.
    cp = sub.add_parser("cross-pool-eval",
                        help="place vs win pool: win-selected policy at real dividends")
    cp.add_argument("--from", dest="from_", type=_parse_date, required=True)
    cp.add_argument("--to", dest="to", type=_parse_date, required=True)
    cp.add_argument("--lambda2", type=float, default=None,
                    help="Harville stage discount for 2nd place (084 market-q fit: 0.8312)")
    cp.add_argument("--lambda3", type=float, default=None,
                    help="Harville stage discount for 3rd place (084 market-q fit: 0.7101)")
    cp.add_argument("--seed", type=int, default=20260729)
    cp.add_argument("--bootstrap-b", type=int, default=2000)
    cp.add_argument("--json", dest="json_out", default="out/cross-pool-place.json")
    cp.add_argument("--database-url", default=None)

    # ΔR² (Benter 1994): does the model add information the MARKET lacks? Evidence instrument
    # (can_adopt=false) — winner NLL stays the adoption gate; ΔR² says whether a change can move
    # ROI at all. Read-only.
    dr = sub.add_parser("delta-r2-eval",
                        help="Benter pseudo-R² increment over the market (evidence instrument)")
    dr.add_argument("--bundle", required=True, help="OOF prediction bundle (074/078)")
    dr.add_argument("--from", dest="from_", type=_parse_date, required=True)
    dr.add_argument("--to", dest="to", type=_parse_date, required=True)
    dr.add_argument("--seed", type=int, default=20260729)
    dr.add_argument("--bootstrap-b", type=int, default=2000)
    dr.add_argument("--delta-min", type=float, default=0.0,
                    help="materiality threshold; 0.0 keeps this evidence-only")
    dr.add_argument("--json", dest="json_out", default="out/delta-r2.json")
    dr.add_argument("--database-url", default=None)

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
    if args.command == "promote-model":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _promote_model(session, args)
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
    if args.command == "folklore-probe":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _folklore_probe(session, args)
    if args.command == "joint-calibration":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _joint_calibration(session, args)
    if args.command == "register-arm-e":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _register_arm_e(session, args)
    if args.command == "cross-pool-win":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _cross_pool_win(session, args)
    if args.command == "pool-information":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _pool_information(session, args)
    if args.command == "exotic-price-edge":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _exotic_price_edge(session, args)
    if args.command == "exotic-portfolio-eval":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _exotic_portfolio_eval(session, args)
    if args.command == "cross-pool-eval":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _cross_pool_eval(session, args)
    if args.command == "delta-r2-eval":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _delta_r2_eval(session, args)
    if args.command == "accuracy-readout":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _accuracy_readout(session, args)
    if args.command == "dispersion-bands":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _dispersion_bands(session, args)
    if args.command == "dispersion-pcal":
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            return _dispersion_pcal(session, args)
    if args.command == "chaos-bands":
        if args.chaos_bands_command == "add-horizon":
            return _chaos_bands_add_horizon(args)
        engine = create_db_engine(args.database_url)
        with Session(engine) as session:
            if args.chaos_bands_command == "fit":
                return _chaos_bands_fit(session, args)
            if args.chaos_bands_command == "diagnose":
                return _chaos_bands_diagnose(session, args)
            if args.chaos_bands_command == "coverage":
                return _chaos_bands_coverage(session, args)
            return _chaos_bands_prospective_report(session, args)
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
            if (getattr(args, "weight_mask_rate", None) is None) != (
                getattr(args, "weight_mask_seed", None) is None
            ):
                print("--weight-mask-rate and --weight-mask-seed must be given together "
                      "(a rate without a seed is not reproducible)", file=sys.stderr)
                return 1
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
                verdict=_load_verdict(getattr(args, "verdict", None)),
                weight_mask_rate=getattr(args, "weight_mask_rate", None),
                weight_mask_seed=getattr(args, "weight_mask_seed", None),
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
    # Feature 091: 'wmask=<rate>/<seed>' trains this arm with the race-atomic weight mask. The
    # candidate carries it; the active arm does not (and additionally drops weight_history, since
    # the model it stands for has no prev_weight column at all).
    wmask_rate = wmask_seed = None
    for seg in parts[2:]:
        if seg.startswith("drop="):
            groups = tuple(g for g in seg[len("drop="):].split(",") if g)
            drop_features = _expand_group_drops(groups)
        elif seg.startswith("wmask="):
            rate_s, _, seed_s = seg[len("wmask="):].partition("/")
            if not seed_s:
                raise ValueError("wmask= needs '<rate>/<seed>' (a rate without a seed is not "
                                 "reproducible)")
            wmask_rate, wmask_seed = float(rate_s), int(seed_s)
        elif seg:
            calib_frac = float(seg)
    return ModelRecipe(
        objective=objective, calibration=calibration, calib_frac=calib_frac,
        drop_features=drop_features, label=spec,
        weight_mask_rate=wmask_rate, weight_mask_seed=wmask_seed,
    )


def _factory_from_spec(session, spec: str, *, use_materialized: bool = False,
                       materialized_path: str | None = None,
                       pin_snapshot: bool = False):
    """Build the right PredictorFactory for a recipe spec.

    ``objective:oof_power`` → 068 C/D arm (full-history booster + strict-past OOF power γ);
    ``objective:oof_isotonic`` → 085 arm E (same booster, strict-past OOF isotonic);
    anything else → A/B arm (train-internal calibration holdout via RecipeFactory).

    The OOF names must be routed EXPLICITLY: they are not ``fit_calibrator`` methods, so an
    unrouted name would fall through to the holdout factory and silently measure a different
    arm (found by the 085 design review; ``fit_calibrator`` now also rejects unknown names)."""
    parts = spec.split(":")
    oof_method = {"oof_power": "power", "oof_isotonic": "isotonic"}.get(
        parts[1] if len(parts) > 1 else ""
    )
    if oof_method is not None:
        from .calib_split import CalibSplitFactory
        from .recipe import ModelRecipe
        # A trailing drop=<groups> (069) MUST survive into the OOF arm: the earlier form
        # discarded it, so "pl_topk:oof_isotonic:drop=g" silently evaluated the full column set
        # (same latent fault as the shared-matrix scope bypass fixed in feature 097).
        base = _recipe_from_spec(spec)
        return CalibSplitFactory(
            session, ModelRecipe(objective=parts[0], calibration="none",
                                 drop_features=base.drop_features, label=spec),
            method=oof_method,
        )
    from .recipe import RecipeFactory
    return RecipeFactory(
        session, _recipe_from_spec(spec),
        use_materialized=use_materialized, materialized_path=materialized_path,
        pin_snapshot=pin_snapshot,
    )


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




def _input_provenance(args) -> dict:
    """Identify the exact input a paired run consumed (091 research D16).

    Reading the live database makes a multi-hour evaluation unrepeatable: the daily ingest rewrites
    rows underneath it. When the run is pinned to a parquet we record that parquet's fingerprint
    and coverage, so "same inputs?" is a question the artifact can answer by itself.
    """
    if not getattr(args, "use_materialized", False):
        return {"input_source": "live_database",
                "input_note": ("NOT reproducible: the source tables can change between runs. "
                               "Use --use-materialized --pin-snapshot for a repeatable result.")}
    import json as _json
    from pathlib import Path as _Path

    path = _Path(args.materialized_path)
    out: dict = {"input_source": "materialized_parquet", "materialized_path": str(path),
                 "pinned": bool(getattr(args, "pin_snapshot", False))}
    mpath = path.with_suffix(".manifest.json")
    try:
        m = _json.loads(mpath.read_text())
        out["snapshot_fingerprint"] = m.get("source_fingerprint")
        out["snapshot_data_through"] = m.get("data_through")
        out["snapshot_content_hash"] = m.get("content_hash")
        out["snapshot_feature_version"] = m.get("feature_version")
    except Exception as exc:  # noqa: BLE001 — provenance is recorded, never load-bearing
        out["manifest_error"] = f"{type(exc).__name__}: {exc}"
    return out


def _paired_eval_regimes(args, cand, act, eval_races, gate_cfg, eval_start_year) -> int:
    """Feature 091 T048: serving-regime PRIMARY + full-info guard, with a materialised verdict."""
    import json

    from horseracing_eval.regime_paired import VERDICT_KIND, evaluate_regimes
    from horseracing_features.weight_mask import MaskSpec

    if not gate_cfg:
        print("--weight-regime requires --gate-config (the mask spec is pre-registered there)",
              file=sys.stderr)
        return 1
    wm = gate_cfg["weight_mask"]
    # The evaluation-time serving spec masks EVERY race (rate=1.0): it reproduces the condition,
    # it is not the training mixture. The training rate lives in the recipe.
    serving_spec = MaskSpec(rate=float(wm["eval_serving_rate"]), seed=int(wm["seed"]),
                            unit=wm["unit"])
    kind = VERDICT_KIND
    cand_rate = _recipe_from_spec(args.candidate).weight_mask_rate
    if getattr(args, "acceptance_recent_folds", None):
        kind = "acceptance"  # outcome-blind wiring check; its folds are inside the confirm window
    # A diagnostic arm (m=0 / m=1) is any candidate whose training mask rate is NOT the frozen
    # pre-registered one. Derive it from the spec rather than asking for a flag: an operator who
    # forgets the flag would otherwise emit an artifact that the verdict loader happily accepts,
    # which is exactly the post-hoc arm selection the pre-registration exists to prevent (068 C2).
    #
    # `is None` counts as "not the frozen arm" (2026-08 multi-codex review). It previously did not,
    # so a pre-091 recipe — one that predates the masking mechanism entirely — ran against a
    # gate-config whose frozen arm is m=0.5 and still came out stamped verdict-eligible.
    elif cand_rate is None or abs(cand_rate - float(wm["rate"])) > 1e-12:
        kind = "diagnostic"
        print(f"[091] candidate trains at m={cand_rate} but the frozen arm is "
              f"m={wm['rate']} -> artifact_kind=diagnostic (not verdict-eligible)")
    elif not getattr(args, "confirmatory", False):
        # A run that never proved it used the frozen config must not be indistinguishable from one
        # that did: `assert_verdict_eligible` reads only kind + flag, so the distinction has to be
        # stamped here.
        kind = "exploratory"
        print("[091] --weight-regime without --confirmatory -> artifact_kind=exploratory "
              "(not verdict-eligible; re-run with --confirmatory --gate-config-hash to decide)")

    # The frozen config declares a determinism contract. It was being ignored: LightGBM sums
    # partial gradients per thread, so a multi-threaded run is only reproducible to ~1e-4, while
    # the config promises 1e-9. Three runs of this evaluation drifted by 8.5e-5 — immaterial to a
    # -0.0106 effect, but it made the recorded "deterministic" claim false. Honour the declaration
    # instead of quietly running wider than it.
    det = gate_cfg.get("determinism") or {}
    frozen_threads = det.get("num_threads")
    num_threads = args.num_threads
    if frozen_threads is not None:
        # An explicit --num-threads used to win silently, which made the recorded "deterministic"
        # claim false again by a different route. A conflicting request is refused rather than
        # resolved: whichever way it resolved, one of the two statements would be a lie.
        if num_threads is not None and int(num_threads) != int(frozen_threads):
            print(f"error: --num-threads {num_threads} contradicts the frozen determinism "
                  f"contract (determinism.num_threads={frozen_threads}). Drop the flag to honour "
                  "the freeze, or re-freeze the config.", file=sys.stderr)
            return 1
        num_threads = int(frozen_threads)
        print(f"[091] honouring frozen determinism.num_threads={num_threads} "
              f"(tolerance {det.get('tolerance')})")

    report = evaluate_regimes(
        cand, act, eval_races,
        serving_spec=serving_spec,
        gate_config=gate_cfg,
        first_valid_year=eval_start_year,
        # Folds are year-granular, so --from only picks the eval START YEAR. Without also
        # passing it as the day-exact scored-window start, a mid-year frozen window (arm E's
        # prospective holdout: 2026-07-13) scores the whole calendar year — and
        # assert_confirmatory would still report the window as checked, because it compares the
        # CLI flags to the gate-config, not the races that were scored.
        valid_from=args.from_,
        num_threads=num_threads,
        artifact_kind=kind,
    )
    d = report.to_dict()
    d["snapshot"] = {"git_sha": _git_sha(), "feature_version": FEATURE_VERSION,
                     "candidate_spec": args.candidate, "active_spec": args.active,
                     # 091 D16: WHICH bytes this run read. Without it "we re-ran it and got a
                     # different number" is unanswerable — that is how the determinism drift went
                     # misattributed to LightGBM threading for a day.
                     **_input_provenance(args),
                     # record what was ACTUALLY used, so a reader can tell whether the run met
                     # the frozen determinism contract or ran wider than it
                     "num_threads": num_threads,
                     "determinism_declared": det or None}
    srv, fi = d["serving_regime"], d["full_info_regime"]
    print(f"paired-eval[regime] candidate={args.candidate} active={args.active} "
          f"kind={d['artifact_kind']} races={d['notes']['n_valid_races']}")

    if d["artifact_kind"] == "acceptance":
        # OUTCOME-BLIND. The acceptance folds sit inside the confirmatory window, so seeing the
        # effect here and deciding to continue on it IS the selection leak this run exists to
        # avoid. Printing it would make the discipline theatre, so the effect is withheld — only
        # the wiring facts are shown. The numbers are still written to --out for the record.
        print("  [outcome-blind acceptance: effect sizes withheld by design]")
        print(f"  wiring: races_scored={srv['n_races']} "
              f"masked_candidate={srv['mask_races_candidate']} "
              f"masked_active={srv['mask_races_active']} "
              f"full_info_unmasked={fi['mask_races_candidate'] == 0}")
        finite = all(
            v is not None and v == v
            for v in (srv["diff"], srv["ci_low"], srv["ci_high"], fi["diff"])
        )
        print(f"  wiring: metrics_finite={finite} "
              f"uncalibrated_available="
              f"{bool((d.get('uncalibrated') or {}).get('serving', {}).get('available'))}")
        print(f"  wiring: race_set_hash={d['race_set_hash'][:16]} "
              f"eligible_for_verdict={d['eligible_for_verdict']}")
    else:
        print(f"  serving  diff={srv['diff']:+.6f} CI=[{srv['ci_low']:+.6f},{srv['ci_high']:+.6f}] "
              f"n={srv['n_races']} masked={srv['mask_races_candidate']}")
        print(f"  full_info diff={fi['diff']:+.6f} CI=[{fi['ci_low']:+.6f},{fi['ci_high']:+.6f}] "
              f"guard={d['full_info_guard']}")
        for regime, u in (d.get("uncalibrated") or {}).items():
            if isinstance(u, dict) and u.get("available"):
                print(f"  uncalibrated[{regime}] diff={u['diff']:+.6f} "
                      f"CI=[{u['ci_low']:+.6f},{u['ci_high']:+.6f}]  (DIAGNOSTIC)")
        v = d["verdict"]
        print(f"  verdict.adopt={v['adopt']} "
              f"(primary={v['primary']} delta={v['min_effect_delta']})")
    if getattr(args, "out", None):
        # The per-day diffs are ~19k floats; pretty-printed inline they turn a 16 KB report into a
        # 1.1 MB file that nobody can read. Split them into a compact sibling instead — the report
        # stays reviewable and the raw material for any later CI question is still on disk.
        from pathlib import Path as _Path

        out = _Path(args.out)
        diffs = d.pop("diffs_by_day", None)
        # ...and only for the run that can actually decide something. Keeping 800 KB of raw floats
        # beside a control arm buys little: the block-width table is already in every report, and a
        # diagnostic's CI is not one anybody will need to re-derive.
        if diffs and d.get("eligible_for_verdict"):
            side = out.with_suffix(".diffs.json")
            side.write_text(json.dumps(diffs, separators=(",", ":"), default=float))
            d["diffs_by_day_file"] = side.name
            print(f"  per-day diffs -> {side.name}")
        elif diffs:
            d["diffs_by_day_file"] = None
            d["diffs_by_day_note"] = (
                "not retained: this artifact cannot decide a verdict, and the block-width "
                "sensitivity table above already covers the re-bucketing questions."
            )
        with open(out, "w") as fh:
            json.dump(d, fh, ensure_ascii=False, indent=2, default=float)
        print(f"  wrote {args.out}")
    return 0


def _paired_eval(session: Session, args) -> int:
    """Feature 068 (T018): build two RecipeFactory arms, run paired_eval, print + optional JSON.

    Both arms are re-fit per fold from their ModelRecipe (never a saved booster, codex C1) and
    scored on the same model-blind valid race set. eval owns the orchestration; the CLI only
    injects the training-side factories (020 boundary)."""
    import json

    mat_kwargs = {
        "use_materialized": bool(getattr(args, "use_materialized", False)),
        "materialized_path": getattr(args, "materialized_path", None),
        "pin_snapshot": bool(getattr(args, "pin_snapshot", False)),
    }
    if mat_kwargs["use_materialized"] and not mat_kwargs["materialized_path"]:
        print("--use-materialized requires --materialized-path (fail-closed: an implicit default "
              "would silently pick whichever parquet happens to be on disk)", file=sys.stderr)
        return 1
    if mat_kwargs["pin_snapshot"] and not mat_kwargs["use_materialized"]:
        print("--pin-snapshot only means something with --use-materialized", file=sys.stderr)
        return 1


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
        # The CLI window must match the frozen gate-config's eval_window. Without passing it,
        # assert_confirmatory only checked version+hash, so a confirmatory run could silently
        # score a DIFFERENT window than the one registered (2026-07 multi-codex review).
        eval_window = (
            {"from": args.from_.isoformat() if args.from_ else None,
             "to": args.to.isoformat() if args.to else None}
            if (args.from_ or args.to) else None
        )
        assert_confirmatory(
            gate_cfg, expected_hash=getattr(args, "gate_config_hash", None),
            eval_window=eval_window,
        )

    # codex C#1 fix: --from is the EVAL-window start, NOT a training-history floor. Load the FULL
    # history up to --to so each fold's outer-train uses ALL strictly-prior races; derive the eval
    # start year from --from. Previously start_date=args.from_ bounded BOTH training and eval, which
    # silently truncated every fold's outer-train to >= --from and dropped the first eval year
    # (empty train) — e.g. --from 2019 trained the 2020 fold on 2019 only, not 2008-2019.
    eval_start_year = args.from_.year if args.from_ else args.first_valid_year
    opp_races, opp_provenance = _load_opportunity_races(
        getattr(args, "opportunity_races", None)
    )
    eval_races = load_eval_races(session, start_date=None, end_date=args.to)
    cand = _factory_from_spec(session, args.candidate, **mat_kwargs)
    act = _factory_from_spec(session, args.active, **mat_kwargs)

    # Feature 091: regime-aware path. The standard paired-eval scores settled races, where the
    # same-day weight is present — the one condition under which this feature cannot help. The
    # PRIMARY measurement has to be taken with the weight masked on BOTH arms.
    if getattr(args, "weight_regime", None):
        return _paired_eval_regimes(args, cand, act, eval_races, gate_cfg, eval_start_year)

    report = paired_eval(
        cand, act, eval_races,
        gate_config=gate_cfg,
        first_valid_year=eval_start_year,
        # `--from` は年ではなく日付として効く。年単位 fold のままだと、年の途中から始まる窓が
        # その年をまるごと採点していた(2026-08 のレビュー)。Jan-1 始まりの既存 config では
        # 挙動は変わらない。
        valid_from=args.from_,
        bootstrap_seed=args.seed,
        bootstrap_b=args.bootstrap_b,
        num_threads=args.num_threads,
        opportunity_races=opp_races,
        snapshot={"git_sha": _git_sha(), "feature_version": FEATURE_VERSION,
                  "candidate_spec": args.candidate, "active_spec": args.active,
                  **({"opportunity_mask": opp_provenance} if opp_provenance else {})},
        subgroups=_require_subgroups(args),
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
    # v3: the recent guard is an evidence-of-harm veto with a margin, not a sign test — show the
    # per-window interval so "recent=True" cannot be read as "the recent window is fine".
    _recent = (g.reasons or {}).get("recent") or {}
    if _recent.get("windows"):
        for lab, w in _recent["windows"].items():
            if w.get("empty"):
                print(f"  {lab}: (no races in window)")
            elif "ci_low" in w:
                print(f"  {lab}: diff={w['diff']:+.6f} CI[{w['ci_low']:+.6f},{w['ci_high']:+.6f}] "
                      f"margin={_recent['margin']} -> {w['decision']}")
            else:
                print(f"  {lab}: diff={w['diff']:+.6f} (legacy point test) -> {w['decision']}")
    # Feature 073 US1 (FR-001): single machine-decided tri-value verdict (operator judgement=0).
    print(f"  DECISION={report.decision} "
          f"(cause={report.decision_reason.get('cause')}) "
          f"contract={report.evaluation_contract_version} gate_hash={report.gate_config_hash[:12]}")
    if report.opportunity:  # 適用集合(宣言があるときのみ)
        o = report.opportunity
        print(f"  opportunity[{o['definition']}]:")
        print(f"    coverage {o['coverage']:.3f} ({o['n_races']:,}/{o['n_eligible_total']:,} "
              f"レース・{o['n_days']} 開催日) 宣言 {o['declared_coverage']} "
              f"-> {'OK' if o['coverage_as_declared'] else 'NG'}")
        if o["ci_low"] is not None:
            print(f"    diff={o['diff']:+.6f} 標本CI[{o['ci_low']:+.6f}, {o['ci_high']:+.6f}] "
                  f"合成CI[{o['total_ci_low']:+.6f}, {o['total_ci_high']:+.6f}]")
        print("    注: 適用集合の優越性だけでは採用しない(全体非劣性と AND)")
    if report.subgroups:  # Feature 069 US1
        sg = report.subgroups
        for grain in ("race_subgroups", "horse_subgroups"):
            for lab, v in sg[grain].items():
                cci = v["bootstrap_ci"]
                rr = v.get("residual_risk")
                print(f"  subgroup[{lab}]: decision={v['decision']} "
                      f"CI[{cci['ci_low']},{cci['ci_high']}] days={v['n_days']} "
                      f"cand_minus_uniform={v['cand_minus_uniform']}"
                      + (f" residual_risk={rr:+.6f}" if rr is not None else ""))
        # v3: status is the veto input (FAIL/MISSING only); `subgroup_guard` is the strict IU
        # claim, kept as the "was full assurance achieved" audit field.
        print(f"  subgroup_guard(critical={sg['critical']}): status={sg['subgroup_guard_status']} "
              f"full_assurance={sg['subgroup_guard']} decisions={sg['subgroup_decisions']}")
        if sg["subgroup_guard_status"] == "NOT_PROVEN":
            print("    NOTE: 'no FAIL' is not 'no harm' — at least one critical subgroup could "
                  "not establish non-inferiority; see residual_risk for what is still admitted.")
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
        arms_filter=set(args.arms.split(",")) if args.arms else None,
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


def _joint_calibration(session: Session, args) -> int:
    """Frozen PL/Harville joint-calibration diagnostic (SECONDARY, can_adopt=false)."""
    import json
    from pathlib import Path

    from .joint_calibration_run import run

    payload = run(
        session,
        date_from=args.from_,
        date_to=args.to,
        seed=args.seed,
        bootstrap_b=args.bootstrap_b,
    )
    provenance = payload["provenance"]
    print(
        f"joint-calibration  races={provenance['n_races']}  "
        f"days={provenance['n_days']}  us2={provenance['us2_scope']}  "
        f"us2_scoreable={provenance['n_us2_scoreable_races']}"
    )
    print(f"  window: {provenance['frozen_window']}")
    print(f"  exclusions: {payload['exclusions']}")
    for section in (
        "stage_losses",
        "bet_type_nll",
        "selected_subset",
        "wide_inclusion",
        "field_size_mismatch_note",
    ):
        if section in payload:
            print(
                f"  {section}: "
                f"{json.dumps(payload[section], ensure_ascii=False, default=str)}"
            )
    print("  reliability: recorded in JSON (cell-micro and race-normalized readouts)")
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        print(f"  wrote {path}")
    return 0


def _register_arm_e(session: Session, args) -> int:
    """085 §9-4. Registration is not promotion; the row is pinned CANDIDATE."""
    import json
    from pathlib import Path

    from .arm_e_register import run

    art = Path(args.artifacts_dir)
    if not art.is_absolute():
        raise SystemExit(
            f"--artifacts-dir must be absolute (got {args.artifacts_dir!r}); a bare relative path "
            "is stored in weights_uri and fails to resolve from the ops predict cwd"
        )
    payload = run(
        session, model_version=args.model_version, artifacts_dir=str(art),
        n_oof_blocks=args.n_oof_blocks, seed=args.seed,
        weight_mask_rate=args.weight_mask_rate, weight_mask_seed=args.weight_mask_seed,
        n_estimators=args.n_estimators,
        num_threads=args.num_threads,
    )
    print(f"register-arm-e {payload['model_version']}  races={payload['n_races']}  "
          f"oof_rows={payload['n_oof_rows']}")
    print("  protocol=strict_past_oof_isotonic_v1  "
          f"thresholds={payload['threshold_checksum'][:16]}")
    print(f"  round-trip parity: {payload['parity_probes']} probes, "
          f"max|diff|={payload['parity_max_abs_diff']}")
    print(f"  artifacts: {payload['artifacts_dir']}")
    print("  registered as CANDIDATE — promotion needs the 085 §7 prospective holdout")
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"  wrote {args.json_out}")
    return 0


def _cross_pool_win(session: Session, args) -> int:
    """Back what the exotic pool likes, in the win pool (evidence instrument, can_adopt=false)."""
    import json
    from pathlib import Path

    from .cross_pool_win_run import run

    payload = run(session, seed=args.seed, bootstrap_b=args.bootstrap_b)
    pr, r = payload["provenance"], payload["result"]
    print(f"cross-pool-win  races={pr['n_races']}  days={pr['n_days']}  "
          f"pre-reg: {r['preregistration']}")
    print(f"  exclusions: {payload['exclusions']}")
    b = r["blind_all_started"]
    print(f"  全馬無差別(参照): ROI={b['roi']:.4f} [{b['ci_low']:.4f}, {b['ci_high']:.4f}]  "
          f"n={b['n_bets']}   1-控除率={r['reference_return']}")
    print("  方向             R      n_bets  hits  平均odds   ROI      95% CI            "
          "maxhit%  LOHO   判定")
    for c in r["cells"]:
        if not c["n_bets"]:
            continue
        lo = "  n/a " if c["ci_low"] is None else f"{c['ci_low']:.4f}"
        hi = "  n/a " if c["ci_high"] is None else f"{c['ci_high']:.4f}"
        star = " *" if c["verdict"] == "profitable" else "  "
        label = "exotic>win" if c["direction"] == "exotic_over_win" else "win>exotic(対照)"
        print(f"  {label:<16} {c['threshold']:<5} {c['n_bets']:>6} {c['n_hits']:>5} "
              f"{c['mean_selected_odds']:8.1f} {c['roi']:7.4f} [{lo}, {hi}] "
              f"{c['max_single_hit_share'] * 100:6.1f}% {c['leave_one_hit_out_roi']:6.4f} "
              f"{c['verdict']}{star}"
              + (f"  ({c['demoted_reason']})" if c["demoted_reason"] else ""))
    print(f"  HOLM 生存(順方向のみ): {r['holm_survivors'] or 'なし'}")
    print(f"  {r['control_note']}")
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"  wrote {args.json_out}")
    return 0


def _pool_information(session: Session, args) -> int:
    """Exotic pool vs win pool as winner predictors (evidence instrument, can_adopt=false)."""
    import json
    from pathlib import Path

    from .pool_information_run import run

    payload = run(session, seed=args.seed, bootstrap_b=args.bootstrap_b)
    pr, r = payload["provenance"], payload["result"]
    print(f"pool-information  races={pr['n_races']}  days={pr['n_days']}  blocks={pr['n_blocks']}")
    print(f"  exclusions: {payload['exclusions']}")
    print(f"  R2  exotic marginal = {r['r2']['model']:.5f}")
    print(f"  R2  win pool        = {r['r2']['market_raw']:.5f}")
    print(f"  R2  win recalibrated= {r['r2']['market_calibrated']:.5f}")
    print(f"  R2  blended         = {r['r2']['combined']:.5f}")
    lit, cond = r["ci_literal"], r["ci_model_given_market"]
    print(f"  dR2 literal            = {r['delta_r2_literal']:+.6f} "
          f"[{lit['ci_low']:+.6f}, {lit['ci_high']:+.6f}]")
    print(f"  dR2 exotic|win (PRI)   = {r['delta_r2_model_given_market']:+.6f} "
          f"[{cond['ci_low']:+.6f}, {cond['ci_high']:+.6f}]   verdict={r['verdict']}")
    print("  blend weight per block (alpha = weight on the EXOTIC signal):")
    for f in r["fits"][-6:]:
        print(f"    {f['block']}  n_fit={f['n_fit_races']:5}  alpha={f['alpha']:+.4f}  "
              f"beta={f['beta']:+.4f}  converged={f['converged']}")
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"  wrote {args.json_out}")
    return 0


def _exotic_price_edge(session: Session, args) -> int:
    """Real-price exotic edge (evidence instrument, can_adopt=false)."""
    import json
    from pathlib import Path

    from .exotic_price_edge_run import run

    payload = run(session, seed=args.seed, bootstrap_b=args.bootstrap_b)
    r = payload["result"]
    print(f"exotic price edge  pre-reg: {r['preregistration']}")
    print(f"  races: {payload['provenance']['n_races_by_bet_type']}")
    print(f"  exclusions: {payload['exclusions']}")
    print("  券種       EV閾値   n_bets  hits   ROI      95% CI            maxhit%  LOHO   判定")
    for c in r["cells"]:
        if not c["n_bets"]:
            continue
        lo = "  n/a " if c["ci_low"] is None else f"{c['ci_low']:.4f}"
        hi = "  n/a " if c["ci_high"] is None else f"{c['ci_high']:.4f}"
        star = " *" if c["verdict"] == "profitable" else "  "
        print(f"  {c['bet_type']:<10} {c['threshold']:5.2f} {c['n_bets']:>7} {c['n_hits']:>5} "
              f"{c['roi']:7.4f} [{lo}, {hi}] {c['max_single_hit_share'] * 100:6.1f}% "
              f"{c['leave_one_hit_out_roi']:6.4f} {c['verdict']}{star}"
              + (f"  ({c['demoted_reason']})" if c["demoted_reason"] else ""))
    print(f"  参照値(1-控除率): {r['reference_returns']}")
    print(f"  HOLM 生存: {r['holm_survivors'] or 'なし'}   ({r['multiplicity']})")
    print("  帯別内訳(交絡3の確認用):")
    for c in r["cells"]:
        if c["n_bets"] and c["verdict"] != "unprofitable":
            print(f"    {c['bet_type']:<10} T={c['threshold']:<5} {c['band_mix']}")
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"  wrote {args.json_out}")
    return 0


def _exotic_portfolio_eval(session: Session, args) -> int:
    """Combination-portfolio backtest (evidence instrument, can_adopt=false)."""
    import json
    from pathlib import Path

    from .exotic_portfolio_run import run_exotic_portfolio

    payload = run_exotic_portfolio(
        session, date_from=args.from_, date_to=args.to, seed=args.seed,
        bootstrap_b=args.bootstrap_b,
        bundle_path=Path(args.bundle) if args.bundle else None,
    )
    r = payload["result"]
    print(f"exotic portfolio  pre-reg: {r['preregistration']}")
    print(f"  races: {payload['provenance']['n_races_by_source_bet_type']}")
    print(f"  exclusions: {payload['exclusions']}")
    print("  src     bet_type    K  staking        n_bets  hits    ROI     95% CI"
          "               maxhit%  LOHO   verdict")
    for c in r["cells"]:
        if c["n_bets"] == 0:
            continue
        lo = "  n/a " if c["ci_low"] is None else f"{c['ci_low']:.4f}"
        hi = "  n/a " if c["ci_high"] is None else f"{c['ci_high']:.4f}"
        mark = " *" if c["verdict"] == "profitable" else "  "
        print(f"  {c['source']:<7} {c['bet_type']:<10} {c['k']:>2} "
              f"{c['staking']:<14} {c['n_bets']:>6} "
              f"{c['n_hits']:>5} {c['roi']:7.4f} [{lo}, {hi}] "
              f"{c['max_single_hit_share'] * 100:6.1f}% {c['leave_one_hit_out_roi']:6.4f} "
              f"{c['verdict']}{mark}"
              + (f"  ({c['demoted_reason']})" if c["demoted_reason"] else ""))
    print(f"  reference returns (1-takeout): {r['reference_returns']}")
    print(f"  HOLM SURVIVORS (primary family): {r['holm_survivors'] or 'none'}")
    print(f"  exploratory (not corrected): {r['exploratory']}")
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"  wrote {args.json_out}")
    return 0


def _cross_pool_eval(session: Session, args) -> int:
    """Cross-pool place diagnostic (evidence instrument, can_adopt=false)."""
    import json
    from pathlib import Path

    from .cross_pool_run import run_cross_pool

    payload = run_cross_pool(
        session, date_from=args.from_, date_to=args.to, seed=args.seed,
        bootstrap_b=args.bootstrap_b, lambda2=args.lambda2, lambda3=args.lambda3,
    )
    r = payload["result"]
    print(f"cross-pool(place)  races={r['n_races']}  days={r['n_days']}  "
          f"no-structure reference = {r['reference_return']:.2f}")
    print(f"  exclusions: {payload['exclusions']}")
    print("  rank profile (LAMBDA-INVARIANT primary):")
    print("    rank      n   hit%    meanQ   ROI      95% CI")
    for row in r["rank_profile"]:
        lo = "  n/a" if row["ci_low"] is None else f"{row['ci_low']:.4f}"
        hi = "  n/a" if row["ci_high"] is None else f"{row['ci_high']:.4f}"
        print(f"    {row['rank']:>4} {row['n_bets']:>6} {row['hit_rate'] * 100:5.1f}% "
              f"{row['mean_q']:7.4f} {row['roi']:7.4f}  [{lo}, {hi}]")
    for pol in r["primary_policies"]:
        if pol["name"] == "all_started":
            print(f"  blind all-started ROI = {pol['roi']:.4f} "
                  f"[{pol['ci_low']:.4f}, {pol['ci_high']:.4f}]  n={pol['n_bets']}")
    print("  secondary (lambda-DEPENDENT threshold policies):")
    for pol in r["secondary_threshold_policies"]:
        if pol["n_bets"]:
            print(f"    {pol['name']:<22} n={pol['n_bets']:>6} ROI={pol['roi']:.4f} "
                  f"[{pol['ci_low']:.4f}, {pol['ci_high']:.4f}]")
    pw = r.get("paired_win_vs_place")
    if pw:
        print(f"  PAIRED win vs place (pre-reg: {pw['preregistration']}):")
        print("    rank      n  win_hit% place_hit%  ROI_win  ROI_place   dROI     95% CI"
              "            verdict")
        for row in pw["by_rank"]:
            print(f"    {row['rank']:>4} {row['n_bets']:>6} {row['win_hit_rate'] * 100:8.1f}% "
                  f"{row['place_hit_rate'] * 100:9.1f}% {row['roi_win']:8.4f} "
                  f"{row['roi_place']:10.4f} {row['delta_roi']:+8.4f} "
                  f"[{row['ci_low']:+.4f}, {row['ci_high']:+.4f}]  {row['verdict']}")
        print(f"    dead-heat wins: {pw['n_dead_heat_win_races']} races  |  {pw['multiplicity']}")
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"  wrote {args.json_out}")
    return 0


def _delta_r2_eval(session: Session, args) -> int:
    """ΔR² (Benter 1994): pseudo-R² increment of p over the market. Evidence, never a gate."""
    import json
    from pathlib import Path

    from .delta_r2_run import run_delta_r2

    payload = run_delta_r2(
        session, bundle_path=Path(args.bundle), eval_from=args.from_, eval_to=args.to,
        seed=args.seed, bootstrap_b=args.bootstrap_b, delta_min=args.delta_min,
    )
    r = payload["result"]
    print(f"delta-r2  races={r['n_races']}  days={r['n_days']}  "
          f"blocks_scored={r['n_blocks_scored']}  mean log N={r['mean_log_field']:.5f}")
    print(f"  R2  model={r['r2']['model']:.5f}  market_raw={r['r2']['market_raw']:.5f}  "
          f"market_cal={r['r2']['market_calibrated']:.5f}  combined={r['r2']['combined']:.5f}")
    lit, cond = r["ci_literal"], r["ci_model_given_market"]
    print(f"  dR2 literal            = {r['delta_r2_literal']:+.6f}  "
          f"95% CI [{lit['ci_low']:+.6f}, {lit['ci_high']:+.6f}]")
    print(f"  dR2 model|market (PRI) = {r['delta_r2_model_given_market']:+.6f}  "
          f"95% CI [{cond['ci_low']:+.6f}, {cond['ci_high']:+.6f}]")
    print(f"  verdict={r['verdict']}   (Benter reference: fundamental "
          f"{r['reference']['benter_fundamental']}, tipster {r['reference']['benter_tipster']})")
    for f in r["fits"]:
        print(f"    fit {f['block']}: n={f['n_fit_races']} through={f['fit_through_day']} "
              f"alpha={f['alpha']:.5f} beta={f['beta']:.5f} gamma={f['gamma']:.5f} "
              f"converged={f['converged']}")
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        print(f"  wrote {args.json_out}")
    return 0


def _accuracy_readout(session: Session, args) -> int:
    """Feature 082: segment accuracy readout (SECONDARY verification instrument)."""
    import json
    from pathlib import Path

    from .segment_accuracy_run import run_segment_accuracy

    fvy = args.first_valid_year if args.first_valid_year is not None else args.eval_from.year
    payload = run_segment_accuracy(
        session, out_root=Path(args.out_root),
        bundle_path=Path(args.bundle) if args.bundle else None,
        eval_from=args.eval_from, eval_to=args.to, first_valid_year=fvy,
        seed=args.seed, bootstrap_b=args.bootstrap_b, num_threads=args.num_threads,
    )
    prov = payload["provenance"]
    pop = payload["population"]
    print(f"accuracy-readout model={prov['base_model_version']} "
          f"window={prov['eval_window']} (SECONDARY — verification instrument)")
    print(f"  scored: {pop['n_scored_races']} races / {pop['n_scored_horses']} horses; "
          f"exclusions={pop['exclusions']}")
    for axis in payload["axes"]:   # frozen library order — deliberately NOT sorted by score
        n_buckets = len(axis["buckets"])
        print(f"  axis {axis['axis_id']:18s} [{axis['family']:22s} grain={axis['grain']:5s}] "
              f"buckets={n_buckets}")
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w") as fh:
            json.dump(payload, fh, indent=2, default=str)
        print(f"  wrote {args.json_out}")
    if args.persist:
        from horseracing_eval.diagnostics_store import save_segment_accuracy_run
        lv = (f"segment-accuracy;model={prov['base_model_version']};"
              f"mask={prov['mask_library_version']};metric={prov['metric_contract_version']};"
              f"bundle={prov['bundle_digest'][:12]}")
        run = save_segment_accuracy_run(
            session, payload, date_from=args.eval_from, date_to=args.to, logic_version=lv,
        )
        session.commit()
        print(f"  persisted diagnostic_run {run.diagnostic_run_id} (kind=segment_accuracy)")
    return 0


def _folklore_probe(session: Session, args) -> int:
    """Feature 081 Phase 0: residual-offset SCREENING probe (can_adopt=false). Read-only."""
    import json
    from pathlib import Path

    from .folklore_probe import run_folklore_probe, write_report

    with open(args.gate_config) as fh:
        gate = json.load(fh)
    # Version-agnostic freeze check (the 073 assert_confirmatory is pinned to contract v2; this
    # is the phase0-screening-v1 contract, so verify the frozen hash directly).
    if getattr(args, "confirmatory", False):
        from horseracing_eval.decision import gate_config_hash
        want = getattr(args, "gate_config_hash", None)
        if not gate:
            print("error: confirmatory mode requires a gate-config", file=sys.stderr)
            return 2
        if want is not None and gate_config_hash(gate) != want:
            print("error: gate-config hash mismatch (config changed after freeze)",
                  file=sys.stderr)
            return 2
    if gate.get("can_adopt", True):
        print("error: folklore-probe requires can_adopt=false (screening-only)", file=sys.stderr)
        return 2

    report = run_folklore_probe(
        session, spec=args.spec, make_factory=lambda s: _factory_from_spec(session, s),
        gate=gate, from_date=args.from_, to_date=args.to,
        first_valid_year=args.first_valid_year, seed=args.seed, b=args.bootstrap_b,
        num_threads=args.num_threads, cache_path=Path(args.cache), reuse_cache=args.reuse_cache,
    )
    print(f"folklore-probe contract={report['contract']} can_adopt={report['can_adopt']} "
          f"window={report['window']} (SCREENING ONLY)")
    holm = report["holm_adjusted_diagnostic"]
    for r in sorted(report["reports"], key=lambda x: x["point_delta_nll"]):
        print(f"  {r['candidate_id']:22s} [{r['family']:10s}] k={r['k']:2d} "
              f"ΔNLL={r['point_delta_nll']:+.5f} CI[{r['ci_low']},{r['ci_high']}] "
              f"cov={r['coverage']:.2f} holm={holm.get(r['candidate_id'])} "
              f"{'PASS' if r['passes_screen'] else 'screen<'}")
    if args.json_out:
        write_report(report, Path(args.json_out))
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


def _chaos_bands_fit(session: Session, args) -> int:
    """Feature 084: fit and content-address publish the frozen chaos artifact."""
    from .chaos_bands import ChaosArtifactError, fit_artifact

    try:
        published = fit_artifact(
            session,
            fit_from=args.fit_from,
            fit_to=args.fit_to,
            valid_from=args.valid_from,
            out_dir=args.out_dir,
            code_sha=_git_sha(),
            primary_horizon={
                "minimum_seconds_to_post": args.primary_horizon_min_seconds_to_post,
                "maximum_seconds_to_post": args.primary_horizon_max_seconds_to_post,
                "basis": args.primary_horizon_basis,
            },
        )
    except ChaosArtifactError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    payload = published.payload
    print(
        f"chaos-bands fit: {payload['n_races_fit']} races "
        f"[{payload['fit_from']}..{payload['fit_to']}]"
    )
    print(f"  lambda2={payload['lambda2']:.6f} lambda3={payload['lambda3']:.6f}")
    print(f"  quintile_edges={payload['quintile_edges']}")
    print("  excluded races by reason:")
    for reason in (
        "no_started_horses",
        "field_too_small",
        "invalid_popularity_ranks",
        "partial_market_odds",
    ):
        print(f"    {reason}={published.excluded_race_counts.get(reason, 0)}")
    print(f"  numeric_stability={payload['numeric_stability_report']['status']}")
    print(f"  artifact_digest={published.artifact_digest}")
    print(f"  published={published.path}")
    print("  承認 manifest にこの digest を追記してください。")
    return 0


def _chaos_bands_add_horizon(args) -> int:
    """Feature 086: create-only bootstrap of one approved legacy artifact."""

    import json
    from pathlib import Path

    from horseracing_probability.chaos_artifact import (
        ChaosArtifactError as LoadArtifactError,
    )

    from .chaos_bands import ChaosArtifactError, add_horizon_artifact

    repository_root = Path(__file__).resolve().parents[3]
    artifacts_dir = repository_root / "artifacts" / "chaos_bands"
    source_path = artifacts_dir / f"{args.artifact}.json"
    try:
        result = add_horizon_artifact(
            source_path=source_path,
            expected_digest=args.artifact,
            minimum_seconds_to_post=args.primary_horizon_min_seconds_to_post,
            maximum_seconds_to_post=args.primary_horizon_max_seconds_to_post,
            basis=args.primary_horizon_basis,
            measured_coverage=args.primary_horizon_measured_coverage,
            out_dir=artifacts_dir,
        )
    except (ChaosArtifactError, LoadArtifactError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print("chaos-bands add-horizon: CREATE-ONLY")
    print(f"  source={result.source_path}")
    print(f"  published={result.path}")
    print("  full key-level diff:")
    for entry in result.key_diff:
        if entry.status == "added":
            rendered = json.dumps(
                entry.after,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )
            print(f"    ADDED     {entry.path} = {rendered}")
        elif entry.status == "removed":
            rendered = json.dumps(
                entry.before,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )
            print(f"    REMOVED   {entry.path} = {rendered}")
        elif entry.status == "changed":
            before = json.dumps(
                entry.before,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )
            after = json.dumps(
                entry.after,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )
            print(f"    CHANGED   {entry.path}: {before} -> {after}")
        else:
            rendered = json.dumps(
                entry.after,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )
            print(f"    UNCHANGED {entry.path} = {rendered}")
    print("  differences=2")
    print(
        "  only_differences="
        "artifact_digest,preregistration.primary_horizon"
    )
    print(f"  artifact_digest={result.artifact_digest}")
    print("  source_modified=false")
    print("  approval_manifest_modified=false")
    return 0


def _load_chaos_diagnostic_artifact(artifact_ref: str, target_date: datetime.date):
    from pathlib import Path

    from horseracing_probability.chaos_artifact import (
        approved_digests_from_manifest,
        load_chaos_artifact,
    )

    reference_path = Path(artifact_ref)
    repository_root = Path(__file__).resolve().parents[3]
    if reference_path.is_absolute() or reference_path.is_file():
        artifact_path = reference_path
    elif reference_path.suffix == ".json":
        artifact_path = repository_root / reference_path
    else:
        artifact_path = (
            repository_root / "artifacts" / "chaos_bands" / f"{artifact_ref}.json"
        )
    manifest_path = repository_root / "config" / "chaos_bands_approved.json"
    # Use the shared reader rather than a fourth inline parser: with three separate parsers one
    # of them eventually fails to learn a rule the others know. This path loads an EXPLICIT
    # digest, so it needs the status-irrelevant permission set, not the active resolver.
    approved = approved_digests_from_manifest(manifest_path)
    return load_chaos_artifact(
        artifact_path,
        approved_digests=approved,
        target_date=target_date,
    )


def _format_optional(value, digits: int = 4) -> str:
    return "-" if value is None else f"{float(value):.{digits}f}"


def _print_chaos_diagnosis(report: dict) -> None:
    print("chaos-bands diagnose: SECONDARY — not an adoption gate")
    print("  2024+ is discovery data")
    print("  PRIMARY: reliability, Brier, log score; AUC is AUXILIARY ONLY")
    print(
        f"  window=[{report['window']['from']}..{report['window']['to']}] "
        f"n={report['population']['analyzed_races']}"
    )
    print(
        "  pointwise 95% CIs: multiple comparisons not adjusted; "
        f"race-day clusters, seed={report['bootstrap']['seed']}"
    )
    print("\nBands (field-size means are shown to disclose N drift):")
    for band_row in report["band_summary"]:
        print(
            f"  {band_row['band']:<10} n={band_row['n']:>5} "
            f"mean_N={_format_optional(band_row['mean_field_size'], 2):>5} "
            f"median_S={_format_optional(band_row['median_s'], 1):>4}"
        )
        for event_key in ("s_ge_20", "himo_are", "total_collapse", "s_ge_30"):
            event = band_row["events"][event_key]
            if event["n"] == 0:
                print(f"    {event_key:<16} NO_DECISION (empty band)")
                continue
            calibration_ci = event["reliability"]["predicted_minus_realized_ci"]
            brier_ci = event["brier"]["cluster_ci"]
            log_ci = event["log_score"]["cluster_ci"]
            print(
                f"    {event_key:<16} predicted={event['predicted_rate']:.4f} "
                f"realized={event['realized_rate']:.4f} "
                f"calΔCI=[{_format_optional(calibration_ci['ci_low'])},"
                f"{_format_optional(calibration_ci['ci_high'])}] "
            )
            print(
                f"      Brier={event['brier']['point']:.4f} "
                f"CI=[{_format_optional(brier_ci['ci_low'])},"
                f"{_format_optional(brier_ci['ci_high'])}] "
                f"log={event['log_score']['point']:.4f} "
                f"CI=[{_format_optional(log_ci['ci_low'])},"
                f"{_format_optional(log_ci['ci_high'])}] "
                f"AUC(aux)={_format_optional(event['auc']['point'])} "
                f"{event['decision_status']}"
            )
    field_size_means = report["band_field_size_means"]
    calm_mean = field_size_means.get("t3_calm")
    wild_mean = field_size_means.get("t3_wild")
    print(
        "  disclosed field-size mean drift "
        f"t3_calm→t3_wild: {_format_optional(calm_mean, 2)}"
        f"→{_format_optional(wild_mean, 2)}"
    )

    print("\nOverall proper scores and fair baselines:")
    for event_key, chaos in report["overall"]["events"].items():
        baseline = report["baselines"]["events"][event_key]
        n_only = baseline["n_only"]
        g_h_n = baseline["g_h_n"]
        calibration_ci = chaos["reliability"]["predicted_minus_realized_ci"]
        print(
            f"  {event_key}: chaos Brier={chaos['brier']['point']:.4f} "
            f"log={chaos['log_score']['point']:.4f} "
            f"AUC(aux)={_format_optional(chaos['auc']['point'])} "
            f"{chaos['decision_status']}"
        )
        print(
            f"    reliability ECE={chaos['reliability']['ece']:.4f} "
            f"predicted-realized={chaos['reliability']['calibration_in_the_large']:+.4f} "
            f"CI=[{_format_optional(calibration_ci['ci_low'])},"
            f"{_format_optional(calibration_ci['ci_high'])}]"
        )
        print(
            f"    N-only     Brier={n_only['brier']['point']:.4f} "
            f"log={n_only['log_score']['point']:.4f} "
            f"AUC(aux)={_format_optional(n_only['auc']['point'])}"
        )
        print(
            f"    g(H,N)     Brier={g_h_n['brier']['point']:.4f} "
            f"log={g_h_n['log_score']['point']:.4f} "
            f"AUC(aux)={_format_optional(g_h_n['auc']['point'])}"
        )

    print("\nWithin-field-size buckets (AUC auxiliary; paired proper-score CIs are primary):")
    for bucket in report["within_field_size_buckets"]:
        print(f"  N={bucket['bucket']:<5} n={bucket['n']}")
        for event_key in ("s_ge_20", "himo_are", "total_collapse"):
            event = bucket["events"][event_key]
            scores = event["proper_scores"]
            auxiliary = event["auxiliary_auc_scores"]
            delta = event["paired_deltas"]["chaos_minus_g_h_n"]
            brier_ci = delta["brier_delta"]
            log_ci = delta["log_score_delta"]
            print(
                f"    {event_key:<16} chaos/g(H,N) Brier="
                f"{scores['chaos_probability']['brier']:.4f}/"
                f"{scores['g_h_n']['brier']:.4f} "
                f"ΔCI=[{_format_optional(brier_ci['ci_low'])},"
                f"{_format_optional(brier_ci['ci_high'])}]"
            )
            print(
                f"      chaos/g(H,N) log={scores['chaos_probability']['log_score']:.4f}/"
                f"{scores['g_h_n']['log_score']:.4f} "
                f"ΔCI=[{_format_optional(log_ci['ci_low'])},"
                f"{_format_optional(log_ci['ci_high'])}] "
                f"AUC(aux) chaos/g(H,N)/H/E[S]="
                f"{_format_optional(scores['chaos_probability']['auc_auxiliary'])}/"
                f"{_format_optional(scores['g_h_n']['auc_auxiliary'])}/"
                f"{_format_optional(auxiliary['normalized_entropy_h'])}/"
                f"{_format_optional(auxiliary['expected_s'])}"
            )

    fixture = report.get("fixture_export")
    if fixture is not None:
        print(
            f"\n  fixture={fixture['path']} n={fixture['n_races']} "
            f"sha256={fixture['sha256']}"
        )


def _chaos_bands_diagnose(session: Session, args) -> int:
    from horseracing_probability.chaos_artifact import ChaosArtifactError as LoadArtifactError

    from .chaos_bands import (
        ChaosArtifactError,
        diagnose,
    )

    try:
        artifact = _load_chaos_diagnostic_artifact(
            args.artifact,
            args.diagnose_from,
        )
        report = diagnose(
            session,
            diagnose_from=args.diagnose_from,
            diagnose_to=args.diagnose_to,
            artifact=artifact,
            bootstrap_b=args.bootstrap_b,
            export_fixture=args.export_fixture,
        )
    except (AssertionError, ChaosArtifactError, LoadArtifactError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    _print_chaos_diagnosis(report)
    if args.persist:
        from horseracing_eval.diagnostics_store import save_chaos_bands_run

        run = save_chaos_bands_run(
            session,
            report,
            date_from=args.diagnose_from,
            date_to=args.diagnose_to,
            logic_version=artifact.version,
        )
        session.commit()
        print(f"\n  persisted diagnostic_run_id={run.diagnostic_run_id}")
    return 0


def _format_percent(value) -> str:
    return "-" if value is None else f"{100.0 * float(value):.1f}%"


def _print_chaos_coverage(report: dict) -> None:
    population = report["population"]
    capture = report["capture_rate"]
    post_time = report["post_time_coverage"]
    print(
        "chaos-bands coverage: "
        f"[{report['window']['from']}..{report['window']['to']}]"
    )
    print(
        f"  captured={capture['numerator']}/{capture['denominator']} "
        f"({_format_percent(capture['rate'])}) "
        f"race_days={population['race_days']}"
    )
    print("  capture_strength (denominator=captured races):")
    for strength, count in report["capture_strength"]["counts"].items():
        rate = report["capture_strength"]["rates"][strength]
        print(f"    {strength}={count} ({_format_percent(rate)})")
    freshness = report["seconds_to_post"]
    print(
        "  seconds_to_post: "
        f"n={freshness['n_numeric']} missing={freshness['n_missing']} "
        f"p10={_format_optional(freshness['p10'], 0)} "
        f"median={_format_optional(freshness['median'], 0)} "
        f"p90={_format_optional(freshness['p90'], 0)}"
    )
    print(
        f"  post_time coverage={post_time['numerator']}/{post_time['denominator']} "
        f"({_format_percent(post_time['rate'])})"
    )
    for year, row in post_time["by_year"].items():
        print(
            f"    {year}: {row['numerator']}/{row['denominator']} "
            f"({_format_percent(row['rate'])})"
        )
    print(f"  CAP-10: {post_time['interpretation']}")

    not_captured = report["not_captured_characteristics"]
    print("  races NOT captured (selection-bias audit):")
    print(
        f"    n={not_captured['n_races']} "
        f"days={not_captured['n_race_days']} "
        f"mean_N={_format_optional(not_captured['mean_field_size'], 2)} "
        f"post_time_known={_format_percent(not_captured['post_time_known_rate'])}"
    )
    for label, key in (
        ("field_size", "field_size_buckets"),
        ("venue", "venues"),
        ("track", "track_types"),
        ("grade", "grades"),
        ("class", "race_classes"),
    ):
        print(f"    {label}={not_captured[key]['counts']}")


def _chaos_bands_coverage(session: Session, args) -> int:
    from .chaos_bands import coverage_report

    try:
        report = coverage_report(
            session,
            report_from=args.coverage_from,
            report_to=args.coverage_to,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _print_chaos_coverage(report)
    return 0


def _print_prospective_event(event_key: str, event: dict) -> None:
    if event["n"] == 0:
        print(f"    {event_key:<16} n=0 NO_DECISION")
        return
    reliability = event["reliability"]
    calibration_ci = reliability["predicted_minus_realized_ci"]
    brier_ci = event["brier"]["cluster_ci"]
    log_ci = event["log_score"]["cluster_ci"]
    print(
        f"    {event_key:<16} n={event['n']} positives={event['positives']} "
        f"predicted={event['predicted_rate']:.4f} "
        f"realized={event['realized_rate']:.4f}"
    )
    print(
        f"      reliability ECE={reliability['ece']:.4f} "
        f"calΔCI=[{_format_optional(calibration_ci['ci_low'])},"
        f"{_format_optional(calibration_ci['ci_high'])}]"
    )
    print(
        f"      Brier={event['brier']['point']:.4f} "
        f"CI=[{_format_optional(brier_ci['ci_low'])},"
        f"{_format_optional(brier_ci['ci_high'])}] "
        f"log={event['log_score']['point']:.4f} "
        f"CI=[{_format_optional(log_ci['ci_low'])},"
        f"{_format_optional(log_ci['ci_high'])}] "
        f"AUC(aux only)={_format_optional(event['auc']['point'])}"
    )


def _print_chaos_prospective(report: dict) -> None:
    promotion = report["promotion"]
    cohort = report["cohort"]
    print(
        f"chaos-bands prospective-report: {promotion['decision']} "
        f"[{report['window']['valid_from']}..{report['window']['through']}]"
    )
    print(
        "  PRIMARY metrics: reliability / Brier / log score; "
        "AUC is AUXILIARY ONLY and never decides"
    )
    print(
        f"  cohort: loaded={cohort['loaded_readouts']} "
        f"analyzed={cohort['analyzed_races']} "
        f"race_days={cohort['race_days']} "
        "capture_strength=confirmatory only"
    )
    print(f"  exclusions={cohort['exclusions']}")
    print(
        "  analysis unit: one row per race at the primary horizon; "
        "selecting a latest row is forbidden"
    )
    print("  outcomes use frozen snapshot popularity; live race_horses is never used")

    print("\n  Overall:")
    for event_key in ("s_ge_20", "himo_are", "total_collapse", "s_ge_30"):
        _print_prospective_event(
            event_key,
            report["overall"]["events"][event_key],
        )
    print(f"\n  By field size: {[row['bucket'] for row in report['by_field_size']]}")
    print(
        "  By capture horizon: "
        f"{[row['horizon'] for row in report['by_capture_horizon']]}"
    )

    # FR-011 / FR-012: the trigger breakdown and the selection-bias disclosure are MUST-print.
    # They live in the payload, but the operator reads THIS output -- omitting them here would
    # leave the honest-limits statement invisible to the only person who acts on it.
    print("\n  By capture trigger (all five always shown, including n=0):")
    for row in report["by_capture_trigger"]:
        print(
            f"    {row['trigger']:<18} n={row['n']:<5} "
            f"confirmation_eligible={row['confirmation_eligible']:<5} "
            f"selection_biased={row['selection_biased']}"
        )
    share = report["user_selected_share"]
    print(
        "    user_selected_share="
        + ("null (no observation outside legacy_unknown)" if share is None else f"{share:.3f}")
    )
    bias = report["prospective_selection_bias"]
    print(
        f"    policy_primary_source={bias['policy_primary_source']} "
        f"observed_primary_source={bias['observed_primary_source']} "
        f"claim_violated={bias['primary_source_claim_violated']}"
    )
    print(
        f"    user_selected_role={bias['user_selected_role']} "
        f"removable={bias['removable']}"
    )
    print(f"    note={bias['note']}")

    print("\n  Promotion gate:")
    print(
        f"    controller=p_s_ge_20 positives={promotion['observed_positives']}/"
        f"{promotion['minimum_positives']} race_days="
        f"{promotion['observed_race_days']}/{promotion['minimum_race_days']} "
        f"final_date={promotion['final_decision_date']}"
    )
    print("    himo_are=secondary; total_collapse=NOT ELIGIBLE; s_ge_30=diagnostic only")
    print(f"    reasons={promotion['decision_reasons']}")
    print(f"    {promotion['panel_action']}: {promotion['panel_action_ja']}")
    print(f"    {report['lambda_limit_note']}")

    estimates = report["required_sample_estimates"]
    print("\n  Required-sample estimate:")
    print(
        "    s_ge_20: "
        f"{estimates['s_ge_20']['cluster_adjusted_races']} races, "
        f"{estimates['s_ge_20']['years'][0]}-"
        f"{estimates['s_ge_20']['years'][1]} years"
    )
    print(
        "    s_ge_30 (diagnostic only): "
        f"{estimates['s_ge_30']['cluster_adjusted_races']} races, "
        f"{estimates['s_ge_30']['years'][0]}-"
        f"{estimates['s_ge_30']['years'][1]} years"
    )

    coverage = report["capture_coverage"]
    capture = coverage["capture_rate"]
    post_time = coverage["post_time_coverage"]
    excluded = report["excluded_race_characteristics"]
    print("\n  Capture coverage and excluded-race characteristics (always reported):")
    print(
        f"    captured={capture['numerator']}/{capture['denominator']} "
        f"({_format_percent(capture['rate'])}) "
        f"post_time={_format_percent(post_time['rate'])}"
    )
    print(
        f"    not_captured n={excluded['n_races']} "
        f"mean_N={_format_optional(excluded.get('mean_field_size'), 2)} "
        f"post_time_known={_format_percent(excluded.get('post_time_known_rate'))}"
    )


def _chaos_bands_prospective_report(session: Session, args) -> int:
    from horseracing_probability.chaos_artifact import ChaosArtifactError as LoadArtifactError

    from .chaos_bands import ChaosArtifactError, prospective_report

    report_date = datetime.date.today()
    try:
        artifact = _load_chaos_diagnostic_artifact(
            args.artifact,
            report_date,
        )
        report = prospective_report(
            session,
            artifact=artifact,
            as_of=report_date,
            bootstrap_b=args.bootstrap_b,
        )
    except (ChaosArtifactError, LoadArtifactError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    _print_chaos_prospective(report)
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
