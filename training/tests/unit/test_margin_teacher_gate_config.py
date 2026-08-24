"""099 T016-T018: ゲート配線の単体テスト — carriage・凍結整合・アーム同一性・構造 assert。

069 の drop=、099 analyze の wmask=/params と、OOF 分岐は「セグメントを捨てる」前歴が
2 度ある。ここは spec 文字列 → 実効 recipe の全フィールド保持と、凍結 gate-config が
実行に本当に届くことを機械で固定する。
"""

from __future__ import annotations

import json
import pathlib

import pytest
from horseracing_eval.decision import gate_config_hash

from horseracing_training.cli import (
    _assert_arm_identity,
    _assert_gate_arms,
    _factory_from_spec,
    _gate_arm_overrides,
    _post_run_structure_check,
    _recipe_from_spec,
)

REPO = pathlib.Path(__file__).resolve().parents[3]
GATE_CONFIG = REPO / "specs" / "099-margin-teacher-signal" / "gate-config.json"
#: contracts/adoption-gate.md の凍結値(完全 64 桁・prefix 比較禁止 = analyze M4)
FROZEN_HASH = "d8c479dea834a22e4b27030d4558e9b1cc2e120639fbb29c32a8955331d098b7"


def _cfg() -> dict:
    return json.loads(GATE_CONFIG.read_text())


# --- T018: 凍結整合 -----------------------------------------------------------------------

def test_gate_config_hash_matches_the_frozen_value():
    assert gate_config_hash(_cfg()) == FROZEN_HASH


def test_frozen_variant_maps_to_the_implementation_constants():
    """config は M0/GMIN を持たない(FR-002 の非露出)— "v1" が実装の凍結定数を指す。"""
    from horseracing_training.dataset import MARGIN_GMIN, MARGIN_M0

    cfg = _cfg()
    assert cfg["arms"]["margin_teacher_candidate"] == "v1"
    assert cfg["arms"]["margin_teacher_active"] is None
    assert MARGIN_M0 == 0.2 and MARGIN_GMIN == 0.25  # spike の事前選択値


def test_gate_config_has_v4_required_shape():
    cfg = _cfg()
    assert cfg["evaluation_contract_version"] == "v4"
    assert cfg["seed_noise"]["sd_fold"] == 0.001816
    assert cfg["eval_window"]["from"] == "2019-01-01"
    assert cfg["eval_window"]["min_eval_days"] == 400
    assert cfg["arm_identity"]["require_distinct_recipe_hash"] is True


# --- T016: spec 文字列 → 実効 recipe の carriage -------------------------------------------

FULL_SPEC = "pl_topk:oof_isotonic:mteach=v1:wmask=0.5/20260810"


def test_recipe_spec_parses_mteach():
    r = _recipe_from_spec("pl_topk:isotonic:0.3:mteach=v1")
    assert r.margin_teacher == "v1"
    with pytest.raises(ValueError, match="margin_teacher"):
        _recipe_from_spec("pl_topk:isotonic:0.3:mteach=v2")


def test_oof_branch_carries_every_segment():
    """OOF 分岐がセグメントを捨てない(drop= を捨てた 069・wmask=/params を捨てていた
    analyze C1 の再発防止)。session は factory 構築では未使用なので None で足りる。"""
    f = _factory_from_spec(None, FULL_SPEC)
    r = f.recipe
    assert r.margin_teacher == "v1"
    assert (r.weight_mask_rate, r.weight_mask_seed) == (0.5, 20260810)
    assert f.method == "isotonic"


def test_oof_branch_applies_arm_overrides():
    """凍結アーム構成(rounds/n_oof_blocks/seed)が factory に届く(analyze C1 の本丸)。"""
    ov = {"n_estimators": 900, "n_oof_blocks": 8, "seed": 42,
          "weight_mask_rate": 0.5, "weight_mask_seed": 20260810}
    f = _factory_from_spec(None, "pl_topk:oof_isotonic:mteach=v1", arm_overrides=ov)
    assert dict(f.recipe.params or ())["n_estimators"] == 900
    assert f.n_oof_blocks == 8
    assert f.recipe.seed == 42
    assert (f.recipe.weight_mask_rate, f.recipe.weight_mask_seed) == (0.5, 20260810)


def test_oof_branch_receives_snapshot_pin():
    """--pin-snapshot が OOF アームで黙って無効化されない(analyze 2 周目 C1)。"""
    f = _factory_from_spec(
        None, "pl_topk:oof_isotonic", use_materialized=True,
        materialized_path="/tmp/x.parquet", pin_snapshot=True,
    )
    assert f.use_materialized is True
    assert f.materialized_path == "/tmp/x.parquet"
    assert f.pin_snapshot is True


