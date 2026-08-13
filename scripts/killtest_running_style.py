"""Kill-test: what does the netkeiba 脚質 relabelling cost us?

`race_horses.running_style` kept its name across the supply cutover but changed meaning. The
feature layer binarises it — `{逃げ,先行}` -> is_front, `{差し,追込,ﾏｸﾘ}` -> is_closer — and feeds
those into per-horse as-of rates plus the whole 031 pace-scenario block. Measured on started rows:

    era        front   closer   null      categories
    JRA-VAN    0.331   0.132    0.004     逃げ 先行 中団 後方 差し 追込 ﾏｸﾘ
    netkeiba   0.285   0.389    0.097     後方 and ﾏｸﾘ GONE; 追込 2.8% -> 21.6%

The racing did not change; the label did. Applying ONE deterministic rule to `corner_orders`
(available for 86% of netkeiba-era rows) reproduces 0.310/0.123 on the JRA-VAN era and 0.311/0.119
on the netkeiba era — i.e. the underlying behaviour is stable and it is the supplier's
classification that moved.

So this is the `prev_weight` pattern from 091, not a new feature: keep the real label where it
means what the model learned, and substitute a validated estimate where it does not. Training data
(overwhelmingly JRA-VAN era) is untouched, which is what makes a fixed-model replay valid here.

The rule and its thresholds are fitted on the JRA-VAN era ONLY and frozen before touching the
netkeiba era. No retraining, no external requests.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pickle
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from horseracing_features.builder import build_feature_matrix
from horseracing_training.cond_logit import race_softmax
from horseracing_training.target_encoding import apply_encoded_columns
from sqlalchemy import text
from sqlalchemy.orm import Session

from horseracing_db.session import create_db_engine

OUT = Path("out/style-killtest")
MODEL = Path("artifacts/model_versions/lgbm-091-wmask")
CLIP = 1e-6
END = dt.date(2026, 8, 9)
#: The supply cutover. Rows on or after this date carry netkeiba's classification.
CUTOVER = dt.date(2025, 7, 1)
#: Frozen on the JRA-VAN era before looking at any netkeiba-era outcome (grid search in the
#: exploratory pass): front if the horse is in the leading quarter at the first corner; closer if
#: it is not up front and improved by more than 14% of the field between first and last corner.
FRONT_MAX = 0.250
CLOSER_MIN_GAIN = 0.14
#: One representative label per binarised class — the feature layer only ever tests membership of
#: `_FRONT_STYLES` / `_CLOSER_STYLES`, so these reproduce the binarisation exactly.
LABEL = {"front": "先行", "closer": "差し", "mid": "中団"}

STYLE_SQL = text("""
SELECT rh.race_id, rh.horse_id, rr.corner_orders, n.n_started
FROM race_horses rh
JOIN races r  ON r.race_id = rh.race_id
JOIN race_results rr ON rr.race_id = rh.race_id AND rr.horse_id = rh.horse_id
JOIN (SELECT race_id, count(*) n_started FROM race_horses
      WHERE entry_status = 'started' GROUP BY 1) n ON n.race_id = rh.race_id
WHERE rh.entry_status = 'started' AND r.race_date >= :cutover
  AND rr.corner_orders IS NOT NULL
""")

COHORT_SQL = text("""
SELECT r.race_id FROM races r
WHERE r.race_date >= :cutover AND r.race_date <= :end
  AND EXISTS (SELECT 1 FROM race_results rr WHERE rr.race_id = r.race_id)
