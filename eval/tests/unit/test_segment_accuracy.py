"""Feature 082 segment accuracy readout — pure-core unit tests (SC-002/003/006 + grain rules)."""

from __future__ import annotations

import numpy as np
import pytest

from horseracing_eval.segment_accuracy import (
    MASK_LIBRARY_V1,
    RaceInput,
    _assign_horse_axis,
    _assign_race_axis,
    build_payload,
    mask_library_hash,
)


def _uniform_race(race_id, day, year, n, winner_idx, **attr_over):
    p = np.full(n, 1.0 / n)
    y = np.zeros(n, dtype=int)
    y[winner_idx] = 1
    race_attrs = {"year": year, "track_type": "芝", "distance": 1600, "race_class": "未勝利",
                  "field_size": n, "venue_code": "05"}
    hattrs = tuple(
        {"sex": "牡", "horse_id": f"h{i}", "n_prior_starts": 5, "n_prior_odds_obs": 5,
         "q": 1.0 / n, "month": 5, "days_since_last": 30, "prior_gap_days": 30,
         "prev_finish": 5, "draw_pct": i / max(n - 1, 1), "weight": 480,
         "body_cell": "turf-firm", "weight_diff": 0, **attr_over}
        for i in range(n)
    )
    return RaceInput(race_id=race_id, day=day, year=year, field_size=n, winner_idx=winner_idx,
                     p=p, y=y, q=p.copy(), race_attrs=race_attrs, horse_attrs=hattrs)


def _payload(races, seed=1, b=50):
    return build_payload(
        races, provenance={"base_model_version": "test"}, exclusions={"no_finished_label": 0},
        seed=seed, bootstrap_b=b,
    )


class TestGoldenUniform:
    """SC-002: a uniform predictor has excess 0 in EVERY bucket at ANY field size."""

    def test_race_excess_zero_across_variable_field_sizes(self):
        races = [
            _uniform_race("r1", "2024-01-06", 2024, 8, 0),
            _uniform_race("r2", "2024-01-07", 2024, 16, 3),
            _uniform_race("r3", "2024-01-13", 2024, 12, 11),
            _uniform_race("r4", "2024-01-14", 2024, 5, 2),
        ]
        pl = _payload(races)
        surface = next(a for a in pl["axes"] if a["axis_id"] == "surface")
        blk = surface["buckets"]["芝"]
        assert abs(blk["excess_nll_uniform"]["point"]) < 1e-9
        assert blk["n_races"] == 4

    def test_horse_excess_zero_for_uniform(self):
        races = [_uniform_race("r1", "2024-01-06", 2024, 8, 0),
                 _uniform_race("r2", "2024-01-07", 2024, 14, 5)]
        pl = _payload(races)
        sex = next(a for a in pl["axes"] if a["axis_id"] == "sex")
        assert abs(sex["buckets"]["牡"]["excess_logloss_vs_uniform"]["point"]) < 1e-9


class TestOutputContract:
    """SC-003: frozen order, forbidden keys absent, year-wise stability form."""

    def test_axes_in_frozen_library_order(self):
        pl = _payload([_uniform_race("r1", "2024-01-06", 2024, 8, 0)])
        assert [a["axis_id"] for a in pl["axes"]] == [m.axis_id for m in MASK_LIBRARY_V1]

    def test_forbidden_keys_rejected(self):
        with pytest.raises(ValueError, match="forbidden"):
            from horseracing_eval.segment_accuracy import _assert_no_forbidden_keys
            _assert_no_forbidden_keys({"axes": [{"rank": 1}]})

    def test_payload_has_contract_header_and_unadjusted_ci_label(self):
        pl = _payload([_uniform_race("r1", "2024-01-06", 2024, 8, 0)])
        ic = pl["instrument_contract"]
        assert ic["secondary"] is True and ic["can_adopt"] is False
        assert "NOT adjusted" in ic["ci_note"]
        surface = next(a for a in pl["axes"] if a["axis_id"] == "surface")
        assert "NOT adjusted" in surface["buckets"]["芝"]["excess_nll_uniform"]["ci_note"]

    def test_year_wise_stability_present(self):
        races = [_uniform_race("r1", "2023-06-01", 2023, 8, 0),
                 _uniform_race("r2", "2024-06-01", 2024, 8, 0)]
        pl = _payload(races)
        surface = next(a for a in pl["axes"] if a["axis_id"] == "surface")
        assert set(surface["buckets"]["芝"]["by_year"]) == {"2023", "2024"}


