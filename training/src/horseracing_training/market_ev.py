"""Market-aware expected return (期待回収率) — product port of the 2026-09 ROI exploration's Arm C.

Research origin: ``scripts/roi_explore/build_dataset.py`` (feature builder) and
``scripts/roi_explore/direct_return_model.py`` (walk-forward binary LightGBM with CURRENT win odds
as an input). Report: ``docs/roi-pattern-exploration-20260923/report.md``. Spec: Feature 137
(``specs/137-market-ev-display/plan.md`` section 0.3).

What this module does (batch, offline, never on the API request path):
  1. load every flat started row from 2007 (constitution I; history is needed for as-of features),
  2. rebuild the research features exactly (parity-tested against the research builder),
  3. apply the saved yearly booster for the target races' year (trained strictly on earlier years),
  4. return per-horse ``win_prob`` (market-aware), ``odds_used`` and
     ``expected_return = win_prob × odds``,
  5. (``compute_and_persist``) replace the target races' rows of ``market_ev_predictions``.

Feature 138 (``specs/138-attention-conditions`` plan 0.4) adds an optional second version computed
from the SAME features in the same run: the 15-seed average ``mev-ens15-v1``
(``EnsembleMarketEvModel`` / ``predict_ensemble``). Both versions share one run_id / computed_at /
transaction, and that run also records the attention-condition picks (``attention_picks``) and,
after the commit, the checkpoint decisions (``attention_checkpoints``). Without an ensemble
directory nothing of that runs and the single-version path is unchanged.

This model deliberately uses the current win odds as an input. It is NOT a win-probability model of
the main prediction pipeline (constitution II keeps odds out of those), and its output never
re-enters any feature (leak guard: ``features/tests/unit/test_market_ev_leak_guard.py``). The
display threshold (120%) lives in the API only; nothing here decides what is highlighted.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import pathlib
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal
from functools import partial

import numpy as np
import pandas as pd
from horseracing_db.models import MarketEvPrediction, Race, RaceResult
from sqlalchemy import delete, insert, select, text
from sqlalchemy.orm import Session

ROWS_SQL = """
select rh.race_id, rh.horse_id, rh.horse_number, rh.frame, rh.sex, rh.age, rh.weight,
       rh.weight_diff, rh.jockey_weight, rh.odds, rh.popularity, rh.running_style, rh.jockey_id,
       rh.trainer_id, rh.updated_at as horse_row_updated_at,
       r.race_date, r.venue_code, r.race_number, r.distance, r.track_type, r.going, r.weather,
       r.race_class, r.grade, r.prize_money,
       rs.finish_order, rs.result_status, extract(epoch from rs.finish_time_diff) as margin_sec,
       rs.last_3f,
       h.sire_line, h.damsire_line
from race_horses rh
join races r using(race_id)
left join race_results rs on rs.race_id = rh.race_id and rs.horse_id = rh.horse_id
left join horses h on h.horse_id = rh.horse_id
where rh.entry_status = 'started' and r.race_date >= :since and r.race_date <= :through
  and r.track_type <> '障'
