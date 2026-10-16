"""140 研究ハーネス(①-a / ①-c)の共有部品。本番コードは変更しない(読むだけ)。

- 市場 12 列の再計算は R02 の ``market_block`` を importlib で読み込んで使う(複製しない)。
- 注入生成器 ``u = a(h) + b(h)·log q_F + s(h)·(√ρ·ε_race + √(1−ρ)·ε_i)`` の適用(``perturb``)。
- LightGBM の設計行列は本番 ``market_ev._design_matrix`` をそのまま使う(列順=features→cats)。

    cd training && uv run python ../scripts/roi_explore/judged_20261011/<script>.py ...
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[3]
HERE = pathlib.Path(__file__).resolve().parent
R02_DRIFT = ROOT / "scripts/roi_explore/missed_20261004/r02_drift.py"
PAIRS = ROOT / "artifacts/roi_explore/missed_20261004/R02_odds_drift/pairs.parquet"
SCORED_PAIRS = ROOT / "artifacts/roi_comprehensive_20261005/price_robustness/scored_pairs.parquet"
ROWS_2007 = ROOT / "artifacts/market_ev/rows_2007.parquet"
ENS_DIR = ROOT / "artifacts/market_ev/mev-ens15-v1"
ENS_VERSION = "mev-ens15-v1"
ENS_RESULTS = ROOT / "artifacts/roi_explore/results"
JUDGED_DIR = ROOT / "artifacts/market_ev/judged/mev-ens15-judged-v0"
JUDGED_VERSION = "mev-ens15-judged-v0"
GENERATOR_JSON = HERE / "generator.json"
COEFFICIENTS_JSON = HERE / "coefficients.json"
PROTOCOL_JSON = HERE / "protocol.json"
PROTOCOL_SHA = HERE / "protocol.sha256"
RESULTS_DIR = HERE / "results"
SEEDS = tuple(range(1, 16))

#: 生成器の fit 期間(R02 の 17 開催日)。ハーネスはこの期間を含む --from を拒否する(FR-006)。
FIT_PERIOD_FROM = "2026-08-02"
FIT_PERIOD_TO = "2026-10-04"

#: horizon 帯(分)。右閉 (lo, hi]。最後の帯は上限なしで扱う(評価では ">600")。
H_BAND_EDGES = (0.0, 10.0, 30.0, 60.0, 180.0, 600.0, 3000.0)
H_BAND_LABELS = ("(0,10]", "(10,30]", "(30,60]", "(60,180]", "(180,600]", "(600,3000]")
H_BAND_LABELS_SCORE = ("(0,10]", "(10,30]", "(30,60]", "(60,180]", "(180,600]", ">600")

#: 本番 spec と同じ LightGBM params(m3_spike.py PARAMS)+決定論(1 スレッド)。
LGB_PARAMS = {
    "objective": "binary",
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_data_in_leaf": 1000,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 10.0,
    "verbosity": -1,
    "num_threads": 1,
    "max_bin": 255,
    "deterministic": True,
    "force_row_wise": True,
}
ROUNDS = 300

DISCLAIMERS = (
    "ROI の改善を示すものではない",
    "判断時専用版(mev-ens15-judged)は表示・確定側に接続しない(研究 artifact・本番非結線)",
)


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_r02():
    """R02 の r02_drift モジュール(``MARKET_COLS``・``market_block``)。"""
    spec = importlib.util.spec_from_file_location("r02_drift", R02_DRIFT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def h_band_index(mins: np.ndarray, *, open_last: bool = False) -> np.ndarray:
    """帯の添字(右閉)。``open_last`` のとき最後の帯は上限なし。範囲外は -1。"""
    m = np.asarray(mins, dtype=float)
    idx = np.searchsorted(np.asarray(H_BAND_EDGES[1:]), m, side="left")
    idx = idx.astype(int)
    bad = (m <= H_BAND_EDGES[0]) | ~np.isfinite(m)
    if open_last:
        idx = np.minimum(idx, len(H_BAND_LABELS) - 1)
    else:
        bad |= m > H_BAND_EDGES[-1]
    idx[bad] = -1
    return idx


def band_params(gen: dict, mins: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """generator.json の帯別 a/b/s を分単位 horizon に割り当てる(範囲外は最後の帯にクランプ)。"""
    idx = h_band_index(mins, open_last=True)
    if (idx < 0).any():
        raise ValueError("horizon must be > 0 minutes")
    bands = gen["bands"]
    a = np.array([bands[i]["a"] for i in idx], dtype=float)
    b = np.array([bands[i]["b"] for i in idx], dtype=float)
    s = np.array([bands[i]["s"] for i in idx], dtype=float)
    return a, b, s


def race_segments(race_ids: np.ndarray) -> tuple[np.ndarray, int]:
    """(race,horse) 順の配列からレース添字(0..n_r-1)と n_r。連続した同一 race_id を 1 レースとみなす。"""
    rid = np.asarray(race_ids)
    starts = np.r_[0, np.flatnonzero(rid[1:] != rid[:-1]) + 1]
    n_r = len(starts)
    seg = np.repeat(np.arange(n_r), np.diff(np.r_[starts, len(rid)]))
    return seg, n_r


def perturb_odds(
    odds_f: np.ndarray,
    seg: np.ndarray,
    n_r: int,
    mins_per_race: np.ndarray,
    gen: dict,
    rng: np.random.Generator,
) -> np.ndarray:
    """確定オッズ O_F から判断時相当のオッズ O_J を生成する(m3_spike.py ``perturb`` と同じ手順)。

    u = a + b·log q_F + s·(√ρ·ε_race + √(1−ρ)·ε_i)、O_J = O_F·exp(u)、レース内 Σ1/O を確定と同じに
    再正規化、0.1 刻み・下限 1.0。``rng`` からの乱数の順序は (ε_race[n_r], ε_i[n]) で固定。

    注(statistics-2): レース内で定数の項 — a(h) と s·√ρ·ε_race — は exp(定数) として Σ1/O の再正規化で
    厳密に打ち消される。注入に効くのは b(h)(log q_F への傾き=圧縮)と s·√(1−ρ)(レース内残差 sd)だけで、
    ρ の役割は s²(1−ρ) を実データのレース内残差分散に合わせること(ICC 推定量=fit_generator.icc_rho)。
    a・ρ の推定精度そのものは注入結果に影響しない(合成テスト test_perturb_race_constant_terms_cancel)。"""
    odds_f = np.asarray(odds_f, dtype=float)
    inv_f = 1.0 / odds_f
    q_f = inv_f / np.bincount(seg, weights=inv_f, minlength=n_r)[seg]
    a, b, s = band_params(gen, mins_per_race)
    rho = float(gen["rho"])
    eps_r = rng.standard_normal(n_r)
    eps_i = rng.standard_normal(len(odds_f))
    u = (
        a[seg]
        + b[seg] * np.log(q_f)
        + s[seg] * (np.sqrt(rho) * eps_r[seg] + np.sqrt(1.0 - rho) * eps_i)
    )
    oj = odds_f * np.exp(u)
    inv_j = np.bincount(seg, weights=1.0 / oj, minlength=n_r)
    oj = oj * (inv_j / np.bincount(seg, weights=inv_f, minlength=n_r))[seg]
    return np.maximum(np.round(oj, 1), 1.0)


def apply_market_block(df: pd.DataFrame, r02, key: str = "race_id") -> pd.DataFrame:
    """``df['odds']`` から市場 12 列を再計算して上書きし、``popularity`` を ``odds_rank`` で置く。
    df は (key, horse_number) 順であること。"""
    d = df.reset_index(drop=True)
    m = r02.market_block(d[[key, "horse_id", "horse_number", "odds"]].copy(), key)
    for c in r02.MARKET_COLS:
        d[c] = m[c].to_numpy()
    d["popularity"] = d["odds_rank"].astype(float)
    return d


def horizon_pool_minutes(pairs: pd.DataFrame) -> np.ndarray:
    """R02 の組ごと(pair_id で 1 つ)の horizon(分)=注入の h の抽出元。"""
    return pairs.drop_duplicates("pair_id")["hours"].to_numpy(dtype=float) * 60.0


def nll_binary(p: np.ndarray, y: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, float)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def winner_nll_per_race(p: np.ndarray, race_ids: np.ndarray, won: np.ndarray) -> pd.Series:
    """レース内正規化 winner NLL(1 レース 1 値)。勝者が 1 頭でないレースは NaN。"""
    s = pd.DataFrame(
        {"p": np.asarray(p, float), "r": np.asarray(race_ids), "w": np.asarray(won, bool)}
    )
    s["pn"] = s["p"] / s.groupby("r")["p"].transform("sum")
    g = s[s["w"]].groupby("r")["pn"]
    out = -np.log(g.first().clip(lower=1e-12))
    out[g.size() != 1] = np.nan
    return out


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(np.asarray(p, float), 1e-9, 1 - 1e-9)
    return np.log(p / (1 - p))


def sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.asarray(z, float)))


def read_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: pathlib.Path, obj: dict) -> None:
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=1, default=_default), encoding="utf-8"
    )


def _default(x):
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        v = float(x)
        return v if np.isfinite(v) else None
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, (pd.Timestamp,)):
        return x.isoformat()
    raise TypeError(f"not JSON serialisable: {type(x)}")
