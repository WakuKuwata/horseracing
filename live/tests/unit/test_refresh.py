"""Feature 050: refresh_range wiring — order (predict THEN recommend), argument/force
propagation, and stage isolation (a predict crash does not skip the recommend stage).
Both underlying stages have their own coverage (serving 044 / betting 043 tests); this
tests ONLY the bundling.
"""

from __future__ import annotations

import datetime

import horseracing_betting.cli as betting_cli
import horseracing_serving.pipeline as serving_pipeline
from horseracing_serving.pipeline import BackfillCounts

from horseracing_live.orchestrate import refresh_range

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


# --- 「静かに成功」を止める(2026-08-24) -------------------------------------------------------
#
# 両ステージは失敗を**内部で**隔離して正常に返る。全日が例外を出した range も、例外ではなく
# error_days=N を持った counts として返ってくる。終了コードはステージのクラッシュだけを見て
# いたので、ops の refresh ジョブは rc=0 を SUCCEEDED に写し、UI には「更新成功」と出た —
# 予測が 1 件も書かれていないのに。判定を RefreshReport.ok の 1 箇所に置いて、CLI と
# 他の呼び出し元が食い違えないようにする。

def _report(**over):
    from horseracing_live.orchestrate import RefreshReport

    base = {
        "date_from": _FROM, "date_to": _TO,
        "predict": {"generated": 3, "skip_exists": 0, "skip_no_started": 0,
                    "error_days": 0, "skip_no_odds": 0},
        "predict_error": None,
        "recommend": {"races": 3, "generated": 3, "topped_up": 0, "skip_no_run": 0,
                      "skip_no_odds": 0, "skip_exists": 0, "error": 0},
        "recommend_error": None,
    }
    base.update(over)
    return RefreshReport(**base)


def test_a_clean_refresh_is_ok():
    assert _report().ok is True


def test_isolated_predict_failures_are_not_success():
    """全日が落ちても例外は出ない。それを成功と報告していたのが本件."""
    rep = _report(predict={"generated": 0, "skip_exists": 0, "skip_no_started": 0,
                           "error_days": 2, "skip_no_odds": 0})
    assert rep.ok is False


def test_isolated_recommend_failures_are_not_success():
    rep = _report(recommend={"races": 3, "generated": 0, "topped_up": 0, "skip_no_run": 0,
                             "skip_no_odds": 0, "skip_exists": 0, "error": 1})
    assert rep.ok is False


def test_a_stage_crash_is_still_not_success():
    assert _report(predict=None, predict_error="RuntimeError: boom").ok is False
    assert _report(recommend=None, recommend_error="RuntimeError: boom").ok is False


def test_the_cause_of_each_failed_day_is_kept(monkeypatch):
    """件数だけでは行動できない。どの日がなぜ落ちたかを残す。

    recommend 側は以前からレース単位のエラー文言を出していたが、predict 側は例外を
    握り潰して数だけ残していた。
    """
    def boom_predict(session, *, date_from, date_to, force=False, **kw):
        return BackfillCounts(error_days=1, errors=[("2024-12-28", "ValueError: bad")])

    def ok_recommend(session, *, date_from, date_to, **kw):
        return {"races": 0, "generated": 0, "topped_up": 0, "skip_no_run": 0,
                "skip_no_odds": 0, "skip_exists": 0, "error": 0}

    monkeypatch.setattr(serving_pipeline, "run_serving_backfill", boom_predict)
    monkeypatch.setattr(betting_cli, "recommend_backfill", ok_recommend)

    rep = refresh_range(object(), date_from=_FROM, date_to=_TO)
    assert rep.ok is False
    assert rep.predict["errors"] == [{"day": "2024-12-28", "error": "ValueError: bad"}]
