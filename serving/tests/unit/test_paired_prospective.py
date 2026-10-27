"""133: the paired prospective record/score path must never back-date, leak or silently pool."""

import datetime as dt
import decimal
import json
import math

import numpy as np
import pandas as pd
import pytest
from horseracing_eval.stage_discount import StageDiscount
from horseracing_training.predictor import assemble_predictions

from horseracing_serving import paired_prospective as pp

UTC = dt.UTC
SD = StageDiscount(0.8583894860493742, 0.7156577981098011, 0, 0, False)
BUNDLE_SHA = "28f39715a9c1dba13755ee44f3a439099d5aaffd94685ac11e4d23166b388471"
ANCHOR_SHA = "80fd7734f2d77f729bc71350a584c5ac5fb0c171b90940972fe7ad224e6537b3"


def plan(**over):
    base = {
        "artifact_kind": pp.PREREG_KIND,
        "_sha256": "a" * 64,
        "primary": {"regime": "preweight"},
        "comparators": {
            "new_bundle_manifest_sha256": BUNDLE_SHA,
            "old_anchor_sha256": ANCHOR_SHA,
            "display_lambda2": SD.lambda2,
            "display_lambda3": SD.lambda3,
        },
        "window": {"from": "2026-09-13", "to": "2026-12-28", "min_eval_days": 2},
        "bootstrap": {"b": 200, "seed": 1},
        "decision_rule": "95% CI sign",
        "retraining_variance_note": {"sd_fold_historical": 0.001816, "status": "context"},
    }
    base.update(over)
    return base


def heads(win, sd=SD):
    ids = sorted(win)
    w = np.asarray([win[h] for h in ids], dtype=float)
    return pp._display(ids, w, sd)


def record(race_id, day, win_new, win_old, *, source="capture", classification="prospective",
           pre_result=True, regimes=("preweight", "serving"), weighed=0, bundle=BUNDLE_SHA,
           anchor=ANCHOR_SHA, lam=(SD.lambda2, SD.lambda3)):
    ids = sorted(win_new)
    reg = {
        r: {
            "regime": r, "started_ids": ids, "new": heads(win_new), "old": heads(win_old),
            "regime_audit": {"n_started": len(ids), "n_weighed_before": weighed},
        }
        for r in regimes
    }
    return {
        "schema_version": 1, "artifact_kind": pp.RECORD_KIND, "source": source,
        "classification": classification, "pre_result": pre_result, "race_id": race_id,
        "race_day": day, "regimes": reg,
        "db_weight_state_at_capture": {
            "n_started": len(ids), "n_started_with_weight": weighed,
            "publication_state": "prepublication" if weighed == 0 else "partial",
        },
        "serving_equals_preweight": weighed == 0,
        "comparators": {
            "new": {"bundle_manifest_sha256": bundle}, "old": {"anchor_sha256": anchor},
            "shared_display_stage_discount": {"lambda2": lam[0], "lambda3": lam[1]},
        },
        "can_adopt": False,
    }


# --- files ------------------------------------------------------------------------------------


def test_publish_json_is_exclusive_atomic_and_rejects_nonfinite(tmp_path):
    p = tmp_path / "r.json"
    pp.publish_json(p, {"a": 1})
    with pytest.raises(FileExistsError):
        pp.publish_json(p, {"a": 2})
    assert json.loads(p.read_text()) == {"a": 1}
    with pytest.raises(pp.PairedError):
        pp.publish_json(tmp_path / "bad.json", {"x": float("nan")})
    assert sorted(x.name for x in tmp_path.iterdir()) == ["r.json"]  # no temp file left behind


def test_encode_rows_keeps_types_and_nulls():
    rows = pd.DataFrame({
        "horse_id": ["b", "a"], "i": [np.int64(3), 4], "f": [1.5, np.nan],
        "s": ["牡", None], "d": [decimal.Decimal("0.25"), decimal.Decimal("1")], "flag": [True, False],
    })
    enc = pp.encode_rows(rows)
    assert enc["columns"] == ["i", "f", "s", "d", "flag"]
    assert enc["rows"]["b"] == [3, 1.5, "牡", 0.25, True]
    assert enc["rows"]["a"] == [4, None, None, 1.0, False]
    assert enc["dtypes"]["f"] == "float64"


