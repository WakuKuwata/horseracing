"""Feature 106 (T005): the user's PURCHASE RECORDS never enter the feature pipeline (憲法 II).

purchase_records stores the user's actual betting actions. Letting it reach any feature loader
would create a brand-new leak surface — the user's own money flowing into the model that then
advises the user. The guard is structural: no source file in the features package may reference
the table or its ORM class, and the loader's SQL surface is scanned directly.
"""

from __future__ import annotations

from pathlib import Path

from horseracing_features.registry import REGISTRY, materialized_columns, model_input_features

_ROOT = Path(__file__).resolve().parents[3]
_FEATURES_SRC = _ROOT / "features" / "src" / "horseracing_features"

_FORBIDDEN = ("purchase_records", "PurchaseRecord", "purchase_record")


def test_no_features_source_references_purchase_records():
    offenders = []
    for path in sorted(_FEATURES_SRC.rglob("*.py")):
        body = path.read_text(encoding="utf-8")
        for token in _FORBIDDEN:
            if token in body:
                offenders.append(f"{path.name}: {token}")
    assert not offenders, f"purchase records leaked into the feature pipeline: {offenders}"


def test_no_purchase_named_feature_columns():
    names = {f.lower() for f in REGISTRY}
    names |= {c.lower() for c in materialized_columns()}
    names |= {c.lower() for c in model_input_features()}
    hits = [n for n in names if "purchase" in n]
    assert not hits, f"purchase-derived columns registered as features: {hits}"


def test_purchase_records_migration_present_and_head_is_0019():
    versions = sorted(
        p.stem for p in (_ROOT / "db" / "migrations" / "versions").glob("0*.py")
    )
    assert "0017_purchase_records" in versions
    assert versions[-1] == "0019_attention_picks", versions[-1]