order by rh.race_id, rh.horse_number
"""

#: Recorded on every persisted row (constitution V): which feature builder / exclusions / data
#: window produced the value. Bump when any of them changes.
LOGIC_VERSION = "mev-v1;features=roi-explore-2026-09;drop=sameday,weightlive;data>=2007"

#: Feature 138: the logic version of the 15-seed average rows. ``EnsembleMarketEvModel.load``
#: refuses an ensemble spec that does not match it (seeds 1-15, one thread, deterministic).
ENSEMBLE_LOGIC_VERSION = (
    "mev-ens15-v1;seeds=1-15;threads=1;deterministic;features=roi-explore-2026-09;"
    "drop=sameday,weightlive;data>=2007"
)
ENSEMBLE_SEEDS = tuple(range(1, 16))
#: what ``drop=sameday,weightlive;data>=2007`` in ENSEMBLE_LOGIC_VERSION states about the training
ENSEMBLE_DROP_GROUPS = ("sameday", "weightlive")
ENSEMBLE_TRAIN_FROM = 2007
ENSEMBLE_SPEC_NAME = "ensemble.spec.json"

#: Constitution I: JRA-VAN data before 2007 uses a different ID system and must not be used.
DATA_START = "2007-01-01"

#: Artifacts under a Claude worktree vanish with the worktree (a model row once outlived its
#: calibrator that way and stopped every prediction). The model directory must be durable.
_WORKTREE_MARKER = "/.claude/worktrees/"

_BOOSTER_NAME = re.compile(r"^model_(\d{4})\.txt$")
_MANIFEST_NAME = re.compile(r"^ensemble_(\d{4})\.json$")

_CLASS_RANK = {"debut": 0, "maiden": 1, "C1": 2, "C2": 3, "C3": 4, "OP": 5}
_CLASS_CANON = {
    "新馬": "debut", "未出走": "debut", "未勝利": "maiden",
    "1勝": "C1", "１勝": "C1", "300万": "C1", "400万": "C1", "500万": "C1", "５００万": "C1",
    "2勝": "C2", "２勝": "C2", "600万": "C2", "700万": "C2", "800万": "C2", "900万": "C2",
    "1000万": "C2", "１０００万": "C2",
    "3勝": "C3", "３勝": "C3", "1400万": "C3", "1500万": "C3", "1600万": "C3", "１６００万": "C3",
    "ｵｰﾌﾟﾝ": "OP", "オープン": "OP", "OP": "OP", "OP(L)": "OP", "L": "OP", "重賞": "OP",
    "Ｇ３": "OP", "Ｇ２": "OP", "Ｇ１": "OP", "G3": "OP", "G2": "OP", "G1": "OP",
}
_GRADE_CANON = {"A": "G1", "B": "G2", "C": "G3", "L": "L", "G1": "G1", "G2": "G2", "G3": "G3"}

_PREDICTION_COLUMNS = (
    "race_id", "horse_id", "horse_number", "odds_used", "odds_observed_at",
    "win_prob", "expected_return", "booster", "booster_sha256",
)


def canon_class(raw) -> str | None:
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return None
    s = str(raw).strip()
    if s in _CLASS_CANON:
        return _CLASS_CANON[s]
    for k, v in _CLASS_CANON.items():
        if s.startswith(k):
            return v
    return None


def _group_sum(values: pd.Series, keys: pd.Series) -> pd.Series:
    return values.groupby(keys).transform("sum")


def _rolling_window_counts(df: pd.DataFrame, key: str, window_days: int = 365) -> pd.DataFrame:
    """Per (key, race_date): starts / wins / Σq strictly before the date (all-time and window)."""
    daily = (
        df.groupby([key, "race_date"], sort=True)
        .agg(n=("won", "size"), w=("won", "sum"), eq=("q", "sum"))
        .reset_index()
    )
    daily["race_date"] = pd.to_datetime(daily["race_date"])
    m = len(daily)
    out = {k: np.zeros(m) for k in ("all_n", "all_w", "all_q", "win_n", "win_w", "win_q")}
    keys = daily[key].to_numpy()
    dates = daily["race_date"].to_numpy().astype("datetime64[D]").astype(np.int64)
    n = daily["n"].to_numpy(dtype=float)
    w = daily["w"].to_numpy(dtype=float)
    eq = daily["eq"].fillna(0.0).to_numpy(dtype=float)
    change = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1], True])
    for a, b in zip(change[:-1], change[1:], strict=True):
        d = dates[a:b]
        cn = np.r_[0.0, np.cumsum(n[a:b])]
        cw = np.r_[0.0, np.cumsum(w[a:b])]
        cq = np.r_[0.0, np.cumsum(eq[a:b])]
        idx = np.arange(b - a)
        out["all_n"][a:b] = cn[idx]
        out["all_w"][a:b] = cw[idx]
        out["all_q"][a:b] = cq[idx]
        lo = np.searchsorted(d, d - window_days, side="left")
        out["win_n"][a:b] = cn[idx] - cn[lo]
        out["win_w"][a:b] = cw[idx] - cw[lo]
        out["win_q"][a:b] = cq[idx] - cq[lo]
    for k, v in out.items():
        daily[k] = v
    return daily.drop(columns=["n", "w", "eq"])


def load_rows(conn, *, through: str, since: str = DATA_START) -> pd.DataFrame:
    return pd.read_sql(text(ROWS_SQL), conn, params={"through": through, "since": since})


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Rebuild the research features (build_dataset.py) for every row. Outcome columns of a row are
    never used for that row's own features; they enter only through strictly-earlier races/dates."""
    df = df.copy()
    df["race_date"] = pd.to_datetime(df["race_date"])
    for col in ("odds", "jockey_weight", "last_3f", "margin_sec"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    for col in ("frame", "age", "weight", "weight_diff", "popularity", "distance", "race_number",
                "prize_money", "finish_order", "horse_number"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    df["race_id"] = df["race_id"].astype(str)
    df["horse_id"] = df["horse_id"].astype(str)
    df["finished"] = df["result_status"] == "finished"
    df["won"] = df["finished"] & (df["finish_order"] == 1.0)
    df["finish_order"] = np.where(df["finished"], df["finish_order"], np.nan)
    df["year"] = df["race_date"].dt.year.astype(int)
    df["month"] = df["race_date"].dt.month.astype(int)
    df["dow"] = df["race_date"].dt.dayofweek.astype(int)
    df["dist_band"] = pd.cut(df["distance"], [0, 1400, 1800, 2200, 10000], right=False,
                             labels=["sprint", "mile", "mid", "long"]).astype(object)
    df["race_class_canon"] = [canon_class(x) for x in df["race_class"]]
    df["class_rank"] = df["race_class_canon"].map(_CLASS_RANK).astype(float)
    df["is_graded"] = (
        df["grade"].isin(["A", "B", "C", "G1", "G2", "G3"])
        | df["race_class"].isin(["Ｇ１", "Ｇ２", "Ｇ３"])
    )
    df["grade"] = df["grade"].map(_GRADE_CANON).astype(object)

    # ---- race-level market structure (current odds)
    g = df.groupby("race_id", sort=False)
    df["field_size"] = g["horse_id"].transform("size").astype(float)
    df["race_ok"] = g["odds"].transform(lambda s: bool(s.notna().all() and (s > 0).all()))
    inv = 1.0 / df["odds"]
    df["q"] = inv / _group_sum(inv, df["race_id"])
    order = df.sort_values(["race_id", "odds", "horse_number"]).index
    rank = np.empty(len(df), dtype=float)
    rank[order] = df.loc[order].groupby("race_id", sort=False).cumcount().to_numpy() + 1
    df["odds_rank"] = rank
    fav = df[df["odds_rank"] == 1].set_index("race_id")["odds"]
    sec = df[df["odds_rank"] == 2].set_index("race_id")["odds"]
    df["fav_odds"] = df["race_id"].map(fav).astype(float)
    df["second_odds"] = df["race_id"].map(sec).astype(float)
    df["odds_gap12"] = df["second_odds"] - df["fav_odds"]
    df["fav_q"] = g["q"].transform("max")
    df["q_share_of_fav"] = df["q"] / df["fav_q"]
    ent = _group_sum(-(df["q"] * np.log(df["q"].clip(lower=1e-12))), df["race_id"])
    df["q_entropy_norm"] = ent / np.log(df["field_size"].clip(lower=2))
    df["n_fav_under_2"] = _group_sum((df["odds"] < 2.0).astype(float), df["race_id"])
    df["n_odds_under_10"] = _group_sum((df["odds"] < 10.0).astype(float), df["race_id"])
    df["is_fav"] = df["odds_rank"] == 1
    rn = df.groupby(["race_date", "venue_code"])["race_number"]
    df["is_last_race"] = df["race_number"] == rn.transform("max")
    df["is_first_race"] = df["race_number"] == rn.transform("min")
    l3 = df["last_3f"].where(df["finished"])
    df["last3f_rank"] = l3.groupby(df["race_id"]).rank(method="min", ascending=True)
    df.loc[df["won"], "margin_sec"] = 0.0

    # ---- horse history (one start per day → shift is strictly-before)
    df = df.sort_values(["horse_id", "race_date", "race_id"]).reset_index(drop=True)
    gh = df.groupby("horse_id", sort=False)
    df["career_starts"] = gh.cumcount().astype(float)
    df["career_wins"] = gh["won"].cumsum().astype(float) - df["won"].astype(float)
    top3 = (df["finish_order"] <= 3).astype(float)
    df["career_top3"] = top3.groupby(df["horse_id"]).cumsum() - top3
    has_starts = df["career_starts"] > 0
    df["career_win_rate"] = np.where(
        has_starts, df["career_wins"] / df["career_starts"], np.nan
    )
    df["career_top3_rate"] = np.where(
        has_starts, df["career_top3"] / df["career_starts"], np.nan
    )
    df["is_debut"] = df["career_starts"] == 0

    def lag(col, k=1):
        return gh[col].shift(k)

    df["prev_finish"] = lag("finish_order")
    df["prev2_finish"] = lag("finish_order", 2)
    df["prev3_finish"] = lag("finish_order", 3)
    f = [lag("finish_order", k) for k in range(1, 6)]
    df["avg_last3_finish"] = pd.concat(f[:3], axis=1).mean(axis=1)
    df["best_finish_last5"] = pd.concat(f, axis=1).min(axis=1)
    w = [lag("won", k).astype(float) for k in range(1, 6)]
    df["wins_last5"] = pd.concat(w, axis=1).sum(axis=1, min_count=1)
    t3 = top3.groupby(df["horse_id"])
    df["top3_last5"] = (
        pd.concat([t3.shift(k) for k in range(1, 6)], axis=1).sum(axis=1, min_count=1)
    )
    df["prev_popularity"] = lag("popularity")
    df["prev_odds"] = lag("odds")
    df["prev_q"] = lag("q")
    df["prev_field_size"] = lag("field_size")
    df["prev_finish_pct"] = df["prev_finish"] / df["prev_field_size"]
    df["prev_beat_market"] = df["prev_popularity"] - df["prev_finish"]
    df["prev_distance"] = lag("distance")
    df["prev_track_type"] = lag("track_type")
    df["prev_venue_code"] = lag("venue_code")
    df["prev_class_canon"] = lag("race_class_canon")
    df["dist_change"] = df["distance"] - df["prev_distance"]
    df["class_change"] = np.sign(df["class_rank"] - lag("class_rank"))
    prev_date = lag("race_date")
    df["days_since_last"] = (df["race_date"] - prev_date).dt.days.astype(float)
    prev2_date = lag("race_date", 2)
    df["tataki_2"] = np.where(
        prev2_date.isna(), np.nan, ((prev_date - prev2_date).dt.days > 70).astype(float)
    )
    df["prev_weight"] = lag("weight")
    df["weight_change_vs_prev"] = df["weight"] - df["prev_weight"]
    df["prev_running_style"] = lag("running_style")
    df["prev_last3f_rank"] = lag("last3f_rank")
    df["prev_margin_sec"] = lag("margin_sec")
    df["last_won"] = lag("won").astype(float)
    prev_jockey = lag("jockey_id")
    df["jockey_change"] = np.where(
        prev_jockey.isna(), np.nan, (df["jockey_id"] != prev_jockey).astype(float)
    )

    # ---- jockey / trainer / combo as-of (strictly before the race date)
    df["combo_id"] = df["jockey_id"].astype(str) + "|" + df["trainer_id"].astype(str)
    for key, pref in (("jockey_id", "jockey"), ("trainer_id", "trainer"), ("combo_id", "combo")):
        daily = _rolling_window_counts(df[[key, "race_date", "won", "q"]], key)
        daily = daily.rename(columns={"all_n": f"{pref}_starts_all", "all_w": f"{pref}_wins_all",
                                      "win_n": f"{pref}_starts_365", "win_w": f"{pref}_wins_365",
                                      "all_q": f"{pref}_q_all", "win_q": f"{pref}_q_365"})
        df = df.merge(daily, on=[key, "race_date"], how="left")
        if pref in ("jockey", "trainer"):
            df[f"{pref}_excess_365"] = df[f"{pref}_wins_365"] - df[f"{pref}_q_365"]
            df[f"{pref}_excess_all"] = df[f"{pref}_wins_all"] - df[f"{pref}_q_all"]
        starts_all = df[f"{pref}_starts_all"]
        starts_365 = df[f"{pref}_starts_365"]
        df[f"{pref}_win_rate_all"] = np.where(
            starts_all > 0, df[f"{pref}_wins_all"] / starts_all, np.nan
        )
        df[f"{pref}_win_rate_365"] = np.where(
            starts_365 > 0, df[f"{pref}_wins_365"] / starts_365, np.nan
        )
    df = df.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
    return df


def _encode_category(value, mapping: dict) -> float:
    """Research cat code for ``value``; missing or unseen values are NaN (LightGBM's missing)."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    key = str(value)
    return float(mapping[key]) if key in mapping else np.nan


def _read_booster(path: pathlib.Path):
    """Load a booster from the exact bytes whose sha256 is recorded (no read-twice gap)."""
    import lightgbm as lgb

    data = path.read_bytes()
    return lgb.Booster(model_str=data.decode("utf-8")), hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class MarketEvModel:
    model_dir: pathlib.Path
    spec: dict
    version: str

    @staticmethod
    def load(model_dir: str | pathlib.Path, version: str) -> MarketEvModel:
        d = pathlib.Path(model_dir)
        spec = json.loads((d / "model.spec.json").read_text())
        if spec.get("objective") != "binary":
            raise ValueError(
                f"market-ev expects a binary model spec, got {spec.get('objective')!r}"
            )
        return MarketEvModel(d, spec, version)

    def booster_path_for_year(self, year: int) -> pathlib.Path:
        """Walk-forward rule: races in year y use the booster trained strictly on years < y.
        If the exact year is missing (e.g. a new year before retraining), fall back to the latest
        earlier one. The file actually used is recorded on every persisted row."""
        exact = self.model_dir / f"model_{year}.txt"
        if exact.exists():
            return exact
        cands = sorted(
            int(m.group(1))
            for p in self.model_dir.glob("model_*.txt")
            if (m := _BOOSTER_NAME.match(p.name))
        )
        earlier = [y for y in cands if y < year]
        if not earlier:
            raise FileNotFoundError(f"no market-ev booster usable for {year} in {self.model_dir}")
        return self.model_dir / f"model_{earlier[-1]}.txt"

    def design_matrix(self, feats: pd.DataFrame) -> np.ndarray:
        return _design_matrix(self.spec, feats)


def _design_matrix(spec: dict, feats: pd.DataFrame) -> np.ndarray:
    X = feats[spec["features"]].astype(float).copy()
    for c in spec["cats"]:
        encode = partial(_encode_category, mapping=spec["cat_maps"][c])
        X[c] = feats[c].astype(object).map(encode).astype(float)
    return X.to_numpy(dtype=np.float32)


class EnsembleIntegrityError(ValueError):
    """The ensemble artifact does not match its manifest or the pinned logic version."""


@dataclass(frozen=True)
class EnsembleMarketEvModel:
    """Feature 138: the 15-seed average (``mev-ens15-v1``) — same features, same filters.

    Layout (``scripts/roi_explore/assemble_ens15_20261001.py``): ``ensemble.spec.json`` (shared
    features / cats / cat_maps, seeds, threads, deterministic) and one ``ensemble_YYYY.json`` per
    booster year listing every member's relative path and the sha256 of its exact bytes."""

    ensemble_dir: pathlib.Path
    spec: dict
    version: str

    @staticmethod
    def load(ensemble_dir: str | pathlib.Path, version: str) -> EnsembleMarketEvModel:
        d = pathlib.Path(ensemble_dir)
        spec = json.loads((d / ENSEMBLE_SPEC_NAME).read_text())
        if spec.get("objective") != "binary":
            raise EnsembleIntegrityError(
                f"market-ev ensemble expects a binary spec, got {spec.get('objective')!r}"
            )
        missing = [k for k in ("features", "cats", "cat_maps") if k not in spec]
        if missing:
            raise EnsembleIntegrityError(f"{ENSEMBLE_SPEC_NAME} lacks {missing}")
        if spec.get("version") != version:
            raise EnsembleIntegrityError(
                f"{ENSEMBLE_SPEC_NAME} is version {spec.get('version')!r}, expected {version!r}"
            )
        # the rows carry ENSEMBLE_LOGIC_VERSION, so the artifact must be what it claims
        if (
            list(spec.get("seeds") or []) != list(ENSEMBLE_SEEDS)
            or spec.get("num_threads") != 1
            or spec.get("deterministic") is not True
            or sorted(spec.get("drop_groups") or []) != list(ENSEMBLE_DROP_GROUPS)
            or spec.get("train_from") != ENSEMBLE_TRAIN_FROM
        ):
            raise EnsembleIntegrityError(
                f"{ENSEMBLE_SPEC_NAME} does not match ENSEMBLE_LOGIC_VERSION "
                f"(seeds={spec.get('seeds')}, num_threads={spec.get('num_threads')}, "
                f"deterministic={spec.get('deterministic')}, "
                f"drop_groups={spec.get('drop_groups')}, train_from={spec.get('train_from')})"
            )
        return EnsembleMarketEvModel(d, spec, version)

    def booster_manifest_for_year(self, year: int) -> pathlib.Path:
        """``ensemble_{year}.json``, else the latest earlier year (the walk-forward rule of
        ``MarketEvModel.booster_path_for_year``)."""
        exact = self.ensemble_dir / f"ensemble_{year}.json"
        if exact.exists():
            return exact
        cands = sorted(
            int(m.group(1))
            for p in self.ensemble_dir.glob("ensemble_*.json")
            if (m := _MANIFEST_NAME.match(p.name))
        )
        earlier = [y for y in cands if y < year]
        if not earlier:
            raise FileNotFoundError(
                f"no market-ev ensemble manifest usable for {year} in {self.ensemble_dir}"
            )
        return self.ensemble_dir / f"ensemble_{earlier[-1]}.json"

    def load_members(self, manifest_path: pathlib.Path) -> tuple[list, str]:
        """Every member booster of a manifest (seed order) + the sha256 of the manifest bytes.

        Fail-closed: the manifest must name this version and year, list exactly the spec's
        seeds in order (no partial average), keep member paths inside the ensemble directory,
        and every member's bytes must hash to the recorded sha256."""
        data = manifest_path.read_bytes()
        manifest = json.loads(data.decode("utf-8"))
        m = _MANIFEST_NAME.match(manifest_path.name)
        if manifest.get("version") != self.version or (
            m is None or manifest.get("year") != int(m.group(1))
        ):
            raise EnsembleIntegrityError(
                f"{manifest_path.name}: version/year {manifest.get('version')!r}/"
                f"{manifest.get('year')!r} do not match {self.version!r}"
            )
        members = manifest.get("members") or []
        seeds = [member.get("seed") for member in members]
        if seeds != list(self.spec["seeds"]):
            raise EnsembleIntegrityError(
                f"{manifest_path.name}: member seeds {seeds} != spec seeds {self.spec['seeds']}"
            )
        root = self.ensemble_dir.resolve()
        boosters = []
        for member in members:
            rel = pathlib.PurePosixPath(str(member.get("path", "")))
            path = (self.ensemble_dir / rel).resolve()
            if rel.is_absolute() or ".." in rel.parts or root not in path.parents:
                raise EnsembleIntegrityError(
                    f"{manifest_path.name}: member path escapes the ensemble dir: {rel}"
                )
            bst, sha256 = _read_booster(path)
            if sha256 != member.get("sha256"):
                raise EnsembleIntegrityError(
                    f"{manifest_path.name}: sha256 mismatch for {rel} "
                    f"(manifest {member.get('sha256')}, file {sha256})"
                )
            boosters.append(bst)
        return boosters, hashlib.sha256(data).hexdigest()

    def design_matrix(self, feats: pd.DataFrame) -> np.ndarray:
        return _design_matrix(self.spec, feats)


def predict(model: MarketEvModel, feats: pd.DataFrame) -> pd.DataFrame:
    """Per-horse market-aware win probability and expected return for already-built rows.
    Rows of races whose started horses do not all have odds are skipped (race_ok=False)."""
    rows = feats[feats["race_ok"].astype(bool)].copy()
    out = []
    for year, part in rows.groupby("year"):
        path = model.booster_path_for_year(int(year))
        bst, sha256 = _read_booster(path)
        p = bst.predict(model.design_matrix(part))
        res = part[["race_id", "horse_id", "horse_number", "odds", "horse_row_updated_at"]].copy()
        res["win_prob"] = p
        res["expected_return"] = p * part["odds"].to_numpy(dtype=float)
        res["booster"] = path.name
        res["booster_sha256"] = sha256
        out.append(res)
    if not out:
        return pd.DataFrame(columns=list(_PREDICTION_COLUMNS))
    return pd.concat(out, ignore_index=True).rename(
        columns={"odds": "odds_used", "horse_row_updated_at": "odds_observed_at"}
    )


def predict_ensemble(model: EnsembleMarketEvModel, feats: pd.DataFrame) -> pd.DataFrame:
    """``predict`` for the ensemble: the same ``race_ok`` filter, ``win_prob`` = the arithmetic
    mean of the members' predictions, ``booster`` = the manifest file used and
    ``booster_sha256`` = the sha256 of that manifest's bytes."""
    rows = feats[feats["race_ok"].astype(bool)].copy()
    out = []
    loaded: dict[pathlib.Path, tuple[list, str]] = {}
    for year, part in rows.groupby("year"):
        path = model.booster_manifest_for_year(int(year))
        if path not in loaded:
            loaded[path] = model.load_members(path)
        boosters, sha256 = loaded[path]
        X = model.design_matrix(part)
        p = np.mean(np.vstack([bst.predict(X) for bst in boosters]), axis=0)
        res = part[["race_id", "horse_id", "horse_number", "odds", "horse_row_updated_at"]].copy()
        res["win_prob"] = p
        res["expected_return"] = p * part["odds"].to_numpy(dtype=float)
        res["booster"] = path.name
        res["booster_sha256"] = sha256
        out.append(res)
    if not out:
        return pd.DataFrame(columns=list(_PREDICTION_COLUMNS))
    return pd.concat(out, ignore_index=True).rename(
        columns={"odds": "odds_used", "horse_row_updated_at": "odds_observed_at"}
    )


def drop_invalid_odds_races(pred: pd.DataFrame) -> tuple[pd.DataFrame, set[str]]:
    """NUMERIC CHECK odds_used >= 1.0: a race carrying an impossible price is not computed at all
    (race-atomic), rather than aborting the whole run on the constraint."""
    invalid = set(pred.loc[pred["odds_used"].astype(float) < 1.0, "race_id"].astype(str))
    if invalid:
        pred = pred[~pred["race_id"].astype(str).isin(invalid)]
    return pred, invalid


def validate_model_dir(
    model_dir: str | pathlib.Path, *, flag: str = "--model-dir"
) -> pathlib.Path:
    """The model directory must be an absolute, durable path (never inside a Claude worktree)."""
    path = pathlib.Path(model_dir)
    if not path.is_absolute():
        raise ValueError(f"{flag} must be an absolute path: {model_dir}")
    for candidate in (str(path), str(path.resolve())):
        if _WORKTREE_MARKER in candidate:
            raise ValueError(f"{flag} must not live inside a Claude worktree: {candidate}")
    return path


def _as_date(value: datetime.date | str) -> datetime.date:
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value))


