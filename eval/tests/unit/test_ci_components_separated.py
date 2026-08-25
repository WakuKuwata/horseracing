"""sampling CI と seed 成分が分離して報告され続けることを固定する(100 / T019a・FR-032)。

現状の挙動だが回帰テストが無い。**この分離があったからこそ 56%:44% という内訳が読め、
feature 100 が起きた**。リファクタで片方に畳まれると、次に同じ問いを立てたとき答えられない。
"""

from __future__ import annotations

import math

import pytest

from horseracing_eval.paired import paired_eval

from .test_evidence_recompute_parity import CFG, _FakeFactory, _races


def _rep(cfg=None):
    return paired_eval(_FakeFactory(0.62, "cand"), _FakeFactory(0.45, "act"), _races(),
                       gate_config=cfg or CFG, first_valid_year=2020)


def test_both_intervals_are_reported() -> None:
    rep = _rep()
    assert rep.bootstrap_ci and rep.total_ci
    assert rep.bootstrap_ci["point"] == rep.total_ci["point"]  # 点推定は成分で動かない


def test_total_is_wider_than_sampling_when_a_seed_term_is_declared() -> None:
    rep = _rep()
    assert rep.total_ci["ci_low"] < rep.bootstrap_ci["ci_low"]
    assert rep.total_ci["ci_high"] > rep.bootstrap_ci["ci_high"]


def test_seed_noise_block_records_what_was_added_and_where_it_came_from() -> None:
    """後から「どの成分がどれだけ効いたか」を読めること。"""
    rep = _rep()
    sn = rep.seed_noise
    assert sn["sd_fold"] == CFG["seed_noise"]["sd_fold"]
    assert sn["k_seeds"] == 1 and sn["n_folds"] >= 1
    assert sn["source"] == CFG["seed_noise"]["source"]
    assert sn["applied"] is True


def test_variance_split_is_recoverable_from_the_report() -> None:
    """**この feature の出発点になった計算**が報告から再現できること。

    097 の実 verdict から sampling 56% / seed 44% を逆算できたのは、両区間が別々に載っていた
    からである。同じ算術がここで通ることを pin する。
    """
    rep = _rep()
    point = rep.bootstrap_ci["point"]
    sample_arm = rep.bootstrap_ci["ci_high"] - point
    total_arm = rep.total_ci["ci_high"] - point
    pad = math.sqrt(max(total_arm**2 - sample_arm**2, 0.0))
    assert pad > 0
    sampling_share = sample_arm**2 / total_arm**2
    assert 0.0 < sampling_share < 1.0
    # pad から seed sd を逆算すると宣言値と整合する。z は**その run の alpha**から取る
    # (gate-config の alpha を無視して 0.05 決め打ちにすると、ここが静かにずれる)。
    import statistics

    alpha = rep.evidence.bootstrap["alpha"]
    z = statistics.NormalDist().inv_cdf(1.0 - alpha / 2.0)
    recovered = pad / z * math.sqrt(rep.seed_noise["n_folds"])
    assert recovered == pytest.approx(CFG["seed_noise"]["sd_fold"], rel=1e-6)


def test_identity_when_no_seed_term_is_declared() -> None:
    """v3 以前の凍結 config(``seed_noise`` 無し)では両区間が一致する。"""
    cfg = {k: v for k, v in CFG.items() if k != "seed_noise"}
    rep = _rep(cfg)
    assert rep.total_ci == rep.bootstrap_ci
    assert rep.seed_noise["applied"] is False
