"""175: score the decision quotes (174) — DB loading, candidate replay, report assembly.

Read-only: four bulk SELECTs assemble ``QuoteRecord`` objects (the eval module does the
arithmetic); the only writes are new JSON files under ``artifacts/near_post/score/``.

Candidate replay (spec US3, plan D6): a candidate is scored on the SAME quote only when the
incumbent can be reproduced from the stored inputs — the morning feature rows (parquet saved by
``near-post-capture``, or rebuilt when absent) with the stored per-horse quote substituted, run
through the production predictor, must give back the stored ``p_ens`` bit for bit. A race that
fails that gate is ``not_reproducible`` and leaves the pair (counted, never approximated).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import subprocess
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
from horseracing_db.enums import EntryStatus, ResultStatus
from horseracing_db.models import DecisionQuote, OfficialWinPayout, Race, RaceHorse, RaceResult
from horseracing_eval import decision_quote_scoring as dqs
from horseracing_eval import display_update_rule as dur
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import near_post as np_

LOGIC_VERSION = "near-post-score-v1"
REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC_DIR = REPO_ROOT / "specs" / "175-decision-quote-scoring"
DEFAULT_CANDIDATES_PATH = SPEC_DIR / "candidates.json"
RULE_JSON = REPO_ROOT / "specs" / "174-near-post-decision-quotes" / "display-update-rule.json"
RULE_SHA = RULE_JSON.with_name(RULE_JSON.name + ".sha256")
DEFAULT_OUT_DIR = np_.DEFAULT_FEATURES_DIR / "score"
EVAL_MODULE_PATH = Path(dqs.__file__).resolve()
CANDIDATE_KINDS = ("postprocess", "ensemble_dir")


class ScoreRefused(ValueError):
    """A fail-closed check failed before anything was read or written (exit 2)."""


# --------------------------------------------------------------------------- frozen rule


def assert_rule_frozen(rule_json: Path = RULE_JSON, sha_path: Path = RULE_SHA) -> str:
    """The frozen rule file must hash to its recorded sha AND equal the code's payload."""
    text = rule_json.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    recorded = sha_path.read_text(encoding="utf-8").split()[0]
    if digest != recorded:
        raise ScoreRefused(f"display-update-rule.json sha {digest[:12]} != recorded {recorded[:12]}")
    if json.loads(text) != dur.frozen_payload():
        raise ScoreRefused("display-update-rule.json does not match display_update_rule.frozen_payload()")
    return digest


# --------------------------------------------------------------------------- loading


def _f(x) -> float | None:
    return None if x is None else float(x)


