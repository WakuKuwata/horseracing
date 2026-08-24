"""099 T013: margin スケールの手計算一致・中立意味論・ON/OFF の fit 統合(container DB)。

INV-MT7(run 1 バグ形の回帰): LEAD は全完走馬に対して計算し着順 1..3 制限は後 — 4 頭目が
見えないと s3 が全レース中立になる。INV-MT8: 直後馬の時計欠損は中立 1.0 であり、次の別馬と
ペアリングしてはならない。実データ形状の s2/s3 fireable 平均(<0.9)の表明は本番 DB での
E2E(T015)側 — container の合成データは分布を持たないため。
"""

from __future__ import annotations

import datetime

import pytest
from horseracing_eval.dataset import load_eval_races

from horseracing_training.dataset import _margin_stage_scales
from horseracing_training.predictor import LightGBMPredictor
from tests._synth import insert_race, seed_learnable

pytestmark = pytest.mark.integration


def _t(seconds: float) -> datetime.timedelta:
    return datetime.timedelta(seconds=seconds)


def test_stage3_sees_the_fourth_finisher(session):
    """完走 4 頭・隣接差 0.1s → s2 = s3 = 0.5(手計算)。run 1 のバグ形なら s3=1.0 で赤。"""
    insert_race(
        session, race_id="202001010101", race_date=datetime.date(2020, 1, 1),
        horses=[
            {"horse_id": f"H{i}", "horse_number": i, "finish_order": i,
             "finish_time": _t(90.0 + 0.1 * (i - 1))}
            for i in range(1, 5)
        ],
    )
    scales, audit = _margin_stage_scales(session)
    assert scales["202001010101"] == (0.5, 0.5)
    assert audit["s3_defined"] == 1 and audit["s3_undefined"] == 0


def test_three_finishers_leave_stage3_neutral(session):
    """完走 3 頭 → 4 頭目が存在しない = s3 中立 1.0(INV-MT8)。"""
    insert_race(
        session, race_id="202001010102", race_date=datetime.date(2020, 1, 1),
        horses=[
            {"horse_id": f"G{i}", "horse_number": i, "finish_order": i,
             "finish_time": _t(90.0 + 0.1 * (i - 1))}
            for i in range(1, 4)
        ],
    )
    scales, audit = _margin_stage_scales(session)
    assert scales["202001010102"] == (0.5, 1.0)
    assert audit["s3_undefined"] == 1


def test_missing_clock_on_next_horse_is_neutral_not_repaired(session):
    """3 着の時計欠損 → s2 は「次の別馬(4 着)」とペアリングせず中立 1.0(INV-MT8)。

    時計欠損行を window から除外すると 2 着が 4 着と組み「定義済みだが誤った margin」に
    なる — その形をここで止める。3 着自身の s3 も NULL 差分 → 中立。
    """
    insert_race(
        session, race_id="202001010103", race_date=datetime.date(2020, 1, 1),
        horses=[
            {"horse_id": "M1", "horse_number": 1, "finish_order": 1, "finish_time": _t(90.0)},
            {"horse_id": "M2", "horse_number": 2, "finish_order": 2, "finish_time": _t(90.1)},
            {"horse_id": "M3", "horse_number": 3, "finish_order": 3, "finish_time": None},
            {"horse_id": "M4", "horse_number": 4, "finish_order": 4, "finish_time": _t(90.4)},
        ],
    )
    scales, _ = _margin_stage_scales(session)
    assert scales["202001010103"] == (1.0, 1.0)


def _seed_with_clocks(session):
    seed_learnable(session, years=(2007, 2008, 2009), races_per_year=12, field_size=8)
    from horseracing_db.models import RaceResult

    for rr in session.query(RaceResult).all():
        rr.finish_time = _t(90.0 + 0.1 * rr.finish_order)  # 隣接差 0.1s → 全ステージ 0.5
    session.commit()


def test_margin_teacher_fit_records_stats_and_changes_the_model(session):
    """ON: fit_info に統計(fireable_mean=0.5・scale_lt1>0)。OFF: key 不在で予測が異なる。"""
    _seed_with_clocks(session)
    races = load_eval_races(session)
    contexts = [er.context for er in races]

    on = LightGBMPredictor(
        session, objective="pl_topk", calibration="none", margin_teacher="v1")
    on.fit(contexts)
    info = on.fit_info_["margin_teacher"]
    assert info["variant"] == "v1" and info["m0"] == 0.2 and info["gmin"] == 0.25
    for stage in ("s2", "s3"):
        assert info[stage]["scale_lt1_races"] > 0, stage
        assert info[stage]["fireable_mean"] == pytest.approx(0.5), stage
    assert info["build_audit"]["races_in_map"] > 0

    off = LightGBMPredictor(session, objective="pl_topk", calibration="none")
    off.fit(contexts)
    assert "margin_teacher" not in off.fit_info_  # INV-MT9: OFF は key 不在

    # 教師信号が実際にモデルを変えたこと(silent no-op の否定)
    rc = contexts[-1]
    p_on = on.predict_race(rc)
    p_off = off.predict_race(rc)
    diffs = [abs(p_on[h].win - p_off[h].win) for h in p_on]
    assert max(diffs) > 0.0


def test_margin_teacher_rejects_non_pl_topk(session):
    with pytest.raises(ValueError, match="pl_topk"):
        LightGBMPredictor(session, objective="cond_logit", margin_teacher="v1")
    with pytest.raises(ValueError, match="margin_teacher"):
        LightGBMPredictor(session, objective="pl_topk", margin_teacher="v2")
