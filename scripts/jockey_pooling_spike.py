"""騎手の加法的部分プーリング切片を raw score へ外部加算する(スパイク)。

なぜこの形しか残っていないか
----------------------------
`screen_architecture.py` の分割オラクルが、本番忠実な arm E の held-out OOF 予測の上に
**加法的な騎手切片**で winner NLL −0.0039(6 分割で −0.0028〜−0.0047)の残差構造を検出した。
機構: production の `jockey_id` はカテゴリでなく連続値 = TE が識別子を勝率スカラーに潰しており、
モデルには騎手個体の自由度が 1 次元しか無い。

`jockey_cat_spike.py` が安い経路(生の識別子を真のカテゴリ列として追加)を潰した:
**B − A = +0.013492 CI[+0.007394,+0.018894] と有意に悪化**。665 水準に対し LightGBM の
貪欲なカテゴリ分割は縮約の原理を持たない。オラクルの λ 感度と整合する(**罰則なしの切片は
+0.0085 と悪化し、λ≈10〜100 で初めて −0.0039**)。

→ 騎手信号は実在するが **強い縮約を通してしか取り出せない**。残るのは木の外側で
加法切片を持つ形だけ。split 予算を食わず、FEATURE_VERSION も動かない。

設計(codex の設計レビューに従う)
--------------------------------
- `b` は **strict-past の内側 OOF 行**(arm E が校正に使うのと同じ行)から推定する。
  全期間 booster の in-sample raw score を使うと自分のラベルで自分を説明してしまう
- 交互最適化はしない。booster を offset に固定して 1 回だけ推定する
- `b` は **GBM の入力列ではなく raw score への外部加算**
- 縮約は **経験ベイズ**: 各騎手のスコア検定量から τ̂² を method-of-moments で出し λ=1/τ̂²。
  **評価窓を一切見ない**(グリッドを held-out で選ばない)
- 識別のため露出加重で中心化する(レース定数は softmax で消えるので数値には効かないが、
  b の解釈と監査のため)
- **時変にしない**(初回)。as-of の騎手 6 列が時間変化を既に担当しており、
  半減期 730 日の時間重みは winner NLL +0.0058 で有意に悪化した実績がある
- **isotonic は調整後スコアの上で fit し直す**(校正器の定義域を実際に serve する
  ベクトルに合わせる)

アーム
------
  A: 現行 arm E
  B: A + 騎手の部分プーリング切片(外部加算 + 調整後スコアで isotonic 再 fit)

判定規則(実行前に固定)
----------------------
  Δ <= -0.003  → 生存。別窓で新規に事前登録して確認へ
  Δ >= -0.001  → 死。騎手軸を閉じる(オラクルが見た構造は本番手続きでは取れない)
  その間       → 曖昧

**SCREENING ONLY — can_adopt=false。** 2025-2026 窓は既に選択に使っているので、
最終判定には別窓と新規の gate-config 凍結が要る。

    cd training && uv run python ../scripts/jockey_pooling_spike.py
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import time

import numpy as np
import pandas as pd
from horseracing_db.session import create_db_engine
from horseracing_eval.bootstrap import race_day_cluster_bootstrap_ci_v1
from horseracing_eval.dataset import load_eval_races, population_masks
from horseracing_eval.foldfit import predict_over_folds
from scipy.optimize import minimize
from sqlalchemy.orm import Session

from horseracing_training.calib_split import (
    DEFAULT_CLIP,
    CalibSplitFactory,
    OofCalibratedPredictor,
    _started_all_outcomes,
    day_block_partition,
)
from horseracing_training.calibration import fit_calibrator
from horseracing_training.predictor import LightGBMPredictor, assemble_predictions
from horseracing_training.recipe import ModelRecipe

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
MIN_RIDES = 30            # これ未満の騎手は係数を持たない(= prior に完全縮約)
UNKNOWN = "__unknown__"


def _race_softmax(s: np.ndarray, rid: np.ndarray, nR: int) -> np.ndarray:
    m = np.full(nR, -np.inf)
    np.maximum.at(m, rid, s)
    e = np.exp(s - m[rid])
    den = np.zeros(nR)
    np.add.at(den, rid, e)
    return e / den[rid]


def estimate_b(lp, rid, nR, win, codes, K) -> tuple[np.ndarray, dict]:
    """offset を固定した winner NLL + ridge。λ は経験ベイズ(評価窓を見ない)。"""
    p0 = _race_softmax(lp, rid, nR)                       # b=0 でのモデル確率
    g = np.zeros(K)
    np.add.at(g, codes[win], 1.0)
    np.subtract.at(g, codes, p0)                          # スコア検定量
    v = np.zeros(K)
    np.add.at(v, codes, p0 * (1.0 - p0))                  # Fisher 情報(対角近似)
    ok = v > 0
    b1 = np.where(ok, g / np.where(ok, v, 1.0), 0.0)      # 一段 Newton
    num = float(np.sum(v[ok] * (b1[ok] ** 2 - 1.0 / v[ok])))
    tau2 = max(0.0, num / float(np.sum(v[ok]))) if ok.any() else 0.0
    lam = float("inf") if tau2 <= 0 else 1.0 / tau2

    if not np.isfinite(lam):
        return np.zeros(K), {"tau2": tau2, "lambda": None, "n_entities": int(K)}

    def fg(b):
        s = lp + b[codes]
        sm = _race_softmax(s, rid, nR)
        nll = -np.sum(np.log(np.maximum(sm[win], 1e-300))) / nR
        gr = np.zeros(K)
        np.add.at(gr, codes[win], -1.0)
        np.add.at(gr, codes, sm)
        return nll + 0.5 * lam * float(b @ b) / nR, gr / nR + lam * b / nR

    r = minimize(fg, np.zeros(K), jac=True, method="L-BFGS-B",
                 options={"maxiter": 2000, "ftol": 1e-14})
    b = r.x
    n_j = np.zeros(K)
    np.add.at(n_j, codes, 1.0)
    b = b - float(n_j @ b) / float(n_j.sum())             # 露出加重で中心化
    return b, {"tau2": tau2, "lambda": lam, "n_entities": int(K),
               "b_sd": float(np.std(b)), "b_absmax": float(np.max(np.abs(b))),
               "converged": bool(r.success)}


class JockeyPooledPredictor(OofCalibratedPredictor):
    """arm E + 騎手の加法切片(raw score への外部加算)。"""

    def _jockey_map(self) -> dict:
        if getattr(self, "_jmap", None) is None:
            f = self._base._ensure_data().frame
            self._jmap = dict(zip(zip(f["race_id"], f["horse_id"], strict=True),
                                  f["jockey_id"].astype(str), strict=True))
        return self._jmap

    def fit(self, train_races, *, num_threads=None):
        self._reset_calibration_state()
        self._jmap = None
        self.b_: dict[str, float] = {}
        self.b_info_: dict = {}
        self._base = self._make_base()
        self._base.fit(train_races)

        rid_s, hid_s, raw_s, lab_s = self._oof_rows_with_ids(train_races)
        if not raw_s:
            self.oof_info_.update(sufficient=False, reason="no_oof_rows")
            return self
        jm = self._jockey_map()
        jk = pd.Series([jm.get((r, h), UNKNOWN) for r, h in zip(rid_s, hid_s, strict=True)])
        n = jk.value_counts()
        jk = jk.where(jk.isin(set(n[n >= MIN_RIDES].index)) & (jk != UNKNOWN), UNKNOWN)
        codes, uniq = pd.factorize(jk, use_na_sentinel=False)
        keep = [u for u in uniq if u != UNKNOWN]
        # UNKNOWN は係数を持たない(prior に完全縮約) -> 列から外す
        col = {u: i for i, u in enumerate(keep)}
        idx = np.array([col.get(u, -1) for u in uniq])[codes]
        mask = idx >= 0

        rid, nR = pd.factorize(np.asarray(rid_s))[0], len(set(rid_s))
        lp = np.log(np.clip(np.asarray(raw_s, dtype=float), 1e-15, None))
        win = np.asarray(lab_s, dtype=int) == 1
        K = len(keep)
        # UNKNOWN 行は専用のダミー列(索引 K)へ集め、係数を 0 に固定して捨てる
        codes_full = np.where(mask, idx, K)
        b_all, info = estimate_b(lp, rid, nR, win, codes_full, K + 1)
        b_all[K] = 0.0
        self.b_ = {u: float(b_all[i]) for u, i in col.items()}
        self.b_info_ = {**info, "n_oof_rows": len(raw_s), "n_oof_races": nR,
                        "n_jockeys": K, "min_rides": MIN_RIDES}

        # 調整後スコアの上で isotonic を fit し直す(校正器の定義域を serve に合わせる)
        s = lp + b_all[codes_full]
        adj = _race_softmax(s, rid, nR)
        self.calibrator_ = fit_calibrator(adj, np.asarray(lab_s, dtype=int),
                                          method="isotonic", clip=DEFAULT_CLIP)
        self.n_oof_samples_ = len(adj)
        self.oof_info_.update(sufficient=True, reason="ok", n_oof_rows=len(adj),
                              n_oof_races=nR)
        return self

    def _oof_rows_with_ids(self, train_races):
        races = sorted(train_races, key=lambda r: (r.race_date, r.race_id))
        days = sorted({r.race_date for r in races})
        rids: list[str] = []
        hids: list[str] = []
        raw_out: list[float] = []
        labs: list[int] = []
        if len(days) < self.n_oof * 2:
            return rids, hids, raw_out, labs
        outcomes = _started_all_outcomes(self.session, [r.race_id for r in races])
        for earlier_days, block_days in day_block_partition(days, self.n_oof):
            eset, bset = set(earlier_days), set(block_days)
            earlier = [r for r in races if r.race_date in eset]
            block = [r for r in races if r.race_date in bset]
            if not earlier or not block:
                continue
            pred = self._make_base()
            pred.fit(earlier)
            for ctx in block:
                got = outcomes.get(ctx.race_id)
                if got is None:
                    continue
                n_result_rows, winners = got
                started = [h.horse_id for h in ctx.started_horses]
                if not started or n_result_rows < len(started) or not winners:
                    continue
                ids, raw = pred.raw_win_probs(ctx)
                if list(ids) != started or not np.isfinite(raw).all():
                    raise RuntimeError(f"prediction/started mismatch for {ctx.race_id}")
                for hid, sc in zip(ids, raw, strict=True):
                    rids.append(ctx.race_id)
                    hids.append(hid)
                    raw_out.append(float(sc))
                    labs.append(1 if hid in winners else 0)
        return rids, hids, raw_out, labs

    def predict_race(self, race):
        assert self._base is not None
        started_ids, raw = self._base.raw_win_probs(race)
        jm = self._jockey_map()
        b = np.array([self.b_.get(jm.get((race.race_id, hid), UNKNOWN), 0.0)
                      for hid in started_ids], dtype=float)
        s = np.log(np.clip(np.asarray(raw, dtype=float), 1e-15, None)) + b
        adj = _race_softmax(s, np.zeros(len(s), dtype=np.intp), 1)
        win_scores = (np.asarray(self.calibrator_.transform(adj), dtype=float)
                      if self.calibrator_ is not None else adj)
        return assemble_predictions(started_ids, win_scores)


class PooledFactory(CalibSplitFactory):
    def fit(self, train_races, *, num_threads=None):
        if self._shared is None:
            tmp = LightGBMPredictor(self.session, objective=self.recipe.objective,
                                    calibration="none", use_materialized=self.use_materialized,
                                    materialized_path=self.materialized_path,
                                    skip_fingerprint_verify=self.pin_snapshot)
            self._shared = tmp._ensure_data()
        if self._pred is None:
            self._pred = JockeyPooledPredictor(
                self.session, self.recipe, shared_data=self._shared,
                n_oof_blocks=self.n_oof_blocks, method="isotonic",
                require_sufficient=False)
        self._pred.fit(train_races)
        return self._pred


def diffs_by_day(valid, pa, pb) -> dict:
    out: dict[str, list[float]] = {}
    for er in valid:
        pop = population_masks(er)
        if not pop.eligible:
            continue
        a, b = pa[er.context.race_id].get(pop.winner_horse_id), \
            pb[er.context.race_id].get(pop.winner_horse_id)
        if a is None or b is None:
            continue
        f = lambda p: -math.log(min(max(float(p), 1e-15), 1 - 1e-15))  # noqa: E731
        out.setdefault(er.context.race_date.isoformat(), []).append(f(a.win) - f(b.win))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=900)
    ap.add_argument("--n-oof-blocks", type=int, default=3)
    ap.add_argument("--first-valid-year", type=int, default=2025)
    ap.add_argument("--to", default="2026-08-16")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--num-threads", type=int, default=4)
    ap.add_argument("--bootstrap-b", type=int, default=1000)
    ap.add_argument("--materialized-path", default="../artifacts/features.parquet")
    ap.add_argument("--json", dest="json_out", default="../out/jockey_pooling_spike.json")
    args = ap.parse_args()

    print("*** SCREENING ONLY — can_adopt=false ***")
    print("判定: Δ<=-0.003 生存 / Δ>=-0.001 死(騎手軸を閉じる) / その間 曖昧\n")

    engine = create_db_engine(DB)
    with Session(engine) as session:
        races = load_eval_races(session, end_date=datetime.date.fromisoformat(args.to))
        preds, valid, binfo = {}, None, None
        for name, factory_cls in (("A arm E", CalibSplitFactory), ("B +pooled b", PooledFactory)):
            recipe = ModelRecipe(objective="pl_topk", calibration="none", calib_frac=0.0,
                                 seed=args.seed, params=(("n_estimators", args.rounds),),
                                 label=f"jockey-pooling-spike:{name}")
            fac = factory_cls(session, recipe, n_oof_blocks=args.n_oof_blocks,
                              method="isotonic", require_sufficient=False,
                              use_materialized=True,
                              materialized_path=args.materialized_path, pin_snapshot=True)
            t0 = time.time()
            p, v = predict_over_folds(fac, races, first_valid_year=args.first_valid_year,
                                      num_threads=args.num_threads)
            preds[name], valid = p, v
            print(f"  {name:<14} {time.time()-t0:7.1f}s  valid={len(v):,} races")
            if factory_cls is PooledFactory:
                binfo = fac._pred.b_info_
                print(f"    λ={binfo.get('lambda')}  τ²={binfo.get('tau2'):.6g}  "
                      f"騎手 {binfo.get('n_jockeys')} 人  b の sd={binfo.get('b_sd'):.4f}  "
                      f"|b|max={binfo.get('b_absmax'):.4f}")

    nd = sum(1 for er in valid for hid, p in preds["A arm E"][er.context.race_id].items()
             if preds["B +pooled b"][er.context.race_id].get(hid) is not None
             and preds["B +pooled b"][er.context.race_id][hid].win != p.win)
    if nd == 0:
        raise RuntimeError("両アームの予測が完全一致 = b が効いていない(097 型の縮退)")
    ci = race_day_cluster_bootstrap_ci_v1(
        diffs_by_day(valid, preds["B +pooled b"], preds["A arm E"]),
        b=args.bootstrap_b, seed=20260827)
    print(f"\n  異なる予測 {nd:,} 頭")
    print(f"  B − A : diff={ci.point:+.6f}  CI[{ci.ci_low:+.6f}, {ci.ci_high:+.6f}]  "
          f"n_days={ci.n_days}")

    print("\n=== 判定(事前登録した規則) ===")
    if ci.point <= -0.003:
        v = "survives"
        print("  → 生存。別窓で新規に事前登録して確認へ")
    elif ci.point >= -0.001:
        v = "dead"
        print("  → 死。騎手軸を閉じる")
    else:
        v = "ambiguous"
        print("  → 曖昧")
    print("  注: 再学習ノイズの SD は fold 水準で 0.001816")

    with open(args.json_out, "w") as fh:
        json.dump({"screening_only": True, "can_adopt": False, "rounds": args.rounds,
                   "seed": args.seed, "n_oof_blocks": args.n_oof_blocks,
                   "first_valid_year": args.first_valid_year, "to": args.to,
                   "b_info": binfo, "n_differing_predictions": nd, "diff": ci.point,
                   "ci_low": ci.ci_low, "ci_high": ci.ci_high, "verdict": v}, fh, indent=2)
    print(f"  wrote {args.json_out}")


if __name__ == "__main__":
    main()