def resolve_race_date(session: Session, race_id: str) -> datetime.date:
    """The date whose whole card ``--race-id`` recomputes."""
    race_date = session.execute(
        select(Race.race_date).where(Race.race_id == race_id)
    ).scalar_one_or_none()
    if race_date is None:
        raise LookupError(f"race not found or has no race_date: {race_id}")
    return race_date


def _races_with_results(session: Session, race_ids: list[str]) -> set[str]:
    if not race_ids:
        return set()
    found = session.execute(
        select(RaceResult.race_id).where(RaceResult.race_id.in_(race_ids)).distinct()
    ).scalars()
    return set(found)


def _exact(value: float) -> Decimal:
    """Shortest round-trip decimal of a float (odds 12.3 stays exactly 12.3 in NUMERIC)."""
    return Decimal(repr(float(value)))


def stored_values(win_prob: float, odds: float) -> tuple[Decimal, Decimal, Decimal]:
    """(win_prob, odds_used, expected_return) exactly as persisted: the expected return is the
    exact product of the two stored values (plan 0.2). The attention picks match the rules on
    this same stored value, so a judged row and the row it came from can never disagree."""
    win = _exact(win_prob)
    odds_used = _exact(odds)
    return win, odds_used, win * odds_used


def _row_values(
    rec: dict,
    *,
    model_version: str,
    pending: bool,
    run_id: uuid.UUID,
    computed_at: datetime.datetime,
    logic_version: str = LOGIC_VERSION,
) -> dict:
    win_prob, odds_used, expected_return = stored_values(rec["win_prob"], rec["odds_used"])
    number = rec["horse_number"]
    observed = pd.Timestamp(rec["odds_observed_at"])
    return {
        "race_id": str(rec["race_id"]),
        "model_version": model_version,
        "horse_id": str(rec["horse_id"]),
        "horse_number": None if pd.isna(number) else int(number),
        "win_prob": win_prob,
        "odds_used": odds_used,
        # stored as the exact product of the two stored values (plan 0.2)
        "expected_return": expected_return,
        "odds_observed_at": observed.to_pydatetime(),
        "result_pending_at_compute": pending,
        "booster": str(rec["booster"]),
        "booster_sha256": str(rec["booster_sha256"]),
        "logic_version": logic_version,
        "run_id": run_id,
        "computed_at": computed_at,
    }


