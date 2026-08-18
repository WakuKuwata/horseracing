"""LightGBM の容量(rounds / leaves / lr / colsample)の粗いスクリーニング。

**SCREENING ONLY — can_adopt=false。** ここで勝った設定をそのまま採用してはいけない。
評価窓を見て設定を選ぶのは選択リークそのものなので、勝ちが出た場合は**別窓での新規の
事前登録**で確認する。この探索の役割は「容量に arm E 級(0.01 超)の余地があるか」だけ。

なぜ今やるか: 現 active のハイパーパラメータは `DEFAULT_PARAMS` と 1 文字も違わない
(n_estimators=300 / num_leaves=31 / lr=0.05 / subsample=1.0 / colsample=1.0)。これは
objective が `binary` だった頃の既定で、その後 objective は pl_topk に、特徴は 54→138 列に、
booster は arm E で全期間学習に変わったが、**容量だけが当時のまま**。しかも pl_topk では
HPO が NotImplementedError で、本番の目的関数で一度も調整されたことがない。

なぜ粗いグリッドか: 同日の実測で **再学習ノイズの SD は fold 水準で 0.0018**
([[adoption-gate-underpowered]])。0.002 級の差は測れないので、細かく刻む意味がない。
大きく振って arm E 級が出るかだけを見る。

判定規則(実行前に固定):
  最良の変種の |diff| が
    >= 0.006  → 容量は本物のレバー。別窓で新規に事前登録して確認する
    <= 0.002  → 容量は死んでいる。この軸を閉じる
    その間    → 曖昧。fold を増やして測り直すか、閉じるかを別途判断する

全設定で seed は固定する(設定間の差が seed 引きにならないように)。
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import time

from horseracing_db.session import create_db_engine
from horseracing_eval.bootstrap import race_day_cluster_bootstrap_ci_v1
from horseracing_eval.dataset import load_eval_races, population_masks
from horseracing_eval.foldfit import predict_over_folds
from sqlalchemy.orm import Session

from horseracing_training.predictor import LightGBMPredictor
from horseracing_training.recipe import ModelRecipe
from horseracing_training.win_model import DEFAULT_PARAMS

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"

#: 事前登録するグリッド。既定から**大きく**振る。
GRID = {
    "base(300/31/0.05)": {},
    "rounds x3 (900)": {"n_estimators": 900},
    "leaves x4 (127)": {"num_leaves": 127},
    "rounds x3 + leaves x4 + lr/1.7": {"n_estimators": 900, "num_leaves": 127,
                                       "learning_rate": 0.03},
    "colsample 0.7": {"colsample_bytree": 0.7},
}


class ParamFactory:
    """RecipeFactory と同じ配線で、params だけ差し替える(screening 専用)。"""

    def __init__(self, session, recipe: ModelRecipe, params: dict, *, materialized_path: str):
        self.session, self.recipe, self.params = session, recipe, params
        self.materialized_path = materialized_path
        self._pred = None

    recipe_meta = property(lambda self: self.recipe.meta())
    recipe_hash = property(lambda self: self.recipe.recipe_hash())

    def fit(self, train_races, *, num_threads=None):
        if self._pred is None:
            self._pred = LightGBMPredictor(
                self.session, seed=self.recipe.seed, calibration=self.recipe.calibration,
                calib_frac=self.recipe.calib_frac,
                target_encode_cols=self.recipe.target_encode_cols,
                te_smoothing=self.recipe.te_smoothing, objective=self.recipe.objective,
                calibration_split_unit=self.recipe.calibration_split_unit,
                params={**DEFAULT_PARAMS, **self.params},
                use_materialized=True, materialized_path=self.materialized_path,
                skip_fingerprint_verify=True,
            )
        self._pred.fit(train_races)
        return self._pred


def diffs_by_day(valid_races, pa, pb) -> dict:
    out: dict[str, list[float]] = {}
    for er in valid_races:
        pop = population_masks(er)
        if not pop.eligible:
            continue
        a = pa[er.context.race_id].get(pop.winner_horse_id)
        b = pb[er.context.race_id].get(pop.winner_horse_id)
        if a is None or b is None:
            continue
        f = lambda p: -math.log(min(max(float(p), 1e-15), 1 - 1e-15))  # noqa: E731
        out.setdefault(er.context.race_date.isoformat(), []).append(f(a.win) - f(b.win))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--first-valid-year", type=int, default=2025)
    ap.add_argument("--to", default="2026-08-16")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--bootstrap-b", type=int, default=1000)
    ap.add_argument("--materialized-path", default="../artifacts/features.parquet")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args()

    print("*** SCREENING ONLY — can_adopt=false。勝ちは別窓で新規に事前登録して確認する ***\n")
    engine = create_db_engine(DB)
    with Session(engine) as session:
        races = load_eval_races(session, end_date=datetime.date.fromisoformat(args.to))
        recipe = ModelRecipe(objective="pl_topk", calibration="isotonic", calib_frac=0.3,
                             seed=args.seed, label="capacity-screen")
        preds, valid = {}, None
        for name, over in GRID.items():
            fac = ParamFactory(session, recipe, over,
                               materialized_path=args.materialized_path)
            t0 = time.time()
            p, v = predict_over_folds(fac, races, first_valid_year=args.first_valid_year,
                                      num_threads=1)
            preds[name], valid = p, v
            print(f"  {name:<32} {time.time()-t0:7.1f}s  valid={len(v):,} races")

    base = preds["base(300/31/0.05)"]
    rows = []
    print()
    for name in GRID:
        if name.startswith("base"):
            continue
        ci = race_day_cluster_bootstrap_ci_v1(diffs_by_day(valid, preds[name], base),
                                              b=args.bootstrap_b, seed=20260818)
        rows.append({"config": name, "diff": ci.point, "ci_low": ci.ci_low,
                     "ci_high": ci.ci_high, "n_days": ci.n_days})
        print(f"  {name:<32} diff={ci.point:+.6f} CI[{ci.ci_low:+.6f}, {ci.ci_high:+.6f}]")

    best = min(rows, key=lambda r: r["diff"])
    mag = abs(best["diff"])
    print("\n=== 判定(事前登録した規則) ===")
    print(f"  最良の変種: {best['config']}  diff={best['diff']:+.6f}")
    print("  参考: 同日実測の再学習ノイズ SD = 0.001816 (fold 水準)")
    if mag >= 0.006:
        print("  → 容量は本物のレバー。別窓で新規に事前登録して確認する")
    elif mag <= 0.002:
        print("  → 容量は死んでいる。この軸を閉じる")
    else:
        print("  → 曖昧。fold を増やすか閉じるかを別途判断する")

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump({"grid": {k: v for k, v in GRID.items()}, "seed": args.seed,
                       "first_valid_year": args.first_valid_year, "results": rows,
                       "screening_only": True, "can_adopt": False}, fh, indent=2)
        print(f"  wrote {args.json_out}")


if __name__ == "__main__":
    main()
