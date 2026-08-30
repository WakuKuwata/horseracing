"""騎手 ID を「真のカテゴリ列」として足すと、再学習した GBM が残差を捕まえるか。

背景
----
`screen_architecture.py` が本番忠実な arm E の held-out OOF 予測の上で、**加法的な騎手切片**に
winner NLL −0.0039(6 分割で −0.0028〜−0.0047)の残差構造を検出した。

機構は production booster を開いて確認済み: **`jockey_id` はカテゴリではなく連続値
[0.0059, 0.3164]** = OOF target encoding が識別子を勝率スカラーに置き換えている。
モデルには騎手個体の自由度が 1 次元しか無く、同じエンコード値の 2 人を原理的に区別できない。

残る問い
--------
オラクルが見たのは「凍結モデルの上に事後の加法調整を足せば回収できる分」。
**再学習した GBM が自力で捕まえられるか**は別問題で、それがこのスパイクの唯一の問い。

アーム
------
  A: 現行 arm E(jockey_id は TE で潰される)
  B: A + `jockey_cat`(生の jockey_id を **TE しない真のカテゴリ列**として別に追加)

TE 対象列は `cat_for_model` から外れる実装なので、`jockey_id`(TE 数値)と
`jockey_cat`(カテゴリ)が同時に入力に立つ。

**FEATURE_VERSION は動かさない。** 列はスクリーニング用にその場で共有行列へ足すだけで、
registry にも materialize にも触らない。

判定規則(実行前に固定)
----------------------
  Δ <= -0.003  → 木は自力で捕まえられる。列追加として事前登録判定へ
  Δ >= -0.001  → 木には捕まえられない(split 予算では表現できない)。
                 部分プーリング(raw score への外部加算・FEATURE_VERSION 不要)へ進む
  その間       → 曖昧。両方を並べて測り直す

**SCREENING ONLY — can_adopt=false。** 勝ちが出ても別窓で新規に事前登録して確認する
(094 の容量スパイクと同じ運用)。

    cd training && uv run python ../scripts/jockey_cat_spike.py
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import math
import time

from horseracing_db.session import create_db_engine
from horseracing_eval.bootstrap import race_day_cluster_bootstrap_ci_v1
from horseracing_eval.dataset import load_eval_races, population_masks
from horseracing_eval.foldfit import predict_over_folds
from sqlalchemy.orm import Session

from horseracing_training.calib_split import CalibSplitFactory
from horseracing_training.predictor import LightGBMPredictor
from horseracing_training.recipe import ModelRecipe

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
NEW_COL = "jockey_cat"


def augment(shared):
    """共有行列に `jockey_cat` を足した **別インスタンス** を返す(A 側を汚さない)。"""
    if NEW_COL in shared.feature_cols:
        raise RuntimeError(f"{NEW_COL} が既に入力にある")
    frame = shared.frame.copy(deep=False)
    frame[NEW_COL] = frame["jockey_id"].astype("category")
    return dataclasses.replace(
        shared, frame=frame,
        feature_cols=[*shared.feature_cols, NEW_COL],
        categorical_cols=[*shared.categorical_cols, NEW_COL],
    )


def diffs_by_day(valid, pa, pb) -> dict:
    out: dict[str, list[float]] = {}
    for er in valid:
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


def n_differing(valid, pa, pb) -> int:
    n = 0
    for er in valid:
        for hid, p in pa[er.context.race_id].items():
            q = pb[er.context.race_id].get(hid)
            if q is not None and p.win != q.win:
                n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=900)
    ap.add_argument("--n-oof-blocks", type=int, default=3)
    ap.add_argument("--first-valid-year", type=int, default=2025)
    ap.add_argument("--to", default="2026-08-16")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--num-threads", type=int, default=1)
    ap.add_argument("--bootstrap-b", type=int, default=1000)
    ap.add_argument("--materialized-path", default="../artifacts/features.parquet")
    ap.add_argument("--json", dest="json_out", default="../out/jockey_cat_spike.json")
    args = ap.parse_args()

    print("*** SCREENING ONLY — can_adopt=false ***")
    print(__doc__.split("判定規則(実行前に固定)")[1].split("**SCREENING")[0].strip(" -\n"))
    print()

    engine = create_db_engine(DB)
    with Session(engine) as session:
        races = load_eval_races(session, end_date=datetime.date.fromisoformat(args.to))
        tmp = LightGBMPredictor(session, objective="pl_topk", calibration="none",
                                use_materialized=True,
                                materialized_path=args.materialized_path,
                                skip_fingerprint_verify=True)
        base = tmp._ensure_data()
        aug = augment(base)
        print(f"A: {len(base.feature_cols)} 列 (categorical {len(base.categorical_cols)})")
        print(f"B: {len(aug.feature_cols)} 列 (categorical {len(aug.categorical_cols)}) "
              f"= A + {NEW_COL}")
        assert len(aug.feature_cols) == len(base.feature_cols) + 1
        assert NEW_COL in aug.categorical_cols
        assert NEW_COL not in base.frame.columns, "A 側の行列を汚している"
        print(f"  {NEW_COL} の水準数 = {aug.frame[NEW_COL].nunique():,}\n")

        preds = {}
        valid = None
        for name, shared in (("A 現行", base), ("B +jockey_cat", aug)):
            recipe = ModelRecipe(objective="pl_topk", calibration="none", calib_frac=0.0,
                                 seed=args.seed, params=(("n_estimators", args.rounds),),
                                 label=f"jockey-cat-spike:{name}")
            fac = CalibSplitFactory(session, recipe, n_oof_blocks=args.n_oof_blocks,
                                    method="isotonic", require_sufficient=False,
                                    use_materialized=True,
                                    materialized_path=args.materialized_path,
                                    pin_snapshot=True)
            fac._shared = shared   # 共有行列を直接与える(両アームで唯一の差)
            t0 = time.time()
            p, v = predict_over_folds(fac, races, first_valid_year=args.first_valid_year,
                                      num_threads=args.num_threads)
            preds[name], valid = p, v
            print(f"  {name:<16} {time.time()-t0:7.1f}s  valid={len(v):,} races")

    nd = n_differing(valid, preds["B +jockey_cat"], preds["A 現行"])
    if nd == 0:
        raise RuntimeError("両アームの予測が完全一致 = 列が効いていない(097 型の縮退)")
    ci = race_day_cluster_bootstrap_ci_v1(
        diffs_by_day(valid, preds["B +jockey_cat"], preds["A 現行"]),
        b=args.bootstrap_b, seed=20260827)
    print(f"\n  異なる予測 {nd:,} 頭")
    print(f"  B − A : diff={ci.point:+.6f}  CI[{ci.ci_low:+.6f}, {ci.ci_high:+.6f}]  "
          f"n_days={ci.n_days}")

    print("\n=== 判定(事前登録した規則) ===")
    if ci.point <= -0.003:
        v = "tree_captures"
        print("  → 木は自力で捕まえられる。列追加として事前登録判定へ")
    elif ci.point >= -0.001:
        v = "needs_partial_pooling"
        print("  → 木には捕まえられない。部分プーリング(raw score への外部加算)へ進む")
    else:
        v = "ambiguous"
        print("  → 曖昧。両方を並べて測り直す")
    print("  注: 参考として再学習ノイズの SD は fold 水準で 0.001816")

    with open(args.json_out, "w") as fh:
        json.dump({"screening_only": True, "can_adopt": False, "rounds": args.rounds,
                   "seed": args.seed, "n_oof_blocks": args.n_oof_blocks,
                   "first_valid_year": args.first_valid_year, "to": args.to,
                   "n_differing_predictions": nd, "diff": ci.point,
                   "ci_low": ci.ci_low, "ci_high": ci.ci_high, "n_days": ci.n_days,
                   "verdict": v}, fh, indent=2)
    print(f"  wrote {args.json_out}")


if __name__ == "__main__":
    main()
