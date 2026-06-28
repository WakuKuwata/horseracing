"""Deterministic, feature-aware fake Predictor for Feature 020 harness tests.

The 020 eval harnesses (feature_eval / ablation / market_edge) are predictor-AGNOSTIC — they take
a predictor (or factory) and call evaluate(). These fakes let us test the harness/gate logic (diff,
primary gate, fold guards, per-group contribution, determinism) WITHOUT pulling in LightGBM or the
real training package (which would be a dependency cycle: training already depends on eval). The real
LightGBM wiring is covered by the training CLI + the real-DB quickstart smoke (T018).

The synthetic fields used by these tests always make horse_number 1 the winner, so a higher
``skill`` (more mass on #1) is genuinely better-calibrated and lower-LogLoss. ``skill`` reads only
``horse_number`` — a legit POST_FRAME feature, never result-time data.
"""

from __future__ import annotations

from horseracing_eval.baselines import harville_topk
from horseracing_eval.predictor import Prediction, RaceContext

_EPS = 1e-6


class FakePredictor:
    """win mass on horse_number 1 grows with ``skill`` (skill=1 → uniform)."""

    is_leaky_reference = False

    def __init__(self, skill: float = 1.0) -> None:
        self.skill = max(skill, 1.0)

    def fit(self, train_races: list[RaceContext]) -> None:  # noqa: ARG002
        return None

    def predict_race(self, race: RaceContext) -> dict[str, Prediction]:
        horses = race.started_horses
        raw = [self.skill if h.horse_number == 1 else 1.0 for h in horses]
        s = sum(raw)
        win = [min(max(r / s, _EPS), 1.0 - _EPS) for r in raw]
        top2, top3 = harville_topk(win)
        return {
            h.horse_id: Prediction(win=win[i], top2=top2[i], top3=top3[i])
            for i, h in enumerate(horses)
        }


class YearSkillFakePredictor:
    """Feature 023: skill varies by the VALID race's year, so the candidate can win some folds and
    lose others — lets tests exercise the strict-majority and worst-fold-LogLoss guards."""

    is_leaky_reference = False

    def __init__(self, skill_by_year: dict[int, float], default_skill: float = 8.0) -> None:
        self.skill_by_year = skill_by_year
        self.default_skill = default_skill

    def fit(self, train_races: list[RaceContext]) -> None:  # noqa: ARG002
        return None

    def predict_race(self, race: RaceContext) -> dict[str, Prediction]:
        skill = max(self.skill_by_year.get(race.race_date.year, self.default_skill), 1.0)
        horses = race.started_horses
        raw = [skill if h.horse_number == 1 else 1.0 for h in horses]
        s = sum(raw)
        win = [min(max(r / s, _EPS), 1.0 - _EPS) for r in raw]
        top2, top3 = harville_topk(win)
        return {
            h.horse_id: Prediction(win=win[i], top2=top2[i], top3=top3[i])
            for i, h in enumerate(horses)
        }


# Per-column "importance" used by the ablation factory below: dropping a column lowers skill by its
# weight. human_form columns are made more important than recent_form so the ablation contributions
# are distinguishable (T014 / SC-007).
_COL_WEIGHT = {
    "jockey_win_rate": 3.0,
    "trainer_win_rate": 3.0,   # human_form total = 6.0
    "avg_last3_finish": 0.5,
    "recent_win_rate": 0.5,    # recent_form total = 1.0
    "dist_band_win_rate": 1.0,
    "dist_band_avg_finish": 0.5,
    "surface_win_rate": 0.5,   # aptitude total = 2.0
    "class_transition": 0.5,
    "field_size": 0.5,         # race_condition total = 1.0
    # Feature 023: pace_time total = 4.0 (heavier) vs position_style total = 0.7 (lighter),
    # so ablation can distinguish the two 023 groups.
    "rel_last3f_avg": 1.0, "rel_last3f_best": 0.5, "rel_time_avg": 1.0,
    "finish_diff_avg": 1.0, "finish_diff_best": 0.5,
    "rel_corner_pos_avg": 0.3, "front_runner_rate": 0.2, "closer_rate": 0.2,
}


def ablation_predictor_factory(base_skill: float = 8.0):
    """make_predictor(drop) → FakePredictor whose skill drops by the weight of the dropped columns."""

    def make(drop: tuple[str, ...]):
        penalty = sum(_COL_WEIGHT.get(c, 0.0) for c in drop)
        return FakePredictor(skill=base_skill - penalty)

    return make
