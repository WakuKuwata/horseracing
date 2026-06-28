"""T014 (US2 unit): reliability_bins — counts, realized rate, Wilson CI, suppression (FR-006b/R5)."""

from __future__ import annotations

from horseracing_eval.harness import RELIABILITY_MIN_COUNT, _wilson, reliability_bins


def test_bins_count_and_realized_rate():
    # 100 horses in [0.0,0.1): 6 winners -> realized 0.06; 50 in [0.5,0.6): 25 winners -> 0.5
    probs = [0.05] * 100 + [0.55] * 50
    labels = [1] * 6 + [0] * 94 + [1] * 25 + [0] * 25
    bins = reliability_bins(probs, labels, min_count=30)
    by_lo = {b["pred_lo"]: b for b in bins}
    assert by_lo[0.0]["count"] == 100 and abs(by_lo[0.0]["realized_rate"] - 0.06) < 1e-9
    assert by_lo[0.5]["count"] == 50 and abs(by_lo[0.5]["realized_rate"] - 0.5) < 1e-9
    assert by_lo[0.0]["suppressed"] is False  # 100 >= 30


def test_low_count_bin_suppressed():
    probs = [0.95] * 5  # only 5 samples in [0.9,1.0)
    labels = [1, 0, 0, 0, 0]
    bins = reliability_bins(probs, labels, min_count=30)
    assert len(bins) == 1 and bins[0]["count"] == 5 and bins[0]["suppressed"] is True


def test_wilson_within_unit_interval_and_brackets_rate():
    lo, hi = _wilson(6, 100)
    assert 0.0 <= lo <= 0.06 <= hi <= 1.0
    # wider interval for smaller n
    lo2, hi2 = _wilson(1, 5)
    assert (hi2 - lo2) > (hi - lo)


def test_empty_bins_skipped():
    bins = reliability_bins([0.05, 0.05], [0, 1], min_count=1)
    assert all(b["count"] > 0 for b in bins)  # no empty bins emitted
    assert len(bins) == 1


def test_min_count_default_exposed():
    assert RELIABILITY_MIN_COUNT >= 1
