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

**2026-09(bdc789e)で表の読み方が変わった — 契約そのものは変わっていない**
-------------------------------------------------------------------------
``evaluate_promotion`` は版の比較を通ったあとに、昇格証拠の束縛(``promotion_evidence``・
両アームの ``fitted_contract`` と登録済み契約の一致)まで検査するようになった。証拠の束縛は
``assert_confirmatory`` を経由するので、**現行版以外を宣言した report は必ずそこで落ちる**。
つまり「最終 status が active か」はもはや版比較だけの関数ではなく、表を status で張ると
*床より新しい版*まで candidate に見えてしまう(= 等値比較との区別がつかなくなる)。

そこで表は **版ゲートが弾いたかどうか**(``cause == "verdict_contract_too_old"``)で張る。
これが下限性の直接の観測点で、後段のガードが増えても意味が変わらない。「下限性が実際に
active まで到達しうる」ことは ``test_current_contract_version_reaches_active`` が別に押さえる。
"""

from __future__ import annotations

import pytest
from horseracing_eval.decision import EVALUATION_CONTRACT_VERSION

from horseracing_training import adoption
from horseracing_training.adoption import AdoptionDecision, evaluate_promotion
from tests._promotion_evidence import confirmed_report, contracts

ADOPTED = AdoptionDecision(adopted=True, reasons={})

#: 版ゲートが弾いたときだけ付く cause。
TOO_OLD = "verdict_contract_too_old"


def _report(version: str) -> dict:
    """現行契約で完全に適格な confirmed report の、**宣言版だけ**を差し替える。

    版以外を適格なままにしておくのが要点。そうしないと「版で落ちた」のか「証拠が足りなくて
    落ちた」のかが区別できず、表が下限性を測らなくなる。
    """
    report = confirmed_report()
    report["evaluation_contract_version"] = version
    return report


def _cause(version: str) -> str | None:
    return evaluate_promotion(
        legacy=ADOPTED, verdict=_report(version), **contracts()
    ).reasons.get("cause")


#: (注入する床, verdict が宣言する版, 版ゲートが弾くか)
TABLE = [
    (3, "v3", False),   # 床ちょうど
    (3, "v4", False),   # 床より新しい = より強い証拠。**弾いてはならない**
    (3, "v5", False),   # 将来版も同様
    (4, "v3", True),    # 床より古い = 弱い証拠
    (4, "v4", False),
    (4, "v5", False),
    (5, "v4", True),
]


@pytest.mark.parametrize(("floor", "declared", "blocked"), TABLE)
def test_lower_bound_table(monkeypatch, floor: int, declared: str, blocked: bool) -> None:
    monkeypatch.setattr(adoption, "MIN_CONTRACT_VERSION", floor)
    cause = _cause(declared)
    assert (cause == TOO_OLD) is blocked, (
        f"floor={floor} declared={declared}: expected blocked_by_version_gate={blocked}, "
        f"got cause={cause!r}"
    )


def test_equality_mutation_would_kill_newer_evidence(monkeypatch) -> None:
    """**mutation**: 下限を等値に変えると「より新しい版の証拠」が全滅することを実演する。

    これは仮想の心配ではない。training が ``"v3"`` を等値で要求している間に eval が ``"v4"`` に
    なり、**より強い**証拠を出した判定がすべて candidate に落とされていた実績がある。
    """
    monkeypatch.setattr(adoption, "MIN_CONTRACT_VERSION", 3)
    assert _cause("v4") != TOO_OLD

    # 等値比較に相当する床(= その版でしか通らない状態)を作ると新しい証拠が弾かれる
    monkeypatch.setattr(adoption, "MIN_CONTRACT_VERSION", 5)
    assert _cause("v4") == TOO_OLD


def test_actual_floor_is_three_and_does_not_move_for_v5() -> None:
    """実際の床を pin する。v5 を導入しても**床は上げない**(FR-003 / analyze M1)。

    下限比較なので、床を 3 のままにしても v5 の verdict は**版ゲートを通過する**。床を上げる
    必要が出るのは「v4 以前の証拠を積極的に締め出したい」ときだけで、本 feature はそれを
    要求していない。
    """
    assert adoption.MIN_CONTRACT_VERSION == 3
    assert _cause("v5") != TOO_OLD


def test_current_contract_version_reaches_active() -> None:
    """表が空回りしていないことの担保: 現行版の適格な証拠は実際に ACTIVE まで到達する。

    ``cause != TOO_OLD`` だけを見ていると、後段のガードが全件を candidate に落としていても
    表は緑のままになる。現行版の 1 点だけは最終 status で押さえる。
    """
    decision = evaluate_promotion(
        legacy=ADOPTED, verdict=confirmed_report(), **contracts()
    )
    assert decision.status == "active", decision.reasons
    assert decision.reasons["cause"] == "legacy_gate_and_v3_verdict_agree"
    # 現行版は床より新しい(= 下限比較でなければ通らない)ことを明示しておく
    assert int(EVALUATION_CONTRACT_VERSION.lstrip("v")) > adoption.MIN_CONTRACT_VERSION
