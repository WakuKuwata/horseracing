"""race_class 軸の定義は 1 つでなければならない。

047(segment_edge)と 082(segment_accuracy)は同じ「race_class 軸」を名乗りながら別実装を
持っていた。047 は NFKC + 部分一致、082 は完全一致のホワイトリスト。実 DB の値で突き合わせると
**ＪＧ１/ＪＧ２/ＪＧ３ の 187 レース**で食い違い、082 は障害重賞を「条件」として集計していた —
「条件戦でのモデル精度」の読みに重賞が混ざっていたということ。

047 側には「NFKC を入れないと当時の重賞が全部 条件 に落ちていた」という修正コメントが既にあり、
082 は同じ罠を踏み直した実装だった。ここで固定するのは「両者が一致すること」そのもので、片方を
直したときにもう片方が取り残される形を封じる。

`race_class` の綴りが供給元切替で割れている(`1勝` vs `１勝`、`ｵｰﾌﾟﾝ` vs `オープン`)ことは
仕様であって直さない(098 が正準化を測って REJECT)。だからこそ軸のバケティングは綴りに対して
頑健でなければならない。
"""

from __future__ import annotations

import pytest

from horseracing_eval.segment_accuracy import _assign_race_axis
from horseracing_eval.segment_edge import class_group

#: 実 DB(2007-2026、全 67,000 レース)に存在する race_class の全語彙。
#: 供給元切替をまたいでいるので、同義の綴りが両方入っている。
REAL_VOCABULARY = [
    "未勝利", "500万", "1勝", "新馬", "1000万", "2勝", "ｵｰﾌﾟﾝ", "1600万",
    "Ｇ３", "3勝", "１勝", "Ｇ２", "Ｇ１", "OP(L)", "２勝", "３勝", "オープン",
    "ＪＧ３", "ＪＧ２", "ＪＧ１", "重賞",
]


@pytest.mark.parametrize("race_class", REAL_VOCABULARY)
def test_the_two_diagnostics_agree_on_every_real_value(race_class):
    assert _assign_race_axis("race_class", {"race_class": race_class}) == class_group(race_class)


@pytest.mark.parametrize("race_class", ["ＪＧ１", "ＪＧ２", "ＪＧ３"])
def test_graded_jump_races_are_not_conditioned_races(race_class):
    """187 レース。082 の旧ホワイトリストはこれを 条件 に落としていた。"""
    assert class_group(race_class) == "OP系"


def test_the_feed_naming_the_class_instead_of_the_grade_is_still_graded():
    """`重賞`(アルテミスＳ など 12 レース)。両診断とも 条件 に落としていた共通の取りこぼし。"""
    assert class_group("重賞") == "OP系"


@pytest.mark.parametrize(
    ("old", "new"),
    [("1勝", "１勝"), ("2勝", "２勝"), ("3勝", "３勝"), ("ｵｰﾌﾟﾝ", "オープン")],
)
def test_the_source_cutover_spellings_land_in_the_same_bucket(old, new):
    """綴りは割れたままにする(098)。割れていても軸が割れないことがここの担保。"""
    assert class_group(old) == class_group(new)


def test_a_missing_class_keeps_each_module_s_own_missing_label():
    """統一するのはバケティングであって、欠損の表し方ではない。"""
    assert class_group(None) == "unknown"
    assert _assign_race_axis("race_class", {"race_class": None}) == "missing"
