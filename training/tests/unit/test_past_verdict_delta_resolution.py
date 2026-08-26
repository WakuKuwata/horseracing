"""過去 verdict の δ は**当時の**出所から解決する(100 / T026・FR-031a・INV-D3)。

過去 verdict は当時の δ で判断されている。今の δ で読み直すと採否の意味が変わり、新旧の
ADOPT 率を比べても無意味になる。だから「解決できなければ現行値で補う」は禁止で、
**解決できなければ落とす**。
"""

from __future__ import annotations

import json
import pathlib

import pytest

from horseracing_training.frozen_configs import (
    FrozenConfigNotFound,
    resolve_delta_for_verdict,
    resolve_frozen_config,
)

REPO = pathlib.Path(__file__).resolve().parents[3]

#: 実在する凍結 config の hash(feature 100 T001 の fixture と同じ出所)。
KNOWN = json.loads(
    (REPO / "eval/tests/fixtures/frozen_contracts/index.json").read_text()
)


@pytest.mark.parametrize("name", sorted(KNOWN))
def test_every_frozen_config_resolves_by_hash(name: str) -> None:
    f = resolve_frozen_config(KNOWN[name]["expected_gate_config_hash"], root=REPO)
    assert f.feature == name
    assert f.path.startswith("specs/")


def test_resolved_config_carries_the_delta_that_judged_it() -> None:
    f = resolve_frozen_config(KNOWN["097-early-mid-pace"]["expected_gate_config_hash"], root=REPO)
    assert f.min_effect_delta == 0.002
    assert f.contract_version == "v4"


def test_unknown_hash_fails_closed_instead_of_using_the_current_delta() -> None:
    """**本命**: 解決できないときに現行 δ で補ってはならない。"""
    with pytest.raises(FrozenConfigNotFound, match="補ってはならない"):
        resolve_frozen_config("0" * 64, root=REPO)


def test_empty_hash_fails_closed() -> None:
    with pytest.raises(FrozenConfigNotFound):
        resolve_frozen_config("", root=REPO)


def test_verdict_delta_prefers_its_own_record_then_the_frozen_config() -> None:
    h = KNOWN["098-race-class-spelling"]["expected_gate_config_hash"]
    # verdict 自身が δ を持つならそれが正
    assert resolve_delta_for_verdict({"min_effect_delta": 0.0042, "gate_config_hash": h},
                                     root=REPO) == 0.0042
    # 持たないなら当時の凍結 config から引く
    assert resolve_delta_for_verdict({"gate_config_hash": h}, root=REPO) == 0.002


def test_verdict_without_any_resolvable_delta_fails_closed() -> None:
    with pytest.raises(FrozenConfigNotFound):
        resolve_delta_for_verdict({"gate_config_hash": "deadbeef"}, root=REPO)


def test_a_new_delta_does_not_leak_into_past_verdicts() -> None:
    """INV-D2: 新しい δ を凍結しても、過去 verdict の解決結果は動かない。"""
    new = json.loads((REPO / "specs/100-eval-contract-v5/delta-derivation.json").read_text())
    assert new["derived_delta"] != 0.002  # 新旧が別値であることが前提
    for name in ("097-early-mid-pace", "098-race-class-spelling"):
        h = KNOWN[name]["expected_gate_config_hash"]
        assert resolve_delta_for_verdict({"gate_config_hash": h}, root=REPO) == 0.002