def load_quote_records(
    session: Session, *, date_from: datetime.date, date_to: datetime.date
) -> list[dqs.QuoteRecord]:
    """Every decision_quotes row of races held in [date_from, date_to], joined with the current
    started field, results, official win payouts and (when final) the closing win odds."""
    if date_from > date_to:
        raise ScoreRefused("--from must not be after --to")
    rows = session.execute(
        select(DecisionQuote, Race).join(Race, Race.race_id == DecisionQuote.race_id)
        .where(Race.race_date >= date_from, Race.race_date <= date_to)
        .order_by(Race.race_date, DecisionQuote.race_id)
    ).all()
    race_ids = [q.race_id for q, _r in rows]
    if not race_ids:
        return []
    entries = session.execute(
        select(RaceHorse.race_id, RaceHorse.horse_id, RaceHorse.horse_number, RaceHorse.odds,
               RaceHorse.entry_status).where(RaceHorse.race_id.in_(race_ids))
    ).all()
    results = session.execute(
        select(RaceResult.race_id, RaceResult.horse_id, RaceResult.finish_order,
               RaceResult.result_status).where(RaceResult.race_id.in_(race_ids))
    ).all()
    payouts = session.execute(
        select(OfficialWinPayout.race_id, OfficialWinPayout.horse_number,
               OfficialWinPayout.payout_yen).where(OfficialWinPayout.race_id.in_(race_ids))
    ).all()
    started: dict[str, dict[str, int | None]] = defaultdict(dict)
    odds_now: dict[str, dict[int, float]] = defaultdict(dict)
    for rid, hid, hn, odds, status in entries:
        if status == EntryStatus.STARTED:
            started[rid][hid] = int(hn) if hn is not None else None
            if hn is not None and odds is not None:
                odds_now[rid][int(hn)] = float(odds)
    has_results: set[str] = set()
    winners: dict[str, list[str]] = defaultdict(list)
    for rid, hid, order, status in results:
        has_results.add(rid)
        if order == 1 and status == ResultStatus.FINISHED:
            winners[rid].append(hid)
    pays: dict[str, dict[int, int]] = defaultdict(dict)
    for rid, hn, yen in payouts:
        pays[rid][int(hn)] = int(yen)
    out = []
    for q, race in rows:
        pred_by = {int(p["horse_number"]): p for p in q.pred}
        horses = []
        for f in sorted(q.field, key=lambda d: int(d["horse_number"])):
            hn = int(f["horse_number"])
            p = pred_by.get(hn, {})
            horses.append(dqs.QuoteHorse(
                horse_id=str(f["horse_id"]), horse_number=hn, win_odds=_f(f.get("win_odds")),
                popularity=(int(f["popularity"]) if f.get("popularity") is not None else None),
                p_ens=_f(p.get("p_ens")), ev_ens=_f(p.get("ev_ens")),
                p_single=_f(p.get("p_single")), ev_single=_f(p.get("ev_single")),
                p_mix=_f(p.get("p_mix")), ev_mix=_f(p.get("ev_mix")),
            ))
        final = (race.win_odds_observed_at is not None and race.win_odds_official_at is None
                 and q.race_id in has_results)
        out.append(dqs.QuoteRecord(
            race_id=q.race_id, race_date=race.race_date, post_time=q.post_time,
            track_type=race.track_type, captured_at=q.captured_at, official_at=q.official_at,
            seconds_to_post=int(q.seconds_to_post), capture_target=q.capture_target,
            horses=horses, mix_status=q.mix_status, field_digest=q.field_digest,
            started=dict(started.get(q.race_id, {})), has_results=q.race_id in has_results,
            winner_horse_ids=frozenset(winners.get(q.race_id, ())),
            payouts=dict(pays.get(q.race_id, {})),
            closing=(dict(odds_now.get(q.race_id, {})) if final else None),
            meta={
                "features_sha256": q.features_sha256, "ensemble_manifest": q.ensemble_manifest,
                "ensemble_manifest_sha256": q.ensemble_manifest_sha256,
                "single_booster_sha256": q.single_booster_sha256, "logic_version": q.logic_version,
                "policy_version": q.policy_version, "run_id": str(q.run_id),
                "mix_coefficient_year": q.mix_coefficient_year,
            },
        ))
    return out


# --------------------------------------------------------------------------- candidates


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class Candidate:
    name: str
    kind: str
    artifact_path: Path | None
    artifact_sha256: str
    threshold: float
    registered_on: datetime.date
    train_through: datetime.date | None
    note: str
    artifact_version: str


def load_candidates(path: Path = DEFAULT_CANDIDATES_PATH, *, names: Sequence[str] | None = None,
                    year: int | None = None) -> tuple[list[Candidate], str]:
    """The registered candidates (optionally only ``names``), each with its artifact sha verified:
    ``postprocess`` → sha256 of the eval scoring module file; ``ensemble_dir`` → the sha of the
    year's manifest as the production loader computes it. Returns (candidates, file sha256)."""
    p = Path(path)
    if not p.is_file():
        raise ScoreRefused(f"candidates file missing: {p}")
    raw = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ScoreRefused("candidates.json must be a list")
    by_name = {}
    for item in raw:
        name = str(item.get("name", ""))
        if not name or name in by_name:
            raise ScoreRefused(f"candidate name missing or duplicated: {name!r}")
        kind = item.get("kind")
        if kind not in CANDIDATE_KINDS:
            raise ScoreRefused(f"candidate {name}: kind must be one of {CANDIDATE_KINDS}")
        by_name[name] = item
    wanted = list(names) if names else []
    for n in wanted:
        if n not in by_name:
            raise ScoreRefused(f"candidate {n!r} is not registered in {p.name}")
    chosen = []
    for name in wanted:
        item = by_name[name]
        kind = item["kind"]
        art = item.get("artifact_path")
        sha = str(item.get("artifact_sha256", ""))
        version = str(item.get("artifact_version", "mev-ens15-v1"))
        if kind == "postprocess":
            actual = file_sha256(EVAL_MODULE_PATH)
            if art is not None:
                raise ScoreRefused(f"candidate {name}: postprocess takes no artifact_path")
        else:
            if not art or not Path(art).is_absolute():
                raise ScoreRefused(f"candidate {name}: ensemble_dir needs an absolute artifact_path")
            if year is None:
                raise ScoreRefused("year is required to verify an ensemble_dir candidate")
            from horseracing_training import market_ev

            ens = market_ev.EnsembleMarketEvModel.load(Path(art), version)
            _members, actual = ens.load_members(ens.booster_manifest_for_year(year))
        if actual != sha:
            raise ScoreRefused(f"candidate {name}: artifact sha {actual[:12]} != registered {sha[:12]}")
        try:
            reg = datetime.date.fromisoformat(str(item["registered_on"]))
        except (KeyError, ValueError) as exc:
            raise ScoreRefused(f"candidate {name}: registered_on must be an ISO date") from exc
        tt = item.get("train_through")
        chosen.append(Candidate(
            name=name, kind=kind, artifact_path=Path(art) if art else None, artifact_sha256=sha,
            threshold=float(item.get("mark_rule", {}).get("threshold", dqs.EXPECTED_RETURN_THRESHOLD)),
            registered_on=reg, train_through=datetime.date.fromisoformat(tt) if tt else None,
            note=str(item.get("note", "")), artifact_version=version,
        ))
    return chosen, file_sha256(p)


