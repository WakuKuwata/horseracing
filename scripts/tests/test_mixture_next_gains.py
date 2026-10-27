"""134: pure mixture algebra and scoring helpers (no cache, no DB)."""

import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mixture_next_gains as g  # noqa: E402


def _race(n=5, seed=0):
    rng = np.random.default_rng(seed)
    logp = np.log(rng.dirichlet(np.ones(n), size=6).T)  # n x 6, each column sums to 1
    z = rng.normal(0, 0.1, size=(n, 6))
    return logp, z


def test_corrected_matches_mixture_correction_algebra():
    logp, z = _race()
    q = g.corrected(logp, z, 1.0)
    assert np.allclose(q.sum(axis=0), 1.0)
    expected = np.exp(logp) * np.exp(z)
    expected /= expected.sum(axis=0, keepdims=True)
    assert np.allclose(q, expected)
    assert np.allclose(
        g.corrected(logp, z, 0.0), np.exp(logp)
    )  # scale 0 = no correction


def test_mix_win_equal_weights_is_arithmetic_mean_and_gamma_is_identity_at_one():
    logp, z = _race()
    q = g.corrected(logp, z, 1.0)
    assert np.allclose(g.mix_win(q), q.mean(axis=1))
    assert np.allclose(g.mix_win(q, gamma=1.0), g.mix_win(q))
    sharper = g.mix_win(q, gamma=1.5)
    assert np.allclose(sharper.sum(), 1.0)
    # ranking preserved under a positive power
    assert np.argsort(sharper).tolist() == np.argsort(g.mix_win(q)).tolist()
    # branch weight 1.0 = joint side only; 0.0 = anchor side only
    assert np.allclose(g.mix_win(q, w_joint=1.0), q[:, :3].mean(axis=1))
    assert np.allclose(g.mix_win(q, w_joint=0.0), q[:, 3:].mean(axis=1))
    dropped = g.mix_win(q, drop=0)
    assert np.allclose(
        dropped,
        np.concatenate([q[:, 1:3] * 0.25, q[:, 3:] / 6], axis=1).sum(axis=1) / 1.0,
    )


def test_geometric_mean_normalises_and_differs_from_arithmetic():
    logp, z = _race(seed=3)
    q = g.corrected(logp, z, 1.0)
    geo = g.mix_win(q, geometric=True)
    assert np.isclose(geo.sum(), 1.0) and not np.allclose(geo, q.mean(axis=1))


def test_fit_gamma_recovers_a_known_exponent():
    rng = np.random.default_rng(1)
    rows = []
    for _ in range(400):
        p = rng.dirichlet(np.ones(10))
        sharp = p**1.3
        sharp /= sharp.sum()
        rows.append((p, int(rng.choice(10, p=sharp))))
    gamma = g.fit_gamma(rows)
    assert 1.15 < gamma < 1.45  # truth 1.3 up to sampling noise


def test_finishers_require_unique_positions():
    assert g._finishers(0, [1, 1, 0, 0], [1, 1, 1, 0]) == (0, 1, 2)
    assert g._finishers(0, [1, 1, 1, 0], [1, 1, 1, 0])[1] is None  # dead heat at 2nd
    assert g._finishers(0, [1, 1, 0, 0], [1, 1, 1, 1])[2] is None  # dead heat at 3rd


def test_summarize_diffs_handles_one_day_and_sign():
    one = g.summarize_diffs([("2026-01-01", -0.1), ("2026-01-01", -0.3)])
    assert one["ci95"] is None and math.isclose(one["point"], -0.2)
    two = g.summarize_diffs([("2026-01-01", -0.1), ("2026-01-02", -0.3)])
    assert (
        two["ci95"][0] <= two["point"] <= two["ci95"][1]
        and two["share_improved"] == 1.0
    )


def test_confirmation_refuses_without_preregistration(tmp_path):
    with pytest.raises(ValueError):
        g.hypotheses(
            tmp_path / "missing.pkl",
            tmp_path / "out.json",
            stage="confirmation",
            prereg=None,
        )
