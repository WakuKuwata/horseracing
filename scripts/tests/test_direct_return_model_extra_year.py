"""D17 (2026-10-28): ``--extra-booster-year`` trains the coming season's booster without changing
anything the run produced before (the yearly loop skips a year that has no rows, so in December
the booster for January cannot otherwise be made). Synthetic rows, real LightGBM fits, two runs
compared byte for byte."""
import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "roi_explore"))
import direct_return_model as m  # noqa: E402

TRAIN_YEARS = (2019, 2020)
TEST_YEAR = 2021
ROUNDS = 4


def _rows(path: Path, *, n_train_per_year: int = 26_000, n_test: int = 2_000) -> None:
    """All NUM + CAT columns plus the bookkeeping columns main() reads. 52k training rows clear the
    loop's 50,000-row floor; the test year is small so the diagnostics stay quick."""
    rng = np.random.default_rng(20261028)
    parts = []
    for year, n in [(y, n_train_per_year) for y in TRAIN_YEARS] + [(TEST_YEAR, n_test)]:
        n_races = max(1, n // 10)
        race_ids = np.repeat(np.arange(n_races), 10)[:n]
        df = pd.DataFrame({c: rng.normal(size=n) for c in m.NUM})
        df["odds"] = rng.uniform(1.1, 60.0, size=n)
        df["q"] = 1.0 / df["odds"]
        df["q"] = df["q"] / df.groupby(race_ids)["q"].transform("sum")
        for c in m.CAT:
            df[c] = rng.choice(["a", "b", "c"], size=n).astype(object)
        df["race_id"] = [f"{year}{r:08d}" for r in race_ids]
        df["horse_id"] = [f"h{i:07d}" for i in rng.integers(0, 10**7, size=n)]
        df["horse_number"] = (np.arange(n) % 10) + 1
        day = (race_ids % 40) + 1
        df["race_date"] = [f"{year}-{1 + d // 28:02d}-{1 + d % 28:02d}" for d in day]
        df["year"] = year
        won = np.zeros(n, dtype=bool)
        won[np.unique(race_ids, return_index=True)[1]] = True  # one winner per race
        df["won"] = won
        df["race_ok"] = True
        df["dead_heat"] = False
        parts.append(df)
    pd.concat(parts, ignore_index=True).to_parquet(path, index=False)


def _run(tmp: Path, rows: Path, name: str, extra: list[str]) -> Path:
    prefix = tmp / name / "model"
    argv = ["--rows", str(rows), "--objective", "binary", "--train-from", "2019", "--start-year", str(TEST_YEAR),
            "--end-year", str(TEST_YEAR), "--rounds", str(ROUNDS), "--threads", "1", "--deterministic", "--seed", "3",
            "--boot", "20", "--tag", f"_{name}", "--save-last-model", str(prefix)] + extra
    assert m.main(argv) == 0
    return prefix


@pytest.fixture(scope="module")
def runs(tmp_path_factory, monkeypatch_module):
    tmp = tmp_path_factory.mktemp("d17")
    monkeypatch_module.setattr(m, "ART", tmp / "art")  # result.json / predictions.parquet land here
    rows = tmp / "rows.parquet"
    _rows(rows)
    base = _run(tmp, rows, "base", [])
    extra = _run(tmp, rows, "extra", ["--extra-booster-year", str(TEST_YEAR + 1)])
    return tmp, base, extra


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def test_default_outputs_are_byte_identical(runs):
    tmp, base, extra = runs
    for suffix in (".txt", f"_{TEST_YEAR}.txt"):
        assert (base.parent / ("model" + suffix)).read_bytes() == (extra.parent / ("model" + suffix)).read_bytes()
    res = tmp / "art" / "results"
    a, b = res / "armC_binary_seed3_from2019_base", res / "armC_binary_seed3_from2019_extra"
    assert (a / "predictions.parquet").read_bytes() == (b / "predictions.parquet").read_bytes()
    ra, rb = json.loads((a / "result.json").read_text()), json.loads((b / "result.json").read_text())
    for r in (ra, rb):
        r.pop("elapsed_s")
        r.pop("tag")
    assert ra == rb


def test_extra_booster_is_saved_and_recorded(runs):
    tmp, base, extra = runs
    extra_year = TEST_YEAR + 1
    assert not (base.parent / f"model_{extra_year}.txt").exists()
    bst = lgb.Booster(model_file=str(extra.parent / f"model_{extra_year}.txt"))
    assert bst.num_trees() == ROUNDS
    spec_b = json.loads((base.parent / "model.spec.json").read_text())
    spec_e = json.loads((extra.parent / "model.spec.json").read_text())
    assert "extra_booster_year" not in spec_b
    assert spec_e.pop("extra_booster_year") == extra_year
    assert spec_e["training_cutoff_by_year"].pop(str(extra_year)) == {"train_from": 2019, "train_through": TEST_YEAR}
    assert spec_e == spec_b  # trained_for_year / train_through / feature_hash / … untouched
    assert spec_b["trained_for_year"] == TEST_YEAR and spec_b["train_through"] == TEST_YEAR - 1


def test_extra_booster_equals_a_plain_fit_on_all_rows(runs):
    """The saved booster is exactly 'train on every row < Y with the run's params'."""
    tmp, base, extra = runs
    saved = lgb.Booster(model_file=str(extra.parent / f"model_{TEST_YEAR + 1}.txt"))
    df = pd.read_parquet(tmp / "rows.parquet")
    spec = json.loads((extra.parent / "model.spec.json").read_text())
    X = df[spec["features"]].astype(float).copy()
    for c in spec["cats"]:
        X[c] = df[c].map(spec["cat_maps"][c]).astype(float)
    X = X.to_numpy(dtype=np.float32)
    p_saved = saved.predict(X[:500])
    assert np.isfinite(p_saved).all() and 0 < p_saved.min() and p_saved.max() < 1
    # the loop's 2021 booster (trained on 2019-2020 only) differs from the 2022 one (2019-2021)
    loop = lgb.Booster(model_file=str(extra.parent / f"model_{TEST_YEAR}.txt"))
    assert not np.allclose(loop.predict(X[:500]), p_saved)


@pytest.mark.parametrize("year, msg", [
    (TEST_YEAR, "rows for that year exist"),
    (TEST_YEAR - 1, "rows for that year exist"),
    (TEST_YEAR + 2, r"max data year \+ 1 = 2022"),
])
def test_validation_rejects_wrong_years(year, msg):
    years = np.array([2019, 2020, TEST_YEAR])
    with pytest.raises(ValueError, match=msg):
        m.validate_extra_booster_year(year, years, "/tmp/x/model")


def test_validation_needs_a_save_prefix():
    with pytest.raises(ValueError, match="save-last-model"):
        m.validate_extra_booster_year(TEST_YEAR + 1, np.array([TEST_YEAR]), "")
    assert m.validate_extra_booster_year(TEST_YEAR + 1, np.array([2019, TEST_YEAR]), "/tmp/x/model") == TEST_YEAR + 1


def test_model_spec_default_has_no_extra_key():
    base = dict(objective="binary", feats=["odds"], cats=[], cat_maps={}, last_year=2026,
                params={"seed": 1, "num_threads": 1}, rounds=1, train_from=2007, drop_groups="",
                training_cutoff_by_year={}, input_rows_sha256="x")
    assert "extra_booster_year" not in m.model_spec(**base)
    assert m.model_spec(**base, extra_booster_year=2027)["extra_booster_year"] == 2027
