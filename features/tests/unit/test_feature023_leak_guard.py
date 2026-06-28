"""T017/T018 (023): leak boundary + no schema change (憲法 II/VI, FR-015/016, SC-006)."""

from __future__ import annotations

from pathlib import Path

from horseracing_features.registry import (
    FEATURE_GROUPS,
    FEATURE_VERSION,
    REGISTRY,
    model_input_features,
)

_ROOT = Path(__file__).resolve().parents[3]
_PACE_COLS = [c for c, g in FEATURE_GROUPS.items() if g in ("pace_time", "position_style")]


def test_no_odds_token_in_model_features():
    for name in model_input_features():
        low = name.lower()
        assert "odds" not in low and "payout" not in low and "dividend" not in low, name


def test_pace_features_registered_and_model_inputs():
    # 023 features are PRE_ENTRY (computed from PAST results, available before the race) model inputs
    assert _PACE_COLS, "023 groups must be registered"
    inputs = set(model_input_features())
    for c in _PACE_COLS:
        assert c in REGISTRY and c in inputs, c
    assert FEATURE_VERSION == "features-007"  # bumped by Feature 026 (pedigree)


def test_no_schema_change_or_orm_table():
    versions = sorted(p.name for p in (_ROOT / "db" / "migrations" / "versions").glob("0*.py"))
    assert versions[-1].startswith("0006_"), versions[-1]  # head unchanged
    for f in (_ROOT / "features" / "src" / "horseracing_features").rglob("*.py"):
        assert "__tablename__" not in f.read_text(encoding="utf-8"), f