def test_rows_from_snapshots_decodes_nulls_and_orders_columns():
    snaps = {"h2": {"x": 1, "y": None, "c": "A"}, "h1": {"x": 2.5, "y": 0.1, "c": None}}
    frame = pp.rows_from_snapshots(snaps, race_id="r", race_date=dt.date(2026, 9, 13), feature_cols=["x", "y", "c"])
    assert list(frame.columns) == ["race_id", "horse_id", "race_date", "x", "y", "c"]
    assert frame.horse_id.tolist() == ["h1", "h2"]
    assert math.isnan(frame.loc[1, "y"]) and frame.loc[0, "c"] is np.nan or pd.isna(frame.loc[0, "c"])
    with pytest.raises(pp.PairedError):
        pp.rows_from_snapshots({"h": {"x": 1}}, race_id="r", race_date=dt.date(2026, 9, 13), feature_cols=["x", "y"])
    with pytest.raises(pp.PairedError):
        pp.rows_from_snapshots({}, race_id="r", race_date=dt.date(2026, 9, 13), feature_cols=["x"])


# --- display convention -------------------------------------------------------------------------


def test_display_keeps_win_exact_and_uses_stage_discount():
    ids = ["a", "b", "c"]
    win = np.asarray([0.6, 0.3999995, 5e-7])  # one sub-clip horse: eps=1e-6 would renormalise
    out = pp._display(ids, win, SD)
    assert [out[h][0] for h in ids] == win.tolist()
    ref = assemble_predictions(ids, win, eps=0.0, stage_discount=SD)
    assert out["a"][1] == ref["a"].top2 and out["c"][2] == ref["c"].top3
    other = pp._display(ids, win, StageDiscount(1.0, 1.0, 0, 0, True))
    assert [other[h][0] for h in ids] == win.tolist()  # λ never touches win
    assert other["a"][1] != out["a"][1]


def test_adapt_shadow_record_rederives_served_heads_and_keeps_research_heads():
    shadow = {
        "artifact_kind": "mixture_shadow_record", "classification": "prospective", "pre_result": True,
        "race_id": "r1", "race_day": "2026-09-13", "scheduled_start": "2026-09-13T00:45:00+00:00",
        "n_result_rows_at_capture": 0, "input_captured_at": "t0", "predicted_at": "t1",
        "primary_regime": "preweight",
        "regime_audit": {"regime": "preweight", "n_started": 2, "n_weighed_before": 0},
        "anchor_stage_discount": {"lambda2": SD.lambda2, "lambda3": SD.lambda3},
        "candidate_started_ids": ["a", "b"],
        "candidate": {"a": [0.7, 0.99, 0.99], "b": [0.3, 0.99, 0.99]},  # research heads are bogus on purpose
        "anchor": {"a": [0.6, 0.9, 1.0], "b": [0.4, 0.9, 1.0]},
        "candidate_input_sha256": "i" * 64, "history_sha256": "h" * 64, "n_history_rows": 3,
        "bundle_manifest_sha256": BUNDLE_SHA, "bundle_id": "b", "anchor_model_sha256": ANCHOR_SHA,
        "anchor_model_version": "lgbm-094-cap900",
    }
    r = pp.adapt_shadow_record(shadow)
    reg = r["regimes"]["preweight"]
    assert r["source"] == "shadow_129" and reg["new"]["a"][0] == 0.7
    assert reg["new"]["a"][1] == heads({"a": 0.7, "b": 0.3})["a"][1]  # served convention, not 0.99
    assert reg["new_research_heads_not_served"]["a"] == [0.7, 0.99, 0.99]
    assert reg["old"] == shadow["anchor"]
    assert r["db_weight_state_at_capture"]["publication_state"] == "prepublication"


# --- selection -----------------------------------------------------------------------------------


