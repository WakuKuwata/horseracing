"""δ が測定ノイズから導出されていないことを実行時に強制する(100 / T025・T025a・FR-029/029a)。

「``sd_fold`` を変えても δ が動かない」は、δ が gate-config の literal である以上**すでに自明に
真**で、それだけのテストは何も守らない。実際の危険は「**次の凍結のときに人が `sd_fold` から
δ を導き直すこと**」であり、それを止めるには provenance を必須にするしかない。
"""

from __future__ import annotations

import json
import pathlib

import pytest

from horseracing_eval.delta_provenance import (
    DeltaProvenanceError,
    assert_delta_provenance,
)

REPO = pathlib.Path(__file__).resolve().parents[3]
DERIVATION = REPO / "specs" / "100-eval-contract-v5" / "delta-derivation.json"


def _cfg(**kw) -> dict:
    base = {
        "evaluation_contract_version": "v4",
        "min_effect_delta": 0.0035188338580500285,
        "delta_derivation_ref": "specs/100-eval-contract-v5/delta-derivation.json",
    }
    base.update(kw)
    return base


def test_valid_provenance_passes() -> None:
    assert_delta_provenance(_cfg(), root=REPO)


def test_missing_ref_fails_closed() -> None:
    cfg = _cfg()
    del cfg["delta_derivation_ref"]
    with pytest.raises(DeltaProvenanceError, match="delta_derivation_ref"):
        assert_delta_provenance(cfg, root=REPO)


def test_unresolvable_ref_fails_closed() -> None:
    with pytest.raises(DeltaProvenanceError, match="解決できない"):
        assert_delta_provenance(_cfg(delta_derivation_ref="specs/nope.json"), root=REPO)


def test_delta_must_match_the_derivation() -> None:
    """config の δ と導出結果が食い違ったら落とす(片方だけ手で書き換える事故)。"""
    with pytest.raises(DeltaProvenanceError, match="一致しない"):
        assert_delta_provenance(_cfg(min_effect_delta=0.002), root=REPO)


def test_seed_noise_derived_delta_is_rejected(tmp_path: pathlib.Path) -> None:
    """**本命**: `sd_fold` を入力に持つ導出は受理しない(FR-029a)。"""
    bad = json.loads(DERIVATION.read_text())
    bad["sd_fold"] = 0.001816              # 測定ノイズを入力に持ち込んだ
    bad["derived_delta"] = 0.001816
    p = tmp_path / "bad-derivation.json"
    p.write_text(json.dumps(bad, ensure_ascii=False))
    cfg = _cfg(min_effect_delta=0.001816, delta_derivation_ref=str(p))
    with pytest.raises(DeltaProvenanceError, match="sd_fold"):
        assert_delta_provenance(cfg, root=REPO)


def test_wrong_method_is_rejected(tmp_path: pathlib.Path) -> None:
    bad = json.loads(DERIVATION.read_text())
    bad["method"] = "measurement_noise"
    p = tmp_path / "d.json"
    p.write_text(json.dumps(bad, ensure_ascii=False))
    with pytest.raises(DeltaProvenanceError, match="method"):
        assert_delta_provenance(_cfg(delta_derivation_ref=str(p)), root=REPO)


def test_delta_does_not_move_when_sd_fold_moves() -> None:
    """SC-007: `sd_fold` を動かしても δ は動かない(安価な回帰)。"""
    cfg = _cfg()
    before = cfg["min_effect_delta"]
    for sd in (0.0, 0.0005, 0.001816, 0.01):
        cfg["seed_noise"] = {"sd_fold": sd, "k_seeds": 1}
        assert_delta_provenance(cfg, root=REPO)
        assert cfg["min_effect_delta"] == before


def test_shipped_derivation_records_the_rejected_alternatives() -> None:
    """FR-030a: 採らなかった導出が理由つきで残っている。"""
    d = json.loads(DERIVATION.read_text())
    rejected = d["rejected_derivations"]
    assert set(rejected) == {
        "measurement_noise", "compute_cost", "one_over_n_of_past_levers", "monetary_value"
    }
    for name, entry in rejected.items():
        assert entry.get("why_rejected"), name
