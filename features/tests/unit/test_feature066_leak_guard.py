"""T008 (066): dispersion/divergence display axes never re-enter model features (憲法 II).

Feature 066 surfaces race-level dispersion (entropy/favourite/top-3 from market q) and p-vs-q
divergence for DISPLAY only. These are read-only decision-support outputs — market q is a leak and
the aggregates are functions of q/served-p, so none may become a model input or a materialized
feature column. Also confirms 066 adds no schema change (migration head unchanged).
"""

from __future__ import annotations

from pathlib import Path

from horseracing_features.registry import materialized_columns, model_input_features

_ROOT = Path(__file__).resolve().parents[3]
# Specific 066 display-axis names. NOT "band_" — that legitimately appears in as-of ability
# features (dist_band_win_rate); the leak concern is the dispersion/divergence display axes only.
_FORBIDDEN_066 = (
    "normalized_entropy", "favorite_win_prob", "top3_cumulative", "dispersion",
    "divergence", "rank_agreement", "underrated_longshot",
)


def test_dispersion_axes_not_in_model_features():
    feats = {f.lower() for f in model_input_features()}
    for token in _FORBIDDEN_066:
        assert not any(token in f for f in feats), f"066 display axis '{token}' leaked into features"


def test_dispersion_axes_not_in_materialized_columns():
    cols = {c.lower() for c in materialized_columns()}
    for token in _FORBIDDEN_066:
        assert not any(token in c for c in cols), f"066 display axis '{token}' in materialized cols"


def test_066_adds_no_schema_change():
    versions = sorted(p.name for p in (_ROOT / "db" / "migrations" / "versions").glob("0*.py"))
    assert versions[-1].startswith("0011_"), versions[-1]
