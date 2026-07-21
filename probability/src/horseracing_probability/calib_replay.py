"""Feature 078 US3 (T013): pre-activation OOF-replay parity check (research D8).

Before a generated manifest is ever activated, replay its SHIPPED params through the SAME production
pure apply functions (``apply_p_calibrator`` for two-gamma, ``harville_topk`` for stage discount)
over representative OOF win vectors and assert the structural safety properties:

- two-gamma: the calibrated win vector is a probability (Σ≈1), order-preserving (monotone), and —
  when identity (γ=1) — byte-identical to the engine-normalized input (identity byte-parity).
- stage discount: top2/top3 derived from the win vector sum to ~min(2,N)/min(3,N), are monotone in
  win, WIN is never touched (the stage layer only DERIVES top2/top3), and an identity λ reproduces
  plain Harville derivation byte-for-byte.

This must NOT re-score production persisted predictions (that would be non-OOS in the fit window):
the input is the OOF win vectors the manifest was fit on / the fixture vectors a caller supplies.
"""

from __future__ import annotations

from horseracing_eval.baselines import harville_topk

from .model_calibration import apply_p_calibrator


class ReplayParityError(AssertionError):
    """A generated manifest's shipped params are not apply-safe (fail-closed before activation)."""


def _normalized_list(p: dict[str, float]) -> list[float]:
    ids = sorted(p)
    total = sum(p[h] for h in ids)
    if total <= 0.0:
        raise ReplayParityError("win vector does not sum to a positive value")
    return [p[h] / total for h in ids]


def _is_monotone(order_key: list[float], values: list[float], *, tol: float = 1e-9) -> bool:
    """values must be non-decreasing wherever order_key is non-decreasing (ties allowed)."""
    pairs = sorted(zip(order_key, values, strict=True))
    prev = -1.0
    for _k, v in pairs:
        if v < prev - tol:
            return False
        prev = v
    return True


def replay_parity_report(
    *, two_gamma, stage_discount, win_vectors: list[dict[str, float]], tol: float = 1e-9,
) -> dict:
    """Assert D8 apply-safety over ``win_vectors``. Raises :class:`ReplayParityError` on any
    violation; returns a small summary on success."""
    tg_identity = float(two_gamma.params.get("gamma_lo", 1.0)) == 1.0 and \
        float(two_gamma.params.get("gamma_hi", 1.0)) == 1.0
    st_identity = stage_discount.lambda2 == 1.0 and stage_discount.lambda3 == 1.0
    n_checked = 0
    for p in win_vectors:
        norm = _normalized_list(p)
        n = len(norm)
        # --- two-gamma: probability + monotone + identity byte-parity ---
        cal = apply_p_calibrator(p, two_gamma)
        if abs(sum(cal.values()) - 1.0) > 1e-6:
            raise ReplayParityError(f"calibrated win does not sum to 1: {sum(cal.values())}")
        cal_list = [cal[h] for h in sorted(p)]
        if not _is_monotone(norm, cal_list, tol=tol):
            raise ReplayParityError("two-gamma calibration is not monotone in win")
        if tg_identity and any(abs(cal_list[i] - norm[i]) > tol for i in range(n)):
            raise ReplayParityError("identity two-gamma is not byte-parity with normalized win")

        # --- stage discount: Σ, monotone, WIN untouched, identity byte-parity ---
        base2, base3 = harville_topk(norm)  # plain Harville (λ=1)
        c2, c3 = harville_topk(norm, lambda2=stage_discount.lambda2, lambda3=stage_discount.lambda3)
        if abs(sum(c2) - min(2, n)) > 1e-6 or abs(sum(c3) - min(3, n)) > 1e-6:
            raise ReplayParityError(f"top2/top3 do not sum to min(k,N): {sum(c2)}, {sum(c3)}")
        if not _is_monotone(norm, c2, tol=tol) or not _is_monotone(norm, c3, tol=tol):
            raise ReplayParityError("stage-discounted top2/top3 are not monotone in win")
        # (WIN is never touched: the stage layer takes ``norm`` and only DERIVES top2/top3.)
        if st_identity and (c2 != base2 or c3 != base3):
            raise ReplayParityError("identity stage discount not byte-parity with plain Harville")
        n_checked += 1

    return {
        "n_win_vectors": n_checked,
        "two_gamma_identity": tg_identity,
        "stage_identity": st_identity,
        "passed": True,
    }
