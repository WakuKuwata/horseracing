"""Feature 064: pure policy-gate scorer — same-population comparison, fold/band breakdown,
adoption rule, closing-oracle note, and the no-betting-import boundary. No DB, no predictor."""

from __future__ import annotations

from horseracing_eval.policy_gate import PolicyGateReport, evaluate_policy_gate


def _rows(years=(2010, 2011, 2012, 2013, 2014), races_per_year=4, fav_wins_per_year=1):
    """Each race: favourite (odds 3, p .6) + longshot (odds 50, p .4). Both are EV>=1 bets, but the
    longshot NEVER wins → it only bleeds. cap<21 excludes it, so cap recovery > current every year."""
    rows = []
    for y in years:
        for k in range(races_per_year):
            rid = f"{y}{k:04d}0101"
            fav_won = 1 if k < fav_wins_per_year else 0
            rows.append({"race_id": rid, "year": y, "p": 0.6, "odds": 3.0, "won": fav_won})
            rows.append({"race_id": rid, "year": y, "p": 0.4, "odds": 50.0, "won": 0})
    return rows


def test_policy_gate_uses_same_folds_same_race_set_current_vs_cap():
    rep = evaluate_policy_gate(_rows(), cap=21.0, threshold=1.0)
    assert isinstance(rep, PolicyGateReport)
    # both policies evaluated over the SAME race set (20 races, 5 folds)
    assert rep.n_races == 20 and rep.n_folds == 5
    ev, cap = rep.policies["ev"], rep.policies["ev_oddscap21"]
    # current bets fav+longshot every race (2 per race); cap bets only the fav (longshot >= 21)
    assert ev.n_bets == 40 and cap.n_bets == 20
    # cap drops the pure-bleed longshot → strictly higher recovery, same race set
    assert cap.recovery > ev.recovery


def test_policy_gate_report_by_fold_and_band():
    rep = evaluate_policy_gate(_rows(), cap=21.0, threshold=1.0)
    assert [f["year"] for f in rep.by_fold] == [2010, 2011, 2012, 2013, 2014]
    assert all(f["delta"] > 0 for f in rep.by_fold)          # cap improves every fold
    bands = {b["band"]: b for b in rep.by_odds_band}
    assert "21-51" in bands and bands["21-51"]["ev_recovery"] == 0.0  # longshot (odds 50) bleeds to 0
    assert bands["21-51"]["n"] == 20


def test_policy_gate_rejects_cap_selection_from_oos_results():
    """cap is a FIXED argument, never chosen from the results: a different cap yields a different
    report, and the scorer never searches caps. Passing cap=11 vs cap=21 both just apply the fixed
    value (no argmax over caps inside)."""
    r21 = evaluate_policy_gate(_rows(), cap=21.0)
    r11 = evaluate_policy_gate(_rows(), cap=11.0)
    assert r21.cap == 21.0 and r11.cap == 11.0
    assert "ev_oddscap21" in r21.policies and "ev_oddscap11" in r11.policies


def test_policy_gate_adoption_rule_and_note():
    rep = evaluate_policy_gate(_rows(), cap=21.0, threshold=1.0)
    # relative improvement + all folds improved + worst-fold delta >= 0 → adopted
    assert rep.n_folds_improved == rep.n_folds
    assert rep.worst_fold_delta >= 0.0
    assert rep.adopted is True
    # honest closing-oracle framing is always attached
    assert "CLOSING odds" in rep.note and "ROI>1 is NOT the bar" in rep.note


def test_policy_gate_not_adopted_when_cap_does_not_help():
    """If the longshot wins as often as the favourite, capping removes winning bets → no adoption."""
    rows = []
    for y in range(2010, 2013):
        for k in range(4):
            rid = f"{y}{k:04d}0101"
            rows.append({"race_id": rid, "year": y, "p": 0.5, "odds": 2.0, "won": 1 if k == 0 else 0})
            rows.append({"race_id": rid, "year": y, "p": 0.5, "odds": 50.0, "won": 1 if k == 1 else 0})
    rep = evaluate_policy_gate(rows, cap=21.0, threshold=1.0)
    # cap removes the 50.0 winner too → cap recovery not strictly better every fold → not adopted
    assert rep.adopted is False


def test_no_betting_or_training_import_in_policy_gate():
    """Constitution / cycle guard: eval.policy_gate must not IMPORT betting or training (mentions in
    prose are fine; only actual import statements would create a cycle)."""
    import horseracing_eval.policy_gate as m
    src = __import__("inspect").getsource(m)
    for line in src.splitlines():
        s = line.strip()
        assert not s.startswith(("import horseracing_betting", "from horseracing_betting"))
        assert not s.startswith(("import horseracing_training", "from horseracing_training"))
