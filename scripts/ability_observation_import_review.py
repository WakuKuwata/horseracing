"""Independent light-memory validation of imported baseline cache payloads."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import pickle

import ability_observation as driver
from horseracing_training.calib_split import CalibSplitFactory

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "artifacts/111-ability-observation"
OUT = ROOT / "specs/111-ability-observation/evidence/import-review.json"


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    if OUT.exists():
        raise FileExistsError("Review evidence already exists")
    cert_path = WORK / "baseline-equivalence.json"
    cert_hash = digest(cert_path)
    cert = json.loads(cert_path.read_text())
    manifest_path = WORK / "prefill-full/manifest.json"
    manifest_hash = digest(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    assert manifest["baseline_equivalence_sha256"] == cert_hash
    assert json.loads((WORK / "prefill-full/manifest-freeze.json").read_text())["manifest_sha256"] == manifest_hash
    assert cert["method_sha256"] == digest(ROOT / "scripts/ability_observation_reuse.py")
    assert cert["new_freeze_sha256"] == digest(WORK / "run-freeze.json")
    assert cert["old_freeze_sha256"] == digest(ROOT / "artifacts/110-feature-pruning/run-freeze.json")
    cfg = driver.load_config()
    expected_recipe = CalibSplitFactory(None, driver.make_recipe(cfg, cfg["baseline_drop_features"]),
        n_oof_blocks=cfg["arms"]["n_oof_blocks"], method="isotonic").recipe_meta
    expected_columns = driver.columns_from_model()
    assert len(expected_columns) == 138
    rows = []
    assert sorted(r["year"] for r in cert["records"]) == list(range(2019, 2027))
    for record in cert["records"]:
        original_path = Path(record["old_path"])
        imported_path = WORK / "cache" / f"{record['new_key']}.pkl"
        receipt_path = WORK / "prefill-full" / f"{record['new_key']}.json"
        assert digest(original_path) == record["old_sha256"]
        assert digest(Path(record["old_receipt_path"])) == record["old_receipt_sha256"]
        with original_path.open("rb") as f:
            original = pickle.load(f)
        with imported_path.open("rb") as f:
            imported = pickle.load(f)
        receipt = json.loads(receipt_path.read_text())
        job = next(j for j in manifest["jobs"] if j["key"] == record["new_key"])
        assert job["year"] == record["year"] and job["arm"] == {
            "id": "baseline", "drop_features": cfg["baseline_drop_features"]}
        assert receipt["job"] == job and receipt["imported"] is True
        assert receipt["cache_sha256"] == digest(imported_path)
        assert receipt["import_certificate_sha256"] == imported["import_certificate_sha256"] == cert_hash
        assert imported["source_cache_sha256"] == record["old_sha256"]
        assert imported["key"] == record["new_key"] and original["key"] == record["old_key"]
        assert imported["recipe_meta"] == expected_recipe
        assert driver.stable_hash(imported["recipe_meta"]) == driver.stable_hash(record["new_recipe_meta"])
        for field in ("predictions", "oof_info", "train_hash", "feature_columns"):
            assert imported[field] == original[field], field
        assert imported["train_hash"] == record["train_hash"]
        assert imported["feature_columns"] == expected_columns
        assert imported["oof_info"]["sufficient"] is True
        assert imported["elapsed_seconds"] == 0.0
        assert imported["historical_fit_seconds"] == receipt["historical_fit_seconds"] == record["historical_fit_seconds"] == original["elapsed_seconds"]
        assert digest(original_path) == record["old_sha256"]
        rows.append({"year": record["year"], "original_sha256": record["old_sha256"],
            "imported_sha256": digest(imported_path), "receipt_sha256": digest(receipt_path),
            "n_races": len(imported["predictions"]),
            "n_horse_appearances": sum(len(v) for v in imported["predictions"].values()),
            "payload_exact": True, "wrapper_recipe_and_receipt_valid": True,
            "current_fit_seconds": 0.0, "historical_fit_seconds": imported["historical_fit_seconds"]})
    assert digest(cert_path) == cert_hash and digest(manifest_path) == manifest_hash
    result = {"status": "PASS", "method_path": str(Path(__file__).resolve()),
        "method_sha256": digest(Path(__file__)), "certificate_sha256": cert_hash,
        "manifest_sha256": manifest_hash, "n_imports": len(rows), "checks": rows,
        "current_fit_seconds": sum(r["current_fit_seconds"] for r in rows),
        "historical_fit_seconds": sum(r["historical_fit_seconds"] for r in rows),
        "notes": ["Independent direct comparison of original and imported prediction, OOF, train-hash and feature-column payloads.",
                  "Original cache bytes remain unchanged. No large training matrix is loaded.",
                  "Historical fit time is provenance, not current training time or a controlled speedup benchmark."]}
    with OUT.open("x") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "checks"}, indent=2))


if __name__ == "__main__":
    main()