def test_select_records_precedence_identity_and_no_fallback(tmp_path):
    w_new, w_old = {"a": 0.5, "b": 0.5}, {"a": 0.6, "b": 0.4}
    pp.publish_json(tmp_path / "capture" / "r1.json", record("r1", "2026-09-13", w_new, w_old))
    pp.publish_json(tmp_path / "production_snapshot" / "r1.json",
                    record("r1", "2026-09-13", w_new, w_old, source="production_snapshot"))
    pp.publish_json(tmp_path / "production_snapshot" / "r2.json",
                    record("r2", "2026-09-13", w_new, w_old, source="production_snapshot"))
    # r3: capture has a wrong bundle -> the race is rejected even though a snapshot record is fine
    pp.publish_json(tmp_path / "capture" / "r3.json", record("r3", "2026-09-13", w_new, w_old, bundle="0" * 64))
    pp.publish_json(tmp_path / "production_snapshot" / "r3.json",
                    record("r3", "2026-09-13", w_new, w_old, source="production_snapshot"))
    # r4: rehearsal is never evidence
    pp.publish_json(tmp_path / "capture" / "r4.json",
                    record("r4", "2026-09-06", w_new, w_old, classification="rehearsal", pre_result=False))
    chosen, rejected = pp.select_records([tmp_path], plan=plan())
    assert set(chosen) == {"r1", "r2"}
    assert chosen["r1"]["source"] == "capture" and chosen["r2"]["source"] == "production_snapshot"
    assert any(v == "new_identity_mismatch" for v in rejected.values())
    assert any(v == "not_prospective" for v in rejected.values())
    diag, _ = pp.select_records([tmp_path], plan=plan(), allow_rehearsal=True)
    assert set(diag) == {"r4"}


def test_select_records_merges_the_two_129_regime_files(tmp_path):
    base = {
        "artifact_kind": "mixture_shadow_record", "classification": "prospective", "pre_result": True,
        "race_id": "r1", "race_day": "2026-09-13", "n_result_rows_at_capture": 0,
        "input_captured_at": "t0", "predicted_at": "t1",
        "anchor_stage_discount": {"lambda2": SD.lambda2, "lambda3": SD.lambda3},
        "candidate_started_ids": ["a", "b"], "candidate": {"a": [0.7, 0, 0], "b": [0.3, 0, 0]},
        "anchor": {"a": [0.6, 0, 0], "b": [0.4, 0, 0]}, "candidate_input_sha256": "i" * 64,
        "bundle_manifest_sha256": BUNDLE_SHA, "anchor_model_sha256": ANCHOR_SHA,
        "anchor_model_version": "lgbm-094-cap900",
    }
    for regime in ("preweight", "serving"):
        pp.publish_json(tmp_path / regime / "r1.json", {**base, "primary_regime": regime,
                        "regime_audit": {"regime": regime, "n_started": 2, "n_weighed_before": 0}})
    chosen, rejected = pp.select_records([tmp_path], plan=plan())
    assert rejected == {} and set(chosen["r1"]["regimes"]) == {"preweight", "serving"}


# --- eligibility protocol ----------------------------------------------------------------------


def bundle(results, started=None):
    return {"results": results, "started_now": set(started if started is not None else results)}


def test_eligibility_protocol_cases():
    r = record("r", "2026-09-13", {"a": 0.5, "b": 0.3, "c": 0.2}, {"a": 0.4, "b": 0.4, "c": 0.2})
    assert pp.eligibility(r, bundle({}), regime="preweight")[:2] == ("pending", "results_not_ingested")
    assert pp.eligibility(r, bundle({}), regime="full_info")[:2] == ("excluded", "regime_not_recorded")
    # incomplete results, same field -> pending
    assert pp.eligibility(r, bundle({"a": (1, "finished")}, started=["a", "b", "c"]), regime="preweight")[:2] == (
        "pending", "results_incomplete")
    # c scratched after capture (no result row, started set shrank) -> excluded
    assert pp.eligibility(r, bundle({"a": (1, "finished"), "b": (2, "finished")}), regime="preweight")[:2] == (
        "excluded", "field_changed_after_capture")
    # late addition d -> excluded
    full = {"a": (1, "finished"), "b": (2, "finished"), "c": (3, "finished")}
    assert pp.eligibility(r, bundle({**full, "d": (4, "finished")}), regime="preweight")[:2] == (
        "excluded", "field_changed_after_capture")
    # first-place dead heat -> excluded
    assert pp.eligibility(r, bundle({"a": (1, "finished"), "b": (1, "finished"), "c": (3, "finished")}),
                          regime="preweight")[:2] == ("excluded", "dead_heat_or_no_unique_winner")
    # stopped horse: NULL finish, label 0 everywhere; 2nd-place tie counts both as top2
    st, reason, sc = pp.eligibility(
        r, bundle({"a": (1, "finished"), "b": (2, "finished"), "c": (None, "stopped")}), regime="preweight")
    assert (st, reason, sc["winner"]) == ("scored", None, "a")
    assert sc["labels"] == {"a": (1, 1, 1), "b": (0, 1, 1), "c": (0, 0, 0)}
    st, _, sc = pp.eligibility(
        r, bundle({"a": (1, "finished"), "b": (2, "finished"), "c": (2, "finished")}), regime="preweight")
    assert sc["labels"]["c"] == (0, 1, 1)
    with pytest.raises(pp.PairedError):
        pp.eligibility(r, bundle({"a": (1, "finished"), "b": (2, "finished"), "c": (3, "disqualified")}),
                       regime="preweight")
    with pytest.raises(pp.PairedError):
        pp.eligibility(r, bundle({"a": (1, "finished"), "b": (None, "finished"), "c": (3, "finished")}),
                       regime="preweight")


