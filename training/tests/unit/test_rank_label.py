"""Feature 042 T004: finish_rank is a LABEL, never a model feature (leak boundary)."""

from __future__ import annotations

from horseracing_features.registry import model_input_features

from horseracing_training.dataset import RANK_LABEL, WIN_LABEL


def test_rank_label_not_a_model_feature():
    feats = model_input_features()
    assert RANK_LABEL not in feats
    assert WIN_LABEL not in feats  # sanity: labels never leak into inputs


def test_rank_label_name_has_no_market_tokens():
    low = RANK_LABEL.lower()
    for tok in ("odds", "payout", "dividend", "popularity"):
        assert tok not in low
