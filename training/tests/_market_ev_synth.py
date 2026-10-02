"""Feature 137: tiny fixtures for the market-aware expected return (market_ev) tests.

``raw_frame`` mimics ``market_ev.ROWS_SQL`` output (one row per started horse); ``write_model_dir``
writes a model directory in the production layout (``model.spec.json`` + ``model_YYYY.txt``) with a
tiny LightGBM binary booster, so the tests never depend on the git-ignored real artifact.
"""

from __future__ import annotations

import datetime
import json
import pathlib

import numpy as np
import pandas as pd

#: a small subset of the production inputs, mixing market / history / human / categorical columns
SPEC_FEATURES = ["odds", "q", "odds_rank", "career_starts", "prev_finish", "jockey_win_rate_all"]
SPEC_CATS = ["venue_code", "track_type"]
SPEC_CAT_MAPS = {"venue_code": {"05": 0, "06": 1}, "track_type": {"芝": 0, "ダ": 1}}

D1 = datetime.date(2026, 1, 5)
D2 = datetime.date(2026, 1, 12)
D3 = datetime.date(2026, 1, 19)
DATES = (D1, D2, D3)

#: race 1 of each day: H1-H3 / race 2: H4-H6. J1 rides H1 (race 1) AND H4 (race 2) on the same day.
_CARD = {
    1: [("H1", "J1", "T1"), ("H2", "J2", "T1"), ("H3", "J3", "T2")],
    2: [("H4", "J1", "T2"), ("H5", "J2", "T1"), ("H6", "J3", "T2")],
}
_ODDS = (2.0, 4.0, 8.0)


def race_id(day: datetime.date, race_number: int) -> str:
    return f"{day:%Y%m%d}05{race_number:02d}"


def raw_frame() -> pd.DataFrame:
    rows = []
    for day in DATES:
        for race_number, card in _CARD.items():
            for i, (horse, jockey, trainer) in enumerate(card):
                rows.append({
                    "race_id": race_id(day, race_number),
                    "horse_id": horse,
                    "horse_number": i + 1,
                    "frame": i + 1,
                    "sex": "牡",
                    "age": 3,
                    "weight": 480 + i,
                    "weight_diff": 0,
                    "jockey_weight": 55.0,
                    "odds": _ODDS[i],
                    "popularity": i + 1,
                    "running_style": "先行",
                    "jockey_id": jockey,
                    "trainer_id": trainer,
                    "horse_row_updated_at": pd.Timestamp(day, tz="UTC"),
                    "race_date": day,
                    "venue_code": "05",
                    "race_number": race_number,
                    "distance": 1600,
                    "track_type": "芝",
                    "going": "良",
                    "weather": "晴",
                    "race_class": "未勝利",
                    "grade": None,
                    "prize_money": 500,
                    "finish_order": i + 1,
                    "result_status": "finished",
                    "margin_sec": 0.2 * i,
                    "last_3f": 34.0 + i,
                    "sire_line": "ナスルーラ系",
                    "damsire_line": None,
                })
    return pd.DataFrame(rows)


def write_model_dir(
    path: pathlib.Path,
    *,
    years: tuple[int, ...] = (2026,),
    seed: int = 0,
) -> pathlib.Path:
    """Write ``model.spec.json`` + ``model_{y}.txt`` for each year (tiny random binary boosters)."""
    import lightgbm as lgb

    path.mkdir(parents=True, exist_ok=True)
    spec = {"objective": "binary", "features": SPEC_FEATURES, "cats": SPEC_CATS,
            "cat_maps": SPEC_CAT_MAPS}
    (path / "model.spec.json").write_text(json.dumps(spec, ensure_ascii=False))
    rng = np.random.default_rng(seed)
    n_cols = len(SPEC_FEATURES) + len(SPEC_CATS)
    for year in years:
        X = rng.normal(size=(400, n_cols))
        y = (X[:, 0] + rng.normal(size=400) > 0.5).astype(int)
        params = {"objective": "binary", "verbose": -1, "num_leaves": 4,
                  "min_data_in_leaf": 10, "seed": seed}
        bst = lgb.train(params, lgb.Dataset(X, y), num_boost_round=5)
        bst.save_model(str(path / f"model_{year}.txt"))
    return path


def write_ensemble_dir(
    path: pathlib.Path,
    *,
    years: tuple[int, ...] = (2026,),
    seeds: tuple[int, ...] = tuple(range(1, 16)),
    version: str | None = None,
) -> pathlib.Path:
    """Feature 138: an ensemble in the production layout of ``assemble_ens15_20261001.py``.

    ``seed_NN/model_{y}.txt`` (one tiny booster per seed and year, distinct seeds so the members
    differ), ``ensemble.spec.json`` (shared spec + seeds / threads / deterministic / drop groups /
    first training year) and one
    ``ensemble_{y}.json`` manifest per year with each member's relative path and sha256."""
    import hashlib

    path.mkdir(parents=True, exist_ok=True)
    version = version or path.name
    for s in seeds:
        write_model_dir(path / f"seed_{s:02d}", years=years, seed=s)
    spec = {"objective": "binary", "features": SPEC_FEATURES, "cats": SPEC_CATS,
            "cat_maps": SPEC_CAT_MAPS, "version": version, "seeds": list(seeds),
            "num_threads": 1, "deterministic": True, "drop_groups": ["sameday", "weightlive"],
            "train_from": 2007, "booster_years": list(years),
            "members": [f"seed_{s:02d}" for s in seeds]}
    (path / "ensemble.spec.json").write_text(json.dumps(spec, ensure_ascii=False))
    for year in years:
        members = []
        for s in seeds:
            rel = f"seed_{s:02d}/model_{year}.txt"
            members.append({"seed": s, "path": rel,
                            "sha256": hashlib.sha256((path / rel).read_bytes()).hexdigest()})
        manifest = {"version": version, "year": year, "members": members}
        (path / f"ensemble_{year}.json").write_text(json.dumps(manifest, sort_keys=True))
    return path
