"""T009: 並走評価ハーネス(FR-009/FR-010)。現行 ens15 / ①-a judged / ①-c1 / ①-c2 を判断時オッズで採点する。

母集団 = races(race_date∈[from,to]) ∧ 結果あり ∧ market_ev_predictions(model_version='mev-ens15-v1',
result_pending_at_compute=true)の全 started 馬の行がある ∧ 全 started 馬に odds_used ∧ 非同着。
--from ≤ 2026-10-04(生成器・c1 の fit 期間)は拒否する。``odds_observed_at`` の出所は**行の ``logic_version`` の
``oddsobs=`` トークン**(全行 quote=quote、それ以外=proxy・D12。``races.win_odds_observed_at`` は確定後に上書きされるので
使わない)。proxy のレースがある日は dry_run=true(記録のみ・判定の母数に数えない)。レース内で ``odds_observed_at`` が
複数値のレースは除外(protocol v2・statistics-4)。h = max(1, (post_time − odds_observed_at)/分)。特徴 = 本番 ``market_ev.load_rows``→``build_features`` の as-of 65 列 +
``odds_used`` から r02_drift.market_block で再計算した市場 12 列。出力は results/<from>_<to>.json(append-only)。
DB は読み取り専用(SET TRANSACTION READ ONLY)。

    cd training && uv run python ../scripts/roi_explore/judged_20261011/score.py --from 2026-10-10 --to 2026-10-11
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common as C  # noqa: E402
import fit_correction as FC  # noqa: E402

BOOT_B = 4000
BOOT_SEED = 20261011
ARMS = ("ens15", "judged", "c1", "c2")
EV_BAND_LABELS = ("<=1.0", "(1.0,1.2]", ">1.2")
MIN_DAYS = 8
MIN_RACES = 200
DEFAULT_DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


# ------------------------------------------------------------------ pure helpers (unit-tested)


def check_window(d_from: str, d_to: str) -> tuple[dt.date, dt.date]:
    a, b = dt.date.fromisoformat(d_from), dt.date.fromisoformat(d_to)
    if a <= dt.date.fromisoformat(C.FIT_PERIOD_TO):
        raise ValueError(
            f"--from {a} is inside the generator/c1 fit period (<= {C.FIT_PERIOD_TO}); refused (FR-006)"
        )
    if a > b:
        raise ValueError(f"--from {a} is after --to {b}")
    return a, b


def h_minutes(post_time, observed_at) -> np.ndarray:
    pt = pd.to_datetime(pd.Series(post_time), utc=True)
    ob = pd.to_datetime(pd.Series(observed_at), utc=True)
    h = (pt - ob).dt.total_seconds().to_numpy(dtype=float) / 60.0
    return np.maximum(1.0, h)


def h_band_label(mins: np.ndarray) -> np.ndarray:
    idx = C.h_band_index(mins, open_last=True)
    return np.asarray(C.H_BAND_LABELS_SCORE, dtype=object)[idx]


def ev_band_label(ev: np.ndarray) -> np.ndarray:
    ev = np.asarray(ev, float)
    return np.where(
        ev <= 1.0, EV_BAND_LABELS[0], np.where(ev <= 1.2, EV_BAND_LABELS[1], EV_BAND_LABELS[2])
    )


ODDSOBS_QUOTE = "oddsobs=quote"


def odds_observed_source(logic_versions) -> str:
    """``quote`` iff EVERY row of the race was computed with the race's win-quote provenance, i.e.
    its stored ``logic_version`` carries the per-row token ``oddsobs=quote`` (A3-era market_ev);
    anything else (``oddsobs=proxy`` = fallback to race_horses.updated_at, no token = pre-A3
    compute, NULL) is ``proxy``. The race's own ``races.win_odds_observed_at`` cannot be used for
    this: the final-odds write overwrites it once results land."""
    for lv in logic_versions:
        if lv is None or (isinstance(lv, float) and np.isnan(lv)):
            return "proxy"
        if ODDSOBS_QUOTE not in str(lv).split(";"):
            return "proxy"
    return "quote"


def select_population(
    preds: pd.DataFrame,
    started: pd.DataFrame,
    results: pd.DataFrame,
    payouts: pd.DataFrame,
    provenance: pd.DataFrame | None,
) -> tuple[pd.DataFrame, dict]:
    """評価母集団(馬行)と除外理由。

    preds: race_id, horse_id, horse_number, odds_used, odds_observed_at, post_time, race_date, computed_at,
           logic_version(quote/proxy は行の ``oddsobs=`` トークンで決める=odds_observed_source)
    started: race_id, horse_id, odds (現在値=O_F)   results: race_id, horse_id, finish_order, result_status
    payouts: race_id, horse_number, payout_yen        provenance: races.win_odds_observed_at(情報のみ・判定に使わない)
    """
    excl: dict[str, list[str]] = {
        k: []
        for k in (
            "no_results",
            "results_incomplete",
            "dead_heat_or_no_winner",
            "prediction_rows_mismatch",
            "odds_used_missing",
            "post_time_missing",
            "odds_observed_at_missing",
            "odds_observed_at_mixed_within_race",
        )
    }
    out = []
    started_g = {k: g for k, g in started.groupby("race_id")}
    res_g = {k: g for k, g in results.groupby("race_id")}
    pay_g = {k: g for k, g in payouts.groupby("race_id")} if len(payouts) else {}
    prov = None
    if provenance is not None and len(provenance):
        prov = provenance.set_index("race_id")["win_odds_observed_at"]
    for rid, g in preds.groupby("race_id", sort=True):
        st = started_g.get(rid)
        rs = res_g.get(rid)
        if st is None or rs is None or len(rs) == 0:
            excl["no_results"].append(rid)
            continue
        if not set(st["horse_id"]) <= set(rs["horse_id"]):
            excl["results_incomplete"].append(rid)
            continue
        winners = rs[(rs["result_status"] == "finished") & (rs["finish_order"] == 1)]
        if len(winners) != 1:
            excl["dead_heat_or_no_winner"].append(rid)
            continue
        if set(g["horse_id"]) != set(st["horse_id"]):
            excl["prediction_rows_mismatch"].append(rid)
            continue
        ou = pd.to_numeric(g["odds_used"], errors="coerce")
        if ou.isna().any() or (ou <= 0).any():
            excl["odds_used_missing"].append(rid)
            continue
        if g["post_time"].isna().any():
            excl["post_time_missing"].append(rid)
            continue
        if g["odds_observed_at"].isna().any():
            excl["odds_observed_at_missing"].append(rid)
            continue
        if g["odds_observed_at"].nunique() > 1:
            # protocol v2 (statistics-4): h is a race constant by construction (a quote is one
            # payload per race); a proxy observed_at can differ per horse (updated_at moves per
            # row), and the race-normalised winner NLL of a race split across h bands would be
            # normalised over a partial race. Fail-closed: excluded and counted.
            excl["odds_observed_at_mixed_within_race"].append(rid)
            continue
        d = g.copy()
        d["odds_used"] = ou.astype(float)
        d["won"] = d["horse_id"].isin(set(winners["horse_id"]))
        d = d.merge(
            st[["race_id", "horse_id", "odds"]].rename(columns={"odds": "O_F"}),
            on=["race_id", "horse_id"],
            how="left",
        )
        d["O_F"] = pd.to_numeric(d["O_F"], errors="coerce").astype(float)
        pay = pay_g.get(rid)
        if pay is not None and len(pay):
            pm = dict(
                zip(pay["horse_number"].astype(int), pay["payout_yen"].astype(float), strict=True)
            )
            d["payout_yen"] = [
                pm.get(int(hn), 0.0) if w else 0.0
                for hn, w in zip(d["horse_number"], d["won"], strict=True)
            ]
            d["has_official_payout"] = True
        else:
            d["payout_yen"] = np.nan
            d["has_official_payout"] = False
        src = odds_observed_source(
            g["logic_version"].tolist() if "logic_version" in g.columns else [None] * len(g)
        )
        d["odds_observed_source"] = src
        d["race_provenance_present_now"] = bool(
            prov is not None and rid in prov.index and pd.notna(prov.loc[rid])
        )
        d["h_min"] = h_minutes(d["post_time"], d["odds_observed_at"])
        d["h_band"] = h_band_label(d["h_min"].to_numpy())
        d["day"] = pd.to_datetime(d["race_date"]).dt.date.astype(str)
        out.append(d)
    pop = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    if len(pop):
        pop = pop.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
        day_dry = pop.groupby("day")["odds_observed_source"].apply(
            lambda s: bool((s == "proxy").any())
        )
        pop["dry_run"] = pop["day"].map(day_dry).astype(bool)
    return pop, {k: sorted(v) for k, v in excl.items()}


def metrics(df: pd.DataFrame, p: np.ndarray) -> dict:
    """1 腕の指標。df は母集団(won・odds_used・O_F・payout_yen・has_official_payout・race_id)。"""
    p = np.asarray(p, float)
    y = df["won"].to_numpy(bool)
    oj = df["odds_used"].to_numpy(float)
    of = df["O_F"].to_numpy(float)
    ev = p * oj
    nll = C.nll_binary(p, y)
    wn = C.winner_nll_per_race(p, df["race_id"].to_numpy(), y)
    out = {
        "n": int(len(df)),
        "n_races": int(df["race_id"].nunique()),
        "won": int(y.sum()),
        "nll_J": float(nll.mean()),
        "winner_nll_J": float(np.nanmean(wn)) if len(wn) else None,
        "sum_p": float(p.sum()),
        "calib_ratio_all": float(y.sum() / max(p.sum(), 1e-12)),
        "median_O_F": float(np.nanmedian(of)),
        "median_O_J": float(np.median(oj)),
        "median_log_OF_over_OJ": float(np.nanmedian(np.log(of / oj))),
        "ev_bands": {},
    }
    lab = ev_band_label(ev)
    for b in EV_BAND_LABELS:
        m = lab == b
        out["ev_bands"][b] = {
            "n": int(m.sum()),
            "won": int(y[m].sum()),
            "sum_p": float(p[m].sum()),
            "calib_ratio": float(y[m].sum() / p[m].sum()) if m.any() else None,
        }
    m1 = ev > 1.0
    out["ev_gt1"] = {
        "n": int(m1.sum()),
        "won": int(y[m1].sum()),
        "sum_p": float(p[m1].sum()),
        "calib_ratio": float(y[m1].sum() / p[m1].sum()) if m1.any() else None,
    }
    m2 = ev > 1.2
    hp = df["has_official_payout"].to_numpy(bool)
    settle = m2 & hp
    pay = np.nan_to_num(df["payout_yen"].to_numpy(float), nan=0.0)
    out["ev_gt12"] = {
        "n": int(m2.sum()),
        "won": int(y[m2].sum()),
        "n_settled_official": int(settle.sum()),
        "n_without_official_payout": int((m2 & ~hp).sum()),
        "roi_official": float(pay[settle].sum() / 100.0 / settle.sum()) if settle.any() else None,
        "roi_note": "ROI の改善を示すものではない(記録のみ・採否に使わない)",
    }
    return out


def _ratio_reps(counts: np.ndarray, num: np.ndarray, den: np.ndarray) -> np.ndarray:
    n = counts @ num
    d = counts @ den
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(d > 0, n / d, np.nan)


def paired_bootstrap(day_idx: np.ndarray, n_days: int, series: dict, counts: np.ndarray) -> dict:
    """``series[name] = (num_arm, den_arm, num_ref, den_ref)`` 行単位の値。差 Σnum/Σden(arm) − Σnum/Σden(ref) の
    点推定と開催日ブロック bootstrap 95% 区間(counts は全腕・全系列で共通の抽選)。"""
    out = {}
    for name, (na, da, nr, dr) in series.items():
        sa = np.bincount(day_idx, weights=na, minlength=n_days)
        sda = np.bincount(day_idx, weights=da, minlength=n_days)
        sr = np.bincount(day_idx, weights=nr, minlength=n_days)
        sdr = np.bincount(day_idx, weights=dr, minlength=n_days)
        pa = sa.sum() / sda.sum() if sda.sum() > 0 else np.nan
        pr = sr.sum() / sdr.sum() if sdr.sum() > 0 else np.nan
        reps = _ratio_reps(counts, sa, sda) - _ratio_reps(counts, sr, sdr)
        ok = np.isfinite(reps)
        out[name] = {
            "point": float(pa - pr) if np.isfinite(pa - pr) else None,
            "ci_low": float(np.percentile(reps[ok], 2.5)) if ok.sum() > 0 else None,
            "ci_high": float(np.percentile(reps[ok], 97.5)) if ok.sum() > 0 else None,
            "n_replicates_valid": int(ok.sum()),
            "arm_value": float(pa) if np.isfinite(pa) else None,
            "ref_value": float(pr) if np.isfinite(pr) else None,
        }
    return out


def paired_series(
    df: pd.DataFrame, p_arm: np.ndarray, p_ref: np.ndarray, mask: np.ndarray | None = None
) -> dict:
    """paired_bootstrap に渡す行単位の系列(NLL_J・winner NLL_J・EV_J>1.0 帯の Σwon/Σp̂)。"""
    y = df["won"].to_numpy(float)
    oj = df["odds_used"].to_numpy(float)
    m = np.ones(len(df), bool) if mask is None else np.asarray(mask, bool)
    nll_a, nll_r = C.nll_binary(p_arm, y), C.nll_binary(p_ref, y)
    # winner NLL: one value per race placed on the winner's row
    wa = C.winner_nll_per_race(p_arm, df["race_id"].to_numpy(), y.astype(bool))
    wr = C.winner_nll_per_race(p_ref, df["race_id"].to_numpy(), y.astype(bool))
    rid = df["race_id"].to_numpy()
    win_row = y.astype(bool)
    wrow_a = np.where(win_row, pd.Series(rid).map(wa).to_numpy(float), 0.0)
    wrow_r = np.where(win_row, pd.Series(rid).map(wr).to_numpy(float), 0.0)
    ones = np.ones(len(df))
    ev_a = (p_arm * oj > 1.0) & m
    ev_r = (p_ref * oj > 1.0) & m
    return {
        "nll_J": (nll_a * m, ones * m, nll_r * m, ones * m),
        "winner_nll_J": (wrow_a * m, win_row * m, wrow_r * m, win_row * m),
        "calib_ratio_ev_gt1": (y * ev_a, p_arm * ev_a, y * ev_r, p_ref * ev_r),
    }


def h_distribution(h: np.ndarray) -> dict:
    lab = h_band_label(h)
    return {
        "quantiles_min": {str(q): float(np.quantile(h, q)) for q in (0.1, 0.25, 0.5, 0.75, 0.9)},
        "by_band": {b: int((lab == b).sum()) for b in C.H_BAND_LABELS_SCORE},
    }


def write_result(path: pathlib.Path, obj: dict) -> None:
    if path.exists():
        raise FileExistsError(
            f"{path} exists; results are append-only (use a different window or keep the file)"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    C.write_json(path, obj)


def verify_protocol() -> dict:
    data = C.PROTOCOL_JSON.read_bytes()
    sha = C.sha256_bytes(data)
    want = C.PROTOCOL_SHA.read_text().split()[0]
    if sha != want:
        raise RuntimeError(
            f"protocol.json sha256 {sha} != protocol.sha256 {want} (frozen protocol was edited)"
        )
    return {"protocol_sha256": sha, "protocol": C.read_json(C.PROTOCOL_JSON)}


# ------------------------------------------------------------------ DB (read-only)


def _conn(db_url: str):
    from sqlalchemy import create_engine, text

    eng = create_engine(db_url)
    conn = eng.connect()
    conn.execute(text("SET TRANSACTION READ ONLY"))
    return conn


def provenance_column_exists(conn) -> bool:
    from sqlalchemy import text

    r = conn.execute(
        text(
            "select 1 from information_schema.columns where table_name='races' "
            "and column_name='win_odds_observed_at'"
        )
    ).fetchone()
    return r is not None


def fetch_inputs(conn, d_from: dt.date, d_to: dt.date) -> dict:
    from sqlalchemy import text

    params = {"f": d_from, "t": d_to, "mv": C.ENS_VERSION}
    preds = pd.read_sql(
        text("""
        select m.race_id, m.horse_id, m.horse_number, m.odds_used, m.odds_observed_at, m.computed_at,
               m.logic_version, m.win_prob as stored_win_prob, r.post_time, r.race_date
        from market_ev_predictions m join races r using(race_id)
        where m.model_version = :mv and m.result_pending_at_compute and r.race_date between :f and :t
          and r.track_type <> '障'
        order by m.race_id, m.horse_number"""),
        conn,
        params=params,
    )
    started = pd.read_sql(
        text("""
        select rh.race_id, rh.horse_id, rh.horse_number, rh.odds
        from race_horses rh join races r using(race_id)
        where rh.entry_status = 'started' and r.race_date between :f and :t"""),
        conn,
        params=params,
    )
    results = pd.read_sql(
        text("""
        select rs.race_id, rs.horse_id, rs.finish_order, rs.result_status
        from race_results rs join races r using(race_id) where r.race_date between :f and :t"""),
        conn,
        params=params,
    )
    payouts = pd.read_sql(
        text("""
        select p.race_id, p.horse_number, p.payout_yen
        from official_win_payouts p join races r using(race_id) where r.race_date between :f and :t"""),
        conn,
        params=params,
    )
    has_prov = provenance_column_exists(conn)
    prov = None
    if has_prov:
        prov = pd.read_sql(
            text(
                "select race_id, win_odds_observed_at from races where race_date between :f and :t"
            ),
            conn,
            params=params,
        )
    return {
        "preds": preds,
        "started": started,
        "results": results,
        "payouts": payouts,
        "provenance": prov,
        "provenance_column_present": has_prov,
    }


def load_rows_compat(conn, d_to: dt.date) -> pd.DataFrame:
    """本番 ``market_ev.load_rows``。A3 の列(races.win_odds_observed_at)が未適用の DB では、本番 ROWS_SQL の
    来歴 coalesce を ``rh.updated_at`` に置き換えた互換 SQL で同じ行を読む(特徴は odds_observed_at に依存しない)。"""
    from sqlalchemy import text

    from horseracing_training import market_ev

    if provenance_column_exists(conn) or "win_odds_observed_at" not in market_ev.ROWS_SQL:
        return market_ev.load_rows(conn, through=d_to.isoformat())
    sql = market_ev.ROWS_SQL.replace(
        "coalesce(r.win_odds_observed_at, rh.updated_at)", "rh.updated_at"
    ).replace("(r.win_odds_observed_at is null)", "true")
    if "win_odds_observed_at" in sql:
        raise RuntimeError(
            "ROWS_SQL references win_odds_observed_at in an unexpected form; apply migration 0021"
        )
    return pd.read_sql(
        text(sql), conn, params={"through": d_to.isoformat(), "since": market_ev.DATA_START}
    )


def build_judged_features(
    conn, pop: pd.DataFrame, d_to: dt.date, r02
) -> tuple[pd.DataFrame, list[str]]:
    """本番の as-of 特徴(load_rows→build_features)を母集団の行に限定し、市場 12 列を odds_used から再計算。"""
    from horseracing_training import market_ev

    raw = load_rows_compat(conn, d_to)
    feats = market_ev.build_features(raw)
    feats["race_id"] = feats["race_id"].astype(str)
    feats["horse_id"] = feats["horse_id"].astype(str)
    keep = feats.merge(pop[["race_id", "horse_id"]], on=["race_id", "horse_id"], how="inner")
    # fail-closed: every population row must have exactly one feature row and each race must be complete
    cnt = keep.groupby("race_id").size()
    pop_cnt = pop.groupby("race_id").size()
    bad = sorted(set(pop_cnt.index) - set(cnt.index)) + sorted(
        r for r in cnt.index if cnt[r] != pop_cnt.get(r, -1)
    )
    keep = keep[~keep["race_id"].isin(bad)]
    keep = keep.merge(
        pop[["race_id", "horse_id", "odds_used", "h_min"]], on=["race_id", "horse_id"], how="inner"
    )
    keep = keep.sort_values(["race_id", "horse_number"]).reset_index(drop=True).copy()
    keep["odds_final_db"] = keep["odds"]
    keep["odds"] = keep["odds_used"].astype(float)
    keep = C.apply_market_block(keep, r02).copy()
    keep["log_min"] = np.log(keep["h_min"].to_numpy(float))
    return keep, bad


# ------------------------------------------------------------------ arms


def load_ens15(ens_dir: pathlib.Path):
    from horseracing_training import market_ev

    model = market_ev.EnsembleMarketEvModel.load(ens_dir, C.ENS_VERSION)
    man = model.booster_manifest_for_year(2026)
    boosters, sha = model.load_members(man)
    return model, boosters, {"manifest": man.name, "manifest_sha256": sha}


def load_judged(
    judged_dir: pathlib.Path, base_features: list, seeds=C.SEEDS
) -> tuple[dict | None, list, dict]:
    """①-a の 15 seed。欠けていれば (None, [], info) で腕を skip する。"""
    import lightgbm as lgb

    found, missing, boosters, spec = [], [], [], None
    for s in seeds:
        d = judged_dir / f"seed_{s:02d}"
        mp, sp = d / "model_2026.txt", d / "model.spec.json"
        if not (mp.exists() and sp.exists()):
            missing.append(s)
            continue
        sj = C.read_json(sp)
        if (
            sj.get("judged") is not True
            or sj.get("version") != C.JUDGED_VERSION
            or sj.get("features") != list(base_features) + ["log_min"]
            or sj.get("seed") != s
        ):
            raise RuntimeError(f"{sp}: not a valid judged spec for seed {s}")
        if spec is None:
            spec = sj
        elif sj["cat_maps"] != spec["cat_maps"] or sj["cats"] != spec["cats"]:
            raise RuntimeError(f"{sp}: cats/cat_maps differ between seeds")
        if C.sha256_file(mp) != sj.get("model_sha256"):
            raise RuntimeError(f"{mp}: sha256 differs from its spec")
        boosters.append(lgb.Booster(model_file=str(mp)))
        found.append(s)
    info = {
        "dir": str(judged_dir),
        "seeds_found": found,
        "seeds_missing": missing,
        "partial": len(found) != len(C.SEEDS),
    }
    if not found or missing:
        return None, [], info
    info["generator_sha256"] = spec.get("generator_sha256")
    info["model_sha256"] = {
        f"seed_{s:02d}": C.sha256_file(judged_dir / f"seed_{s:02d}" / "model_2026.txt")
        for s in found
    }
    return spec, boosters, info


def predict_arms(feat: pd.DataFrame, ens, judged, coef: dict) -> tuple[dict, dict]:
    from horseracing_training import market_ev

    model, boosters, _ = ens
    X = model.design_matrix(feat)
    p_ens = np.mean(np.vstack([b.predict(X, num_threads=1) for b in boosters]), axis=0)
    arms = {"ens15": p_ens}
    notes = {}
    spec_j, boosters_j, info_j = judged
    if spec_j is not None:
        XJ = market_ev._design_matrix(spec_j, feat)
        arms["judged"] = np.mean(
            np.vstack([b.predict(XJ, num_threads=1) for b in boosters_j]), axis=0
        )
    else:
        notes["judged"] = "skipped: artifact missing or incomplete"
    h = feat["h_min"].to_numpy(float)
    q = feat["q"].to_numpy(float)
    for c in ("c1", "c2"):
        arms[c] = FC.apply_correction(coef[c]["coef"], p_ens, h, q)
    return arms, notes


# ------------------------------------------------------------------ main


def evaluate(
    pop: pd.DataFrame, feat: pd.DataFrame, arms: dict, *, b: int = BOOT_B, seed: int = BOOT_SEED
) -> dict:
    """全腕の指標(全体・h 帯別・日別)と現行版との paired bootstrap(全体・h 帯別)。"""
    from horseracing_eval.bootstrap import block_bootstrap_counts

    cols = [
        "won",
        "O_F",
        "payout_yen",
        "has_official_payout",
        "day",
        "h_band",
        "dry_run",
        "odds_observed_source",
    ]
    df = feat.drop(columns=[c for c in cols if c in feat.columns]).merge(
        pop[["race_id", "horse_id", *cols]], on=["race_id", "horse_id"], how="inner"
    )
    assert len(df) == len(feat)
    days = sorted(df["day"].unique())
    day_idx = pd.Series(df["day"]).map({d: i for i, d in enumerate(days)}).to_numpy()
    counts = block_bootstrap_counts(len(days), b, seed)
    ref = arms["ens15"]
    out = {
        "n_rows": int(len(df)),
        "n_races": int(df["race_id"].nunique()),
        "n_days": len(days),
        "days": days,
        "won": int(df["won"].sum()),
        "h_distribution": h_distribution(df["h_min"].to_numpy()),
        "ev_gt1_counts_by_h_band": {},
        "arms": {},
    }
    bands = list(C.H_BAND_LABELS_SCORE)
    for name, p in arms.items():
        a = {"overall": metrics(df, p), "by_h_band": {}, "by_day": {}}
        for bl in bands:
            m = (df["h_band"] == bl).to_numpy()
            a["by_h_band"][bl] = metrics(df[m], p[m]) if m.any() else {"n": 0}
        for d in days:
            m = (df["day"] == d).to_numpy()
            a["by_day"][d] = dict(metrics(df[m], p[m]), dry_run=bool(df.loc[m, "dry_run"].iloc[0]))
        if name != "ens15":
            a["paired_vs_ens15"] = {
                "overall": paired_bootstrap(day_idx, len(days), paired_series(df, p, ref), counts),
                "by_h_band": {},
            }
            for bl in bands:
                m = (df["h_band"] == bl).to_numpy()
                if m.any():
                    a["paired_vs_ens15"]["by_h_band"][bl] = paired_bootstrap(
                        day_idx, len(days), paired_series(df, p, ref, mask=m), counts
                    )
        out["arms"][name] = a
    oj = df["odds_used"].to_numpy(float)
    for bl in bands:
        m = (df["h_band"] == bl).to_numpy()
        out["ev_gt1_counts_by_h_band"][bl] = {
            name: int(((p * oj > 1.0) & m).sum()) for name, p in arms.items()
        }
    out["bootstrap"] = {
        "method": "race-day block bootstrap, shared draws across arms/series",
        "B": b,
        "seed": seed,
        "n_blocks": len(days),
    }
    return out


def verdict_block(
    ev_all: dict, ev_elig: dict | None, protocol: dict, *, diagnostic_arms: set | frozenset = ()
) -> dict:
    """``diagnostic_arms``: arms scored outside the protocol (a ``--judged-seeds`` partial average
    is not the 15-seed ``judged`` arm) — recorded in the evaluation blocks but never judged
    (review harness-leak-protocol-2)."""
    n_days = ev_elig["n_days"] if ev_elig else 0
    n_races = ev_elig["n_races"] if ev_elig else 0
    v = {
        "eligible_days": n_days,
        "eligible_races": n_races,
        "min_days": MIN_DAYS,
        "min_races": MIN_RACES,
        "verdict": "recorded_only",
        "rules": protocol["judgement_rules"],
        "diagnostic_arms_not_judged": sorted(diagnostic_arms),
    }
    if n_days >= MIN_DAYS and n_races >= MIN_RACES:
        v["verdict"] = "judgeable"
        checks = {}
        for name, a in ev_elig["arms"].items():
            if name == "ens15":
                continue
            if name in diagnostic_arms:
                checks[name] = {"skipped": "diagnostic arm (partial/non-protocol average)"}
                continue
            pr = a["paired_vs_ens15"]["overall"]
            ii = pr["nll_J"]["ci_high"] is not None and pr["nll_J"]["ci_high"] < 0
            ra, rr = pr["calib_ratio_ev_gt1"]["arm_value"], pr["calib_ratio_ev_gt1"]["ref_value"]
            iii = ra is not None and rr is not None and abs(ra - 1) < abs(rr - 1)
            checks[name] = {
                "rule_ii_nll_ci_high_lt_0": bool(ii),
                "rule_iii_ev_gt1_ratio_closer_to_1": bool(iii),
                "rule_i_closing_oos": None if name == "judged" else "n/a",
                "note": "rule (i) for ①-a is checked separately on the closing-odds 2026 OOS (T012)",
            }
        v["checks_on_eligible_days"] = checks
    else:
        v["reason"] = (
            f"eligible (non-dry-run) days {n_days} < {MIN_DAYS} or races {n_races} < {MIN_RACES}"
        )
    return v


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d_from", required=True)
    ap.add_argument("--to", dest="d_to", required=True)
    ap.add_argument("--results-dir", default=str(C.RESULTS_DIR))
    ap.add_argument("--db-url", default=os.environ.get("DATABASE_URL", DEFAULT_DB))
    ap.add_argument("--ens-dir", default=str(C.ENS_DIR))
    ap.add_argument("--judged-dir", default=str(C.JUDGED_DIR))
    ap.add_argument("--coefficients", default=str(C.COEFFICIENTS_JSON))
    ap.add_argument(
        "--judged-seeds",
        default="",
        help="diagnostic only: comma list of seeds (default all 15; "
        "fewer marks the judged arm partial)",
    )
    args = ap.parse_args(argv)
    t0 = time.time()
    d_from, d_to = check_window(args.d_from, args.d_to)
    out_path = pathlib.Path(args.results_dir) / f"{d_from}_{d_to}.json"
    if out_path.exists():
        raise SystemExit(f"{out_path} exists; results are append-only")
    proto = verify_protocol()
    coef = C.read_json(pathlib.Path(args.coefficients))
    r02 = C.load_r02()
    conn = _conn(args.db_url)
    try:
        inp = fetch_inputs(conn, d_from, d_to)
        pop, excl = select_population(
            inp["preds"], inp["started"], inp["results"], inp["payouts"], inp["provenance"]
        )
        log(
            f"population: {pop['race_id'].nunique() if len(pop) else 0} races / {len(pop)} rows; "
            f"excluded {sum(len(v) for v in excl.values())} races ({time.time() - t0:.0f}s)"
        )
        if not len(pop):
            raise SystemExit("empty population")
        feat, bad = build_judged_features(conn, pop, d_to, r02)
    finally:
        conn.close()
    if bad:
        pop = pop[~pop["race_id"].isin(bad)].reset_index(drop=True)
        excl["feature_rows_mismatch"] = bad
    log(f"features: {len(feat)} rows ({time.time() - t0:.0f}s)")
    ens = load_ens15(pathlib.Path(args.ens_dir))
    seeds = tuple(int(s) for s in args.judged_seeds.split(",") if s) or C.SEEDS
    judged = load_judged(pathlib.Path(args.judged_dir), ens[0].spec["features"], seeds)
    if judged[0] is None and args.judged_seeds:
        # diagnostic partial run: load whatever was requested
        judged = load_judged_partial(pathlib.Path(args.judged_dir), ens[0].spec["features"], seeds)
    arms, notes = predict_arms(feat, ens, judged, coef)
    parity = ens15_parity(feat, pop, arms["ens15"])
    ev_all = evaluate(pop, feat, arms)
    elig = pop[~pop["dry_run"]]
    ev_elig = None
    if len(elig):
        m = feat["race_id"].isin(set(elig["race_id"])).to_numpy()
        ev_elig = evaluate(elig, feat[m].reset_index(drop=True), {k: v[m] for k, v in arms.items()})
    days = sorted(pop["day"].unique())
    res = {
        "window": {"from": str(d_from), "to": str(d_to)},
        "computed_at": dt.datetime.now(dt.UTC).isoformat(),
        "dry_run": bool(pop["dry_run"].all()),
        "dry_run_days": sorted(pop.loc[pop["dry_run"], "day"].unique()),
        "provenance": {
            "column_present": inp["provenance_column_present"],
            "races_by_source": {
                k: int(v)
                for k, v in pop.drop_duplicates("race_id")["odds_observed_source"]
                .value_counts()
                .items()
            },
            "note": "quote = every row's logic_version carries oddsobs=quote (A3-era compute with the "
            "race's win-quote provenance); proxy = oddsobs=proxy (fallback to race_horses.updated_at) or "
            "no token (pre-A3 compute). Days with any proxy race are dry_run. races.win_odds_observed_at "
            "is not used for this (overwritten by the final-odds write once results land).",
        },
        "n_races": int(pop["race_id"].nunique()),
        "n_days": len(days),
        "days": days,
        "n_rows": int(len(pop)),
        "n_won": int(pop["won"].sum()),
        "n_races_with_official_payout": int(
            pop.drop_duplicates("race_id")["has_official_payout"].sum()
        ),
        "exclusions": {k: {"n": len(v), "race_ids": v} for k, v in excl.items()},
        "inputs": {
            "ens15": ens[2],
            "judged": judged[2],
            "coefficients_sha256": C.sha256_file(pathlib.Path(args.coefficients)),
            "generator_sha256": C.sha256_file(C.GENERATOR_JSON)
            if C.GENERATOR_JSON.exists()
            else None,
            "protocol_sha256": proto["protocol_sha256"],
            "db_url_host": args.db_url.split("@")[-1],
            "feature_builder": "horseracing_training.market_ev.load_rows/build_features (as-of) + "
            "r02_drift.market_block on odds_used; weather/going = current DB values (D9)",
        },
        "arms_scored": list(arms),
        "arm_notes": notes,
        "ens15_parity_vs_stored_win_prob": parity,
        "evaluation_all_days": ev_all,
        "evaluation_eligible_days": ev_elig,
        "verdict": verdict_block(
            ev_all, ev_elig, proto["protocol"],
            diagnostic_arms={"judged"} if judged[2].get("diagnostic_only") else set(),
        ),
        "disclaimers": list(C.DISCLAIMERS),
        "elapsed_s": round(time.time() - t0, 1),
    }
    write_result(out_path, res)
    for name, a in ev_all["arms"].items():
        o = a["overall"]
        pr = a.get("paired_vs_ens15", {}).get("overall", {})
        d = pr.get("nll_J", {})
        print(
            f"{name:>7}: nll_J={o['nll_J']:.5f} wnll_J={o['winner_nll_J']:.4f} ev>1 n={o['ev_gt1']['n']} "
            f"ratio={o['ev_gt1']['calib_ratio']} ev>1.2 n={o['ev_gt12']['n']} roi={o['ev_gt12']['roi_official']}"
            + (
                f" | dNLL {d.get('point'):+.5f} [{d.get('ci_low'):+.5f},{d.get('ci_high'):+.5f}]"
                if d
                else ""
            )
        )
    print(f"verdict={res['verdict']['verdict']} dry_run={res['dry_run']} → {out_path}")
    for line in C.DISCLAIMERS:  # FR-011: on every output, stdout included
        print(line)
    return 0


def ens15_parity(feat: pd.DataFrame, pop: pd.DataFrame, p_ens: np.ndarray) -> dict:
    """再計算した ens15 の p̂_J と保存済み win_prob の一致度(差は D9 の天候・馬場の現在値などに限られるはず)。"""
    if "stored_win_prob" not in pop.columns:
        return {"available": False}
    d = feat[["race_id", "horse_id"]].merge(
        pop[["race_id", "horse_id", "stored_win_prob"]], on=["race_id", "horse_id"], how="left"
    )
    st = pd.to_numeric(d["stored_win_prob"], errors="coerce").to_numpy(float)
    diff = np.abs(p_ens - st)
    ok = np.isfinite(diff)
    return {
        "available": True,
        "n": int(ok.sum()),
        "max_abs_diff": float(np.nanmax(diff)) if ok.any() else None,
        "median_abs_diff": float(np.nanmedian(diff)) if ok.any() else None,
        "n_within_1e-6": int((diff[ok] < 1e-6).sum()),
        "note": "stored win_prob was computed on the same odds_used; residual differences come from as-of inputs "
        "that moved since compute (weather/going current values, D9)",
    }


def load_judged_partial(judged_dir, base_features, seeds):
    spec, boosters, info = load_judged(judged_dir, base_features, seeds)
    if spec is None:
        import lightgbm as lgb

        found = [s for s in seeds if (judged_dir / f"seed_{s:02d}" / "model_2026.txt").exists()]
        if not found:
            return None, [], info
        spec = C.read_json(judged_dir / f"seed_{found[0]:02d}" / "model.spec.json")
        boosters = [
            lgb.Booster(model_file=str(judged_dir / f"seed_{s:02d}" / "model_2026.txt"))
            for s in found
        ]
        info = dict(info, seeds_found=found, partial=True, diagnostic_only=True)
    return spec, boosters, info


if __name__ == "__main__":
    sys.exit(main())