""")


def derived_style(corner_orders, n_started: int) -> str | None:
    """The frozen rule. None when the corners cannot place the horse (86% coverage, not 100%)."""
    if not corner_orders or not n_started or n_started < 2:
        return None
    co = [int(x) for x in corner_orders]
    if not co:
        return None
    denom = n_started - 1
    first = (co[0] - 1) / denom
    last = (co[-1] - 1) / denom
    if first <= FRONT_MAX:
        return LABEL["front"]
    return LABEL["closer"] if (first - last) > CLOSER_MIN_GAIN else LABEL["mid"]


def _load_model():
    return (lgb.Booster(model_file=str(MODEL / "model.txt")),
            pickle.load(open(MODEL / "preprocessor.pkl", "rb")),
            pickle.load(open(MODEL / "calibrator.pkl", "rb")))


def _predict(rows: pd.DataFrame, sizes: list[int], booster, prep, calib) -> np.ndarray:
    cols, cats = list(prep["feature_cols"]), list(prep.get("categorical_cols", []))
    encoders = prep.get("encoders", {})
    rows = rows.copy()
    for c in cats:
        if c in rows.columns:
            rows[c] = rows[c].astype("category")
    for c in [c for c in cols if c not in cats and c not in encoders]:
        rows[c] = pd.to_numeric(rows[c], errors="coerce")
    X = rows[cols].copy()
    if encoders:
        X = apply_encoded_columns(X, {c: e.transform(rows[c]) for c, e in encoders.items()}, cols)
    p = np.clip(np.asarray(calib.transform(race_softmax(
        np.asarray(booster.predict(X), dtype=float), sizes)), dtype=float), CLIP, 1 - CLIP)
    idx = np.repeat(np.arange(len(sizes)), sizes)
    return p / np.bincount(idx, weights=p, minlength=len(sizes))[idx]


def _winner_nll(feats, p, winners, days) -> dict:
    by_day: dict[str, list[float]] = {}
    vals = []
    for rid, g in feats.assign(_p=p).groupby("race_id", sort=True):
        w = winners.get(rid)
        if w is None:
            continue
        hit = g.loc[g["horse_id"] == w, "_p"]
        if hit.empty:
            continue
        nll = -float(np.log(max(float(hit.iloc[0]), CLIP)))
        vals.append(nll)
        d = days.get(rid)
        if d:
            by_day.setdefault(str(d)[:10], []).append(nll)
    return {"mean": float(np.mean(vals)) if vals else None, "n": len(vals), "by_day": by_day}


def _cluster_ci(diff_by_day, b: int = 2000, seed: int = 20260813):
    days = sorted(diff_by_day)
    if not days:
        return None, None, None
    per = np.array([np.mean(diff_by_day[d]) for d in days])
    wts = np.array([len(diff_by_day[d]) for d in days], dtype=float)
    point = float(np.sum(per * wts) / wts.sum())
    rng = np.random.default_rng(seed)
    boots = [float(np.sum(per[k] * wts[k]) / wts[k].sum())
             for k in (rng.integers(0, len(days), len(days)) for _ in range(b))]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return point, float(lo), float(hi)


def main() -> int:
    os.environ.setdefault(
        "DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
    OUT.mkdir(parents=True, exist_ok=True)

    with Session(create_db_engine()) as s:
        rows = list(s.execute(STYLE_SQL, {"cutover": CUTOVER}))
        winners = {r.race_id: r.horse_id for r in s.execute(text(
            "SELECT race_id, horse_id FROM race_results "
            "WHERE finish_order = 1 AND result_status = 'finished'"))}
        days = {r.race_id: str(r.race_date)
                for r in s.execute(text("SELECT race_id, race_date FROM races"))}
        cohort = frozenset(
            r.race_id for r in s.execute(COHORT_SQL, {"cutover": CUTOVER, "end": END})
            if r.race_id in winners)

        repairs = [(r.race_id, r.horse_id, derived_style(r.corner_orders, r.n_started))
                   for r in rows]
        repairs = [(rid, hid, st) for rid, hid, st in repairs if st is not None]
        print(f"netkeiba-era started rows with corners : {len(rows)}")
        print(f"  repairable by the frozen rule        : {len(repairs)} "
              f"({len(repairs) / max(len(rows), 1):.1%})")
        print(f"cohort (settled, post-cutover)         : {len(cohort)} races")

        booster, prep, calib = _load_model()
        results = {}
        for arm in ("A_current", "B_derived"):
            if arm == "B_derived":
                # Uncommitted, then rolled back — same discipline as the grade kill-test: the real
                # derivation must run, and the database must be left exactly as found.
                for rid, hid, st in repairs:
                    s.execute(text("UPDATE race_horses SET running_style = :st "
                                   "WHERE race_id = :r AND horse_id = :h"),
                              {"st": st, "r": rid, "h": hid})
                s.flush()
                print(f"  arm B: relabelled {len(repairs)} rows (uncommitted)")
            feats = build_feature_matrix(s, end_date=END, target_race_ids=cohort)
            feats = feats.sort_values(["race_id", "horse_id"]).reset_index(drop=True)
            sizes = feats.groupby("race_id", sort=True).size().tolist()
            p = _predict(feats, sizes, booster, prep, calib)
            results[arm] = {"feats": feats, "p": p,
                            "nll": _winner_nll(feats, p, winners, days)}
            print(f"  {arm}: winner NLL {results[arm]['nll']['mean']:.6f} "
                  f"over {results[arm]['nll']['n']} races")
        s.rollback()

    a, bb = results["A_current"], results["B_derived"]
    assert a["feats"]["race_id"].equals(bb["feats"]["race_id"]), "row alignment broke"
    d = np.abs(bb["p"] - a["p"])
    moved = d > 1e-12
    diff_by_day = {
        day: [y - x for x, y in zip(av, bb["nll"]["by_day"][day], strict=True)]
        for day, av in a["nll"]["by_day"].items()
        if len(av) == len(bb["nll"]["by_day"].get(day, []))
    }
    pt, lo, hi = _cluster_ci(diff_by_day)
    report = {
        "cutover": str(CUTOVER),
        "rule": {"front_max_first_pct": FRONT_MAX, "closer_min_gain": CLOSER_MIN_GAIN,
                 "fitted_on": "JRA-VAN era only, frozen before touching netkeiba-era outcomes"},
        "rows_relabelled": len(repairs),
        "cohort_races": len(cohort),
        "rows": int(len(d)), "rows_changed": int(moved.sum()),
        "abs_delta_p": {
            "mean_over_changed": float(d[moved].mean()) if moved.any() else 0.0,
            "p50": float(np.percentile(d[moved], 50)) if moved.any() else 0.0,
            "p95": float(np.percentile(d[moved], 95)) if moved.any() else 0.0,
            "max": float(d.max()),
        },
        "winner_nll": {"A_current": a["nll"]["mean"], "B_derived": bb["nll"]["mean"],
                       "n_races": a["nll"]["n"], "diff_B_minus_A": pt, "ci": [lo, hi],
                       "n_days": len(diff_by_day)},
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
