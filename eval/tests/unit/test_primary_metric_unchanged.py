"""PRIMARY 指標が feature 100 で変わっていないことを固定する(100 / T009a・FR-001)。

証拠 artifact の追加は**報告を増やすだけ**で、何をどう測るかを変えてはならない。この feature は
判定の道具に手を入れるので、「ついでに指標も直した」が最も混入しやすい。
"""

from __future__ import annotations

from horseracing_eval.paired import paired_eval

from .test_evidence_recompute_parity import CFG, _FakeFactory, _races


def _rep():
    return paired_eval(_FakeFactory(0.62, "cand"), _FakeFactory(0.45, "act"), _races(),
                       gate_config=CFG, first_valid_year=2020)


def test_primary_is_race_level_winner_nll() -> None:
    """指標は 1 レース 1 標本の winner NLL のまま。"""
    rep = _rep()
    assert set(rep.periods) >= {"all", "recent_3y", "recent_5y"}
    all_period = rep.periods["all"]
    assert all_period["diff"] == all_period["candidate"] - all_period["active"]


def test_point_estimate_is_the_mean_of_the_per_race_diffs() -> None:
    """点推定 = per-race 差の単純平均。**共変量で調整していない**(US2 は棄却済み・FR-014)。"""
    rep = _rep()
    diffs = [r.diff for r in rep.evidence.rows]
    assert rep.bootstrap_ci["point"] == sum(diffs) / len(diffs)


def test_evaluation_population_is_the_eligible_races() -> None:
    """評価母集団が証拠の行集合と一致している(母集団のすり替えが無い)。"""
    rep = _rep()
    assert len(rep.evidence.rows) == rep.n_eligible
    assert len({r.race_id for r in rep.evidence.rows}) == rep.n_eligible


def test_gate_config_carries_no_control_variate_block() -> None:
    """FR-014: 調整器は導入しない。gate-config にその口を作らない。"""
    rep = _rep()
    assert "control_variate" not in CFG
    assert rep.evidence.to_dict().get("control_variate") is None


def test_covariates_do_not_reach_the_interval() -> None:
    """共変量を書き換えても点推定も CI も動かない = 記録専用であることの証明。"""
    from horseracing_eval.evidence import PairedEvidenceArtifact, recompute
    rep = _rep()
    d = rep.evidence.to_dict()
    for row in d["rows"]:
        row["covariates"] = {"field_size": 999, "race_year": 1900, "bogus": 1.0}
    tampered = recompute(PairedEvidenceArtifact.from_dict(d))
    assert tampered["point"] == rep.bootstrap_ci["point"]
    assert tampered["sample_ci"] == rep.bootstrap_ci
    assert tampered["total_ci"] == rep.total_ci