class TestGrainRules:
    def test_horse_axis_has_no_winner_nll(self):
        pl = _payload([_uniform_race("r1", "2024-01-06", 2024, 8, 0)])
        sex = next(a for a in pl["axes"] if a["axis_id"] == "sex")
        blk = sex["buckets"]["牡"]
        assert blk["grain"]["winner_nll"] == "NOT_AVAILABLE_AT_HORSE_GRAIN"
        assert "winner_nll" not in {k for k in blk if k != "grain"}

    def test_race_axis_calibration_uses_all_started_horses(self):
        pl = _payload([_uniform_race("r1", "2024-01-06", 2024, 8, 0)])
        surface = next(a for a in pl["axes"] if a["axis_id"] == "surface")
        blk = surface["buckets"]["芝"]
        assert blk["grain"]["calibration"] == "started_horse_within_selected_races"
        assert blk["calibration"]["n"] == 8


class TestReconciliation:
    """SC-006: Σ(bucket n_races) == scored races for a total race partition."""

    def test_sigma_n_races(self):
        races = [_uniform_race(f"r{i}", f"2024-01-{6+i:02d}", 2024, 8 + i, 0) for i in range(5)]
        pl = _payload(races)
        for axis in pl["axes"]:
            if axis["grain"] != "race":
                continue
            total = sum(b["n_races"] for b in axis["buckets"].values())
            assert total == pl["population"]["n_scored_races"], axis["axis_id"]

    def test_sigma_n_horses_for_horse_axes(self):
        races = [_uniform_race("r1", "2024-01-06", 2024, 8, 0),
                 _uniform_race("r2", "2024-01-07", 2024, 12, 1)]
        pl = _payload(races)
        for axis in pl["axes"]:
            if axis["grain"] != "horse":
                continue
            total = sum(b["n_horses"] for b in axis["buckets"].values())
            assert total == pl["population"]["n_scored_horses"], axis["axis_id"]


class TestMaskAssignment:
    def test_result_blind_missing_buckets(self):
        assert _assign_horse_axis("rotation_band", {"days_since_last": None}) == "missing"
        assert _assign_horse_axis("q_band", {"q": None}) == "q_missing"
        assert _assign_race_axis("dist_band", {"distance": None}) == "missing"

    def test_boundary_values(self):
        assert _assign_horse_axis("rotation_band", {"days_since_last": 7}) == "<=7"
        assert _assign_horse_axis("rotation_band", {"days_since_last": 8}) == "8-14"
        assert _assign_horse_axis("rotation_band", {"days_since_last": 70}) == "29-70"
        assert _assign_horse_axis("rotation_band", {"days_since_last": 71}) == ">70"
        assert _assign_horse_axis("weight_gain_band", {"weight_diff": 11}) == ">=+11"
        assert _assign_horse_axis("weight_gain_band", {"weight_diff": -11}) == "<=-11"
        assert _assign_race_axis("dist_band", {"distance": 1400}) == "<=1400"
        assert _assign_race_axis("dist_band", {"distance": 1401}) == "<=1800"

    def test_post_081_axes_labeled(self):
        for m in MASK_LIBRARY_V1:
            if m.family == "post_081_exploratory":
                assert m.origin == "post_081_exploratory"
                assert "independent confirmation" in m.definition["origin_note"]

    def test_library_hash_deterministic(self):
        assert mask_library_hash() == mask_library_hash()


class TestInputValidation:
    def test_p_must_sum_to_one(self):
        r = _uniform_race("r1", "2024-01-06", 2024, 8, 0)
        bad = RaceInput(race_id=r.race_id, day=r.day, year=r.year, field_size=r.field_size,
                        winner_idx=r.winner_idx, p=r.p * 2, y=r.y, q=r.q,
                        race_attrs=r.race_attrs, horse_attrs=r.horse_attrs)
        with pytest.raises(ValueError, match="sum to 1"):
            _payload([bad])

    def test_market_incomplete_race_still_scored_model_side(self):
        r = _uniform_race("r1", "2024-01-06", 2024, 8, 0)
        no_q = RaceInput(race_id=r.race_id, day=r.day, year=r.year, field_size=r.field_size,
                         winner_idx=r.winner_idx, p=r.p, y=r.y, q=None,
                         race_attrs=r.race_attrs, horse_attrs=r.horse_attrs)
        pl = _payload([no_q])
        surface = next(a for a in pl["axes"] if a["axis_id"] == "surface")
        blk = surface["buckets"]["芝"]
        assert blk["n_races"] == 1
        assert blk["market"]["n_market_complete_races"] == 0


def test_determinism_same_seed():
    races = [_uniform_race(f"r{i}", f"2024-01-{6+i:02d}", 2024, 10, i % 10) for i in range(6)]
    a = _payload(races, seed=7)
    b = _payload(races, seed=7)
    assert a == b
