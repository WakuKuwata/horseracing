"""1200m のテン3F 導出 — 唯一成立する距離でだけ埋め、他距離では推測しない。

`race_results.first_3f` は JRA-VAN 生 CSV 由来で、供給停止により 2026 年は 0.0% になった。
netkeiba は馬ごとの上がり3F しか出さず、テン3F は出さない(ラップページが持つのはレース単位の
先頭ペースであって馬ごとの値ではないので、ラップを取っても届かない)。

唯一の経路が 1200m の恒等式で、実 DB の既存値 **187,833 行に対し最大誤差 0.0000 秒**、他距離
(1000/1400/1600/2000m)では 3〜50 秒ずれて全く成立しない。JRA 自身がその定義で出している。
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from horseracing_scrape.upsert import DERIVABLE_FIRST3F_DISTANCE, _derive_first_3f


def _td(sec: float) -> datetime.timedelta:
    return datetime.timedelta(seconds=sec)


def test_derives_at_1200m():
    assert _derive_first_3f(1200, _td(70.5), Decimal("35.2")) == Decimal("35.3")


@pytest.mark.parametrize("distance", [1000, 1150, 1400, 1600, 2000, 3000])
def test_other_distances_are_never_derived(distance):
    """他距離では中間区間があるので恒等式が成立しない。埋めるより空のままが正しい。"""
    assert _derive_first_3f(distance, _td(70.5), Decimal("35.2")) is None


@pytest.mark.parametrize("finish,last3f", [
    (None, Decimal("35.2")),      # 走破時計が無い
    (_td(70.5), None),            # 上がりが無い
    (None, None),
])
def test_missing_inputs_yield_none(finish, last3f):
    assert _derive_first_3f(1200, finish, last3f) is None


def test_unknown_distance_yields_none():
    assert _derive_first_3f(None, _td(70.5), Decimal("35.2")) is None


@pytest.mark.parametrize("finish_sec,last3f", [(35.2, "35.2"), (30.0, "35.2")])
def test_nonpositive_result_is_refused(finish_sec, last3f):
    """走破時計 <= 上がり は入力が壊れている合図。0 や負のテン3F を作らない。"""
    assert _derive_first_3f(1200, _td(finish_sec), Decimal(last3f)) is None


def test_derivation_is_exact_not_float_rounded():
    """Decimal で計算する。float だと 0.1 秒単位の値で末尾誤差が出て、実測との
    バイト一致(最大誤差 0.0000 秒)が壊れる。"""
    v = _derive_first_3f(1200, _td(69.7), Decimal("34.9"))
    assert v == Decimal("34.8")
    assert str(v) == "34.8"


def test_constant_matches_the_only_verified_distance():
    assert DERIVABLE_FIRST3F_DISTANCE == 1200
