"""本番忠実な arm E の held-out OOF 予測を一度だけダンプする。

なぜ必要か
----------
アーキ側 3 レバー(騎手/調教師の PL 残差効果・race-simplex 校正・IIA 緩和)は、いずれも
**凍結した予測の上の分割オラクル**で実装前に上限を測れる。ところが arm E 構成の永続化予測は
DB に 108 レースしか無く(active lgbm-094-cap900)、screening に耐えない。8,703 レースある
lgbm-058-acc は **arm E 以前の校正(holdout isotonic)** なので、校正軸を測ると
「arm E が既に取った分」を残差として誤検出する。

そこで本番と同じ arm E を walk-forward で 1 回だけ回し、**raw race-softmax と校正後 p の両方**を
馬単位で吐く。以後の screen はこの凍結ダンプの上で秒で回る。

出力(parquet): race_id, race_date, horse_id, raw, p, is_win, n_started
(騎手・調教師 ID は screen 側で DB から join する)

    cd training && uv run python ../scripts/dump_arme_oof.py --out ../out/arme_oof.parquet
"""

from __future__ import annotations

import argparse
import datetime
import time

import pandas as pd
from horseracing_db.session import create_db_engine
from horseracing_eval.dataset import load_eval_races, population_masks
from horseracing_eval.foldfit import predict_over_folds_multi
from sqlalchemy.orm import Session

from horseracing_training.calib_split import CalibSplitFactory
from horseracing_training.recipe import ModelRecipe

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../out/arme_oof.parquet")
    ap.add_argument("--rounds", type=int, default=900)
    ap.add_argument("--n-oof-blocks", type=int, default=3)
    ap.add_argument("--first-valid-year", type=int, default=2025)
    ap.add_argument("--to", default="2026-08-16")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--num-threads", type=int, default=1)
    ap.add_argument("--materialized-path", default="../artifacts/features.parquet")
    args = ap.parse_args()

    engine = create_db_engine(DB)
    with Session(engine) as session:
        races = load_eval_races(session, end_date=datetime.date.fromisoformat(args.to))
        recipe = ModelRecipe(
            objective="pl_topk", calibration="none", calib_frac=0.0, seed=args.seed,
            params=(("n_estimators", args.rounds),), label="arme-oof-dump",
        )
        fac = CalibSplitFactory(session, recipe, n_oof_blocks=args.n_oof_blocks,
                                method="isotonic", require_sufficient=False,
                                use_materialized=True,
                                materialized_path=args.materialized_path,
                                pin_snapshot=True)
        t0 = time.time()
        preds, valid, raw = predict_over_folds_multi(
            fac, races, regimes={"default": None},
            first_valid_year=args.first_valid_year, num_threads=args.num_threads,
            collect_raw=True)
        print(f"fit+predict {time.time()-t0:.1f}s  valid={len(valid):,} races")

    if not raw["default"]:
        raise RuntimeError("raw race-softmax が取れていない(fail-closed)")

    rows = []
    for er in valid:
        rid = er.context.race_id
        pop = population_masks(er)
        winner = pop.winner_horse_id if pop.eligible else None
        pr, rw = preds["default"][rid], raw["default"].get(rid, {})
        n = len(pr)
        for hid, pred in pr.items():
            rows.append((rid, er.context.race_date, hid, rw.get(hid), pred.win,
                         pred.top2, pred.top3,
                         (1 if winner is not None and hid == winner else 0),
                         (1 if pop.eligible else 0), n))
    df = pd.DataFrame(rows, columns=["race_id", "race_date", "horse_id", "raw", "p",
                                     "top2", "top3", "is_win", "eligible", "n_started"])
    miss = int(df["raw"].isna().sum())
    if miss:
        raise RuntimeError(f"raw が {miss} 行欠落(fail-closed)")
    df.to_parquet(args.out, index=False)
    elig = df[df["eligible"] == 1]
    print(f"wrote {args.out}: {len(df):,} 行 / {df['race_id'].nunique():,} レース "
          f"(winner NLL の対象 {elig['race_id'].nunique():,} レース)")
    print(f"  raw の Σ/レース 平均 = {df.groupby('race_id')['raw'].sum().mean():.6f}")
    print(f"  p   の Σ/レース 平均 = {df.groupby('race_id')['p'].sum().mean():.6f}")


if __name__ == "__main__":
    main()
