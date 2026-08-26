"""094〜099 の凍結済み gate-config が、今のコードでも同じ hash になることを固定する(100 / T002).

feature 100 は評価契約に手を入れる。その最大のリスクは**過去の凍結成果物を静かに壊すこと**で、
経路は 2 つある。

1. ``gate_config_hash`` の入力に版の既定値を注入する → 凍結 hash が全部変わる(FR-002a)
2. ``EVALUATION_CONTRACT_VERSION`` を上げる → ``assert_confirmatory`` の**等値比較**により
   これら全ての config が即座に ``ConfirmatoryContractError`` になる(analyze C1 / FR-002b)

どちらも「テストは通るが本番が静かに壊れる」型なので、**過去の実物**を fixture として持ち込み、
hash と confirmatory 通過性の両方を守る。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from horseracing_eval.decision import (
    EVALUATION_CONTRACT_VERSION,
    ConfirmatoryContractError,
    assert_confirmatory,
    gate_config_hash,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "frozen_contracts"
INDEX = json.loads((FIXTURES / "index.json").read_text())


def _cfg(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.gate-config.json").read_text())


@pytest.mark.parametrize("name", sorted(INDEX))
def test_frozen_gate_config_hash_is_unchanged(name: str) -> None:
    """凍結時の hash が今のコードでも再現する(FR-002/FR-002a)。"""
    assert gate_config_hash(_cfg(name)) == INDEX[name]["expected_gate_config_hash"]


#: 各凍結 config が宣言している契約版。**094 だけが v3** である — v3→v4 の bump で 094 は
#: confirmatory 経路から締め出された。これは事故ではなく意図した挙動(「別のルールで判断された
#: 数値を黙って再判定すると verdict の不変性が壊れる」)だが、**同時に「版を上げると古い config が
#: 一斉に孤児になる」ことの実例**でもある。v5 に上げれば 095〜099 が同じ運命をたどる(analyze C1)。
DECLARED_VERSION = {
    "094-booster-capacity": "v3",
    "095-current-baseline-v4": "v4",
    "096-colsample-confirm": "v4",
    "097-early-mid-pace": "v4",
    "098-race-class-spelling": "v4",
    "099-margin-teacher-signal": "v4",
}


@pytest.mark.parametrize("name", sorted(INDEX))
def test_declared_contract_version_is_pinned(name: str) -> None:
    """どの config がどの版を宣言しているかを表として固定する。

    この表が動いたら、confirmatory 経路に入れる config の集合が変わったということである。
    """
    assert _cfg(name).get("evaluation_contract_version") == DECLARED_VERSION[name]


@pytest.mark.parametrize(
    "name", sorted(n for n, v in DECLARED_VERSION.items() if v == EVALUATION_CONTRACT_VERSION)
)
def test_current_version_configs_pass_confirmatory(name: str) -> None:
    """現行版を宣言する凍結 config は confirmatory ゲートを通り続ける(FR-002 / analyze C1)。

    ここが空集合になったら即座に赤くなる(下の ``test_...not_empty``)。空集合化は
    ``EVALUATION_CONTRACT_VERSION`` の bump で起きる — その瞬間に SC-002 は達成不能になる。

    直し方は「等値比較を下限に緩める」では**ない**。その等値比較は意図的な fail-closed で
    ある(FR-002b)。**判定ルールを変えないなら版を上げない**が正しい(FR-002c)。
    """
    cfg = _cfg(name)
    window = cfg.get("eval_window") or {}
    assert_confirmatory(
        cfg,
        expected_hash=INDEX[name]["expected_gate_config_hash"],
        eval_window={"from": window.get("from"), "to": window.get("to")},
    )


def test_at_least_one_frozen_config_is_on_the_current_contract() -> None:
    """**C1 の番人**: 版を上げると凍結 config が全部孤児になることを検出する。

    上の parametrize は該当ゼロだと「テストが 0 件通った」= 緑になってしまう。版 bump は
    まさにその形で静かに通り抜けるので、集合が空でないことを独立に主張する。
    """
    on_current = [n for n, v in DECLARED_VERSION.items() if v == EVALUATION_CONTRACT_VERSION]
    assert on_current, (
        f"どの凍結 config も現行版 {EVALUATION_CONTRACT_VERSION!r} を宣言していない。"
        "版を上げると assert_confirmatory の等値比較で 094〜099 が一斉に締め出され、"
        "SC-002(v4 の凍結 config の verdict がビット一致)が達成不能になる。"
        "US1/US4 は判定ルールを変えないので版を上げてはならない(FR-002c)。"
    )


def test_older_contract_config_fails_closed() -> None:
    """**意図的な fail-closed の実証**: 旧版を宣言する config は confirmatory で弾かれる。

    094 は v3 を宣言しており、v4 のコード下では通らない。これはバグではなく設計である。
    下限比較に緩める mutation はこのテストを落とす(FR-002b)。
    """
    stale = [n for n, v in DECLARED_VERSION.items() if v != EVALUATION_CONTRACT_VERSION]
    assert stale, "旧版を宣言する凍結 config が無く、fail-closed の実証ができない"
    name = sorted(stale)[0]
    cfg = _cfg(name)
    window = cfg.get("eval_window") or {}
    with pytest.raises(ConfirmatoryContractError, match="evaluation_contract_version"):
        assert_confirmatory(
            cfg,
            expected_hash=INDEX[name]["expected_gate_config_hash"],
            eval_window={"from": window.get("from"), "to": window.get("to")},
        )


def test_hash_would_change_if_defaults_were_injected() -> None:
    """**mutation**: hash を取る前に既定値を注入すると凍結 hash が壊れることを実演する。

    「既定値を補ってから hash する」は一見無害な整理に見えるが、凍結済みの 6 件を一度に
    無効化する。禁止が言葉だけで終わらないよう、破れることを実測で示しておく。
    """
    name = sorted(INDEX)[0]
    cfg = _cfg(name)
    frozen = INDEX[name]["expected_gate_config_hash"]
    assert gate_config_hash(cfg) == frozen

    injected = {"control_variate": None, "ensemble": None, **cfg}
    assert gate_config_hash(injected) != frozen


def test_verdict_contract_keys_are_unchanged() -> None:
    """verdict の契約キーが正本(specs/)と一致し続ける(FR-002)。"""
    repo = Path(__file__).resolve().parents[3]
    checked = 0
    for name, entry in sorted(INDEX.items()):
        if "verdict_keys" not in entry:
            continue
        frozen = json.loads((FIXTURES / entry["verdict_keys"]).read_text())
        live = json.loads((repo / entry["source_verdict"]).read_text())
        for key, want in frozen.items():
            got = live["primary"][key[len("primary_"):]] if key.startswith("primary_") \
                else live.get(key)
            assert json.loads(json.dumps(got, default=str)) == want, f"{name}.{key}"
        checked += 1
    assert checked >= 3


def test_unknown_hash_is_rejected() -> None:
    """hash 照合が実際に効いている(照合を素通りさせる mutation を落とす)。"""
    name = sorted(INDEX)[0]
    cfg = _cfg(name)
    window = cfg.get("eval_window") or {}
    with pytest.raises(ConfirmatoryContractError):
        assert_confirmatory(
            cfg, expected_hash="0" * 64,
            eval_window={"from": window.get("from"), "to": window.get("to")},
        )
