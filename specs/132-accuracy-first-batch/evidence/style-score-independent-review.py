"""Independent numpy audit of completed 132 style predictions; never fits a model.

Run only after both candidate receipts and style summary exist. Binary inputs are
SHA-checked before loading. No experiment scorer, population helper, or bootstrap
helper is imported. The sealed 111 snapshot is used only to verify copied labels.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
from pathlib import Path
import pickle

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
WORK = ROOT / "artifacts/132-accuracy-first-batch"
STYLE = WORK / "style"
INPUT = ROOT / "artifacts/111-ability-observation"
SNAPSHOT_SHA = "58397ba94dc685c6e49b509619fb447106702d869adebc38889b021f3be6859a"
YEARS = (2024, 2026)
REGIMES = ("full", "preweight")
END = "2026-08-23"
CUTOFF = "2026-09-06"
CHECKS = []
HASHES = {}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def pin(path, expected=None):
    path = Path(path)
    value = sha(path)
    assert expected is None or value == expected, (str(path), "SHA mismatch")
    assert str(path) not in HASHES or HASHES[str(path)] == value
    HASHES[str(path)] = value
    return value


def read(path, expected=None):
    pin(path, expected)
    return json.loads(Path(path).read_text())


def load(path, expected):
    pin(path, expected)
    with Path(path).open("rb") as f:
        return pickle.load(f)


def canonical(value):
    s = json.dumps(value, sort_keys=True, separators=(",", ":"),
                   default=str, ensure_ascii=False)
    return hashlib.sha256(s.encode()).hexdigest()


def close(actual, expected, label):
    assert np.allclose(actual, expected, rtol=0, atol=2e-12), (label, actual, expected)
    CHECKS.append(label)


def outcomes(row):
    ids = tuple(row["ids"])
    assert ids and len(ids) == len(set(ids))
    assert "2007-01-01" <= row["race_date"] <= END <= CUTOFF
    assert len(row["labels"]) == len({x[0] for x in row["labels"]})
    labels = {x[0]: x[1:] for x in row["labels"]}
    y = np.asarray([labels.get(h, (0, 0, 0)) for h in ids], dtype=float)
    assert y.shape == (len(ids), 3) and np.isin(y, [0, 1]).all()
    assert (np.diff(y, axis=1) >= 0).all()
    assert row["n_result_rows"] is not None
    complete = row["n_result_rows"] >= len(ids)
    winners = np.flatnonzero(y[:, 0] == 1)
    winner = int(winners[0]) if complete and len(winners) == 1 else None
    return y, complete, winner


def numpy_ece(p, y):
    # Move every nominal decile boundary to the right edge of its tied plateau.
    order = np.argsort(p, kind="stable")
    s, residual = p[order], (p - y)[order]
    cuts = np.linspace(0, len(p), 11).astype(int)[1:]
    ends = sorted({int(np.searchsorted(s, s[c - 1], side="right")) for c in cuts if c > 0})
    starts = [0, *ends[:-1]]
    ece = sum(abs(float(residual[a:b].sum())) for a, b in zip(starts, ends)) / len(p)
    return {"ece": ece, "n_bins": len(ends), "bin_counts": [b - a for a, b in zip(starts, ends)]}


def score(rows, predictions):
    assert set(predictions) >= {r["race_id"] for r in rows}
    flat_p, flat_y, winner_losses = [], [], []
    n_complete, eligible_rows = 0, []
    for row in rows:
        y, complete, winner = outcomes(row)
        raw = predictions[row["race_id"]]
        assert tuple(raw) == tuple(row["ids"])
        p = np.asarray(list(raw.values()), dtype=float)
        assert p.shape == y.shape and np.isfinite(p).all()
        assert (p >= -1e-12).all() and (p <= 1 + 1e-12).all()
        assert (np.diff(p, axis=1) >= -1e-10).all()
        assert np.allclose(p.sum(axis=0), np.minimum([1, 2, 3], len(p)), rtol=0, atol=1e-8)
        if winner is not None:
            loss = float(-np.log(np.clip(p[winner, 0], 1e-15, 1 - 1e-15)))
            winner_losses.append(loss)
            eligible_rows.append((row["race_id"], row["race_date"], loss))
        if complete:
            n_complete += 1
            flat_p.append(p)
            flat_y.append(y)
    p, y = np.concatenate(flat_p), np.concatenate(flat_y)
    q = np.clip(p, 1e-15, 1 - 1e-15)
    losses = np.where(y == 1, -np.log(q), -np.log1p(-q)).mean(axis=0)
    brier = np.square(p - y).mean(axis=0)
    bands = {}
    edges = (0., .05, .15, .30, 1.)
    for i, (lo, hi) in enumerate(zip(edges[:-1], edges[1:])):
        last = i == len(edges) - 2
        m = (p[:, 0] >= lo) & ((p[:, 0] <= hi) if last else (p[:, 0] < hi))
        if m.any():
            bands[f"[{lo:.2f},{hi:.2f}{']' if last else ')'}"] = float(abs((p[m, 0] - y[m, 0]).mean()))
    return {
        "winner_nll": float(np.mean(winner_losses)), "winner_excluded": len(rows) - len(winner_losses),
        "top2_logloss": float(losses[1]), "top3_logloss": float(losses[2]),
        "started_all": {"log_loss": float(losses[0]), "brier": float(brier[0])},
        "ece_equal_width_like": numpy_ece(p[:, 0], y[:, 0]), "ece_by_band": bands,
        "n_races": len(rows), "n_eligible": len(winner_losses),
        "n_days": len({r[1] for r in eligible_rows}),
        "n_complete": n_complete, "n_started_scored": len(p),
        "heads_brier": brier.tolist(),
    }, eligible_rows


def bootstrap(eligible_base, eligible_cand, cfg):
    assert [(r, d) for r, d, _ in eligible_base] == [(r, d) for r, d, _ in eligible_cand]
    diffs = {}
    for (_, day, b), (_, _, c) in zip(eligible_base, eligible_cand):
        diffs.setdefault(day, []).append(c - b)
    days = sorted(diffs)
    sums = np.asarray([np.sum(diffs[d]) for d in days])
    counts = np.asarray([len(diffs[d]) for d in days])
    rng = np.random.default_rng(cfg["seed"])
    samples = []
    for _ in range(cfg["b"]):
        # Independently resample day multiplicities and reweight sufficient stats.
        k = np.bincount(rng.integers(0, len(days), len(days)), minlength=len(days))
        samples.append(float(k @ sums / (k @ counts)))
    lo, hi = np.quantile(samples, [cfg["alpha"] / 2, 1 - cfg["alpha"] / 2])
    return {"point": float(sums.sum() / counts.sum()), "ci_low": float(lo), "ci_high": float(hi),
            "b": cfg["b"], "seed": cfg["seed"], "block": "race_day", "n_days": len(days),
            "no_decision": False}


def compare(rows, base, cand, expected, cfg, scope):
    b, br = score(rows, base)
    c, cr = score(rows, cand)
    for arm, actual in (("baseline", b), ("candidate", c)):
        saved = expected[arm]
        for key in ("winner_nll", "winner_excluded", "top2_logloss", "top3_logloss"):
            close(actual[key], saved[key], f"{scope}:{arm}:{key}")
        for key in ("log_loss", "brier"):
            close(actual["started_all"][key], saved["started_all"][key], f"{scope}:{arm}:started:{key}")
        for key in ("ece", "n_bins", "bin_counts"):
            close(actual["ece_equal_width_like"][key], saved["ece_equal_width_like"][key], f"{scope}:{arm}:ECE:{key}")
        assert actual["ece_by_band"].keys() == saved["ece_by_band"].keys()
        for key, value in actual["ece_by_band"].items():
            close(value, saved["ece_by_band"][key], f"{scope}:{arm}:band:{key}")
    for key in ("n_races", "n_eligible", "n_days"):
        close(b[key], expected[key], f"{scope}:{key}")
        close(c[key], b[key], f"{scope}:paired_population:{key}")
    for key, saved in (("winner_nll", "winner_nll_diff"), ("top2_logloss", "top2_diff"), ("top3_logloss", "top3_diff")):
        close(c[key] - b[key], expected[saved], f"{scope}:{saved}")
    assert len(br) == len(expected["rows"])
    for (rid, day, bl), (crid, cday, cl), saved in zip(br, cr, expected["rows"]):
        assert (rid, day) == (crid, cday) == (saved["race_id"], saved["race_date"])
        assert np.allclose([bl, cl, cl - bl], [saved["baseline_nll"], saved["candidate_nll"], saved["diff"]], atol=2e-12, rtol=0)
    CHECKS.append(f"{scope}:all_saved_winner_loss_rows")
    ci = bootstrap(br, cr, cfg)
    for key in ("point", "ci_low", "ci_high", "n_days", "b", "seed"):
        close(ci[key], expected["sample_ci"][key], f"{scope}:CI:{key}")
    assert ci["block"] == expected["sample_ci"]["block"] and expected["sample_ci"]["no_decision"] is False
    return {"baseline": b, "candidate": c,
            "winner_nll_diff": c["winner_nll"] - b["winner_nll"],
            "top2_diff": c["top2_logloss"] - b["top2_logloss"],
            "top3_diff": c["top3_logloss"] - b["top3_logloss"], "sample_ci": ci}


def verify_source_labels(rows, frozen):
    meta = read(INPUT / "snapshot.json", frozen["inputs"]["files"]["snapshot.json"])
    assert meta["data_through"] == END and meta["snapshot_sha256"] == SNAPSHOT_SHA
    assert frozen["inputs"]["files"]["snapshot.pkl"] == SNAPSHOT_SHA
    matrix, races = load(INPUT / "snapshot.pkl", SNAPSHOT_SHA)
    dates = matrix.frame.race_date
    assert not dates.isna().any() and "2007-01-01" <= str(dates.min()) <= str(dates.max()) <= END
    del matrix, dates
    gc.collect()
    assert len(races) == len({r.context.race_id for r in races})
    assert all("2007-01-01" <= str(r.context.race_date) <= END for r in races)
    original = {r.context.race_id: r for r in races if r.context.race_date.year in YEARS}
    assert set(original) == {r["race_id"] for r in rows}
    for row in rows:
        er = original[row["race_id"]]
        assert row["race_date"] == str(er.context.race_date)
        assert tuple(row["ids"]) == tuple(h.horse_id for h in er.context.started_horses)
        assert row["n_result_rows"] == er.n_result_rows
        assert row["labels"] == [(l.horse_id, l.win, l.top2, l.top3) for l in er.labels]
    details = {"compared_races": len(rows), "snapshot_sha256": SNAPSHOT_SHA,
               "labels_and_started_order_exact": True, "result_row_counts_exact": True}
    del races, original
    gc.collect()
    return details


def run():
    # Check completion before any binary data is read or expensive audit starts.
    required = [STYLE / "summary.json"] + [STYLE / "cache" / f"candidate-{y}.{suffix}"
                                            for y in YEARS for suffix in ("pkl", "json")]
    assert all(p.is_file() for p in required), "Style study is not complete"
    output = Path(__file__).with_suffix(".json")
    assert not output.exists(), "Preserve existing independent evidence"
    pin(Path(__file__))
    summary = read(STYLE / "summary.json")
    frozen = read(STYLE / "run-freeze.json", summary["freeze_sha256"])
    reuse = read(STYLE / "baseline-reuse.json", frozen["baseline_reuse_sha256"])
    assert summary["baseline_reuse"] == reuse and reuse["status"] == "REUSE_VERIFIED"
    assert summary["data_through"] == END and summary["cutoff"] == CUTOFF
    assert summary["status"] == "RESEARCH_COMPLETE" and summary["can_adopt"] is False
    assert summary["eligible_for_verdict"] is False
    assert frozen["inputs"]["data_through"] == END
    cfg = frozen["config"]["bootstrap"]
    assert cfg == {"alpha": .0125, "b": 4000, "seed": 20260907}
    topk = read(WORK / "topk/summary.json")
    assert topk["provenance"]["actual_data_through"] == END
    assert topk["provenance"]["snapshot_sha256"] == SNAPSHOT_SHA
    full = load(WORK / "topk/full-predictions.pkl", topk["regimes"]["full"]["predictions_sha256"])
    assert len(full) == len({r["race_id"] for r in full})
    assert all("2007-01-01" <= r["race_date"] <= END for r in full)
    rows = [r for r in full if int(r["race_date"][:4]) in YEARS]
    del full
    label_check = verify_source_labels(rows, frozen)
    overlay = load(STYLE / "overlay.pkl", frozen["overlay_sha256"])
    affected = set(overlay["affected_race_ids"])
    del overlay
    gc.collect()
    old_freeze = read(reuse["freeze_path"], reuse["freeze_sha256"])
    assert old_freeze["snapshot_sha256"] == SNAPSHOT_SHA
    caches, populations = {}, {}
    for year in YEARS:
        subset = [r for r in rows if int(r["race_date"][:4]) == year]
        item = reuse["years"][str(year)]
        receipt = read(item["receipt_path"], item["receipt_sha256"])
        assert receipt["cache_sha256"] == item["cache_sha256"]
        assert receipt["member"] == "anchor-42" and receipt["year"] == year and receipt["smoke"] is False
        assert receipt["source_hash"] == old_freeze["source_hash"]
        base = load(item["cache_path"], item["cache_sha256"])
        assert base["member"] == "anchor-42" and base["year"] == year and base["smoke"] is False
        assert canonical(base["predictions"]) == item["prediction_sha256"]
        receipt = read(STYLE / "cache" / f"candidate-{year}.json")
        assert receipt["year"] == year and receipt["arm"] == "candidate" and receipt["smoke"] is False
        assert receipt["freeze_sha256"] == HASHES[str(STYLE / "run-freeze.json")]
        cand = load(STYLE / "cache" / f"candidate-{year}.pkl", receipt["cache_sha256"])
        assert cand["year"] == year and cand["arm"] == "candidate" and cand["smoke"] is False
        assert cand["input_snapshot_sha256"] == SNAPSHOT_SHA
        assert cand["source_hash"] == frozen["source_hash"] and cand["overlay_sha256"] == frozen["overlay_sha256"]
        assert cand["recipe_meta"] == frozen["recipe_meta"] == reuse["recipe_meta"]
        assert cand["feature_columns"] == base["feature_cols"] == frozen["feature_columns"]
        assert len(cand["feature_columns"]) == 138
        assert cand["oof_info"]["sufficient"] and base["oof_info"]["sufficient"]
        assert cand["oof_info"]["oof_pred_through"] < f"{year}-01-01"
        assert base["oof_info"]["oof_pred_through"] < f"{year}-01-01"
        assert cand["train_hash"] == frozen["folds"][str(year)]["train_hash"]
        ids = [r["race_id"] for r in subset]
        assert cand["valid_ids_sha256"] == canonical(ids) == frozen["folds"][str(year)]["valid_ids_sha256"]
        assert item["fold"] == frozen["folds"][str(year)] == old_freeze["population"][str(year)]
        for arm in (base, cand):
            assert set(arm["predictions"]) == set(REGIMES)
            for regime in REGIMES:
                assert set(arm["predictions"][regime]) == set(ids)
        caches[year] = (base["predictions"], cand["predictions"])
        populations[str(year)] = {"n_races": len(subset), "from": min(r["race_date"] for r in subset),
                                  "through": max(r["race_date"] for r in subset)}
    results = {}
    for year in YEARS:
        subset = [r for r in rows if int(r["race_date"][:4]) == year]
        base, cand = caches[year]
        for regime in REGIMES:
            expected = summary["years"][str(year)][regime]
            name = f"{year}:{regime}"
            results[name] = compare(subset, base[regime], cand[regime], expected, cfg, name)
            affected_rows = [r for r in subset if r["race_id"] in affected]
            if affected_rows:
                name += ":materially_affected"
                results[name] = compare(affected_rows, base[regime], cand[regime],
                    expected["materially_affected_input_races"], cfg, name)
            else:
                assert expected["materially_affected_input_races"] is None
    for regime in REGIMES:
        base = {k: v for year in YEARS for k, v in caches[year][0][regime].items()}
        cand = {k: v for year in YEARS for k, v in caches[year][1][regime].items()}
        name = f"pooled:{regime}"
        results[name] = compare(rows, base, cand, summary["pooled"][regime], cfg, name)
    for path, expected in HASHES.items():
        assert sha(path) == expected, (path, "Changed during independent audit")
    report = {"status": "PASS", "method_sha256": HASHES[str(Path(__file__))],
        "reviewer": "/root/challenge_model", "source_sha256": HASHES, "labels": label_check,
        "populations": populations, "results": results, "n_numeric_checks": len(CHECKS),
        "numeric_checks": CHECKS, "max_allowed_abs_error": 2e-12,
        "allowed_data_through": CUTOFF, "actual_data_through": END,
        "bootstrap_method": "Independently sampled day multiplicities; sum of race loss differences divided by number of sampled races. Percentiles 0.00625/0.99375, 4000 samples, seed20260907.",
        "ci_scope": "Fixed saved model predictions; race-day sampling uncertainty only; no selection or refit uncertainty.",
        "additional_booster_fits": 0, "db_reads": 0, "post_cutoff_data_reads": 0,
        "experiment_scorer_calls": 0, "can_adopt": False, "eligible_for_verdict": False}
    with output.open("x") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")
    print(json.dumps({"status": "PASS", "checks": len(CHECKS), "path": str(output)}))


def self_test():
    # Four tiny synthetic races distinguish winner, dead heat, DNF and missing result.
    labels = [[("A", 1, 1, 1), ("B", 0, 1, 1)], [("A", 1, 1, 1)],
              [("A", 1, 1, 1), ("B", 1, 1, 1)], [("A", 1, 1, 1)]]
    rows = [{"race_id": str(i), "race_date": f"2024-01-0{i + 1}", "ids": ("A", "B"),
             "labels": labels[i], "n_result_rows": 1 if i == 3 else 2} for i in range(4)]
    predictions = {str(i): {"A": (.7, 1., 1.), "B": (.3, 1., 1.)} for i in range(4)}
    s, losses = score(rows, predictions)
    assert (s["n_eligible"], s["n_complete"], s["n_started_scored"]) == (2, 3, 6)
    assert abs(s["winner_nll"] + np.log(.7)) < 1e-15
    assert [r[0] for r in losses] == ["0", "1"]
    assert abs(s["top2_logloss"] - (-np.log(1e-15) / 6)) < .001
    b = [("a", "2024-01-01", 1.), ("b", "2024-01-01", 2.), ("c", "2024-01-02", 3.)]
    c = [("a", "2024-01-01", 1.3), ("b", "2024-01-01", 2.3), ("c", "2024-01-02", 3.9)]
    ci = bootstrap(b, c, {"b": 4000, "seed": 7, "alpha": .05})
    assert np.allclose([ci["point"], ci["ci_low"], ci["ci_high"]], [.5, .3, .9], rtol=0, atol=1e-14)
    assert numpy_ece(np.array([.2] * 10), np.array([0, 1] * 5)) == {
        "ece": .3, "n_bins": 1, "bin_counts": [10]}
    print("SYNTHETIC INDEPENDENT AUDIT CHECKS PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    self_test() if args.self_test else run()
