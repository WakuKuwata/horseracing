"""Feature 082: segment accuracy readout — pure instrument core (SECONDARY, spec 082).

Measures the ACTIVE recipe's historical OOF accuracy per frozen mask axis. This is a
verification instrument: it generates hypotheses but never adjudicates them. It has no
adoption semantics — the 073 gate must never reference its output (FR-013).

Estimand: "active-recipe historical OOF accuracy" — NOT the deployed artifact's operational
accuracy (that is a separate, deferred prospective instrument).

Primary displays (codex review, all adopted):
* race-grain axes:  ``excess_nll_uniform = mean[-log p_winner - log field_size]``
  (additive, 0 for a uniform predictor at ANY field size; raw winner/uniform/market NLL are
  context columns, market restricted to the market-complete subset).
* horse-grain axes: paired per-horse excess logloss vs the same-row ``p=1/field_size``
  baseline, plus fixed-bin reliability / ECE / calibration-in-the-large.
* two-grain ECE: a race-grain axis selects RACES; its calibration block then uses ALL
  started horses within the selected races (grain recorded in the payload).

Anti-fishing output contract (spec US2): payload is in frozen library order, carries NO
worst/rank/verdict/colour fields, labels every pointwise CI as unadjusted, and shows
stability as non-overlapping per-year values.

Leak boundary (II): inputs are OOF probabilities + result-blind attributes; results appear
only as evaluation labels. Nothing here may flow back into model features.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .bootstrap import race_day_cluster_bootstrap_ci_v1
from .hashing import stable_hash
from .segment_edge import class_group

MASK_LIBRARY_VERSION = "sa-mask-v1"
METRIC_CONTRACT_VERSION = "sa-v1"

#: sa-v1 metric contract: fixed equal-width probability bins (021 convention), frozen here.
PROB_BIN_EDGES: tuple[float, ...] = tuple(np.round(np.linspace(0.0, 1.0, 11), 2))

#: payload keys that must NEVER appear anywhere (anti-fishing output contract, spec US2/SC-003)
FORBIDDEN_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {"worst_segment", "rank", "verdict", "pass", "fail", "color", "adopted", "decision"}
)

MISSING = "missing"


@dataclass(frozen=True)
class MaskAxis:
    """One frozen segmentation axis. ``definition`` freezes boundaries/buckets/missing policy —
    its hash is the comparability key across runs (definition change => NEW axis id)."""

    axis_id: str
    family: str
    grain: str                      # "race" | "horse"
    origin: str                     # "core" | "post_081_exploratory"
    definition: dict[str, Any]

    @property
    def definition_hash(self) -> str:
        return stable_hash({
            "axis_id": self.axis_id, "grain": self.grain, "definition": self.definition,
        })


def _band(value, edges: list, labels: list[str]) -> str:
    """Half-open banding: value < edges[i] -> labels[i]; else last label. None/NaN -> missing."""
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return MISSING
    for e, lab in zip(edges, labels[:-1], strict=True):
        if value < e:
            return lab
    return labels[-1]


# --- frozen v1 axis definitions -------------------------------------------------------------
# Boundaries reuse the pre-registered bins of 020 (dist), 047 (class/field/q) and record the
# 081 folklore origin for the exploratory family (post-selection: NOT usable as independent
# confirmation of 081, spec US3).

def _assign_race_axis(axis_id: str, attrs: dict) -> str:
    a = attrs
    if axis_id == "year":
        return str(a.get("year") or MISSING)
    if axis_id == "surface":
        return a.get("track_type") or MISSING
    if axis_id == "dist_band":
        return _band(a.get("distance"), [1401, 1801, 2201], ["<=1400", "<=1800", "<=2200", ">2200"])
    if axis_id == "race_class":
        rc = a.get("race_class")
        # 047's definition, not a second copy of it. The exact-match whitelist this replaces filed
        # ＪＧ１/ＪＧ２/ＪＧ３ (187 graded jump races) under 条件, so "how does the model do on
        # conditioned races" was reading a set that included graded stakes. `class_group` answers
        # the same axis by NFKC + substring, which is what makes it survive a vocabulary that
        # demonstrably moved at the source cutover (`1勝` → `１勝`, `ｵｰﾌﾟﾝ` → `オープン`).
        return class_group(rc) if rc else MISSING
    if axis_id == "field_size_band":
        return _band(a.get("field_size"), [9, 14], ["<=8", "9-13", ">=14"])
    if axis_id == "venue_track":
        v, t = a.get("venue_code"), a.get("track_type")
        return f"{v}:{t}" if v and t else MISSING
    raise KeyError(axis_id)


def _assign_horse_axis(axis_id: str, h: dict) -> str:
    if axis_id == "sex":
        return h.get("sex") or MISSING
    if axis_id == "history_depth":
        n = h.get("n_prior_starts")
        return _band(n, [1, 3], ["debut", "1-2", "3+"])
    if axis_id == "id_source":
        hid = h.get("horse_id") or ""
        return "nk" if hid.startswith("nk:") else "canonical"
    if axis_id == "pm_coverage":
        return _band(h.get("n_prior_odds_obs"), [1, 3], ["0", "1-2", "3+"])
    if axis_id == "q_band":
        q = h.get("q")
        if q is None or (isinstance(q, float) and not np.isfinite(q)):
            return "q_missing"
        return _band(q, [0.05, 0.15, 0.30], ["<0.05", "0.05-0.15", "0.15-0.30", ">=0.30"])
    if axis_id == "sex_season":
        sex, mon = h.get("sex"), h.get("month")
        if not sex or mon is None:
            return MISSING
        season = "summer" if 6 <= int(mon) <= 9 else "other"
        who = "female" if sex == "牝" else "male_gelding"
        return f"{who}:{season}"
    if axis_id == "rotation_band":
        return _band(h.get("days_since_last"), [8, 15, 29, 71],
                     ["<=7", "8-14", "15-28", "29-70", ">70"])
    if axis_id == "prior_rotation":
        g = h.get("prior_gap_days")
        if g is None or (isinstance(g, float) and not np.isfinite(g)):
            return MISSING
        return "prior>70d" if g > 70 else "prior<=70d"
    if axis_id == "prev_finish_band":
        return _band(h.get("prev_finish"), [4, 6, 10], ["1-3", "4-5", "6-9", "10+"])
    if axis_id == "draw_band":
        return _band(h.get("draw_pct"), [0.25, 0.75], ["inner", "mid", "outer"])
    if axis_id == "body_mass_going":
        w, cell = h.get("weight"), h.get("body_cell")
        if w is None or not cell:
            return MISSING
        return f"{'light' if w < 440 else 'notlight'}:{cell}"
    if axis_id == "weight_gain_band":
        return _band(h.get("weight_diff"), [-10, 11], ["<=-11", "-10..+10", ">=+11"])
    raise KeyError(axis_id)


def _core(axis_id, family, grain, definition):
    return MaskAxis(axis_id, family, grain, "core", definition)


def _p081(axis_id, family, grain, definition):
    d = dict(definition)
    d["origin_note"] = ("boundaries chosen AFTER seeing 081 folklore results; "
                        "cannot serve as 081's independent confirmation")
    return MaskAxis(axis_id, family, grain, "post_081_exploratory", d)


MASK_LIBRARY_V1: tuple[MaskAxis, ...] = (
    _core("year", "temporal", "race", {"buckets": "eval calendar year"}),
    _core("surface", "race_core", "race", {"buckets": ["芝", "ダ", "障"], "missing": MISSING}),
    _core("dist_band", "race_core", "race",
          {"edges_m": [1401, 1801, 2201], "origin_bins": "020"}),
    _core("race_class", "race_core", "race",
          {"buckets": ["新馬", "未勝利", "OP系", "条件"], "origin_bins": "047"}),
    _core("field_size_band", "race_core", "race",
          {"edges": [9, 14], "origin_bins": "047"}),
    _core("venue_track", "course", "race", {"cross": "venue_code x track_type"}),
    _core("sex", "horse_core", "horse", {"buckets": ["牡", "牝", "セ"], "missing": MISSING}),
    _core("history_depth", "horse_core", "horse", {"edges_prior_starts": [1, 3]}),
    _core("id_source", "data_quality", "horse", {"rule": "horse_id startswith 'nk:'"}),
    _core("pm_coverage", "data_quality", "horse",
          {"edges_prior_odds_obs": [1, 3], "origin_bins": "069"}),
    _core("q_band", "market_context", "horse",
          {"edges": [0.05, 0.15, 0.30], "origin_bins": "047",
           "note": "closing-market-conditioned (race_horses.odds is closing-leaning)"}),
    _p081("sex_season", "post_081_exploratory", "horse",
          {"summer_months": [6, 9], "female": "牝"}),
    _p081("rotation_band", "post_081_exploratory", "horse",
          {"edges_days": [8, 15, 29, 71]}),
    _p081("prior_rotation", "post_081_exploratory", "horse", {"layoff_days": 70}),
    _p081("prev_finish_band", "post_081_exploratory", "horse", {"edges": [4, 6, 10]}),
    _p081("draw_band", "post_081_exploratory", "horse", {"edges_pct": [0.25, 0.75]}),
    _p081("body_mass_going", "post_081_exploratory", "horse",
          {"light_kg": 440, "cells": ["turf-firm", "turf-off", "dirt"]}),
    _p081("weight_gain_band", "post_081_exploratory", "horse", {"edges_kg": [-10, 11]}),
)


def mask_library_hash() -> str:
    return stable_hash([m.definition_hash for m in MASK_LIBRARY_V1])


# --- inputs ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class RaceInput:
    """One eligible race's instrument inputs (winner NLL population).

    ``p`` sums to ~1 (asserted); ``y`` is the started-all winner label vector; ``q`` is the
    market vote share on the COMPLETE field or None (market-incomplete — model metrics still
    computed, market metrics unavailable; populations deliberately separated, codex).
    """

    race_id: str
    day: str
    year: int
    field_size: int
    winner_idx: int
    p: np.ndarray
    y: np.ndarray
    q: np.ndarray | None
    race_attrs: dict[str, Any]
    horse_attrs: tuple[dict[str, Any], ...] = field(default=())


# --- metric primitives ----------------------------------------------------------------------

_CLIP = 1e-12


def _race_excess_uniform(r: RaceInput) -> float:
    return float(-np.log(max(r.p[r.winner_idx], _CLIP)) - np.log(r.field_size))


def _race_winner_nll(r: RaceInput) -> float:
    return float(-np.log(max(r.p[r.winner_idx], _CLIP)))


def _race_market_nll(r: RaceInput) -> float | None:
    if r.q is None:
        return None
    return float(-np.log(max(r.q[r.winner_idx], _CLIP)))


def _horse_excess_logloss(p: float, y: int, field_size: int) -> float:
    """Per-horse binary logloss minus the same-row 1/N-baseline logloss (paired excess)."""
    pc = min(max(p, _CLIP), 1 - _CLIP)
    b = 1.0 / field_size
    loss = -(y * np.log(pc) + (1 - y) * np.log(1 - pc))
    base = -(y * np.log(b) + (1 - y) * np.log(1 - b))
    return float(loss - base)


def _bin_index(ps: np.ndarray) -> np.ndarray:
    """Fixed-bin index per observation (sa-v1 equal-width bins; ps in [0,1])."""
    idx = np.minimum((ps * (len(PROB_BIN_EDGES) - 1)).astype(int), len(PROB_BIN_EDGES) - 2)
    return idx


def _ece_cluster_ci(
    ps: np.ndarray, ys: np.ndarray, days: np.ndarray, *, seed: int, b: int,
) -> dict:
    """Race-day cluster bootstrap CI for the fixed-bin ECE (FR-005; unadjusted, codex P0#3).

    Vectorised via per-day per-bin sufficient statistics (count, Σp, Σy): each replicate
    resamples days with replacement and recombines — O(b × n_days × n_bins), not O(b × n)."""
    uniq_days, day_idx = np.unique(days, return_inverse=True)
    n_days = len(uniq_days)
    if n_days < 2:
        return {"point": None, "ci_low": None, "ci_high": None, "n_days": n_days,
                "no_decision": True,
                "ci_note": "pointwise 95% CI, NOT adjusted for multiple comparisons"}
    n_bins = len(PROB_BIN_EDGES) - 1
    bins = _bin_index(ps)
    flat = day_idx * n_bins + bins
    cnt = np.bincount(flat, minlength=n_days * n_bins).reshape(n_days, n_bins)
    psum = np.bincount(flat, weights=ps, minlength=n_days * n_bins).reshape(n_days, n_bins)
    ysum = np.bincount(flat, weights=ys, minlength=n_days * n_bins).reshape(n_days, n_bins)

    def _ece(c, p_, y_):
        tot = c.sum()
        m = c > 0
        return float(np.sum(np.abs(p_[m] / c[m] - y_[m] / c[m]) * (c[m] / tot))) if tot else None

    point = _ece(cnt.sum(0), psum.sum(0), ysum.sum(0))
    rng = np.random.default_rng(seed)
    boots = np.empty(b)
    for i in range(b):
        pick = rng.integers(0, n_days, size=n_days)
        boots[i] = _ece(cnt[pick].sum(0), psum[pick].sum(0), ysum[pick].sum(0))
    return {"point": None if point is None else round(point, 6),
            "ci_low": round(float(np.percentile(boots, 2.5)), 6),
            "ci_high": round(float(np.percentile(boots, 97.5)), 6),
            "n_days": n_days, "no_decision": False,
            "ci_note": "pointwise 95% CI, NOT adjusted for multiple comparisons"}


def _reliability(ps: np.ndarray, ys: np.ndarray) -> dict:
    """Fixed equal-width bins (sa-v1): reliability table + ECE + calibration-in-the-large.
    Wilson CIs are AUXILIARY (horse rows within a race are dependent, codex P0#5)."""
    bins = []
    ece = 0.0
    n = len(ps)
    for lo, hi in zip(PROB_BIN_EDGES[:-1], PROB_BIN_EDGES[1:], strict=True):
        m = (ps >= lo) & (ps < hi) if hi < 1.0 else (ps >= lo) & (ps <= hi)
        cnt = int(m.sum())
        if cnt == 0:
            bins.append({"lo": lo, "hi": hi, "n": 0, "pred_mean": None, "realized": None,
                         "wilson_low": None, "wilson_high": None})
            continue
        pm = float(ps[m].mean())
        rr = float(ys[m].mean())
        z = 1.959964
        denom = 1 + z * z / cnt
        centre = (rr + z * z / (2 * cnt)) / denom
        half = z * np.sqrt(rr * (1 - rr) / cnt + z * z / (4 * cnt * cnt)) / denom
        bins.append({"lo": lo, "hi": hi, "n": cnt, "pred_mean": round(pm, 6),
                     "realized": round(rr, 6),
                     "wilson_low": round(centre - half, 6), "wilson_high": round(centre + half, 6)})
        ece += (cnt / n) * abs(pm - rr)
    return {
        "grain_note": "Wilson CIs are auxiliary: horse rows within a race are dependent",
        "bins": bins,
        "ece": round(float(ece), 6) if n else None,
        "calibration_in_the_large": round(float(ps.mean() - ys.mean()), 6) if n else None,
        "n": n,
    }


def _ci(dct_by_day: dict, *, seed: int, b: int) -> dict:
    ci = race_day_cluster_bootstrap_ci_v1(dct_by_day, b=b, seed=seed)
    return {
        "point": None if ci.point != ci.point else round(ci.point, 6),
        "ci_low": ci.ci_low, "ci_high": ci.ci_high, "n_days": ci.n_days,
        "no_decision": ci.no_decision,
        "ci_note": "pointwise 95% CI, NOT adjusted for multiple comparisons",
    }


# --- per-axis computation -------------------------------------------------------------------

def _race_axis_block(axis: MaskAxis, races: list[RaceInput], *, seed: int, b: int) -> dict:
    buckets: dict[str, list[RaceInput]] = {}
    for r in races:
        buckets.setdefault(_assign_race_axis(axis.axis_id, r.race_attrs), []).append(r)
    out = {}
    for bucket in sorted(buckets):
        rs = buckets[bucket]
        exc_by_day: dict[str, list[float]] = {}
        exc_by_year: dict[int, list[float]] = {}
        for r in rs:
            e = _race_excess_uniform(r)
            exc_by_day.setdefault(r.day, []).append(e)
            exc_by_year.setdefault(r.year, []).append(e)
        wnll = float(np.mean([_race_winner_nll(r) for r in rs]))
        unll = float(np.mean([np.log(r.field_size) for r in rs]))
        # market comparison on the SAME population: both model and market winner NLL restricted
        # to the market-complete subset (codex P0#3 — a mixed-population difference is not a
        # like-for-like comparison).
        mk_rs = [r for r in rs if r.q is not None]
        mk_nll = [_race_market_nll(r) for r in mk_rs]
        # two-grain calibration: ALL started horses within the SELECTED races
        ps = np.concatenate([r.p for r in rs])
        ys = np.concatenate([r.y for r in rs])
        days_h = np.concatenate([np.full(len(r.p), r.day) for r in rs])
        cal = _reliability(ps, ys)
        # race-grain calibration-in-the-large is IDENTICALLY 0 (Σp = Σy = 1 per selected race) —
        # displaying the 0 would misread as "well calibrated" (codex P0#3).
        cal["calibration_in_the_large"] = None
        cal["citl_note"] = "structurally 0 at race grain (sum p = sum y = 1 per selected race)"
        out[bucket] = {
            "grain": {"winner_nll": "race", "calibration": "started_horse_within_selected_races"},
            "n_races": len(rs), "n_horses": int(len(ps)),
            "excess_nll_uniform": _ci(exc_by_day, seed=seed, b=b),
            "winner_nll": round(wnll, 6), "uniform_nll": round(unll, 6),
            "market": ({"n_market_complete_races": len(mk_rs),
                        "n_total_races": len(rs),
                        "market_nll": round(float(np.mean(mk_nll)), 6),
                        "winner_nll_market_subset": round(
                            float(np.mean([_race_winner_nll(r) for r in mk_rs])), 6),
                        "excess_nll_market": round(
                            float(np.mean([_race_winner_nll(r) for r in mk_rs]))
                            - float(np.mean(mk_nll)), 6)}
                       if mk_rs else {"n_market_complete_races": 0, "n_total_races": len(rs)}),
            "by_year": {str(y): {"n_races": len(v),
                                 "excess_nll_uniform_point": round(float(np.mean(v)), 6)}
                        for y, v in sorted(exc_by_year.items())},
            "calibration": cal,
            "ece_ci": _ece_cluster_ci(ps, ys, days_h, seed=seed, b=b),
        }
    return out


def _horse_axis_block(axis: MaskAxis, races: list[RaceInput], *, seed: int, b: int) -> dict:
    # bucket -> accumulators
    acc: dict[str, dict] = {}
    for r in races:
        for i, h in enumerate(r.horse_attrs):
            bucket = _assign_horse_axis(axis.axis_id, h)
            a = acc.setdefault(bucket, {
                "exc_by_day": {}, "exc_by_year": {}, "ps": [], "ys": [], "days": [],
                "race_ids": set(),
            })
            e = _horse_excess_logloss(float(r.p[i]), int(r.y[i]), r.field_size)
            a["exc_by_day"].setdefault(r.day, []).append(e)
            a["exc_by_year"].setdefault(r.year, []).append(e)
            a["ps"].append(float(r.p[i]))
            a["ys"].append(int(r.y[i]))
            a["days"].append(r.day)
            a["race_ids"].add(r.race_id)
    out = {}
    for bucket in sorted(acc):
        a = acc[bucket]
        ps, ys = np.asarray(a["ps"]), np.asarray(a["ys"])
        out[bucket] = {
            "grain": {"excess_logloss": "horse", "winner_nll": "NOT_AVAILABLE_AT_HORSE_GRAIN"},
            "n_horses": int(len(ps)), "n_races": len(a["race_ids"]),
            "excess_logloss_vs_uniform": _ci(a["exc_by_day"], seed=seed, b=b),
            "by_year": {str(y): {"n_horses": len(v),
                                 "excess_logloss_point": round(float(np.mean(v)), 6)}
                        for y, v in sorted(a["exc_by_year"].items())},
            "calibration": _reliability(ps, ys),
            "ece_ci": _ece_cluster_ci(ps, ys, np.asarray(a["days"]), seed=seed, b=b),
        }
    return out


# --- payload --------------------------------------------------------------------------------

def build_payload(
    races: list[RaceInput],
    *,
    provenance: dict[str, Any],
    exclusions: dict[str, int],
    seed: int,
    bootstrap_b: int = 2000,
) -> dict:
    """Build the full segment_accuracy payload (frozen library order, output contract)."""
    for r in races:
        s = float(r.p.sum())
        if not (0.999 <= s <= 1.001):
            raise ValueError(f"race {r.race_id}: p does not sum to 1 ({s})")
        if len(r.horse_attrs) != len(r.p):
            raise ValueError(f"race {r.race_id}: horse_attrs misaligned with p")
    axes = []
    for axis in MASK_LIBRARY_V1:   # frozen order = payload order (no score sorting)
        block = (_race_axis_block if axis.grain == "race" else _horse_axis_block)(
            axis, races, seed=seed, b=bootstrap_b,
        )
        axes.append({
            "axis_id": axis.axis_id, "family": axis.family, "grain": axis.grain,
            "origin": axis.origin, "definition": axis.definition,
            "mask_definition_hash": axis.definition_hash,
            "buckets": block,
        })
    payload = {
        "instrument_contract": {
            "kind": "segment_accuracy",
            "secondary": True,
            "can_adopt": False,
            "estimand": "active-recipe historical OOF accuracy "
                        "(NOT deployed-artifact operational accuracy)",
            "discovery_rule": "verifying anything found here requires a NEW pre-registration "
                              "carrying discovery_run_id; the 073 gate must never reference "
                              "this instrument",
            "ci_note": "all CIs are pointwise and NOT adjusted for multiple comparisons",
            "known_confounds": [
                "model-age-within-year: yearly expanding folds predict a whole year with the "
                "prior-year-end model (confounds season diagnostics)",
                "q is closing-leaning (race_horses.odds), not a pre-race snapshot",
            ],
            "metric_contract_version": METRIC_CONTRACT_VERSION,
            "mask_library_version": MASK_LIBRARY_VERSION,
            "mask_library_hash": mask_library_hash(),
        },
        "provenance": dict(provenance),
        "population": {
            "n_scored_races": len(races),
            "n_scored_horses": int(sum(len(r.p) for r in races)),
            "exclusions": dict(exclusions),
        },
        "axes": axes,
    }
    _assert_no_forbidden_keys(payload)
    return payload


def _assert_no_forbidden_keys(obj: Any) -> None:
    if isinstance(obj, dict):
        bad = FORBIDDEN_PAYLOAD_KEYS & {str(k).lower() for k in obj}
        if bad:
            raise ValueError(f"forbidden payload keys present: {sorted(bad)}")
        for v in obj.values():
            _assert_no_forbidden_keys(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _assert_no_forbidden_keys(v)
