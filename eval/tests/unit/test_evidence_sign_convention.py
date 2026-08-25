"""差の符号規約が実データ経路で保たれる(100 / T011・INV-E4・FR-008a)。

**なぜ再計算の一致だけでは足りないか**: アームの向きを取り違えても CI の幅は同じくらいの
大きさに見え、点推定の符号だけが反転する。「候補が有利」と「候補が不利」が入れ替わっても、
数値の見た目は自然なままである。だから向きは明示的に検査する。
"""

from __future__ import annotations

from horseracing_eval.evidence import SIGN_CONVENTION, diffs_by_day, recompute
from horseracing_eval.paired import paired_eval

from .test_evidence_recompute_parity import CFG, _FakeFactory, _races


def _run(cand_w: float, act_w: float):
    return paired_eval(_FakeFactory(cand_w, "cand"), _FakeFactory(act_w, "act"), _races(),
                       gate_config=CFG, first_valid_year=2020)


def test_better_candidate_yields_negative_diffs() -> None:
    """候補が良い(勝ち馬に多く確率を置く)なら差は負。"""
    rep = _run(0.70, 0.40)
    assert rep.evidence.sign_convention == SIGN_CONVENTION
    assert all(r.diff < 0 for r in rep.evidence.rows)
    assert recompute(rep.evidence)["point"] < 0


def test_worse_candidate_yields_positive_diffs() -> None:
    rep = _run(0.40, 0.70)
    assert all(r.diff > 0 for r in rep.evidence.rows)
    assert recompute(rep.evidence)["point"] > 0


def test_swapping_arms_flips_every_row() -> None:
    """アームを入れ替えると全行の符号が反転する — 向きが実際に効いている。"""
    ab = {r.race_id: r.diff for r in _run(0.70, 0.40).evidence.rows}
    ba = {r.race_id: r.diff for r in _run(0.40, 0.70).evidence.rows}
    assert set(ab) == set(ba)
    for rid, d in ab.items():
        assert d == -ba[rid]


def test_row_diff_matches_the_arms_it_records() -> None:
    """行が持つ 2 つの loss と差が整合している(片方だけ取り違える事故の検出)。"""
    rep = _run(0.70, 0.40)
    for r in rep.evidence.rows:
        assert r.diff == r.candidate_winner_nll - r.active_winner_nll
        # 候補が良いのだから候補側の NLL は小さい
        assert r.candidate_winner_nll < r.active_winner_nll


def test_flipped_rows_would_change_the_verdict_direction() -> None:
    """**mutation**: 行の向きを逆に組んだら判定の向きが変わることを実演する。

    「符号を取り違えても幅は同じに見える」を数値で示す — 幅はほぼ変わらないのに点推定だけが
    反転するので、幅を眺めていても気づけない。
    """
    rep = _run(0.70, 0.40)
    good = recompute(rep.evidence)
    flipped_by_day = {d: [-x for x in v] for d, v in diffs_by_day(rep.evidence.rows).items()}
    from horseracing_eval.bootstrap import race_day_cluster_bootstrap_ci_v1
    bad = race_day_cluster_bootstrap_ci_v1(
        flipped_by_day, b=rep.evidence.bootstrap["b"], seed=rep.evidence.bootstrap["seed"],
        alpha=rep.evidence.bootstrap["alpha"])
    assert good["point"] < 0 < bad.point
    good_width = good["sample_ci"]["ci_high"] - good["sample_ci"]["ci_low"]
    bad_width = bad.ci_high - bad.ci_low
    assert abs(good_width - bad_width) / good_width < 0.10  # 幅はほぼ同じ = 幅では気づけない
