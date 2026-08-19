"""事前登録した適用集合(opportunity set)での判定 — 2026-08-18。

なぜ要るか: 主指標は窓全体の平均なので、狭い層で強く効く特徴は被覆率で割られる。適用率 25%
のレースで −0.006 の効果を持つ候補は、全体では −0.0015 に薄まり測定ノイズに沈む。20 件超の
特徴がまさにその帯に落ちてきた。

規則は**両方**を要求する: 適用集合での優越性 AND 窓全体での非劣性。優越性だけでは採用しない
(狭い層で稼いで残りで損する候補を通してしまうため)。
"""

from __future__ import annotations

import datetime as dt

import pytest

from horseracing_eval.dataset import EvalRace, ScoringLabel
from horseracing_eval.gates import evaluate_core_gate
from horseracing_eval.opportunity import (
    OpportunityContractError,
    OpportunityScores,
    score_opportunity,
)
from horseracing_eval.predictor import HorseEntry, Prediction, RaceContext

CFG = {
    "opportunity_set": {
        "definition": "牝馬かつ 6-9 月(候補特徴が利用可能でレース内に変動がある)",
        "expected_coverage": [0.20, 0.30],
        "overall_noninferior_margin": 0.001,
    },
    "bootstrap": {"b": 200, "seed": 1, "alpha": 0.05},
    "seed_noise": {"sd_fold": 0.001816, "k_seeds": 1},
}


def _clip(p):
    import math
    return -math.log(min(max(float(p), 1e-15), 1 - 1e-15))


def _races(n=100, start=dt.date(2026, 1, 5)):
    out = []
    for i in range(n):
        rid = f"r{i:04d}"
        horses = (HorseEntry(horse_id="A", horse_number=1),
                  HorseEntry(horse_id="B", horse_number=2))
        out.append(EvalRace(
            context=RaceContext(race_id=rid, race_date=start + dt.timedelta(days=i),
                                started_horses=horses),
            labels=(ScoringLabel(horse_id="A", win=1, top2=1, top3=1),),
            n_result_rows=2,
        ))
    return out


def _preds(races, p_in, p_out, mask):
    """適用集合の中だけ候補が強い、という構図を作る。"""
    return {er.context.race_id: {
        "A": Prediction(p_in if er.context.race_id in mask else p_out, 0.5, 0.5),
        "B": Prediction(1 - (p_in if er.context.race_id in mask else p_out), 0.5, 0.5),
    } for er in races}


def _score(races, mask, cand_p, act_p, cfg=CFG):
    cand = _preds(races, cand_p, 0.50, mask)
    act = _preds(races, act_p, 0.50, mask)
    return score_opportunity(races, cand, act, races=mask, cfg=cfg, clip_nll=_clip,
                             b=cfg["bootstrap"]["b"], seed=1, alpha=0.05,
                             sd_fold=cfg["seed_noise"]["sd_fold"])


def test_effect_is_measured_where_it_applies_not_diluted_by_the_whole_window():
    races = _races()
    mask = {er.context.race_id for er in races[:25]}      # 適用率 25%
    got = _score(races, mask, cand_p=0.60, act_p=0.50)
    assert got.n_races == 25
    assert got.coverage == pytest.approx(0.25)
    assert got.coverage_as_declared is True
    assert got.diff < 0                                   # 適用集合内では候補が有利
    assert got.n_folds == 1


def test_an_undeclared_mask_is_refused():
    """窓を見てから層を選ぶのは、この設計が防ごうとしている選択そのもの。"""
    races = _races()
    mask = {er.context.race_id for er in races[:25]}
    with pytest.raises(OpportunityContractError, match="declares none"):
        _score(races, mask, 0.60, 0.50, cfg={k: v for k, v in CFG.items()
                                             if k != "opportunity_set"})


def test_coverage_outside_the_declared_range_fails_closed():
    """マスクが登録されたものと違う、あるいは選んではいけないもので選んでいる場合、
    たいてい被覆率が動く。それを検知するための検査。"""
    races = _races()
    mask = {er.context.race_id for er in races[:60]}      # 60% は宣言 [0.20,0.30] の外
    with pytest.raises(OpportunityContractError, match="outside the declared range"):
        _score(races, mask, 0.60, 0.50)


def _opp(diff, ci_high, coverage_ok=True):
    return OpportunityScores(
        definition="d", n_races=25, n_days=25, n_eligible_total=100, coverage=0.25,
        declared_coverage=[0.2, 0.3], coverage_as_declared=coverage_ok, diff=diff,
        ci_low=diff - 0.002, ci_high=ci_high, total_ci_low=diff - 0.003,
        total_ci_high=ci_high, n_folds=1,
    )


def _gate(opp, *, overall_diff, overall_ci_low):
    return evaluate_core_gate(
        diff=overall_diff, ci_low=overall_ci_low, ci_high=overall_ci_low + 0.004,
        recent={"pass": True}, top2_diff=-0.0001, top3_diff=-0.0001,
        cand_ece=0.0008, act_ece=0.0009, cfg=CFG, opportunity=opp,
    )


def test_superiority_on_the_slice_plus_non_inferiority_overall_adopts():
    g = _gate(_opp(-0.006, -0.001), overall_diff=-0.0015, overall_ci_low=-0.0035)
    assert g.adopted is True
    assert set(g.sub_gates) >= {"opportunity_effect_beats_delta",
                                "opportunity_ci_upper_below_zero",
                                "opportunity_coverage_as_declared", "overall_noninferior"}
    # 全体の CI 上限がゼロを跨いでいても、それは条件になっていない(希釈されるのが前提)
    assert "ci_upper_below_zero" not in g.sub_gates


def test_superiority_on_the_slice_alone_is_not_enough():
    """狭い層で稼いで残りで損する候補は通してはいけない。全体が確信的に悪ければ落とす。"""
    g = _gate(_opp(-0.006, -0.001), overall_diff=+0.004, overall_ci_low=+0.002)
    assert g.sub_gates["opportunity_ci_upper_below_zero"] is True
    assert g.sub_gates["overall_noninferior"] is False
    assert g.adopted is False


def test_an_inconclusive_slice_does_not_adopt_however_good_the_point_estimate():
    g = _gate(_opp(-0.006, +0.002), overall_diff=-0.0015, overall_ci_low=-0.0035)
    assert g.sub_gates["opportunity_ci_upper_below_zero"] is False
    assert g.adopted is False


def test_coverage_flag_is_a_gate_term_not_just_a_note():
    g = _gate(_opp(-0.006, -0.001, coverage_ok=False),
              overall_diff=-0.0015, overall_ci_low=-0.0035)
    assert g.sub_gates["opportunity_coverage_as_declared"] is False
    assert g.adopted is False


def test_without_an_opportunity_set_the_gate_is_byte_identical_to_before():
    """宣言が無ければ従来どおり(全体での優越性)。opt-in であることの確認。"""
    g = evaluate_core_gate(
        diff=-0.01, ci_low=-0.02, ci_high=-0.001, recent={"pass": True},
        top2_diff=-0.0001, top3_diff=-0.0001, cand_ece=0.0008, act_ece=0.0009,
        cfg={}, opportunity=None,
    )
    assert set(g.sub_gates) == {
        "effect_beats_delta", "ci_upper_below_zero", "recent_no_evidence_of_harm",
        "top2_noninferior", "top3_noninferior", "calibration_noninferior",
        "calibration_not_emergency",
    }
    assert g.adopted is True