# --- scoring -------------------------------------------------------------------------------------


def _results(winner, ids):
    return {"results": {h: (1 if h == winner else i + 2, "finished") for i, h in enumerate(ids)},
            "started_now": set(ids)}


def test_score_records_hand_computed_and_mode_separation():
    chosen = {
        "d1r1": record("d1r1", "2026-09-13", {"a": 0.5, "b": 0.5}, {"a": 0.25, "b": 0.75}),
        "d1r2": record("d1r2", "2026-09-13", {"a": 0.2, "b": 0.8}, {"a": 0.5, "b": 0.5}),
        "d2r1": record("d2r1", "2026-09-20", {"a": 0.4, "b": 0.6}, {"a": 0.4, "b": 0.6}, weighed=1),
        "d2r2": record("d2r2", "2026-09-20", {"a": 0.9, "b": 0.1}, {"a": 0.8, "b": 0.2}),
        "pend": record("pend", "2026-09-20", {"a": 0.9, "b": 0.1}, {"a": 0.8, "b": 0.2}),
    }
    results = {"d1r1": _results("a", ["a", "b"]), "d1r2": _results("b", ["a", "b"]),
               "d2r1": _results("a", ["a", "b"]), "d2r2": _results("a", ["a", "b"]),
               "pend": {"results": {}, "started_now": {"a", "b"}}}
    attrs = {k: {"distance": 1600, "track_type": "芝", "race_class": "１勝"} for k in chosen}
    out = pp.score_records(chosen, results, attrs, plan=plan(), regime="preweight", mode="interim")
    diffs = [-math.log(0.5) + math.log(0.25), -math.log(0.8) + math.log(0.5), 0.0, -math.log(0.9) + math.log(0.8)]
    assert out["overall"]["n_races"] == 4 and out["overall"]["n_days"] == 2
    assert out["overall"]["diff_winner_nll"] == pytest.approx(float(np.mean(diffs)))
    assert out["overall"]["ci95"][0] is not None and out["status"] == "INTERIM_STATUS_NOT_A_VERDICT"
    assert out["verdict"] is None and out["pending_counts"] == {"results_not_ingested": 1}
    assert out["overall"]["top1_agreement"] == 0.5 and out["overall"]["new_top1_hit"] == 0.75
    assert out["conditions"]["weight_publication_state"]["partial"]["n_races"] == 1
    assert out["conditions"]["race_class"]["conditions"]["n_races"] == 4
    win_new = out["heads_served_convention_frozen_lambda"]["win"]["new"]
    assert win_new["n_horses"] == 8 and win_new["realized_rate"] == 0.5
    assert out["retraining_variance_note"]["sd_fold_historical"] == 0.001816
    # final mode gates on the window, pending results and min days
    fin = pp.score_records(chosen, results, attrs, plan=plan(), regime="preweight", mode="final",
                           today=dt.date(2026, 12, 1))
    assert fin["status"] == "NO_DECISION" and any("window not over" in r for r in fin["no_decision_reasons"])
    assert any("pending" in r for r in fin["no_decision_reasons"])
    del chosen["pend"], results["pend"]
    fin = pp.score_records(chosen, results, attrs, plan=plan(), regime="preweight", mode="final",
                           today=dt.date(2026, 12, 29))
    assert fin["status"] == "FINAL" and fin["verdict"] in ("NEW_BETTER", "OLD_BETTER", "INCONCLUSIVE")
    with pytest.raises(pp.PairedError):
        pp.score_records(chosen, results, attrs, plan=plan(), regime="preweight", mode="sneak_peek")


