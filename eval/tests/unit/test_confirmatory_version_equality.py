"""``assert_confirmatory`` の版比較が**等値**であることを固定する(100 / T005).

この等値比較は**意図的な fail-closed** である。理由は ``assert_confirmatory`` の docstring に
書いたとおり: 別のルールで判断された数値を黙って再判定すると、それが記録した verdict の
不変性が壊れる。

昇格ゲート(``adoption.evaluate_promotion``)の**下限**比較とは目的が逆であり、両者を
「版比較」として一括で扱う変更は片方を必ず壊す(FR-003)。ここはその取り違えを止める番人。
"""

from __future__ import annotations

import pytest

from horseracing_eval.decision import (
    EVALUATION_CONTRACT_VERSION,
    ConfirmatoryContractError,
    assert_confirmatory,
    gate_config_hash,
)


def _cfg(version: str) -> dict:
    return {
        "evaluation_contract_version": version,
        "primary_metric": "winner_nll",
        "min_effect_delta": 0.002,
        "seed_noise": {"sd_fold": 0.001816, "k_seeds": 1},
        "eval_window": {"from": "2019-01-01", "to": "2026-08-16"},
    }


def _assert(cfg: dict) -> None:
    assert_confirmatory(
        cfg,
        expected_hash=gate_config_hash(cfg),
        eval_window={"from": cfg["eval_window"]["from"], "to": cfg["eval_window"]["to"]},
    )


def test_current_version_passes() -> None:
    _assert(_cfg(EVALUATION_CONTRACT_VERSION))


@pytest.mark.parametrize("version", ["v1", "v2", "v3"])
def test_older_version_fails_closed(version: str) -> None:
    """旧版は弾かれる。**これを下限比較に緩める mutation はここで落ちる。**"""
    with pytest.raises(ConfirmatoryContractError, match="evaluation_contract_version"):
        _assert(_cfg(version))


def test_newer_version_also_fails_closed() -> None:
    """新しい版も弾かれる — 「下限」ではなく「等値」であることの証明。

    昇格ゲートなら新しい版は通す(より強い証拠だから)。ここは通さない。verdict を記録した
    ときのルールと、今のコードが実装するルールが**同じであること**を要求している。
    """
    newer = f"v{int(EVALUATION_CONTRACT_VERSION.lstrip('v')) + 1}"
    with pytest.raises(ConfirmatoryContractError, match="evaluation_contract_version"):
        _assert(_cfg(newer))


def test_missing_version_fails_closed() -> None:
    cfg = _cfg(EVALUATION_CONTRACT_VERSION)
    del cfg["evaluation_contract_version"]
    with pytest.raises(ConfirmatoryContractError, match="evaluation_contract_version"):
        _assert(cfg)


def test_us1_and_us4_must_not_bump_the_constant() -> None:
    """**feature 100 の設計判断を pin する**(FR-002c)。

    US1(証拠 artifact の追加)も US4(δ の provenance)も**判定ルールを変えない**ので、
    契約版を上げてはならない。上げた瞬間に 095〜099 が confirmatory 経路から締め出され、
    SC-002 が達成不能になる(analyze C1)。

    版が上がってよいのは US3(estimand と seed 成分の扱いが変わる)だけで、それはスパイクの
    足切りを通過してからである。したがって **US1/US4 の実装中は定数が v4 のままであること**が
    要件になる。
    """
    assert EVALUATION_CONTRACT_VERSION == "v4", (
        "契約版が動いている。US1/US4 では上げてはならない(FR-002c)。US3 が足切りを通過して "
        "estimand が変わるときだけ上げ、そのとき 095〜099 が孤児になることを承知で行うこと。"
    )
