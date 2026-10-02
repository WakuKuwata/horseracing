"""Feature 137 (plan 0.3): market-aware expected return — feature builder, predict, boosters, CLI.

The builder's leak boundary (憲法 II) is behavioural here: a race's own outcome columns never move
its own features, and history is strictly before the race date (a same-day earlier race of the
same jockey does not count). ``predict`` only scores ``race_ok`` races; the year fallback and the
recorded booster identity follow the walk-forward rule; the CLI rejects bad scopes and model
directories before touching any database.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine

from horseracing_training import cli, market_ev
from tests._market_ev_synth import (
    D1,
    D2,
    D3,
    SPEC_CAT_MAPS,
    SPEC_CATS,
    SPEC_FEATURES,
    race_id,
    raw_frame,
    write_ensemble_dir,
    write_model_dir,
)

#: the 77 inputs of the production spec mev-binary-v2 (66 numeric + 11 categorical)
PRODUCTION_INPUTS = (
    "odds", "q", "popularity", "odds_rank", "q_share_of_fav", "fav_q", "fav_odds", "second_odds",
    "odds_gap12", "q_entropy_norm", "n_fav_under_2", "n_odds_under_10", "field_size", "distance",
    "race_number", "month", "dow", "prize_money", "is_graded", "is_last_race", "is_first_race",
    "age", "frame", "horse_number", "jockey_weight", "is_debut", "career_starts", "career_wins",
    "career_win_rate", "career_top3_rate", "prev_finish", "prev2_finish", "prev3_finish",
    "avg_last3_finish", "best_finish_last5", "wins_last5", "top3_last5", "prev_popularity",
    "prev_odds", "prev_q", "prev_finish_pct", "prev_beat_market", "prev_field_size",
    "prev_distance", "dist_change", "class_change", "days_since_last", "tataki_2", "prev_weight",
    "prev_last3f_rank", "prev_margin_sec", "last_won", "jockey_change", "jockey_win_rate_365",
    "jockey_starts_365", "jockey_win_rate_all", "jockey_starts_all", "trainer_win_rate_365",
    "trainer_starts_365", "trainer_win_rate_all", "combo_starts_all", "combo_win_rate_all",
    "jockey_excess_365", "trainer_excess_365", "jockey_excess_all", "trainer_excess_all",
    "venue_code", "track_type", "going", "weather", "race_class_canon", "dist_band", "sex",
    "prev_running_style", "prev_class_canon", "prev_track_type", "sire_line",
)
#: a row's own outcome columns (and the within-race rank of its own last 3F)
OUTCOME_COLUMNS = {
    "finish_order", "result_status", "margin_sec", "last_3f", "finished", "won", "last3f_rank",
}


def _row(feats: pd.DataFrame, rid: str, horse: str) -> pd.Series:
    hit = feats[(feats["race_id"] == rid) & (feats["horse_id"] == horse)]
    assert len(hit) == 1
    return hit.iloc[0]


def _upto(feats: pd.DataFrame, day: datetime.date) -> pd.DataFrame:
    kept = feats[feats["race_date"].dt.date <= day]
    cols = [c for c in feats.columns if c not in OUTCOME_COLUMNS]
    return kept[cols].sort_values(["race_id", "horse_id"]).reset_index(drop=True)


def _mutate_outcomes(raw: pd.DataFrame, rid: str) -> pd.DataFrame:
    """Reverse the finishing order, stop one horse, and move last 3F / margins of race ``rid``."""
    out = raw.copy()
    m = out["race_id"] == rid
    out.loc[m, "finish_order"] = out.loc[m, "finish_order"].to_numpy()[::-1]
    out.loc[m & (out["horse_number"] == 2), "result_status"] = "stopped"
    out.loc[m & (out["horse_number"] == 2), "finish_order"] = None
    out.loc[m, "last_3f"] = out.loc[m, "last_3f"].to_numpy()[::-1] + 1.5
    out.loc[m, "margin_sec"] = out.loc[m, "margin_sec"] + 0.7
    return out


# --------------------------------------------------------------------------- build_features


def test_build_features_emits_every_production_model_input():
    feats = market_ev.build_features(raw_frame())
    missing = [c for c in PRODUCTION_INPUTS if c not in feats.columns]
    assert missing == []
    assert len(PRODUCTION_INPUTS) == 77


def test_own_race_outcome_never_moves_that_race_or_same_day_features():
    target = race_id(D2, 1)
    base = market_ev.build_features(raw_frame())
    moved = market_ev.build_features(_mutate_outcomes(raw_frame(), target))

    # the target race AND the same-day race 2 (J1 rode H1 in race 1 before riding H4 in race 2)
    pd.testing.assert_frame_equal(_upto(base, D2), _upto(moved, D2), check_exact=True)
    # sanity: the mutation is real — it reaches the NEXT race day through strictly-earlier history
    assert _row(base, race_id(D3, 1), "H1")["prev_finish"] == 1.0
    assert _row(moved, race_id(D3, 1), "H1")["prev_finish"] == 3.0
    assert (_row(base, race_id(D3, 2), "H4")["jockey_wins_all"]
            != _row(moved, race_id(D3, 2), "H4")["jockey_wins_all"])


def test_future_outcomes_never_move_earlier_features():
    base = market_ev.build_features(raw_frame())
    moved = market_ev.build_features(_mutate_outcomes(raw_frame(), race_id(D3, 1)))
    pd.testing.assert_frame_equal(_upto(base, D2), _upto(moved, D2), check_exact=True)


def test_horse_history_is_strictly_before():
    feats = market_ev.build_features(raw_frame())
    debut = _row(feats, race_id(D1, 1), "H1")
    assert debut["career_starts"] == 0 and bool(debut["is_debut"])
    assert np.isnan(debut["prev_finish"]) and np.isnan(debut["days_since_last"])

    second = _row(feats, race_id(D2, 1), "H1")
    assert second["career_starts"] == 1 and second["career_wins"] == 1
    assert second["prev_finish"] == 1.0 and second["last_won"] == 1.0
    assert second["days_since_last"] == 7.0 and second["prev_odds"] == 2.0

    third = _row(feats, race_id(D3, 1), "H1")
    assert third["career_starts"] == 2 and third["career_wins"] == 2


def test_jockey_stats_exclude_the_same_day():
    feats = market_ev.build_features(raw_frame())
    # D1: J1 wins race 1 on H1, then rides H4 in race 2 — race 1 must not count for race 2
    for rn, horse in ((1, "H1"), (2, "H4")):
        row = _row(feats, race_id(D1, rn), horse)
        assert row["jockey_starts_all"] == 0 and np.isnan(row["jockey_win_rate_all"])
    # D2: both D1 rides (two wins) count, still nothing from D2 itself
    for rn, horse in ((1, "H1"), (2, "H4")):
        row = _row(feats, race_id(D2, rn), horse)
        assert row["jockey_starts_all"] == 2 and row["jockey_wins_all"] == 2
        assert row["jockey_win_rate_all"] == 1.0


def test_race_ok_requires_every_started_horse_priced():
    raw = raw_frame()
    raw.loc[(raw["race_id"] == race_id(D3, 1)) & (raw["horse_id"] == "H2"), "odds"] = None
    raw.loc[(raw["race_id"] == race_id(D3, 2)) & (raw["horse_id"] == "H5"), "odds"] = 0.0
    feats = market_ev.build_features(raw)
    ok = feats.groupby("race_id")["race_ok"].agg(["min", "max"])
    assert not ok.loc[race_id(D3, 1)].any() and not ok.loc[race_id(D3, 2)].any()
    assert ok.drop(index=[race_id(D3, 1), race_id(D3, 2)]).all().all()


# --------------------------------------------------------------------------- predict


def test_predict_scores_only_race_ok_races_and_records_the_booster(tmp_path):
    model_dir = write_model_dir(tmp_path / "mev-test", years=(2026,))
    model = market_ev.MarketEvModel.load(model_dir, "mev-test")
    raw = raw_frame()
    raw.loc[(raw["race_id"] == race_id(D3, 1)) & (raw["horse_id"] == "H2"), "odds"] = None
    pred = market_ev.predict(model, market_ev.build_features(raw))

    assert list(pred.columns) == list(market_ev._PREDICTION_COLUMNS)
    assert race_id(D3, 1) not in set(pred["race_id"])
    assert len(pred) == 5 * 3  # 6 races, one skipped
    assert pred["win_prob"].between(0, 1, inclusive="neither").all()
    np.testing.assert_array_equal(
        pred["expected_return"].to_numpy(), pred["win_prob"].to_numpy() * pred["odds_used"].to_numpy()
    )
    booster = model_dir / "model_2026.txt"
    assert set(pred["booster"]) == {"model_2026.txt"}
    assert set(pred["booster_sha256"]) == {hashlib.sha256(booster.read_bytes()).hexdigest()}


def test_predict_on_nothing_returns_the_output_columns(tmp_path):
    model = market_ev.MarketEvModel.load(write_model_dir(tmp_path / "m"), "m")
    raw = raw_frame()
    raw["odds"] = None
    pred = market_ev.predict(model, market_ev.build_features(raw))
    assert pred.empty and list(pred.columns) == list(market_ev._PREDICTION_COLUMNS)


def test_design_matrix_maps_categories_and_leaves_unknown_missing(tmp_path):
    model = market_ev.MarketEvModel.load(write_model_dir(tmp_path / "m"), "m")
    feats = market_ev.build_features(raw_frame()).head(3).copy()
    feats["venue_code"] = ["06", "99", None]
    feats["track_type"] = ["ダ", np.nan, "芝"]
    X = model.design_matrix(feats)
    assert X.shape == (3, len(SPEC_FEATURES) + len(SPEC_CATS))
    venue = X[:, len(SPEC_FEATURES)]
    track = X[:, len(SPEC_FEATURES) + 1]
    assert venue[0] == SPEC_CAT_MAPS["venue_code"]["06"]
    assert np.isnan(venue[1]) and np.isnan(venue[2])
    assert track[0] == SPEC_CAT_MAPS["track_type"]["ダ"] and np.isnan(track[1])


# --------------------------------------------------------------------------- boosters / spec


def _bare_model_dir(path: pathlib.Path, years: tuple[int, ...]) -> market_ev.MarketEvModel:
    path.mkdir(parents=True)
    (path / "model.spec.json").write_text(json.dumps({"objective": "binary"}))
    for y in years:
        (path / f"model_{y}.txt").write_text("x")
    (path / "model.txt").write_text("x")          # the "latest" copy is never a yearly booster
    (path / "model_backup.txt").write_text("x")   # nor is a non-year suffix
    return market_ev.MarketEvModel.load(path, path.name)


def test_booster_for_year_uses_exact_year_else_latest_earlier(tmp_path):
    model = _bare_model_dir(tmp_path / "m", (2019, 2021, 2023))
    assert model.booster_path_for_year(2021).name == "model_2021.txt"
    assert model.booster_path_for_year(2022).name == "model_2021.txt"
    assert model.booster_path_for_year(2027).name == "model_2023.txt"
    assert model.booster_path_for_year(2020).name == "model_2019.txt"
    with pytest.raises(FileNotFoundError):
        model.booster_path_for_year(2018)


def test_load_rejects_a_non_binary_spec(tmp_path):
    (tmp_path / "model.spec.json").write_text(json.dumps({"objective": "lambdarank"}))
    with pytest.raises(ValueError, match="binary"):
        market_ev.MarketEvModel.load(tmp_path, "m")


def test_validate_model_dir(tmp_path):
    assert market_ev.validate_model_dir(tmp_path) == tmp_path
    with pytest.raises(ValueError, match="absolute"):
        market_ev.validate_model_dir("artifacts/market_ev/mev-binary-v2")
    with pytest.raises(ValueError, match="worktree"):
        market_ev.validate_model_dir("/repo/.claude/worktrees/x/artifacts/market_ev/mev")


def test_logic_version_is_the_contract_value():
    assert market_ev.LOGIC_VERSION == (
        "mev-v1;features=roi-explore-2026-09;drop=sameday,weightlive;data>=2007"
    )
    assert market_ev.DATA_START == "2007-01-01"
    assert not hasattr(market_ev, "HIGHLIGHT_THRESHOLD")  # the threshold lives in the API only


# --------------------------------------------------------------------------- CLI


@pytest.fixture
def no_db(monkeypatch):
    """Argument errors must be raised before any engine is created."""
    def _boom(*_a, **_k):
        raise AssertionError("argument validation must not open a database")

    monkeypatch.setattr(cli, "create_db_engine", _boom)


@pytest.fixture
def model_dir(tmp_path) -> pathlib.Path:
    d = tmp_path / "mev-cli"
    d.mkdir()
    (d / "model.spec.json").write_text(json.dumps({"objective": "binary"}))
    return d


def _exit_code(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as info:
        cli.main(argv)
    return info.value.code


@pytest.mark.parametrize(
    "scope",
    [
        [],                                                    # no scope at all
        ["--date", "2026-09-27", "--race-id", "202609270511"],  # two scopes
        ["--date", "2026-09-27", "--from", "2026-09-01"],       # two scopes
        ["--from", "2026-09-01"],                               # --from without --to
        ["--to", "2026-09-27"],                                 # --to without --from
        ["--date", "2026-09-27", "--to", "2026-09-28"],         # --to with --date
        ["--from", "2026-09-28", "--to", "2026-09-01"],         # reversed range
        ["--date", "2026-13-01"],                               # not a date
    ],
)
def test_cli_rejects_bad_scopes(no_db, model_dir, scope):
    assert _exit_code(["market-ev", *scope, "--model-dir", str(model_dir)]) == 2


def test_cli_requires_model_dir(no_db):
    assert _exit_code(["market-ev", "--date", "2026-09-27"]) == 2


def test_cli_rejects_relative_worktree_and_specless_model_dirs(no_db, tmp_path, model_dir):
    base = ["market-ev", "--date", "2026-09-27", "--model-dir"]
    assert _exit_code([*base, "artifacts/market_ev/mev-binary-v2"]) == 2
    worktree = tmp_path / ".claude" / "worktrees" / "wt" / "mev"
    worktree.mkdir(parents=True)
    (worktree / "model.spec.json").write_text(json.dumps({"objective": "binary"}))
    assert _exit_code([*base, str(worktree)]) == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _exit_code([*base, str(empty)]) == 2


@pytest.fixture
def fake_run(monkeypatch):
    """A throwaway engine plus a recording compute_and_persist."""
    engine = create_engine("sqlite://")
    monkeypatch.setattr(cli, "create_db_engine", lambda _url=None: engine)
    calls: list[dict] = []
    outcome: dict = {"status": "ok"}

    def _compute(session, **kwargs):
        calls.append(kwargs)
        if isinstance(outcome.get("raise"), Exception):
            raise outcome["raise"]
        ok = outcome["status"] == "ok"
        return {
            "status": outcome["status"], "reason": None if ok else "no_races_with_odds",
            "races": 3 if ok else 0, "horses": 42 if ok else 0,
            "from": kwargs["race_date_from"].isoformat(), "to": kwargs["race_date_to"].isoformat(),
            "model_version": kwargs["model_version"] or kwargs["model_dir"].name,
            "logic_version": market_ev.LOGIC_VERSION, "run_id": "r" if ok else None,
            "races_in_range": 3, "races_invalid_odds": 0, "result_pending_races": 3,
            "boosters": {"model_2026.txt": "ab" * 32} if ok else {},
        }

    monkeypatch.setattr(market_ev, "compute_and_persist", _compute)
    return calls, outcome


def _last_line(capsys) -> str:
    return capsys.readouterr().out.strip().splitlines()[-1]


def test_cli_date_scope_prints_ok_marker_last(fake_run, model_dir, capsys):
    calls, _ = fake_run
    rc = cli.main(["market-ev", "--date", "2026-09-27", "--model-dir", str(model_dir)])
    assert rc == 0
    assert _last_line(capsys) == "OK: races=3 horses=42 from=2026-09-27 to=2026-09-27"
    (call,) = calls
    assert call["race_date_from"] == call["race_date_to"] == datetime.date(2026, 9, 27)
    assert call["model_dir"] == model_dir and call["model_version"] is None


def test_cli_range_and_explicit_model_version(fake_run, model_dir, capsys):
    calls, _ = fake_run
    rc = cli.main(["market-ev", "--from", "2026-09-01", "--to", "2026-09-28",
                   "--model-dir", str(model_dir), "--model-version", "mev-x"])
    assert rc == 0
    assert _last_line(capsys) == "OK: races=3 horses=42 from=2026-09-01 to=2026-09-28"
    assert calls[0]["model_version"] == "mev-x"


def test_cli_race_id_computes_that_race_day(fake_run, model_dir, monkeypatch, capsys):
    calls, _ = fake_run
    seen: list[str] = []

    def _resolve(_session, rid):
        seen.append(rid)
        return datetime.date(2026, 9, 27)

    monkeypatch.setattr(market_ev, "resolve_race_date", _resolve)
    rc = cli.main(["market-ev", "--race-id", "202609270511", "--model-dir", str(model_dir)])
    assert rc == 0 and seen == ["202609270511"]
    assert calls[0]["race_date_from"] == calls[0]["race_date_to"] == datetime.date(2026, 9, 27)
    assert _last_line(capsys) == "OK: races=3 horses=42 from=2026-09-27 to=2026-09-27"


def test_cli_skipped_marker(fake_run, model_dir, capsys):
    _, outcome = fake_run
    outcome["status"] = "skipped"
    rc = cli.main(["market-ev", "--date", "2026-09-27", "--model-dir", str(model_dir)])
    assert rc == 0
    assert _last_line(capsys) == "SKIPPED: no_races_with_odds"


def test_cli_exception_exits_nonzero_without_a_marker(fake_run, model_dir, capsys):
    _, outcome = fake_run
    outcome["raise"] = RuntimeError("boom")
    rc = cli.main(["market-ev", "--date", "2026-09-27", "--model-dir", str(model_dir)])
    assert rc == 1
    captured = capsys.readouterr()
    assert "OK:" not in captured.out and "SKIPPED:" not in captured.out
    assert "ERROR: market-ev failed: RuntimeError: boom" in captured.err


# --------------------------------------------------------------------------- Feature 138: ensemble

ENS = "mev-ens15-v1"
SINGLE = "mev-binary-v2"


def test_ensemble_logic_version_is_the_contract_value():
    assert market_ev.ENSEMBLE_LOGIC_VERSION == (
        "mev-ens15-v1;seeds=1-15;threads=1;deterministic;features=roi-explore-2026-09;"
        "drop=sameday,weightlive;data>=2007"
    )
    assert market_ev.ENSEMBLE_SEEDS == tuple(range(1, 16))
    # the single-version contract is untouched
    assert market_ev.LOGIC_VERSION.startswith("mev-v1;")


def test_predict_ensemble_is_the_mean_of_the_members(tmp_path):
    ens_dir = write_ensemble_dir(tmp_path / ENS, years=(2026,))
    model = market_ev.EnsembleMarketEvModel.load(ens_dir, ENS)
    feats = market_ev.build_features(raw_frame())
    pred = market_ev.predict_ensemble(model, feats)

    assert list(pred.columns) == list(market_ev._PREDICTION_COLUMNS)
    single = market_ev.predict(market_ev.MarketEvModel.load(ens_dir / "seed_01", "s1"), feats)
    assert market_ev._row_keys(pred) == market_ev._row_keys(single)  # same race_ok filter

    ok = feats[feats["race_ok"].astype(bool)]
    X = model.design_matrix(ok)
    import lightgbm as lgb

    members = [lgb.Booster(model_file=str(ens_dir / f"seed_{s:02d}" / "model_2026.txt"))
               for s in range(1, 16)]
    expected = np.mean(np.vstack([b.predict(X) for b in members]), axis=0)
    np.testing.assert_array_equal(pred["win_prob"].to_numpy(), expected)
    np.testing.assert_array_equal(
        pred["expected_return"].to_numpy(), expected * pred["odds_used"].to_numpy()
    )
    # the members really differ (an average of identical boosters would hide a wiring bug)
    assert not np.array_equal(members[0].predict(X), members[1].predict(X))
    manifest = ens_dir / "ensemble_2026.json"
    assert set(pred["booster"]) == {"ensemble_2026.json"}
    assert set(pred["booster_sha256"]) == {hashlib.sha256(manifest.read_bytes()).hexdigest()}


def test_ensemble_manifest_falls_back_to_the_latest_earlier_year(tmp_path):
    ens_dir = write_ensemble_dir(tmp_path / ENS, years=(2024, 2025))
    model = market_ev.EnsembleMarketEvModel.load(ens_dir, ENS)
    assert model.booster_manifest_for_year(2025).name == "ensemble_2025.json"
    assert model.booster_manifest_for_year(2026).name == "ensemble_2025.json"
    with pytest.raises(FileNotFoundError):
        model.booster_manifest_for_year(2023)
    pred = market_ev.predict_ensemble(model, market_ev.build_features(raw_frame()))
    assert set(pred["booster"]) == {"ensemble_2025.json"}


def _rewrite_manifest(ens_dir: pathlib.Path, year: int, mutate) -> None:
    path = ens_dir / f"ensemble_{year}.json"
    manifest = json.loads(path.read_text())
    mutate(manifest)
    path.write_text(json.dumps(manifest))


def test_ensemble_member_sha_mismatch_fails_closed(tmp_path):
    ens_dir = write_ensemble_dir(tmp_path / ENS)
    member = ens_dir / "seed_07" / "model_2026.txt"
    member.write_text(member.read_text() + "\n")  # one byte differs from the recorded sha256
    model = market_ev.EnsembleMarketEvModel.load(ens_dir, ENS)
    with pytest.raises(market_ev.EnsembleIntegrityError, match="sha256 mismatch"):
        market_ev.predict_ensemble(model, market_ev.build_features(raw_frame()))


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda m: m["members"].pop(), "member seeds"),                    # partial average
        (lambda m: m["members"].reverse(), "member seeds"),                # order is part of it
        (lambda m: m["members"][0].update(path="../escape.txt"), "escapes"),
        (lambda m: m.update(version="mev-other"), "version/year"),
        (lambda m: m.update(year=2025), "version/year"),
    ],
)
def test_ensemble_manifest_must_match_spec(tmp_path, mutate, match):
    ens_dir = write_ensemble_dir(tmp_path / ENS)
    _rewrite_manifest(ens_dir, 2026, mutate)
    model = market_ev.EnsembleMarketEvModel.load(ens_dir, ENS)
    with pytest.raises(market_ev.EnsembleIntegrityError, match=match):
        model.load_members(ens_dir / "ensemble_2026.json")


@pytest.mark.parametrize(
    "patch",
    [
        {"seeds": list(range(1, 15))},
        {"num_threads": 8},
        {"deterministic": False},
        {"objective": "lambdarank"},
        {"version": "mev-ens15-v0"},
        # "drop=sameday,weightlive;data>=2007" is part of the logic version the rows carry
        {"drop_groups": ["sameday"]},
        {"drop_groups": ["sameday", "weightlive", "odds"]},
        {"drop_groups": None},
        {"train_from": 1986},
        {"train_from": None},
    ],
)
def test_ensemble_spec_must_match_the_logic_version(tmp_path, patch):
    ens_dir = write_ensemble_dir(tmp_path / ENS)
    spec_path = ens_dir / "ensemble.spec.json"
    spec = json.loads(spec_path.read_text()) | patch
    spec_path.write_text(json.dumps(spec))
    with pytest.raises(market_ev.EnsembleIntegrityError):
        market_ev.EnsembleMarketEvModel.load(ens_dir, ENS)


def test_ensemble_spec_drop_group_order_is_irrelevant(tmp_path):
    ens_dir = write_ensemble_dir(tmp_path / ENS)
    spec_path = ens_dir / "ensemble.spec.json"
    spec = json.loads(spec_path.read_text()) | {"drop_groups": ["weightlive", "sameday"]}
    spec_path.write_text(json.dumps(spec))
    assert market_ev.EnsembleMarketEvModel.load(ens_dir, ENS).spec["train_from"] == 2007


def test_ensemble_runs_only_pair_the_registry_versions(tmp_path):
    """Picks recorded from another pair would be filed under the same rule set (fail-closed,
    before any database access)."""
    single = write_model_dir(tmp_path / "mev-it-v1")
    ens = write_ensemble_dir(tmp_path / ENS)
    with pytest.raises(ValueError, match=SINGLE):
        market_ev.compute_and_persist(None, race_date_from=D1, race_date_to=D1,
                                      model_dir=single, ensemble_dir=ens)
    other = write_ensemble_dir(tmp_path / "mev-ens15-v9")
    with pytest.raises(ValueError, match=ENS):
        market_ev.compute_and_persist(None, race_date_from=D1, race_date_to=D1,
                                      model_dir=write_model_dir(tmp_path / SINGLE),
                                      ensemble_dir=other)
    with pytest.raises(ValueError, match="ensemble_only requires"):
        market_ev.compute_and_persist(None, race_date_from=D1, race_date_to=D1,
                                      model_dir=single, ensemble_only=True)


def test_drop_invalid_odds_races_is_race_atomic():
    pred = pd.DataFrame({"race_id": ["a", "a", "b"], "odds_used": [0.9, 3.0, 2.0]})
    kept, invalid = market_ev.drop_invalid_odds_races(pred)
    assert invalid == {"a"} and list(kept["race_id"]) == ["b"]


# --------------------------------------------------------------------------- Feature 138: CLI


@pytest.fixture
def ensemble_dir(tmp_path) -> pathlib.Path:
    d = tmp_path / ENS
    d.mkdir()
    (d / "ensemble.spec.json").write_text(json.dumps({"objective": "binary"}))
    return d


def test_cli_rejects_bad_ensemble_dirs(no_db, tmp_path, model_dir, ensemble_dir):
    base = ["market-ev", "--date", "2026-09-27", "--model-dir", str(model_dir)]
    assert _exit_code([*base, "--ensemble-dir", "artifacts/market_ev/mev-ens15-v1"]) == 2
    worktree = tmp_path / ".claude" / "worktrees" / "wt" / ENS
    worktree.mkdir(parents=True)
    (worktree / "ensemble.spec.json").write_text("{}")
    assert _exit_code([*base, "--ensemble-dir", str(worktree)]) == 2
    specless = tmp_path / "specless"
    specless.mkdir()
    (specless / "model.spec.json").write_text("{}")  # a single-model dir is not an ensemble
    assert _exit_code([*base, "--ensemble-dir", str(specless)]) == 2
    assert _exit_code([*base, "--ensemble-only"]) == 2
    assert _exit_code([*base, "--ensemble-version", ENS]) == 2


@pytest.fixture
def fake_ens_run(monkeypatch):
    """A recording compute_and_persist that answers like an ensemble run."""
    engine = create_engine("sqlite://")
    monkeypatch.setattr(cli, "create_db_engine", lambda _url=None: engine)
    calls: list[dict] = []
    outcome: dict = {"checkpoints": {"written": [], "pending": [], "error": None,
                                     "prospective_start_date": None}}

    def _compute(session, **kwargs):
        calls.append(kwargs)
        only = kwargs.get("ensemble_only", False)
        single = {"races": 3, "horses": 42, "result_pending_races": 3,
                  "logic_version": market_ev.LOGIC_VERSION,
                  "boosters": {"model_2026.txt": "ab" * 32}}
        ens = {"races": 3, "horses": 42, "result_pending_races": 3,
               "logic_version": market_ev.ENSEMBLE_LOGIC_VERSION,
               "boosters": {"ensemble_2026.json": "cd" * 32}}
        versions = {ENS: ens} if only else {SINGLE: single, ENS: ens}
        top = ens if only else single
        return {
            "status": "ok", "reason": None, "races": 3, "horses": 42,
            "from": kwargs["race_date_from"].isoformat(), "to": kwargs["race_date_to"].isoformat(),
            "model_version": ENS if only else SINGLE, "logic_version": top["logic_version"],
            "run_id": "r", "races_in_range": 3, "races_invalid_odds": 0,
            "result_pending_races": 3, "boosters": top["boosters"], "versions": versions,
            "picks": {"races_first_computed": 3, "races_already_scanned": 0, "written": 5,
                      "voided_scratched": 1, "skipped_no_horse_number": 0},
            "checkpoints": outcome["checkpoints"],
        }

    monkeypatch.setattr(market_ev, "compute_and_persist", _compute)
    return calls, outcome


def _ens_argv(model_dir, ensemble_dir, *extra):
    return ["market-ev", "--date", "2026-09-27", "--model-dir", str(model_dir),
            "--ensemble-dir", str(ensemble_dir), *extra]


def test_cli_ensemble_marker_ok(fake_ens_run, model_dir, ensemble_dir, capsys):
    calls, _ = fake_ens_run
    assert cli.main(_ens_argv(model_dir, ensemble_dir)) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[-1] == (
        "OK: races=3 horses=42 from=2026-09-27 to=2026-09-27 versions=2 picks=5 checkpoints=ok"
    )
    assert any(ln.startswith(f"market-ev: model_version={ENS} ") for ln in lines)
    assert any(ln.startswith("attention-picks: ") and "voided_scratched=1" in ln for ln in lines)
    (call,) = calls
    assert call["ensemble_dir"] == ensemble_dir
    assert call["ensemble_version"] is None and call["ensemble_only"] is False


def test_cli_ensemble_marker_pending(fake_ens_run, model_dir, ensemble_dir, capsys):
    _, outcome = fake_ens_run
    outcome["checkpoints"] = {"written": [("S3", 300, "continue")], "pending": [("S1", 300)],
                              "error": None, "prospective_start_date": "2026-10-04"}
    assert cli.main(_ens_argv(model_dir, ensemble_dir)) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[-1].endswith(" versions=2 picks=5 checkpoints=pending")
    assert lines[-2] == ("attention-checkpoints: written=S3:300:continue pending=S1:300 "
                         "prospective_start_date=2026-10-04")


def test_cli_ensemble_marker_error_keeps_the_cause_right_before_it(
    fake_ens_run, model_dir, ensemble_dir, capsys
):
    _, outcome = fake_ens_run
    outcome["checkpoints"] = {"written": [], "pending": [], "error": "RuntimeError: boom",
                              "prospective_start_date": "2026-10-04"}
    assert cli.main(_ens_argv(model_dir, ensemble_dir)) == 0  # the computed rows are committed
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[-1].endswith(" versions=2 picks=5 checkpoints=error")
    assert lines[-2] == "attention-checkpoints: error=RuntimeError: boom"


def test_cli_ensemble_only_marker(fake_ens_run, model_dir, ensemble_dir, capsys):
    calls, _ = fake_ens_run
    argv = _ens_argv(model_dir, ensemble_dir, "--ensemble-only", "--ensemble-version", ENS)
    assert cli.main(argv) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[-1] == (
        "OK: races=3 horses=42 from=2026-09-27 to=2026-09-27 versions=ens picks=5 checkpoints=ok"
    )
    assert calls[0]["ensemble_only"] is True and calls[0]["ensemble_version"] == ENS
    # one market-ev line only: the written version is the ensemble
    assert [ln for ln in lines if ln.startswith("market-ev:")] == [
        ln for ln in lines if ln.startswith(f"market-ev: model_version={ENS} ")
    ]


def test_cli_ensemble_skipped_prints_the_void_pass_before_the_marker(
    fake_ens_run, model_dir, ensemble_dir, monkeypatch, capsys
):
    picks = {"races_first_computed": 0, "races_already_scanned": 0, "written": 0,
             "voided_scratched": 2, "skipped_no_horse_number": 0, "void_run_id": "v"}

    def _compute(session, **kwargs):
        return {"status": "skipped", "reason": "no_pending_races", "races": 0, "horses": 0,
                "from": "2026-09-27", "to": "2026-09-27", "model_version": SINGLE,
                "logic_version": market_ev.LOGIC_VERSION, "run_id": None, "races_in_range": 3,
                "races_invalid_odds": 0, "result_pending_races": 0, "boosters": {},
                "versions": {}, "picks": picks, "checkpoints": None}

    monkeypatch.setattr(market_ev, "compute_and_persist", _compute)
    assert cli.main(_ens_argv(model_dir, ensemble_dir, "--pending-only")) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[-1] == "SKIPPED: no_pending_races"  # the marker ops reads is unchanged
    assert lines[-2].startswith("attention-picks: ") and "voided_scratched=2" in lines[-2]


def test_cli_without_ensemble_dir_passes_no_ensemble_arguments(fake_run, model_dir, capsys):
    calls, _ = fake_run
    assert cli.main(["market-ev", "--date", "2026-09-27", "--model-dir", str(model_dir)]) == 0
    assert not any(k.startswith("ensemble") for k in calls[0])
    out = capsys.readouterr().out
    assert "attention-" not in out and "versions=" not in out


@pytest.fixture
def fake_checkpoints(monkeypatch):
    from horseracing_training import attention_checkpoints

    engine = create_engine("sqlite://")
    monkeypatch.setattr(cli, "create_db_engine", lambda _url=None: engine)
    calls: list[dict] = []
    outcome: dict = {"error": None}

    def _evaluate(session, **kwargs):
        calls.append(kwargs)
        return {"written": [("S3", 300, "continue")], "pending": [], "error": outcome["error"],
                "prospective_start_date": "2026-10-04", "dry_run": kwargs["dry_run"]}

    monkeypatch.setattr(attention_checkpoints, "evaluate_checkpoints", _evaluate)
    return calls, outcome


def test_cli_attention_checkpoints(fake_checkpoints, capsys):
    calls, outcome = fake_checkpoints
    assert cli.main(["attention-checkpoints", "--dry-run"]) == 0
    assert _last_line(capsys) == "OK: written=1 pending=0 dry_run=true"
    assert calls[0]["dry_run"] is True and calls[0]["now"].tzinfo is not None
    assert cli.main(["attention-checkpoints"]) == 0
    assert _last_line(capsys) == "OK: written=1 pending=0 dry_run=false"
    outcome["error"] = "CheckpointStartDateMismatch: S1/300"
    assert cli.main(["attention-checkpoints"]) == 1
    assert "ERROR: attention-checkpoints: CheckpointStartDateMismatch" in capsys.readouterr().err
