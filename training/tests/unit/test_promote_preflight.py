"""昇格前確認: 切り替えた瞬間に本番の予測が止まる事故を構造的に止める。

[[model-artifact-outlived-by-row]] の実例 — worktree で登録した calibrator が消え、モデル行だけ
生き残って**本番の予測が全件停止した**。登録時のチェックでは捕まらない(置き場所が defect)。
active を切り替える瞬間は、その事故が起きる最後の分岐点。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from horseracing_features.registry import FEATURE_VERSION, model_input_features

from horseracing_training.artifacts import feature_hash
from horseracing_training.promote import _artifact_problems


@dataclass
class _Row:
    weights_uri: str | None
    calibrator_uri: str | None


def _artifact(tmp_path, *, fv=FEATURE_VERSION, fh=None, write_meta=True):
    d = tmp_path / "mv"
    d.mkdir(exist_ok=True)
    (d / "model.txt").write_text("x")
    (d / "calibrator.pkl").write_text("x")
    if write_meta:
        (d / "metadata.json").write_text(json.dumps({
            "feature_version": fv,
            "feature_hash": fh if fh is not None else feature_hash(model_input_features()),
        }))
    return _Row(str(d / "model.txt"), str(d / "calibrator.pkl"))


def test_a_servable_artifact_has_no_problems(tmp_path):
    assert _artifact_problems(_artifact(tmp_path), current_fv=FEATURE_VERSION) == []


def test_missing_artifact_files_are_caught(tmp_path):
    row = _artifact(tmp_path)
    (tmp_path / "mv" / "calibrator.pkl").unlink()
    problems = _artifact_problems(row, current_fv=FEATURE_VERSION)
    assert any("calibrator_uri のファイルが実在しない" in p for p in problems)


def test_relative_uris_are_caught(tmp_path):
    """ops predict は cwd=serving で shell out するので、裸相対パスは解決できない."""
    _artifact(tmp_path)
    problems = _artifact_problems(_Row("mv/model.txt", "mv/calibrator.pkl"),
                                 current_fv=FEATURE_VERSION)
    assert any("相対パス" in p for p in problems)


def test_feature_schema_mismatch_is_caught(tmp_path):
    """exact でも compat pin でもない artifact は serving でロードできない."""
    row = _artifact(tmp_path, fv="features-999", fh="deadbeef" * 8)
    problems = _artifact_problems(row, current_fv=FEATURE_VERSION)
    assert any("feature schema が serving に乗らない" in p for p in problems)


def test_missing_metadata_is_caught(tmp_path):
    row = _artifact(tmp_path, write_meta=False)
    assert any("metadata.json が無い" in p
               for p in _artifact_problems(row, current_fv=FEATURE_VERSION))
