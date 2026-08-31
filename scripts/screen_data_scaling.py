"""学習データ量のスケーリング曲線(同分布・レース単位)。

問い: 「**分布を変えずにレース数だけ**増やしたとき winner NLL はどう下がるか」。
これは 1986-2006 の 905,152 行(≒72,000 レース)を取り込む価値の **楽観上限** を出すための
screening である。古いレースの限界価値は分布シフトのぶん必ずこれ以下になる。

なぜこの形か(codex 指摘の反映)
------------------------------
当初案は「学習窓の開始年をずらす」ablation だったが、それは **量と年代を同時に動かす**ので
交絡する。ここでは 2007-2026 の全期間から **年別に層化して**レースを間引くので、年代構成は
ほぼ不変のまま量だけが変わる。

**単位はレースであって馬行ではない。** Plackett-Luce の尤度はレース単位なので、905,152 馬行を
905,152 独立標本として外挿してはいけない。

判定規則(実行前に固定する)
--------------------------
diff(f) = NLL(f) − NLL(1.0) を f ∈ {0.25, 0.5, 0.75} で測り、diff(f) = −b·ln(f) を最小二乗で
当てる。レース数 2 倍の楽観上限 = **b·ln(2)**。

    b·ln(2) <= 0.0015 → 同分布ですら 2 倍で採用バーに届かない。**pre-2007 路線を閉じる**
    b·ln(2) >= 0.0045 → 楽観上限は十分。次は「古さによる価値低下率 ρ」を測る段へ進む
    その間            → 曖昧。年代移送を小さく回すか閉じるかを別途判断する

バーは点推定 −0.0031(CI 上限<0 の要求)/ δ=0.00352。ρ<1 なので 0.0045 でも楽観的である
ことに注意 — **go は「採用できそう」ではなく「ρ を測る価値がある」の意味**。

正直な限界
----------
1. **容量は rounds 900 に固定**する(本番構成)。データが 2 倍なら最適容量も上がるので、
   この曲線は容量固定下の値であり、**閉じる方向には保守的でない**。
2. 対数線形の当てはめ自体が楽観(学習曲線は普通は逓減する)。0.25-1.0 の広い傾きと
   0.5-1.0 の局所傾きを両方出し、食い違えば飽和のサインとして報告する。
3. これは **screening であって採否ではない**(can_adopt=false)。

    cd training && uv run python ../scripts/screen_data_scaling.py
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import time

import numpy as np
from horseracing_db.session import create_db_engine
from horseracing_eval.bootstrap import race_day_cluster_bootstrap_ci_v1
from horseracing_eval.dataset import load_eval_races, population_masks
from horseracing_eval.foldfit import predict_over_folds
from sqlalchemy.orm import Session

from horseracing_training.calib_split import CalibSplitFactory
from horseracing_training.recipe import ModelRecipe

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"

#: 事前登録する間引き率。1.0 が基準アーム。
FRACTIONS = (1.0, 0.75, 0.5, 0.25)

#: サブサンプルのハッシュ salt。組込み hash() は PYTHONHASHSEED で再現しないので使わない。
SALT = "screen_data_scaling/v1"


def stable_u(race_id: str) -> float:
    """race_id -> [0,1) の決定論的な一様値(実行間・マシン間で不変)。"""
    h = hashlib.sha1(f"{SALT}|{race_id}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


def nested_strata(race_ids_by_year: dict[int, list[str]]) -> dict[float, set[str]]:
    """年別に層化した **入れ子** のサブサンプル集合を作る。

    各年でレースを stable_u の昇順に並べ、先頭 f 割を採る。順序は f に依存しないので
    0.25 ⊂ 0.5 ⊂ 0.75 ⊂ 1.0 が構造的に保証される(アーム間の分散が下がる)。
    """
    out: dict[float, set[str]] = {f: set() for f in FRACTIONS}
    for _year, ids in race_ids_by_year.items():
        ordered = sorted(ids, key=stable_u)
        for f in FRACTIONS:
            k = int(round(len(ordered) * f))
            out[f].update(ordered[:k])
    return out


class SubsampleFactory:
    """内側の factory に渡す train_races を keep 集合で絞るだけのラッパ。

    valid 側は predict_over_folds が別に扱うので触らない(model-blind は保たれる)。
    """

    def __init__(self, inner, keep: set[str], name: str) -> None:
        self.inner, self.keep, self.name = inner, keep, name
        self.fold_sizes: list[tuple[int, int]] = []  # (渡された数, 実際に学習した数)

    recipe_meta = property(lambda s: {**s.inner.recipe_meta, "subsample": s.name})
    recipe_hash = property(lambda s: s.inner.recipe_hash)

    def fit(self, train_races, *, num_threads=None):
        kept = [r for r in train_races if r.race_id in self.keep]
        self.fold_sizes.append((len(train_races), len(kept)))
        if not kept:
            raise RuntimeError(f"{self.name}: 学習レースが 0 になった(間引き率が過大)")
        return self.inner.fit(kept, num_threads=num_threads)


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


def n_differing(valid_races, pa, pb) -> int:
    """097 の教訓: 両アームが同一モデルに縮退していないことを構造的に確かめる。"""
    n = 0
    for er in valid_races:
        for hid, p in pa[er.context.race_id].items():
            q = pb[er.context.race_id].get(hid)
            if q is not None and p.win != q.win:
                n += 1
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fractions", default=None,
                    help="事前登録は 1.0,0.75,0.5,0.25。smoke 用にのみ変更する")
    ap.add_argument("--n-oof-blocks", type=int, default=3,
                    help="arm E の OOF ブロック数。本番は 8 だが探索ではコストのため 3")
    ap.add_argument("--rounds", type=int, default=900, help="本番 active と同じ容量")
    ap.add_argument("--first-valid-year", type=int, default=2025)
    ap.add_argument("--to", default="2026-08-16")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--num-threads", type=int, default=1)
    ap.add_argument("--bootstrap-b", type=int, default=1000)
    ap.add_argument("--materialized-path", default="../artifacts/features.parquet",
                    help="長時間 run 中に DB が動くので snapshot を pin する(091 D16 / 099)")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args()

    fracs = tuple(float(x) for x in args.fractions.split(",")) if args.fractions else FRACTIONS
    if fracs[0] != 1.0:
        raise SystemExit("基準アーム(1.0)を先頭に置くこと")

    print("*** SCREENING ONLY — can_adopt=false。勝ちは別窓で新規に事前登録して確認する ***")
    print(__doc__.split("判定規則(実行前に固定する)")[1].split("正直な限界")[0].strip())
    print()

    engine = create_db_engine(DB)
    with Session(engine) as session:
        races = load_eval_races(session, end_date=datetime.date.fromisoformat(args.to))
        by_year: dict[int, list[str]] = {}
        for er in races:
            by_year.setdefault(er.context.race_date.year, []).append(er.context.race_id)
        keep = nested_strata(by_year)

        preds: dict[float, dict] = {}
        valid = None
        sizes: dict[float, int] = {}
        for f in fracs:
            recipe = ModelRecipe(
                objective="pl_topk", calibration="none", calib_frac=0.0, seed=args.seed,
                params=(("n_estimators", args.rounds),),
                label=f"data-scaling:f={f}",
            )
            inner = CalibSplitFactory(session, recipe, n_oof_blocks=args.n_oof_blocks,
                                      method="isotonic", require_sufficient=False,
                                      use_materialized=True,
                                      materialized_path=args.materialized_path,
                                      pin_snapshot=True)
            fac = SubsampleFactory(inner, keep[f], f"f={f}")
            t0 = time.time()
            p, v = predict_over_folds(fac, races, first_valid_year=args.first_valid_year,
                                      num_threads=args.num_threads)
            preds[f], valid = p, v
            sizes[f] = fac.fold_sizes[-1][1]
            print(f"  f={f:<5} {time.time()-t0:7.1f}s  train={sizes[f]:,} races "
                  f"(最終 fold, 渡された {fac.fold_sizes[-1][0]:,} 件中)  valid={len(v):,}")

        base = preds[1.0]
        rows = []
        print()
        for f in fracs[1:]:
            nd = n_differing(valid, preds[f], base)
            if nd == 0:
                raise RuntimeError(
                    f"f={f} の予測が基準と完全一致 = 間引きが効いていない(097 型の縮退)")
            ci = race_day_cluster_bootstrap_ci_v1(diffs_by_day(valid, preds[f], base),
                                                  b=args.bootstrap_b, seed=20260827)
            rows.append({"fraction": f, "n_train_races": sizes[f], "diff": ci.point,
                         "ci_low": ci.ci_low, "ci_high": ci.ci_high, "n_days": ci.n_days,
                         "n_differing_predictions": nd})
            print(f"  f={f:<5} n={sizes[f]:>6,}  diff={ci.point:+.6f} "
                  f"CI[{ci.ci_low:+.6f}, {ci.ci_high:+.6f}]  (異なる予測 {nd:,} 頭)")

        # diff(f) = -b*ln(f) を原点通過の最小二乗で当てる(f=1 で diff=0 は定義から厳密)
        x = np.array([-math.log(r["fraction"]) for r in rows])
        y = np.array([r["diff"] for r in rows])
        b_global = float((x @ y) / (x @ x))
        # 限界傾き: f=1.0 に最も近い**区間**の傾き(飽和の検出用)。
        # 原点通過の 2 点フィットにすると遠い点に引きずられて限界傾きにならない。
        nearest = max(rows, key=lambda r: r["fraction"])
        b_local = float(nearest["diff"] / -math.log(nearest["fraction"]))
        # 参考: 全区間の限界傾き(飽和の形を見るため)
        seg = [(1.0, 0.0)] + [(r["fraction"], r["diff"]) for r in rows]
        seg.sort(key=lambda t: -t[0])
        segments = [{"from": b[0], "to": a[0],
                     "b": (b[1] - a[1]) / (math.log(a[0]) - math.log(b[0]))}
                    for a, b in zip(seg, seg[1:], strict=False)]

        ceil_global, ceil_local = b_global * math.log(2), b_local * math.log(2)
        print("\n=== 傾き ===")
        print(f"  全域 (0.25-1.0): b={b_global:.6f}  → レース数 2 倍の楽観上限 {-ceil_global:+.6f}")
        print(f"  1.0 直近の区間 : b={b_local:.6f}  → 同 {-ceil_local:+.6f}")
        for sg in segments:
            print(f"    区間 f {sg['from']:.2f}→{sg['to']:.2f}  b={sg['b']:.6f}")
        if b_local < b_global * 0.6:
            print("  ! 1.0 近傍の傾きが全域より明確に小さい = 既に飽和しかけている")

        print("\n=== 判定(事前登録した規則・全域の傾きを正本とする) ===")
        if ceil_global <= 0.0015:
            verdict = "close"
            print("  → 同分布ですら 2 倍で届かない。**pre-2007 路線を閉じる**")
        elif ceil_global >= 0.0045:
            verdict = "go_measure_rho"
            print("  → 楽観上限は十分。次は年代移送で価値低下率 ρ を測る")
        else:
            verdict = "ambiguous"
            print("  → 曖昧。年代移送を小さく回すか閉じるかを別途判断する")
        print("  注: go は『採用できそう』ではなく『ρ を測る価値がある』の意味。"
              "バーは点推定 −0.0031 / δ=0.00352 で、ρ<1 のぶん必ず割り引かれる。")

        if args.json_out:
            with open(args.json_out, "w") as fh:
                json.dump({"screening_only": True, "can_adopt": False,
                           "arm": "armE", "rounds": args.rounds, "seed": args.seed,
                           "n_oof_blocks": args.n_oof_blocks,
                           "first_valid_year": args.first_valid_year, "to": args.to,
                           "salt": SALT, "fractions": list(fracs), "results": rows,
                           "b_global": b_global, "b_local": b_local,
                           "segments": segments,
                           "ceiling_double_global": -ceil_global,
                           "ceiling_double_local": -ceil_local,
                           "verdict": verdict}, fh, indent=2)
            print(f"  wrote {args.json_out}")


if __name__ == "__main__":
    main()