class CandidateEnsemble:
    """An ``ensemble_dir`` candidate: another 15-seed bundle on the same 77-input design."""

    def __init__(self, cand: Candidate, *, year: int):
        from horseracing_training import market_ev

        self.ens = market_ev.EnsembleMarketEvModel.load(cand.artifact_path, cand.artifact_version)
        self.members, self.sha = self.ens.load_members(self.ens.booster_manifest_for_year(year))

    def ev(self, rows: pd.DataFrame) -> np.ndarray:
        X = self.ens.design_matrix(rows)
        p = np.mean(np.vstack([b.predict(X, num_threads=1) for b in self.members]), axis=0)
        return p * rows["odds"].to_numpy(dtype=float)


# --------------------------------------------------------------------------- replay


def _quote_from_record(rec: dqs.QuoteRecord):
    rows = tuple(SimpleNamespace(horse_number=h.horse_number, odds=h.win_odds, popularity=h.popularity)
                 for h in rec.horses)
    return SimpleNamespace(rows=rows)


@dataclass
class Replay:
    rows: pd.DataFrame | None
    reproducible: bool
    max_abs_diff: float
    p_ens: np.ndarray | None
    features_source: str
    features_sha_match: bool | None


def replay_race(
    rec: dqs.QuoteRecord, day_features: np_.DayFeatures | None, predictor: np_.NearPostPredictor,
    *, features_source: str,
) -> Replay:
    """Reproduce the incumbent on the stored quote; bit equality of every p_ens is the gate."""
    if day_features is None:
        return Replay(None, False, float("nan"), None, "unavailable", None)
    sha_match = day_features.sha256 == rec.meta.get("features_sha256")
    rows = day_features.race_rows(rec.race_id)
    stored = {h.horse_id: h for h in rec.horses}
    if len(rows) == 0 or set(rows["horse_id"].astype(str)) != set(stored):
        return Replay(None, False, float("nan"), None, features_source, sha_match)
    try:
        rows = np_.rows_with_quote(rows, _quote_from_record(rec))
        bundle = predictor.predict(rows, race_date=rec.race_date)
    except ValueError:
        return Replay(None, False, float("nan"), None, features_source, sha_match)
    got = np.array([float(p["p_ens"]) for p in bundle.pred])
    want = np.array([float(stored[str(h)].p_ens) for h in rows["horse_id"].astype(str)])
    diff = float(np.max(np.abs(got - want))) if got.size else 0.0
    exact = bool(got.size == want.size and np.array_equal(got, want))
    return Replay(rows, exact, diff, got if exact else None, features_source, sha_match)


