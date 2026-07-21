"""Feature 050: refresh_range wiring — order (predict THEN recommend), argument/force
propagation, and stage isolation (a predict crash does not skip the recommend stage).
Both underlying stages have their own coverage (serving 044 / betting 043 tests); this
tests ONLY the bundling.
"""

from __future__ import annotations

import datetime

import horseracing_betting.cli as betting_cli
import horseracing_serving.pipeline as serving_pipeline
from horseracing_live.orchestrate import refresh_range
from horseracing_serving.pipeline import BackfillCounts

_FROM = datetime.date(2024, 12, 28)
_TO = datetime.date(2024, 12, 29)


def test_refresh_runs_predict_then_recommend_with_args(monkeypatch):
    calls = []

    def fake_predict(session, *, date_from, date_to, force=False, **kw):
        calls.append(("predict", date_from, date_to, force))
        return BackfillCounts(generated=3)

    def fake_recommend(session, *, date_from, date_to, **kw):
        calls.append(("recommend", date_from, date_to))
        return {"races": 3, "generated": 3, "topped_up": 0, "skip_no_run": 0,
                "skip_no_odds": 0, "skip_exists": 0, "error": 0}

    monkeypatch.setattr(serving_pipeline, "run_serving_backfill", fake_predict)
    monkeypatch.setattr(betting_cli, "recommend_backfill", fake_recommend)

    rep = refresh_range(object(), date_from=_FROM, date_to=_TO, force=True)

    assert [c[0] for c in calls] == ["predict", "recommend"]  # order is the contract (046/048)
    assert calls[0] == ("predict", _FROM, _TO, True)          # force propagates to predict only
    assert calls[1] == ("recommend", _FROM, _TO)
    assert rep.predict == {"generated": 3, "skip_exists": 0, "skip_no_started": 0,
                           "error_days": 0, "skip_no_odds": 0}  # Feature 060 added skip_no_odds
    assert rep.recommend["generated"] == 3
    assert rep.predict_error is None and rep.recommend_error is None


def test_refresh_propagates_materialized_to_predict_stage_only(monkeypatch):
    # Feature 055: --use-materialized reaches the prediction stage (which builds features);
    # the recommend stage builds no feature matrices, so its signature stays untouched.
    seen = {}

    def fake_predict(session, *, date_from, date_to, force=False,
                     use_materialized=False, materialized_path=None, **kw):
        seen["mat"] = (use_materialized, materialized_path)
        return BackfillCounts(generated=0)

    def fake_recommend(session, *, date_from, date_to, **kw):
        return {"races": 0, "generated": 0, "topped_up": 0, "skip_no_run": 0,
                "skip_no_odds": 0, "skip_exists": 0, "error": 0}

    monkeypatch.setattr(serving_pipeline, "run_serving_backfill", fake_predict)
    monkeypatch.setattr(betting_cli, "recommend_backfill", fake_recommend)

    refresh_range(object(), date_from=_FROM, date_to=_TO,
                  use_materialized=True, materialized_path="p.parquet")
    assert seen["mat"] == (True, "p.parquet")

    refresh_range(object(), date_from=_FROM, date_to=_TO)  # default OFF unchanged
    assert seen["mat"] == (False, None)


def test_predict_crash_does_not_skip_recommend(monkeypatch):
    ran = []

    def boom(session, **kw):
        raise RuntimeError("model artifact missing")

    def fake_recommend(session, *, date_from, date_to, **kw):
        ran.append("recommend")
        return {"races": 0, "generated": 0, "topped_up": 0, "skip_no_run": 0,
                "skip_no_odds": 0, "skip_exists": 0, "error": 0}

    monkeypatch.setattr(serving_pipeline, "run_serving_backfill", boom)
    monkeypatch.setattr(betting_cli, "recommend_backfill", fake_recommend)

    class _S:  # session stub with the rollback used by stage isolation
        def rollback(self):
            pass

    rep = refresh_range(_S(), date_from=_FROM, date_to=_TO)
    assert rep.predict is None and "model artifact missing" in rep.predict_error
    assert ran == ["recommend"]                       # idempotent stage still runs
    assert rep.recommend is not None and rep.recommend_error is None