# --- T016a/T017: 注入と実行前検査 ----------------------------------------------------------

def test_overrides_confirmatory_reads_arms_and_smoke_reads_smoke():
    cfg = _cfg()
    conf = _gate_arm_overrides(cfg, confirmatory=True)
    assert conf["n_estimators"] == 900 and conf["n_oof_blocks"] == 8
    smoke = _gate_arm_overrides(cfg, confirmatory=False)
    assert smoke == {"n_estimators": 50}
    assert _gate_arm_overrides(None, confirmatory=True) is None


def test_effective_recipe_mismatch_fails_closed():
    cfg = _cfg()
    ov = _gate_arm_overrides(cfg, confirmatory=True)
    good = _factory_from_spec(None, "pl_topk:oof_isotonic:mteach=v1", arm_overrides=ov)
    _assert_gate_arms(good, cfg["arms"], role="candidate")  # 一致 = 例外なし

    bad = _factory_from_spec(None, "pl_topk:oof_isotonic:mteach=v1")  # 注入漏れの形
    with pytest.raises(SystemExit, match="EFFECTIVE recipe"):
        _assert_gate_arms(bad, cfg["arms"], role="candidate")

    wrong_teacher = _factory_from_spec(None, "pl_topk:oof_isotonic", arm_overrides=ov)
    with pytest.raises(SystemExit, match="margin_teacher"):
        _assert_gate_arms(wrong_teacher, cfg["arms"], role="candidate")


def test_identical_arms_fail_before_running():
    """セグメント欠落は両アーム同一 = 全ゼロレポートの故障。実行前に止める。"""
    cfg = _cfg()
    a = _factory_from_spec(None, "pl_topk:oof_isotonic")
    b = _factory_from_spec(None, "pl_topk:oof_isotonic")
    with pytest.raises(SystemExit, match="IDENTICAL recipe_hash"):
        _assert_arm_identity(a, b, cfg, confirmatory=False)


def test_confounded_arms_fail_in_confirmatory():
    cfg = _cfg()
    cand = _factory_from_spec(None, "pl_topk:oof_isotonic:mteach=v1")
    confounded = _factory_from_spec(None, "pl_topk:oof_isotonic:wmask=0.5/20260810")
    with pytest.raises(SystemExit, match="EXACTLY"):
        _assert_arm_identity(cand, confounded, cfg, confirmatory=True)
    clean_active = _factory_from_spec(None, "pl_topk:oof_isotonic")
    _assert_arm_identity(cand, clean_active, cfg, confirmatory=True)  # 差 = teacher のみ


# --- T017a: 実行後の構造 assert -------------------------------------------------------------

class _Report:
    def __init__(self, diffs):
        self.diffs_by_day = diffs


class _Pred:
    def __init__(self, fit_info):
        self.fit_info_ = fit_info


class _Factory:
    def __init__(self, meta, fit_info):
        self.recipe_meta = meta
        self._pred = _Pred(fit_info)


_GOOD_STATS = {
    "margin_teacher": {
        "s2": {"scale_lt1_races": 10, "fireable_mean": 0.7},
        "s3": {"scale_lt1_races": 12, "fireable_mean": 0.66},
    }
}


def test_all_zero_diffs_are_a_failure_not_a_result():
    cfg = _cfg()
    rep = _Report({"2026-01-01": [0.0, 0.0], "2026-01-02": [0.0]})
    f = _Factory({"margin_teacher": "v1"}, _GOOD_STATS)
    err = _post_run_structure_check(rep, f, cfg)
    assert err and "nonzero" in err


def test_silent_no_op_is_caught_after_the_run():
    cfg = _cfg()
    rep = _Report({"2026-01-01": [0.001, -0.002]})
    no_stats = _Factory({"margin_teacher": "v1"}, {})
    assert "silently ignored" in _post_run_structure_check(rep, no_stats, cfg)
    neutral = _Factory({"margin_teacher": "v1"}, {
        "margin_teacher": {"s2": {"scale_lt1_races": 0}, "s3": {"scale_lt1_races": 5}}})
    assert "never fired" in _post_run_structure_check(rep, neutral, cfg)


def test_healthy_run_passes_the_structure_check():
    cfg = _cfg()
    rep = _Report({"2026-01-01": [0.001, -0.002]})
    f = _Factory({"margin_teacher": "v1"}, _GOOD_STATS)
    assert _post_run_structure_check(rep, f, cfg) is None
