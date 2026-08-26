"""per-race 証拠行の不変条件(100 / T009・INV-E1..E7)。"""

from __future__ import annotations

import json

import pytest

from horseracing_eval.evidence import (
    SIGN_CONVENTION,
    EvidenceContractError,
    PairedEvidenceArtifact,
    PairedEvidenceRow,
    assert_contract,
    build_rows,
    diffs_by_day,
    race_covariates,
)

ENTRIES = [
    ("202401010101", "2024-01-01", 2.10, 2.30),
    ("202401010102", "2024-01-01", 1.80, 1.70),
    ("202401020101", "2024-01-02", 2.00, 2.00),
]


def _artifact(rows) -> PairedEvidenceArtifact:
    return PairedEvidenceArtifact(
        rows=tuple(rows),
        bootstrap={"b": 200, "seed": 1, "alpha": 0.05, "block": "race_day"},
        seed_noise={},
        evaluation_contract_version="v4",
        gate_config_hash="h", race_id_set_hash="r",
        candidate_recipe_hash="c", active_recipe_hash="a",
        window={"from": "2024-01-01", "to": "2024-01-02"},
    )


def test_diff_is_exactly_candidate_minus_active() -> None:
    """INV-E3: 浮動小数点で厳密一致。丸めや再計算を挟まない。"""
    for r, (_rid, _day, c, a) in zip(build_rows(ENTRIES), ENTRIES, strict=True):
        assert r.diff == c - a
        assert r.candidate_winner_nll == c
        assert r.active_winner_nll == a


def test_sign_convention_is_candidate_minus_active() -> None:
    """INV-E4: 符号規約。**向きが逆でも CI の幅はもっともらしく見える**ので明示検査する。"""
    rows = build_rows(ENTRIES)
    assert SIGN_CONVENTION == "candidate_minus_active"
    # 候補が良い(NLL が小さい)レースは負の差になる
    assert rows[1].diff > 0   # candidate 1.80 > active 1.70 -> 候補が悪い
    assert rows[0].diff < 0   # candidate 2.10 < active 2.30 -> 候補が良い
    assert _artifact(rows).sign_convention == SIGN_CONVENTION


def test_row_count_mismatch_fails_closed() -> None:
    """INV-E1: 行数が verdict の n_races と一致しなければ落とす。"""
    art = _artifact(build_rows(ENTRIES))
    assert_contract(art, n_races=3)
    with pytest.raises(EvidenceContractError, match="n_races"):
        assert_contract(art, n_races=4)


def test_duplicate_race_id_fails_closed() -> None:
    """INV-E2: race_id は artifact 内で一意。"""
    dupe = build_rows(ENTRIES + [ENTRIES[0]])
    with pytest.raises(EvidenceContractError, match="重複"):
        assert_contract(_artifact(dupe))


def test_tampered_diff_fails_closed() -> None:
    """INV-E3 の番人: 差だけ書き換えた証拠は受理しない。"""
    rows = list(build_rows(ENTRIES))
    rows[0] = PairedEvidenceRow(**{**rows[0].to_dict(), "diff": 0.0})
    with pytest.raises(EvidenceContractError, match="INV-E3"):
        assert_contract(_artifact(rows))


def test_broken_sign_convention_fails_closed() -> None:
    """符号規約を宣言し直した artifact は受理しない(INV-E4)。"""
    art = _artifact(build_rows(ENTRIES))
    flipped = PairedEvidenceArtifact(**{**art.to_dict(), "rows": art.rows,
                                        "sign_convention": "active_minus_candidate"})
    with pytest.raises(EvidenceContractError, match="sign_convention"):
        assert_contract(flipped)


def test_grouping_is_row_order_independent() -> None:
    """INV-E6: ファイル上の行順を入れ替えても集約が変わらない(``seq`` で復元する)。"""
    rows = build_rows(ENTRIES)
    assert diffs_by_day(rows) == diffs_by_day(list(reversed(rows)))
    assert diffs_by_day(rows) == diffs_by_day(sorted(rows, key=lambda r: r.race_id, reverse=True))


def test_seq_must_be_a_permutation_of_range() -> None:
    """``seq`` が欠けたり重複したら再現できないので落とす。"""
    rows = list(build_rows(ENTRIES))
    rows[2] = PairedEvidenceRow(**{**rows[2].to_dict(), "seq": 99})
    with pytest.raises(EvidenceContractError, match="seq"):
        assert_contract(_artifact(rows))


def test_round_trip_is_lossless() -> None:
    """INV-E7: JSON を往復しても値が動かない(丸めない)。"""
    art = _artifact(build_rows(ENTRIES))
    back = PairedEvidenceArtifact.from_dict(json.loads(json.dumps(art.to_dict())))
    assert back.to_dict() == art.to_dict()
    for a, b in zip(art.rows, back.rows, strict=True):
        assert a.diff == b.diff and a.candidate_winner_nll == b.candidate_winner_nll


def test_covariates_carry_no_result_information() -> None:
    """INV-E5: 共変量はレース属性のみ。着順・オッズ・払戻に触れない。"""
    cov = race_covariates("202401010101", field_size=16, race_day="2024-01-01")
    assert cov == {"field_size": 16, "race_year": 2024}
    forbidden = ("finish", "odds", "popular", "payout", "dividend", "winner", "result")
    for key in cov:
        assert not any(f in key.lower() for f in forbidden), key
