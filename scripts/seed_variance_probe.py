"""再学習 seed だけを変えたときの、測定される差の散らばり(帰無実験)。

なぜ: 採用ゲートの CI は**開催日クラスタのブートストラップ**で作られる。これはレースの
標本抽出による不確実性は捉えるが、**同じデータを同じレシピで学習し直したときのばらつき
(seed ノイズ)は入っていない**。もし seed だけで測定差が SE と同程度動くなら、
0.0005〜0.002 の帯は丸ごとノイズで、これまでの「有意」の読み方が変わる。

設計は 2 通りあり、**実運用に対応するのは rerun の方**である。

  rerun(既定・主測定): 実際の paired-eval は候補と現行に**同じ seed**(既定 42)を使う。
    そこで「同じ 2 アームの比較を、seed を変えて丸ごと再実行したら、報告される差がどれだけ
    動くか」を測る。これが「もう一度回したら別の数字が出るのか」への直接の答え。
  independent(参考): 同一レシピを別 seed 同士で比べる。両アームの seed が独立な場合の
    ノイズ床で、rerun の**上限**にあたる。

判定規則(実行前に固定):
  seed_sd / bootstrap_se が
    >= 0.5  → CI は不確実性を実質的に過小申告。過去の「有意」を読み直す必要がある
    <= 0.2  → seed ノイズは無視できる。CI はそのまま使える
    その間  → 部分的な過小申告。定量して記録する

注意: 1 fold で測るので、これは**その fold の差の seed ノイズ**である。多 fold 評価では
fold ごとの寄与がある程度打ち消し合うため、全窓の数字にそのまま当てはめてはいけない。
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import statistics
import time

from horseracing_db.session import create_db_engine
from horseracing_eval.bootstrap import race_day_cluster_bootstrap_ci_v1
from horseracing_eval.dataset import load_eval_races, population_masks
from horseracing_eval.foldfit import predict_over_folds
from sqlalchemy.orm import Session

from horseracing_training.recipe import ModelRecipe, RecipeFactory

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
CLIP = 1e-15


def _clip_nll(p: float) -> float:
    return -math.log(min(max(float(p), CLIP), 1.0 - CLIP))


def winner_diffs_by_day(valid_races, preds_a, preds_b) -> dict:
    """2 つの予測集合の、レース単位 winner NLL 差を開催日ごとに束ねる。"""
    out: dict[str, list[float]] = {}
    for er in valid_races:
        pop = population_masks(er)
        if not pop.eligible:
            continue
        rid, w = er.context.race_id, pop.winner_horse_id
        pa, pb = preds_a[rid].get(w), preds_b[rid].get(w)
        if pa is None or pb is None:
            continue
        out.setdefault(er.context.race_date.isoformat(), []).append(
            _clip_nll(pa.win) - _clip_nll(pb.win)
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("rerun", "independent"), default="rerun")
    ap.add_argument("--drop", default="pace_first3f",
                    help="rerun モードの対照。真の効果がほぼゼロの小さい群が望ましい")
    ap.add_argument("--seeds", default="42,7,101,2026,31337")
    ap.add_argument("--first-valid-year", type=int, default=2026)
    ap.add_argument("--to", default="2026-08-16")
    ap.add_argument("--spec-objective", default="pl_topk")
    ap.add_argument("--calibration", default="isotonic")
    ap.add_argument("--calib-frac", type=float, default=0.3)
    ap.add_argument("--bootstrap-b", type=int, default=1000)
    ap.add_argument("--materialized-path", default="../artifacts/features.parquet")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args()

    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    engine = create_db_engine(DB)
    with Session(engine) as session:
        import datetime
        races = load_eval_races(
            session, end_date=datetime.date.fromisoformat(args.to)
        )
        print(f"eval races: {len(races):,}  seeds={seeds}  "
              f"first_valid_year={args.first_valid_year}")

        preds_by_seed, valid_races = {}, None
        base_label = f"{args.spec_objective}:{args.calibration}:{args.calib_frac}"

        def _factory(recipe):
            return RecipeFactory(session, recipe, use_materialized=True,
                                 materialized_path=args.materialized_path, pin_snapshot=True)

        if args.mode == "rerun":
            import dataclasses

            from horseracing_training.cli import _recipe_from_spec
            base = _recipe_from_spec(base_label)
            cand = _recipe_from_spec(f"{base_label}:drop={args.drop}")
            print(f"contrast: drop={args.drop} ({len(cand.drop_features)} 列) vs 落とさない")
            for s_ in seeds:
                t0 = time.time()
                pa, v = predict_over_folds(
                    _factory(dataclasses.replace(cand, seed=s_)), races,
                    first_valid_year=args.first_valid_year, num_threads=1)
                pb, _ = predict_over_folds(
                    _factory(dataclasses.replace(base, seed=s_)), races,
                    first_valid_year=args.first_valid_year, num_threads=1)
                preds_by_seed[s_] = (pa, pb)
                valid_races = v
                print(f"  seed={s_:<6} 両アーム {time.time()-t0:6.1f}s  valid={len(v):,} races")
        else:
            for s_ in seeds:
                recipe = ModelRecipe(
                    objective=args.spec_objective, calibration=args.calibration,
                    calib_frac=args.calib_frac, seed=s_, label=f"{base_label}:seed={s_}")
                t0 = time.time()
                p, v = predict_over_folds(_factory(recipe), races,
                                          first_valid_year=args.first_valid_year, num_threads=1)
                preds_by_seed[s_] = p
                valid_races = v
                print(f"  seed={s_:<6} fit+predict {time.time()-t0:6.1f}s  valid={len(v):,} races")

    rows = []
    if args.mode == "rerun":
        # 各 seed で「同じ 2 アームの比較」を丸ごと 1 回やったことになる。
        for s_ in seeds:
            pa, pb = preds_by_seed[s_]
            by_day = winner_diffs_by_day(valid_races, pa, pb)
            ci = race_day_cluster_bootstrap_ci_v1(by_day, b=args.bootstrap_b, seed=20260818)
            se = (ci.ci_high - ci.ci_low) / 2 / 1.96 if ci.ci_low is not None else float("nan")
            rows.append({"seed": s_, "diff": ci.point, "ci_low": ci.ci_low,
                         "ci_high": ci.ci_high, "se": se, "n_days": ci.n_days})
            print(f"  seed {s_:>6}: diff={ci.point:+.6f} "
                  f"CI[{ci.ci_low:+.6f}, {ci.ci_high:+.6f}] se={se:.6f}")
    else:
        for a, b in itertools.combinations(seeds, 2):
            by_day = winner_diffs_by_day(valid_races, preds_by_seed[a], preds_by_seed[b])
            ci = race_day_cluster_bootstrap_ci_v1(by_day, b=args.bootstrap_b, seed=20260818)
            se = (ci.ci_high - ci.ci_low) / 2 / 1.96 if ci.ci_low is not None else float("nan")
            rows.append({"seed_a": a, "seed_b": b, "diff": ci.point, "ci_low": ci.ci_low,
                         "ci_high": ci.ci_high, "se": se, "n_days": ci.n_days})
            print(f"  seed {a:>6} vs {b:>6}: diff={ci.point:+.6f} "
                  f"CI[{ci.ci_low:+.6f}, {ci.ci_high:+.6f}] se={se:.6f}")

    diffs = [r["diff"] for r in rows]
    seed_sd = statistics.pstdev(diffs) if len(diffs) > 1 else float("nan")
    boot_se = statistics.mean(r["se"] for r in rows)
    print("\n=== 結果 ===")
    print(f"  観測数(mode={args.mode})  : {len(diffs)}")
    print("  真の効果               : mode により 0 または固定(seed に依らない)")
    print(f"  測定された差の範囲      : {min(diffs):+.6f} .. {max(diffs):+.6f}")
    print(f"  seed ノイズ SD         : {seed_sd:.6f}")
    print(f"  同 fold の bootstrap SE : {boot_se:.6f}")
    ratio = seed_sd / boot_se if boot_se else float("nan")
    print(f"  比 (seed_sd / boot_se)  : {ratio:.2f}")
    if ratio >= 0.5:
        print("  → CI は不確実性を実質的に過小申告している")
    elif ratio <= 0.2:
        print("  → seed ノイズは無視できる。CI はそのまま使える")
    else:
        print("  → 部分的な過小申告。定量して記録する")

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump({"seeds": seeds, "pairs": rows, "seed_sd": seed_sd,
                       "bootstrap_se": boot_se, "ratio": ratio}, fh, indent=2)
        print(f"  wrote {args.json_out}")


if __name__ == "__main__":
    main()
