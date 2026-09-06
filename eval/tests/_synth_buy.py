"""Synthetic bet-row arrays for the feature 109 unit tests (numpy only, no DB)."""

from __future__ import annotations

import datetime as dt

import numpy as np

CLASSES = ["新馬", "未勝利", "1勝", "２勝", "1600万", "ｵｰﾌﾟﾝ", "Ｇ１"]


def gate_config(**over) -> dict:
    cfg = {
        "feature": "109-buy-pattern-gate", "bet_type": "win", "stake_yen": 100,
        "payout_rule": "odds_x_stake_if_won",
        "dead_heat": {"primary": "exclude_race", "sensitivities": ["equal_split", "half_odds"]},
        "population_rule": {"bundle_digest": "test", "track_types": ["芝", "ダ"],
                            "require_odds_all_started": True, "require_p_all_started": True,
                            "exactly_one_winner": True},
        "windows": {"discovery": ["2008-01-01", "2013-12-31"],
                    "qualification": ["2014-01-01", "2018-12-31"],
                    "confirmatory": ["2019-01-01", "2026-08-31"]},
        "bootstrap": {"block": "race_day", "b": 200, "seed": 20260905, "alpha_two_sided": 0.05,
                      "sensitivity_blocks": ["iso_week", "calendar_month"], "block_kind": "fixed_cluster"},
        "selftest": {"b_inner_size": 200, "b_inner_power": 100, "reps_size_per_config": 8,
                     "reps_power": 4, "analysis_versions": ["race_day", "iso_week", "calendar_month"],
                     "rho_grid": [1.0, 1.10], "rho_negative_control": 0.796,
                     "edge_shapes": ["klmin_roi_tilt", "long_odds_top20pct", "days_10pct"],
                     "edge_shape_sensitivity": "odds_neutral", "size_alpha_one_sided": 0.025},
        "test": {"profit_null": "roi_le_1", "futility_null": "roi_ge_1.02",
                 "p_value": "basic_bootstrap_one_sided_plus_one", "holm_alpha_one_sided": 0.025},
        "demotion": {"min_hits": 3, "max_single_hit_share": 0.5, "min_days": 5},
        "survivor_rule": {"point_min": 1.0, "rank_key": "min_lcb", "max_survivors": 5,
                          "merge_identical": True, "jaccard_max": 0.9},
        "ruled_out_delta": 0.02,
        "controls": ["no_bet", "favorite", "cap11_all", "cap21_all"],
        "patterns_hash": "x", "code_sha": "y", "evaluation_contract_version": "roi-v1",
    }
    cfg.update(over)
    return cfg


def make_arrays(rng: np.random.Generator, *, n_races: int = 120, start: str = "2010-01-09",
                n_days: int = 30, field_min: int = 6, field_max: int = 12,
                jump_races: int = 0, missing_odds_races: int = 0, missing_p_races: int = 0,
                not_in_bundle_races: int = 0, dead_heat_races: int = 0, no_winner_races: int = 0) -> dict:
    d0 = dt.date.fromisoformat(start)
    days = [d0 + dt.timedelta(days=7 * (i // 12) + (i % 12) % 2) for i in range(n_days)]
    days = sorted(set(days))
    cols: dict[str, list] = {k: [] for k in (
        "race_id", "horse_number", "horse_id", "race_date", "year", "track_type", "distance",
        "race_class", "field_size", "sex", "interval_days", "prev_finish", "tataki_2", "odds", "p",
        "in_bundle", "won", "n_winners")}
    special = {}
    order = list(range(n_races))
    rng.shuffle(order)
    pos = 0
    for label, cnt in (("jump", jump_races), ("mo", missing_odds_races), ("mp", missing_p_races),
                       ("nb", not_in_bundle_races), ("dh", dead_heat_races), ("nw", no_winner_races)):
        for j in order[pos:pos + cnt]:
            special[j] = label
        pos += cnt
    for r in range(n_races):
        day = days[r % len(days)]
        n = int(rng.integers(field_min, field_max + 1))
        rid = f"{day.year}{(r % 10) + 1:02d}{(r // 10) % 5 + 1:02d}{(r % 8) + 1:02d}{(r % 12) + 1:02d}"
        rid = rid[:12].ljust(12, "0")
        rid = f"{day.year}" + f"{r:08d}"
        strength = rng.gamma(2.0, 1.0, size=n)
        q = strength / strength.sum()
        odds = np.round(0.8 / q, 1)
        odds = np.maximum(odds, 1.1)
        p = q * np.exp(rng.normal(0, 0.15, size=n))
        p = p / p.sum()
        kind = special.get(r)
        if kind == "nw":
            winners: list[int] = []
        elif kind == "dh":
            w = rng.choice(n, size=2, replace=False, p=q)
            winners = list(w)
        else:
            winners = [int(rng.choice(n, p=q))]
        for h in range(n):
            cols["race_id"].append(rid)
            cols["horse_number"].append(h + 1)
            cols["horse_id"].append(f"h{r:04d}{h:02d}")
            cols["race_date"].append(day.isoformat())
            cols["year"].append(day.year)
            cols["track_type"].append("障" if kind == "jump" else ("芝" if r % 2 == 0 else "ダ"))
            cols["distance"].append(float([1200, 1600, 2000, 2400][r % 4]))
            cols["race_class"].append(CLASSES[r % len(CLASSES)])
            cols["field_size"].append(float(n))
            cols["sex"].append(["牡", "牝", "セ"][h % 3])
            iv = float(rng.choice([np.nan, 7, 14, 21, 35, 60, 90]))
            cols["interval_days"].append(iv)
            cols["prev_finish"].append(np.nan if np.isnan(iv) else float(rng.integers(1, 14)))
            cols["tataki_2"].append(np.nan if np.isnan(iv) else float(rng.integers(0, 2)))
            cols["odds"].append(np.nan if (kind == "mo" and h == 0) else float(odds[h]))
            cols["p"].append(np.nan if (kind == "mp" and h == 1) else float(p[h]))
            cols["in_bundle"].append(kind != "nb")
            cols["won"].append(h in winners)
            cols["n_winners"].append(len(winners))
    arr = {k: np.asarray(v, dtype=object if k in ("race_id", "horse_id", "race_date", "track_type", "race_class", "sex") else None)
           for k, v in cols.items()}
    arr["won"] = arr["won"].astype(bool)
    arr["in_bundle"] = arr["in_bundle"].astype(bool)
    arr["n_winners"] = arr["n_winners"].astype(int)
    arr["horse_number"] = arr["horse_number"].astype(int)
    arr["year"] = arr["year"].astype(int)
    for k in ("distance", "field_size", "interval_days", "prev_finish", "tataki_2", "odds", "p"):
        arr[k] = arr[k].astype(float)
    return arr


def add_context(arr: dict) -> dict:
    """Derived context columns the driver normally adds (canon class, sex group, season)."""
    from horseracing_eval.buy_patterns import canon_class

    out = dict(arr)
    out["race_class_canon"] = np.asarray([canon_class(x) for x in arr["race_class"]], dtype=object)
    out["sex_group"] = np.asarray([("F" if s == "牝" else ("M" if s in ("牡", "セ") else None)) for s in arr["sex"]], dtype=object)
    months = np.asarray([int(str(d)[5:7]) for d in arr["race_date"]])
    out["is_summer"] = np.isin(months, [6, 7, 8, 9]).astype(float)
    return out
