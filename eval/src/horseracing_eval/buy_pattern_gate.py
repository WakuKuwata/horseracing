"""Feature 109: buy-pattern adoption gate — PURE SCORER (numpy + dict only).

What lives here: population fixing, derived columns, mask → per-day matrices, the frozen
evaluation contract (ratio block bootstrap, null-centred one-sided p-values, Holm, demotion,
futility, max-T bound), the five-state machine, sensitivities, the synthetic self-test
(within-race categorical draws under a KL-min ROI tilt with a day random effect), and the
verdict/conclusion text rules.

What does NOT live here: DB / OOF-bundle loading, pandas, parquet, git — those belong to
``scripts/buy_pattern_gate.py`` (the eval environment has no pandas and this module must stay
importable there). This module imports neither ``horseracing_betting`` nor
``horseracing_training`` (same rule as ``policy_gate.py``).

Constitution III: the gate is frozen and self-tested BEFORE any real pattern is scored;
III/V: every number in a verdict is recomputable from the saved evidence with the same
function (``race_block_ratio_bootstrap_ci_v1``) and the same frozen seed.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import pathlib
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.stats import beta as _beta
from scipy.stats import norm as _norm

from .bootstrap import BlockRatioBootstrap, race_block_ratio_bootstrap_ci_v1
from .decision import gate_config_hash
from .hashing import stable_hash

STATES = ("SCREENED_OUT", "NO_DECISION", "ADOPT_CLOSE", "RULED_OUT", "NOT_ADOPTED")
FORBIDDEN_PHRASES = ("全パターン REJECT", "効果なし", "利益パターンは存在しない")
LIMITATIONS = (
    "closing_price_leak",
    "payout_approximation",
    "dead_heat_handling",
    "no_correction_history",
    "win_only",
)
RESUME_CONDITIONS = (
    "発走前の時点固定価格が前向きに蓄積されたら(選定価格の価格リークを塞げる)",
    "控除率・リベート・税制の変更(必要 t/q の閾値が動く)",
    "市場が持たない新情報源(調教・当日情報)が使えるようになったら",
)
ANALYSIS_VERSIONS = ("race_day", "iso_week", "calendar_month")
DEAD_HEAT_VERSIONS = ("dead_heat_equal_split", "dead_heat_half_odds")


class GateConfigMismatch(RuntimeError):
    pass


class FrozenArtifactMismatch(RuntimeError):
    pass


class AggregationMismatch(RuntimeError):
    pass


# ---------------------------------------------------------------------------------------------
# Frozen config / artifacts (fail-closed)
# ---------------------------------------------------------------------------------------------


def load_gate_config(
    path: str | pathlib.Path, expected_hash: str | None, *, require_frozen: bool = True
) -> dict:
    cfg = json.loads(pathlib.Path(path).read_text())
    h = gate_config_hash(cfg)
    if expected_hash is not None and h != expected_hash:
        raise GateConfigMismatch(f"gate-config hash {h[:12]} != expected {expected_hash[:12]}")
    if require_frozen:
        missing = [k for k in ("patterns_hash", "code_sha") if not cfg.get(k)]
        win = cfg.get("windows", {}).get("confirmatory") or [None, None]
        if not win[1]:
            missing.append("windows.confirmatory[1]")
        if missing:
            raise GateConfigMismatch(f"gate-config is not frozen: empty {missing}")
    return cfg


def verify_frozen(kind: str, payload: dict | list, expected_hash: str) -> str:
    h = stable_hash(payload)
    if h != expected_hash:
        raise FrozenArtifactMismatch(f"{kind} hash {h[:12]} != expected {expected_hash[:12]}")
    return h


# ---------------------------------------------------------------------------------------------
# Canonical key + independent aggregation (FR-022)
# ---------------------------------------------------------------------------------------------


def selection_hash(race_ids: Sequence, horse_numbers: Sequence) -> str:
    keys = sorted((str(r), int(h)) for r, h in zip(race_ids, horse_numbers, strict=True))
    return stable_hash(keys)


def independent_aggregate(race_ids, horse_numbers, payouts, stakes) -> dict:
    """Pure-Python dict accumulation, deliberately not numpy/pandas (second implementation)."""
    seen: dict[tuple[str, int], None] = {}
    n_bets = 0
    n_hits = 0
    s_stake = 0.0
    s_pay = 0.0
    for r, h, p, s in zip(race_ids, horse_numbers, payouts, stakes, strict=True):
        k = (str(r), int(h))
        if k in seen:
            raise AggregationMismatch(f"duplicate bet key {k}")
        seen[k] = None
        n_bets += 1
        s_stake += float(s)
        if float(p) > 0:
            n_hits += 1
            s_pay += float(p)
    return {"n_bets": n_bets, "n_hits": n_hits, "sum_stake": s_stake, "sum_payout": s_pay}


def assert_aggregation_matches(primary: dict, second: dict) -> None:
    for k in ("n_bets", "n_hits"):
        if int(primary[k]) != int(second[k]):
            raise AggregationMismatch(f"{k}: {primary[k]} != {second[k]}")
    for k in ("sum_stake", "sum_payout"):
        if not math.isclose(float(primary[k]), float(second[k]), rel_tol=0, abs_tol=1e-6):
            raise AggregationMismatch(f"{k}: {primary[k]} != {second[k]}")


# ---------------------------------------------------------------------------------------------
# Population (research D1) and derived columns (D4 / D5)
# ---------------------------------------------------------------------------------------------


def _race_index(arrays: dict) -> tuple[np.ndarray, np.ndarray]:
    rid = np.asarray(arrays["race_id"], dtype=object)
    uniq, inv = np.unique(rid.astype(str), return_inverse=True)
    return uniq, inv


def _sub(arrays: dict, keep: np.ndarray) -> dict:
    return {
        k: (np.asarray(v)[keep] if isinstance(v, np.ndarray) or hasattr(v, "__len__") else v)
        for k, v in arrays.items()
    }


def fix_population(arrays: dict) -> tuple[dict, dict]:
    """Fix the evaluation population (research D1 (a)-(d)) and return the flow report.

    Pre-result exclusions (per race): not in bundle / jump / any started horse without odds /
    any without p. Post-result: no winner (dropped), dead heat (KEPT with ``dead_heat=True`` and
    excluded from the primary judgement by mask so the sensitivities can restore it).
    """
    uniq, inv = _race_index(arrays)
    nr = len(uniq)
    n = len(inv)
    in_bundle = np.asarray(arrays["in_bundle"], dtype=bool)
    track = np.asarray(arrays["track_type"], dtype=object)
    odds = np.asarray(arrays["odds"], dtype=float)
    p = np.asarray(arrays["p"], dtype=float)
    nwin = np.asarray(arrays["n_winners"], dtype=int)

    def race_any(flag: np.ndarray) -> np.ndarray:
        out = np.zeros(nr, dtype=bool)
        np.logical_or.at(out, inv, flag)
        return out

    r_not_bundle = ~race_any(in_bundle)
    r_jump = race_any(np.array([t not in ("芝", "ダ") for t in track], dtype=bool))
    r_missing_odds = race_any(~np.isfinite(odds))
    r_missing_p = race_any(~np.isfinite(p))
    r_nwin = np.zeros(nr, dtype=int)
    r_nwin[inv] = nwin
    r_no_winner = r_nwin == 0
    r_dead_heat = r_nwin >= 2

    excl_pre = r_not_bundle | r_jump | r_missing_odds | r_missing_p
    keep_race = ~excl_pre & ~r_no_winner
    keep_row = keep_race[inv]
    out = _sub(arrays, keep_row)
    dead = r_dead_heat[inv][keep_row]
    out["dead_heat"] = dead
    out["primary"] = ~dead
    kept_primary_ids = sorted(str(x) for x in uniq[keep_race & ~r_dead_heat])
    years = np.asarray(arrays["year"], dtype=int)
    r_year = np.zeros(nr, dtype=int)
    r_year[inv] = years
    by_year: dict[str, int] = {}
    for y in sorted(set(r_year[keep_race & ~r_dead_heat].tolist())):
        by_year[str(y)] = int(np.sum(r_year[keep_race & ~r_dead_heat] == y))
    report = {
        "total_races": int(nr),
        "total_rows": int(n),
        "excluded_pre_result": {
            "not_in_bundle": int(r_not_bundle.sum()),
            "jump": int((r_jump & ~r_not_bundle).sum()),
            "missing_odds": int((r_missing_odds & ~r_not_bundle & ~r_jump).sum()),
            "missing_p": int((r_missing_p & ~r_not_bundle & ~r_jump & ~r_missing_odds).sum()),
        },
        "excluded_post_result": {
            "no_winner": int((r_no_winner & ~excl_pre).sum()),
            "dead_heat": int((r_dead_heat & ~excl_pre).sum()),
        },
        "kept_races": len(kept_primary_ids),
        "kept_rows_primary": int((~dead).sum()),
        "retained_dead_heat_rows": int(dead.sum()),
        "population_hash": stable_hash(kept_primary_ids),
        "races_by_year": by_year,
    }
    if len(kept_primary_ids) == 0:
        return out, report
    # INV-P1..P3
    sub_inv = inv[keep_row]
    w = np.asarray(out["won"], dtype=bool)
    per_race_w = np.zeros(nr, dtype=int)
    np.add.at(per_race_w, sub_inv, w.astype(int))
    prim_races = np.unique(sub_inv[~dead])
    if not np.all(per_race_w[prim_races] == 1):
        raise AssertionError("INV-P1: primary population must have exactly one winner per race")
    if not (
        np.all(np.isfinite(np.asarray(out["odds"], float)))
        and np.all(np.isfinite(np.asarray(out["p"], float)))
    ):
        raise AssertionError("INV-P2: kept rows must have odds and p")
    return out, report


def derive(arrays: dict, *, warmup_through: str = "2008-12-31") -> dict:
    """Add q / fav_q / is_fav / entropy / p_rank / p_over_q / ev and the strict-past bands."""
    out = dict(arrays)
    n = len(out["odds"])
    uniq, inv = _race_index(out)
    nr = len(uniq)
    odds = np.asarray(out["odds"], dtype=float)
    p = np.asarray(out["p"], dtype=float)
    hn = np.asarray(out["horse_number"], dtype=int)
    inv_odds = 1.0 / odds
    tot = np.zeros(nr)
    np.add.at(tot, inv, inv_odds)
    q = inv_odds / tot[inv]
    out["q"] = q
    fav_q = np.zeros(nr)
    np.maximum.at(fav_q, inv, q)
    out["fav_q"] = fav_q[inv]
    # favourite: lowest odds, ties by horse_number ascending
    order = np.lexsort((hn, odds, inv))
    first = np.ones(n, dtype=bool)
    first[1:] = inv[order][1:] != inv[order][:-1]
    is_fav = np.zeros(n, dtype=bool)
    is_fav[order[first]] = True
    out["is_fav"] = is_fav
    # model rank: p descending, ties by horse_number
    order_p = np.lexsort((hn, -p, inv))
    rank = np.empty(n, dtype=int)
    start = np.ones(n, dtype=bool)
    start[1:] = inv[order_p][1:] != inv[order_p][:-1]
    pos = np.arange(n) - np.maximum.accumulate(np.where(start, np.arange(n), 0))
    rank[order_p] = pos + 1
    out["p_rank"] = rank
    out["p_over_q"] = p / q
    out["ev"] = p * odds
    # normalised market entropy per race
    ent_num = np.zeros(nr)
    np.add.at(ent_num, inv, -q * np.log(q))
    cnt = np.bincount(inv, minlength=nr).astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        ent = np.where(cnt >= 2, ent_num / np.log(np.maximum(cnt, 2)), np.nan)
    out["entropy"] = ent[inv]
    # strict-past bands (expanding, by race date; warm-up → -1)
    dates = np.asarray(out["race_date"], dtype=object).astype(str)
    p_band = np.full(n, -1, dtype=int)
    e_band = np.full(n, -1, dtype=int)
    day_order = np.argsort(dates, kind="stable")
    ds = dates[day_order]
    p_sorted = p[day_order]
    race_sorted = inv[day_order]
    # race-level entropy list in date order (one per race, first row)
    bounds = np.flatnonzero(np.r_[True, ds[1:] != ds[:-1]])
    bounds = np.r_[bounds, n]
    p_hist = np.empty(n)
    e_hist = np.empty(nr)
    p_cnt = 0
    e_cnt = 0
    for bi in range(len(bounds) - 1):
        lo, hi = bounds[bi], bounds[bi + 1]
        day = ds[lo]
        if day > warmup_through and p_cnt >= 100 and e_cnt >= 30:
            q75, q90 = np.percentile(p_hist[:p_cnt], [75.0, 90.0])
            t1, t2 = np.percentile(e_hist[:e_cnt], [100.0 / 3.0, 200.0 / 3.0])
            seg = p_sorted[lo:hi]
            pb = np.where(seg >= q90, 0, np.where(seg >= q75, 1, 2))
            p_band[day_order[lo:hi]] = pb
            seg_e = ent[race_sorted[lo:hi]]
            eb = np.where(seg_e >= t2, 2, np.where(seg_e >= t1, 1, 0))
            eb = np.where(np.isfinite(seg_e), eb, -1)
            e_band[day_order[lo:hi]] = eb
        # append the day to history
        p_hist[p_cnt : p_cnt + (hi - lo)] = p_sorted[lo:hi]
        p_cnt += hi - lo
        races_today = np.unique(race_sorted[lo:hi])
        vals = ent[races_today]
        vals = vals[np.isfinite(vals)]
        e_hist[e_cnt : e_cnt + len(vals)] = vals
        e_cnt += len(vals)
    out["p_band_past"] = p_band
    out["entropy_band_past"] = e_band
    return out


def day_universe(arrays: dict) -> list[str]:
    return sorted(set(np.asarray(arrays["race_date"], dtype=object).astype(str).tolist()))


# ---------------------------------------------------------------------------------------------
# Masks → per-day matrices
# ---------------------------------------------------------------------------------------------


@dataclass
class DayMatrix:
    ids: list[str]
    days: list[str]
    payout: np.ndarray  #: (P, D)
    stake: np.ndarray  #: (P, D)
    n_bets: np.ndarray
    n_hits: np.ndarray
    max_hit: np.ndarray
    n_days: np.ndarray  #: days with stake > 0
    selection_hash: list[str]
    by_year: list[dict]  #: per pattern {year: {n_bets, payout, stake}}
    second_agg_match: list[bool]


def payout_vector(arrays: dict, version: str = "race_day") -> tuple[np.ndarray, np.ndarray]:
    """(payout per row, eligible-row mask) for a primary or dead-heat sensitivity version."""
    odds = np.asarray(arrays["odds"], dtype=float)
    won = np.asarray(arrays["won"], dtype=bool)
    dead = np.asarray(arrays.get("dead_heat", np.zeros(len(odds), dtype=bool)), dtype=bool)
    nwin = np.asarray(arrays.get("n_winners", np.ones(len(odds), dtype=int)), dtype=float)
    if version in ANALYSIS_VERSIONS:
        return np.where(won, odds, 0.0), ~dead
    if version == "dead_heat_equal_split":
        pay = np.where(won, odds / np.maximum(nwin, 1.0), 0.0)
        return pay, np.ones(len(odds), dtype=bool)
    if version == "dead_heat_half_odds":
        div = np.where(dead, np.maximum(2.0, nwin), 1.0)
        pay = np.where(won, odds / div, 0.0)
        return pay, np.ones(len(odds), dtype=bool)
    raise ValueError(f"unknown version {version!r}")


def apply_masks(
    arrays: dict,
    masks: Iterable[np.ndarray],
    ids: Sequence[str],
    days: Sequence[str],
    *,
    payout: np.ndarray | None = None,
    eligible: np.ndarray | None = None,
    with_hashes: bool = True,
) -> DayMatrix:
    dates = np.asarray(arrays["race_date"], dtype=object).astype(str)
    day_list = [str(d) for d in days]
    day_pos = {d: i for i, d in enumerate(day_list)}
    didx = np.array([day_pos[d] for d in dates], dtype=np.int64)
    D = len(day_list)
    if payout is None or eligible is None:
        payout, eligible = payout_vector(arrays, "race_day")
    years = np.asarray(arrays["year"], dtype=int)
    rid = np.asarray(arrays["race_id"], dtype=object)
    hn = np.asarray(arrays["horse_number"], dtype=int)
    ids = list(ids)
    P = len(ids)
    pay_m = np.zeros((P, D))
    stk_m = np.zeros((P, D))
    n_bets = np.zeros(P, dtype=np.int64)
    n_hits = np.zeros(P, dtype=np.int64)
    max_hit = np.zeros(P)
    n_days = np.zeros(P, dtype=np.int64)
    hashes: list[str] = []
    by_year: list[dict] = []
    matches: list[bool] = []
    for i, m in enumerate(masks):
        mm = np.asarray(m, dtype=bool) & eligible
        idx = np.flatnonzero(mm)
        pv = payout[idx]
        pay_m[i] = np.bincount(didx[idx], weights=pv, minlength=D)
        stk_m[i] = np.bincount(didx[idx], minlength=D).astype(float)
        n_bets[i] = len(idx)
        n_hits[i] = int((pv > 0).sum())
        max_hit[i] = float(pv.max()) if len(idx) else 0.0
        n_days[i] = int((stk_m[i] > 0).sum())
        yrs: dict = {}
        if len(idx):
            for y in np.unique(years[idx]):
                sel = years[idx] == y
                yrs[str(int(y))] = {
                    "n_bets": int(sel.sum()),
                    "payout": float(pv[sel].sum()),
                    "stake": float(sel.sum()),
                }
        by_year.append(yrs)
        if with_hashes:
            hashes.append(selection_hash(rid[idx], hn[idx]))
            second = independent_aggregate(rid[idx], hn[idx], pv, np.ones(len(idx)))
            primary = {
                "n_bets": int(n_bets[i]),
                "n_hits": int(n_hits[i]),
                "sum_stake": float(stk_m[i].sum()),
                "sum_payout": float(pay_m[i].sum()),
            }
            assert_aggregation_matches(primary, second)
            matches.append(True)
        else:
            hashes.append("")
            matches.append(False)
    return DayMatrix(
        ids, day_list, pay_m, stk_m, n_bets, n_hits, max_hit, n_days, hashes, by_year, matches
    )


# ---------------------------------------------------------------------------------------------
# Scoring (research D2)
# ---------------------------------------------------------------------------------------------


@dataclass
class PatternScore:
    pattern_id: str
    n_bets: int
    n_hits: int
    n_days: int
    roi: float
    ci_low: float | None
    ci_high: float | None
    p_profit_one_sided: float
    p_futility_one_sided: float
    sd_replicates: float
    mde_80: float
    max_single_hit_share: float
    leave_one_hit_out_roi: float
    demoted_reason: str | None
    n_zero_den: int
    by_year: dict
    selection_hash: str
    second_aggregation_match: bool
    block: str

    def to_dict(self) -> dict:
        return dict(vars(self))


def one_sided_pvalues(reps: np.ndarray, point: float, *, c: float) -> tuple[float, float]:
    """Null-centred basic-bootstrap p-values (codex plan #12/#13), ``(1+#tail)/(B+1)``."""
    r = reps[np.isfinite(reps)]
    B = len(r)
    if B == 0 or not np.isfinite(point):
        return 1.0, 1.0
    p_profit = (1.0 + float(np.sum(r - point >= point - 1.0))) / (B + 1.0)
    p_fut = (1.0 + float(np.sum(point - r >= c - point))) / (B + 1.0)
    return p_profit, p_fut


def mde_80(sd: float, *, alpha: float, m: int) -> float:
    if not np.isfinite(sd) or sd <= 0:
        return float("nan")
    return float((_norm.ppf(1.0 - alpha / max(m, 1)) + _norm.ppf(0.80)) * sd)


def score_patterns(
    dm: DayMatrix,
    cfg: dict,
    *,
    block: str = "race_day",
    m: int = 1,
    bs: BlockRatioBootstrap | None = None,
) -> tuple[list[PatternScore], BlockRatioBootstrap]:
    boot = cfg["bootstrap"]
    if bs is None:
        bs = race_block_ratio_bootstrap_ci_v1(
            dm.payout,
            dm.stake,
            dm.days,
            block=block,
            b=int(boot["b"]),
            seed=int(boot["seed"]),
            alpha=float(boot["alpha_two_sided"]),
        )
    dem = cfg["demotion"]
    c = 1.0 + float(cfg["ruled_out_delta"])
    alpha = float(cfg["test"]["holm_alpha_one_sided"])
    out: list[PatternScore] = []
    for i, pid in enumerate(dm.ids):
        reps = bs.replicates[i]
        point = float(bs.point[i])
        pp, pf = one_sided_pvalues(reps, point, c=c)
        sd = float(np.nanstd(reps)) if np.isfinite(reps).any() else float("nan")
        total_pay = float(dm.payout[i].sum())
        total_stake = float(dm.stake[i].sum())
        share = (dm.max_hit[i] / total_pay) if total_pay > 0 else 0.0
        loho = ((total_pay - dm.max_hit[i]) / total_stake) if total_stake > 0 else float("nan")
        reason = None
        if dm.n_bets[i] == 0:
            reason = "not_fired"
        elif dm.n_hits[i] < int(dem["min_hits"]):
            reason = f"n_hits {int(dm.n_hits[i])} < {int(dem['min_hits'])}"
        elif share > float(dem["max_single_hit_share"]):
            reason = f"one hit carries {share:.1%} of the payout"
        elif dm.n_days[i] < int(dem["min_days"]):
            reason = f"n_days {int(dm.n_days[i])} < {int(dem['min_days'])}"
        elif int(bs.n_zero_den[i]) > 0:
            reason = "zero_denominator_replicate"
        elif bs.no_decision:
            reason = "cannot_run"
        out.append(
            PatternScore(
                pid,
                int(dm.n_bets[i]),
                int(dm.n_hits[i]),
                int(dm.n_days[i]),
                point,
                None if not np.isfinite(bs.ci_low[i]) else float(bs.ci_low[i]),
                None if not np.isfinite(bs.ci_high[i]) else float(bs.ci_high[i]),
                pp,
                pf,
                sd,
                mde_80(sd, alpha=alpha, m=m),
                float(share),
                float(loho),
                reason,
                int(bs.n_zero_den[i]),
                dm.by_year[i],
                dm.selection_hash[i],
                dm.second_agg_match[i],
                block,
            )
        )
    return out, bs


def holm_one_sided(pvalues: dict[str, float], alpha: float = 0.025) -> dict[str, bool]:
    """Holm step-down; demoted entries are passed as p=1 and COUNT in m (no early break)."""
    items = sorted(pvalues.items(), key=lambda kv: (kv[1], kv[0]))
    m = len(items)
    out = {k: False for k, _ in items}
    failed = False
    for i, (k, p) in enumerate(items):
        if failed or p > alpha / (m - i):
            failed = True
            continue
        out[k] = True
    return out


def maxt_upper(reps: np.ndarray, points: np.ndarray, *, alpha: float = 0.025) -> np.ndarray:
    """Studentised single-step max-T simultaneous upper bounds (reference only)."""
    reps = np.atleast_2d(reps)
    pts = np.atleast_1d(points).astype(float)
    s = np.nanstd(reps, axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        e = (pts[:, None] - reps) / s[:, None]
    emax = np.nanmax(e, axis=0)
    c = float(np.nanquantile(emax, 1.0 - alpha))
    return pts + c * s


def decide(
    scores_by_version: dict[str, list[PatternScore]],
    *,
    alpha: float = 0.025,
    primary: str = "race_day",
) -> dict[str, dict]:
    """Five-state machine over the survivors (research D10, priority order fixed).

    ``scores_by_version`` maps an analysis version → scores for the SAME ids. Demoted patterns
    enter Holm as p=1 (m preserved). A pattern is ADOPT_CLOSE only if the profit test is rejected
    in every version; RULED_OUT if the profit test is not rejected and the futility test is
    rejected; if the versions disagree on the category → NO_DECISION(sensitivity_split).
    """
    versions = list(scores_by_version)
    if primary not in versions:
        raise ValueError("primary version missing")
    ids = [s.pattern_id for s in scores_by_version[primary]]
    cat: dict[str, dict[str, str]] = {pid: {} for pid in ids}
    reasons: dict[str, str] = {}
    for v in versions:
        scores = {s.pattern_id: s for s in scores_by_version[v]}
        if set(scores) != set(ids):
            raise ValueError("versions must score the same ids")
        p_prof = {
            pid: (1.0 if scores[pid].demoted_reason else scores[pid].p_profit_one_sided)
            for pid in ids
        }
        p_fut = {
            pid: (1.0 if scores[pid].demoted_reason else scores[pid].p_futility_one_sided)
            for pid in ids
        }
        h_prof = holm_one_sided(p_prof, alpha)
        h_fut = holm_one_sided(p_fut, alpha)
        for pid in ids:
            if scores[pid].demoted_reason:
                cat[pid][v] = "NO_DECISION"
                reasons.setdefault(pid, f"demoted[{v}]: {scores[pid].demoted_reason}")
            elif h_prof[pid]:
                cat[pid][v] = "ADOPT_CLOSE"
            elif h_fut[pid]:
                cat[pid][v] = "RULED_OUT"
            else:
                cat[pid][v] = "NOT_ADOPTED"
    out: dict[str, dict] = {}
    for pid in ids:
        cs = cat[pid]
        if any(c == "NO_DECISION" for c in cs.values()):
            state, reason = "NO_DECISION", reasons.get(pid, "demoted")
        elif len(set(cs.values())) > 1:
            state, reason = (
                "NO_DECISION",
                "sensitivity_split: " + ", ".join(f"{k}={v}" for k, v in cs.items()),
            )
        else:
            state = next(iter(cs.values()))
            reason = {
                "ADOPT_CLOSE": "profit Holm rejected in all versions",
                "RULED_OUT": "profit not rejected; futility (ROI >= 1+delta) rejected",
                "NOT_ADOPTED": "neither profit nor futility rejected",
            }[state]
        out[pid] = {"state": state, "reason": reason, "by_version": dict(cs)}
    return out


def controls_masks(arrays: dict) -> dict[str, np.ndarray]:
    odds = np.asarray(arrays["odds"], dtype=float)
    return {
        "no_bet": np.zeros(len(odds), dtype=bool),
        "favorite": np.asarray(arrays["is_fav"], dtype=bool),
        "cap11_all": np.isfinite(odds) & (odds < 11.0),
        "cap21_all": np.isfinite(odds) & (odds < 21.0),
    }


# ---------------------------------------------------------------------------------------------
# Synthetic self-test (research D6)
# ---------------------------------------------------------------------------------------------


@dataclass
class RaceStructure:
    inv: np.ndarray  #: race index per row (rows sorted so races are contiguous)
    nr: int
    q: np.ndarray
    d: np.ndarray  #: payout multiplier (odds)
    day_idx: np.ndarray  #: per row
    n_days: int
    last_row: np.ndarray  #: per race, index of its last row (rows contiguous)


def race_structure(arrays: dict, days: Sequence[str]) -> tuple[RaceStructure, np.ndarray]:
    """Sort rows so races are contiguous; returns (structure, order) — masks must be reordered."""
    uniq, inv = _race_index(arrays)
    order = np.argsort(inv, kind="stable")
    inv_s = inv[order]
    q = np.asarray(arrays["q"], dtype=float)[order]
    d = np.asarray(arrays["odds"], dtype=float)[order]
    dates = np.asarray(arrays["race_date"], dtype=object).astype(str)[order]
    pos = {str(x): i for i, x in enumerate(days)}
    day_idx = np.array([pos[x] for x in dates], dtype=np.int64)
    last = np.zeros(len(uniq), dtype=np.int64)
    np.maximum.at(last, inv_s, np.arange(len(inv_s)))
    return RaceStructure(inv_s, len(uniq), q, d, day_idx, len(days), last), order


def _normalise(w: np.ndarray, inv: np.ndarray, nr: int) -> np.ndarray:
    tot = np.zeros(nr)
    np.add.at(tot, inv, w)
    return w / tot[inv]


def race_probs(rs: RaceStructure, expo: np.ndarray) -> np.ndarray:
    # subtract per-race max for stability
    mx = np.full(rs.nr, -np.inf)
    np.maximum.at(mx, rs.inv, expo)
    w = rs.q * np.exp(expo - mx[rs.inv])
    return _normalise(w, rs.inv, rs.nr)


def expected_roi(pi: np.ndarray, mask: np.ndarray, d: np.ndarray) -> float:
    s = float(mask.sum())
    return float((mask * d * pi).sum() / s) if s > 0 else float("nan")


_GH_X, _GH_W = np.polynomial.hermite_e.hermegauss(5)
_GH_W = _GH_W / _GH_W.sum()


@dataclass
class TiltSpec:
    mask: np.ndarray  #: which rows the pattern bets on
    s: np.ndarray  #: tilt weights (0 outside the pattern)
    target: float | None  #: equality target ROI, or None → constraint ROI <= 1.0
    lam: float = 0.0
    feasible: bool = True


# Day random effect scale.
# The first self-test run (2026-09-06) applied u_day to the ODDS-scaled tilt s = b·d, as written
# in codex plan #9. With tau = 0.022 and odds up to 300 that puts ±6.6 in the exponent of a
# 300x horse on some days; the 5-node Gauss-Hermite marginal then bore no relation to the
# simulated one (a "boundary null" of ROI 1.00 realised 1.33-1.40 and the gate rightly adopted
# it 27% of the time). The day effect therefore multiplies the SELECTION indicator b (a uniform
# log-probability shift of the pattern's horses on that day), which keeps the marginal solve
# well-conditioned; tau is estimated on the same scale and fit_tilts verifies the marginal by
# simulation. Research D6 records the correction.
def day_effect_weights(mask: np.ndarray) -> np.ndarray:
    return np.asarray(mask, dtype=float)


def _marginal_roi(
    rs: RaceStructure, specs: list[TiltSpec], j: int, tau: float, target_idx: int
) -> float:
    base = np.zeros(len(rs.q))
    for sp in specs:
        base += sp.lam * sp.s
    if tau <= 0:
        pi = race_probs(rs, base)
        return expected_roi(pi, specs[j].mask, rs.d)
    acc = 0.0
    de = day_effect_weights(specs[target_idx].mask)
    for x, w in zip(_GH_X, _GH_W, strict=True):
        pi = race_probs(rs, base + tau * x * de)
        acc += w * expected_roi(pi, specs[j].mask, rs.d)
    return acc


def _bisect_lambda(
    rs, specs, j, tau, target_idx, goal, lo=-20.0, hi=20.0, it=40
) -> tuple[float, bool]:
    def f(lam):
        specs[j].lam = lam
        return _marginal_roi(rs, specs, j, tau, target_idx) - goal

    flo, fhi = f(lo), f(hi)
    if not (flo <= 0 <= fhi):
        specs[j].lam = 0.0
        return 0.0, False
    for _ in range(it):
        mid = 0.5 * (lo + hi)
        if f(mid) < 0:
            lo = mid
        else:
            hi = mid
    specs[j].lam = 0.5 * (lo + hi)
    return specs[j].lam, True


def fit_tilts(
    rs: RaceStructure,
    specs: list[TiltSpec],
    *,
    tau: float,
    target_idx: int,
    sweeps: int = 6,
    tol: float = 1e-3,
    sim_check_draws: int = 30,
) -> dict:
    """Coordinate-wise KL-min tilts: equality targets by bisection, ``<= 1`` constraints only
    when violated. The target's lambda is re-solved LAST with the day effect integrated out."""
    for sp in specs:
        sp.lam = 0.0
        sp.feasible = True
    info: dict = {"sweeps": 0, "max_violation": 0.0}
    for sweep in range(sweeps):
        moved = 0.0
        for j, sp in enumerate(specs):
            if j == target_idx:
                continue
            if sp.target is not None:
                old = sp.lam
                _, ok = _bisect_lambda(rs, specs, j, tau, target_idx, sp.target)
                sp.feasible = ok
                moved = max(moved, abs(sp.lam - old))
            else:
                sp.lam = 0.0 if sweep == 0 else sp.lam
                roi = _marginal_roi(rs, specs, j, tau, target_idx)
                if roi > 1.0 + tol:
                    old = sp.lam
                    _, ok = _bisect_lambda(rs, specs, j, tau, target_idx, 1.0, lo=-20.0, hi=0.0)
                    sp.feasible = ok
                    moved = max(moved, abs(sp.lam - old))
        old = specs[target_idx].lam
        _, ok = _bisect_lambda(rs, specs, target_idx, tau, target_idx, specs[target_idx].target)
        specs[target_idx].feasible = ok
        moved = max(moved, abs(specs[target_idx].lam - old))
        info["sweeps"] = sweep + 1
        if moved < 1e-6:
            break
    viol = 0.0
    for j, sp in enumerate(specs):
        roi = _marginal_roi(rs, specs, j, tau, target_idx)
        if sp.target is None:
            viol = max(viol, roi - 1.0)
        else:
            viol = max(viol, abs(roi - sp.target))
    info["max_violation"] = float(viol)
    info["lambdas"] = [float(sp.lam) for sp in specs]
    # simulation check of the marginal: the quadrature must agree with actual draws
    sim_rng = np.random.default_rng(20260906)
    sims = []
    for _ in range(sim_check_draws):
        won = synth_outcomes(rs, specs, target_idx, tau, sim_rng)
        sims.append(expected_roi(won.astype(float), specs[target_idx].mask, rs.d))
    tgt = float(specs[target_idx].target)
    info["sim_roi_mean"] = float(np.mean(sims))
    info["sim_roi_se"] = float(np.std(sims) / max(np.sqrt(len(sims)), 1.0))
    sim_ok = abs(info["sim_roi_mean"] - tgt) <= max(0.02, 3.0 * info["sim_roi_se"])
    info["sim_ok"] = bool(sim_ok)
    info["feasible"] = bool(all(sp.feasible for sp in specs) and viol <= 5e-3 and sim_ok)
    return info


def tilt_weights(
    rs: RaceStructure, mask: np.ndarray, shape: str, rng: np.random.Generator
) -> np.ndarray:
    m = np.asarray(mask, dtype=bool)
    if shape == "klmin_roi_tilt":
        return np.where(m, rs.d, 0.0)
    if shape == "odds_neutral":
        return m.astype(float)
    if shape == "long_odds_top20pct":
        if m.sum() == 0:
            return np.zeros(len(m))
        thr = np.percentile(rs.d[m], 80.0)
        return np.where(m & (rs.d >= thr), rs.d, 0.0)
    if shape == "days_10pct":
        days = np.arange(rs.n_days)
        chosen = np.zeros(rs.n_days, dtype=bool)
        chosen[rng.choice(days, size=max(1, rs.n_days // 10), replace=False)] = True
        return np.where(m & chosen[rs.day_idx], rs.d, 0.0)
    raise ValueError(f"unknown edge shape {shape!r}")


def draw_winners(rs: RaceStructure, pi: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Exactly one winner per race (categorical), rows contiguous by race."""
    cs = np.cumsum(pi)
    start = np.zeros(rs.nr)
    first = np.ones(len(pi), dtype=bool)
    first[1:] = rs.inv[1:] != rs.inv[:-1]
    start[rs.inv[first]] = cs[first] - pi[first]
    c = cs - start[rs.inv]
    key = rs.inv + np.minimum(c, 1.0 - 1e-12)
    u = rng.random(rs.nr)
    target = np.arange(rs.nr) + u
    found = np.searchsorted(key, target, side="left")
    found = np.minimum(found, rs.last_row)
    won = np.zeros(len(pi), dtype=bool)
    won[found] = True
    return won


def synth_outcomes(
    rs: RaceStructure, specs: list[TiltSpec], target_idx: int, tau: float, rng: np.random.Generator
) -> np.ndarray:
    expo = np.zeros(len(rs.q))
    for sp in specs:
        expo += sp.lam * sp.s
    if tau > 0:
        u = rng.normal(0.0, tau, size=rs.n_days)
        expo = expo + u[rs.day_idx] * day_effect_weights(specs[target_idx].mask)
    pi = race_probs(rs, expo)
    return draw_winners(rs, pi, rng)


def categorical_day_variance(rs: RaceStructure, mask: np.ndarray, pi: np.ndarray) -> np.ndarray:
    s = np.where(mask, rs.d, 0.0)
    e1 = np.zeros(rs.nr)
    e2 = np.zeros(rs.nr)
    np.add.at(e1, rs.inv, s * pi)
    np.add.at(e2, rs.inv, s * s * pi)
    v_race = e2 - e1**2
    race_day = np.zeros(rs.nr, dtype=np.int64)
    race_day[rs.inv] = rs.day_idx
    v_day = np.zeros(rs.n_days)
    np.add.at(v_day, race_day, v_race)
    return v_day


def estimate_day_effect(
    rs: RaceStructure,
    mask_ref: np.ndarray,
    won_real: np.ndarray,
    rng: np.random.Generator,
    *,
    sims: int = 5,
    it: int = 10,
) -> dict:
    """tau by simulation matching of the daily payout-total variance (codex plan #9)."""
    s = np.where(mask_ref, rs.d, 0.0)
    obs_daily = np.bincount(rs.day_idx, weights=np.where(won_real, s, 0.0), minlength=rs.n_days)
    stake_daily = np.bincount(rs.day_idx, weights=mask_ref.astype(float), minlength=rs.n_days)
    active = stake_daily > 0
    obs_var = float(np.var(obs_daily[active], ddof=1)) if active.sum() > 1 else 0.0
    pi0 = race_probs(rs, np.zeros(len(rs.q)))
    v_cat = categorical_day_variance(rs, mask_ref, pi0)
    base_var = float(np.mean(v_cat[active])) if active.any() else 0.0
    spec = TiltSpec(mask_ref, s, None, 0.0)

    def sim_var(tau: float) -> float:
        vals = []
        for _ in range(sims):
            won = synth_outcomes(rs, [spec], 0, tau, rng)
            daily = np.bincount(rs.day_idx, weights=np.where(won, s, 0.0), minlength=rs.n_days)
            vals.append(float(np.var(daily[active], ddof=1)))
        return float(np.mean(vals))

    if obs_var <= base_var:
        return {
            "tau": 0.0,
            "observed_var": obs_var,
            "categorical_var": base_var,
            "matched_var": sim_var(0.0),
            "scale": "selection_indicator",
        }
    lo, hi = 0.0, 1.0
    if sim_var(hi) < obs_var:
        hi = 3.0
    for _ in range(it):
        mid = 0.5 * (lo + hi)
        if sim_var(mid) < obs_var:
            lo = mid
        else:
            hi = mid
    tau = 0.5 * (lo + hi)
    return {
        "tau": float(tau),
        "observed_var": obs_var,
        "categorical_var": base_var,
        "matched_var": sim_var(tau),
        "scale": "selection_indicator",
    }


def binom_one_sided_bounds(k: int, n: int, conf: float = 0.95) -> tuple[float, float]:
    lo = 0.0 if k == 0 else float(_beta.ppf(1.0 - conf, k, n - k + 1))
    hi = 1.0 if k == n else float(_beta.ppf(conf, k + 1, n - k))
    return lo, hi


def representative_indices(n_bets: np.ndarray, k: int = 5) -> list[int]:
    """Indices of patterns nearest to the bet-count quantiles (min/25/50/75/max), unique."""
    order = np.argsort(n_bets, kind="stable")
    valid = order[n_bets[order] > 0]
    if len(valid) == 0:
        return []
    qs = np.linspace(0, 1, k)
    picks: list[int] = []
    for qq in qs:
        j = int(round(qq * (len(valid) - 1)))
        cand = int(valid[j])
        step = 1
        while cand in picks and step < len(valid):
            j2 = min(len(valid) - 1, j + step)
            cand = int(valid[j2])
            step += 1
        if cand not in picks:
            picks.append(cand)
    return picks


def _score_versions(
    arrays_sorted: dict,
    masks: list[np.ndarray],
    ids: list[str],
    days: list[str],
    cfg: dict,
    won: np.ndarray,
    versions: Sequence[str],
) -> dict[str, list[PatternScore]]:
    arr = dict(arrays_sorted)
    arr["won"] = won
    payout, eligible = payout_vector(arr, "race_day")
    dm = apply_masks(arr, masks, ids, days, payout=payout, eligible=eligible, with_hashes=False)
    out = {}
    for v in versions:
        scores, _ = score_patterns(dm, cfg, block=v, m=len(ids))
        out[v] = scores
    return out


def selftest(
    arrays: dict,
    masks_all: list[np.ndarray],
    ids_all: list[str],
    days: list[str],
    cfg: dict,
    *,
    tau: float,
    rng: np.random.Generator,
    reps_size: int | None = None,
    reps_power: int | None = None,
    progress: Callable[[str], None] | None = None,
    checkpoint_dir: pathlib.Path | None = None,
    extrapolate_only: bool = False,
) -> dict:
    """Size + power of the frozen gate on real race structure with synthetic winners."""
    st = cfg["selftest"]
    reps_size = int(st["reps_size_per_config"]) if reps_size is None else reps_size
    reps_power = int(st["reps_power"]) if reps_power is None else reps_power
    versions = tuple(st.get("analysis_versions", ANALYSIS_VERSIONS))
    alpha = float(cfg["test"]["holm_alpha_one_sided"])
    size_alpha = float(st["size_alpha_one_sided"])
    rho_grid = [float(x) for x in st["rho_grid"]]
    shapes = list(st["edge_shapes"]) + [st.get("edge_shape_sensitivity", "odds_neutral")]
    cfg_size = dict(cfg)
    cfg_size["bootstrap"] = dict(cfg["bootstrap"], b=int(st["b_inner_size"]))
    cfg_power = dict(cfg)
    cfg_power["bootstrap"] = dict(cfg["bootstrap"], b=int(st["b_inner_power"]))

    rs, order = race_structure(arrays, days)
    arr_s = _sub(arrays, order)
    n_bets = np.array([int(m.sum()) for m in masks_all])
    rep_idx = representative_indices(n_bets, 5)
    rep_ids = [ids_all[i] for i in rep_idx]
    rep_masks = [np.asarray(masks_all[i], dtype=bool)[order] for i in rep_idx]
    report: dict[str, Any] = {
        "representative": [{"pattern_id": ids_all[i], "n_bets": int(n_bets[i])} for i in rep_idx],
        "tau": float(tau),
        "versions": list(versions),
        "size": {},
        "power": {},
        "negative_control": {},
        "reps_size_per_config": reps_size,
        "reps_power": reps_power,
    }
    t0 = time.time()

    def log(msg: str) -> None:
        if progress:
            progress(msg)

    def run_config(
        name: str,
        target_i: int,
        edge_others: dict[int, float],
        rho: float,
        shape: str,
        reps: int,
        cfg_run: dict,
    ) -> dict:
        specs = []
        for k, m in enumerate(rep_masks):
            s = tilt_weights(rs, m, shape if k == target_i else "klmin_roi_tilt", rng)
            if k == target_i:
                specs.append(TiltSpec(m, s, rho))
            elif k in edge_others:
                specs.append(TiltSpec(m, s, edge_others[k]))
            else:
                specs.append(TiltSpec(m, s, None))
        fit = fit_tilts(rs, specs, tau=tau, target_idx=target_i)
        if not fit["feasible"]:
            return {"infeasible": True, "fit": fit, "n": 0, "adopt": 0}
        adopt = 0
        demoted = 0
        n = 0
        t_start = time.time()
        for _ in range(reps):
            won = synth_outcomes(rs, specs, target_i, tau, rng)
            sv = _score_versions(arr_s, rep_masks, rep_ids, days, cfg_run, won, versions)
            dec = decide(sv, alpha=alpha, primary="race_day")
            st_ = dec[rep_ids[target_i]]["state"]
            adopt += st_ == "ADOPT_CLOSE"
            demoted += st_ == "NO_DECISION"
            n += 1
            if extrapolate_only and n >= 20:
                break
        elapsed = time.time() - t_start
        lo, hi = binom_one_sided_bounds(adopt, n)
        return {
            "infeasible": False,
            "fit": {k: v for k, v in fit.items() if k != "lambdas"},
            "lambdas": fit["lambdas"],
            "n": n,
            "adopt": adopt,
            "rate": adopt / n if n else float("nan"),
            "lower_95": lo,
            "upper_95": hi,
            "demoted_rate": demoted / n if n else float("nan"),
            "seconds": elapsed,
            "seconds_per_rep": elapsed / n if n else float("nan"),
        }

    # ---- size (boundary null ρ=1.00): global boundary + partial null
    size_pass = True
    for ti in range(len(rep_masks)):
        for conf_name, others in (
            ("global_boundary", {}),
            ("partial_null", {(ti + 1) % len(rep_masks): 1.10} if len(rep_masks) > 1 else {}),
        ):
            key = f"{conf_name}:{rep_ids[ti]}"
            log(f"size {key}")
            res = run_config(key, ti, others, 1.00, "klmin_roi_tilt", reps_size, cfg_size)
            report["size"][key] = res
            if not res.get("infeasible") and res["n"] > 0 and res["lower_95"] > size_alpha:
                size_pass = False
            if checkpoint_dir is not None:
                (checkpoint_dir / f"selftest-partial-size-{ti}-{conf_name}.json").write_text(
                    json.dumps(res, ensure_ascii=False, indent=2, default=float)
                )
            if extrapolate_only:
                break
        if extrapolate_only:
            break
    # ---- negative control ρ=0.796
    log("negative control")
    report["negative_control"] = run_config(
        "neg",
        0,
        {},
        float(st["rho_negative_control"]),
        "klmin_roi_tilt",
        min(reps_size, 500),
        cfg_size,
    )
    # ---- power curves
    for ti in range(len(rep_masks)):
        for shape in shapes:
            curve = []
            for rho in rho_grid:
                key = f"{rep_ids[ti]}:{shape}:{rho}"
                log(f"power {key}")
                res = run_config(key, ti, {}, rho, shape, reps_power, cfg_power)
                res["rho"] = rho
                curve.append(res)
                if extrapolate_only:
                    break
            rates = [(c["rho"], c.get("rate")) for c in curve if not c.get("infeasible")]
            mde = float("nan")
            for (r0, p0), (r1, p1) in zip(rates, rates[1:], strict=False):
                if p0 is not None and p1 is not None and p0 < 0.8 <= p1:
                    mde = r0 + (0.8 - p0) * (r1 - r0) / max(p1 - p0, 1e-9) - 1.0
                    break
            report["power"][f"{rep_ids[ti]}:{shape}"] = {"curve": curve, "mde_80_from_curve": mde}
            if checkpoint_dir is not None:
                (checkpoint_dir / f"selftest-partial-power-{ti}-{shape}.json").write_text(
                    json.dumps(
                        report["power"][f"{rep_ids[ti]}:{shape}"],
                        ensure_ascii=False,
                        indent=2,
                        default=float,
                    )
                )
            if extrapolate_only:
                break
        if extrapolate_only:
            break
    report["size_passed"] = bool(size_pass)
    report["elapsed_seconds"] = time.time() - t0
    return report


# ---------------------------------------------------------------------------------------------
# Verdict text
# ---------------------------------------------------------------------------------------------


def conclusion_ja(
    states: dict[str, int], cfg: dict, selftest_report: dict | None, *, confirmatory_end: str
) -> str:
    n_adopt = int(states.get("ADOPT_CLOSE", 0))
    n_ruled = int(states.get("RULED_OUT", 0))
    n_not = int(states.get("NOT_ADOPTED", 0))
    n_nd = int(states.get("NO_DECISION", 0))
    n_so = int(states.get("SCREENED_OUT", 0))
    delta = float(cfg["ruled_out_delta"])
    mde_txt = ""
    if selftest_report:
        mdes = [v.get("mde_80_from_curve") for v in selftest_report.get("power", {}).values()]
        mdes = [m for m in mdes if m is not None and np.isfinite(m)]
        n_curves = len(selftest_report.get("power", {}))
        n_unreached = n_curves - len(mdes)
        if mdes:
            mde_txt = (
                f" 自己検証で測った 80% 検出可能な最小効果は回収率 +{min(mdes):.3f}〜"
                f"+{max(mdes):.3f} で、これより小さい真の利益は検出できない"
                + (
                    f"(代表 {n_curves} 曲線のうち 80% に届いたのは {n_curves - n_unreached} 本。"
                    "届かない曲線には常時降格の最小パターンと実現不能な形を含む)。"
                    if n_unreached
                    else "。"
                )
            )
    w = cfg.get("windows", {})
    d0 = str(w.get("discovery", ["2008-01-01"])[0])[:4]
    q1 = str(w.get("qualification", ["", "2018-12-31"])[1])[:4]
    c0 = str(w.get("confirmatory", ["2019-01-01"])[0])[:4]
    head = (
        f"凍結した探索(screening {d0}〜{q1} → 確認窓 {c0}〜{confirmatory_end})で、"
        f"closing オッズ条件つき回収率が 1 を超える候補を"
    )
    if n_adopt > 0:
        body = (
            f"{n_adopt} 本確認した(ADOPT_CLOSE)。closing の価格で選んだ歴史的な結果であり、"
            "買えるパターンの証明ではない。"
        )
    else:
        body = "確認できなかった。"
    tail = (
        f" 内訳: ADOPT_CLOSE {n_adopt} / NOT_ADOPTED {n_not}"
        f"(利益を示せず δ={delta:.2f} 超も排除できない) / RULED_OUT {n_ruled}(δ 超の利益を否定)"
        f" / NO_DECISION {n_nd} / SCREENED_OUT {n_so}(確認未実施であり否定ではない)。"
    )
    text = head + body + tail + mde_txt
    for ph in FORBIDDEN_PHRASES:
        if ph in text:
            raise ValueError(f"forbidden phrase in conclusion: {ph}")
    return text


def build_verdict(
    *,
    cfg: dict,
    gate_config_hash: str,
    patterns_hash: str,
    population_hash: str,
    survivors_hash: str,
    bundle_digest: str,
    run_code_sha: str,
    run_tree_dirty: bool,
    windows: dict,
    states: dict[str, int],
    per_survivor: list[dict],
    controls: dict,
    evidence_refs: list[dict],
    selftest_report: dict | None,
    screened_out_reasons: dict,
) -> dict:
    conf_end = windows["confirmatory"][1]
    v = {
        "feature": "109-buy-pattern-gate",
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "gate_config_hash": gate_config_hash,
        "patterns_hash": patterns_hash,
        "population_hash": population_hash,
        "survivors_hash": survivors_hash,
        "bundle_digest": bundle_digest,
        "code_sha": cfg.get("code_sha"),
        "run_code_sha": run_code_sha,
        "run_tree_dirty": bool(run_tree_dirty),
        "windows": windows,
        "evidence_refs": evidence_refs,
        "controls": controls,
        "states": {s: int(states.get(s, 0)) for s in STATES},
        "screened_out_reasons": screened_out_reasons,
        "per_survivor": per_survivor,
        "limitations": list(LIMITATIONS),
        "limitation_notes": {
            "closing_price_leak": (
                "選定価格と精算価格がともに closing オッズ。closing-line oracle であり"
                "発注可能性は評価しない。"
            ),
            "payout_approximation": "公式払戻ではなくオッズ×100 円の近似。",
            "dead_heat_handling": "同着は主判定で除外し感度(等分 / odds/max(2,勝者数))で確認。",
            "no_correction_history": "過去走由来の述語と頭数は現在の DB 値で作り訂正履歴が無い。",
            "win_only": "単勝のみ。複勝・組合せは発走前価格の蓄積が 2025 年以降で screening 不能。",
        },
        "resume_conditions": list(RESUME_CONDITIONS),
        "conclusion_ja": conclusion_ja(states, cfg, selftest_report, confirmatory_end=conf_end),
    }
    return v


def validate_verdict(v: dict) -> None:
    required = (
        "gate_config_hash",
        "patterns_hash",
        "population_hash",
        "survivors_hash",
        "bundle_digest",
        "run_code_sha",
        "run_tree_dirty",
        "windows",
        "evidence_refs",
        "controls",
        "states",
        "per_survivor",
        "limitations",
        "resume_conditions",
        "conclusion_ja",
    )
    missing = [k for k in required if k not in v]
    if missing:
        raise ValueError(f"verdict missing keys: {missing}")
    if tuple(v["limitations"]) != LIMITATIONS:
        raise ValueError("verdict limitations must be the fixed 5 items")
    for ph in FORBIDDEN_PHRASES:
        if ph in v["conclusion_ja"]:
            raise ValueError(f"forbidden phrase: {ph}")
    for s in v["per_survivor"]:
        for k in (
            "pattern_id",
            "state",
            "available_at",
            "historical_close_signal",
            "p_profit_one_sided",
            "p_futility_one_sided",
        ):
            if k not in s:
                raise ValueError(f"per_survivor entry missing {k}")
