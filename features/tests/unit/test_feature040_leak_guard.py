"""Feature 040 T021: display-derived explanation/importance/divergence are NEVER model features
(憲法 II leak boundary, SC-007) + migration head is 0009 (054 diagnostic_runs) + features adds no ORM table.
"""

from __future__ import annotations

from pathlib import Path

from horseracing_features.registry import model_input_features

_ROOT = Path(__file__).resolve().parents[3]

# tokens that would signal a display-derived value leaking back into model inputs.
# "split_gain" (not bare "gain"): 041 の asof_late_gain_* は過去走の位置ゲイン=ドメイン用語で
# 040 の表示由来値ではない。importance/contribution 等で 040 リーク面は引き続き担保。
_FORBIDDEN = (
    "explanation", "contribution", "base_value", "divergence",
    "importance", "split_gain", "pred_contrib",
)


def test_explanation_tokens_not_in_model_features():
    for name in model_input_features():
        low = name.lower()
        for tok in _FORBIDDEN:
            assert tok not in low, f"{name} looks like a 040 display-derived value"


def test_migration_head_is_0009():
    versions = sorted(p.name for p in (_ROOT / "db" / "migrations" / "versions").glob("0*.py"))
    assert versions[-1].startswith("0009_"), versions[-1]


def test_features_adds_no_orm_table():
    # 040's only schema change is in db/ (race_predictions.explanation); features adds no table
    for f in (_ROOT / "features" / "src" / "horseracing_features").rglob("*.py"):
        assert "__tablename__" not in f.read_text(encoding="utf-8"), f