def candidate_races(
    records: Sequence[dqs.QuoteRecord],
    cands: Sequence[Candidate],
    *,
    predictor_for: Callable[[int], np_.NearPostPredictor],
    features_dir: Path,
    rebuild: Callable[[datetime.date], np_.DayFeatures] | None,
    ensemble_for: Callable[[str, int], CandidateEnsemble] | None = None,
) -> tuple[dict[str, dict[str, dqs.CandidateRace]], dict]:
    """Replay every eligible record once (per day features), then price each candidate on it.
    Boosters are per year, so ``predictor_for(year)`` / ``ensemble_for(name, year)`` are resolved
    per race day (cached by the caller)."""
    out: dict[str, dict[str, dqs.CandidateRace]] = {c.name: {} for c in cands}
    stats = {"n_replayed": 0, "n_reproducible": 0, "days": {}}
    by_day: dict[datetime.date, list[dqs.QuoteRecord]] = defaultdict(list)
    for r in records:
        if dqs.classify(r) == "eligible":
            by_day[r.race_date].append(r)
    for day in sorted(by_day):
        shas = {r.meta.get("features_sha256") for r in by_day[day]}
        feats: dict[str, tuple[np_.DayFeatures | None, str]] = {}
        for sha in shas:
            df = np_.load_day_features(day, str(sha), features_dir) if sha else None
            if df is not None:
                feats[sha] = (df, "parquet")
            elif rebuild is not None:
                feats[sha] = (rebuild(day), "rebuilt")
            else:
                feats[sha] = (None, "unavailable")
        stats["days"][day.isoformat()] = {s[:12]: src for s, (_d, src) in feats.items()}
        predictor = predictor_for(day.year)
        for rec in by_day[day]:
            df, source = feats[rec.meta.get("features_sha256")]
            rp = replay_race(rec, df, predictor, features_source=source)
            stats["n_replayed"] += 1
            stats["n_reproducible"] += int(rp.reproducible)
            for c in cands:
                if not rp.reproducible:
                    out[c.name][rec.race_id] = dqs.CandidateRace(
                        rec.race_id, False, rp.max_abs_diff, rp.features_source, rp.features_sha_match,
                        None, None)
                    continue
                numbers = rp.rows["horse_number"].astype(int).tolist()
                odds = rp.rows["odds"].to_numpy(dtype=float)
                if c.kind == "postprocess":
                    ev = dqs.normalize_148(rp.p_ens.tolist(), odds.tolist())
                else:
                    if ensemble_for is None:
                        raise ScoreRefused(f"candidate {c.name}: no ensemble loader")
                    ev = ensemble_for(c.name, day.year).ev(rp.rows).tolist()
                out[c.name][rec.race_id] = dqs.CandidateRace(
                    rec.race_id, True, 0.0, rp.features_source, rp.features_sha_match,
                    dict(zip(numbers, [float(x) for x in ev], strict=True)),
                    {h.horse_number: float(h.ev_ens) for h in rec.horses},
                )
    return out, stats


# --------------------------------------------------------------------------- report


def git_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True,
                              text=True, check=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        return None


def code_sha256() -> str:
    h = hashlib.sha256()
    for p in (EVAL_MODULE_PATH, Path(__file__).resolve(), Path(np_.__file__).resolve()):
        h.update(p.read_bytes())
    return h.hexdigest()


def default_out_path(date_from: datetime.date, date_to: datetime.date, run_id: str) -> Path:
    return DEFAULT_OUT_DIR / f"{date_from.isoformat()}_{date_to.isoformat()}_{run_id}.json"


def assemble(
    report: dict, *, date_from: datetime.date, date_to: datetime.date, n_rows: int,
    rule_sha: str, candidates_sha: str | None, run_id: str, computed_at: datetime.datetime,
    replay_stats: dict | None,
) -> dict:
    report["rule"]["json_sha256"] = rule_sha
    report["inputs"] = {
        "from": date_from.isoformat(), "to": date_to.isoformat(), "n_rows": n_rows,
        "candidates_json_sha256": candidates_sha, "code_sha256": code_sha256(), "git_sha": git_sha(),
        "quote_source": "decision_quotes", "replay": replay_stats,
    }
    report["run"] = {"run_id": run_id, "computed_at": computed_at.isoformat(),
                     "logic_version": LOGIC_VERSION}
    return report


def summary_lines(report: dict) -> list[str]:
    lines = [report["power_note"]["text"]]
    f = report["funnel"]
    lines.append("funnel: " + " ".join(f"{k}={v}" for k, v in f.items()))
    for c in report["columns"]:
        rr = c["recovery_rate"]
        lines.append(
            f"{c['column']}: n_bets={c['n_bets']} n_hits={c['n_hits']} "
            f"recovery_rate={'—' if rr is None else f'{rr:.4f}'} ci={c['ci']} "
            f"remaining_to_min={c['remaining_to_min']}"
        )
    for p in report["paired"]:
        lines.append(
            f"candidate {p['name']}: reproducible={p['n_races_reproducible']} "
            f"not_reproducible={p['n_races_not_reproducible']} n_points={p['n_points']} "
            f"diff={p['diff']} diff_ci={p['diff_ci']} verdict={p['verdict']}"
        )
    lines.append(f"verdict_overall={report['verdict_overall']}")
    d = report["diagnostics"]
    lines.append(
        f"diagnostic: closing_unavailable={d['price_drift']['n_races_closing_unavailable']} "
        f"drift_all={d['price_drift']['all']} retention={d['retention']['rate']}"
    )
    return lines
