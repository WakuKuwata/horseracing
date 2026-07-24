"""Feature 081 Phase 0: folklore residual-offset probe orchestration (training side).

Runs the ACTIVE model's recipe-faithful strict-past OOF once (expensive, cached to disk), joins
each candidate factor's as-of value, and runs the pure ``eval.residual_probe`` per candidate.

This module is allowed to import both ``eval`` (probe + OOF harness) and the training RecipeFactory
— it is the CLI orchestration layer (020 boundary: ``eval`` never imports ``training``). It is
READ-ONLY: it writes only the probe cache/report artifacts, never the DB or any model feature.

Contract: gate-config ``phase0-screening-v1`` (hash c696cec79c95). ``can_adopt=false`` — this
produces a SCREENING map (Δwinner-NLL per candidate + race-day cluster CI + Holm-adjusted p as a
diagnostic), never an ADOPT.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from horseracing_eval.bootstrap import race_day_cluster_bootstrap_ci_v1
from horseracing_eval.dataset import load_eval_races, population_masks
from horseracing_eval.foldfit import predict_over_folds
from horseracing_eval.residual_probe import RaceProbe, prequential_delta_nll
from sqlalchemy import text
from sqlalchemy.orm import Session

#: candidate id -> (raw column list, family, optional one-hot cell column).
#: A cell column expands ``factor × one-hot(cell)`` (the cell is race-constant, so the interaction
#: is a per-cell slope — this is how "draw bias flips sign by venue" is represented).
CANDIDATES: dict[str, dict] = {
    "tataki_2": {"cols": ["tataki_2"], "family": "PRE_ENTRY"},
    "prior_gap_log": {"cols": ["prior_gap_log"], "family": "PRE_ENTRY"},
    "seasonal_sex": {"cols": ["seasonal_sex_sin", "seasonal_sex_cos"], "family": "PRE_ENTRY"},
    "current_gap_shape": {"cols": ["gap_log", "gap_hinge_short", "gap_hinge_long"],
                          "family": "PRE_ENTRY"},
    "prev_finish_reversion": {"cols": ["prev_fin_2_3", "prev_fin_6_9"], "family": "PRE_ENTRY"},
    "draw_venue": {"cols": ["draw_pct"], "cell": "draw_cell", "family": "PRE_ENTRY"},
    "body_mass_going": {"cols": ["light_body"], "cell": "body_cell", "family": "POST_WEIGHT"},
    "weight_gain": {"cols": ["weight_gain"], "family": "POST_WEIGHT"},
}

_CANDIDATE_SQL = Path(__file__).with_name("folklore_candidates.sql").read_text()


def _load_candidates(session: Session) -> pd.DataFrame:
    df = pd.read_sql(text(_CANDIDATE_SQL), session.bind)
    return df.set_index(["race_id", "horse_id"])


def build_oof_cache(
    session: Session, *, spec: str, make_factory, from_date, to_date, first_valid_year: int,
    num_threads: int | None, cache_path: Path,
) -> pd.DataFrame:
    """Run the recipe-faithful OOF once and join candidate columns; cache to parquet.

    Rows: one per (race_id, horse_id) started horse in the OOF-predicted valid races, with the OOF
    win prob ``p``, ``is_winner``, ``day``, ``eligible`` and every candidate raw/cell column.
    """
    eval_races = load_eval_races(session, start_date=from_date, end_date=to_date)
    factory = make_factory(spec)
    preds, valid = predict_over_folds(
        factory, eval_races, first_valid_year=first_valid_year, num_threads=num_threads,
    )
    cand = _load_candidates(session)
    cand_cols = sorted({c for m in CANDIDATES.values() for c in m["cols"]}
                       | {m["cell"] for m in CANDIDATES.values() if "cell" in m})

    rows = []
    for er in valid:
        ctx = er.context
        pop = population_masks(er)
        winner = pop.winner_horse_id
        pr = preds.get(ctx.race_id, {})
        for h in ctx.started_horses:
            pred = pr.get(h.horse_id)
            if pred is None:
                continue
            key = (ctx.race_id, h.horse_id)
            crow = cand.loc[key] if key in cand.index else None
            row = {
                "race_id": ctx.race_id, "horse_id": h.horse_id,
                "day": ctx.race_date.isoformat(), "p": float(pred.win),
                "is_winner": int(h.horse_id == winner), "eligible": int(pop.eligible),
            }
            for c in cand_cols:
                row[c] = (float(crow[c]) if c not in ("draw_cell", "body_cell")
                          else crow[c]) if crow is not None and pd.notna(crow[c]) else None
            rows.append(row)
    df = pd.DataFrame(rows)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path)
    return df


@dataclass(frozen=True)
class CandidateReport:
    candidate_id: str
    family: str
    k: int
    n_races: int
    coverage: float
    mean_score_U: list[float]
    point_delta_nll: float
    ci_low: float | None
    ci_high: float | None
    n_days: int
    passes_screen: bool
    screen_reason: str
    gammas_by_fold: list[list[float]]      # provenance: per-fold prequential γ (codex P0)
    gamma_sign_stable: bool                 # all folds agree on the sign of every γ column


def _race_probes_for_candidate(df: pd.DataFrame, meta: dict) -> tuple[list, int]:
    """Build (list-of-folds-by-year of RaceProbe, k). Only eligible races (exactly 1 winner)."""
    cols = meta["cols"]
    cell_col = meta.get("cell")
    cells = sorted(df[cell_col].dropna().unique()) if cell_col else None
    k = len(cells) if cells else len(cols)

    probes_by_year: dict[int, list[RaceProbe]] = {}
    for _race_id, g in df.groupby("race_id", sort=False):
        if not int(g["eligible"].iloc[0]):
            continue
        widx = np.where(g["is_winner"].to_numpy() == 1)[0]
        if widx.size != 1:
            continue
        p = g["p"].to_numpy(dtype=float)
        if cells:
            base = g[cols[0]].to_numpy(dtype=float)          # single factor
            cell = g[cell_col].to_numpy()
            h = np.zeros((len(g), k), dtype=float)
            for j, cval in enumerate(cells):
                mask = cell == cval
                h[mask, j] = base[mask]
            # rows whose factor is NaN stay 0 (no tilt); mark them NaN so _clean treats as 0
        else:
            h = g[cols].to_numpy(dtype=float)
        year = int(g["day"].iloc[0][:4])
        probes_by_year.setdefault(year, []).append(
            RaceProbe(day=g["day"].iloc[0], p=p, h=h, winner_idx=int(widx[0]))
        )
    folds = [probes_by_year[y] for y in sorted(probes_by_year)]
    return folds, k


def run_probe(df: pd.DataFrame, gate: dict, *, seed: int, b: int) -> list[CandidateReport]:
    prom = gate["promotion_to_phase1"]
    reports: list[CandidateReport] = []
    for cid, meta in CANDIDATES.items():
        folds, k = _race_probes_for_candidate(df, meta)
        res = prequential_delta_nll(folds, cid, k)
        ci = race_day_cluster_bootstrap_ci_v1(res.delta_nll_by_day, b=b, seed=seed)
        point = res.point_delta_nll
        passes = (
            ci.ci_high is not None
            and point <= prom["point_le"]
            and ci.ci_high <= prom["ci_upper_le"]
        )
        if ci.ci_high is None:
            reason = "no_decision (CI undefined: <2 race-days)"
        elif not passes:
            reason = (f"below loose screen (point {point:+.5f} vs <= {prom['point_le']}, "
                      f"ci_high {ci.ci_high:+.5f} vs <= {prom['ci_upper_le']})")
        else:
            reason = "PASS loose screen -> Phase 1 candidate"
        gsigns = [
            {int(np.sign(round(g[j], 9))) for g in res.gammas_by_fold}
            for j in range(k)
        ] if res.gammas_by_fold else []
        sign_stable = all(len(s - {0}) <= 1 for s in gsigns)
        reports.append(CandidateReport(
            candidate_id=cid, family=meta["family"], k=k, n_races=res.n_races,
            coverage=round(res.coverage, 4), mean_score_U=[round(u, 6) for u in res.mean_score_U],
            point_delta_nll=round(point, 6), ci_low=ci.ci_low, ci_high=ci.ci_high,
            n_days=ci.n_days, passes_screen=bool(passes), screen_reason=reason,
            gammas_by_fold=[[round(v, 6) for v in g] for g in res.gammas_by_fold],
            gamma_sign_stable=bool(sign_stable),
        ))
    return reports


def holm_adjusted(reports: list[CandidateReport]) -> dict[str, float]:
    """Diagnostic Holm-adjusted 'p-like' values from the CI (two-sided normal approx of the
    point/SE where SE ~ (ci_high-ci_low)/(2*1.96)). Screening-only: reported, not gated."""
    from math import erfc, sqrt
    raw = {}
    for r in reports:
        if r.ci_low is None or r.ci_high is None:
            raw[r.candidate_id] = float("nan")
            continue
        se = (r.ci_high - r.ci_low) / (2 * 1.959964)
        z = abs(r.point_delta_nll) / se if se > 0 else float("inf")
        raw[r.candidate_id] = erfc(z / sqrt(2))
    ordered = sorted((v, k) for k, v in raw.items() if v == v)
    m = len(ordered)
    adj: dict[str, float] = {k: float("nan") for k in raw}
    running = 0.0
    for i, (pval, cid) in enumerate(ordered):
        running = max(running, min(1.0, (m - i) * pval))
        adj[cid] = round(running, 5)
    return adj


def run_folklore_probe(
    session: Session, *, spec: str, make_factory, gate: dict, from_date, to_date,
    first_valid_year: int, seed: int, b: int, num_threads: int | None, cache_path: Path,
    reuse_cache: bool,
) -> dict:
    if reuse_cache and cache_path.exists():
        df = pd.read_parquet(cache_path)
    else:
        df = build_oof_cache(
            session, spec=spec, make_factory=make_factory, from_date=from_date, to_date=to_date,
            first_valid_year=first_valid_year, num_threads=num_threads, cache_path=cache_path,
        )
    reports = run_probe(df, gate, seed=seed, b=b)
    holm = holm_adjusted(reports)
    return {
        "contract": gate.get("evaluation_contract_version"),
        "can_adopt": gate.get("can_adopt", False),
        "spec": spec,
        "training_load_window": [str(from_date), str(to_date)],
        "eval_first_valid_year": first_valid_year,
        "eval_window_note": f"OOF valid races are {first_valid_year}..{to_date} "
                            f"(training expands from {from_date}); window[] is the LOAD range",
        "window": [str(from_date), str(to_date)],
        "seed": seed, "bootstrap_b": b,
        "generated_at_note": "screening_only; not an adoption gate",
        "reports": [asdict(r) for r in reports],
        "holm_adjusted_diagnostic": holm,
    }


def write_report(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(report, fh, indent=2, default=str)