def test_final_verdict_rules_without_minimum_effect():
    base = {"n_days": 30, "ci95": [-0.0010, -0.0001], "diff_winner_nll": -0.0005}
    today = dt.date(2026, 12, 29)
    assert pp.final_verdict(base, plan(), {}, regime="preweight", today=today)["verdict"] == "NEW_BETTER"  # tiny but CI<0
    assert pp.final_verdict({**base, "ci95": [0.0001, 0.001]}, plan(), {}, regime="preweight", today=today)["verdict"] == "OLD_BETTER"
    assert pp.final_verdict({**base, "ci95": [-0.001, 0.001]}, plan(), {}, regime="preweight", today=today)["verdict"] == "INCONCLUSIVE"
    assert pp.final_verdict(base, plan(), {}, regime="preweight", today=today, unsettled_in_window=3)["status"] == "NO_DECISION"
    assert pp.final_verdict({**base, "n_days": 1}, plan(), {}, regime="preweight", today=today)["status"] == "NO_DECISION"
    assert pp.final_verdict(base, plan(), {}, regime="preweight", today=dt.date(2026, 12, 28))["status"] == "NO_DECISION"


def test_condition_bands_and_argmax_tiebreak():
    assert [pp.field_band(n) for n in (8, 9, 13, 14)] == ["<=8", "9-13", "9-13", ">=14"]
    assert [pp.distance_band(d) for d in (1400, 1401, 1800, 2200, 2201, None)] == [
        "<=1400", "<=1800", "<=1800", "<=2200", ">2200", "unknown"]
    assert [pp.race_class_group(c) for c in ("新馬", "１勝", "Ｇ１", None)] == ["maiden", "conditions", "open", "unknown"]
    assert pp._argmax({"b": [0.5, 0, 0], "a": [0.5, 0, 0]}) == "a"


# --- capture guards --------------------------------------------------------------------------------


def test_prospective_capture_refuses_an_injected_clock():
    with pytest.raises(pp.PairedError):
        pp.capture_day(None, race_ids=["r"], comparators=None, out_dir="/nonexistent",
                       now=dt.datetime(2026, 9, 13, tzinfo=UTC), classification="prospective")


@pytest.mark.parametrize('day,reason', [
    ('2026-09-12', 'outside_registered_window'),
    ('2026-12-29', 'outside_registered_window'),
    ('bad-date', 'invalid_race_day'), (None, 'invalid_race_day'),
])
def test_window_rejected_by_selection_and_direct_scoring(tmp_path, day, reason):
    r = record('r', day, {'a': .8, 'b': .2}, {'a': .2, 'b': .8})
    pp.publish_json(tmp_path / 'r.json', r)
    chosen, rejected = pp.select_records([tmp_path], plan=plan())
    assert not chosen and list(rejected.values()) == [reason]
    out = pp.score_records({'r': r}, {}, {}, plan=plan(), regime='preweight', mode='final',
                           today=dt.date(2027, 1, 2))
    assert out['excluded'] == {'r': reason} and not out['pending']
    assert out['overall']['n_days'] == 0 and out['status'] == 'NO_DECISION'


def test_window_inclusive_and_diagnostic_interim_only(tmp_path):
    records = {str(i): record(str(i), day, {'a': .8, 'b': .2}, {'a': .2, 'b': .8})
               for i, day in enumerate(('2026-09-13', '2026-12-28'))}
    for rid, r in records.items():
        pp.publish_json(tmp_path / f'{rid}.json', r)
    chosen, rejected = pp.select_records([tmp_path], plan=plan())
    assert set(chosen) == set(records) and not rejected
    results = {rid: _results('a', ['a', 'b']) for rid in records}
    out = pp.score_records(records, results, {}, plan=plan(), regime='preweight', mode='final',
                           today=dt.date(2027, 1, 2))
    assert out['overall']['n_days'] == 2 and out['status'] == 'FINAL'
    r = record('r', '2026-09-06', {'a': .8, 'b': .2}, {'a': .2, 'b': .8},
               classification='rehearsal', pre_result=False)
    diagnostic = dict(plan=plan(), regime='preweight', diagnostic=True)
    out = pp.score_records({'r': r}, {'r': _results('a', ['a', 'b'])}, {},
                           mode='interim', **diagnostic)
    assert out['overall']['n_races'] == 1
    assert out['status'] == 'DIAGNOSTIC_REHEARSAL_NOT_EVIDENCE' and out['verdict'] is None
    with pytest.raises(pp.PairedError):
        pp.score_records({'r': r}, {}, {}, mode='final', **diagnostic)
    out = pp.score_records({'r': r}, {}, {}, plan=plan(), regime='preweight', mode='interim')
    assert out['excluded'] == {'r': 'not_prospective'}


