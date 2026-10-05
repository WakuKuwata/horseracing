"""R03_080_gate STEP 2 — diagnostics for the frozen 080 exotic gate (read-only).

Replicates `horseracing_betting.exotic_gate.run_exotic_gate` ONCE (same model, same window, same
selection code, same scoring) but keeps the per-race stake/payout/hit totals of the model EV picks
AND both baselines, so that:
  * the gate's n / point / CI / p are reproduced exactly (consistency check vs the CLI output);
  * the pre-registered diagnostics D1-D4 (absolute ROI, sub-windows, counts, dividend coverage)
    and the text-faithful Holm (m=6) can be computed.
Pre-registration: artifacts/roi_explore/missed_20261004/R03_080_gate/prereg.json
No DB writes: the session is only SELECTed from and rolled back.

Run: cd betting && DATABASE_URL=... uv run python ../scripts/roi_explore/missed_20261004/r03_080_gate_diag.py
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from horseracing_betting.exotic_backtest import _bets_for, _field_and_outcome
from horseracing_betting.exotic_gate import (
    PREREGISTERED_N_MIN,
    _holm_adjusted_p_values,
    _one_sided_bootstrap_p_value,
    evaluate_exotic_gate,
)
from horseracing_betting.exotic_market import load_real_exotic_odds
from horseracing_betting.exotic_roi import score_exotic
from horseracing_betting.exotic_types import ALL_EXOTIC, DEFAULT_SEED
from horseracing_db.models import Race
from horseracing_eval.bootstrap import (
    centered_one_sided_p_from_replicates,
    race_block_ratio_bootstrap_ci_v1,
    race_day_cluster_bootstrap_ci_v1,
)
from horseracing_features.builder import build_feature_matrix
from horseracing_serving.model_loader import load_serving_model
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

ROOT = Path("/Users/kuwatawaku/workspace/horseracing")
OUT = ROOT / "artifacts/roi_explore/missed_20261004/R03_080_gate"
DATE_FROM = dt.date(2026, 7, 23)
DATE_TO = dt.date(2026, 10, 3)
MODEL = "lgbm-094-cap900"
MODEL_OOS_FROM = "2026-08-17"  # lgbm-094-cap900 booster trained through 2026-08-16
PRINTED_THROUGH = "2026-09-06"  # records 1/2 printed point diffs through this day
MIX_ACTIVATION = "2026-09-09"
STRATS = ("ev", "lowest_oest", "uniform")
TAKEOUT = {"place": 0.20, "quinella": 0.225, "wide": 0.225, "exacta": 0.25, "trio": 0.25,
           "trifecta": 0.275}
GATE_SEED = 20260723
GATE_B = 2000
ALPHA = 0.05


def collect(session: Session) -> tuple[pd.DataFrame, dict]:
    model = load_serving_model(session, MODEL)
    feature_rows = build_feature_matrix(session, end_date=DATE_TO)
    present = set(feature_rows["race_id"].unique())
    races = session.execute(
        select(Race.race_id, Race.race_date)
        .where(Race.race_date >= DATE_FROM)
        .where(Race.race_date <= DATE_TO)
        .order_by(Race.race_id)
    ).all()
    counts = defaultdict(int)
    recs = []
    for race_id, race_date in races:
        counts["races_in_window"] += 1
        if race_id not in present:
            counts["skip_not_in_feature_matrix"] += 1
            continue
        field, outcome = _field_and_outcome(session, model, race_id, feature_rows)
        if field is None or not field.p_norm:
            counts["skip_no_field"] += 1
            continue
        real_odds = load_real_exotic_odds(session, race_id)
        if not real_odds:
            counts["skip_no_real_dividend"] += 1
            continue
        counts["races_scored"] += 1
        for strat in STRATS:
            bets = _bets_for(strat, field, threshold=1.0, top_k=5, bet_types=ALL_EXOTIC,
                             seed=DEFAULT_SEED, payout_rates=None, odds_cap=10000.0,
                             stage_discount=None)
            scored, skipped = score_exotic(bets, outcome, stake=100.0, real_odds=real_odds)
            agg: dict[str, list] = {}
            for s in scored:
                a = agg.setdefault(s.bet.bet_type, [0.0, 0.0, 0, 0, 0, 0])
                a[0] += s.stake
                a[1] += s.payout
                a[2] += 1
                a[3] += int(s.hit)
                a[4] += int(s.hit and s.pseudo)
                a[5] += int(s.hit and not s.pseudo)
            for bt, (stake, payout, n, hits, hit_pseudo, hit_real) in agg.items():
                recs.append(dict(race_id=race_id, race_date=race_date.isoformat(), strategy=strat,
                                 bet_type=bt, stake=stake, payout=payout, n_bets=n, hits=hits,
                                 hits_pseudo=hit_pseudo, hits_real=hit_real))
    session.rollback()
    return pd.DataFrame(recs), dict(counts)


def gate_diffs(df: pd.DataFrame, baseline: str, *, lo: str | None = None,
               hi: str | None = None) -> dict[str, dict[str, list[float]]]:
    """Exactly the gate's per-race diff (only races where BOTH sides bet that type)."""
    sub = df
    if lo is not None:
        sub = sub[sub.race_date >= lo]
    if hi is not None:
        sub = sub[sub.race_date <= hi]
    m = sub[sub.strategy == "ev"].set_index(["race_id", "bet_type"])
    b = sub[sub.strategy == baseline].set_index(["race_id", "bet_type"])
    j = m.join(b, how="inner", lsuffix="_m", rsuffix="_b")
    j = j[(j.stake_m > 0) & (j.stake_b > 0)].reset_index().sort_values(["race_id"])
    out: dict[str, dict[str, list[float]]] = {bt: {} for bt in ALL_EXOTIC}
    for row in j.itertuples(index=False):
        d = (row.payout_m / row.stake_m - 1.0) - (row.payout_b / row.stake_b - 1.0)
        out[row.bet_type].setdefault(row.race_date_m, []).append(d)
    return out


