"""gate-config の hash が「書かれたまま」を対象にすることを固定する(100 / T006-T007).

凍結 config の digest は、事前登録と事後の書き換えを分ける唯一の境界である。hash を取る前に
何かを補うと、**凍結済みの全件が一度に無効になる**。禁止を言葉で終わらせず、破れることを
実測で示す。

なお「v5 で必須のブロックが欠けている config を fail-closed にする」検証は、**v5 が導入される
まで対象が存在しない**ため Phase 6 に送っている(v5 の必須ブロックは ``ensemble`` のみ・
FR-002c・analyze U1)。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from horseracing_eval.decision import gate_config_hash

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "frozen_contracts"
INDEX = json.loads((FIXTURES / "index.json").read_text())


def _any_frozen() -> tuple[dict, str]:
    name = sorted(INDEX)[0]
    cfg = json.loads((FIXTURES / f"{name}.gate-config.json").read_text())
    return cfg, INDEX[name]["expected_gate_config_hash"]


def test_comment_keys_are_ignored() -> None:
    """``_``前置キーは digest に入らない(凍結後にコメントを直せる)。"""
    cfg, frozen = _any_frozen()
    assert gate_config_hash({**cfg, "_note": "後から書いた注記"}) == frozen


def test_nested_comment_keys_are_ignored() -> None:
    cfg, frozen = _any_frozen()
    win = dict(cfg.get("eval_window") or {})
    win["_why"] = "窓の理由"
    assert gate_config_hash({**cfg, "eval_window": win}) == frozen


#: (補おうとする既定値, 凍結 6 件のうち digest が動く件数)
#:
#: **危険は「どの config も持っていないキー」で最大になる**。全件が既に持っているキー
#: (``min_effect_delta``)を補っても no-op なので、そこだけ見て「defaults 注入は無害」と
#: 結論しないための対照として一緒に並べてある。``seed_noise`` は 094 だけが持たないので
#: 1 件動く — つまり**たった 1 件の古い config のために全体の規約が要る**。
INJECTION_CASES = [
    ({"ensemble": None}, 6),                                   # v5 の必須ブロック候補
    ({"control_variate": None}, 6),                            # US2 の遺物(実装しない)
    ({"seed_noise": {"sd_fold": 0.001816, "k_seeds": 1}}, 1),  # 094 のみ欠落
    ({"min_effect_delta": 0.002}, 0),                          # 全件が保持 = 対照
]


@pytest.mark.parametrize(
    ("injected", "expected_moved"), INJECTION_CASES,
    ids=["v5-ensemble", "dead-control-variate", "seed-noise", "control-already-present"],
)
def test_injecting_defaults_breaks_frozen_digests(injected: dict, expected_moved: int) -> None:
    """**mutation**: 既定値を補ってから hash すると凍結 digest が動く(FR-002a)。

    「どうせ同じ値だから補っても無害」は、**そのキーを持たない config** を巻き添えにする。
    件数まで pin してあるので、凍結 config が増減したらここが落ちて棚卸しを促す。
    """
    moved = 0
    for name, entry in sorted(INDEX.items()):
        cfg = json.loads((FIXTURES / f"{name}.gate-config.json").read_text())
        # 既にあるキーは上書きせず、「欠けている config に補う」動きだけを再現する
        filled = {**injected, **cfg}
        if gate_config_hash(filled) != entry["expected_gate_config_hash"]:
            moved += 1
    assert moved == expected_moved, (
        f"{injected} の注入で digest が動いた件数 ={moved}(期待 {expected_moved})。"
        "凍結 config の集合が変わったか、hash の対象が変わった。"
    )


def test_hash_is_deterministic_across_key_order() -> None:
    """dict のキー順で digest が動かない(JSON 正準化が効いている)。"""
    cfg, frozen = _any_frozen()
    shuffled = dict(reversed(list(cfg.items())))
    assert gate_config_hash(shuffled) == frozen


def test_empty_and_none_hash_to_the_same_thing() -> None:
    """欠落を「空 config」として扱う挙動を pin する(fail-closed は呼び出し側の責務)。"""
    assert gate_config_hash({}) == gate_config_hash(None)


def test_value_change_moves_the_digest() -> None:
    """当たり前だが要である: 値を変えれば digest は動く(凍結が機能している)。"""
    cfg, frozen = _any_frozen()
    tampered = {**cfg, "min_effect_delta": 0.0005}
    assert gate_config_hash(tampered) != frozen