def test_secondary_cannot_issue_verdict():
    base = {'n_days': 30, 'ci95': [-.01, -.001], 'diff_winner_nll': -.005}
    out = pp.final_verdict(base, plan(), {}, regime='serving', today=dt.date(2027, 1, 2))
    assert out['status'] == 'SECONDARY_SUMMARY_NOT_A_VERDICT' and out['verdict'] is None
    broken = plan()
    del broken['primary']
    with pytest.raises(pp.PairedError):
        pp.final_verdict(base, broken, {}, regime='preweight', today=dt.date(2027, 1, 2))


def final_report():
    return {'artifact_kind': pp.SCORE_KIND, 'mode': 'final', 'regime': 'preweight',
            'preregistration_sha256': plan()['_sha256'], 'window': plan()['window'],
            'status': 'FINAL', 'verdict': 'NEW_BETTER', 'can_adopt': False}


def test_final_receipt_reexports_without_recomputing_after_export_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, 'FINAL_RECEIPT_DIR', tmp_path / 'canonical')
    calls = []
    def compute():
        calls.append(1)
        return final_report()
    args = dict(plan=plan(), regime='preweight', mode='final', compute=compute)
    conflict = tmp_path / 'conflict.json'
    pp.publish_json(conflict, {'unrelated': True})
    with pytest.raises(FileExistsError):
        pp.publish_score_once(output=conflict, **args)
    first = pp.publish_score_once(output=tmp_path / 'first.json', **args)
    second = pp.publish_score_once(output=tmp_path / 'second.json', **args)
    assert first == second == final_report() and calls == [1]
    assert pp.publish_score_once(output=tmp_path / 'first.json', **args) == first
    assert len(list((tmp_path / 'canonical').glob('*.json'))) == 1


@pytest.mark.parametrize('status,regime,mode', [
    ('NO_DECISION', 'preweight', 'final'),
    ('SECONDARY_SUMMARY_NOT_A_VERDICT', 'serving', 'final'),
    ('INTERIM_STATUS_NOT_A_VERDICT', 'preweight', 'interim'),
])
def test_nonfinal_does_not_consume_final_slot(tmp_path, monkeypatch, status, regime, mode):
    monkeypatch.setattr(pp, 'FINAL_RECEIPT_DIR', tmp_path / 'canonical')
    pp.publish_score_once(plan=plan(), regime=regime, mode=mode, output=tmp_path / 'not-final.json',
        compute=lambda: {**final_report(), 'status': status, 'regime': regime, 'mode': mode, 'verdict': None})
    assert not list((tmp_path / 'canonical').glob('*.json'))
    pp.publish_score_once(plan=plan(), regime='preweight', mode='final',
                         output=tmp_path / 'final.json', compute=final_report)
    assert len(list((tmp_path / 'canonical').glob('*.json'))) == 1


def test_corrupt_receipt_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, 'FINAL_RECEIPT_DIR', tmp_path / 'canonical')
    args = dict(plan=plan(), regime='preweight', mode='final')
    pp.publish_score_once(output=tmp_path / 'first.json', compute=final_report, **args)
    receipt = next((tmp_path / 'canonical').glob('*.json'))
    doc = json.loads(receipt.read_text())
    doc['report']['verdict'] = 'OLD_BETTER'
    receipt.write_text(json.dumps(doc))
    def forbidden():
        pytest.fail('corrupted receipt must never trigger recalculation')
    with pytest.raises(pp.PairedError):
        pp.publish_score_once(output=tmp_path / 'second.json', compute=forbidden, **args)


