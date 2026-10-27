"""Leakage, race objective, artifact identity and saved-booster checks for 142."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SOURCE = Path(__file__).resolve().parents[1] / "roi_explore/roi_improvement_142/models.py"
spec = importlib.util.spec_from_file_location("models142", SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def data(years=(2013, 2014, 2015), races=3):
    rows = []
    for year in years:
        for race in range(races):
            for horse in range(4):
                row = {c: float(horse + race) for c in m.NUM}
                row.update({c: f"category_{race % 2}" for c in m.CAT})
                row.update(race_id=f"{year}0101{race+1:04d}", horse_id=f"h{horse}", horse_number=float(horse+1),
                           race_date=f"{year}-01-03", year=year, won=horse == race % 4,
                           odds=4. + horse, field_size=4., race_ok=True, dead_heat=False)
                rows.append(row)
    return pd.DataFrame(rows)


def test_feature_contract_excludes_unavailable_and_labels():
    assert len(m.FEATURES) == len(set(m.FEATURES)) == 77
    assert not set(m.FEATURES) & (m.DROP | {"won", "race_ok", "dead_heat", "finish_order", "p", "ret"})
    assert all(x["num_leaves"] in (31, 63) for x in m.CANDIDATES)
    assert len(m.CANDIDATES) == 6
    assert m.SCREEN_YEARS == (2016, 2017, 2018)


def test_immediate_date_bounds_before_result_filters():
    raw = pd.concat([data((2006,)), data((2007,)), data((2026,)), data((2027,))])
    raw.loc[raw.year == 2026, "race_date"] = "2026-06-26"
    invalid_future = data((2026,))
    invalid_future["race_date"] = "2026-06-27"
    invalid_future["field_size"] = 999
    result = m.bounded_rows(pd.concat([raw, invalid_future]))
    assert set(result.year) == {2007, 2026}
    assert result.race_date.max() == "2026-06-26"


def test_whole_invalid_races_removed_and_incomplete_groups_rejected():
    raw = data()
    rid = raw.race_id.iloc[0]
    raw.loc[raw.index[0], "race_ok"] = False
    assert rid not in set(m.bounded_rows(raw).race_id)
    partial = data().iloc[1:]
    with pytest.raises(ValueError, match="incomplete race group"):
        m.bounded_rows(partial)
    double = data()
    double.loc[1, "won"] = True
    with pytest.raises(ValueError, match="exactly one winner"):
        m.bounded_rows(double)


def test_group_contiguity_rejects_repeated_race():
    with pytest.raises(ValueError, match="not contiguous"):
        m.segments(["a", "b", "a"])


def test_nested_years_and_categories_only_observe_training():
    d = data((2014, 2015, 2016, 2017))
    d.loc[d.year == 2016, "venue_code"] = "future_category"
    tr, va, te = m.annual_split(d, 2016)
    assert set(tr.year) == {2014} and set(va.year) == {2015} and set(te.year) == {2016}
    maps = m.category_maps(pd.concat([tr, va]))
    assert "future_category" not in maps["venue_code"]
    x = m.design_matrix(te, maps)
    assert np.isnan(x[:, m.FEATURES.index("venue_code")]).all()
    assert m.category_maps(pd.concat([tr, va, te]))["venue_code"] != maps["venue_code"]


def test_softmax_finite_gradient_and_psd_hessian_upper_bound():
    raw = np.array([-.8, .1, 1.3, 5., -1.])
    y = np.array([0., 1., 0., 1., 0.])
    counts = np.array([3, 2])
    grad, hess = m.softmax_derivatives(raw, y, counts)
    eps = 1e-5
    def loss(z):
        return -np.dot(y, np.log(m.race_softmax(z, counts)))
    numeric = np.array([(loss(raw+np.eye(5)[i]*eps)-loss(raw-np.eye(5)[i]*eps))/(2*eps) for i in range(5)])
    np.testing.assert_allclose(grad, numeric, atol=1e-8)
    p = m.race_softmax(raw, counts)
    start = 0
    for count in counts:
        pr = p[start:start+count]
        exact = np.diag(pr)-np.outer(pr, pr)
        majorizer = np.diag(hess[start:start+count])-exact
        assert np.linalg.eigvalsh(majorizer).min() >= -1e-12
        assert np.sum(pr) == pytest.approx(1.)
        start += count
    np.testing.assert_allclose(m.race_softmax(raw+1e5, counts), p, atol=1e-11)
    assert np.isfinite(m.race_softmax(np.array([1000., -1000.]), [2])).all()


def test_race_metrics_count_races_once():
    d = data((2015,), races=2)
    result = m.metrics(d, np.full(len(d), .25))
    assert result["winner_nll"] == pytest.approx(np.log(4))
    assert result["winner_nll_sum"] == pytest.approx(2*np.log(4))
    assert result["ece_race"] == pytest.approx(0.)


@pytest.mark.parametrize("arm", ["binary", "softmax"])
def test_saved_booster_roundtrip_resume_and_identity(arm, tmp_path):
    d = data((2014, 2015), races=12)
    train, target = d[d.year == 2014], d[d.year == 2015]
    output = tmp_path / arm
    kwargs = dict(arm=arm, seed=1, rounds=4, output=output, identity={"protocol": "fixed"},
                  overrides={"num_leaves": 7, "min_data_in_leaf": 2})
    p, receipt = m.fit_model(train, target, **kwargs)
    p_saved, resumed = m.fit_model(train, target, **kwargs)
    np.testing.assert_array_equal(p, p_saved)
    assert resumed == receipt
    assert receipt["roundtrip_max_abs_raw_difference"] <= 1e-12
    saved_spec = json.loads((output / "feature_spec.json").read_text())
    assert saved_spec["train_through"] == 2014
    assert saved_spec["params"]["num_threads"] == 1
    if arm == "softmax":
        assert saved_spec["params"]["bagging_fraction"] == 1
        assert saved_spec["params"]["bagging_freq"] == 0
        np.testing.assert_allclose(np.add.reduceat(p, np.arange(0, len(p), 4)), 1.)
    with pytest.raises(ValueError, match="recipe differs"):
        m.fit_model(train, target, **dict(kwargs, seed=2))
    (output / "model.txt").write_text("tampered")
    with pytest.raises(ValueError, match="hash differs"):
        m.fit_model(train, target, **kwargs)


def test_partial_fit_and_nonpast_fit_are_rejected(tmp_path):
    d = data((2014, 2015))
    out = tmp_path / "partial"
    out.mkdir()
    with pytest.raises(ValueError, match="incomplete fit"):
        m.fit_model(d[d.year == 2014], d[d.year == 2015], arm="binary", seed=1, rounds=2, output=out, identity={})
    with pytest.raises(ValueError, match="strictly later"):
        m.fit_model(d, d, arm="binary", seed=1, rounds=2, output=tmp_path / "bad", identity={})


def test_prepare_run_requires_exact_identity_and_artifacts(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "ROOT", tmp_path)
    out = tmp_path / "artifacts" / "run"
    m.prepare_run(out, {"fixed": 1}, resume=False)
    with pytest.raises(ValueError, match="output exists"):
        m.prepare_run(out, {"fixed": 1}, resume=False)
    m.prepare_run(out, {"fixed": 1}, resume=True)
    with pytest.raises(ValueError, match="identity differs"):
        m.prepare_run(out, {"fixed": 2}, resume=True)
    with pytest.raises(ValueError, match="artifacts"):
        m.prepare_run(tmp_path / "production", {}, resume=False)


def test_cli_requires_frozen_protocol_for_full_learning(tmp_path):
    with pytest.raises(SystemExit):
        m.main(["screen", "--out", str(tmp_path / "new")])
    with pytest.raises(SystemExit):
        m.main(["evaluate", "--out", str(tmp_path / "new")])


def test_known_alias_race_excluded_whole_and_reason_recorded():
    valid = data((2024,))
    corrupt = data((2025,), races=1)
    corrupt["race_id"] = "202504040407"
    corrupt.loc[corrupt.index[-1], "horse_number"] = corrupt.horse_number.iloc[-2]
    result = m.bounded_rows(pd.concat([valid, corrupt]))
    assert set(result.year) == {2024}
    assert result.attrs["excluded_races"] == [dict(race_id="202504040407", reason=m.EXCLUDED_RACES["202504040407"], rows=4)]


def test_screen_reader_never_loads_outer_period(monkeypatch):
    seen = []
    def parquet(path, columns, filters):
        seen.append(filters)
        return data((2018,))
    monkeypatch.setattr(m.pd, "read_parquet", parquet)
    m.read_rows(Path("unused"), upper="2018-12-31")
    assert ("race_date", "<=", "2018-12-31") in seen[0]


def test_protocol_verifies_recipe_sidecar_and_bound_sources(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "ROOT", tmp_path)
    source = tmp_path / "source.py"
    source.write_text("frozen source")
    monkeypatch.setattr(m, "SOURCE", source)
    rows = tmp_path / "rows.parquet"
    rows.write_text("frozen rows")
    protocol = dict(feature="142-roi-policy-model-improvement", version=1, seeds=list(range(1, 16)),
                    max_workers=2, threads_each=1, can_adopt=False, untouched_holdout=False,
                    models=m.model_protocol(), window=dict(training_from=2007, through=m.UPPER,
                    model_inner=list(m.SCREEN_YEARS), model_outer=list(m.OUTER_YEARS)),
                    inputs_and_source_sha256={"source.py": m.sha(source), "rows.parquet": m.sha(rows)})
    path = tmp_path / "protocol.json"
    def persist():
        path.write_text(json.dumps(protocol))
        path.with_suffix(".sha256").write_text(m.sha(path))
    persist()
    assert m.verify_protocol(path, rows)["models"] == m.model_protocol()
    path.write_text("tampered")
    with pytest.raises(ValueError, match="sidecar"):
        m.verify_protocol(path, rows)
    protocol["threads_each"] = 8
    persist()
    with pytest.raises(ValueError, match="constraint differs: threads_each"):
        m.verify_protocol(path, rows)
    protocol["threads_each"] = 1
    protocol["models"]["candidates"] = list(m.CANDIDATES[:-1])
    persist()
    with pytest.raises(ValueError, match="constraint differs: models"):
        m.verify_protocol(path, rows)
    protocol["models"] = m.model_protocol()
    persist()
    rows.write_text("changed")
    with pytest.raises(ValueError, match="hash mismatch: rows"):
        m.verify_protocol(path, rows)
