"""T022 (021): display-derived values never re-enter model features (憲法 II / FR-019 / SC-006).

Feature 021 surfaces market q (market_win_prob), reliability/calibration, and data_backing for
display only. These are read-only decision-support outputs — they must NEVER become model inputs
(market q is a leak; reliability is result-derived). Also confirms 021 adds no schema/ORM table.
"""

from __future__ import annotations

from pathlib import Path

from horseracing_features.registry import model_input_features

_ROOT = Path(__file__).resolve().parents[3]
_FORBIDDEN_021 = (
    "market_win_prob", "market_q", "vote_share", "data_backing",
    "reliability", "calibration", "p_minus_q", "pseudo_roi",
)


def test_display_derived_values_not_in_model_features():
    feats = {f.lower() for f in model_input_features()}
    for token in _FORBIDDEN_021:
        assert not any(token in f for f in feats), f"display-derived '{token}' leaked into features"


def test_021_adds_no_schema_change():
    versions = sorted(p.name for p in (_ROOT / "db" / "migrations" / "versions").glob("0*.py"))
    assert versions[-1].startswith("0010_"), versions[-1]
