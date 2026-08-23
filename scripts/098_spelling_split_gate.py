"""Feature 098 adoption gate: counterfactual race-class spelling simulation.

The frozen gate-config owns the cutoffs, scored windows, recipes, capacities, thresholds and
verdict formula. This driver builds one canonical simulation matrix, creates spelling-only A/B
arms for every cutoff, pools their paired race-level differences, then checks direction on a
separately-built raw real-window matrix. It never writes to the database.

No effect number is printed or written until primary, real-window guard and transportability have
all been computed. Smoke mode uses its config section, skips the real-window guard, and writes the
same structural artifact with every effect number set to null.

Usage:
    cd training && uv run python ../scripts/098_spelling_split_gate.py \
        --gate-config ../specs/098-race-class-spelling/gate-config.json \
        --gate-config-hash $(cat ../specs/098-race-class-spelling/gate-config.hash.txt) \
        --json ../specs/098-race-class-spelling/verdict.json
Smoke: add ``--smoke --json ../out/098-smoke.json``.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import math
import time
from dataclasses import asdict
from pathlib import Path

import pandas as pd
from horseracing_db.models import Race
from horseracing_db.session import create_db_engine
from horseracing_eval.bootstrap import inflate_for_seed_noise, race_day_cluster_bootstrap_ci_v1
from horseracing_eval.dataset import load_eval_races, population_masks
from horseracing_eval.decision import EVALUATION_CONTRACT_VERSION, assert_confirmatory
from horseracing_eval.gates import evaluate_core_gate, recent_window_guard
from horseracing_eval.paired import paired_eval
from horseracing_eval.splits import expanding_folds
from horseracing_eval.subgroups import FAIL, three_way
from horseracing_features.race_class_canon import CANONICAL_TABLE
from horseracing_features.registry import FEATURE_VERSION, RACE_CLASS_REPRESENTATION
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from horseracing_training.calib_split import CalibSplitFactory
from horseracing_training.predictor import LightGBMPredictor
from horseracing_training.recipe import ModelRecipe
from horseracing_training.spelling_gate import (
    FORMULA,
    leave_one_out_points,
    pool_diffs_by_day,
    transportability,
    verdict_precedence,
)
from horseracing_training.spelling_split import assert_arm_identity, make_arms

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"


def _date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def _recipe(arms: dict, *, label: str, rounds: int) -> ModelRecipe:
    recipe = arms["recipe"]
    params = dict(recipe.get("params") or {})
    params["n_estimators"] = rounds
    return ModelRecipe(
        objective=recipe["objective"],
        calibration="none",
        calib_frac=0.0,
        seed=int(recipe["seed"]),
        params=tuple(sorted(params.items())),
        weight_mask_rate=recipe.get("weight_mask_rate"),
        weight_mask_seed=recipe.get("weight_mask_seed"),
        drop_features=(),
        label=label,
    )


def _factories(session, cfg: dict, *, rounds: int, blocks: int):
    arms = cfg["arms"]
    kind = arms["recipe"]["kind"]
    assert kind == "oof_isotonic", f"unsupported frozen arm kind: {kind!r}"
    candidate = CalibSplitFactory(
        session,
        _recipe(arms, label="098:candidate:B", rounds=rounds),
        n_oof_blocks=blocks,
        method=kind.removeprefix("oof_"),
        require_sufficient=True,
    )
    active = CalibSplitFactory(
        session,
        _recipe(arms, label="098:active:A", rounds=rounds),
        n_oof_blocks=blocks,
        method=kind.removeprefix("oof_"),
        require_sufficient=True,
    )
    # Labels are deliberately distinct and participate in recipe_hash. Everything that changes
    # fitting behaviour must still be identical: the arms differ by injected matrix bytes only.
    cand_meta = {key: value for key, value in candidate.recipe_meta.items() if key != "label"}
    act_meta = {key: value for key, value in active.recipe_meta.items() if key != "label"}
    assert cand_meta == act_meta, "A/B behavioural recipes differ"
    assert candidate.recipe_hash != active.recipe_hash, "distinct A/B labels were not recorded"
    return candidate, active


def _allowed_simulation_rows(frame: pd.DataFrame, cutoff: dt.date) -> pd.Series:
    dates = pd.to_datetime(frame["race_date"], errors="raise").dt.date
    return (dates >= cutoff) & frame["race_class"].astype(object).isin(
        set(CANONICAL_TABLE.values())
    )


def _allowed_real_rows(frame: pd.DataFrame) -> pd.Series:
    return frame["race_class"].astype(object).isin(set(CANONICAL_TABLE))


def _restore_transformed_category(arm) -> None:
    # make_arms deliberately transforms through object dtype. LightGBM requires model inputs to
    # be categorical again; no downstream consumer currently performs that coercion for injected
    # matrices, so the driver closes the boundary before fitting.
    arm.frame["race_class"] = arm.frame["race_class"].astype("category")


def _run_pair(
    session,
    cfg: dict,
    *,
    matrix,
    mode: str,
    cutoff: dt.date | None,
    window_from: dt.date,
    window_to: dt.date,
    eval_races,
    rounds: int,
    blocks: int,
    b: int,
    seed: int,
    log_prefix: str,
):
    arm_a, arm_b = make_arms(matrix, mode=mode, cutoff=cutoff)
    transformed = arm_a if mode == "pseudo_split" else arm_b
    _restore_transformed_category(transformed)
    assert isinstance(arm_a.frame["race_class"].dtype, pd.CategoricalDtype)
    assert isinstance(arm_b.frame["race_class"].dtype, pd.CategoricalDtype)
    assert arm_a.frame is not arm_b.frame
    assert arm_a.feature_cols == arm_b.feature_cols
    assert arm_a.categorical_cols == arm_b.categorical_cols
    print(f"{log_prefix}: arms OK", flush=True)

    if mode == "pseudo_split":
        assert cutoff is not None
        allowed = _allowed_simulation_rows(arm_b.frame, cutoff)
    else:
        allowed = _allowed_real_rows(arm_a.frame)
    identity = assert_arm_identity(arm_a, arm_b, allowed_rows_mask=allowed)
    print(f"{log_prefix}: identity OK", flush=True)

    candidate, active = _factories(session, cfg, rounds=rounds, blocks=blocks)
    candidate._shared = arm_b
    active._shared = arm_a
    assert candidate._shared is arm_b
    assert active._shared is arm_a
    assert candidate._shared.frame is not active._shared.frame
    assert candidate._shared.feature_cols == active._shared.feature_cols
    assert candidate._shared.categorical_cols == active._shared.categorical_cols

    snapshot = {
        "driver": "098_spelling_split_gate",
        "arm_A": "split" if mode == "pseudo_split" else "raw",
        "arm_B": "canonical",
        "window": [str(window_from), str(window_to)],
        "cutoff": str(cutoff) if cutoff is not None else None,
        **identity,
    }
    report = paired_eval(
        candidate,
        active,
        eval_races,
        gate_config=cfg,
        first_valid_year=window_from.year,
        valid_from=window_from,
        bootstrap_seed=seed,
        bootstrap_b=b,
        num_threads=int(cfg["determinism"]["num_threads"]),
        subgroups=False,
        snapshot=snapshot,
    )
    n_all = sum(len(values) for values in report.diffs_by_day.values())
    n_nonzero = sum(value != 0.0 for values in report.diffs_by_day.values() for value in values)
    assert n_nonzero > 0, "arms produced identical predictions on every race — comparison invalid"
    print(
        f"{log_prefix}: fit OK ({n_nonzero}/{n_all} races with non-zero paired diff)",
        flush=True,
    )
    return report, identity


def _weighted(reports, key: str) -> float:
    n_races = sum(report.n_races for report in reports)
    if n_races == 0:
        raise AssertionError("cannot pool auxiliary metrics with no races")
    return sum(report.gate.reasons[key] * report.n_races for report in reports) / n_races


def _ci_is_runnable(ci) -> bool:
    return bool(
        not ci.no_decision
        and ci.ci_low is not None
        and ci.ci_high is not None
        and all(math.isfinite(value) for value in (ci.point, ci.ci_low, ci.ci_high))
    )


def _valid_eligible_races(eval_races, *, first_valid_year: int, valid_from: dt.date):
    valid = []
    for fold in expanding_folds(
        eval_races, first_valid_year=first_valid_year, valid_from=valid_from
    ):
        valid.extend(race for race in fold.valid if population_masks(race).eligible)
    return valid


def _nk_surrogate_strata(eval_races, diffs_by_day: dict, *, valid_from: dt.date) -> dict:
    eligible = _valid_eligible_races(
        eval_races, first_valid_year=valid_from.year, valid_from=valid_from
    )
    races_by_day: dict[str, list] = {}
    for race in eligible:
        day = race.context.race_date.isoformat()
        races_by_day.setdefault(day, []).append(race)
    assert set(races_by_day) == set(diffs_by_day), (
        "real-window diagnostic race-days do not align with paired diffs"
    )

    values: dict[str, list[float]] = {"0": [], "<50%": [], ">=50%": []}
    for day, diffs in diffs_by_day.items():
        races = races_by_day[day]
        assert len(races) == len(diffs), (
            f"real-window diagnostic race count differs on {day}: {len(races)} != {len(diffs)}"
        )
        for race, diff in zip(races, diffs, strict=True):
            horses = race.context.started_horses
            share = sum(horse.horse_id.startswith("nk:") for horse in horses) / len(horses)
            label = "0" if share == 0.0 else "<50%" if share < 0.5 else ">=50%"
            values[label].append(float(diff))
    return {
        label: {
            "point": sum(stratum) / len(stratum) if stratum else None,
            "n": len(stratum),
        }
        for label, stratum in values.items()
    }


def _redacted_ci(ci) -> dict:
    payload = asdict(ci)
    for key in ("point", "ci_low", "ci_high"):
        payload[key] = None
    return payload


def _real_output(
    *,
    cfg: dict,
    gate_hash: str,
    plan,
    reports,
    identities,
    pooled_ci,
    total_ci,
    sufficient: bool,
    recent: dict,
    gate,
    guard_from: dt.date,
    guard_to: dt.date,
    guard_report,
    guard_identity: dict,
    guard_ci,
    guard_decision: str,
    evidence_of_harm: bool,
    transport: dict,
    diagnostics: dict,
    verdict: dict,
    capacity: dict,
    elapsed: float,
) -> dict:
    per_cutoff = []
    for (cutoff, window_from, window_to), report, identity in zip(
        plan, reports, identities, strict=True
    ):
        per_cutoff.append(
            {
                "cutoff": str(cutoff),
                "window": [str(window_from), str(window_to)],
                "n_races": report.n_races,
                "n_days": report.bootstrap_ci["n_days"],
                "point": report.bootstrap_ci["point"],
                **{
                    key: identity[key]
                    for key in (
                        "race_class_hash_A",
                        "race_class_hash_B",
                        "n_rows_differing",
                    )
                },
            }
        )
    return {
        "evaluation_contract_version": EVALUATION_CONTRACT_VERSION,
        "artifact_kind": cfg["artifact_isolation"]["verdict_kind"],
        "eligible_for_verdict": False,
        "feature_adoption_eligible": True,
        "evidence_regime": cfg["primary_regime"],
        "model_training_regime": "real_canonical",
        "gate_config_hash": gate_hash,
        "recipe_hashes": {
            "candidate": reports[0].candidate_recipe_hash,
            "active": reports[0].active_recipe_hash,
        },
        "representation": {
            "feature_version": cfg["representation"]["feature_version"],
            "race_class_representation": cfg["representation"]["race_class_representation"],
            "table": cfg["representation"]["table"],
        },
        "capacity": capacity,
        "simulation": {
            "cutoffs": [str(cutoff) for cutoff, _, _ in plan],
            "scored_windows": [[str(start), str(end)] for _, start, end in plan],
            "per_cutoff": per_cutoff,
            "pooled": {
                "n_races": sum(report.n_races for report in reports),
                "n_days": pooled_ci.n_days,
                "sufficient": sufficient,
                "point": pooled_ci.point,
                "ci_sample": asdict(pooled_ci),
                "ci_inflated": asdict(total_ci),
                "recent": recent,
                "gate": {
                    "sub_gates": gate.sub_gates,
                    "adopted": gate.adopted,
                    "reasons": gate.reasons,
                },
            },
        },
        "guard_real_direction": {
            "window_from": str(guard_from),
            "window_to": str(guard_to),
            "race_set_hash": guard_report.race_id_set_hash,
            "n_races": guard_report.n_races,
            "n_days": guard_ci.n_days,
            "point": guard_ci.point,
            "ci_sample": asdict(guard_ci),
            "three_way": guard_decision,
            "evidence_of_harm": evidence_of_harm,
            "pass": not evidence_of_harm,
            **{
                key: guard_identity[key]
                for key in (
                    "race_class_hash_A",
                    "race_class_hash_B",
                    "n_rows_differing",
                )
            },
        },
        "transportability": transport,
        "diagnostics": diagnostics,
        "verdict": verdict,
        "note": ("reported as counterfactual robustness, not a reconstruction of past predictions"),
        "elapsed_s": round(elapsed),
    }


def _smoke_output(
    *,
    cfg: dict,
    gate_hash: str,
    plan,
    reports,
    identities,
    pooled_ci,
    total_ci,
    sufficient: bool,
    capacity: dict,
    elapsed: float,
) -> dict:
    per_cutoff = []
    for (cutoff, window_from, window_to), report, identity in zip(
        plan, reports, identities, strict=True
    ):
        per_cutoff.append(
            {
                "cutoff": str(cutoff),
                "window": [str(window_from), str(window_to)],
                "n_races": report.n_races,
                "n_days": report.bootstrap_ci["n_days"],
                "point": None,
                **{
                    key: identity[key]
                    for key in (
                        "race_class_hash_A",
                        "race_class_hash_B",
                        "n_rows_differing",
                    )
                },
            }
        )
    return {
        "evaluation_contract_version": EVALUATION_CONTRACT_VERSION,
        "artifact_kind": cfg["smoke"]["artifact_kind"],
        "eligible_for_verdict": bool(cfg["smoke"]["eligible_for_verdict"]),
        "feature_adoption_eligible": False,
        "evidence_regime": cfg["primary_regime"],
        "model_training_regime": "real_canonical",
        "gate_config_hash": gate_hash,
        "recipe_hashes": {
            "candidate": reports[0].candidate_recipe_hash,
            "active": reports[0].active_recipe_hash,
        },
        "representation": {
            "feature_version": cfg["representation"]["feature_version"],
            "race_class_representation": cfg["representation"]["race_class_representation"],
            "table": cfg["representation"]["table"],
        },
        "capacity": capacity,
        "simulation": {
            "cutoffs": [str(cutoff) for cutoff, _, _ in plan],
            "scored_windows": [[str(start), str(end)] for _, start, end in plan],
            "per_cutoff": per_cutoff,
            "pooled": {
                "n_races": sum(report.n_races for report in reports),
                "n_days": pooled_ci.n_days,
                "sufficient": sufficient,
                "point": None,
                "ci_sample": _redacted_ci(pooled_ci),
                "ci_inflated": _redacted_ci(total_ci),
                "recent": None,
                "gate": None,
            },
        },
        "guard_real_direction": {
            "skipped": True,
            "reason": "smoke mode exercises simulation plumbing only",
            "window_from": cfg["guards"]["real_direction"]["window_from"],
            "window_to": None,
            "race_set_hash": None,
            "n_races": 0,
            "n_days": 0,
            "point": None,
            "ci_sample": None,
            "three_way": None,
            "evidence_of_harm": None,
            "pass": None,
            "race_class_hash_A": None,
            "race_class_hash_B": None,
            "n_rows_differing": 0,
        },
        "transportability": {
            "per_cutoff_sign_ok": None,
            "loo_sign_ok": None,
            "real_not_contradicting": None,
            "ok": None,
        },
        "diagnostics": {
            "real_window_strata": {
                "status": "not_computed",
                "reason": "real-window guard is skipped in smoke mode",
            }
        },
        "verdict": {
            "status": "SMOKE",
            "adopt": False,
            "formula": FORMULA,
            "decision_reason": {"cause": "smoke_plumbing_only"},
        },
        "note": ("reported as counterfactual robustness, not a reconstruction of past predictions"),
        "elapsed_s": round(elapsed),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate-config", required=True)
    parser.add_argument("--gate-config-hash", required=True)
    parser.add_argument("--json", dest="json_out", required=True)
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="plumbing only: config-owned capacity, real guard skipped, effects redacted",
    )
    args = parser.parse_args()

    cfg = json.loads(Path(args.gate_config).read_text())
    envelope = cfg["eval_window"]
    assert_confirmatory(
        cfg,
        expected_hash=args.gate_config_hash,
        eval_window={"from": envelope["from"], "to": envelope["to"]},
    )
    assert cfg["evaluation_contract_version"] == EVALUATION_CONTRACT_VERSION
    assert cfg["representation"]["feature_version"] == FEATURE_VERSION
    assert cfg["representation"]["race_class_representation"] == RACE_CLASS_REPRESENTATION
    assert cfg["representation"]["table"] == CANONICAL_TABLE
    assert cfg["bootstrap"]["block"] == "race_day"
    assert cfg["guards"]["real_direction"]["mode"] == "evidence_of_harm"
    assert float(cfg["determinism"]["tolerance"]) >= 0.0
    assert {
        "winner_nll",
        "top2_top3_diff",
        "ece",
    } <= cfg["pooling"].keys()

    simulation = cfg["simulation"]
    envelope_from, envelope_to = _date(envelope["from"]), _date(envelope["to"])
    for window_from, window_to in simulation["scored_windows"]:
        assert envelope_from <= _date(window_from) <= _date(window_to) <= envelope_to, (
            "scored window outside the confirmatory envelope"
        )

    eval_cfg = cfg
    if args.smoke:
        smoke = cfg["smoke"]
        assert smoke["redact_effect_numbers"] is True
        plan = [
            (
                _date(smoke["cutoff"]),
                _date(smoke["scored_window"][0]),
                _date(smoke["scored_window"][1]),
            )
        ]
        rounds = int(smoke["rounds"])
        blocks = int(smoke["n_oof_blocks"])
        b = int(smoke["b"])
        eval_cfg = copy.deepcopy(cfg)
        # paired_eval and recent_window_guard prefer the config value over their call argument.
        eval_cfg["bootstrap"]["b"] = b
    else:
        plan = [
            (_date(cutoff), _date(window[0]), _date(window[1]))
            for cutoff, window in zip(
                simulation["cutoffs"], simulation["scored_windows"], strict=True
            )
        ]
        rounds = int(cfg["arms"]["recipe"]["params"]["n_estimators"])
        blocks = int(cfg["arms"]["recipe"]["n_oof_blocks"])
        b = int(cfg["bootstrap"]["b"])
    seed = int(cfg["bootstrap"]["seed"])
    alpha = float(cfg["bootstrap"]["alpha"])
    capacity = {"rounds": rounds, "n_oof_blocks": blocks, "b": b}

    print(
        f"contract {EVALUATION_CONTRACT_VERSION} OK  hash={args.gate_config_hash[:12]}  "
        f"{'SMOKE' if args.smoke else 'REAL'}",
        flush=True,
    )
    engine = create_db_engine(DB)
    started = time.time()
    with Session(engine) as session:
        # Primary simulation: exactly one explicit canonical-v1 matrix build for all cutoffs.
        tmp = LightGBMPredictor(
            session,
            objective=cfg["arms"]["recipe"]["objective"],
            calibration="none",
            race_class_representation=cfg["representation"]["race_class_representation"],
        )
        assert tmp.race_class_representation is not None
        assert tmp.use_materialized is False, "simulation matrix must be built from the session"
        simulation_matrix = tmp._ensure_data()
        assert tmp.race_class_representation_ == "canonical-v1"

        reports, identities = [], []
        for cutoff, window_from, window_to in plan:
            eval_races = load_eval_races(session, end_date=window_to)
            report, identity = _run_pair(
                session,
                eval_cfg,
                matrix=simulation_matrix,
                mode="pseudo_split",
                cutoff=cutoff,
                window_from=window_from,
                window_to=window_to,
                eval_races=eval_races,
                rounds=rounds,
                blocks=blocks,
                b=b,
                seed=seed,
                log_prefix=f"cutoff {cutoff}",
            )
            reports.append(report)
            identities.append(identity)

        pooled_parts = [report.diffs_by_day for report in reports]
        pooled = pool_diffs_by_day(pooled_parts)
        pooled_ci = race_day_cluster_bootstrap_ci_v1(pooled, b=b, seed=seed, alpha=alpha)
        seed_noise = cfg["seed_noise"]
        total_ci = inflate_for_seed_noise(
            pooled_ci,
            sd_fold=float(seed_noise["sd_fold"]),
            n_folds=int(seed_noise["n_folds"]),
            k_seeds=int(seed_noise["k_seeds"]),
            alpha=alpha,
        )
        recent = recent_window_guard(
            pooled,
            cfg=eval_cfg,
            max_date=_date(cfg["recent_guard"]["max_date"]),
        )
        gate = evaluate_core_gate(
            diff=pooled_ci.point,
            ci_low=total_ci.ci_low,
            ci_high=total_ci.ci_high,
            recent=recent,
            top2_diff=_weighted(reports, "top2_diff"),
            top3_diff=_weighted(reports, "top3_diff"),
            cand_ece=_weighted(reports, "cand_ece"),
            act_ece=_weighted(reports, "act_ece"),
            cfg=eval_cfg,
        )
        min_days = int(envelope["min_eval_days"])
        sufficient = pooled_ci.n_days >= min_days

        if args.smoke:
            guard_report = guard_identity = guard_ci = guard_decision = None
            evidence_of_harm = None
            transport = None
            diagnostics = None
            verdict = None
        else:
            # The raw guard is a separate second matrix build. Release the simulation frame first.
            del tmp, simulation_matrix
            raw_tmp = LightGBMPredictor(
                session,
                objective=cfg["arms"]["recipe"]["objective"],
                calibration="none",
                race_class_representation="raw",
            )
            assert raw_tmp.race_class_representation is not None
            assert raw_tmp.use_materialized is False, "real guard matrix must come from session"
            raw_matrix = raw_tmp._ensure_data()
            assert raw_tmp.race_class_representation_ == "raw"

            guard_cfg = cfg["guards"]["real_direction"]
            guard_from = _date(guard_cfg["window_from"])
            latest = session.scalar(select(func.max(Race.race_date)))
            assert isinstance(latest, dt.date), "database has no race_date for the real guard"
            real_races = load_eval_races(session, end_date=latest)
            guard_report, guard_identity = _run_pair(
                session,
                cfg,
                matrix=raw_matrix,
                mode="canonicalise",
                cutoff=None,
                window_from=guard_from,
                window_to=latest,
                eval_races=real_races,
                rounds=rounds,
                blocks=blocks,
                b=b,
                seed=seed,
                log_prefix="guard real direction",
            )
            guard_ci = race_day_cluster_bootstrap_ci_v1(
                guard_report.diffs_by_day, b=b, seed=seed, alpha=alpha
            )
            margin = float(guard_cfg["margin"])
            guard_decision = three_way(
                guard_ci.ci_low, guard_ci.ci_high, margin, point=guard_ci.point
            )
            evidence_of_harm = bool(guard_ci.ci_low is not None and guard_ci.ci_low > margin)
            assert evidence_of_harm is (guard_decision == FAIL)

            loo_points = leave_one_out_points(pooled_parts)
            transport = transportability(
                [float(report.bootstrap_ci["point"]) for report in reports],
                loo_points,
                pooled_point=pooled_ci.point,
                real_ci_low=guard_ci.ci_low,
            )
            diagnostics = {
                "real_window_strata": {
                    "listed_open_history": {
                        "status": "not_computed",
                        "reason": (
                            "PairedReport and EvalRace do not expose prior race-class history; "
                            "a separate history join would be required"
                        ),
                    },
                    "nk_surrogate_share": _nk_surrogate_strata(
                        real_races, guard_report.diffs_by_day, valid_from=guard_from
                    ),
                }
            }
            runnable = (
                _ci_is_runnable(pooled_ci)
                and _ci_is_runnable(total_ci)
                and _ci_is_runnable(guard_ci)
            )
            verdict = verdict_precedence(
                runnable=runnable,
                sufficient=sufficient,
                primary_pooled=gate.adopted,
                guard_real_direction=not evidence_of_harm,
                transportable=transport["ok"],
            )

    elapsed = time.time() - started
    print("--- all components computed ---", flush=True)
    if args.smoke:
        output = _smoke_output(
            cfg=cfg,
            gate_hash=args.gate_config_hash,
            plan=plan,
            reports=reports,
            identities=identities,
            pooled_ci=pooled_ci,
            total_ci=total_ci,
            sufficient=sufficient,
            capacity=capacity,
            elapsed=elapsed,
        )
    else:
        candidate_hashes = {report.candidate_recipe_hash for report in [*reports, guard_report]}
        active_hashes = {report.active_recipe_hash for report in [*reports, guard_report]}
        assert len(candidate_hashes) == len(active_hashes) == 1
        output = _real_output(
            cfg=cfg,
            gate_hash=args.gate_config_hash,
            plan=plan,
            reports=reports,
            identities=identities,
            pooled_ci=pooled_ci,
            total_ci=total_ci,
            sufficient=sufficient,
            recent=recent,
            gate=gate,
            guard_from=guard_from,
            guard_to=latest,
            guard_report=guard_report,
            guard_identity=guard_identity,
            guard_ci=guard_ci,
            guard_decision=guard_decision,
            evidence_of_harm=evidence_of_harm,
            transport=transport,
            diagnostics=diagnostics,
            verdict=verdict,
            capacity=capacity,
            elapsed=elapsed,
        )
    output_path = Path(args.json_out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, default=str) + "\n")

    if args.smoke:
        print(f"SMOKE OK  wrote {args.json_out}  (effect numbers redacted)")
        return 0
    print(
        f"pooled: n_races={output['simulation']['pooled']['n_races']:,} "
        f"n_days={pooled_ci.n_days} point={pooled_ci.point:+.6f} "
        f"sample CI[{pooled_ci.ci_low:+.6f},{pooled_ci.ci_high:+.6f}] "
        f"inflated CI[{total_ci.ci_low:+.6f},{total_ci.ci_high:+.6f}] "
        f"sufficient={sufficient}"
    )
    print(f"gate: {gate.sub_gates} -> adopted={gate.adopted}")
    print(
        f"guard real {guard_from}..{latest}: {guard_ci.point:+.6f} "
        f"CI[{guard_ci.ci_low:+.6f},{guard_ci.ci_high:+.6f}] "
        f"three_way={guard_decision} evidence_of_harm={evidence_of_harm}"
    )
    print(f"transportability: {transport}")
    print(
        f"VERDICT = {verdict['status']}  ({verdict['decision_reason']['cause']})  "
        f"elapsed={elapsed / 3600:.1f}h  wrote {args.json_out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
