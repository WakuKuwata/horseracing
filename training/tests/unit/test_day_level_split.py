"""T021: day-level model/calib split (FR-014b, codex C4) + A race-count reproduction."""

from __future__ import annotations

import datetime

import numpy as np

from horseracing_training.calibration import split_train_by_day, split_train_by_time


def test_day_level_split_never_straddles_a_race_day():
    # 4 days, 3 races each. A day-level split must put every race of a day on the SAME side.
    race_ids, race_dates = [], {}
    for d in range(4):
        for r in range(3):
            rid = f"D{d}R{r}"
            race_ids.append(rid)
            race_dates[rid] = datetime.date(2025, 1, d + 1)
    model_mask, calib_mask = split_train_by_day(race_ids, race_dates, calib_frac=0.25)
    # group side by day; each day must be entirely model or entirely calib
    by_day: dict = {}
    for rid, m in zip(race_ids, calib_mask, strict=True):
        by_day.setdefault(race_dates[rid], set()).add(bool(m))
    for day, sides in by_day.items():
        assert len(sides) == 1, f"day {day} straddles model/calib boundary"
    # latest 25% of 4 days = 1 day (the last) is calib
    assert calib_mask.sum() == 3
    assert set(np.array(race_ids)[calib_mask]) == {"D3R0", "D3R1", "D3R2"}


def test_race_count_split_can_straddle_a_day_reproducing_A():
    # The legacy race-count split is retained for A reproduction; with uneven per-day counts it
    # may split a day (the exact behavior 068 A must reproduce).
    race_ids, race_dates = [], {}
    # day1: 8 races, day2: 2 races -> latest 30% of 10 races = 3 races cross into day1
    for r in range(8):
        race_ids.append(f"A{r}")
        race_dates[f"A{r}"] = datetime.date(2025, 1, 1)
    for r in range(2):
        race_ids.append(f"B{r}")
        race_dates[f"B{r}"] = datetime.date(2025, 1, 2)
    _m, calib_mask = split_train_by_time(race_ids, race_dates, calib_frac=0.3)
    calib_days = {race_dates[r] for r, m in zip(race_ids, calib_mask, strict=True) if m}
    assert len(calib_days) == 2  # straddles both days (documents the difference vs day-level)


def test_single_day_yields_no_calib():
    race_ids = ["x", "y"]
    race_dates = {"x": datetime.date(2025, 1, 1), "y": datetime.date(2025, 1, 1)}
    model_mask, calib_mask = split_train_by_day(race_ids, race_dates, calib_frac=0.3)
    assert calib_mask.sum() == 0
    assert model_mask.all()
