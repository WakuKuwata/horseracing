"""T023 / FR-009 (constitution II): scraped odds & results NEVER become model input features.

The scrapers write race_horses.odds/popularity and race_results.finish_*; none of these may appear
in the model feature set. This is a structural guard on the feature registry.
"""

from __future__ import annotations

from horseracing_features.registry import model_input_features

# fields the netkeiba scrapers populate that must stay out of the model (leak boundary)
_SCRAPED_NON_FEATURES = {"odds", "popularity", "finish_order", "finish_time",
                         "finish_time_diff", "result_status"}


def test_scraped_odds_and_results_are_not_model_features():
    features = set(model_input_features())
    leaked = _SCRAPED_NON_FEATURES & features
    assert not leaked, f"leak boundary violated — scraped fields in model input: {leaked}"
