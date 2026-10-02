"""138 T001: model.spec.json の来歴キー(既存キー不変・追加のみ)。"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "roi_explore"))
import direct_return_model as m  # noqa: E402

_PARAMS = {"seed": 7, "num_threads": 1, "deterministic": True, "force_row_wise": True}


def _spec(**kw):
    base = dict(objective="binary", feats=["odds", "q"], cats=["venue_code"], cat_maps={"venue_code": {"05": 0}},
                last_year=2026, params=_PARAMS, rounds=300, train_from=2007, drop_groups="weightlive,sameday",
                training_cutoff_by_year={2026: {"train_from": 2007, "train_through": 2025},
                                         2019: {"train_from": 2007, "train_through": 2018}},
                input_rows_sha256="abc")
    base.update(kw)
    return m.model_spec(**base)


def test_existing_keys_are_unchanged():
    s = _spec()
    assert {k: s[k] for k in ("objective", "features", "cats", "cat_maps", "trained_for_year", "train_through")} == {
        "objective": "binary", "features": ["odds", "q"], "cats": ["venue_code"], "cat_maps": {"venue_code": {"05": 0}},
        "trained_for_year": 2026, "train_through": 2025}


def test_provenance_keys():
    s = _spec()
    assert s["seed"] == 7 and s["rounds"] == 300 and s["num_threads"] == 1 and s["deterministic"] is True
    assert s["train_from"] == 2007 and s["drop_groups"] == ["sameday", "weightlive"]
    assert s["feature_hash"] == hashlib.sha256(json.dumps(["odds", "q", "venue_code"]).encode()).hexdigest()
    assert s["input_rows_sha256"] == "abc"
    assert list(s["training_cutoff_by_year"]) == ["2019", "2026"]  # 年の昇順・JSON 用に文字列キー
    assert s["training_cutoff_by_year"]["2026"] == {"train_from": 2007, "train_through": 2025}
    json.dumps(s)  # JSON にできる


def test_feature_hash_depends_on_order_and_members():
    assert _spec()["feature_hash"] != _spec(feats=["q", "odds"])["feature_hash"]
    assert _spec()["feature_hash"] != _spec(cats=[])["feature_hash"]


def test_non_deterministic_params_are_recorded_as_such():
    s = _spec(params={"seed": 1, "num_threads": 8})
    assert s["deterministic"] is False and s["num_threads"] == 8


def test_file_sha256_matches_hashlib(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"a" * (3 << 20) + b"tail")
    assert m.file_sha256(f) == hashlib.sha256(f.read_bytes()).hexdigest()
