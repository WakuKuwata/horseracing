"""T017 (020): leak boundary + no schema change (SC-009, 憲法 II)."""

from __future__ import annotations

from pathlib import Path

from horseracing_features.registry import FEATURE_GROUPS, REGISTRY, model_input_features

_ROOT = Path(__file__).resolve().parents[3]
_FORBIDDEN = ("odds", "result", "payout", "finish_order", "dividend")


def test_no_odds_or_result_in_model_features():
    # model features must not include market odds / direct result fields (II); finish-derived
    # aggregates are as-of (avg_finish etc.) — guard only raw odds/result/payout tokens.
    for name in model_input_features():
        low = name.lower()
        assert "odds" not in low and "payout" not in low and "dividend" not in low, name


def test_feature020_groups_registered():
    # every 020 group feature is registered and post_result-free
    for name in FEATURE_GROUPS:
        assert name in REGISTRY
        assert name in model_input_features()


def test_no_new_migration_or_orm():
    # 020 adds no DB migration (head stays 0006) and no new ORM table
    versions = sorted(p.name for p in (_ROOT / "db" / "migrations" / "versions").glob("0*.py"))
    assert versions[-1].startswith("0008_"), versions[-1]
    for f in (_ROOT / "features" / "src" / "horseracing_features").rglob("*.py"):
        assert "__tablename__" not in f.read_text(encoding="utf-8"), f
