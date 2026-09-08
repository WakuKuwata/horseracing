"""Restore tuple types in imported cache metadata without changing model payloads."""
from __future__ import annotations

import json
from pathlib import Path
import pickle
import shutil

import ability_observation as driver
from horseracing_training.calib_split import CalibSplitFactory

WORK = driver.WORK
ARCHIVE = WORK / "import-metadata-repair-originals"
OUT = driver.SPEC / "evidence/import-metadata-repair.json"


def main():
    if ARCHIVE.exists() or OUT.exists():
        raise FileExistsError("Repair already attempted")
    method_hash = driver.digest(__file__)
    cert_path = WORK / "baseline-equivalence.json"
    certificate_hash = driver.digest(cert_path)
    certificate = json.loads(cert_path.read_text())
    assert certificate["method_sha256"] == driver.digest(driver.ROOT / "scripts/ability_observation_reuse.py")
    assert certificate["new_freeze_sha256"] == driver.digest(WORK / "run-freeze.json")
    cfg = driver.load_config()
    actual_recipe = CalibSplitFactory(None, driver.make_recipe(cfg, cfg["baseline_drop_features"]),
        n_oof_blocks=cfg["arms"]["n_oof_blocks"], method="isotonic").recipe_meta
    planned = []
    assert len(certificate["records"]) == 8
    for record in certificate["records"]:
        cache_path = WORK / "cache" / f"{record['new_key']}.pkl"
        receipt_path = WORK / "prefill-full" / f"{record['new_key']}.json"
        with cache_path.open("rb") as f:
            cache = pickle.load(f)
        receipt = json.loads(receipt_path.read_text())
        assert cache["key"] == record["new_key"]
        assert receipt["job"]["arm"] == {"id": "baseline", "drop_features": cfg["baseline_drop_features"]}
        assert receipt["imported"] is True
        assert receipt["import_certificate_sha256"] == cache["import_certificate_sha256"] == certificate_hash
        assert receipt["cache_sha256"] == driver.digest(cache_path)
        assert cache["recipe_meta"] != actual_recipe
        assert driver.stable_hash(cache["recipe_meta"]) == driver.stable_hash(actual_recipe) == driver.stable_hash(record["new_recipe_meta"])
        assert cache["source_cache_sha256"] == record["old_sha256"] == driver.digest(record["old_path"])
        planned.append((record, cache_path, receipt_path, cache, receipt))
    ARCHIVE.mkdir()
    rows = []
    for record, cache_path, receipt_path, cache, receipt in planned:
        old_cache_hash, old_receipt_hash = driver.digest(cache_path), driver.digest(receipt_path)
        archived_cache, archived_receipt = ARCHIVE / cache_path.name, ARCHIVE / receipt_path.name
        shutil.copyfile(cache_path, archived_cache)
        shutil.copyfile(receipt_path, archived_receipt)
        assert driver.digest(archived_cache) == old_cache_hash
        assert driver.digest(archived_receipt) == old_receipt_hash
        repaired = {**cache, "recipe_meta": actual_recipe}
        # Only this metadata field may change; compare every other Python payload exactly.
        assert set(repaired) == set(cache)
        assert {k: v for k, v in repaired.items() if k != "recipe_meta"} == {
            k: v for k, v in cache.items() if k != "recipe_meta"}
        driver.save_pickle(cache_path, repaired)
        new_cache_hash = driver.digest(cache_path)
        updated_receipt = {**receipt, "cache_sha256": new_cache_hash}
        tmp_receipt = receipt_path.with_suffix(".repair.json")
        driver.write_json(tmp_receipt, updated_receipt)
        tmp_receipt.replace(receipt_path)
        with cache_path.open("rb") as f:
            checked = pickle.load(f)
        assert checked == repaired and checked["recipe_meta"] == actual_recipe
        assert driver.digest(record["old_path"]) == record["old_sha256"]
        rows.append({"year": record["year"], "key": record["new_key"],
            "archived_cache": str(archived_cache), "prior_cache_sha256": old_cache_hash,
            "repaired_cache_sha256": new_cache_hash, "archived_receipt": str(archived_receipt),
            "prior_receipt_sha256": old_receipt_hash, "repaired_receipt_sha256": driver.digest(receipt_path),
            "recipe_semantic_hash": driver.stable_hash(actual_recipe),
            "recipe_python_exact": True, "all_other_payload_fields_exact": True})
    assert driver.digest(__file__) == method_hash and driver.digest(cert_path) == certificate_hash
    driver.write_json(OUT, {"status": "PASS", "method_path": str(Path(__file__).resolve()),
        "method_sha256": method_hash, "certificate_sha256": certificate_hash, "n_repaired": len(rows),
        "issue": "Certificate JSON converted tuple-valued recipe metadata to lists; copied pickle wrappers failed the frozen driver's exact Python metadata equality.",
        "repair": "Restore canonical current frozen factory recipe metadata; semantic hash unchanged; preserve every other payload field exactly.",
        "scope": "Imported baseline cache wrappers and their cache-SHA receipts only. Original cache wrappers archived; source caches and running candidate workers untouched.",
        "checks": rows})
    print("REPAIRED metadata-only wrappers:", len(rows), flush=True)


if __name__ == "__main__":
    main()
