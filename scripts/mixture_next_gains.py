"""134: next-gain research on top of the served six-member mixture — NO retraining.

Reads the frozen 130 walk-forward cache (6 members x 7 years x 2 regimes, fresh annual fits on the
111 snapshot, research annual coefficients) and the 111 snapshot labels, and answers two things
without fitting a single booster:

1. ``diagnose``  — where the CURRENT mixture (equal weights, annual residual correction) is weak:
   by year / venue / track / class / field size / distance / month, calibration per year, member
   contributions, the cost of hiding today's weight. Restricted to the SELECTION years
   (2020-2024) so the later years stay available as a confirmation window for this round.
2. ``hypotheses`` — cheap post-hoc changes of the mixture evaluated strictly walk-forward (every
   parameter is chosen on years < Y and applied to year Y): mixture temperature, geometric mean,
   branch weight, correction scale, drop-one-member, and their stack. ``--stage selection`` scores
   2021-2024; ``--stage confirmation`` scores 2025-2026 and is only allowed once a pre-registration
   file exists (its SHA is recorded).

Baseline everywhere = the served recipe reconstructed exactly as 130 did (parity with 130's
walkforward-races.json is asserted). Outputs are append-only JSON under
artifacts/134-mixture-next-gains and specs/134-mixture-next-gains/evidence.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import pickle
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "serving/src"))
from horseracing_eval.baselines import harville_topk  # noqa: E402
from horseracing_eval.bootstrap import race_day_cluster_bootstrap_ci_v1  # noqa: E402
from horseracing_eval.dataset import population_masks  # noqa: E402
from horseracing_eval.metrics import ece_equal_mass  # noqa: E402
from horseracing_eval.stage_discount import (  # noqa: E402
    TopkSample,
    discounted_topk,
    fit_stage_discount,
)
from horseracing_serving import mixture_correction as mc  # noqa: E402

SOURCE = ROOT / "artifacts/130-mixture-preweight-walkforward"
SNAPSHOT = ROOT / "artifacts/111-ability-observation/snapshot.pkl"
SNAPSHOT_SHA256 = "58397ba94dc685c6e49b509619fb447106702d869adebc38889b021f3be6859a"
WORK = ROOT / "artifacts/134-mixture-next-gains"
SPEC = ROOT / "specs/134-mixture-next-gains"
YEARS = tuple(range(2020, 2027))
SELECTION_YEARS = (
    2021,
    2022,
    2023,
    2024,
)  # scored years of the selection stage (2020 = warm-up)
CONFIRMATION_YEARS = (2025, 2026)
DIAG_YEARS = (2020, 2021, 2022, 2023, 2024)
MEMBERS = tuple(f"{b}-{s}" for b in ("joint", "anchor") for s in (42, 43, 44))
JOINT = (0, 1, 2)
ANCHOR = (3, 4, 5)
REGIMES = ("preweight", "full")
BOOT = dict(b=4000, seed=20260913)
TEMPERATURE_GRID = np.round(np.arange(0.80, 1.301, 0.01), 2)
WEIGHT_GRID = np.round(np.arange(0.0, 1.001, 0.1), 1)
SCALE_GRID = (0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as fh:
        json.dump(
            value,
            fh,
            ensure_ascii=False,
            indent=1,
            sort_keys=True,
            allow_nan=False,
            default=str,
        )


# --- stage 0: compact dataset from the frozen cache ------------------------------------------------


def build(out_path):
    """One compact record per eligible race: base member log-wins, correction exponents, labels."""
    meta = json.loads(SNAPSHOT.with_suffix(".json").read_text())
    if (
        digest(SNAPSHOT) != SNAPSHOT_SHA256
        or meta["snapshot_sha256"] != SNAPSHOT_SHA256
    ):
        raise ValueError("111 snapshot identity differs")
    frozen = json.loads((SOURCE / "run-freeze.json").read_text())
    if frozen["snapshot_sha256"] != SNAPSHOT_SHA256:
        raise ValueError("130 freeze points at another snapshot")
    with SNAPSHOT.open("rb") as fh:
        matrix, races = pickle.load(fh)
    frame = matrix.frame
    years = pd.to_datetime(frame.race_date).dt.year
    target = frame.loc[years.isin(YEARS), mc.KEYS + ["days_since_last", "sex"]]
    corr = mc.build_correction_inputs(target, frame[mc.KEYS])
    per_race = {
        rid: g.set_index("horse_id", drop=False)
        for rid, g in corr.groupby("race_id", sort=False)
    }
    records, cache_sha = [], {}
    ref = json.loads(
        (
            ROOT
            / "specs/130-mixture-preweight-walkforward/evidence/walkforward-races.json"
        ).read_text()
    )
    ref_rows = {r["race_id"]: r for r in ref["rows"]}
    worst = 0.0
    for year in YEARS:
        caches = {}
        for m in MEMBERS:
            p = SOURCE / "cache" / f"{m}-{year}.pkl"
            receipt = json.loads((SOURCE / "receipts" / f"{m}-{year}.json").read_text())
            sha = digest(p)
            if receipt.get("cache_sha256") != sha or receipt.get("smoke") is not False:
                raise ValueError(f"cache receipt mismatch {m}-{year}")
            cache_sha[p.name] = sha
            caches[m] = pickle.load(p.open("rb"))
        for er in [r for r in races if r.context.race_date.year == year]:
            pop = population_masks(er)
            if not pop.eligible:
                continue
            ids = [h.horse_id for h in er.context.started_horses]
            c = per_race[er.context.race_id].loc[ids]
            h = np.nan_to_num(c[mc.INPUT_TERMS].to_numpy(dtype=float), nan=0.0)  # n x 4
            rec = {
                "race_id": er.context.race_id,
                "race_day": str(er.context.race_date),
                "year": year,
                "ids": ids,
                "winner": ids.index(pop.winner_horse_id),
                "top2": np.array([pop.started_top2[i] for i in ids], dtype=int),
                "top3": np.array([pop.started_top3[i] for i in ids], dtype=int),
                "h": h,
            }
            for reg in REGIMES:
                logp = np.column_stack(
                    [
                        np.log(
                            np.array(
                                [
                                    caches[m]["predictions"][reg][er.context.race_id][
                                        i
                                    ][0]
                                    for i in ids
                                ]
                            )
                        )
                        for m in MEMBERS
                    ]
                )  # n x 6
                z = np.zeros_like(logp)
                for j, m in enumerate(MEMBERS):
                    beta = np.asarray(frozen["coefficients"][m][str(year)], dtype=float)
                    if m.startswith("joint"):
                        z[:, j] = h @ beta[:4] + beta[4] * (
                            logp[:, j] - logp[:, j].mean()
                        )
                    else:
                        z[:, j] = h[:, 0] * beta[0]
                rec[f"logp_{reg}"] = logp
                rec[f"z_{reg}"] = z
            # parity with 130: equal-weight arithmetic mean of corrected members, preweight & full
            for reg, key in (
                ("preweight", "preweight_candidate_nll"),
                ("full", "full_candidate_nll"),
            ):
                q = corrected(rec[f"logp_{reg}"], rec[f"z_{reg}"], 1.0)
                mix = q.mean(axis=1)
                worst = max(
                    worst,
                    abs(-math.log(mix[rec["winner"]]) - ref_rows[rec["race_id"]][key]),
                )
                a = -math.log(np.exp(rec[f"logp_{reg}"][rec["winner"], 3]))
                worst = max(
                    worst,
                    abs(
                        a
                        - ref_rows[rec["race_id"]][
                            f"{reg.replace('preweight', 'preweight')}_anchor_nll"
                        ]
                    ),
                )
            records.append(rec)
    if worst > 1e-9:
        raise ValueError(
            f"Reconstruction does not reproduce 130 (max |diff| {worst:.3e})"
        )
    out = {
        "records": records,
        "cache_sha256": cache_sha,
        "snapshot_sha256": SNAPSHOT_SHA256,
        "freeze130_sha256": digest(SOURCE / "run-freeze.json"),
        "parity_max_abs_diff_vs_130": worst,
        "coefficients": frozen["coefficients"],
        "built_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with Path(out_path).open("xb") as fh:
        pickle.dump(out, fh)
    print(json.dumps({"n_races": len(records), "parity_max_abs_diff_vs_130": worst}))


# --- pure mixture algebra -----------------------------------------------------------------------------


def corrected(logp, z, scale):
    """Per-member corrected win q_m ∝ p_m·exp(scale·z_m) (n x 6), each column sums to 1."""
    x = logp + scale * z
    x = x - x.max(axis=0, keepdims=True)
    q = np.exp(x)
    return q / q.sum(axis=0, keepdims=True)


def mix_win(q, *, w_joint=0.5, geometric=False, drop=None, gamma=1.0):
    """Combine member win columns. w_joint = total weight of the 125-column side (0.5 = equal 1/6)."""
    cols = list(range(6))
    if drop is not None:
        cols.remove(drop)
    jw = [c for c in cols if c in JOINT]
    aw = [c for c in cols if c in ANCHOR]
    weights = np.zeros(6)
    if jw:
        weights[jw] = w_joint / len(jw)
    if aw:
        weights[aw] = (1 - w_joint) / len(aw)
    if not jw:
        weights[aw] = 1.0 / len(aw)
    if not aw:
        weights[jw] = 1.0 / len(jw)
    if geometric:
        m = np.exp((np.log(q) * weights).sum(axis=1))
    else:
        m = (q * weights).sum(axis=1)
    m = m / m.sum()
    if gamma != 1.0:
        m = m**gamma
        m = m / m.sum()
    return m


def nll(p, winner):
    return -math.log(max(p[winner], 1e-300))


# --- evaluation helpers --------------------------------------------------------------------------------


def ci(by_day, alpha):
    c = race_day_cluster_bootstrap_ci_v1(
        by_day, b=BOOT["b"], seed=BOOT["seed"], alpha=alpha
    )
    return {
        "point": c.point,
        "lo": c.ci_low,
        "hi": c.ci_high,
        "n_days": c.n_days,
        "n_races": int(sum(len(v) for v in by_day.values())),
    }


def summarize_diffs(rows):
    """rows: list of (day, diff)."""
    by_day = defaultdict(list)
    for d, v in rows:
        by_day[d].append(v)
    if len(by_day) < 2:
        return {
            "point": float(np.mean([v for _, v in rows])) if rows else None,
            "n_days": len(by_day),
            "n_races": len(rows),
            "ci95": None,
            "ci9875": None,
        }
    a, b = ci(by_day, 0.05), ci(by_day, 0.0125)
    return {
        "point": a["point"],
        "n_days": a["n_days"],
        "n_races": a["n_races"],
        "ci95": [a["lo"], a["hi"]],
        "ci9875": [b["lo"], b["hi"]],
        "share_improved": float(np.mean([v < 0 for _, v in rows])),
    }


def _finishers(winner, top2, top3):
    i2 = [i for i, v in enumerate(top2) if v == 1 and i != winner]
    i3 = [i for i, v in enumerate(top3) if v == 1 and top2[i] == 0]
    return winner, (i2[0] if len(i2) == 1 else None), (i3[0] if len(i3) == 1 else None)


def head_quality(pairs, *, stage_discount=None):
    """pairs: list of (win_vector, top2_labels, top3_labels, winner). Heads from win via Harville
    (λ=1) or via the given StageDiscount (production convention)."""
    p2, p3, y2, y3, pw, yw = [], [], [], [], [], []
    for win, t2, t3, winner in pairs:
        if stage_discount is None or stage_discount.is_identity:
            a, b = harville_topk(list(win))
        else:
            a, b = discounted_topk(list(win), stage_discount)
        p2.extend(a)
        p3.extend(b)
        y2.extend(t2)
        y3.extend(t3)
        pw.extend(win)
        yw.extend([1 if i == winner else 0 for i in range(len(win))])
    out = {}
    for name, p, y in (("win", pw, yw), ("top2", p2, y2), ("top3", p3, y3)):
        p = np.clip(np.asarray(p, dtype=float), 1e-15, 1 - 1e-15)
        y = np.asarray(y, dtype=float)
        out[name] = {
            "logloss": float(np.mean(-(y * np.log(p) + (1 - y) * np.log(1 - p)))),
            "brier": float(np.mean((p - y) ** 2)),
            "ece_equal_mass_10": ece_equal_mass(p, y, bins=10)["ece"],
            "n": int(len(p)),
        }
    return out


def fit_lambda(pairs):
    samples = [
        TopkSample(tuple(float(v) for v in win), *_finishers(w, t2, t3))
        for win, t2, t3, w in pairs
    ]
    return fit_stage_discount(samples)


def _race_head_losses(pairs, sd):
    """Per race: (mean horse logloss top2, top3) and the pooled probability/label vectors."""
    per_race, pooled = [], {h: ([], []) for h in ("win", "top2", "top3")}
    for win, t2, t3, winner in pairs:
        if sd is None or sd.is_identity:
            a, b = harville_topk(list(win))
        else:
            a, b = discounted_topk(list(win), sd)
        yw = [1 if i == winner else 0 for i in range(len(win))]
        losses = {}
        for name, p, y in (("win", win, yw), ("top2", a, t2), ("top3", b, t3)):
            p = np.clip(np.asarray(p, dtype=float), 1e-15, 1 - 1e-15)
            y = np.asarray(y, dtype=float)
            losses[name] = float(np.mean(-(y * np.log(p) + (1 - y) * np.log(1 - p))))
            pooled[name][0].extend(p.tolist())
            pooled[name][1].extend(y.tolist())
        per_race.append(losses)
    return per_race, pooled


def heads_delta(cand_by_year, base_by_year, eval_years, days_by_year):
    """Quality deltas (candidate − baseline) over the eval years under two head conventions:
    plain Harville (λ=1) and the production convention (λ re-fit per arm on prior years).
    ECE is POOLED over all eval-year horses; logloss deltas carry a paired per-race day-cluster CI."""
    out = {}
    for conv in ("harville_lambda1", "walkforward_lambda"):
        pooled_c = {h: ([], []) for h in ("win", "top2", "top3")}
        pooled_b = {h: ([], []) for h in ("win", "top2", "top3")}
        diffs = {h: [] for h in ("win", "top2", "top3")}
        lam = {}
        for y in eval_years:
            sd_c = sd_b = None
            if conv == "walkforward_lambda":
                prior_c = [x for yy in cand_by_year if yy < y for x in cand_by_year[yy]]
                prior_b = [x for yy in base_by_year if yy < y for x in base_by_year[yy]]
                sd_c, sd_b = fit_lambda(prior_c), fit_lambda(prior_b)
                lam[str(y)] = {
                    "candidate": [sd_c.lambda2, sd_c.lambda3, sd_c.fallback],
                    "baseline": [sd_b.lambda2, sd_b.lambda3, sd_b.fallback],
                }
            rc, pc = _race_head_losses(cand_by_year[y], sd_c)
            rb, pb = _race_head_losses(base_by_year[y], sd_b)
            for h in diffs:
                pooled_c[h][0].extend(pc[h][0])
                pooled_c[h][1].extend(pc[h][1])
                pooled_b[h][0].extend(pb[h][0])
                pooled_b[h][1].extend(pb[h][1])
                diffs[h].extend(
                    (d, c[h] - b[h])
                    for d, c, b in zip(days_by_year[y], rc, rb, strict=True)
                )
        block = {}
        for h in diffs:
            pcv, ycv = np.asarray(pooled_c[h][0]), np.asarray(pooled_c[h][1])
            pbv, ybv = np.asarray(pooled_b[h][0]), np.asarray(pooled_b[h][1])
            block[h] = {
                "logloss_paired_per_race": summarize_diffs(diffs[h]),
                "brier_delta": float(
                    np.mean((pcv - ycv) ** 2) - np.mean((pbv - ybv) ** 2)
                ),
                "ece_pooled_delta": ece_equal_mass(pcv, ycv, bins=10)["ece"]
                - ece_equal_mass(pbv, ybv, bins=10)["ece"],
                "ece_pooled_candidate": ece_equal_mass(pcv, ycv, bins=10)["ece"],
                "n_horses": int(len(pcv)),
            }
        if lam:
            block["lambda_by_year"] = lam
        out[conv] = block
    return out


# --- stage 1: diagnose ----------------------------------------------------------------------------------


def load(ds_path):
    with Path(ds_path).open("rb") as fh:
        return pickle.load(fh)


def attach_attrs(records):
    attrs = pd.read_parquet(WORK / "race_attrs.parquet").set_index("race_id")
    for r in records:
        a = attrs.loc[r["race_id"]]
        n = len(r["ids"])
        r["attrs"] = {
            "venue": str(a.venue_code),
            "track": str(a.track_type),
            "class": "maiden"
            if a.race_class in ("新馬", "未勝利")
            else "conditions"
            # Both spellings. JRA-VAN wrote `1勝/2勝/3勝` (..2025-10-05), netkeiba writes the
            # full-width `１勝/２勝/３勝` (2025-10-11..); see features/race_class_canon.py (098).
            # Matching only the full-width form filed every JRA-VAN-era conditioned race under
            # "open" — 8,068 of the 17,247 races in the 2020-2024 diagnosis window.
            if a.race_class in ("1勝", "2勝", "3勝", "１勝", "２勝", "３勝")
            else "open",
            "class_raw": str(a.race_class),
            "field": "<=8" if n <= 8 else "9-13" if n <= 13 else ">=14",
            "distance": "<=1400"
            if a.distance <= 1400
            else "<=1800"
            if a.distance <= 1800
            else "<=2200"
            if a.distance <= 2200
            else ">2200",
            "month": int(str(r["race_day"])[5:7]),
            "weighed_share_in_db": float(a.n_weighed / a.n_started)
            if a.n_started
            else None,
        }


def baseline_nll(rec, reg):
    q = corrected(rec[f"logp_{reg}"], rec[f"z_{reg}"], 1.0)
    return nll(q.mean(axis=1), rec["winner"])


def fit_gamma(rows):
    """rows: list of (win_vector, winner). Golden-section on the power exponent (winner NLL)."""

    def loss(g):
        tot = 0.0
        for p, w in rows:
            m = p**g
            tot += -math.log(m[w] / m.sum())
        return tot / len(rows)

    lo, hi = 0.5, 2.0
    phi = (math.sqrt(5) - 1) / 2
    a, b = hi - phi * (hi - lo), lo + phi * (hi - lo)
    fa, fb = loss(a), loss(b)
    for _ in range(40):
        if fa < fb:
            hi, b, fb = b, a, fa
            a = hi - phi * (hi - lo)
            fa = loss(a)
        else:
            lo, a, fa = a, b, fb
            b = lo + phi * (hi - lo)
            fb = loss(b)
    return (lo + hi) / 2


def diagnose(ds_path, output, years=DIAG_YEARS):
    global DIAG_YEARS
    DIAG_YEARS = tuple(years)
    data = load(ds_path)
    records = [r for r in data["records"] if r["year"] in DIAG_YEARS]
    attach_attrs(records)
    out = {
        "artifact_kind": "134_mixture_weakness_diagnosis",
        "years": list(DIAG_YEARS),
        "baseline": "served recipe: equal-weight mean of six annually corrected members (130 reconstruction)",
        "comparator": "anchor-42 (138 columns, seed 42, no correction) = the old production recipe",
        "n_races": len(records),
    }
    for reg in REGIMES:
        per = []
        for r in records:
            q = corrected(r[f"logp_{reg}"], r[f"z_{reg}"], 1.0)
            mix = q.mean(axis=1)
            w = r["winner"]
            per.append(
                {
                    "day": r["race_day"],
                    "year": r["year"],
                    "attrs": r["attrs"],
                    "n": len(r["ids"]),
                    "mix": nll(mix, w),
                    "anchor": -r[f"logp_{reg}"][w, 3],
                    "uniform": math.log(len(r["ids"])),
                    "members": [-math.log(q[w, j]) for j in range(6)],
                    "loo": [nll(mix_win(q, drop=j), w) for j in range(6)],
                    "nocorr": nll(
                        np.exp(r[f"logp_{reg}"]).mean(axis=1)
                        / np.exp(r[f"logp_{reg}"]).mean(axis=1).sum(),
                        w,
                    ),
                    "mix_vec": mix,
                    "top2": r["top2"],
                    "top3": r["top3"],
                    "winner": w,
                }
            )
        block = {}

        def cell(rows):
            d = summarize_diffs([(x["day"], x["mix"] - x["anchor"]) for x in rows])
            d.update(
                mix_nll=float(np.mean([x["mix"] for x in rows])),
                anchor_nll=float(np.mean([x["anchor"] for x in rows])),
                uniform_nll=float(np.mean([x["uniform"] for x in rows])),
                n_races=len(rows),
            )
            years_sign = {}
            for y in DIAG_YEARS:
                ys = [x for x in rows if x["year"] == y]
                if ys:
                    years_sign[str(y)] = float(
                        np.mean([x["mix"] - x["anchor"] for x in ys])
                    )
            d["by_year_diff"] = years_sign
            d["years_mix_better"] = sum(1 for v in years_sign.values() if v < 0)
            d["persistent"] = (
                d["years_mix_better"] >= max(1, len(years_sign) - 1)
                and len(years_sign) >= 4
            )
            return d

        block["overall"] = cell(per)
        for key in ("venue", "track", "class", "field", "distance", "month"):
            groups = defaultdict(list)
            for x in per:
                groups[x["attrs"][key]].append(x)
            block[f"by_{key}"] = {
                str(g): cell(v)
                for g, v in sorted(groups.items(), key=lambda kv: str(kv[0]))
            }
        block["by_year"] = {
            str(y): cell([x for x in per if x["year"] == y]) for y in DIAG_YEARS
        }
        # calibration of the mixture win per year: ECE and the in-sample optimal power exponent
        calib = {}
        for y in DIAG_YEARS:
            ys = [x for x in per if x["year"] == y]
            pw = np.concatenate([x["mix_vec"] for x in ys])
            yw = np.concatenate(
                [[1 if i == x["winner"] else 0 for i in range(x["n"])] for x in ys]
            )
            g = fit_gamma([(x["mix_vec"], x["winner"]) for x in ys])
            calib[str(y)] = {
                "ece_equal_mass_10": ece_equal_mass(pw, yw, bins=10)["ece"],
                "in_sample_optimal_power": g,
                "mean_pred": float(pw.mean()),
                "win_rate": float(yw.mean()),
            }
        block["calibration_by_year"] = calib
        # member contributions
        block["members"] = {
            MEMBERS[j]: {
                "nll": float(np.mean([x["members"][j] for x in per])),
                "mixture_without_it_minus_with": float(
                    np.mean([x["loo"][j] - x["mix"] for x in per])
                ),
                "by_year_nll": {
                    str(y): float(
                        np.mean([x["members"][j] for x in per if x["year"] == y])
                    )
                    for y in DIAG_YEARS
                },
            }
            for j in range(6)
        }
        mem = np.array([x["members"] for x in per])
        block["member_nll_correlation"] = np.corrcoef(mem.T).round(4).tolist()
        block["correction_contribution"] = summarize_diffs(
            [(x["day"], x["mix"] - x["nocorr"]) for x in per]
        )
        block["heads_quality"] = head_quality(
            [(x["mix_vec"], x["top2"], x["top3"], x["winner"]) for x in per]
        )
        out[reg] = block
    # cost of hiding today's weight (preweight − full), by year and class
    rows = []
    for r in records:
        rows.append(
            (
                r["race_day"],
                r["year"],
                r["attrs"]["class"],
                baseline_nll(r, "preweight") - baseline_nll(r, "full"),
            )
        )
    out["weight_hidden_cost"] = {
        "overall": summarize_diffs([(d, v) for d, _, _, v in rows]),
        "by_year": {
            str(y): summarize_diffs([(d, v) for d, yy, _, v in rows if yy == y])
            for y in DIAG_YEARS
        },
        "by_class": {
            c: summarize_diffs([(d, v) for d, _, cc, v in rows if cc == c])
            for c in ("maiden", "conditions", "open")
        },
    }
    out["small_sample_rule"] = (
        "a cell counts as a persistent weakness only if the sign of (mixture − anchor) repeats in "
        "≥4 of 5 years AND its 95% day-cluster CI excludes 0; single-year cells are flagged as chance"
    )
    out["computed_at"] = dt.datetime.now(dt.UTC).isoformat()
    write_new(output, out)
    brief = {
        reg: {
            "overall": out[reg]["overall"]["point"],
            "corr": out[reg]["correction_contribution"]["point"],
            "calib": {
                y: round(v["in_sample_optimal_power"], 3)
                for y, v in out[reg]["calibration_by_year"].items()
            },
        }
        for reg in REGIMES
    }
    print(json.dumps(brief, ensure_ascii=False))


# --- stage 2: hypotheses (walk-forward) ------------------------------------------------------------------


def choose_params(train, reg, kind):
    """Pick a parameter on the training records by mean winner NLL (preweight regime)."""

    def mean_nll(fn):
        return float(np.mean([nll(fn(r), r["winner"]) for r in train]))

    def q_of(r, s=1.0):
        return corrected(r[f"logp_{reg}"], r[f"z_{reg}"], s)

    if kind in ("temperature", "temperature_recent1", "temperature_recent2"):
        if kind != "temperature":
            k = 1 if kind.endswith("1") else 2
            years = sorted({r["year"] for r in train})[-k:]
            train = [r for r in train if r["year"] in years]
        return {
            "gamma": fit_gamma([(q_of(r).mean(axis=1), r["winner"]) for r in train]),
            "fit_years": sorted({r["year"] for r in train}),
        }
    if kind == "weight":
        scores = {
            float(w): mean_nll(lambda r, w=w: mix_win(q_of(r), w_joint=w))
            for w in WEIGHT_GRID
        }
        return {"w_joint": min(scores, key=scores.get), "grid": scores}
    if kind == "scale":
        scores = {
            s: mean_nll(lambda r, s=s: q_of(r, s).mean(axis=1)) for s in SCALE_GRID
        }
        return {"scale": min(scores, key=scores.get), "grid": scores}
    if kind == "drop":
        scores = {
            j: mean_nll(lambda r, j=j: mix_win(q_of(r), drop=j)) for j in range(6)
        }
        scores[-1] = mean_nll(lambda r: q_of(r).mean(axis=1))
        best = min(scores, key=scores.get)
        return {
            "drop": None if best == -1 else int(best),
            "grid": {str(k): v for k, v in scores.items()},
        }
    if kind == "stack":
        # coordinate-wise: scale and weight jointly on the grid (γ=1), then temperature on the result
        best, best_v = None, None
        for s in SCALE_GRID:
            for w in WEIGHT_GRID:
                v = mean_nll(lambda r, s=s, w=w: mix_win(q_of(r, s), w_joint=float(w)))
                if best_v is None or v < best_v:
                    best, best_v = (s, float(w)), v
        s, w = best
        g = fit_gamma([(mix_win(q_of(r, s), w_joint=w), r["winner"]) for r in train])
        return {"scale": s, "w_joint": w, "gamma": g}
    raise ValueError(kind)


def apply_candidate(r, reg, kind, params):
    q = corrected(r[f"logp_{reg}"], r[f"z_{reg}"], params.get("scale", 1.0))
    if kind in ("temperature", "temperature_recent1", "temperature_recent2"):
        return mix_win(q, gamma=params["gamma"])
    if kind == "geometric":
        return mix_win(q, geometric=True)
    if kind == "weight":
        return mix_win(q, w_joint=params["w_joint"])
    if kind == "scale":
        return q.mean(axis=1)
    if kind == "drop":
        return mix_win(q, drop=params["drop"])
    if kind == "stack":
        return mix_win(q, w_joint=params["w_joint"], gamma=params["gamma"])
    raise ValueError(kind)


KINDS = (
    "temperature",
    "temperature_recent1",
    "temperature_recent2",
    "geometric",
    "weight",
    "scale",
    "drop",
    "stack",
)


def hypotheses(ds_path, output, *, stage, prereg=None):
    if stage == "confirmation" and prereg is None:
        raise ValueError("confirmation requires the pre-registration file")
    data = load(ds_path)
    records = data["records"]
    if stage == "selection":
        eval_years = SELECTION_YEARS
    else:
        if prereg is None:
            raise ValueError("confirmation requires the pre-registration file")
        eval_years = CONFIRMATION_YEARS
    all_years = tuple(y for y in YEARS if y <= eval_years[-1])
    kinds = KINDS
    if stage == "confirmation":
        plan = json.loads(Path(prereg).read_text())
        if (
            plan.get("artifact_kind") != "134_preregistration"
            or digest(ds_path) != plan["dataset_sha256"]
        ):
            raise ValueError("pre-registration missing or dataset identity differs")
        kinds = tuple(
            [
                plan["primary_candidate"],
                *plan.get("secondary_candidates_reported_only", []),
            ]
        )
    out = {
        "artifact_kind": "134_mixture_hypotheses",
        "stage": stage,
        "eval_years": list(eval_years),
        "kinds_evaluated": list(kinds),
        "dataset_sha256": digest(ds_path),
        "rule": "every parameter is fit on ALL eligible races of years < Y (from 2020) and applied to year Y; "
        "2020 has no prior year and is scored with the identity parameters",
        "baseline": "served recipe (equal weights, scale 1, arithmetic mean, γ=1)",
        "bootstrap": BOOT,
        "preregistration_sha256": digest(prereg) if prereg else None,
        "candidates": {},
    }
    for reg in REGIMES:
        by_year_recs = {y: [r for r in records if r["year"] == y] for y in all_years}
        base_pairs = {y: [] for y in all_years}
        base_nll = {}
        for y in all_years:
            for r in by_year_recs[y]:
                q = corrected(r[f"logp_{reg}"], r[f"z_{reg}"], 1.0).mean(axis=1)
                base_nll[r["race_id"]] = nll(q, r["winner"])
                base_pairs[y].append((q, r["top2"], r["top3"], r["winner"]))
        days_by_year = {y: [r["race_day"] for r in by_year_recs[y]] for y in all_years}
        reg_out = {}
        for kind in kinds:
            cand_pairs = {y: [] for y in all_years}
            params_by_year, diffs, per_year = {}, [], {}
            for y in all_years:
                train = [r for r in records if r["year"] < y]
                if kind == "geometric":
                    params = {}
                elif not train:
                    params = {"gamma": 1.0, "w_joint": 0.5, "scale": 1.0, "drop": None}
                else:
                    params = choose_params(train, reg, kind)
                params_by_year[str(y)] = {
                    k: v for k, v in params.items() if k != "grid"
                }
                yd = []
                for r in by_year_recs[y]:
                    p = apply_candidate(r, reg, kind, params)
                    cand_pairs[y].append((p, r["top2"], r["top3"], r["winner"]))
                    if y in eval_years:
                        d = nll(p, r["winner"]) - base_nll[r["race_id"]]
                        diffs.append((r["race_day"], d))
                        yd.append((r["race_day"], d))
                if y in eval_years:
                    per_year[str(y)] = summarize_diffs(yd)
            entry = {
                "overall": summarize_diffs(diffs),
                "by_year": per_year,
                "params_by_year": {
                    y: params_by_year[y] for y in params_by_year if int(y) in eval_years
                },
            }
            entry["heads_candidate_minus_baseline"] = heads_delta(
                cand_pairs, base_pairs, eval_years, days_by_year
            )
            # leave-one-year-out sensitivity of the point estimate (diagnostic, not a selector)
            entry["leave_one_year_out_point"] = (
                {
                    str(y): float(
                        np.mean([d for (day, d) in diffs if not day.startswith(str(y))])
                    )
                    for y in eval_years
                }
                if len(eval_years) > 1
                else {}
            )
            reg_out[kind] = entry
            print(
                json.dumps(
                    {
                        "regime": reg,
                        "kind": kind,
                        "point": entry["overall"]["point"],
                        "ci95": entry["overall"]["ci95"],
                        "top3_logloss_delta": {
                            c: v["top3"]["logloss_paired_per_race"]["point"]
                            for c, v in entry["heads_candidate_minus_baseline"].items()
                        },
                        "params": params_by_year[str(eval_years[-1])],
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )
        out["candidates"][reg] = reg_out
    out["computed_at"] = dt.datetime.now(dt.UTC).isoformat()
    write_new(output, out)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--output", default=str(WORK / "dataset.pkl"))
    d = sub.add_parser("diagnose")
    d.add_argument("--dataset", default=str(WORK / "dataset.pkl"))
    d.add_argument("--output", required=True)
    d.add_argument(
        "--years",
        default=None,
        help="comma-separated years (default 2020-2024 selection years)",
    )
    h = sub.add_parser("hypotheses")
    h.add_argument("--dataset", default=str(WORK / "dataset.pkl"))
    h.add_argument("--stage", choices=("selection", "confirmation"), required=True)
    h.add_argument("--preregistration", default=None)
    h.add_argument("--output", required=True)
    a = p.parse_args(argv)
    if a.cmd == "build":
        build(a.output)
    elif a.cmd == "diagnose":
        years = tuple(int(y) for y in a.years.split(",")) if a.years else DIAG_YEARS
        diagnose(a.dataset, a.output, years=years)
    else:
        hypotheses(a.dataset, a.output, stage=a.stage, prereg=a.preregistration)


if __name__ == "__main__":
    main()
