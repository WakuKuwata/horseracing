"""昇格ゲートの契約版比較が**下限比較**であることを表として固定する(100 / T003).

このリポジトリには**性質の違う版比較が 2 地点**あり、混同すると片方が必ず壊れる(FR-003)。

===========================================  ==========  ==================================
地点                                          比較        壊れ方
===========================================  ==========  ==================================
``adoption.evaluate_promotion``(ここ)        **下限**    等値にすると版を上げた瞬間に
                                                          昇格が黙って全滅する(発生済み)
``eval.decision.assert_confirmatory``         **等値**    下限に緩めると、別のルールで
                                                          判断された数値を再判定してしまい
                                                          verdict の不変性が壊れる
===========================================  ==========  ==================================

本ファイルは前者だけを扱う。後者は ``eval/tests/unit/test_frozen_contract_parity.py``。

実際の床は ``MIN_CONTRACT_VERSION = 3``(v5 でも上げない — 下限比較なので v5 の verdict は
そのまま受理される)。床の値そのものに依存しないよう、**注入した床**に対して表を張る。
"""

from __future__ import annotations

import pytest

from horseracing_training import adoption
from horseracing_training.adoption import AdoptionDecision, evaluate_promotion

ADOPTED = AdoptionDecision(adopted=True, reasons={})


def _report(version: str) -> dict:
    return {
        "decision": "ADOPT",
        "decision_reason": {"subgroup_assurance": "full"},
        "evaluation_contract_version": version,
    }


def _promote(version: str) -> str:
    return evaluate_promotion(legacy=ADOPTED, verdict=_report(version)).status


#: (注入する床, verdict が宣言する版, 昇格できるか)
TABLE = [
    (3, "v3", True),    # 床ちょうど
    (3, "v4", True),    # 床より新しい = より強い証拠。**通らねばならない**
    (3, "v5", True),    # 将来版も同様
    (4, "v3", False),   # 床より古い = 弱い証拠
    (4, "v4", True),
    (4, "v5", True),
    (5, "v4", False),
]


@pytest.mark.parametrize(("floor", "declared", "promotable"), TABLE)
def test_lower_bound_table(monkeypatch, floor: int, declared: str, promotable: bool) -> None:
    monkeypatch.setattr(adoption, "MIN_CONTRACT_VERSION", floor)
    status = _promote(declared)
    assert (status == "active") is promotable, (
        f"floor={floor} declared={declared}: expected promotable={promotable}, got {status!r}"
    )


def test_equality_mutation_would_kill_newer_evidence(monkeypatch) -> None:
    """**mutation**: 下限を等値に変えると「より新しい版の証拠」が全滅することを実演する。

    これは仮想の心配ではない。training が ``"v3"`` を等値で要求している間に eval が ``"v4"`` に
    なり、**より強い**証拠を出した判定がすべて candidate に落とされていた実績がある。
    """
    monkeypatch.setattr(adoption, "MIN_CONTRACT_VERSION", 3)
    assert _promote("v4") == "active"

    # 等値比較に相当する床(= その版でしか通らない状態)を作ると新しい証拠が弾かれる
    monkeypatch.setattr(adoption, "MIN_CONTRACT_VERSION", 5)
    assert _promote("v4") == "candidate"


def test_actual_floor_is_three_and_does_not_move_for_v5() -> None:
    """実際の床を pin する。v5 を導入しても**床は上げない**(FR-003 / analyze M1)。

    下限比較なので、床を 3 のままにしても v5 の verdict はそのまま受理される。床を上げる
    必要が出るのは「v4 以前の証拠を積極的に締め出したい」ときだけで、本 feature はそれを
    要求していない。
    """
    assert adoption.MIN_CONTRACT_VERSION == 3
    assert _promote("v5") == "active"