def _replace_rows(
    session: Session,
    pred: pd.DataFrame,
    *,
    model_version: str,
    logic_version: str,
    with_results: set[str],
    run_id: uuid.UUID,
    computed_at: datetime.datetime,
) -> tuple[int, int, int]:
    """Per race: delete this version's rows, insert the new ones. Returns (races, horses, pending
    races). The caller owns the transaction."""
    table = MarketEvPrediction.__table__
    n_races = n_horses = n_pending = 0
    for key, part in pred.groupby("race_id", sort=True):
        race_id = str(key)
        pending = race_id not in with_results
        session.execute(
            delete(MarketEvPrediction).where(
                MarketEvPrediction.race_id == race_id,
                MarketEvPrediction.model_version == model_version,
            )
        )
        values = [
            _row_values(rec, model_version=model_version, pending=pending, run_id=run_id,
                        computed_at=computed_at, logic_version=logic_version)
            for rec in part.to_dict("records")
        ]
        session.execute(insert(table), values)
        n_races += 1
        n_horses += len(values)
        n_pending += int(pending)
    return n_races, n_horses, n_pending


def _lock_version_day(session: Session, version: str, day: str) -> None:
    session.execute(
        text("select pg_advisory_xact_lock(hashtext(:k))"),
        {"k": f"market_ev:{version}:{day}"},
    )


