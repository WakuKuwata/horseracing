"""US3 (SC-004): adoption gate — all-label LogLoss + win ECE, win strictly better."""

from __future__ import annotations

from horseracing_training.adoption import AdoptionGate, evaluate_gate


def _summary(win_ll, top2_ll, top3_ll, win_ece):
    return {
        "eval": {
            "overall": {
                "win": {"log_loss": win_ll, "ece": win_ece},
                "top2": {"log_loss": top2_ll, "ece": 0.0},
                "top3": {"log_loss": top3_ll, "ece": 0.0},
            }
        }
    }


def test_adopts_when_strictly_better_and_calibrated():
    model = _summary(0.30, 0.50, 0.60, 0.03)
    base = _summary(0.40, 0.55, 0.65, 0.0)
    d = evaluate_gate(model, base, AdoptionGate(ece_threshold=0.05))
    assert d.adopted is True
    assert all(r["pass"] for r in d.reasons.values())


def test_rejects_when_win_only_equal_not_strict():
    model = _summary(0.40, 0.50, 0.60, 0.01)
    base = _summary(0.40, 0.55, 0.65, 0.0)
    d = evaluate_gate(model, base, AdoptionGate(ece_threshold=0.05))
    assert d.adopted is False
    assert d.reasons["win_logloss_better"]["pass"] is False


def test_rejects_on_top_label_regression():
    model = _summary(0.30, 0.60, 0.60, 0.01)  # top2 worse than baseline
    base = _summary(0.40, 0.55, 0.65, 0.0)
    d = evaluate_gate(model, base, AdoptionGate(ece_threshold=0.05))
    assert d.adopted is False
    assert d.reasons["top2_logloss_no_regression"]["pass"] is False


def test_rejects_when_ece_exceeds_threshold():
    model = _summary(0.30, 0.50, 0.60, 0.20)  # great logloss but poorly calibrated
    base = _summary(0.40, 0.55, 0.65, 0.0)
    d = evaluate_gate(model, base, AdoptionGate(ece_threshold=0.05))
    assert d.adopted is False
    assert d.reasons["win_ece_within_threshold"]["pass"] is False