def holm_m6(raw_p: dict[str, float]) -> dict[str, float]:
    full = {bt: raw_p.get(bt, 1.0) for bt in ALL_EXOTIC}
    return _holm_adjusted_p_values(full)


def roi_table(df: pd.DataFrame, days: list[str], *, lo=None, hi=None) -> list[dict]:
    rows = []
    sub = df
    if lo is not None:
        sub = sub[sub.race_date >= lo]
    if hi is not None:
        sub = sub[sub.race_date <= hi]
    sdays = [d for d in days if (lo is None or d >= lo) and (hi is None or d <= hi)]
    for bt in ALL_EXOTIC:
        nums, dens, meta = [], [], []
        for strat in STRATS:
            s = sub[(sub.strategy == strat) & (sub.bet_type == bt)]
            byday = s.groupby("race_date")[["payout", "stake"]].sum()
            nums.append([float(byday.payout.get(d, 0.0)) for d in sdays])
            dens.append([float(byday.stake.get(d, 0.0)) for d in sdays])
            meta.append(dict(strategy=strat, bet_type=bt, n_bets=int(s.n_bets.sum()),
                             n_races=int(s.race_id.nunique()), hits=int(s.hits.sum()),
                             hits_real=int(s.hits_real.sum()), hits_pseudo=int(s.hits_pseudo.sum()),
                             stake=float(s.stake.sum()), payout=float(s.payout.sum())))
        if len(sdays) < 2:
            continue
        bs = race_block_ratio_bootstrap_ci_v1(np.array(nums), np.array(dens), sdays,
                                              b=20000, seed=20260905)
        for i, mt in enumerate(meta):
            pt = float(bs.point[i])
            mt.update(roi=pt, ci_low=float(bs.ci_low[i]), ci_high=float(bs.ci_high[i]),
                      p_roi_le_1=centered_one_sided_p_from_replicates(bs.replicates[i], pt)
                      if np.isfinite(pt) else None,
                      takeout_null=1.0 - TAKEOUT[bt], n_days=len(sdays))
            rows.append(mt)
    return rows


def sub_diff(df: pd.DataFrame, baseline: str, lo=None, hi=None) -> dict:
    res = {}
    for bt, dd in gate_diffs(df, baseline, lo=lo, hi=hi).items():
        n = sum(len(v) for v in dd.values())
        if n == 0:
            continue
        ci = race_day_cluster_bootstrap_ci_v1(dd, b=GATE_B, seed=GATE_SEED, alpha=ALPHA)
        res[bt] = dict(n=n, n_days=len(dd), point=ci.point, ci_low=ci.ci_low, ci_high=ci.ci_high)
    return res