def _boosters(pred: pd.DataFrame) -> dict[str, str]:
    return (
        pred[["booster", "booster_sha256"]].drop_duplicates().sort_values("booster")
        .set_index("booster")["booster_sha256"].to_dict()
    )


def _row_keys(pred: pd.DataFrame) -> set[tuple[str, str]]:
    return set(zip(pred["race_id"].astype(str), pred["horse_id"].astype(str), strict=True))


def _post_times(session: Session, race_ids: list[str]) -> dict[str, datetime.datetime | None]:
    """races.post_time at compute time (ROWS_SQL does not carry it)."""
    if not race_ids:
        return {}
    found = session.execute(
        select(Race.race_id, Race.post_time).where(Race.race_id.in_(race_ids))
    ).all()
    return {str(rid): post for rid, post in found}


def _check_attention_versions(single_version: str, ensemble_version: str) -> None:
    """The attention rules are defined on exactly these two series (S1-S4 on the displayed
    15-seed average, S5 on 137's single seed). Picks recorded from any other pair would be filed
    under the same rule set and corrupt the prospective record, so refuse them up front."""
    from horseracing_eval import attention_rules

    expected = (
        attention_rules.SINGLE_SEED_MODEL_VERSION,
        attention_rules.DISPLAYED_MARKET_EV_MODEL_VERSION,
    )
    if (single_version, ensemble_version) != expected:
        raise ValueError(
            "an ensemble run records attention picks and must pair "
            f"--model-dir {expected[0]} with --ensemble-dir {expected[1]} "
            f"(got {single_version} / {ensemble_version})"
        )


