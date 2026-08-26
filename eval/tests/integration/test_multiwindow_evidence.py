"""複数窓/複数レジームの driver が per-race 証拠を落とさない(100 / T019・INV-A3・FR-009)。

**なぜ独立したテストが要るか**: 落としていたのは ``paired_eval`` ではなく **driver 側**だった。
097 は各窓の判定を回したうえで、verdict.json には要約だけを書き、差の生値を 1 件も残さなかった。
単体で ``PairedReport`` に証拠が付いていても、束ねる側が捨てれば同じ状態に戻る。
"""

from __future__ import annotations

import pytest

from horseracing_eval.evidence import (
    PairedEvidenceArtifact,
    PairedEvidenceRow,
    diffs_by_day,
    recompute,
)
from horseracing_eval.paired import paired_eval

from ..unit.test_evidence_recompute_parity import CFG, _FakeFactory, _races


def _window(years, tag_c="cand", tag_a="act"):
    """walk-forward なので、採点する窓より前のレースは**学習側として残す**。

    窓のレースだけを渡すと最初の fold に学習データが無くなる。採点範囲は
    ``first_valid_year``(下端)と入力集合の上端で決める。
    """
    races = [er for er in _races() if er.context.race_date.year <= max(years)]
    return paired_eval(_FakeFactory(0.62, tag_c), _FakeFactory(0.45, tag_a), races,
                       gate_config=CFG, first_valid_year=min(years))


def test_each_window_keeps_its_own_evidence() -> None:
    """窓ごとの判定がそれぞれ証拠を持つ。"""
    for years in [(2021, 2022), (2022, 2023), (2023, 2024)]:
        rep = _window(years)
        assert rep.evidence is not None and rep.evidence.rows
        scored = {int(r.race_day[:4]) for r in rep.evidence.rows}
        assert min(scored) >= min(years) and max(scored) <= max(years)


def test_pooling_windows_preserves_every_row() -> None:
    """複数窓を束ねても行が 1 件も消えない(要約に潰さない)。"""
    windows = [_window(y) for y in [(2021, 2021), (2022, 2022), (2023, 2023)]]
    pooled: list[PairedEvidenceRow] = []
    seq = 0
    for rep in windows:
        for r in sorted(rep.evidence.rows, key=lambda x: x.seq):
            pooled.append(PairedEvidenceRow(**{**r.to_dict(), "seq": seq}))
            seq += 1
    assert len(pooled) == sum(len(w.evidence.rows) for w in windows)
    assert len({r.race_id for r in pooled}) == len(pooled)


def test_pooled_evidence_reproduces_a_pooled_interval() -> None:
    """束ねた証拠から窓横断の CI が出せる(097 が本来やりたかったこと)。"""
    windows = [_window(y) for y in [(2021, 2021), (2022, 2022), (2023, 2023)]]
    pooled: list[PairedEvidenceRow] = []
    seq = 0
    for rep in windows:
        for r in sorted(rep.evidence.rows, key=lambda x: x.seq):
            pooled.append(PairedEvidenceRow(**{**r.to_dict(), "seq": seq}))
            seq += 1
    art = PairedEvidenceArtifact(
        rows=tuple(pooled), bootstrap=windows[0].evidence.bootstrap,
        seed_noise=windows[0].evidence.seed_noise, evaluation_contract_version="v4",
        gate_config_hash="pooled", race_id_set_hash="pooled",
        candidate_recipe_hash="cand", active_recipe_hash="act",
        window={"from": "2021-03-01", "to": "2023-12-01"},
    )
    got = recompute(art)
    assert got["n_races"] == len(pooled)
    # 束ねた点推定は各窓の点推定の内側に収まる(全窓で候補が有利な合成データなので)
    assert got["point"] < 0


def test_regime_driver_carries_evidence_per_regime() -> None:
    """``RegimeReport`` にレジームごとの証拠が載っている(FR-009)。"""
    from horseracing_eval.regime_paired import RegimeReport

    fields = {f for f in RegimeReport.__dataclass_fields__}
    assert "evidence_by_regime" in fields, (
        "RegimeReport が証拠を持っていない。複数レジーム driver が要約だけを書く状態に戻っている"
    )
    empty = RegimeReport(artifact_kind="k", eligible_for_verdict=False, race_set_hash="r",
                         serving_regime={}, full_info_regime={}, full_info_guard=True, verdict={})
    assert "evidence_by_regime" in empty.to_dict()


def test_dropping_rows_is_detectable() -> None:
    """**mutation**: 行を落とした証拠は行数照合で落ちる(INV-E1)。"""
    from horseracing_eval.evidence import EvidenceContractError, assert_contract

    rep = _window((2021, 2024))
    full = rep.evidence
    assert_contract(full, n_races=len(full.rows))
    truncated = PairedEvidenceArtifact(**{**full.to_dict(), "rows": full.rows[:-1]})
    with pytest.raises(EvidenceContractError, match="n_races"):
        assert_contract(truncated, n_races=len(full.rows))


def test_day_grouping_matches_the_rows_it_came_from() -> None:
    rep = _window((2021, 2024))
    assert rep.diffs_by_day == diffs_by_day(rep.evidence.rows)
