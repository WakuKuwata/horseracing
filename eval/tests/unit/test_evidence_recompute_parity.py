"""証拠 artifact **だけ**から判定を再現できる(100 / T010・INV-A1・FR-008)。

これが US1 の中核要件である。判定 1 回は 2〜4 時間かかるので、「もう一度回さないと確かめられ
ない」構造は事後解析を不可能にする。ここが落ちたら、**要件を緩めるのではなく、再現に必要な
依存を artifact に足す**のが正しい対処。
"""

from __future__ import annotations

import datetime
import json

import pytest

from horseracing_eval.dataset import EvalRace, ScoringLabel
from horseracing_eval.evidence import (
    PairedEvidenceArtifact,
    assert_contract,
    recompute,
)
from horseracing_eval.paired import paired_eval
from horseracing_eval.predictor import HorseEntry, Prediction, RaceContext


class _FakePredictor:
    is_leaky_reference = False

    def __init__(self, w: float) -> None:
        self._w = w

    def fit(self, races):  # noqa: ARG002
        return None

    def predict_race(self, ctx):
        # レースごとに勝ち馬への確率を散らす。全レースで同じにすると per-race の差が定数になり、
        # bootstrap 区間が点に潰れて「alpha が効いているか」を検出できない。
        n = len(ctx.started_horses)
        jitter = (int(ctx.race_id[-2:]) % 7) * 0.02
        w = min(max(self._w + jitter - 0.06, 0.05), 0.9)
        rest = (1.0 - w) / (n - 1)
        return {
            h.horse_id: Prediction(win=(w if h.horse_id == "h0" else rest),
                                   top2=min(1.0, (w if h.horse_id == "h0" else rest) * 2),
                                   top3=min(1.0, (w if h.horse_id == "h0" else rest) * 3))
            for h in ctx.started_horses
        }


class _FakeFactory:
    def __init__(self, w: float, tag: str) -> None:
        self._w, self.recipe_meta, self.recipe_hash = w, {"tag": tag}, tag

    def fit(self, train_races, *, num_threads=None):  # noqa: ARG002
        return _FakePredictor(self._w)


def _races():
    out = []
    for year in (2020, 2021, 2022, 2023, 2024):
        for month in (3, 6, 9, 12):
            n = 4 + (month // 3)
            horses = tuple(HorseEntry(horse_id=f"h{i}", horse_number=i + 1) for i in range(n))
            ctx = RaceContext(race_id=f"R{year}{month:02d}",
                              race_date=datetime.date(year, month, 1), started_horses=horses)
            labels = (ScoringLabel("h0", 1, 1, 1), ScoringLabel("h1", 0, 1, 1),
                      ScoringLabel("h2", 0, 0, 1))
            out.append(EvalRace(context=ctx, labels=labels))
    return out


CFG = {
    "evaluation_contract_version": "v4",
    "seed_noise": {"sd_fold": 0.001816, "k_seeds": 1, "source": "test"},
    # alpha は**非既定値**にする。0.05 のままだと落としても既定に戻って同じ答えになり、
    # 「artifact に載せ忘れた」を検出できない(過去に gate-config の alpha を誰も読んで
    # いなかった実績があるので、実際に効いていることまで見る)。
    "bootstrap": {"b": 300, "seed": 4242, "alpha": 0.10},
}


def _report():
    return paired_eval(_FakeFactory(0.62, "cand"), _FakeFactory(0.45, "act"), _races(),
                       gate_config=CFG, first_valid_year=2020)


def test_report_always_carries_evidence() -> None:
    """FR-006: 証拠は必ず付く(オプションにしない)。"""
    rep = _report()
    assert rep.evidence is not None
    assert len(rep.evidence.rows) == rep.n_eligible > 0


def test_evidence_row_count_matches_the_judged_population() -> None:
    """INV-E1 を実データ経路で。"""
    rep = _report()
    assert_contract(rep.evidence, n_races=rep.n_eligible)


def test_recompute_from_evidence_is_bit_identical(  ) -> None:
    """**INV-A1**: 証拠だけから点推定・sampling CI・total CI を再計算してビット一致。"""
    rep = _report()
    got = recompute(rep.evidence)
    assert got["point"] == rep.bootstrap_ci["point"]
    assert got["sample_ci"] == rep.bootstrap_ci
    assert got["total_ci"] == rep.total_ci


def test_recompute_survives_a_json_round_trip() -> None:
    """artifact をファイルに落として読み直しても再現する(実運用の経路)。"""
    rep = _report()
    back = PairedEvidenceArtifact.from_dict(json.loads(json.dumps(rep.evidence.to_dict())))
    assert recompute(back)["sample_ci"] == rep.bootstrap_ci
    assert recompute(back)["total_ci"] == rep.total_ci


def test_recompute_is_row_order_independent() -> None:
    """INV-E6: ファイル上の行順を入れ替えても同じ答えになる。"""
    rep = _report()
    d = rep.evidence.to_dict()
    d["rows"] = list(reversed(d["rows"]))
    assert recompute(PairedEvidenceArtifact.from_dict(d))["sample_ci"] == rep.bootstrap_ci


def test_recompute_needs_no_model_or_db() -> None:
    """artifact だけを手で組んでも再現できる(モデルにも DB にも触らない)。"""
    rep = _report()
    rebuilt = PairedEvidenceArtifact.from_dict(json.loads(json.dumps(rep.evidence.to_dict())))
    assert recompute(rebuilt)["n_races"] == rep.n_eligible


def test_bootstrap_parameters_are_carried_not_defaulted() -> None:
    """再現に必要な b/seed/alpha が artifact 側にある(既定値で誤魔化さない)。"""
    ev = _report().evidence
    assert ev.bootstrap == {"b": 300, "seed": 4242, "alpha": 0.10, "block": "race_day"}
    assert ev.seed_noise["sd_fold"] == 0.001816


def test_wrong_bootstrap_seed_changes_the_interval() -> None:
    """seed が artifact に載っていることが実際に効いている(載せ忘れの検出)。"""
    rep = _report()
    d = rep.evidence.to_dict()
    d["bootstrap"] = {**d["bootstrap"], "seed": d["bootstrap"]["seed"] + 1}
    assert recompute(PairedEvidenceArtifact.from_dict(d))["sample_ci"] != rep.bootstrap_ci


def test_evidence_declares_the_arms_and_window_it_came_from() -> None:
    """どの 2 アームの、どの窓の差なのかが artifact 単体で分かる。"""
    ev = _report().evidence
    assert ev.candidate_recipe_hash == "cand" and ev.active_recipe_hash == "act"
    assert ev.window["to"] == "2024-12-01"
    assert ev.evaluation_contract_version == "v4"
    assert ev.artifact_kind == "paired_evidence" and ev.eligible_for_verdict is False


def test_diffs_by_day_is_derived_from_the_same_rows() -> None:
    """097 の ``diffs_by_day`` が証拠から導出されている(二重構築が無い)。"""
    rep = _report()
    from horseracing_eval.evidence import diffs_by_day
    assert rep.diffs_by_day == diffs_by_day(rep.evidence.rows)


@pytest.mark.parametrize("drop", ["b", "seed", "alpha"])
def test_missing_bootstrap_parameter_falls_back_visibly(drop: str) -> None:
    """欠けたパラメータは既定値で埋まる = 再現が静かにずれる。**載せ忘れを検出する**。"""
    rep = _report()
    d = rep.evidence.to_dict()
    d["bootstrap"] = {k: v for k, v in d["bootstrap"].items() if k != drop}
    assert recompute(PairedEvidenceArtifact.from_dict(d))["sample_ci"] != rep.bootstrap_ci