def _one_line_error(exc: BaseException) -> str:
    text_ = " ".join(f"{type(exc).__name__}: {exc}".split())
    return text_[:300]


def _started_fields(raw: pd.DataFrame, in_range: pd.Series) -> dict[str, tuple[str, ...]]:
    """The started field of every race in range at compute time (``field_digest``)."""
    rows = raw.loc[in_range, ["race_id", "horse_id"]].astype(str)
    return rows.groupby("race_id")["horse_id"].agg(tuple).to_dict()


def _void_pass_only(
    session: Session,
    *,
    d_from: datetime.date,
    d_to: datetime.date,
    started: dict[str, tuple[str, ...]],
    with_results: set[str],
    ensemble_version: str,
    single_version: str,
) -> dict:
    """Feature 138 (plan 0.4 step 1・D26): the void pass of an ensemble run that computes no row.

    ``--pending-only`` on a date whose races have all settled (or a date without a ``race_ok``
    race) writes no prediction, yet a 競走除外 at the gate becomes known exactly then, with the
    results. Without this pass its pick would stay a valid pick on screen. Own short transaction
    (committed here) under the ensemble version's per-date advisory locks of [from, to], taken in
    ascending order like every other writer; the voids carry a fresh run_id because no
    market-ev row of this run exists. Nothing else (scan, pick, checkpoint) is touched."""
    from . import attention_picks

    run_id = uuid.uuid4()
    ctx = attention_picks.PickContext(
        run_id=run_id,
        computed_at=datetime.datetime.now(datetime.UTC),
        ensemble_model_version=ensemble_version,
        single_model_version=single_version,
        with_results=frozenset(with_results),
        post_times={},  # the void pass reads races.post_time itself
        started=started,
    )
    try:
        day = d_from
        while day <= d_to:
            _lock_version_day(session, ensemble_version, day.isoformat())
            day += datetime.timedelta(days=1)
        voided = attention_picks.void_scratched(session, date_from=d_from, date_to=d_to, ctx=ctx)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return {
        "races_first_computed": 0,
        "races_already_scanned": 0,
        "written": 0,
        "voided_scratched": voided,
        "skipped_no_horse_number": 0,
        "void_run_id": str(run_id) if voided else None,
    }


