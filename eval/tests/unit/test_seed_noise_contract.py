"""``seed_noise_sd`` の ``k_seeds`` が何をモデル化しているかを固定する(100 / T008).

``k_seeds`` は「**比較全体を k 回やり直して平均する**」を表す。独立な k 回なので ``sqrt(k)``
で縮む。一方、**1 つの出荷モデルの中で k 個の booster を平均する**(feature 100 US3)場合、
member は学習データを共有するので独立ではない。相関 rho に対して

    Var(mean) = sigma^2 * (rho + (1 - rho) / k)

であり、``rho * sigma^2`` は k をいくら増やしても残る。アンサンブルの k をこの関数に渡すと
**達成していない縮小を報告する**ことになり、しかも区間が狭くなる方向なので気づけない。

したがってアンサンブルの縮小率は、独立な k-seed バンドル同士の比較から測るか、主張しない
(FR-026b)。
"""

from __future__ import annotations

import math

import pytest

from horseracing_eval.bootstrap import BootstrapCI, inflate_for_seed_noise, seed_noise_sd

SD_FOLD = 0.001816  # 実測値(scripts/seed_variance_probe.py --mode rerun, 6 seeds)


def test_fold_averaging_shrinks_by_sqrt_n_folds() -> None:
    assert seed_noise_sd(SD_FOLD, n_folds=1) == pytest.approx(SD_FOLD)
    assert seed_noise_sd(SD_FOLD, n_folds=4) == pytest.approx(SD_FOLD / 2.0)


def test_k_seeds_shrinks_by_sqrt_k_because_reruns_are_independent() -> None:
    """比較全体のやり直しは独立なので sqrt(k) で縮む。これがこの引数の正しい用法。"""
    base = seed_noise_sd(SD_FOLD, n_folds=3, k_seeds=1)
    assert seed_noise_sd(SD_FOLD, n_folds=3, k_seeds=4) == pytest.approx(base / 2.0)


def test_degenerate_inputs_return_zero() -> None:
    for kw in ({"n_folds": 0}, {"n_folds": 3, "k_seeds": 0}):
        assert seed_noise_sd(SD_FOLD, **kw) == 0.0
    assert seed_noise_sd(0.0, n_folds=3) == 0.0
    assert seed_noise_sd(-1.0, n_folds=3) == 0.0


def _correlated_average_sd(sd: float, *, k: int, rho: float) -> float:
    """相関 rho を持つ k member の平均の SD。"""
    return sd * math.sqrt(rho + (1.0 - rho) / k)


@pytest.mark.parametrize("rho", [0.3, 0.5, 0.8])
def test_sqrt_k_understates_the_sd_of_a_correlated_ensemble(rho: float) -> None:
    """**この関数をアンサンブルに流用すると縮小を過大評価する**ことを数値で示す。

    ここが本契約の中身。``sd/sqrt(k)`` は相関がある平均の SD を必ず下回るので、区間が
    実際より狭くなる = 偽陽性が増える方向に倒れる。
    """
    k = 5
    independent = seed_noise_sd(SD_FOLD, n_folds=1, k_seeds=k)
    correlated = _correlated_average_sd(SD_FOLD, k=k, rho=rho)
    assert independent < correlated
    # 相関が強いほど乖離も大きい
    assert correlated / independent > 1.0


def test_correlated_component_survives_unbounded_k() -> None:
    """k を無限に増やしても ``rho * sigma^2`` は消えない(R16 の核心)。"""
    rho = 0.5
    huge = _correlated_average_sd(SD_FOLD, k=10_000, rho=rho)
    floor = SD_FOLD * math.sqrt(rho)
    assert huge == pytest.approx(floor, rel=1e-3)
    assert seed_noise_sd(SD_FOLD, n_folds=1, k_seeds=10_000) < floor / 10


def test_inflation_is_identity_without_a_declared_seed_term() -> None:
    """v3 以前の凍結 config(``seed_noise`` 無し)が壊れないこと。"""
    ci = BootstrapCI(point=-0.002, ci_low=-0.004, ci_high=0.0005, b=2000, seed=1,
                     block="race_day", n_days=300, no_decision=False)
    assert inflate_for_seed_noise(ci, sd_fold=0.0, n_folds=3) == ci


def test_inflation_widens_both_arms() -> None:
    """成分は分散加算で、腕ごとに広がる(percentile の非対称を潰さない)。"""
    ci = BootstrapCI(point=-0.002409, ci_low=-0.004612, ci_high=-0.000103, b=2000, seed=1,
                     block="race_day", n_days=323, no_decision=False)
    wide = inflate_for_seed_noise(ci, sd_fold=SD_FOLD, n_folds=3)
    assert wide.point == ci.point
    assert wide.ci_low < ci.ci_low
    assert wide.ci_high > ci.ci_high
    # 097 の実測: 合成後に CI 上限がゼロを跨ぐ(この feature の出発点になった数値)
    assert wide.ci_high > 0
