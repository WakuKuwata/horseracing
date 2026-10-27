"""132: four frozen mixtures of cached126 forecasts; no model/coefficient fitting.

The126 probabilities are full-information, seed42, OOF-isotonic outputs.
Transferring125's annual correction is an explicit NEW candidate, not a claim
that those coefficients were fitted to126. Existing recipe checks stay intact.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import pickle
import platform
import sys
import time
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "serving/src"))
from horseracing_eval.dataset import population_masks
from horseracing_serving.mixture_correction import JOINT_TERMS, correct_member_predictions

CUTOFF = dt.date(2026, 9, 6)
EVAL_END = dt.date(2026, 8, 23)
YEARS = tuple(range(2020, 2027))
SOURCE126 = ROOT / "artifacts/126-market-feature-full-quality"
SPEC126 = ROOT / "specs/126-market-feature-full-quality"
SOURCE125 = ROOT / "artifacts/125-joint-residual-stack"
OUTPUT = ROOT / "artifacts/132-accuracy-first-batch/colsample"
ARMS = {
    "add_raw_1of7": {"transfer": False, "operation": "add", "weight": [1, 7]},
    "add_transferred_1of14": {"transfer": True, "operation": "add", "weight": [1, 14]},
    "add_transferred_1of7": {"transfer": True, "operation": "add", "weight": [1, 7]},
    "replace_joint42": {"transfer": True, "operation": "replace", "member_index": 0,
                        "weight": [1, 6]},
}
REGISTERED_ARM_MAP = {
    "raw_add": "add_raw_1of7",
    "corrected_small_add": "add_transferred_1of14",
    "corrected_equal_add": "add_transferred_1of7",
    "replace_joint42": "replace_joint42",
}
POLICY = {
    "artifact_kind": "accuracy132_cached_colsample_protocol", "data_cutoff": str(CUTOFF),
    "evaluation_window": {"from": "2020-01-01", "to": str(EVAL_END)},
    "regime": "full_information", "baseline": "125_new_joint_mixed6_annual",
    "member_order": ["joint42", "joint43", "joint44", "anchor42", "anchor43", "anchor44"],
    "candidate_seed": 42, "arms": ARMS, "coefficient_source": "125:seed42:same_evaluation_year",
    "coefficient_mode": "frozen_transfer_not_refit", "post_mixture_processing": None,
    "all_heads_same_weights": True, "new_booster_fits": 0, "new_coefficient_fits": 0,
    "bootstrap": {"b": 4000, "seed": 20260907, "alpha": .0125, "block": "race_day"},
    "quality": {"top2_logloss_margin": .0005, "top3_logloss_margin": .0005,
                "ece_margin": .001, "ece_ceiling": .05, "recent_margin": .005,
                "horse_subgroup_margin": .001, "critical": ["canonical", "nk", "recent_year_only"]},
    "selection": "Report every fixed arm. Negative NLL and no quality FAIL may be retained as uncertain research.",
    "can_adopt": False, "eligible_for_verdict": False,
}


def read_json(path):
    return json.loads(Path(path).read_text())


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def canonical(value):
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def stable_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as dest:
        json.dump(value, dest, ensure_ascii=False, allow_nan=False, indent=2)
        dest.write("\n")


def validate_day(day, *, latest=CUTOFF):
    if isinstance(day, dt.datetime) or not isinstance(day, dt.date):
        raise ValueError("Known calendar race date required")
    if not dt.date(2007, 1, 1) <= day <= latest:
        raise ValueError("Race date outside the authorized data cutoff")


def validate_experiment(config):
    expected_arms = {
        "raw_add": {"candidate_weight": "1/7"},
        "corrected_small_add": {"candidate_weight": "1/14"},
        "corrected_equal_add": {"candidate_weight": "1/7"},
        "replace_joint42": {"member": "joint-42", "weight": "1/6"},
    }
    if (config.get("cutoff") != str(CUTOFF) or config.get("source_end") != str(EVAL_END)
            or config.get("can_adopt") is not False or config.get("eligible_for_verdict") is not False
            or config.get("bootstrap") != {key: POLICY["bootstrap"][key] for key in ("alpha", "b", "seed")}
            or config.get("colsample") != {"regime": "full", "arms": expected_arms,
                "coefficient_transfer": "125 seed42 corresponding annual joint coefficients; recompute centered_logp on126"}):
        raise ValueError("132 registered colsample experiment differs")
    for registered, internal in REGISTERED_ARM_MAP.items():
        definition = ARMS[internal]
        expected = expected_arms[registered]
        if list(map(int, expected.get("candidate_weight", expected.get("weight")).split("/"))) != definition["weight"]:
            raise ValueError("Registered/internal mixture weights differ")
    if (set(REGISTERED_ARM_MAP.values()) != set(ARMS)
            or ARMS["replace_joint42"]["member_index"] != 0
            or ARMS["add_raw_1of7"]["transfer"]
            or not all(ARMS[arm]["transfer"] for arm in ARMS if arm != "add_raw_1of7")):
        raise ValueError("Registered/internal mixture operation differs")


def head_array(values, *, n=None):
    a = np.asarray(values, dtype=np.float64)
    if (a.ndim != 2 or a.shape[1] != 3 or a.shape[0] == 0
            or (n is not None and a.shape[0] != n)):
        raise ValueError("Complete n-horse, three-head array required")
    if (not np.isfinite(a).all() or (a < 0).any() or (a > 1 + 1e-10).any()
            or (a[:, 0] <= 0).any() or (np.diff(a, axis=1) < -1e-10).any()
            or not np.allclose(a.sum(0), [min(k, len(a)) for k in (1, 2, 3)], atol=1e-8, rtol=0)):
        raise ValueError("Invalid probability domain/order/head sums")
    return a


def prediction_array(predictions, ids):
    if len(ids) != len(set(ids)) or list(predictions) != list(ids):
        raise ValueError("Cache horse identity/order differs")
    return head_array([[predictions[h].win, predictions[h].top2, predictions[h].top3] for h in ids], n=len(ids))


def mix_arm(members, raw, transferred, arm):
    """Explicit positive member weights, identical across all probability heads."""
    if arm not in ARMS:
        raise ValueError("Unregistered mixture arm")
    x = np.asarray(members, dtype=np.float64)
    if x.ndim != 3 or x.shape[0] != 6:
        raise ValueError("Exactly six existing members required")
    for member in x:
        head_array(member)
    c = head_array(transferred if ARMS[arm]["transfer"] else raw, n=x.shape[1])
    definition = ARMS[arm]
    if definition["operation"] == "replace":
        # No subtraction from the existing average; all six members are explicit.
        revised = x.copy()
        revised[definition["member_index"]] = c
        result = revised.mean(axis=0, dtype=np.float64)
        expected_loss = -np.log(revised[:, :, 0]).mean(axis=0)
    else:
        numerator, denominator = definition["weight"]
        w = numerator / denominator
        result = (1. - w) * x.mean(axis=0, dtype=np.float64) + w * c
        expected_loss = (1. - w) * (-np.log(x[:, :, 0])).mean(axis=0) + w * (-np.log(c[:, 0]))
    head_array(result, n=x.shape[1])
    if np.max(-np.log(result[:, 0]) - expected_loss) > 1e-12:
        raise ValueError("Weighted Jensen invariant failed")
    return result


def validate_coefficients(coef, source_freeze):
    if (coef.get("training_seed") != 42 or coef.get("base_arm") != "pruning125_raw"
            or coef.get("base_recipe_hash") != source_freeze["base_recipe_hashes"]["42"]
            or coef.get("coefficient_order") != JOINT_TERMS
            or coef.get("selected") != ["prior_gap", "global_temperature"]
            or set(coef.get("gammas", {})) != {str(y) for y in YEARS}
            or coef.get("can_adopt") is not False or coef.get("eligible_for_verdict") is not False):
        raise ValueError("Original125 correction provenance differs")
    diagnostics = coef.get("fit_diagnostics", [])
    if len(diagnostics) != len(YEARS):
        raise ValueError("Annual coefficient training dates missing")
    for year, diagnostic in zip(YEARS, diagnostics, strict=True):
        if diagnostic.get("eval_year") != year:
            raise ValueError("Coefficient evaluation year differs")
        fit_end = dt.date.fromisoformat(diagnostic["fit_last_day"])
        valid_start = dt.date.fromisoformat(diagnostic["eval_first_day"])
        validate_day(fit_end); validate_day(valid_start)
        if fit_end >= dt.date(year, 1, 1) or valid_start.year != year or fit_end >= valid_start:
            raise ValueError("Future-fitted coefficient cannot be transferred")
        beta = np.asarray(coef["gammas"][str(year)], dtype=float)
        if beta.shape != (5,) or not np.isfinite(beta).all() or 1. + beta[-1] <= 0:
            raise ValueError("Invalid coefficient/temperature")


def transferred_predictions(record, raw, coef):
    validate_day(record.race_date, latest=EVAL_END)
    if record.year != record.race_date.year or record.year not in YEARS:
        raise ValueError("Correction evaluation year differs")
    beta = coef["gammas"][str(record.year)]
    pred = correct_member_predictions(record.ids, raw[:, 0], record.correction_inputs, JOINT_TERMS, beta)
    return prediction_array(pred, record.ids)


def source_manifest():
    """Read a bounded whitelist and verify receipts before deserializing caches."""
    frozen = read_json(SOURCE126 / "run-freeze.json")
    audit = read_json(SPEC126 / "evidence/independent-review.json")
    report = read_json(SPEC126 / "evidence/full-colsample_07.json")
    cfg = read_json(SPEC126 / "gate-config.json")
    if (frozen.get("selected_candidates") != ["colsample_07"]
            or audit.get("status") != "PASS"
            or audit.get("run_freeze_sha256") != digest(SOURCE126 / "run-freeze.json")
            or audit.get("additional_fits") != 0 or audit.get("can_adopt") is not False
            or frozen.get("can_adopt") is not False or report.get("can_adopt") is not False):
        raise ValueError("Completed research126 independent audit required")
    if cfg["eval_window"] != {"from": "2020-01-01", "to": str(EVAL_END), "min_eval_days": 400}:
        raise ValueError("126 evaluation window changed")
    days = [dt.date.fromisoformat(v[1]) for v in frozen["population"]["eligible_id_days"]]
    if (len(days) != 22990 or len(set(days)) != 715 or max(days) != EVAL_END
            or min(days) != dt.date(2020, 1, 5) or max(days) > CUTOFF):
        raise ValueError("126 population/cutoff differs")
    paths = [SOURCE126 / "run-freeze.json", SPEC126 / "gate-config.json", SPEC126 / "gate-config.hash.txt",
             SPEC126 / "evidence/independent-review.json", SPEC126 / "evidence/independent-review.py",
             SPEC126 / "evidence/full-colsample_07.json", SOURCE126 / "colsample_07-evidence.json",
             SOURCE126 / "colsample_07-receipt.json", SPEC126 / "verdict.json",
             SOURCE125 / "run-freeze.json", SOURCE125 / "seed-42/coefficients.json",
             SOURCE125 / "seed-42/coefficients-receipt.json"]
    if audit["method_sha256"] != digest(SPEC126 / "evidence/independent-review.py"):
        raise ValueError("126 independent audit method changed")
    report_binding = audit["report_hashes"]["colsample_07"]
    if (report_binding["report_sha256"] != digest(SPEC126 / "evidence/full-colsample_07.json")
            or report_binding["evidence_sha256"] != digest(SOURCE126 / "colsample_07-evidence.json")):
        raise ValueError("126 report/evidence digest changed")
    jobs = sorted(frozen["jobs"], key=lambda job: job["year"])
    if [job["year"] for job in jobs] != list(YEARS) or any(job["arm"] != "colsample_07" for job in jobs):
        raise ValueError("Seven colsample126 jobs required")
    for job in jobs:
        path = SOURCE126 / "cache" / (job["key"] + ".pkl")
        receipt = SOURCE126 / "prefill" / (job["key"] + ".json")
        row = read_json(receipt)
        if (row["job"] != job or row["run_freeze_sha256"] != audit["run_freeze_sha256"]
                or row["cache_sha256"] != digest(path)
                or row["cache_sha256"] != audit["cache_hashes"][f"colsample_07:{job['year']}"]
                or job["recipe_hash"] != frozen["arm_recipe_hashes"]["colsample_07"]):
            raise ValueError("126 cached job/receipt/SHA changed")
        paths.extend([path, receipt])
    coef = read_json(SOURCE125 / "seed-42/coefficients.json")
    receipt = read_json(SOURCE125 / "seed-42/coefficients-receipt.json")
    if receipt != {"sha256": digest(SOURCE125 / "seed-42/coefficients.json"),
                   "freeze_sha256": digest(SOURCE125 / "run-freeze.json"), "seed": 42}:
        raise ValueError("125 annual coefficient receipt changed")
    validate_coefficients(coef, read_json(SOURCE125 / "run-freeze.json"))
    return frozen, report, coef, {str(p): digest(p) for p in paths}


def validate_payload(payload, job, report, frozen):
    expected_meta = canonical(report["candidate_recipe_meta"])
    if (payload.get("key") != job["key"] or payload.get("train_hash") != job["train_hash"]
            or canonical(payload.get("recipe_meta")) != expected_meta
            or payload.get("feature_columns") != frozen["arm_columns"]["colsample_07"]
            or payload.get("feature_columns") != job["feature_columns"]
            or len(payload["feature_columns"]) != 125
            or payload.get("feature_dtypes") != job["feature_dtypes"]):
        raise ValueError("126 payload recipe/train/columns differ")
    meta = payload["recipe_meta"]
    if (meta.get("seed") != 42 or meta.get("objective") != "pl_topk" or meta.get("arm") != "oof_isotonic"
            or meta.get("n_oof_blocks") != 8 or meta.get("weight_mask_rate") != .5
            or meta.get("weight_mask_seed") != 20260810 or meta.get("te_smoothing") != 10.):
        raise ValueError("126 fixed training/calibration regime differs")
    params = payload.get("actual_params", {})
    # This existing cache records the wrapper's parameter dictionary as binary;
    # the effective custom PL objective is bound by recipe_meta above.
    expected_params = {"objective": "binary", "n_estimators": 900, "learning_rate": .05,
                       "num_leaves": 31, "min_child_samples": 20, "subsample": 1.,
                       "colsample_bytree": .7, "reg_lambda": 1.}
    oof = payload.get("oof_info", {})
    if params != expected_params or oof.get("sufficient") is not True or oof.get("method") != "isotonic":
        raise ValueError("126 actual parameters/OOF differ")
    oof_end = dt.date.fromisoformat(oof["oof_pred_through"])
    validate_day(oof_end)
    if oof_end >= dt.date(job["year"], 1, 1):
        raise ValueError("126 calibration is not prior-year")


def load_colsample(records, frozen, report):
    all_predictions, all_ids = {}, {record.race_id for record in records}
    if len(all_ids) != len(records) or len(records) != 23030:
        raise ValueError("Exact common research population required")
    for job in sorted(frozen["jobs"], key=lambda row: row["year"]):
        path = SOURCE126 / "cache" / (job["key"] + ".pkl")
        with path.open("rb") as source:
            payload = pickle.load(source)
        validate_payload(payload, job, report, frozen)
        year_records = [record for record in records if record.year == job["year"]]
        expected_ids = [record.race_id for record in year_records]
        if list(payload["predictions"]) != expected_ids:
            raise ValueError("126 cache race population/order differs")
        for record in year_records:
            validate_day(record.race_date, latest=EVAL_END)
            all_predictions[record.race_id] = prediction_array(payload["predictions"][record.race_id], record.ids)
    if set(all_predictions) != all_ids:
        raise ValueError("126 race predictions incomplete")
    return all_predictions


@contextmanager
def prohibit_training():
    """A cache failure must never fall back to an expensive fit."""
    def forbidden(*args, **kwargs):
        raise RuntimeError("132 colsample is cache-only; training is prohibited")
    import lightgbm as lgb
    with patch.object(lgb, "train", side_effect=forbidden), \
         patch.object(lgb.LGBMModel, "fit", side_effect=forbidden):
        yield


def day_ci(rows, *, margin=None):
    """Bootstrap ratios from per-day numerator/denominator sufficient statistics."""
    groups = {}
    for day, total, count in rows:
        sums = groups.setdefault(str(day), [0., 0])
        sums[0] += float(total); sums[1] += int(count)
    groups = {day: values for day, values in groups.items() if values[1]}
    if len(groups) < 2:
        return {"state": "MISSING", "n_days": len(groups), "n_items": sum(v[1] for v in groups.values())}
    a = np.asarray([groups[day] for day in sorted(groups)], dtype=float)
    rng = np.random.default_rng(POLICY["bootstrap"]["seed"])
    result = np.empty(POLICY["bootstrap"]["b"])
    for i in range(len(result)):
        ix = rng.integers(len(a), size=len(a))
        selected = a[ix].sum(0)
        result[i] = selected[0] / selected[1]
    lo, hi = np.quantile(result, [.00625, .99375])
    point = a[:, 0].sum() / a[:, 1].sum()
    out = {"point": float(point), "ci_low": float(lo), "ci_high": float(hi),
           "n_days": len(a), "n_items": int(a[:, 1].sum()), "interval": "conditional_sample"}
    if margin is not None:
        out.update(margin=margin, state="PASS" if hi < margin else "FAIL" if lo > margin else "NOT_PROVEN")
    return out


def quality_diagnostics(records, baseline, candidate):
    rows = {name: [] for name in ("all", "recent_3y", "recent_5y", "canonical", "nk", "recent_year_only", "2026_nakayama")}
    annual = {str(year): [] for year in YEARS}
    for record in records:
        rid, day, ids = record.race_id, record.race_date, record.ids
        pop = population_masks(record.er)
        if pop.eligible:
            i = ids.index(pop.winner_horse_id)
            delta = float(-np.log(candidate[rid][i, 0]) + np.log(baseline[rid][i, 0]))
            row = (day, delta, 1)
            rows["all"].append(row); annual[str(day.year)].append(row)
            for n in (3, 5):
                if day >= dt.date(EVAL_END.year - n, EVAL_END.month, EVAL_END.day):
                    rows[f"recent_{n}y"].append(row)
            if day.year == 2026:
                rows["recent_year_only"].append(row)
                if rid[4:6] == "06":
                    rows["2026_nakayama"].append(row)
        # Preserve the old research subgroup estimand: all started rows, including
        # incomplete results, with its limitations explicitly reported below.
        labels = np.asarray([pop.started_win[h] for h in ids], dtype=float)
        a, b = [np.clip(values[rid][:, 0], 1e-15, 1 - 1e-15) for values in (candidate, baseline)]
        loss_diff = -labels * np.log(a) - (1-labels) * np.log1p(-a) + labels * np.log(b) + (1-labels) * np.log1p(-b)
        for group in ("canonical", "nk"):
            mask = np.asarray([str(h).startswith("nk:") == (group == "nk") for h in ids])
            rows[group].append((day, float(loss_diff[mask].sum()), int(mask.sum())))
    return {
        "all": day_ci(rows["all"]),
        "recent": {name: day_ci(rows[name], margin=.005) for name in ("recent_3y", "recent_5y")},
        "critical": {name: day_ci(rows[name], margin=.005 if name == "recent_year_only" else .001)
                     for name in ("canonical", "nk", "recent_year_only")},
        "fixed_diagnostics": {"2026_nakayama": day_ci(rows["2026_nakayama"])},
        "yearly": {year: day_ci(values) for year, values in annual.items()},
        "horse_subgroup_population": "legacy_all_started_including_incomplete_results_not_main_metric_population",
    }


def research_disposition(baseline, candidate, diagnostics):
    delta = candidate["winner_nll"] - baseline["winner_nll"]
    heads = candidate["heads"]; base_heads = baseline["heads"]
    mean_ece = float(np.mean([heads[head]["ece"] for head in ("win", "top2", "top3")]))
    base_mean_ece = float(np.mean([base_heads[head]["ece"] for head in ("win", "top2", "top3")]))
    numerical = {
        "top2_noninferior": heads["top2"]["logloss"] - base_heads["top2"]["logloss"] <= .0005,
        "top3_noninferior": heads["top3"]["logloss"] - base_heads["top3"]["logloss"] <= .0005,
        "calibration_noninferior": mean_ece - base_mean_ece <= .001,
        "calibration_ceiling": mean_ece <= .05,
    }
    states = [v["state"] for kind in ("recent", "critical") for v in diagnostics[kind].values()]
    quality = all(numerical.values()) and not any(state in ("FAIL", "MISSING") for state in states)
    return {"state": "QUALITY_REVIEW_REQUIRED" if not quality else "DEFER" if delta >= 0 else "RETAIN_UNCERTAIN",
            "winner_nll_diff": delta, "numeric_quality": numerical,
            "calibration": {"baseline_mean_ece": base_mean_ece, "candidate_mean_ece": mean_ece,
                            "mean_ece_diff": mean_ece - base_mean_ece},
            "subgroup_assurance": "PASS" if all(state == "PASS" for state in states) else "NOT_PROVEN",
            "can_adopt": False, "eligible_for_verdict": False,
            "reason": "Conditional historical research; selection and ensemble retraining uncertainty are not covered by this CI."}


def _assert_unchanged(files):
    for path, expected in files.items():
        if digest(path) != expected:
            raise ValueError(f"Frozen input changed: {path}")


def run(output=OUTPUT):
    import accuracy_batch_common as common

    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("Preserve previous or partial132 colsample run; no automatic overwrite")
    start = time.monotonic()
    config_path = ROOT / "specs/132-accuracy-first-batch/experiment.json"
    config = read_json(config_path)
    validate_experiment(config)
    frozen126, report126, coef, files = source_manifest()
    from horseracing_eval import dataset, metrics
    from horseracing_serving import mixture_correction
    from horseracing_training import predictor
    common_paths = [common.SNAPSHOT, common.SNAPSHOT.with_suffix(".json"), common.SOURCE / "run-freeze.json"]
    for member in common.MEMBER_IDS:
        for year in YEARS:
            common_paths.extend([common.SOURCE / "cache" / f"{member}-{year}.pkl",
                                 common.SOURCE / "receipts" / f"{member}-{year}.json"])
    for path in [Path(__file__), ROOT / "scripts/tests/test_accuracy_colsample_recheck.py", Path(common.__file__),
                 config_path, *[Path(module.__file__) for module in (dataset, metrics, mixture_correction, predictor)],
                 *common_paths]:
        files[str(path)] = digest(path)
    freeze = {"protocol": POLICY, "protocol_sha256": stable_hash(POLICY), "sources": files,
              "experiment_sha256": digest(config_path), "config_hash": stable_hash(config),
              "registered_arm_map": REGISTERED_ARM_MAP,
              "coefficient_transfer": {"source_recipe_hash": coef["base_recipe_hash"],
                  "target_recipe_hash": frozen126["arm_recipe_hashes"]["colsample_07"],
                  "source_sha256": files[str(SOURCE125 / "seed-42/coefficients.json")],
                  "fit_diagnostics": coef["fit_diagnostics"], "fitted_to_target": False},
              "runtime": {"python": platform.python_version(), "numpy": np.__version__},
              "frozen_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "can_adopt": False, "eligible_for_verdict": False}
    write_json(output / "run-freeze.json", freeze)
    with prohibit_training():
        inputs = common.load_frozen_inputs()
        if inputs.freeze130["coefficients"]["joint-42"] != coef["gammas"]:
            raise ValueError("126 transfer coefficients differ from common125 seed42 annual coefficients")
        records = list(common.iter_mixed_records(inputs, regime="full"))
        if len(records) != 23030 or max(record.race_date for record in records) != EVAL_END:
            raise ValueError("Common mixed6 population differs")
        for record in records:
            validate_day(record.race_date, latest=EVAL_END)
        baseline = {record.race_id: head_array(record.heads) for record in records}
        raw = load_colsample(records, frozen126, report126)
        base_score = common.summarize_predictions(records, baseline)
        raw_score = common.summarize_predictions(records, raw)
        if not math.isclose(base_score["winner_nll"], 2.039080450, abs_tol=5e-10, rel_tol=0):
            raise ValueError("Original125 mixed6 winner NLL not reproduced")
        expected126 = report126["periods"]["all"]["candidate"]
        if not math.isclose(raw_score["winner_nll"], expected126, abs_tol=1e-12, rel_tol=0):
            raise ValueError("Original126 winner NLL not reproduced")
        print(f"132 baseline parity PASS mixed6={base_score['winner_nll']:.12f} raw126={raw_score['winner_nll']:.12f}", flush=True)
        source_evidence = read_json(SOURCE126 / "colsample_07-evidence.json")["rows"]
        eligible = [record for record in records if population_masks(record.er).eligible]
        if [(record.race_id, str(record.race_date)) for record in eligible] != [(row["race_id"], row["race_day"]) for row in source_evidence]:
            raise ValueError("Original126 eligible loss population/order differs")
        for record, saved in zip(eligible, source_evidence, strict=True):
            winner = population_masks(record.er).winner_horse_id
            loss = -math.log(raw[record.race_id][record.ids.index(winner), 0])
            if abs(loss - saved["candidate_winner_nll"]) > 1e-12:
                raise ValueError("Original126 individual loss not reproduced")
        write_json(output / "baseline-parity.json", {"status": "PASS", "baseline": base_score,
            "raw126": raw_score, "n_individual126_losses": len(eligible), "new_fits": 0,
            "common_provenance": inputs.provenance, "can_adopt": False, "eligible_for_verdict": False})
        transferred = {record.race_id: transferred_predictions(record, raw[record.race_id], coef) for record in records}
        reports = {}
        for arm in ARMS:
            predictions = {record.race_id: mix_arm(record.members, raw[record.race_id], transferred[record.race_id], arm)
                           for record in records}
            scores = common.summarize_predictions(records, predictions)
            diagnostics = quality_diagnostics(records, baseline, predictions)
            paired = common.paired_metrics(records, baseline, predictions,
                **{key: POLICY["bootstrap"][key] for key in ("b", "seed", "alpha")})
            report = {"arm": arm, "definition": ARMS[arm], "scores": scores,
                      "paired": paired, "diagnostics": diagnostics,
                      "research_disposition": research_disposition(base_score, scores, diagnostics),
                      "can_adopt": False, "eligible_for_verdict": False,
                      "new_booster_fits": 0, "new_coefficient_fits": 0}
            # Full prediction values make all probability and label metrics independently reproducible.
            prediction_path = output / f"{arm}-predictions.pkl"
            with prediction_path.open("xb") as dest:
                pickle.dump({"arm": arm, "regime": "full_information", "predictions": predictions,
                    "race_ids": [record.race_id for record in records], "horse_ids": {record.race_id: record.ids for record in records}},
                    dest, protocol=pickle.HIGHEST_PROTOCOL)
            report["prediction_sha256"] = digest(prediction_path)
            report["prediction_path"] = str(prediction_path)
            write_json(output / f"{arm}-report.json", report)
            reports[arm] = report
            print(f"132 {arm}: delta={report['research_disposition']['winner_nll_diff']:+.9f} state={report['research_disposition']['state']}", flush=True)
        _assert_unchanged(files)
        result = {"artifact_kind": "accuracy132_colsample_research_summary", "status": "COMPLETE",
            "protocol_sha256": stable_hash(POLICY), "run_freeze_sha256": digest(output / "run-freeze.json"),
            "actual_evaluation_end": str(EVAL_END), "authorized_data_cutoff": str(CUTOFF),
            "baseline": base_score, "raw126": raw_score, "reports": reports,
            "new_booster_fits": 0, "new_coefficient_fits": 0, "source_hashes_start_end_equal": True,
            "elapsed_wall_seconds": time.monotonic() - start, "can_adopt": False, "eligible_for_verdict": False,
            "limitations": ["Reused development period, one seed42 candidate and one mixture bundle; not prospective confirmation.",
                "Full-information forecasts only; no claim for preweight or131 production head transformation.",
                "Frozen annual125 coefficients transferred to126, never fitted to126.",
                "Conditional sample CIs omit selection and bundle reconstruction uncertainty; no automatic adoption."]}
        write_json(output / "summary.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    run(args.output)