def compute_and_persist(
    session: Session,
    *,
    race_date_from: datetime.date | str,
    race_date_to: datetime.date | str,
    model_dir: str | pathlib.Path,
    model_version: str | None = None,
    pending_only: bool = False,
    ensemble_dir: str | pathlib.Path | None = None,
    ensemble_version: str | None = None,
    ensemble_only: bool = False,
) -> dict:
    """Predict every ``race_ok`` race dated in [from, to] and replace its rows.

    Per race: ``DELETE WHERE race_id=:r AND model_version=:mv`` then INSERT, all races in ONE
    transaction (committed here). A recompute therefore never leaves a scratched horse's stale row.
    Races that are not ``race_ok`` (a started horse without odds) are left untouched and counted.

    ``pending_only=True`` (the automatic recompute after an odds refresh) rewrites only races that
    have no race_results row yet, so a settled race keeps the value computed on its pre-race odds.
    Writers of the same dates are serialised with a transaction-scoped advisory lock per
    (model_version, date), so a manual backfill and a worker job cannot interleave their
    DELETE/INSERT pairs.

    Feature 138 — ``ensemble_dir`` given: the 15-seed average is predicted from the same features
    and written in the same transaction with the same run_id / computed_at (locks: per date in
    ascending order, the single version then the ensemble). Inside that transaction the attention
    picks are recorded (``attention_picks.record_attention``: scratched voids, the first-compute
    scan rows and the picks of first computations); after the commit the checkpoint decisions run
    in a separate transaction whose failure is reported in ``summary['checkpoints']['error']`` and
    never undoes the computed rows. ``ensemble_only=True`` (backfill only, plan D24) writes the
    ensemble rows, scans and picks but leaves the single-seed rows untouched — the single-seed
    predictions are still made in memory for S5 and ``single_expected_return``. A run that
    computes nothing (``status='skipped'``) still runs the void pass over [from, to] in its own
    transaction (``_void_pass_only``) and reports it in ``summary['picks']``.
    """
    d_from = _as_date(race_date_from)
    d_to = _as_date(race_date_to)
    if d_from > d_to:
        raise ValueError(f"race_date_from {d_from} is after race_date_to {d_to}")
    if ensemble_only and ensemble_dir is None:
        raise ValueError("ensemble_only requires ensemble_dir")
    mdir = validate_model_dir(model_dir)
    version = model_version or mdir.name
    model = MarketEvModel.load(mdir, version)  # fail fast before the heavy load
    ens_model: EnsembleMarketEvModel | None = None
    if ensemble_dir is not None:
        edir = validate_model_dir(ensemble_dir, flag="--ensemble-dir")
        ens_version = ensemble_version or edir.name
        _check_attention_versions(version, ens_version)
        ens_model = EnsembleMarketEvModel.load(edir, ens_version)

    raw = load_rows(session.connection(), through=d_to.isoformat())
    raw_dates = pd.to_datetime(raw["race_date"]).dt.date
    in_range = (raw_dates >= d_from) & (raw_dates <= d_to)
    in_range_ids = sorted(raw.loc[in_range, "race_id"].astype(str).unique())
    summary: dict = {
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "model_version": version,
        "logic_version": LOGIC_VERSION,
        "races_in_range": len(in_range_ids),
        "races_invalid_odds": 0,
        "races": 0,
        "horses": 0,
        "result_pending_races": 0,
        "boosters": {},
        "run_id": None,
        "versions": {},
        "picks": None,
        "checkpoints": None,
    }
    if ens_model is not None:
        summary["ensemble_version"] = ens_model.version
        summary["ensemble_only"] = ensemble_only
        if ensemble_only:
            summary["model_version"] = ens_model.version
            summary["logic_version"] = ENSEMBLE_LOGIC_VERSION

    def _skipped(reason: str, results: set[str]) -> dict:
        out = {**summary, "status": "skipped", "reason": reason}
        if ens_model is not None:
            out["picks"] = _void_pass_only(
                session, d_from=d_from, d_to=d_to, started=_started_fields(raw, in_range),
                with_results=results, ensemble_version=ens_model.version,
                single_version=version,
            )
        return out

    if not in_range_ids:
        return _skipped("no_races_with_odds", set())
    # read at the same moment as the odds: "no race_results row yet" for the odds that were used
    with_results = _races_with_results(session, in_range_ids)
    summary["races_settled_skipped"] = 0
    if pending_only:
        summary["races_settled_skipped"] = sum(1 for r in in_range_ids if r in with_results)
        in_range_ids = [r for r in in_range_ids if r not in with_results]
        if not in_range_ids:
            return _skipped("no_pending_races", with_results)

    feats = build_features(raw)
    feat_dates = feats["race_date"].dt.date
    target = feats[(feat_dates >= d_from) & (feat_dates <= d_to)]
    if pending_only:
        target = target[target["race_id"].astype(str).isin(set(in_range_ids))]
    pred = predict(model, target)
    pred, invalid = drop_invalid_odds_races(pred)
    summary["races_invalid_odds"] = len(invalid)
    pred_ens: pd.DataFrame | None = None
    if ens_model is not None:
        pred_ens, invalid_ens = drop_invalid_odds_races(predict_ensemble(ens_model, target))
        # the same target, the same race_ok / odds filters: anything else is a defect (fail-closed)
        if invalid_ens != invalid or _row_keys(pred_ens) != _row_keys(pred):
            raise RuntimeError(
                "market-ev versions disagree on the computed races/horses: "
                f"single={len(pred)} rows, ensemble={len(pred_ens)} rows"
            )
    if pred.empty:
        return _skipped("no_races_with_odds", with_results)

    run_id = uuid.uuid4()
    computed_at = datetime.datetime.now(datetime.UTC)
    write_single = not ensemble_only
    versions: dict[str, dict] = {}
    picks_summary: dict | None = None
    try:
        for day in sorted({str(d) for d in pd.to_datetime(target["race_date"]).dt.date}):
            if write_single:
                _lock_version_day(session, version, day)
            if ens_model is not None:
                _lock_version_day(session, ens_model.version, day)
        if write_single:
            races, horses, pending = _replace_rows(
                session, pred, model_version=version, logic_version=LOGIC_VERSION,
                with_results=with_results, run_id=run_id, computed_at=computed_at,
            )
            versions[version] = {"races": races, "horses": horses, "result_pending_races": pending,
                                 "logic_version": LOGIC_VERSION, "boosters": _boosters(pred)}
        if ens_model is not None and pred_ens is not None:
            from . import attention_picks

            races, horses, pending = _replace_rows(
                session, pred_ens, model_version=ens_model.version,
                logic_version=ENSEMBLE_LOGIC_VERSION, with_results=with_results, run_id=run_id,
                computed_at=computed_at,
            )
            versions[ens_model.version] = {
                "races": races, "horses": horses, "result_pending_races": pending,
                "logic_version": ENSEMBLE_LOGIC_VERSION, "boosters": _boosters(pred_ens),
            }
            target_ids = sorted(target["race_id"].astype(str).unique())
            picks_summary = attention_picks.record_attention(
                session,
                pred_ens=pred_ens,
                pred_single=pred,
                feats=target,
                ctx=attention_picks.PickContext(
                    run_id=run_id,
                    computed_at=computed_at,
                    ensemble_model_version=ens_model.version,
                    single_model_version=version,
                    with_results=frozenset(with_results),
                    post_times=_post_times(session, target_ids),
                    started=_started_fields(raw, in_range),
                ),
                void_date_from=d_from,
                void_date_to=d_to,
            )
        session.commit()
    except Exception:
        session.rollback()
        raise

    top = versions[version] if write_single else versions[ens_model.version]
    result = {
        **summary,
        "status": "ok",
        "reason": None,
        "races": top["races"],
        "horses": top["horses"],
        "result_pending_races": top["result_pending_races"],
        "boosters": top["boosters"],
        "run_id": str(run_id),
        "versions": versions,
        "picks": picks_summary,
    }
    if ens_model is not None:
        from . import attention_checkpoints

        try:
            result["checkpoints"] = attention_checkpoints.evaluate_checkpoints(
                session, now=computed_at, run_id=run_id
            )
        except Exception as exc:  # noqa: BLE001 — never undo the committed computation
            session.rollback()
            result["checkpoints"] = {"written": [], "pending": [], "error": _one_line_error(exc)}
    return result
