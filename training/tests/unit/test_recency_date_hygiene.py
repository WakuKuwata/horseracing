"""Feature 101: 日付と重み仕様を fail-closed に検証する契約テスト。"""

from __future__ import annotations

import datetime

import pytest

from horseracing_training.recency import (
    RecencyContractError,
    RecencyWeightSpec,
    build_recency_weights,
)

_CUTOFF = datetime.date(2026, 4, 1)


def test_race_date_after_cutoff_is_rejected() -> None:
    """実行日ではなく cutoff より未来のレース行を拒否し、未来情報の混入を防ぐ。"""
    with pytest.raises(RecencyContractError):
        build_recency_weights(
            ["A"],
            [_CUTOFF + datetime.timedelta(days=1)],
            cutoff=_CUTOFF,
            spec=RecencyWeightSpec(half_life_days=365),
        )


def test_none_race_date_is_rejected() -> None:
    """欠損日を中立値などへ黙って変換せず、重みの根拠がない行を fail-closed にする。"""
    with pytest.raises(RecencyContractError):
        build_recency_weights(
            ["A"],
            [None],
            cutoff=_CUTOFF,
            spec=RecencyWeightSpec(half_life_days=365),
        )


@pytest.mark.parametrize("half_life_days", [29, 7301])
def test_half_life_outside_registered_range_is_rejected(half_life_days: float) -> None:
    """事前登録範囲外の半減期を拒否し、結果を見た後の探索へ契約が拡張されるのを防ぐ。"""
    with pytest.raises(RecencyContractError):
        spec = RecencyWeightSpec(half_life_days=half_life_days)
        build_recency_weights(["A"], [_CUTOFF], cutoff=_CUTOFF, spec=spec)


def test_mismatched_input_lengths_are_rejected() -> None:
    """race_id と日付の対応ずれを拒否し、別レースの日付で重み付けする事故を防ぐ。"""
    with pytest.raises(RecencyContractError):
        build_recency_weights(
            ["A", "B"],
            [_CUTOFF],
            cutoff=_CUTOFF,
            spec=RecencyWeightSpec(half_life_days=365),
        )


def test_empty_input_is_rejected() -> None:
    """空データの正規化を拒否し、NaN の監査値や見かけ上の成功を残さない。"""
    with pytest.raises(RecencyContractError):
        build_recency_weights(
            [],
            [],
            cutoff=_CUTOFF,
            spec=RecencyWeightSpec(half_life_days=365),
        )


def test_datetime_race_date_is_not_silently_coerced() -> None:
    """datetime を date へ黙って丸めず、時刻型が混ざった入力を明示的に拒否する。"""
    with pytest.raises(RecencyContractError):
        build_recency_weights(
            ["A"],
            [datetime.datetime(2026, 4, 1, 12, 0)],
            cutoff=_CUTOFF,
            spec=RecencyWeightSpec(half_life_days=365),
        )


@pytest.mark.parametrize("floor", [-0.01, 0.0, 1.0, 1.01])
def test_floor_outside_open_unit_interval_is_rejected(floor: float) -> None:
    """0 < floor < 1 を強制し、ゼロ重みや減衰しない重みへ仕様が退化するのを防ぐ。"""
    with pytest.raises(RecencyContractError):
        spec = RecencyWeightSpec(half_life_days=365, floor=floor)
        build_recency_weights(["A"], [_CUTOFF], cutoff=_CUTOFF, spec=spec)


def test_no_wall_clock_dependence() -> None:
    """**壁時計を読まない**ことを固定する(実装で一度入っていた経路の再発防止)。

    「cutoff が今日より後か」を検査したくなるが、それをすると重みが `(race_date, cutoff)` の
    純関数でなくなり、**同じ呼び出しが日付をまたぐと結果(成功/例外)を変える**。
    テストが放っておくと勝手に通り始める/落ち始めることになり、再現性が壊れる。

    walk-forward の cutoff は常に過去なので実害が無いように見えるが、この module は
    「純関数であること」を売りにしている(INV-W1)ので、隠れた入力を持たせない。
    """
    import ast
    import inspect
    import pathlib

    from horseracing_training import recency

    src = pathlib.Path(inspect.getfile(recency)).read_text()
    tree = ast.parse(src)
    banned = {"today", "now", "utcnow", "time", "monotonic"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in banned:
            raise AssertionError(
                f"recency.py が壁時計を読んでいる: .{node.attr}() — 重みは "
                "(race_date, cutoff) の純関数でなければならない(INV-W1)"
            )

    # 挙動でも固定: 実行日より先の cutoff でも、入力が整合していれば通る
    far_future = datetime.date(2099, 12, 31)
    spec = RecencyWeightSpec(half_life_days=365)
    w = build_recency_weights(
        ["r1", "r2"],
        [datetime.date(2099, 1, 1), datetime.date(2098, 1, 1)],
        cutoff=far_future,
        spec=spec,
    )
    assert abs(float(w.sum()) - 2.0) < 1e-9
