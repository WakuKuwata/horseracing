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


# --- 昇格前確認と serving の判定が食い違わないこと(2026-08-23) --------------------------------
#
# 実害の記録: 昇格前確認は hash 一致だけで通していたのに、serving の loader は
# 「hash 一致 **かつ** feature_version 一致」を要求していた。feature_hash は列名の並びしか
# 見ないので、列名を変えずに値の意味だけ変える bump(017 の class_transition/track_type、
# 098 の race_class 正準化)では hash が動かない。結果、古い版を名乗る artifact が昇格を通り、
# 直後に loader に蹴られて本番の予測が全件停止した(2026-07: predict ジョブ 37/37 失敗)。

def test_hash_matches_but_feature_version_is_older_is_caught(tmp_path):
    """列名 hash は一致するが版が古い artifact = 値の意味が違う。昇格させてはならない。

    これがドリフトの本体。旧実装は hash 一致だけを見ていたのでここを素通りさせた。
    """
    row = _artifact(tmp_path, fv="features-018")  # fh は現行の正しい hash のまま
    problems = _artifact_problems(row, current_fv=FEATURE_VERSION)
    assert any("feature schema が serving に乗らない" in p for p in problems)


def test_metadata_without_a_feature_hash_is_caught(tmp_path):
    """hash 欠落 + 未 pin の版。旧 loader は None != None が False で素通りさせていた。"""
    d = tmp_path / "mv"
    d.mkdir(exist_ok=True)
    (d / "model.txt").write_text("x")
    (d / "calibrator.pkl").write_text("x")
    (d / "metadata.json").write_text(json.dumps({"feature_version": "features-999"}))
    row = _Row(str(d / "model.txt"), str(d / "calibrator.pkl"))
    assert any("feature schema が serving に乗らない" in p
               for p in _artifact_problems(row, current_fv=FEATURE_VERSION))


def test_preflight_and_serving_agree_on_every_case():
    """昇格前確認と serving が同一述語を使っていることを、境界ケースで機械的に固定する。

    片方だけ直しても緑にならないよう、**serving の分岐を再実装せず** resolve_servability の
    結果そのものを表明する。ここが割れたら、割れた瞬間に落ちる。
    """
    from horseracing_training.artifacts import resolve_servability

    current = feature_hash(model_input_features())
    cases = {
        # (trained_fv, trained_hash) -> servable であるべきか
        (FEATURE_VERSION, current): True,        # exact
        ("features-018", current): False,        # 同一列名・別版 = 値の意味が違う(本ドリフト)
        (FEATURE_VERSION, "deadbeef" * 8): False,  # 同一版・列 subset(drop_features ablation)
        ("features-999", "deadbeef" * 8): False,   # 未知の版
        ("features-999", None): False,             # hash 欠落 + 未 pin(None == None の穴)
    }
    for (fv, fh), expected in cases.items():
        assert resolve_servability(fv, fh).servable is expected, (fv, fh)

    # exact は「hash 一致 かつ 版一致」の両方を要求する(片方だけでは compat 扱い/不可)
    assert resolve_servability(FEATURE_VERSION, current).exact is True
    assert resolve_servability("features-018", current).exact is False


def test_a_genuinely_pinned_prior_version_still_passes():
    """厳しくした結果 compat 経路(= rollback 経路)を潰していないことを示す。

    pin 表の実値をそのまま引く。「厳しくした」だけを固定して「通るべきものが通る」を
    固定しないと、rollback できない状態に静かに縛られる。
    """
    from horseracing_features.registry import COMPATIBLE_PRIOR_FEATURE_VERSIONS

    from horseracing_training.artifacts import resolve_servability

    pins = COMPATIBLE_PRIOR_FEATURE_VERSIONS.get(FEATURE_VERSION) or {}
    assert pins, f"{FEATURE_VERSION} に compat pin が 1 件も無い(この表明の前提が崩れている)"
    for prior_fv, pinned_hash in pins.items():
        v = resolve_servability(prior_fv, pinned_hash)
        assert v.servable is True, prior_fv      # compat 経路で serve できる
        assert v.exact is False, prior_fv        # ただし exact ではない(列を選択して読む)
        # pin された版でも hash が違えば閉じる(pin は版ではなく hash に対して効く)
        assert resolve_servability(prior_fv, "deadbeef" * 8).servable is False, prior_fv


