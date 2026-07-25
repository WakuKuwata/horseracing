"""codex C#1 regression: the folklore-probe OOF must fail closed when from_date would truncate
the training history (from_date.year >= first_valid_year -> empty first-fold outer-train and a
silently weaker OOF for every later fold)."""

from __future__ import annotations

import datetime

import pytest

from horseracing_training.folklore_probe import build_oof_cache


def test_from_date_coinciding_with_first_valid_year_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="strictly precede"):
        build_oof_cache(
            session=None,  # guard fires before any DB access
            spec="pl_topk:isotonic:0.3",
            make_factory=lambda s: None,
            from_date=datetime.date(2019, 1, 1),
            to_date=datetime.date(2026, 7, 12),
            first_valid_year=2019,
            num_threads=1,
            cache_path=tmp_path / "cache.parquet",
        )


def test_from_date_after_first_valid_year_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="strictly precede"):
        build_oof_cache(
            session=None,
            spec="pl_topk:isotonic:0.3",
            make_factory=lambda s: None,
            from_date=datetime.date(2020, 6, 1),
            to_date=datetime.date(2026, 7, 12),
            first_valid_year=2019,
            num_threads=1,
            cache_path=tmp_path / "cache.parquet",
        )