def _concurrent_final_worker(root, barrier):
    """Spawn-compatible worker: two independent interpreters contend for one final slot."""
    import os
    import time
    from pathlib import Path
    root = Path(root)
    pp.FINAL_RECEIPT_DIR = root / 'canonical'
    def compute():
        with (root / 'computations.txt').open('a') as f:
            f.write(f'{os.getpid()}\n')
        time.sleep(.1)
        return final_report()
    barrier.wait(timeout=15)
    pp.publish_score_once(plan=plan(), regime='preweight', mode='final',
        output=root / f'export-{os.getpid()}.json', compute=compute)


def test_concurrent_processes_compute_one_final(tmp_path):
    import multiprocessing
    context = multiprocessing.get_context('spawn')
    barrier = context.Barrier(2)
    processes = [context.Process(target=_concurrent_final_worker, args=(str(tmp_path), barrier))
                 for _ in range(2)]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=25)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
    assert len((tmp_path / 'computations.txt').read_text().splitlines()) == 1
    exports = list(tmp_path.glob('export-*.json'))
    assert len(exports) == 2 and exports[0].read_bytes() == exports[1].read_bytes()


def test_cli_final_reexport_never_reopens_database(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, 'FINAL_RECEIPT_DIR', tmp_path / 'canonical')
    monkeypatch.setattr(pp, 'now_utc', lambda: dt.datetime(2027, 1, 2, tzinfo=UTC))
    registration = tmp_path / 'plan.json'
    pp.publish_json(registration, plan())
    records = tmp_path / 'records'
    for rid, day in (('r1', '2026-09-13'), ('r2', '2026-12-28')):
        pp.publish_json(records / f'{rid}.json',
                        record(rid, day, {'a': .8, 'b': .2}, {'a': .2, 'b': .8}))
    class ReadOnlySession:
        def __init__(self, engine): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def rollback(self): pass
    reads = []
    monkeypatch.setattr(pp, 'create_db_engine', lambda _: reads.append('engine'))
    monkeypatch.setattr(pp, 'Session', ReadOnlySession)
    monkeypatch.setattr(pp, 'read_only_snapshot', lambda _: reads.append('read_only'))
    monkeypatch.setattr(pp, 'load_results', lambda _, ids: {
        rid: _results('a', ['a', 'b']) for rid in ids})
    monkeypatch.setattr(pp, 'load_race_attributes', lambda *_: {})
    scheduled = {'r1': {'settled': True, 'race_date': '2026-09-13'},
                 'r2': {'settled': True, 'race_date': '2026-12-28'}}
    monkeypatch.setattr(pp, 'scheduled_races_in_window', lambda *_: scheduled)
    args = ['score', '--records', str(records), '--preregistration', str(registration),
            '--mode', 'final']
    assert pp.main([*args, '--regime', 'serving', '--output', str(tmp_path / 'secondary.json')]) == 0
    secondary = json.loads((tmp_path / 'secondary.json').read_text())
    assert secondary['status'] == 'SECONDARY_SUMMARY_NOT_A_VERDICT'
    assert secondary['verdict'] is None and not list((tmp_path / 'canonical').glob('*.json'))
    # Unsettled scheduled races gate the one official final at the CLI boundary.
    scheduled['r2']['settled'] = False
    pp.main([*args, '--output', str(tmp_path / 'pending.json')])
    assert json.loads((tmp_path / 'pending.json').read_text())['status'] == 'NO_DECISION'
    assert not list((tmp_path / 'canonical').glob('*.json'))
    scheduled['r2']['settled'] = True
    assert pp.main([*args, '--output', str(tmp_path / 'first.json')]) == 0
    first = json.loads((tmp_path / 'first.json').read_text())
    assert first['status'] == 'FINAL' and first['verdict'] == 'NEW_BETTER'
    assert reads == ['engine', 'read_only'] * 3
    def forbidden(*args, **kwargs):
        pytest.fail('an existing final must be exported before records or DB reads')
    monkeypatch.setattr(pp, 'select_records', forbidden)
    monkeypatch.setattr(pp, 'create_db_engine', forbidden)
    assert pp.main([*args, '--output', str(tmp_path / 'second.json')]) == 0
    assert json.loads((tmp_path / 'second.json').read_text()) == first
