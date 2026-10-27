"""Check the first neutral-year fits without training or inspecting ROI."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_path = Path(__file__).with_name("independent_audit.py")
_spec = importlib.util.spec_from_file_location("independent145_first_helper", _path)
a = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(a)


def audit(seeds=(1, 2)):
    old = a.read(a.OLD143 / "protocol.json")
    new = a.read(a.OUT / "protocol.json")
    assert a.sha(a.OUT / "protocol.json") == (a.OUT / "protocol.sha256").read_text().strip()
    extras = a.pd.read_parquet(a.ROOT / old["additions"]).set_index(["race_id", "horse_id"])
    cols = list(dict.fromkeys(["race_id", "horse_id"] + old["base_features"]))
    original = a.pd.read_parquet(a.ROOT / old["rows"], columns=cols, filters=[("year", "=", 2019)])
    original = original.set_index(["race_id", "horse_id"])
    results = []
    for seed in seeds:
        folder = a.folder("weighted_pace", 2019, seed)
        rec = a.read(folder / "receipt.json")
        recipe = rec["recipe"]
        assert recipe["identity"]["protocol_sha256"] == a.sha(a.OUT / "protocol.json")
        assert recipe["identity"]["source_sha256"] == a.sha(a.ROOT / "scripts/roi_explore/roi_improvement_145/models.py")
        assert recipe["arm"] == "weighted_pace" and recipe["seed"] == seed
        assert recipe["features"] == new["candidate_features"]
        assert recipe["identity"]["kernel_arm"] == "weighted_pace"
        info = recipe["weights"]
        assert info["available"] == info["informative_rows"] == info["informative_races"] == 0
        assert info["neutral_rows"] == recipe["train_rows"] == 574304
        assert info["minimum"] == info["maximum"] == info["mean"] == 1
        vector_sha = a.hashlib.sha256(a.np.ones(recipe["train_rows"]).tobytes()).hexdigest()
        assert vector_sha == info["vector_sha256"]
        assert info == a.read(a.folder("weighted_baseline", 2019, seed) / "receipt.json")["recipe"]["weights"]
        fs = a.read(folder / "feature_spec.json")
        assert fs == a.read(a.folder("pace", 2019, seed) / "feature_spec.json")
        for name, digest in rec["hashes"].items():
            assert a.sha(folder / name) == digest
        pred = a.pd.read_parquet(folder / "predictions.parquet")
        prior = a.pd.read_parquet(a.folder("pace", 2019, seed) / "predictions.parquet")
        assert pred.equals(prior)
        selected = pred.iloc[:64]
        index = a.pd.MultiIndex.from_frame(selected[["race_id", "horse_id"]])
        source = original.loc[index].join(extras.loc[index], validate="one_to_one")
        x = a.np.column_stack([source[fs["features"]].to_numpy(a.np.float32)] + [
            source[c].astype("string").map(fs["cat_maps"][c]).to_numpy(a.np.float32, na_value=a.np.nan)
            for c in fs["cats"]])
        assert x.shape == (64, 91)
        booster = a.lgb.Booster(model_file=str(folder / "model.txt"))
        assert booster.feature_name() == fs["feature_order"]
        actual = a.expit(booster.predict(x, raw_score=True, num_threads=1))
        a.np.testing.assert_array_equal(actual, selected.p.to_numpy())
        results.append(dict(year=2019, seed=seed, all_prediction_rows_exact=len(pred),
                            all_prediction_columns_exact=pred.columns.tolist(), features=91, replay_rows=64,
                            replay_max_abs_p_difference=0., original143_weights_sha256=vector_sha))
    return dict(reviewer="paceweight145_review", protocol_sha256=a.sha(a.OUT / "protocol.json"),
                models=results, no_roi_inspection=True, no_model_fits_performed=True, blockers=[])


if __name__ == "__main__":
    result = audit()
    output = Path(__file__).with_name("independent_first_models.json")
    with output.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(result))
