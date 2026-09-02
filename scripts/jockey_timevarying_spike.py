"""騎手切片の時変 b スパイク — 騎手軸に残る最後の未測定仮説(スパイク・最終回)。

これまでの実測(scripts/jockey_pooling_spike.py の docstring と memory jockey-identity-residual):
  - 分割オラクル(b を 2025 年の直近データで fit): −0.0039
  - 静的 b(本番手続き=全史 OOF 平均):            −0.0020 CI[−0.0047,+0.0008] = 「曖昧」帯
  - 生カテゴリ列: +0.0135 有意悪化 / booster への時間重み(101): +0.0058 有意悪化

仮説: オラクルと静的 b の差(−0.0019)は **b の鮮度** — 騎手の実力は時間で変わるので
19 年平均の b は古い。101 の recency REJECT は 137 列の交互作用構造の話で、
280 パラメータのエンティティ効果(各騎手に直近騎乗が十分ある)とは regime が違う=別仮説。

アーム
------
  A: 現行 arm E
  B: 静的 b(jockey_pooling_spike の再現・同一 DB 状態での対照)
  C: 時変 b = b の推定に使う内側 OOF 行を「訓練末尾から WINDOW_DAYS=730 日」に限定
     (オラクルが直近だけで fit だったことの最直接の再現)。λ は window 内の経験ベイズ。
     window 内 MIN_RIDES 騎乗未満は係数なし(完全縮約)。W は単一値事前登録・グリッド禁止。

判定規則(実行前に固定)
----------------------
  C−A ≤ −0.003 → 生存。別窓で新規に事前登録して confirmatory へ
  それ以外      → 騎手軸を完全に閉じる(3 回目のスパイク。明確な生存だけが継続に値する)
  C−B は機構診断(鮮度効果の分離)であって判定に使わない。

**SCREENING ONLY — can_adopt=false。** 2025-2026 窓は選択に使用済み。
採用には別窓 + gate-config 凍結 + 再学習 seed 分散込み CI(実効バー 点 ≤−0.0031 かつ
total CI 上限<0)が要る。分割オラクルは実現可能効果を約 2 倍過大評価する実績があるので、
時変で全て回収できても −0.0039 が上限 = 本実装が届かない可能性は高い。それでも測って閉じる。

設計レビューの反映(codex unavailable: CLI 初期化エラー Operation not permitted ×2 →
代替セルフレビューを採否つきで反映):
- estimand は「**年 1 回更新の 730 日窓 b**」(fold 内で b は再推定されないので評価年後半は
  最大 1 年 stale = 本番の再学習サイクルの忠実な再現。連続 rolling ではない)
- λ は EB 推定後に **[10, 500] へクランプ**(短窓で τ̂²→0 による λ 暴走・少数支配による
  過小化の両防護。オラクルの頑健域 λ∈[10,100] と静的 spike の λ=67.7 を包含)
- **ラベルの二重使用を限界として明記**: b と isotonic は同じ内側 OOF ラベルを見る
  (cross-fit しない)。B(静的 spike)と同一手続きなので比較可能性は保たれ、評価窓は非汚染
- 閉鎖の主張範囲は「加法切片・730 日窓・30 騎乗ゲート・年次更新・isotonic 再 fit という
  **この設計族**」に限定(騎手軸の統計的全否定ではない)。C−B は機構診断(ただし window 化に
  伴い λ・対象騎手集合も変わるので純粋な鮮度効果ではない)
- fold 別の λ / 騎手数 / 被覆を診断として保存する

    cd training && nohup uv run python ../scripts/jockey_timevarying_spike.py \
        > ../out/jtv.log 2>&1 &
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
MIN_RIDES = 30            # window 内でこれ未満の騎手は係数を持たない(= prior に完全縮約)
WINDOW_DAYS = 730         # 事前登録の単一値。グリッド探索禁止
UNKNOWN = "__unknown__"


def _race_softmax(s: np.ndarray, rid: np.ndarray, nR: int) -> np.ndarray:
    m = np.full(nR, -np.inf)
    np.maximum.at(m, rid, s)
    e = np.exp(s - m[rid])
    den = np.zeros(nR)
    np.add.at(den, rid, e)
    return e / den[rid]


def estimate_b(lp, rid, nR, win, codes, K) -> tuple[np.ndarray, dict]:
    """offset を固定した winner NLL + ridge。λ は経験ベイズ(評価窓を見ない)。

    jockey_pooling_spike.estimate_b と同一(対照 B と推定手続きをバイト単位で揃えるため
    コピー。C との差は「どの行を入れるか」だけに限定する)。
    """
    p0 = _race_softmax(lp, rid, nR)
    g = np.zeros(K)
    np.add.at(g, codes[win], 1.0)
    np.subtract.at(g, codes, p0)
    v = np.zeros(K)
    np.add.at(v, codes, p0 * (1.0 - p0))
    ok = v > 0
    b1 = np.where(ok, g / np.where(ok, v, 1.0), 0.0)
    num = float(np.sum(v[ok] * (b1[ok] ** 2 - 1.0 / v[ok])))
    tau2 = max(0.0, num / float(np.sum(v[ok]))) if ok.any() else 0.0
    lam_raw = float("inf") if tau2 <= 0 else 1.0 / tau2
    # レビュー反映: 短窓の EB 暴走防護。τ̂²→0(λ→∞)は上限へ、少数支配の過小 λ は下限へ。
    # 事前固定 [10, 500](オラクル頑健域と静的 spike の λ=67.7 を包含)。
    lam = min(max(lam_raw, 10.0), 500.0)

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
    b = b - float(n_j @ b) / float(n_j.sum())
    return b, {"tau2": tau2, "lambda": lam, "lambda_raw": lam_raw if np.isfinite(lam_raw) else None,
               "n_entities": int(K),
               "b_sd": float(np.std(b)), "b_absmax": float(np.max(np.abs(b))),
               "converged": bool(r.success)}


class JockeyPooledPredictor(OofCalibratedPredictor):
    """arm E + 騎手の加法切片。window_days=None なら静的 b(B)、指定で時変 b(C)。"""

    window_days: int | None = None

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

        rid_s, hid_s, raw_s, lab_s, date_s = self._oof_rows_with_ids(train_races)
        if not raw_s:
            self.oof_info_.update(sufficient=False, reason="no_oof_rows")
            return self

        # isotonic は常に全 OOF 行で fit する(B/C とも A と同じ校正母集団)。
        # window は b の推定行だけを絞る — 差が「b の鮮度」以外から来ないようにする。
        jm = self._jockey_map()
        all_dates = np.asarray(date_s)
        rid_all, nR_all = pd.factorize(np.asarray(rid_s))[0], len(set(rid_s))
        lp_all = np.log(np.clip(np.asarray(raw_s, dtype=float), 1e-15, None))
        win_all = np.asarray(lab_s, dtype=int) == 1

        if self.window_days is None:
            sel = np.ones(len(raw_s), dtype=bool)
        else:
            train_end = max(r.race_date for r in train_races)
            cut = train_end - datetime.timedelta(days=self.window_days)
            sel = all_dates >= cut

        jk = pd.Series([jm.get((r, h), UNKNOWN)
                        for r, h, keep in zip(rid_s, hid_s, sel, strict=True) if keep])
        n = jk.value_counts()
        jk_all = pd.Series([jm.get((r, h), UNKNOWN) for r, h in zip(rid_s, hid_s, strict=True)])
        eligible = set(n[n >= MIN_RIDES].index) - {UNKNOWN}
        keep = sorted(eligible)
        col = {u: i for i, u in enumerate(keep)}
        K = len(keep)

        # b は window 内の行だけから推定(レース集合も window 内に絞って factorize し直す)
        w_rid = np.asarray(rid_s)[sel]
        rid_w, nR_w = pd.factorize(w_rid)[0], len(set(w_rid))
        lp_w = lp_all[sel]
        win_w = win_all[sel]
        codes_w = np.array([col.get(u, K) for u in jk_all[sel]])
        b_all, info = estimate_b(lp_w, rid_w, nR_w, win_w, codes_w, K + 1)
        b_all[K] = 0.0
        self.b_ = {u: float(b_all[i]) for u, i in col.items()}
        self.b_info_ = {**info, "n_oof_rows_total": len(raw_s),
                        "n_oof_rows_window": int(sel.sum()),
                        "n_oof_races_window": nR_w, "n_jockeys": K,
                        "min_rides": MIN_RIDES, "window_days": self.window_days,
                        "train_end": max(r.race_date for r in train_races).isoformat()}
        # fold 別診断(レビュー反映): 同一インスタンスが fold ごとに再 fit されるので蓄積する
        self.b_info_history_ = [*getattr(self, "b_info_history_", []), self.b_info_]

        # 調整後スコアの上で isotonic を fit し直す(全 OOF 行=A/B/C で同一母集団)
        codes_all = np.array([col.get(u, K) for u in jk_all])
        s = lp_all + b_all[codes_all]
        adj = _race_softmax(s, rid_all, nR_all)
        self.calibrator_ = fit_calibrator(adj, np.asarray(lab_s, dtype=int),
                                          method="isotonic", clip=DEFAULT_CLIP)
        self.n_oof_samples_ = len(adj)
        self.oof_info_.update(sufficient=True, reason="ok", n_oof_rows=len(adj),
                              n_oof_races=nR_all)
        return self

    def _oof_rows_with_ids(self, train_races):
        races = sorted(train_races, key=lambda r: (r.race_date, r.race_id))
        days = sorted({r.race_date for r in races})
        rids: list[str] = []
        hids: list[str] = []
        raw_out: list[float] = []
        labs: list[int] = []
        dates: list[datetime.date] = []
        if len(days) < self.n_oof * 2:
            return rids, hids, raw_out, labs, dates
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
                    dates.append(ctx.race_date)
        return rids, hids, raw_out, labs, dates

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


def make_factory(window_days: int | None):
    class _F(CalibSplitFactory):
        def fit(self, train_races, *, num_threads=None):
            if self._shared is None:
                tmp = LightGBMPredictor(self.session, objective=self.recipe.objective,
                                        calibration="none",
                                        use_materialized=self.use_materialized,
                                        materialized_path=self.materialized_path,
                                        skip_fingerprint_verify=self.pin_snapshot)
                self._shared = tmp._ensure_data()
            if self._pred is None:
                self._pred = JockeyPooledPredictor(
                    self.session, self.recipe, shared_data=self._shared,
                    n_oof_blocks=self.n_oof_blocks, method="isotonic",
                    require_sufficient=False)
                self._pred.window_days = window_days
            self._pred.fit(train_races)
            return self._pred
    return _F


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
    ap.add_argument("--json", dest="json_out", default="../out/jockey_timevarying_spike.json")
    args = ap.parse_args()

    print("*** SCREENING ONLY — can_adopt=false ***")
    print(f"判定: C−A ≤ −0.003 生存 / それ以外 騎手軸を閉じる。W={WINDOW_DAYS}d 固定\n")

    arms = (("A arm E", None, CalibSplitFactory),
            ("B static b", None, make_factory(None)),
            ("C window b", WINDOW_DAYS, make_factory(WINDOW_DAYS)))

    engine = create_db_engine(DB)
    with Session(engine) as session:
        # DB には pre-2007 レース(データ量実験の残存・71,549 件)があるが特徴プールは
        # FEATURE_POOL_START(2007-01-01)から。start を揃えないと OOF の earlier 分割が
        # 特徴行ゼロのレースだけになり "no training rows" で落ちる(2026-09-02 実測)。
        from horseracing_db.validation import FEATURE_POOL_START
        races = load_eval_races(session, start_date=FEATURE_POOL_START,
                                end_date=datetime.date.fromisoformat(args.to))
        preds, valid, binfos = {}, None, {}
        for name, _w, factory_cls in arms:
            recipe = ModelRecipe(objective="pl_topk", calibration="none", calib_frac=0.0,
                                 seed=args.seed, params=(("n_estimators", args.rounds),),
                                 label=f"jockey-tv-spike:{name}")
            fac = factory_cls(session, recipe, n_oof_blocks=args.n_oof_blocks,
                              method="isotonic", require_sufficient=False,
                              use_materialized=True,
                              materialized_path=args.materialized_path, pin_snapshot=True)
            t0 = time.time()
            p, v = predict_over_folds(fac, races, first_valid_year=args.first_valid_year,
                                      num_threads=args.num_threads)
            preds[name], valid = p, v
            print(f"  {name:<12} {time.time()-t0:7.1f}s  valid={len(v):,} races", flush=True)
            if factory_cls is not CalibSplitFactory:
                binfos[name] = {"last": fac._pred.b_info_,
                                "by_fold": fac._pred.b_info_history_}
                for bi in binfos[name]["by_fold"]:
                    print(f"    [~{bi.get('train_end')}] λ={bi.get('lambda'):.1f} "
                          f"(raw={bi.get('lambda_raw')})  τ²={bi.get('tau2'):.6g}  騎手 "
                          f"{bi.get('n_jockeys')} 人  b sd={bi.get('b_sd'):.4f}  "
                          f"rows(window)={bi.get('n_oof_rows_window'):,}/"
                          f"{bi.get('n_oof_rows_total'):,}", flush=True)

    results = {}
    for tag, x, y in (("C-A", "C window b", "A arm E"),
                      ("B-A", "B static b", "A arm E"),
                      ("C-B", "C window b", "B static b")):
        nd = sum(1 for er in valid for hid, p in preds[y][er.context.race_id].items()
                 if preds[x][er.context.race_id].get(hid) is not None
                 and preds[x][er.context.race_id][hid].win != p.win)
        if nd == 0 and tag != "C-B":
            raise RuntimeError(f"{tag}: 両アームの予測が完全一致 = b が効いていない(097 型の縮退)")
        ci = race_day_cluster_bootstrap_ci_v1(diffs_by_day(valid, preds[x], preds[y]),
                                              b=args.bootstrap_b, seed=20260902)
        results[tag] = {"diff": ci.point, "ci_low": ci.ci_low, "ci_high": ci.ci_high,
                        "n_days": ci.n_days, "n_differing": nd}
        print(f"  {tag}: diff={ci.point:+.6f}  CI[{ci.ci_low:+.6f}, {ci.ci_high:+.6f}]  "
              f"差分 {nd:,} 頭")

    print("\n=== 判定(事前登録した規則: C−A ≤ −0.003 のみ生存) ===")
    verdict = "survives" if results["C-A"]["diff"] <= -0.003 else "axis_closed"
    print(f"  → {verdict}")
    print("  注: 再学習ノイズの SD は fold 水準で 0.001816。C−B は機構診断で判定に不使用")

    with open(args.json_out, "w") as fh:
        json.dump({"screening_only": True, "can_adopt": False, "rounds": args.rounds,
                   "seed": args.seed, "n_oof_blocks": args.n_oof_blocks,
                   "first_valid_year": args.first_valid_year, "to": args.to,
                   "window_days": WINDOW_DAYS, "min_rides": MIN_RIDES,
                   "b_info": binfos, "results": results, "verdict": verdict}, fh, indent=2)
    print(f"  wrote {args.json_out}")


if __name__ == "__main__":
    main()
