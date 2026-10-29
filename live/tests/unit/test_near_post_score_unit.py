"""175: the live side of the scoring without a DB — replay gate, candidate registry, refusals."""

from __future__ import annotations

import datetime
import hashlib
import json

import numpy as np
import pandas as pd
import pytest
from horseracing_eval import decision_quote_scoring as dqs

from horseracing_live import cli
from horseracing_live import near_post as np_
from horseracing_live import near_post_score as nps

UTC = datetime.UTC
DAY = datetime.date(2026, 10, 31)
POST = datetime.datetime(2026, 10, 31, 6, 0, tzinfo=UTC)
RACE = "202610310101"


def _record(p_ens=(0.6, 0.3, 0.1), sha="f" * 64):
    horses = [dqs.QuoteHorse(f"H{i+1:02d}", i + 1, float(o), i + 1, float(p), float(p) * o,
                             float(p), float(p) * o, None, None)
              for i, (p, o) in enumerate(zip(p_ens, (2.0, 5.0, 20.0), strict=True))]
    started = {h.horse_id: h.horse_number for h in horses}
    return dqs.QuoteRecord(
        race_id=RACE, race_date=DAY, post_time=POST, track_type="芝",
        captured_at=POST - datetime.timedelta(seconds=180), official_at=POST - datetime.timedelta(seconds=190),
        seconds_to_post=180, capture_target="t_minus_3", horses=horses, mix_status="no_coefficients",
        field_digest=dqs.digest_of_started(started), started=started, has_results=True,
        winner_horse_ids=frozenset({"H01"}), payouts={1: 200}, closing=None,
        meta={"features_sha256": sha},
    )


def _features(sha=None):
    frame = pd.DataFrame({
        "race_id": [RACE] * 3, "horse_id": ["H01", "H02", "H03"], "horse_number": [1.0, 2.0, 3.0],
        "odds": [9.0, 9.0, 9.0], "popularity": [1.0, 2.0, 3.0],
    })
    return np_.DayFeatures(day=DAY, frame=frame, sha256=sha or np_.frame_sha256(frame), built_at=POST)


class EchoPredictor:
    """Returns the stored p_ens (bit-exact) or a perturbed copy."""

    def __init__(self, p_by_id, *, perturb=0.0):
        self.p_by_id, self.perturb = p_by_id, perturb

    def predict(self, rows, *, race_date):
        pred = []
        for hid, hn, o in zip(rows["horse_id"], rows["horse_number"], rows["odds"], strict=True):
            p = self.p_by_id[str(hid)] + self.perturb
            pred.append({"horse_id": str(hid), "horse_number": int(hn), "p_ens": p, "ev_ens": p * float(o),
                         "p_single": p, "ev_single": p * float(o), "p_mix": None, "ev_mix": None})
        return np_.PredictionBundle(pred, "no_coefficients", None, None, "mev-ens15-v1", "ensemble_2026.json",
                                    "e" * 64, "mev-binary-v2", "model_2026.txt", "b" * 64)


def test_replay_gate_requires_bit_equality():
    rec = _record()
    feats = _features()
    exact = EchoPredictor({h.horse_id: h.p_ens for h in rec.horses})
    rp = nps.replay_race(rec, feats, exact, features_source="parquet")
    assert rp.reproducible and rp.max_abs_diff == 0.0 and rp.features_sha_match is False
    # the quote replaced the morning odds (9.0) with the stored ones
    assert rp.rows["odds"].tolist() == [2.0, 5.0, 20.0]
    off = EchoPredictor({h.horse_id: h.p_ens for h in rec.horses}, perturb=1e-9)
    rp2 = nps.replay_race(rec, feats, off, features_source="rebuilt")
    assert not rp2.reproducible and rp2.max_abs_diff == pytest.approx(1e-9) and rp2.p_ens is None
    assert nps.replay_race(rec, None, exact, features_source="x").features_source == "unavailable"


def test_candidate_races_prices_148_on_the_reproduced_rows(tmp_path):
    rec = _record()
    feats = _features()
    np_.save_day_features(feats, tmp_path)
    rec.meta = {"features_sha256": feats.sha256}
    cand = nps.Candidate("ens15-norm148-v1", "postprocess", None, "x", 1.2, DAY, None, "", "mev-ens15-v1")
    exact = EchoPredictor({h.horse_id: h.p_ens for h in rec.horses})
    out, stats = nps.candidate_races([rec], [cand], predictor_for=lambda y: exact, features_dir=tmp_path,
                                     rebuild=None)
    cr = out["ens15-norm148-v1"][RACE]
    assert cr.reproducible and cr.features_source == "parquet" and cr.features_sha_match is True
    # p = (0.6, 0.3, 0.1) sums to 1 → EV_norm == stored ev_ens
    assert cr.ev_by_number == pytest.approx({1: 1.2, 2: 1.5, 3: 2.0})
    assert stats == {"n_replayed": 1, "n_reproducible": 1, "days": {DAY.isoformat(): {feats.sha256[:12]: "parquet"}}}