def main() -> int:
    url = os.environ.get("DATABASE_URL",
                         "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
    eng = create_engine(url)
    with Session(eng) as session:
        df, counts = collect(session)
    df.to_parquet(OUT / "per_race_strategy.parquet", index=False)
    days = sorted(df.race_date.unique().tolist())

    report: dict = {"model": MODEL, "window": [DATE_FROM.isoformat(), DATE_TO.isoformat()],
                    "counts": counts, "race_days": days}
    # 2026-09-06 included races
    report["counts"]["races_scored_on_2026-09-06"] = int(
        df[df.race_date == "2026-09-06"].race_id.nunique())

    # --- gate reproduction + text-faithful Holm (m=6)
    gate = {}
    for baseline in ("lowest_oest", "uniform"):
        dd = gate_diffs(df, baseline)
        verdicts = evaluate_exotic_gate(dd, PREREGISTERED_N_MIN, b=GATE_B, seed=GATE_SEED,
                                        alpha=ALPHA)
        raw_p = {}
        for bt, v in verdicts.items():
            if v.verdict != "NO_DECISION":
                raw_p[bt] = _one_sided_bootstrap_p_value(dd[bt], b=GATE_B, seed=GATE_SEED)
        m6 = holm_m6(raw_p)
        gate[baseline] = {
            bt: dict(verdict=v.verdict, n=v.n_bets, n_days=v.n_days, point=v.point_diff,
                     ci_low=v.ci_low, ci_high=v.ci_high, p_raw=raw_p.get(bt),
                     p_holm_code=v.p_adjusted, p_holm_m6=(m6[bt] if bt in raw_p else None),
                     note=v.note)
            for bt, v in verdicts.items()
        }
    report["gate_reproduction"] = gate

    # --- §9 OOS guard (model out-of-sample sub-window) and other sub-windows (D2)
    report["subwindows_diff"] = {}
    windows = {
        "model_in_sample_<=2026-08-16": (None, "2026-08-16"),
        "model_oos_>=2026-08-17": (MODEL_OOS_FROM, None),
        "printed_by_records_1_2_<=2026-09-06": (None, PRINTED_THROUGH),
        "never_printed_>=2026-09-07": ("2026-09-07", None),
        "before_mix129_<=2026-09-08": (None, "2026-09-08"),
        "after_mix129_>=2026-09-09": (MIX_ACTIVATION, None),
    }
    for name, (lo, hi) in windows.items():
        report["subwindows_diff"][name] = {
            baseline: sub_diff(df, baseline, lo=lo, hi=hi) for baseline in ("lowest_oest", "uniform")
        }

    # --- final verdict per prereg decision rule
    final = {}
    for bt in ALL_EXOTIC:
        g_l, g_u = gate["lowest_oest"][bt], gate["uniform"][bt]
        if g_l["verdict"] == "NO_DECISION" or g_u["verdict"] == "NO_DECISION":
            final[bt] = "NO_DECISION"
            continue
        oos = report["subwindows_diff"]["model_oos_>=2026-08-17"]
        ok = (g_l["verdict"] == "ADOPT_CANDIDATE" and g_u["verdict"] == "ADOPT_CANDIDATE"
              and g_l["p_holm_m6"] < ALPHA and g_u["p_holm_m6"] < ALPHA
              and oos["lowest_oest"].get(bt, {}).get("point", -1) > 0
              and oos["uniform"].get(bt, {}).get("point", -1) > 0)
        final[bt] = "ADOPT_CANDIDATE" if ok else "REJECT"
    report["final_verdict"] = final

    # --- D1 absolute ROI (full window + sub-windows)
    report["roi_full"] = roi_table(df, days)
    report["roi_model_oos"] = roi_table(df, days, lo=MODEL_OOS_FROM)
    report["roi_never_printed"] = roi_table(df, days, lo="2026-09-07")

    (OUT / "diag_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1,
                                                      default=str))
    print(json.dumps({"counts": report["counts"], "final_verdict": final}, ensure_ascii=False))
    for baseline in ("lowest_oest", "uniform"):
        for bt in ALL_EXOTIC:
            g = gate[baseline][bt]
            print(baseline, bt, g["verdict"], g["n"], g["n_days"], g["point"],
                  g["ci_low"], g["ci_high"], g["p_raw"], g["p_holm_code"], g["p_holm_m6"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
