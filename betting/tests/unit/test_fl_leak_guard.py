"""T015 (013): LEAK GUARD — odds / q / q' are NEVER win-model features (constitution II, SC-002).

FL bias correction (013) transforms the MARKET side (q→q'); q' must stay out of the win model's
feature set. The features registry is the single source of truth for model inputs (odds/popularity
are deliberately unregistered). This test fails fast if any market/odds/q/q' column ever leaks in.
"""

from __future__ import annotations

from horseracing_features.registry import REGISTRY, model_input_features

# substrings that would indicate a market-odds / vote-share / FL-corrected leak into features
_FORBIDDEN = ("odds", "q_prime", "qprime", "market", "vote", "implied", "popularity", "fl_")


def test_no_market_or_odds_column_is_a_model_feature():
    feats = model_input_features()
    for name in feats:
        low = name.lower()
        assert not any(tok in low for tok in _FORBIDDEN), f"leak: {name} looks market-derived"


def test_registry_has_no_odds_sourced_model_feature():
    # no registered model feature may be sourced from odds or post-odds timing
    for name, meta in REGISTRY.items():
        assert "odds" not in meta.source.lower()
        assert meta.timing.value != "post_odds", f"{name} uses post_odds timing as a model feature"


def test_corrected_probs_type_is_not_importable_as_a_feature():
    # q' lives in probability (market side), never imported by the features model-input path
    import horseracing_features.registry as reg
    assert "CorrectedMarketProbs" not in dir(reg)
    assert "q_prime" not in REGISTRY and "q" not in REGISTRY
