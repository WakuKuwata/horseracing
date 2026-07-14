"""T007/T008: paired_eval subgroup extension — CIs, guard, backward-compat (FR-001/002/005)."""

from __future__ import annotations

import datetime

from horseracing_eval.dataset import EvalRace, ScoringLabel
from horseracing_eval.paired import paired_eval
from horseracing_eval.predictor import HorseEntry, Prediction, RaceContext


def _race(rid, year, ids, winner_prob=0.5, winner_idx=0):
    horses = tuple(HorseEntry(horse_id=h) for h in ids)
    ctx = RaceContext(race_id=rid, race_date=datetime.date(year, 6, 1), started_horses=horses)
    labels = tuple(
        ScoringLabel(h, 1 if i == winner_idx else 0, 1 if i < 2 else 0, 1 if i < 3 else 0)
        for i, h in enumerate(ids)
    )
    return EvalRace(context=ctx, labels=labels)


class _FakePredictor:
    is_leaky_reference = False

    def __init__(self, w):
        self._w = w

    def fit(self, races):
        return None

    def predict_race(self, ctx):
        n = len(ctx.started_horses)
        rest = (1.0 - self._w) / (n - 1)
        out = {}
        for i, h in enumerate(ctx.started_horses):
            p = self._w if i == 0 else rest
            out[h.horse_id] = Prediction(win=p, top2=min(1.0, p * 2), top3=min(1.0, p * 3))
        return out


class _FakeFactory:
    def __init__(self, w, tag):
        self._w = w
        self.recipe_meta = {"tag": tag}
        self.recipe_hash = tag

    def fit(self, train_races, *, num_threads=None):
        return _FakePredictor(self._w)


def _mixed_races():
    # 2024 + 2026 races, with canonical + nk: horses across days
    out = []
    for y in (2024, 2026):
        for m in range(1, 7):  # 6 race-days per year for bootstrap blocks
            out.append(_race(f"R{y}{m:02d}", y, [f"{y}h{m}a", f"nk:{y}h{m}b", f"{y}h{m}c", f"{y}h{m}d"]))
    return out


def test_subgroups_off_is_none_backward_compat():
    races = _mixed_races()
    rep = paired_eval(_FakeFactory(0.5, "c"), _FakeFactory(0.5, "a"), races,
                      first_valid_year=2024, bootstrap_b=50)
    assert rep.subgroups is None  # 068 byte-equiv when subgroups not requested (FR-005)


def test_subgroups_on_reports_race_and_horse_grains():
    races = _mixed_races()
    rep = paired_eval(_FakeFactory(0.6, "c"), _FakeFactory(0.4, "a"), races,
                      first_valid_year=2024, bootstrap_b=100, subgroups=True)
    sg = rep.subgroups
    assert sg is not None
    # race-level subgroups are 2026-only (winner NLL grain)
    assert "2026_only" in sg["race_subgroups"]
    # horse-level subgroups include canonical/nk/2026_nk
    assert {"canonical", "nk", "2026_nk"} <= set(sg["horse_subgroups"].keys())
    # each subgroup carries a CI + three-way decision + cand-uniform
    for v in sg["race_subgroups"].values():
        assert "bootstrap_ci" in v and v["decision"] in ("PASS", "FAIL", "NO_DECISION")
        assert "cand_minus_uniform" in v


def test_subgroup_guard_is_intersection_union_over_critical():
    races = _mixed_races()
    rep = paired_eval(_FakeFactory(0.6, "c"), _FakeFactory(0.5, "a"), races,
                      first_valid_year=2024, bootstrap_b=100, subgroups=True)
    sg = rep.subgroups
    assert set(sg["critical"]) == {"2026_only", "nk", "2026_nk"}
    # guard True iff every critical decision is PASS
    expected = all(sg["subgroup_decisions"][c] == "PASS" for c in sg["critical"])
    assert sg["subgroup_guard"] == expected


def test_coverage_bands_only_when_obs_count_injected():
    races = _mixed_races()
    # inject obs_count for every started horse -> coverage bands appear
    oc = {}
    for er in races:
        for i, h in enumerate(er.context.started_horses):
            oc[(er.context.race_id, h.horse_id)] = i  # 0,1,2,3 -> cov_0/cov_1_2/cov_1_2/cov_3plus
    rep = paired_eval(_FakeFactory(0.6, "c"), _FakeFactory(0.5, "a"), races,
                      first_valid_year=2024, bootstrap_b=50, subgroups=True, obs_count=oc)
    hs = rep.subgroups["horse_subgroups"]
    assert {"cov_0", "cov_1_2", "cov_3plus"} <= set(hs.keys())

    # without obs_count -> no coverage bands
    rep2 = paired_eval(_FakeFactory(0.6, "c"), _FakeFactory(0.5, "a"), races,
                       first_valid_year=2024, bootstrap_b=50, subgroups=True)
    assert not ({"cov_0", "cov_1_2", "cov_3plus"} & set(rep2.subgroups["horse_subgroups"].keys()))
