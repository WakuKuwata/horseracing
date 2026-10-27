"""Independent numpy audit: complete-result canonical/nk binary-win guards.

Uses only the two completed132 cached-prediction artifacts. No repository
scorer/model imports, no database connection, and no fit of any kind.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import pickle

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
WORK = ROOT / "artifacts/132-accuracy-first-batch"
OUTPUT = Path(__file__).with_suffix(".json")
CUTOFF = dt.date(2026, 9, 6)
SOURCE_END = dt.date(2026, 8, 23)
ARMS = ("add_raw_1of7", "add_transferred_1of14", "add_transferred_1of7", "replace_joint42")
BOOTSTRAP = {"b": 4000, "seed": 20260907, "alpha": .0125}
MARGIN = .001


def read_json(path):
    return json.loads(path.read_text())


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def win_probabilities(values, n):
    a = np.asarray(values, dtype=float)
    if (a.shape != (n, 3) or not np.isfinite(a).all() or (a < 0).any()
            or (a > 1 + 1e-10).any() or (a[:, 0] <= 0).any()
            or not np.allclose(a.sum(0), [min(k, n) for k in (1, 2, 3)], atol=1e-8, rtol=0)
            or (np.diff(a, axis=1) < -1e-10).any()):
        raise ValueError("Prediction domain, head order or mass differs")
    return np.clip(a[:, 0], 1e-15, 1 - 1e-15)


def binary_loss(p, y):
    return -(y * np.log(p) + (1 - y) * np.log1p(-p))


def day_interval(daily):
    a = np.asarray([daily[day] for day in sorted(daily) if daily[day][1]], dtype=float)
    if len(a) < 2:
        return {"state": "MISSING", "n_days": len(a), "n_horses": int(a[:, 1].sum()) if len(a) else 0}
    rng = np.random.default_rng(BOOTSTRAP["seed"])
    estimates = np.empty(BOOTSTRAP["b"])
    for start in range(0, len(estimates), 128):
        selected = rng.integers(0, len(a), size=(min(128, len(estimates) - start), len(a)))
        totals = a[selected].sum(1)
        estimates[start:start + len(selected)] = totals[:, 0] / totals[:, 1]
    low, high = np.quantile(estimates, [BOOTSTRAP["alpha"] / 2, 1 - BOOTSTRAP["alpha"] / 2])
    return {"point": float(a[:, 0].sum() / a[:, 1].sum()), "ci_low": float(low), "ci_high": float(high),
            "n_days": len(a), "n_horses": int(a[:, 1].sum()), "margin": MARGIN,
            "state": "PASS" if high < MARGIN else "FAIL" if low > MARGIN else "NOT_PROVEN"}


def add_day(daily, day, values):
    if len(values):
        row = daily.setdefault(day, [0., 0])
        row[0] += float(values.sum()); row[1] += len(values)


def main():
    if OUTPUT.exists():
        raise FileExistsError("Preserve existing independent audit")
    design_path = WORK / "topk/design.json"
    summary_path = WORK / "topk/summary.json"
    full_summary_path = WORK / "topk/full-summary.json"
    evidence_path = WORK / "topk/full-predictions.pkl"
    colsample_path = WORK / "colsample/summary.json"
    freeze_path = WORK / "colsample/run-freeze.json"
    design, topk, full, summary, freeze = [read_json(p) for p in
        (design_path, summary_path, full_summary_path, colsample_path, freeze_path)]
    if (design["allowed_data_through"] != str(CUTOFF)
            or topk["provenance"]["actual_data_through"] != str(SOURCE_END)
            or topk["design_sha256"] != sha(design_path)
            or topk["regimes"]["full"] != full
            or full["predictions_sha256"] != sha(evidence_path)
            or summary["status"] != "COMPLETE" or summary["authorized_data_cutoff"] != str(CUTOFF)
            or summary["actual_evaluation_end"] != str(SOURCE_END)
            or summary["run_freeze_sha256"] != sha(freeze_path)
            or summary["new_booster_fits"] != 0 or summary["new_coefficient_fits"] != 0
            or summary["can_adopt"] is not False or summary["eligible_for_verdict"] is not False
            or list(summary["reports"]) != list(ARMS)
            or freeze["protocol"]["bootstrap"] != {**BOOTSTRAP, "block": "race_day"}):
        raise ValueError("Completed cached132 provenance/cutoff differs")
    paths = [Path(__file__), design_path, summary_path, full_summary_path, evidence_path, colsample_path, freeze_path]
    for arm in ARMS:
        path = WORK / "colsample" / f"{arm}-predictions.pkl"
        if sha(path) != summary["reports"][arm]["prediction_sha256"]:
            raise ValueError("Candidate predictions changed")
        paths.append(path)
    source_hashes = {str(p): sha(p) for p in paths}
    with evidence_path.open("rb") as source:
        rows = pickle.load(source)
    race_ids, parsed, excluded = [], [], []
    populations = {group: {"legacy_n_horses": 0, "complete_n_horses": 0} for group in ("canonical", "nk")}
    for row in rows:
        day = dt.date.fromisoformat(row["race_date"])
        if not dt.date(2020, 1, 1) <= day <= SOURCE_END <= CUTOFF:
            raise ValueError("Real race outside authorized window")
        rid, ids = row["race_id"], tuple(row["ids"])
        labels = row["labels"]
        if (not ids or len(set(ids)) != len(ids) or len({v[0] for v in labels}) != len(labels)
                or any(len(v) != 4 or v[0] not in ids or any(x not in (0, 1) for x in v[1:]) for v in labels)):
            raise ValueError("Ambiguous started horse/finished label identities")
        coverage = row["n_result_rows"]
        if not isinstance(coverage, int) or isinstance(coverage, bool) or coverage < 0:
            raise ValueError("Unknown result coverage is not admissible")
        complete = coverage >= len(ids)
        known = {v[0]: v[1] for v in labels}
        y = np.asarray([known.get(h, 0) for h in ids], dtype=float)
        base = win_probabilities(row["arms"]["head_mean"], len(ids))
        masks = {"nk": np.asarray([h.startswith("nk:") for h in ids], dtype=bool)}
        masks["canonical"] = ~masks["nk"]
        for group, mask in masks.items():
            populations[group]["legacy_n_horses"] += int(mask.sum())
            if complete:
                populations[group]["complete_n_horses"] += int(mask.sum())
        if not complete:
            excluded.append({"race_id": rid, "race_date": str(day), "n_started": len(ids),
                "n_result_rows": coverage, "n_finished_labels": len(labels),
                "n_excluded_canonical": int(masks["canonical"].sum()), "n_excluded_nk": int(masks["nk"].sum())})
        race_ids.append(rid)
        parsed.append((rid, str(day), ids, complete, y, base, masks))
    if len(race_ids) != len(set(race_ids)) or len(rows) != 23030 or max(v[1] for v in parsed) != str(SOURCE_END):
        raise ValueError("Exact full research population differs")
    arms = {}
    for arm in ARMS:
        with (WORK / "colsample" / f"{arm}-predictions.pkl").open("rb") as source:
            payload = pickle.load(source)
        if (payload["arm"] != arm or payload["regime"] != "full_information"
                or payload["race_ids"] != race_ids or set(payload["predictions"]) != set(race_ids)
                or set(payload["horse_ids"]) != set(race_ids)):
            raise ValueError("Candidate race/horse population differs")
        groups = {cohort: {group: {} for group in populations} for cohort in ("legacy", "complete")}
        for rid, day, ids, complete, y, base, masks in parsed:
            if tuple(payload["horse_ids"][rid]) != ids:
                raise ValueError("Candidate horse order differs")
            candidate = win_probabilities(payload["predictions"][rid], len(ids))
            delta = binary_loss(candidate, y) - binary_loss(base, y)
            for group, mask in masks.items():
                add_day(groups["legacy"][group], day, delta[mask])
                if complete:
                    add_day(groups["complete"][group], day, delta[mask])
        checks = {}
        for group in populations:
            legacy = day_interval(groups["legacy"][group])
            corrected = day_interval(groups["complete"][group])
            old = summary["reports"][arm]["diagnostics"]["critical"][group]
            if (any(abs(legacy[k] - old[k]) > 1e-12 for k in ("point", "ci_low", "ci_high"))
                    or legacy["state"] != old["state"] or legacy["n_horses"] != old["n_items"]
                    or legacy["n_days"] != old["n_days"]):
                raise ValueError("Independent legacy guard does not reproduce saved report")
            checks[group] = {"legacy_reproduced": True, "legacy": legacy, "complete_results": corrected,
                "complete_minus_legacy": {k: corrected[k] - legacy[k] for k in ("point", "ci_low", "ci_high", "n_days", "n_horses")},
                "state_changed": corrected["state"] != legacy["state"]}
        arms[arm] = checks
    if any(sha(Path(path)) != value for path, value in source_hashes.items()):
        raise ValueError("Frozen audit source changed")
    result = {"artifact_kind": "132_colsample_complete_subgroups_independent_review", "status": "PASS",
        "method": "Independent numpy binary-win loss; started all with known complete result coverage; paired race-day ratio bootstrap.",
        "bootstrap": BOOTSTRAP, "margin": MARGIN, "authorized_data_cutoff": str(CUTOFF),
        "actual_evaluation_end": str(SOURCE_END), "source_hashes": source_hashes, "source_hashes_start_end_equal": True,
        "n_input_races": len(rows), "n_complete_races": len(rows) - len(excluded),
        "n_excluded_races": len(excluded), "n_excluded_horses": sum(v["n_started"] for v in excluded),
        "excluded": excluded, "subgroup_populations": populations, "arms": arms,
        "all_complete_guards_pass": all(v[g]["complete_results"]["state"] == "PASS" for v in arms.values() for g in populations),
        "prior_results_preserved": True, "new_booster_fits": 0, "new_coefficient_fits": 0,
        "can_adopt": False, "eligible_for_verdict": False,
        "limitations": ["Complete result coverage follows the saved n_result_rows >= started_count contract; individual non-finished result identities were not saved.",
            "Intervals are conditional on saved predictions and previously reused study periods; they omit model fitting and selection uncertainty."]}
    with OUTPUT.open("x") as destination:
        json.dump(result, destination, ensure_ascii=False, indent=2, allow_nan=False)
        destination.write("\n")
    print(json.dumps({"status": result["status"], "n_excluded_races": len(excluded),
        "n_excluded_horses": result["n_excluded_horses"], "all_complete_guards_pass": result["all_complete_guards_pass"]}))
    return result


if __name__ == "__main__":
    main()
