"""099 T011: mask/split/sort を通ったスケールの整列と、破損の fail-loud(codex 最小テスト 2)。

配線スコープのみ — ValueError/値域検証そのものの単体テストは保全対象の
test_margin_teacher_objective.py 側(win_model の _group_stage_scales は本ファイルでは
「整列した値が正しいレースのものか」を spy で見る)。
"""

from __future__ import annotations

import numpy as np
import pytest

from horseracing_training.win_model import WinModel, _group_stage_scales


def test_group_stage_scales_follow_sorted_group_order():
    """レースを交互配置 → argsort 後の group 順にスケールが追随すること。

    race_id の stable sort 順(= 'a' → 'b')で group が並ぶので、(n_groups,3) の行順も
    その順でなければならない。整列を崩す(行順のままにする)と別レースのスケールで学習する
    — 097 の「shared matrix 迂回」と同じ静かな汚染。
    """
    # 行は b, a, b, a, ... の交互(未ソート)。race a: s=(0.5, 0.25) / race b: s=(1.0, 0.75)
    race_of_row = np.array(["b", "a", "b", "a", "b", "a"])
    scales_by_race = {"a": (0.5, 0.25), "b": (1.0, 0.75)}
    margin = np.array([scales_by_race[r] for r in race_of_row], dtype=float)

    order = np.argsort(race_of_row, kind="stable")
    sorted_margin = margin[order]
    gsizes = [3, 3]  # sorted: aaa bbb
    out = _group_stage_scales(sorted_margin, gsizes)
    assert out.shape == (2, 3)
    np.testing.assert_array_equal(out[0], [1.0, 0.5, 0.25])   # race a
    np.testing.assert_array_equal(out[1], [1.0, 1.0, 0.75])   # race b


def test_inhomogeneous_scale_within_race_fails_loud():
    """レース内不均一(1 行だけ破損)は先頭行方式で隠れず ValueError(INV-MT3)."""
    margin = np.array([[0.5, 0.25], [0.5, 0.25], [0.5, 0.30]])  # 3 行目の s3 が破損
    with pytest.raises(ValueError, match="not race-constant"):
        _group_stage_scales(margin, [3])


def test_margin_scales_require_pl_topk():
    """cond_logit / binary で受理すると silent no-op — fail-closed を表明。"""
    import pandas as pd

    X = pd.DataFrame({"f": [0.1, 0.2]})
    with pytest.raises(ValueError, match="pl_topk"):
        WinModel(objective="cond_logit").fit(
            X, np.array([1, 0]), group_ids=np.array(["r", "r"]),
            margin_scales=np.array([[0.5, 0.5], [0.5, 0.5]]),
        )


def test_scales_reach_the_objective_through_winmodel(monkeypatch):
    """WinModel.fit → objective 構築まで、ソート追随済みの (n_groups,3) が届くこと(spy)。"""
    import pandas as pd

    import horseracing_training.win_model as wm

    captured = {}
    real = wm.pl_topk_objective

    def spy(gsizes, ranks, offsets=None, stage_scales=None):
        captured["gsizes"] = list(gsizes)
        captured["stage_scales"] = None if stage_scales is None else np.array(stage_scales)
        return real(gsizes, ranks, offsets=offsets, stage_scales=stage_scales)

    monkeypatch.setattr(wm, "pl_topk_objective", spy)

    # 未ソートの 2 レース(交互)・レースごとに別スケール。LightGBM が分割を作れる規模。
    rng = np.random.default_rng(3)
    per_race = 8
    race = np.array(["b", "a"] * per_race)
    ranks = np.zeros(2 * per_race, dtype=int)
    for rid in ("a", "b"):
        idx = np.nonzero(race == rid)[0]
        for j, i in enumerate(idx[:3], start=1):
            ranks[i] = j
    y = (ranks == 1).astype(float)
    X = pd.DataFrame({"f": rng.normal(size=2 * per_race), "g": rng.normal(size=2 * per_race)})
    scales_by = {"a": (0.5, 0.25), "b": (1.0, 0.75)}
    margin = np.array([scales_by[r] for r in race], dtype=float)
    WinModel(
        objective="pl_topk",
        params={"n_estimators": 2, "min_child_samples": 1, "num_leaves": 2},
    ).fit(X, y, group_ids=race, ranks=ranks, margin_scales=margin)
    assert captured["gsizes"] == [per_race, per_race]
    np.testing.assert_array_equal(
        captured["stage_scales"], [[1.0, 0.5, 0.25], [1.0, 1.0, 0.75]]
    )
