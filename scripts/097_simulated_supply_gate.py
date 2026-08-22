"""Feature 097 adoption gate: pseudo-supply-death simulation (contracts/adoption-gate.md).

Everything that could be a judgement call is frozen in gate-config.json (hash fail-closed):
cutoffs, mask, scored windows, arms, δ, seed-noise, guards, pooling rules. This driver only
executes that contract. Three properties are enforced at runtime, not by convention:

- **Symmetry**: the mask changes no scored race, no started set, no winner (assert).
- **Provenance**: right before the matrix is built, the masked session is queried and the build
  must see 0 rows that should have been masked; the projection hash is recorded. Both arms share
  ONE matrix object (``is``) and ``use_materialized`` is False — a parquet, a cache or a second
  connection cannot hand either arm the unmasked column.
- **Output discipline**: no effect number is printed or written until primary, guard 1 and guard 2
  have ALL been computed. Progress lines carry no numbers.

The verdict artifact is ``counterfactual_supply_simulation`` — evidence for adopting COLUMNS under
the regime that matters. It is NOT a full_walk_forward verdict for a registered model:
``eligible_for_verdict`` is False so ``evaluate_promotion`` refuses to promote on it.

Usage (real run, ~4.5h):
    cd training && uv run python ../scripts/097_simulated_supply_gate.py \\
        --gate-config ../specs/097-early-mid-pace/gate-config.json \\
        --gate-config-hash $(cat ../specs/097-early-mid-pace/gate-config.hash.txt) \\
        --json ../specs/097-early-mid-pace/verdict.json
Smoke (plumbing only, numbers redacted, non-registered cutoff):  add ``--smoke --json ../out/...``
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from dataclasses import asdict

from horseracing_db.session import create_db_engine
from horseracing_eval.bootstrap import inflate_for_seed_noise, race_day_cluster_bootstrap_ci_v1
from horseracing_eval.dataset import load_eval_races
from horseracing_eval.decision import EVALUATION_CONTRACT_VERSION, assert_confirmatory
from horseracing_eval.gates import evaluate_core_gate, recent_window_guard
from horseracing_eval.paired import paired_eval
from horseracing_eval.provenance import frame_projection_hash
from horseracing_eval.subgroups import FAIL, three_way
from horseracing_features.registry import FEATURE_GROUPS
from sqlalchemy.orm import Session

from horseracing_training.calib_split import CalibSplitFactory
from horseracing_training.predictor import LightGBMPredictor
from horseracing_training.recipe import ModelRecipe
from horseracing_training.supply_mask import (
    PROVENANCE_COLS,
    apply_first3f_mask,
    projection_rows,
    provenance_violations,
    symmetry_snapshot,
)

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
GROUP = "early_mid_pace"
ARTIFACT_KIND = "counterfactual_supply_simulation"


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def _recipe(a: dict, *, drop: tuple[str, ...], label: str, rounds: int | None,
            blocks_override: int | None) -> ModelRecipe:
    r = a["recipe"]
    params = dict(r.get("params") or {})
    if rounds is not None:
        params["n_estimators"] = rounds
    return ModelRecipe(
        objective=r["objective"], calibration="none", calib_frac=0.0, seed=int(r["seed"]),
        params=tuple(sorted((k, v) for k, v in params.items())) or None,
        weight_mask_rate=r.get("weight_mask_rate"), weight_mask_seed=r.get("weight_mask_seed"),
        drop_features=drop, label=label,
    )


def _arms(session, cfg, *, rounds, blocks):
    a = cfg["arms"]
    drop = tuple(c for c, g in FEATURE_GROUPS.items() if g == GROUP)
    assert drop, "early_mid_pace group has no columns — registry not wired"
    n_blocks = blocks or int(a["recipe"]["n_oof_blocks"])
    cand = CalibSplitFactory(session, _recipe(a, drop=(), label="097:candidate", rounds=rounds,
                                              blocks_override=blocks),
                             n_oof_blocks=n_blocks, method="isotonic", require_sufficient=True)
    act = CalibSplitFactory(session, _recipe(a, drop=drop, label="097:active", rounds=rounds,
                                             blocks_override=blocks),
                            n_oof_blocks=n_blocks, method="isotonic", require_sufficient=True)
    assert cand.recipe_hash != act.recipe_hash
    # ONE matrix for both arms (provenance contract): build once from THIS session, inject.
    tmp = LightGBMPredictor(session, objective=a["recipe"]["objective"], calibration="none")
    assert tmp.use_materialized is False, "matrix must come from the session, not the parquet"
    shared = tmp._ensure_data()
    cand._shared = shared
    act._shared = shared
    assert cand._shared is act._shared
    return cand, act, drop


def _run_window(session, cfg, *, w_from, w_to, mask_cutoff, rounds, blocks, b, seed, log):
    """One scored window. ``mask_cutoff=None`` = full-info world. Returns the report + audit."""
    session.rollback()
    sym_before = symmetry_snapshot(session)
    audit: dict = {"window": [str(w_from), str(w_to)],
                   "mask_cutoff": str(mask_cutoff) if mask_cutoff else None}
    if mask_cutoff is not None:
        audit["masked_rows"] = apply_first3f_mask(session, mask_cutoff)
        log("mask OK")
        assert symmetry_snapshot(session) == sym_before, "mask changed races/started/winners"
        log("symmetry OK")
        viol = provenance_violations(session, mask_cutoff)
        assert viol == 0, f"provenance: {viol} rows still carry a value that should be masked"
    audit["provenance_hash"] = frame_projection_hash(projection_rows(session), PROVENANCE_COLS)
    log("provenance OK")
    cand, act, drop = _arms(session, cfg, rounds=rounds, blocks=blocks)
    log("build OK")
    races = load_eval_races(session, end_date=w_to)
    rep = paired_eval(
        cand, act, races, gate_config=cfg, first_valid_year=w_from.year, valid_from=w_from,
        bootstrap_seed=seed, bootstrap_b=b, num_threads=int(cfg["determinism"]["num_threads"]),
        subgroups=False,
        snapshot={"driver": "097_simulated_supply_gate", "window": audit["window"],
                  "mask_cutoff": audit["mask_cutoff"], "dropped_in_active": list(drop)},
    )
    log("fit OK")
    session.rollback()
    audit.update({"n_races": rep.n_races, "n_days": rep.bootstrap_ci["n_days"],
                  "race_id_set_hash": rep.race_id_set_hash,
                  "candidate_recipe_hash": rep.candidate_recipe_hash,
                  "active_recipe_hash": rep.active_recipe_hash})
    return rep, audit


def _pool(reports):
    pooled: dict[str, list[float]] = {}
    for rep in reports:
        for d, v in rep.diffs_by_day.items():
            assert d not in pooled, f"windows overlap on {d} — pooled bootstrap would double count"
            pooled[d] = list(v)
    return pooled


def _weighted(reports, key):
    n = sum(r.n_races for r in reports)
    return sum(r.gate.reasons[key] * r.n_races for r in reports) / n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate-config", required=True)
    ap.add_argument("--gate-config-hash", required=True)
    ap.add_argument("--json", dest="json_out", required=True)
    ap.add_argument("--smoke", action="store_true",
                    help="plumbing only: non-registered cutoff, low capacity, numbers redacted")
    args = ap.parse_args()

    cfg = json.loads(open(args.gate_config).read())
    env = cfg["eval_window"]
    assert_confirmatory(cfg, expected_hash=args.gate_config_hash,
                        eval_window={"from": env["from"], "to": env["to"]})
    sim, guards, boot = cfg["simulation"], cfg["guards"], cfg["bootstrap"]
    env_from, env_to = _date(env["from"]), _date(env["to"])
    for wf, wt in sim["scored_windows"]:
        assert env_from <= _date(wf) <= _date(wt) <= env_to, "scored window outside envelope"
    print(f"contract {EVALUATION_CONTRACT_VERSION} OK  hash={args.gate_config_hash[:12]}  "
          f"{'SMOKE' if args.smoke else 'REAL'}", flush=True)

    if args.smoke:
        sm = cfg["smoke"]
        plan = [(_date(sm["cutoff"]), _date(sm["scored_window"][0]), _date(sm["scored_window"][1]))]
        rounds, blocks, b = 50, 2, 200
    else:
        plan = [(_date(c), _date(w[0]), _date(w[1]))
                for c, w in zip(sim["cutoffs"], sim["scored_windows"], strict=True)]
        rounds, blocks, b = None, None, int(boot["b"])
    seed = int(boot["seed"])
    def log(msg):
        print(f"  {msg}", flush=True)

    engine = create_db_engine(DB)
    t0 = time.time()
    with Session(engine) as s:
        # ---- primary: masked worlds ----
        prim_reports, prim_audit = [], []
        for cutoff, wf, wt in plan:
            print(f"cutoff {cutoff} -> window {wf}..{wt}:", flush=True)
            rep, au = _run_window(s, cfg, w_from=wf, w_to=wt, mask_cutoff=cutoff, rounds=rounds,
                                  blocks=blocks, b=b, seed=seed, log=log)
            prim_reports.append(rep)
            prim_audit.append(au)
        # ---- guard 1: full-info, same windows ----
        g1_reports, g1_audit = [], []
        for _, wf, wt in plan:
            print(f"guard1 full-info window {wf}..{wt}:", flush=True)
            rep, au = _run_window(s, cfg, w_from=wf, w_to=wt, mask_cutoff=None, rounds=rounds,
                                  blocks=blocks, b=b, seed=seed, log=log)
            g1_reports.append(rep)
            g1_audit.append(au)
        # ---- guard 2: real degraded window ----
        g2_from = _date(guards["real_degraded_direction"]["window_from"])
        s.rollback()
        latest = max(er.context.race_date for er in load_eval_races(s))
        print(f"guard2 real window {g2_from}..{latest}:", flush=True)
        g2_rep, g2_audit = _run_window(s, cfg, w_from=g2_from, w_to=latest, mask_cutoff=None,
                                       rounds=rounds, blocks=blocks, b=b, seed=seed, log=log)
    elapsed = time.time() - t0
    print("--- all components computed ---", flush=True)

    # ---- pooled primary (all numbers from here on) ----
    sn = cfg["seed_noise"]
    pooled = _pool(prim_reports)
    ci = race_day_cluster_bootstrap_ci_v1(pooled, b=b, seed=seed, alpha=float(boot["alpha"]))
    total = inflate_for_seed_noise(
        ci, sd_fold=float(sn["sd_fold"]), n_folds=len(prim_reports),
        k_seeds=int(sn.get("k_seeds", 1)),
    )
    min_days = int(env["min_eval_days"])
    sufficient = ci.n_days >= min_days
    recent = recent_window_guard(pooled, cfg=cfg, max_date=_date(cfg["recent_guard"]["max_date"]))
    gate = evaluate_core_gate(
        diff=ci.point, ci_low=total.ci_low, ci_high=total.ci_high, recent=recent,
        top2_diff=_weighted(prim_reports, "top2_diff"),
        top3_diff=_weighted(prim_reports, "top3_diff"),
        cand_ece=_weighted(prim_reports, "cand_ece"),
        act_ece=_weighted(prim_reports, "act_ece"),
        cfg=cfg,
    )
    # ---- guards (sample CI, no inflate — frozen A1) ----
    g1_pooled = _pool(g1_reports)
    g1_ci = race_day_cluster_bootstrap_ci_v1(g1_pooled, b=b, seed=seed, alpha=float(boot["alpha"]))
    g1_dec = three_way(g1_ci.ci_low, g1_ci.ci_high, float(guards["full_info"]["margin"]),
                       point=g1_ci.point)
    guard1 = g1_dec != FAIL
    g2_ci = race_day_cluster_bootstrap_ci_v1(g2_rep.diffs_by_day, b=b, seed=seed,
                                             alpha=float(boot["alpha"]))
    guard2 = not (g2_ci.ci_low > float(guards["real_degraded_direction"]["margin"]))

    if not sufficient:
        status, cause = "NO_DECISION", f"insufficient_eval_days({ci.n_days}<{min_days})"
    elif gate.adopted and guard1 and guard2:
        status, cause = "ADOPT", "primary_pooled AND guard1 AND guard2"
    else:
        status, cause = "REJECT", "primary_pooled AND guard1 AND guard2 not all true"

    out = {
        "artifact_kind": "smoke" if args.smoke else ARTIFACT_KIND,
        "eligible_for_verdict": False,
        "feature_adoption_eligible": not args.smoke,
        "evidence_regime": "masked_pseudo_supply_death",
        "model_training_regime": "real_unmasked",
        "evaluation_contract_version": EVALUATION_CONTRACT_VERSION,
        "gate_config_hash": args.gate_config_hash,
        "reported_as": "counterfactual robustness — NOT reconstructed historical predictions",
        "elapsed_s": round(elapsed),
        "capacity": {"rounds": rounds or cfg["arms"]["recipe"]["params"]["n_estimators"],
                     "n_oof_blocks": blocks or cfg["arms"]["recipe"]["n_oof_blocks"], "b": b},
        "primary": {
            "windows": prim_audit, "pooled_n_races": sum(r.n_races for r in prim_reports),
            "pooled_n_days": ci.n_days, "sufficient": sufficient, "min_eval_days": min_days,
            "point": ci.point, "sample_ci": asdict(ci), "total_ci": asdict(total),
            "pooled_aux": {k: _weighted(prim_reports, k)
                           for k in ("top2_diff", "top3_diff", "cand_ece", "act_ece")},
            "recent": recent, "gate": {"sub_gates": gate.sub_gates, "adopted": gate.adopted},
            "per_window_decisions_reference_only": [r.decision for r in prim_reports],
        },
        "guard_full_info": {"windows": g1_audit, "ci": asdict(g1_ci), "decision": g1_dec,
                            "margin": guards["full_info"]["margin"], "pass": guard1},
        "guard_real_direction": {
            "window_from": str(g2_from), "window_to": str(latest),
            **{k: g2_audit[k] for k in ("race_id_set_hash", "n_races", "n_days")},
            "ci": asdict(g2_ci), "margin": guards["real_degraded_direction"]["margin"],
            "pass": guard2},
        "verdict": {"status": status, "adopt": status == "ADOPT",
                    "formula": "primary_pooled AND guard1 AND guard2",
                    "decision_reason": {"cause": cause, "sufficient": sufficient,
                                        "gate_adopted": gate.adopted, "guard1": guard1,
                                        "guard2": guard2}},
    }
    if args.smoke and cfg["smoke"].get("redact_effect_numbers", True):
        for k in ("primary", "guard_full_info", "guard_real_direction"):
            keep = ("windows", "pooled_n_days", "sufficient", "n_days", "window_from", "window_to")
            out[k] = {"REDACTED": "smoke run — effect numbers withheld by pre-registration",
                      "plumbing": {kk: vv for kk, vv in out[k].items() if kk in keep}}
        out["verdict"] = {"status": "SMOKE", "adopt": False, "formula": out["verdict"]["formula"]}
    with open(args.json_out, "w") as fh:
        json.dump(out, fh, indent=2, default=str)

    if args.smoke:
        print(f"SMOKE OK  elapsed={elapsed:.0f}s  wrote {args.json_out}  (numbers redacted)")
        return 0
    print(f"pooled: n_races={out['primary']['pooled_n_races']:,} n_days={ci.n_days} "
          f"point={ci.point:+.6f} sample CI[{ci.ci_low:+.6f},{ci.ci_high:+.6f}] "
          f"total CI[{total.ci_low:+.6f},{total.ci_high:+.6f}]  sufficient={sufficient}")
    print(f"gate: {gate.sub_gates} -> adopted={gate.adopted}")
    print(f"guard1 full-info: {g1_ci.point:+.6f} CI[{g1_ci.ci_low:+.6f},{g1_ci.ci_high:+.6f}] "
          f"-> {g1_dec} pass={guard1}")
    print(f"guard2 real {g2_from}..{latest}: {g2_ci.point:+.6f} "
          f"CI[{g2_ci.ci_low:+.6f},{g2_ci.ci_high:+.6f}] pass={guard2}")
    print(f"VERDICT = {status}  ({cause})  elapsed={elapsed/3600:.1f}h  wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
