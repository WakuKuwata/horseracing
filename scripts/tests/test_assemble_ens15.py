"""138 T003: the ensemble assembler is fail-closed and writes relative paths + exact sha256."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "roi_explore"))
import assemble_ens15_20261001 as m  # noqa: E402

_SEEDS = (1, 2, 3)


def _spec(seed, **kw):
    s = {"objective": "binary", "features": ["odds"], "cats": ["venue_code"], "cat_maps": {"venue_code": {"05": 0}},
         "feature_hash": "fh", "input_rows_sha256": "rows", "rounds": 300, "train_from": 2007,
         "drop_groups": ["sameday", "weightlive"], "trained_for_year": 2026, "train_through": 2025,
         "training_cutoff_by_year": {"2026": {"train_from": 2007, "train_through": 2025}},
         "seed": seed, "num_threads": 1, "deterministic": True}
    s.update(kw)
    return s


def _build(tmp_path, *, spec_override=None, params_override=None, drop_year=None):
    ens = tmp_path / "ens"
    res = tmp_path / "results"
    for s in _SEEDS:
        d = ens / f"seed_{s:02d}"
        d.mkdir(parents=True)
        (d / "model.spec.json").write_text(json.dumps(_spec(s, **(spec_override or {}).get(s, {}))))
        for y in (2025, 2026):
            if drop_year == (s, y):
                continue
            (d / f"model_{y}.txt").write_text(f"booster seed={s} year={y}")
        r = res / m.result_tag(s)
        r.mkdir(parents=True)
        params = {"seed": s, "num_threads": 1, "deterministic": True} | (params_override or {}).get(s, {})
        (r / "result.json").write_text(json.dumps({"params": params}))
    return ens, res


def test_result_tag_rule():
    assert m.result_tag(1) == "armC_binary_drop-sameday+weightlive_from2007_ens15"
    assert m.result_tag(7) == "armC_binary_seed7_drop-sameday+weightlive_from2007_ens15"


def test_happy_path_writes_spec_and_manifests(tmp_path):
    ens, res = _build(tmp_path)
    out = m.assemble(ens, res, "mev-ens15-v1", seeds=_SEEDS)
    assert out["spec"]["members"] == ["seed_01", "seed_02", "seed_03"]
    assert out["spec"]["booster_years"] == [2025, 2026] and out["spec"]["version"] == "mev-ens15-v1"
    man = out["manifests"][2026]
    assert [x["path"] for x in man["members"]] == ["seed_01/model_2026.txt", "seed_02/model_2026.txt",
                                                    "seed_03/model_2026.txt"]
    assert man["members"][1]["sha256"] == hashlib.sha256(b"booster seed=2 year=2026").hexdigest()
    paths = m.write(ens, out)
    assert [p.name for p in paths] == ["ensemble.spec.json", "ensemble_2025.json", "ensemble_2026.json"]
    assert json.loads((ens / "ensemble.spec.json").read_text())["features"] == ["odds"]


@pytest.mark.parametrize(
    ("kw", "msg"),
    [
        (dict(spec_override={2: {"feature_hash": "other"}}), "feature_hash"),
        (dict(spec_override={3: {"cat_maps": {"venue_code": {"05": 1}}}}), "cat_maps"),
        (dict(spec_override={2: {"input_rows_sha256": "x"}}), "input_rows_sha256"),
        (dict(spec_override={2: {"num_threads": 8}}), "single-threaded"),
        (dict(params_override={3: {"deterministic": False}}), "deterministic"),
        (dict(params_override={2: {"seed": 9}}), "seed mismatch"),
        (dict(drop_year=(3, 2025)), "booster years"),
    ],
)
def test_fail_closed(tmp_path, kw, msg):
    ens, res = _build(tmp_path, **kw)
    with pytest.raises(m.AssembleError, match=msg):
        m.assemble(ens, res, "mev-ens15-v1", seeds=_SEEDS)
    assert not (ens / "ensemble.spec.json").exists()


def test_missing_member_is_an_error(tmp_path):
    ens, res = _build(tmp_path)
    with pytest.raises(m.AssembleError, match="missing"):
        m.assemble(ens, res, "mev-ens15-v1", seeds=(1, 2, 3, 4))
