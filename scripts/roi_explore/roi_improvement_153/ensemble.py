"""Verify120 candidate receipts and mean15 raw probabilities per year."""

import json
import numpy as np
import pandas as pd


def ensemble(out, protocol, models, research):
    parts, details = [], []
    for year in protocol["window"]["model_outer"]:
        first, total = None, None
        for seed in protocol["seeds"]:
            fit = out / "models" / f"seed_{seed:02d}/year_{year}/market_drop3"
            rec = json.loads((fit / "receipt.json").read_text())
            recipe = rec["recipe"]
            expected_params = dict(
                models.base.BASE_PARAMS,
                seed=seed,
                objective="binary",
                metric="binary_logloss",
            )
            if (
                recipe["arm"] != "market_drop3"
                or recipe["seed"] != seed
                or recipe["identity"]["protocol_sha256"]
                != models.sha(out / "protocol.json")
            ):
                raise ValueError("Candidate fit identity differs")
            if (
                recipe["features"] != protocol["candidate_features"]
                or recipe["params"] != expected_params
            ):
                raise ValueError("Candidate features/params differ")
            if (
                recipe["rounds"] != 300
                or rec["actual_iterations"] != 300
                or recipe["train_end"] >= recipe["target_start"]
            ):
                raise ValueError("Candidate chronology/rounds differ")
            old_fit = (
                research.PREVIOUS
                / "weighted_models"
                / f"seed_{seed:02d}/year_{year}/weighted_baseline/receipt.json"
            )
            old = json.loads(old_fit.read_text())
            if models.canonical(recipe["weights"]) != models.canonical(
                old["recipe"]["weights"]
            ):
                raise ValueError("Candidate weights differ from original143")
            if set(rec["hashes"]) != {
                "model.txt",
                "predictions.parquet",
                "feature_spec.json",
            }:
                raise ValueError("Candidate artifact inventory differs")
            for name, digest in rec["hashes"].items():
                if models.sha(fit / name) != digest:
                    raise ValueError("Candidate artifact changed")
            pred = pd.read_parquet(fit / "predictions.parquet")
            if not pred.year.eq(year).all() or len(pred) != recipe["target_rows"]:
                raise ValueError("Candidate target differs")
            if first is None:
                first, total = pred[models.PRED_KEYS].copy(), np.zeros(len(pred))
            elif not first.equals(pred[models.PRED_KEYS]):
                raise ValueError("Candidate seed population differs")
            total += pred.p.to_numpy()
            details.append(
                dict(
                    arm="market_drop3",
                    year=year,
                    seed=seed,
                    elapsed_s=rec["elapsed_s"],
                    roundtrip_max_abs_raw_difference=rec[
                        "roundtrip_max_abs_raw_difference"
                    ],
                    weights=recipe["weights"],
                    receipt_sha256=models.sha(fit / "receipt.json"),
                )
            )
        first["p"] = total / len(protocol["seeds"])
        parts.append(first)
    return pd.concat(parts, ignore_index=True), details