# --- 昇格記録の組み立て(2026-08-18) ---------------------------------------------------------

def _record(basis="v3_verdict", **over):
    r = {"promoted_at": "2026-08-18T09:00:00", "basis": basis, "override_reason": None,
         "v3_verdict": {"status": "ADOPT"}, "previous_active": "lgbm-old",
         "git_sha": "abc123", "rollback_command": "..."}
    r.update(over)
    return r


def test_registration_time_decision_does_not_survive_into_the_promotion_record():
    """実際に起きた不備: 登録時に save_model_version が書いた
    `reasons: {"cause": "register_as_candidate_requested"}` がマージで生き残り、昇格後の行に
    `basis: "v3_verdict"` と並んでいた。昇格自体は正しくても監査欄に矛盾する根拠が 2 つ並ぶ。"""
    from horseracing_training.promote import merged_promotion_record

    prior = {"basis": None, "promotable": False, "status": "candidate",
             "reasons": {"cause": "register_as_candidate_requested",
                         "legacy_gate_adopted": False},
             "v3_verdict": None, "git_sha": "old000"}
    out = merged_promotion_record(prior, _record())

    assert out["basis"] == "v3_verdict"
    assert out["status"] == "active" and out["promotable"] is True
    assert "reasons" not in out                      # 登録時の判定は最上位に残さない
    assert out["git_sha"] == "abc123"                # 昇格時の値で上書きされる
    # provenance は捨てずに退避する
    assert out["registration"]["reasons"]["cause"] == "register_as_candidate_requested"


def test_re_promotion_does_not_nest_the_registration_record():
    """戻して再度昇格しても registration が入れ子にならない(元の登録記録が保たれる)。"""
    from horseracing_training.promote import merged_promotion_record

    first = merged_promotion_record(
        {"reasons": {"cause": "register_as_candidate_requested"}}, _record())
    second = merged_promotion_record(first, _record(basis="override",
                                                    override_reason="rollback"))
    assert second["basis"] == "override"
    assert second["registration"] == {"reasons": {"cause": "register_as_candidate_requested"}}
    assert "registration" not in second["registration"]


def test_no_registration_key_when_there_was_nothing_prior():
    from horseracing_training.promote import merged_promotion_record

    assert "registration" not in merged_promotion_record(None, _record())
    assert "registration" not in merged_promotion_record({}, _record())


# --- 適用集合の race_id 一覧の読み込み(2026-08-18) -------------------------------------------

def test_opportunity_race_list_parsing_and_provenance(tmp_path):
    """マスクは判定の入力になるので、**どのファイルを読んだか**を残す。事後に差し替えられては
    事前登録の意味が無い。"""
    from horseracing_training.cli import _load_opportunity_races

    f = tmp_path / "mask.txt"
    f.write_text("# opportunity set: X が全馬非欠損\n# coverage 0.25\nR001\nR002\n\n  R003  \n")
    ids, prov = _load_opportunity_races(str(f))
    assert ids == {"R001", "R002", "R003"}          # コメントと空行は無視、前後空白は落ちる
    assert prov["n_race_ids"] == 3
    assert len(prov["sha256"]) == 64
    assert prov["path"] == str(f)

    # 内容が 1 文字でも変われば hash が動く
    f.write_text("R001\nR002\nR003\nR004\n")
    _, prov2 = _load_opportunity_races(str(f))
    assert prov2["sha256"] != prov["sha256"]


def test_no_mask_gives_no_provenance():
    from horseracing_training.cli import _load_opportunity_races

    assert _load_opportunity_races(None) == (None, {})


def test_empty_mask_file_is_refused(tmp_path):
    """空のマスクで走らせると『適用集合ゼロ』を黙って測ることになる。"""
    import pytest

    from horseracing_training.cli import _load_opportunity_races

    f = tmp_path / "empty.txt"
    f.write_text("# コメントだけ\n\n")
    with pytest.raises(SystemExit, match="empty"):
        _load_opportunity_races(str(f))
