"""136: independent recomputation of the 8-equal-member candidate (C1 / C2).

Independence boundary
---------------------
This script deliberately does NOT import the kill-test research code
(`member_kill_test.py`, `stage5_confirm.py`, `stage7_pair.py`).  Every number it
reports -- the mixing, the per-race winner NLL, the seed averaging, the day-cluster
bootstrap, the heads/ECE guards -- is implemented here from first principles.

It reuses only:
  * `accuracy_batch_common` for the hash-validated frozen input layer (111 snapshot +
    130 annual prediction caches).  That layer verifies its own SHA256s and was already
    independently re-verified under 135.
  * `horseracing_serving.mixture_correction` -- the PRODUCTION residual-correction
    routine.  Re-implementing it would test my copy, not the shipped rule.
  * `horseracing_eval.dataset.population_masks` / `metrics.ece_equal_mass` -- the
    canonical started-all label and ECE definitions.

No training, no DB, no model artefacts are touched.  Read-only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import pickle
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import accuracy_batch_common as common  # noqa: E402
from horseracing_eval.dataset import population_masks  # noqa: E402
from horseracing_eval.metrics import ece_equal_mass  # noqa: E402
from horseracing_serving import mixture_correction as mc  # noqa: E402

PRESERVED = ROOT / "artifacts/136-eight-model-candidate/preserved-killtest-2cced7b2"
CANDIDATE_CACHE = PRESERVED / "s2/cache/joint125"
SEEDS = (42, 43, 44)
YEARS = tuple(range(2020, 2027))
MEMBER_A = "extra_trees_joint"
MEMBER_B = "leaves63_child100_joint"
PAIR_W = 1.0 / 8.0
SINGLE_W = 1.0 / 7.0
BOOT_B = 4000
BOOT_SEED = 20260907
BOOT_ALPHA = 0.0125
HEADS = ("win", "top2", "top3")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ candidate loading

def load_candidate(name: str, seed: int, regime: str) -> tuple[dict, dict]:
    """Saved per-race raw heads for one candidate/seed, all seven years, plus receipts.

    Identity is re-derived here: the pickle's own SHA must equal the receipt's
    `cache_sha256`, the receipt must be about this candidate/seed/year, and it must not
    be a smoke fit.  A receipt that merely exists is not accepted.
    """
    preds: dict[int, dict] = {}
    receipts: dict[int, dict] = {}
    for year in YEARS:
        pkl = CANDIDATE_CACHE / f"{name}-s{seed}-{year}.pkl"
        rcp = CANDIDATE_CACHE / f"{name}-s{seed}-{year}.json"
        if not pkl.exists() or not rcp.exists():
            raise FileNotFoundError(f"missing member-year artefact: {pkl.name}")
        row = json.loads(rcp.read_text())
        sha = digest(pkl)
        if row["cache_sha256"] != sha:
            raise ValueError(f"receipt sha mismatch: {rcp.name}")
        if row["smoke"] is not False:
            raise ValueError(f"smoke fit offered as production: {rcp.name}")
        if (row["candidate"], row["year"], row["seed"]) != (name, year, seed):
            raise ValueError(f"receipt identity differs: {rcp.name}")
        with pkl.open("rb") as fh:
            payload = pickle.load(fh)
        if payload["can_adopt"] is not False or payload["eligible_for_verdict"] is not False:
            raise ValueError(f"research artefact claims adoption eligibility: {pkl.name}")
        preds[year] = payload["predictions"][regime]
        receipts[year] = {
            "cache_sha256": sha,
            "source_hash": row["source_hash"],
            "snapshot_sha256": row["snapshot_sha256"],
            "recipe_meta": row["recipe_meta"],
            "n_feature_columns": row["n_feature_columns"],
            "params_effective": row["params_effective"],
            "fit_seconds": row["fit_seconds"],
        }
    return preds, receipts


def corrected_candidate_heads(records, saved, coefficients, arm: str) -> dict:
    """Candidate's own corrected heads, per race.

    `raw` = no correction.  The transfers reuse the PRODUCTION routine with the frozen
    130 annual beta of the transfer source member, recomputing centered_logp from the
    candidate's own win vector (that is what `correct_member_predictions` does).
    """
    out = {}
    for r in records:
        raw = np.array([saved[r.year][r.race_id][h] for h in r.ids], dtype=float)
        if arm == "raw":
            out[r.race_id] = common.validate_heads(raw, len(r.ids))
            continue
        source = "joint-42" if arm == "joint_transfer" else "anchor-42"
        terms = mc.JOINT_TERMS if arm == "joint_transfer" else ["gap_log"]
        beta = coefficients[source][str(r.year)]
        corr = mc.correct_member_predictions(list(r.ids), raw[:, 0], r.correction_inputs, terms, beta)
        out[r.race_id] = common.validate_heads(
            np.array([[corr[h].win, corr[h].top2, corr[h].top3] for h in r.ids], dtype=float),
            len(r.ids))
    return out


# --------------------------------------------------------------------------- mixing

def mix(records, slots) -> dict:
    """(1 - Sum w) * six_member_mean + Sum_i w_i * member_i, accumulated per slot."""
    total = float(sum(w for w, _ in slots))
    if not 0.0 <= total <= 1.0:
        raise ValueError(f"weights outside [0,1]: {total}")
    out = {}
    for r in records:
        acc = (1.0 - total) * r.heads
        for w, std in slots:
            acc = acc + w * std[r.race_id]
        out[r.race_id] = acc
    return out


# --------------------------------------------------------------------------- scoring

def per_race_rows(records, predictions):
    """(race_id, race_date, year, winner_nll) for every ELIGIBLE race, plus head rows."""
    rows, head_p, head_y = [], [], []
    n_races = n_complete = 0
    days = set()
    for r in records:
        p = predictions[r.race_id]
        if p.shape != (len(r.ids), 3):
            raise ValueError("prediction shape differs from the started field")
        pop = population_masks(r.er)
        n_races += 1
        days.add(str(r.race_date))
        if pop.complete_results:
            n_complete += 1
            head_p.append(p)
            head_y.append(np.array(
                [[getattr(pop, f"started_{h}")[i] for h in HEADS] for i in r.ids], dtype=int))
        if pop.eligible:
            idx = r.ids.index(pop.winner_horse_id)
            rows.append((r.race_id, str(r.race_date), r.year, -float(np.log(p[idx, 0]))))
    return rows, (np.concatenate(head_p), np.concatenate(head_y)), {
        "n_races": n_races, "n_days": len(days), "n_complete_races": n_complete,
        "n_eligible_races": len(rows)}


def head_metrics(p, y) -> dict:
    q = np.clip(p, 1e-15, 1 - 1e-15)
    loss = -(y * np.log(q) + (1 - y) * np.log1p(-q))
    return {h: {"logloss": float(loss[:, j].mean()),
                "brier": float(((p[:, j] - y[:, j]) ** 2).mean()),
                "ece": ece_equal_mass(p[:, j], y[:, j])["ece"],
                "n_rows": int(len(p))}
            for j, h in enumerate(HEADS)}


def day_cluster_ci(rows, *, b=BOOT_B, seed=BOOT_SEED, alpha=BOOT_ALPHA):
    """race_day_cluster_bootstrap_ci_v1: resample whole race days, ratio estimator."""
    if not rows:
        return {"point": None, "ci": None, "n_days": 0, "n_races": 0}
    per_day: dict[str, list[float]] = {}
    for _, day, _, diff in rows:
        per_day.setdefault(day, []).append(diff)
    days = sorted(per_day)
    num = np.array([float(np.sum(per_day[d])) for d in days])
    den = np.array([float(len(per_day[d])) for d in days])
    rng = np.random.default_rng(seed)
    reps = np.empty(b)
    for start in range(0, b, 128):
        take = rng.integers(0, len(days), size=(min(128, b - start), len(days)))
        reps[start:start + len(take)] = num[take].sum(axis=1) / den[take].sum(axis=1)
    return {"point": float(num.sum() / den.sum()),
            "ci": np.quantile(reps, [alpha / 2, 1 - alpha / 2]).tolist(),
            "n_days": len(days), "n_races": int(den.sum())}


def paired_rows(base_rows, cand_rows):
    """Per-race (race_id, day, year, cand_nll - base_nll).  Populations must match."""
    bmap = {r[0]: r for r in base_rows}
    if set(bmap) != {r[0] for r in cand_rows} or len(bmap) != len(cand_rows):
        raise ValueError("paired race populations differ")
    out = []
    for rid, day, year, nll in cand_rows:
        b = bmap[rid]
        if (b[1], b[2]) != (day, year):
            raise ValueError("paired race date/year differ")
        out.append((rid, day, year, nll - b[3]))
    return out


def mean_over_seeds(rows_by_seed):
    """Average the per-race LOSS DIFFERENCE across seeds.  Never average probabilities."""
    keys = [{r[0] for r in rows} for rows in rows_by_seed]
    if any(k != keys[0] for k in keys):
        raise ValueError("seed race populations differ")
    acc: dict[str, list] = {}
    for rows in rows_by_seed:
        for rid, day, year, d in rows:
            slot = acc.setdefault(rid, [day, year, 0.0])
            if (slot[0], slot[1]) != (day, year):
                raise ValueError("seed race metadata differ")
            slot[2] += d
    n = len(rows_by_seed)
    return [(rid, v[0], v[1], v[2] / n) for rid, v in sorted(acc.items())]


# ------------------------------------------------------------------------------ main

def run(regime: str, arm: str, years: tuple[int, ...], out_path: Path):
    inputs = common.load_frozen_inputs()
    coefficients = inputs.freeze130["coefficients"]
    records = [r for r in common.iter_mixed_records(inputs, regime=regime) if r.year in years]
    if not records:
        raise ValueError("no records in the requested years")

    baseline = {r.race_id: common.validate_heads(r.heads, len(r.ids)) for r in records}
    base_rows, (bp, by), pop_info = per_race_rows(records, baseline)
    base_heads = head_metrics(bp, by)
    base_nll = float(np.mean([r[3] for r in base_rows]))

    result = {
        "artifact_kind": "eight_model_independent_recheck",
        "schema_version": 1,
        "computed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "independence": ("mixing, scoring, seed averaging, bootstrap and guards are "
                         "implemented in this file; member_kill_test / stage5_confirm / "
                         "stage7_pair are NOT imported"),
        "regime": regime, "correction_arm": arm, "years": list(years),
        "pair_weight": PAIR_W, "single_weight": SINGLE_W,
        "bootstrap": {"fn": "race_day_cluster_bootstrap_ci_v1", "b": BOOT_B,
                      "seed": BOOT_SEED, "alpha": BOOT_ALPHA},
        "baseline": {**pop_info, "winner_nll": base_nll, "heads": base_heads},
        "provenance": {"snapshot_sha256": inputs.provenance["snapshot_sha256"],
                       "freeze130_sha256": inputs.provenance["freeze130_sha256"],
                       "actual_data_through": inputs.provenance["actual_data_through"],
                       "n_130_caches_verified": len(inputs.provenance["cache_sha256"]),
                       "candidate_receipts": {}},
        "comparisons": {}, "can_adopt": False, "eligible_for_verdict": False,
    }

    std = {}
    for name in (MEMBER_A, MEMBER_B):
        for seed in SEEDS:
            saved, receipts = load_candidate(name, seed, regime)
            result["provenance"]["candidate_receipts"][f"{name}-s{seed}"] = receipts
            std[(name, seed)] = corrected_candidate_heads(records, saved, coefficients, arm)

    def compare(cand_by_seed, label):
        rows_by_seed, guards_by_seed, nlls = [], [], []
        for seed in SEEDS:
            cand = cand_by_seed[seed]
            c_rows, (cp, cy), c_pop = per_race_rows(records, cand)
            if c_pop != pop_info:
                raise ValueError("candidate scoring population differs from baseline")
            if not np.array_equal(cy, by):
                raise ValueError("candidate labels differ from baseline")
            rows_by_seed.append(paired_rows(base_rows, c_rows))
            ch = head_metrics(cp, cy)
            guards_by_seed.append({
                "top2_logloss": ch["top2"]["logloss"] - base_heads["top2"]["logloss"],
                "top3_logloss": ch["top3"]["logloss"] - base_heads["top3"]["logloss"],
                "win_ece": ch["win"]["ece"] - base_heads["win"]["ece"]})
            nlls.append(float(np.mean([r[3] for r in c_rows])))
        mean_rows = mean_over_seeds(rows_by_seed)
        per_seed = [float(np.mean([r[3] for r in rows])) for rows in rows_by_seed]
        entry = {
            "label": label,
            "primary": day_cluster_ci(mean_rows),
            "by_year": {str(y): day_cluster_ci([r for r in mean_rows if r[2] == y])
                        for y in years},
            "seed_mean_increment": float(np.mean(per_seed)),
            "seed_sd_increment": float(np.std(per_seed, ddof=1)) if len(per_seed) > 1 else None,
            "per_seed_increment": {str(s): v for s, v in zip(SEEDS, per_seed, strict=True)},
            "candidate_winner_nll_per_seed": {str(s): v for s, v in zip(SEEDS, nlls, strict=True)},
            "guards_mean": {k: float(np.mean([g[k] for g in guards_by_seed]))
                            for k in ("top2_logloss", "top3_logloss", "win_ece")},
            "guards_per_seed": {str(s): g for s, g in zip(SEEDS, guards_by_seed, strict=True)},
        }
        return entry

    pair = {s: mix(records, [(PAIR_W, std[(MEMBER_A, s)]), (PAIR_W, std[(MEMBER_B, s)])])
            for s in SEEDS}
    # Twelve equal members: every seed of every branch, the rule the production bundle already
    # follows (specs/136-eight-model-candidate/configuration-rule.json, frozen before this was
    # computed). It is a single concrete configuration, not an average over a seed choice, so it
    # has no per-seed spread -- the same mixture is repeated in all three slots so the existing
    # seed-matched aggregation returns it unchanged.
    pooled_a = {r.race_id: sum(std[(MEMBER_A, s)][r.race_id] for s in SEEDS) / 3 for r in records}
    pooled_b = {r.race_id: sum(std[(MEMBER_B, s)][r.race_id] for s in SEEDS) / 3 for r in records}
    # Pooling probabilities inside a branch is exactly what the production bundle already does
    # across its own seeds; the 1/8 slot weight keeps the branch's registered exposure.
    u_mix = mix(records, [(PAIR_W, pooled_a), (PAIR_W, pooled_b)])
    twelve_slots = [(1.0 / 12.0, std[(name, s)]) for name in (MEMBER_A, MEMBER_B) for s in SEEDS]
    twelve_one = mix(records, twelve_slots)
    twelve = {s: twelve_one for s in SEEDS}
    a_alone = {s: mix(records, [(SINGLE_W, std[(MEMBER_A, s)])]) for s in SEEDS}
    b_alone = {s: mix(records, [(SINGLE_W, std[(MEMBER_B, s)])]) for s in SEEDS}

    result["comparisons"]["C1_total_pair_vs_six"] = compare(pair, "8 equal members vs current 6")
    result["comparisons"]["D1_twelve_vs_six"] = compare(
        twelve, "12 equal members (all seeds of A and B) vs current 6")
    # U: all seeds of A and B, but the branch allocation of the PRE-REGISTERED eight
    # (joint .375 / anchor .375 / A .125 / B .125). Member weights are unequal by design:
    # joint and anchor seeds at 1/8, A and B seeds at 1/24. This is the configuration selected
    # in configuration-rule-amendment.json, so it needs its own headline and head guards --
    # it is not merely an intermediate in the D2 decomposition.
    u_by_seed = {s: u_mix for s in SEEDS}
    result["comparisons"]["U_allseeds_registered_alloc_vs_six"] = compare(
        u_by_seed, "12 artifacts, registered allocation (A,B pooled at 1/8 combined) vs current 6")
    result["comparisons"]["A_alone_vs_six"] = compare(a_alone, f"{MEMBER_A} at 1/7 vs current 6")
    result["comparisons"]["B_alone_vs_six"] = compare(b_alone, f"{MEMBER_B} at 1/7 vs current 6")

    # C2: pair vs A alone.  Paired at the race level against the A-alone arm, not the six.
    rows_by_seed, guards_by_seed = [], []
    for seed in SEEDS:
        a_rows, (ap, ay), _ = per_race_rows(records, a_alone[seed])
        p_rows, (pp, py), _ = per_race_rows(records, pair[seed])
        rows_by_seed.append(paired_rows(a_rows, p_rows))
        ah, ph = head_metrics(ap, ay), head_metrics(pp, py)
        guards_by_seed.append({
            "top2_logloss": ph["top2"]["logloss"] - ah["top2"]["logloss"],
            "top3_logloss": ph["top3"]["logloss"] - ah["top3"]["logloss"],
            "win_ece": ph["win"]["ece"] - ah["win"]["ece"]})
    mean_rows = mean_over_seeds(rows_by_seed)
    per_seed = [float(np.mean([r[3] for r in rows])) for rows in rows_by_seed]
    by_year = {str(y): day_cluster_ci([r for r in mean_rows if r[2] == y]) for y in years}
    year_points = [by_year[str(y)]["point"] for y in years]
    primary = day_cluster_ci(mean_rows)
    guards_mean = {k: float(np.mean([g[k] for g in guards_by_seed]))
                   for k in ("top2_logloss", "top3_logloss", "win_ece")}
    seed_mean = float(np.mean(per_seed))
    result["comparisons"]["C2_decision_pair_vs_A_alone"] = {
        "label": "8 equal members vs A alone at 1/7 (the registered decision)",
        "primary": primary, "by_year": by_year,
        "seed_mean_increment": seed_mean,
        "seed_sd_increment": float(np.std(per_seed, ddof=1)),
        "per_seed_increment": {str(s): v for s, v in zip(SEEDS, per_seed, strict=True)},
        "guards_mean": guards_mean,
        "guards_per_seed": {str(s): g for s, g in zip(SEEDS, guards_by_seed, strict=True)},
        "registered_checks": {
            "mean_increment_at_or_below_floor": seed_mean <= -0.0004,
            "ci_upper_below_zero": primary["ci"] is not None and primary["ci"][1] < 0,
            "no_guard_fail": (guards_mean["top2_logloss"] <= 0.0005
                              and guards_mean["top3_logloss"] <= 0.0005
                              and guards_mean["win_ece"] <= 0.001),
            "no_materially_bad_year": all(p < 0.0004 for p in year_points),
            "majority_of_years_negative": sum(p < 0 for p in year_points) >= 5,
        },
        "n_years_negative": int(sum(p < 0 for p in year_points)),
        "n_years": len(year_points),
        "worst_year": float(max(year_points)),
    }
    checks = result["comparisons"]["C2_decision_pair_vs_A_alone"]["registered_checks"]
    result["comparisons"]["C2_decision_pair_vs_A_alone"]["all_checks_pass"] = all(checks.values())

    # Decomposition of D2 (independent review, 2026-09-22). Going 8 -> 12 does TWO things at
    # once: it pools the three seeds of each candidate, and it doubles the candidates' combined
    # weight (0.25 -> 0.50). U is the intermediate that holds the weights at the 8-member values
    # while already pooling the seeds, so the two effects separate exactly:
    #     D2 = [L(U) - mean_s L(M_8,s)]  +  [L(M_12) - L(U)]
    #           seed pooling at fixed w      branch-weight change after pooling
    u_rows, _, _ = per_race_rows(records, u_mix)
    pair_rows_by_seed = [paired_rows(per_race_rows(records, pair[s])[0], u_rows) for s in SEEDS]
    seed_pooling = day_cluster_ci(mean_over_seeds(pair_rows_by_seed))
    twelve_rows_for_u, _, _ = per_race_rows(records, twelve_one)
    weight_change = day_cluster_ci(paired_rows(u_rows, twelve_rows_for_u))
    result["d2_decomposition"] = {
        "note": ("D2 = seed_pooling_at_fixed_weight + branch_weight_change. The first term is "
                 "non-positive by convexity of -log; the second is what doubling the candidates' "
                 "combined weight from 0.25 to 0.50 buys. Non-binding, development window."),
        "seed_pooling_at_fixed_weight": seed_pooling,
        "branch_weight_change_after_pooling": weight_change,
        "sum_check": seed_pooling["point"] + weight_change["point"],
    }

    # D2: twelve equal members vs the pre-registered eight. Paired against each of the three
    # eight-member mixtures, so the comparison inherits the pair's seed spread, not the twelve's
    # (the twelve-member mixture is one artefact and has none).
    rows_by_seed, guards_by_seed = [], []
    t_rows, (tp, ty), _ = per_race_rows(records, twelve_one)
    th = head_metrics(tp, ty)
    for seed in SEEDS:
        p_rows, (pp, py), _ = per_race_rows(records, pair[seed])
        rows_by_seed.append(paired_rows(p_rows, t_rows))
        ph = head_metrics(pp, py)
        guards_by_seed.append({
            "top2_logloss": th["top2"]["logloss"] - ph["top2"]["logloss"],
            "top3_logloss": th["top3"]["logloss"] - ph["top3"]["logloss"],
            "win_ece": th["win"]["ece"] - ph["win"]["ece"]})
    mean_rows12 = mean_over_seeds(rows_by_seed)
    per_seed12 = [float(np.mean([r[3] for r in rows])) for rows in rows_by_seed]
    result["comparisons"]["D2_twelve_vs_pair"] = {
        "label": "12 equal members vs the pre-registered 8 (non-binding sizing)",
        "primary": day_cluster_ci(mean_rows12),
        "by_year": {str(y): day_cluster_ci([r for r in mean_rows12 if r[2] == y]) for y in years},
        "seed_mean_increment": float(np.mean(per_seed12)),
        "seed_sd_increment": float(np.std(per_seed12, ddof=1)),
        "per_seed_increment": {str(s): v for s, v in zip(SEEDS, per_seed12, strict=True)},
        "guards_mean": {k: float(np.mean([g[k] for g in guards_by_seed]))
                        for k in ("top2_logloss", "top3_logloss", "win_ece")},
        "binding": False,
        "note": ("configuration-rule.json fixes the 12-member rule; this number is sizing only. "
                 "All seven years are development period, so it cannot confirm anything."),
    }

    # R3 self-consistency: the same candidate in both slots at 1/8 == once at 2/8.
    twice = mix(records, [(PAIR_W, std[(MEMBER_A, 42)]), (PAIR_W, std[(MEMBER_A, 42)])])
    once = mix(records, [(2 * PAIR_W, std[(MEMBER_A, 42)])])
    result["self_consistency_two_slots_vs_double_weight"] = float(
        max(np.max(np.abs(twice[r.race_id] - once[r.race_id])) for r in records))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("x") as fh:
        json.dump(result, fh, indent=2, sort_keys=True, default=str, allow_nan=False)
        fh.write("\n")
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--regime", default="preweight", choices=("preweight", "full"))
    ap.add_argument("--arm", default="joint_transfer",
                    choices=("joint_transfer", "anchor_transfer", "raw"))
    ap.add_argument("--years", default="2020-2026")
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    lo, _, hi = a.years.partition("-")
    years = tuple(range(int(lo), int(hi or lo) + 1))
    if not set(years) <= set(YEARS):
        raise SystemExit(f"years outside the frozen build window {YEARS}")
    res = run(a.regime, a.arm, years, a.out)
    for key in ("C1_total_pair_vs_six", "A_alone_vs_six", "B_alone_vs_six",
                "C2_decision_pair_vs_A_alone", "D1_twelve_vs_six", "D2_twelve_vs_pair",
                "U_allseeds_registered_alloc_vs_six"):
        e = res["comparisons"][key]
        ci = e["primary"]["ci"]
        print(f"{key:34s} mean={e['seed_mean_increment']:+.9f} "
              f"CI=[{ci[0]:+.9f},{ci[1]:+.9f}] sd={e['seed_sd_increment']:.9f}")
    dec = res["d2_decomposition"]
    print(f"  D2 decomposition: seed_pooling={dec['seed_pooling_at_fixed_weight']['point']:+.9f}"
          f"  weight_change={dec['branch_weight_change_after_pooling']['point']:+.9f}"
          f"  sum={dec['sum_check']:+.9f}")
    print("R3 max|two slots - double weight| =",
          res["self_consistency_two_slots_vs_double_weight"])
    print("wrote", a.out)


if __name__ == "__main__":
    main()
