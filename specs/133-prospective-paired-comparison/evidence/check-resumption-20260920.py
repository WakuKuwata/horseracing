"""Recheck saved heads, per-race NLL and capture timing without the experiment scorer.

Result labels/exclusions are taken from the score report; this is not a separate
audit of the result database. No fits, database access or production writes.
"""
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = Path(__file__).resolve().parent
RECORDS = ROOT / "artifacts/133-prospective-paired-comparison/records"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run():
    checked = {}
    files = {}
    for regime in ("preweight", "serving"):
        path = EVIDENCE / f"interim-{regime}-2026-09-20.json"
        doc = json.loads(path.read_text())
        files[str(path.relative_to(ROOT))] = digest(path)
        assert doc["verdict"] is None and doc["can_adopt"] is False
        assert not doc["rejected_records"]
        plan = ROOT / "specs/133-prospective-paired-comparison/preregistration.json"
        assert digest(plan) == doc["preregistration_sha256"]
        losses = {"new": [], "old": []}
        for row in doc["per_race"]:
            record_path = RECORDS / row["source"] / (row["race_id"] + ".json")
            record = json.loads(record_path.read_text())
            files[str(record_path.relative_to(ROOT))] = digest(record_path)
            heads = record["regimes"][regime]
            for arm in losses:
                loss = -math.log(max(1e-15, min(1.0, heads[arm][row["winner"]][0])))
                assert abs(loss - row[arm + "_winner_nll"]) < 1e-12
                losses[arm].append(loss)
            assert abs(losses["new"][-1] - losses["old"][-1] - row["diff_winner_nll"]) < 1e-12
        for arm in losses:
            assert abs(mean(losses[arm]) - doc["overall"][arm + "_winner_nll"]) < 1e-12
        assert abs(mean(losses["new"]) - mean(losses["old"]) - doc["overall"]["diff_winner_nll"]) < 1e-12
        assert len(doc["per_race"]) + len(doc["excluded"]) + len(doc["pending"]) == 72
        checked[regime] = {"n_races": len(doc["per_race"]), "status": "PASS"}

    captures = []
    snapshots = []
    for path in RECORDS.rglob("*.json"):
        doc = json.loads(path.read_text())
        if doc.get("race_day") not in ("2026-09-19", "2026-09-20"):
            continue
        if doc.get("source") == "capture":
            start = dt.datetime.fromisoformat(doc["scheduled_start"])
            assert dt.datetime.fromisoformat(doc["record_written_at"]) < start
            assert dt.datetime.fromisoformat(doc["timeline"]["post_prediction_check_at"]) < start
            assert doc["n_result_rows_at_capture"] == doc["n_result_rows_after_prediction"] == 0
            captures.append(doc["race_id"])
        elif doc.get("source") == "production_snapshot":
            assert dt.datetime.fromisoformat(doc["input_captured_at"]) < dt.datetime.fromisoformat(doc["scheduled_start"])
            assert doc["snapshot_parity"]["max_abs_win_diff"] == 0
            snapshots.append(doc["race_id"])
        else:
            continue
        files[str(path.relative_to(ROOT))] = digest(path)
    assert len(captures) == 22 and len(snapshots) == 48
    out = {"status": "PASS", "scores": checked, "prestart_captures": len(captures),
           "exact_snapshot_replays": len(snapshots), "files": files,
           "scope": "Saved-head NLL, aggregate arithmetic, timings and recorded parity; labels and exclusions are not independently audited here.",
           "can_adopt": False}
    with (EVIDENCE / "resumption-check-20260920.json").open("x") as f:
        json.dump(out, f, indent=2, sort_keys=True)
        f.write("\n")
    print(json.dumps({k: v for k, v in out.items() if k != "files"}))


if __name__ == "__main__":
    run()