def test_candidate_races_rebuilds_when_parquet_is_absent_and_counts_unavailable(tmp_path):
    rec = _record(sha="0" * 64)
    feats = _features()
    cand = nps.Candidate("c", "postprocess", None, "x", 1.2, DAY, None, "", "v")
    exact = EchoPredictor({h.horse_id: h.p_ens for h in rec.horses})
    out, stats = nps.candidate_races([rec], [cand], predictor_for=lambda y: exact, features_dir=tmp_path,
                                     rebuild=lambda day: feats)
    assert out["c"][RACE].features_source == "rebuilt" and out["c"][RACE].reproducible
    out2, _ = nps.candidate_races([rec], [cand], predictor_for=lambda y: exact, features_dir=tmp_path,
                                  rebuild=None)
    assert out2["c"][RACE].features_source == "unavailable" and not out2["c"][RACE].reproducible


def _write_candidates(tmp_path, sha):
    p = tmp_path / "candidates.json"
    p.write_text(json.dumps([{
        "name": "ens15-norm148-v1", "kind": "postprocess", "artifact_path": None, "artifact_sha256": sha,
        "mark_rule": {"column": "expected_return", "threshold": 1.2}, "registered_on": "2026-10-30",
        "train_through": None, "note": "148"}]))
    return p


def test_load_candidates_verifies_the_code_sha(tmp_path):
    good = hashlib.sha256(nps.EVAL_MODULE_PATH.read_bytes()).hexdigest()
    cands, file_sha = nps.load_candidates(_write_candidates(tmp_path, good), names=["ens15-norm148-v1"])
    assert cands[0].registered_on == datetime.date(2026, 10, 30) and cands[0].threshold == 1.2
    assert file_sha == hashlib.sha256((tmp_path / "candidates.json").read_bytes()).hexdigest()
    with pytest.raises(nps.ScoreRefused, match="artifact sha"):
        nps.load_candidates(_write_candidates(tmp_path, "0" * 64), names=["ens15-norm148-v1"])
    with pytest.raises(nps.ScoreRefused, match="not registered"):
        nps.load_candidates(_write_candidates(tmp_path, good), names=["nope"])
    assert nps.load_candidates(_write_candidates(tmp_path, good))[0] == []   # no names → none chosen


def test_rule_freeze_check(tmp_path):
    assert nps.assert_rule_frozen() == nps.RULE_SHA.read_text().split()[0]
    tampered = tmp_path / "rule.json"
    tampered.write_text(nps.RULE_JSON.read_text().replace("300", "301"))
    with pytest.raises(nps.ScoreRefused):
        nps.assert_rule_frozen(tampered, nps.RULE_SHA)


def test_cli_refusals_before_any_db_access(tmp_path, capsys):
    existing = tmp_path / "out.json"
    existing.write_text("{}")
    assert cli.main(["near-post-score", "--from", "2026-10-31", "--to", "2026-10-31", "--out", str(existing)]) == 2
    assert "exists" in capsys.readouterr().out
    assert cli.main(["near-post-score", "--from", "2026-11-01", "--to", "2026-10-31"]) == 2
    bad = _write_candidates(tmp_path, "0" * 64)
    assert cli.main(["near-post-score", "--from", "2026-10-31", "--to", "2026-10-31", "--candidate",
                     "ens15-norm148-v1", "--candidates", str(bad), "--out", str(tmp_path / "x.json")]) == 2
    assert "artifact sha" in capsys.readouterr().out


def test_summary_lines_use_the_report_vocabulary():
    rec = _record()
    report = dqs.build_report([rec], tz=np_.JST)
    doc = nps.assemble(report, date_from=DAY, date_to=DAY, n_rows=1, rule_sha="r", candidates_sha=None,
                       run_id="abc", computed_at=POST, replay_stats=None)
    lines = nps.summary_lines(doc)
    assert lines[0].startswith("300 点未満")
    assert any("expected_return:" in ln for ln in lines) and "verdict_overall=no_candidate" in lines
    assert dqs.forbidden_vocabulary(doc) == []
    assert doc["inputs"]["code_sha256"] and doc["run"]["logic_version"] == nps.LOGIC_VERSION
    assert isinstance(np.float64(1.0), float)
