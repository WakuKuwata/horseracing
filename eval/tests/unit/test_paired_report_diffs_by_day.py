"""Feature 097 (T003/T020): PairedReport exposes the per-day paired diffs it was built from.

Additive: every pre-097 key survives, and the exposed diffs reproduce ``bootstrap_ci`` when fed
back through the same race-day cluster bootstrap — which is exactly what a multi-window driver
does after pooling.
"""

from __future__ import annotations

from dataclasses import asdict, fields

from horseracing_eval.bootstrap import race_day_cluster_bootstrap_ci_v1
from horseracing_eval.paired import PairedReport
from horseracing_eval.provenance import frame_projection_hash


def _minimal_report(**kw) -> PairedReport:
    base = dict(
        candidate_recipe_meta={}, active_recipe_meta={}, candidate_recipe_hash="c",
        active_recipe_hash="a", race_id_set_hash="r", n_races=0, n_eligible=0,
        uniform_baseline_winner_nll=0.0, periods={}, bootstrap_ci={}, gate=None,
    )
    base.update(kw)
    return PairedReport(**base)


def test_diffs_by_day_is_an_additive_field_with_empty_default():
    r = _minimal_report()
    assert r.diffs_by_day == {}
    d = r.to_dict()
    assert "diffs_by_day" in d
    # every dataclass field is in to_dict (asdict) — nothing pre-097 was renamed or dropped
    assert set(d) >= {f.name for f in fields(PairedReport)}


def test_exposed_diffs_reproduce_the_report_interval():
    """Single window: bootstrapping the exposed diffs == the report's own CI (same seed/b)."""
    diffs = {"2020-01-05": [-0.02, 0.01, -0.03], "2020-01-12": [0.0, -0.01],
             "2020-02-02": [-0.05, 0.02, 0.01, -0.02]}
    ci = race_day_cluster_bootstrap_ci_v1(diffs, b=500, seed=7)
    r = _minimal_report(bootstrap_ci=asdict(ci), diffs_by_day=diffs)
    again = race_day_cluster_bootstrap_ci_v1(r.diffs_by_day, b=500, seed=7)
    assert asdict(again) == r.bootstrap_ci


def test_projection_hash_is_row_order_independent_and_value_sensitive():
    cols = ["race_id", "horse_id", "distance", "first_3f"]
    a = [("1", "h", 1200, 35.2), ("2", "h", 1600, None), ("3", "g", 1200, 34.9)]
    b = [a[2], a[0], a[1]]
    assert frame_projection_hash(a, cols) == frame_projection_hash(b, cols)
    c = [("1", "h", 1200, 35.3), a[1], a[2]]
    assert frame_projection_hash(a, cols) != frame_projection_hash(c, cols)
    # NULL-ing a value is a change too (the whole point: a mask must be visible in the hash)
    d = [a[0], a[1], ("3", "g", 1200, None)]
    assert frame_projection_hash(a, cols) != frame_projection_hash(d, cols)
    # Decimal (DB NUMERIC) and float spell the same
    from decimal import Decimal
    e = [("1", "h", 1200, Decimal("35.2")), a[1], a[2]]
    assert frame_projection_hash(a, cols) == frame_projection_hash(e, cols)
