"""feature 104 confirmatory: 片アームを回して per-race 予測を吐く。

アームは **行列そのもの**で区別する(gate-config `_arm_identity_note`):
  active    : HORSERACING_FEATURE_POOL_START=2007-01-01 + artifacts/features.parquet
  candidate : HORSERACING_FEATURE_POOL_START=1986-01-01 + artifacts/features_1986.parquet

2007+ の行列に 1986-2006 の race_id を渡しても行が存在せず自然に落ちるので、
学習プールは行列で一意に決まる。両アームで異なるのはこれだけ。

FEATURE_POOL_START はプロセス全体の定数なので **1 プロセス 1 アーム**。判定は
`confirm_pool_verdict.py` が 2 つの出力を突き合わせて行う。

    cd training && HORSERACING_FEATURE_POOL_START=2007-01-01 \
      uv run python ../scripts/confirm_pool_arm.py --arm active
"""

from __future__ import annotations

import argparse
import datetime
import json
import time

import pandas as pd
from horseracing_db.session import create_db_engine
from horseracing_db.validation import FEATURE_POOL_START
from horseracing_eval.dataset import load_eval_races
from horseracing_eval.foldfit import predict_over_folds
from sqlalchemy.orm import Session

from horseracing_training.calib_split import CalibSplitFactory
from horseracing_training.recipe import ModelRecipe

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
GATE = "../specs/104-historical-data-backfill/gate-config.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=("active", "candidate"), required=True)
    ap.add_argument("--gate", default=GATE)
    ap.add_argument("--num-threads", type=int, default=6)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cfg = json.load(open(args.gate))
    a = cfg["arms"]
    want_pool = datetime.date.fromisoformat(a[f"{args.arm}_pool_start"])
    path = "../" + a[f"{args.arm}_materialized"]

    # fail-closed: env が意図した値でなければ、静かに別アームを回してしまう
    if FEATURE_POOL_START != want_pool:
        raise SystemExit(
            f"HORSERACING_FEATURE_POOL_START={FEATURE_POOL_START} だが arm={args.arm} は "
            f"{want_pool} を要求する。env を設定して起動し直すこと(fail-closed)")
    man = json.load(open(path.replace(".parquet", ".manifest.json")))
    if man["data_from"] != a[f"{args.arm}_manifest_data_from"] or \
            man["n_rows"] != a[f"{args.arm}_manifest_rows"]:
        raise SystemExit(f"parquet が凍結値と違う: {man['data_from']}/{man['n_rows']}")

    print(f"arm={args.arm}  pool={FEATURE_POOL_START}  matrix={path}")
    print(f"  manifest data_from={man['data_from']} rows={man['n_rows']:,}")

    recipe = ModelRecipe(
        objective=a["objective"], calibration=a["calibration"], calib_frac=a["calib_frac"],
        seed=a["seed"], params=(("n_estimators", a["n_estimators"]),),
        weight_mask_rate=a["weight_mask_rate"], weight_mask_seed=a["weight_mask_seed"],
        label=f"104-{args.arm}",
    )
    engine = create_db_engine(DB)
    with Session(engine) as session:
        races = load_eval_races(
            session, end_date=datetime.date.fromisoformat(cfg["eval_window"]["to"]))
        # **各アームの eval プールは、そのアームの pool 開始日から始める。**
        # DB に 1986+ が入った結果 load_eval_races は全期間を返すので、これを入れないと
        # active アームの OOF ブロック分割の最初のブロックが丸ごと pre-2007 になり、
        # 2007+ の行列では学習行が 0 になって落ちる(実際に落ちた)。production は DB 自体が
        # 2007+ だったので、このフィルタがその状態を厳密に再現する。
        n_all = len(races)
        races = [er for er in races if er.context.race_date >= FEATURE_POOL_START]
        print(f"  eval プール {n_all:,} → {len(races):,} レース "
              f"({FEATURE_POOL_START} 以降に制限)")
        fac = CalibSplitFactory(session, recipe, n_oof_blocks=a["n_oof_blocks"],
                                method="isotonic", require_sufficient=False,
                                use_materialized=True, materialized_path=path,
                                pin_snapshot=True)
        print(f"  recipe_hash={fac.recipe_hash}")
        t0 = time.time()
        preds, valid = predict_over_folds(
            fac, races, first_valid_year=cfg["first_valid_year"],
            num_threads=args.num_threads)
        print(f"  fit+predict {time.time()-t0:.1f}s  valid={len(valid):,} races")

    rows = []
    for er in valid:
        rid = er.context.race_id
        for hid, p in preds[rid].items():
            rows.append((rid, er.context.race_date, hid, p.win, p.top2, p.top3))
    df = pd.DataFrame(rows, columns=["race_id", "race_date", "horse_id",
                                     "win", "top2", "top3"])
    out = args.out or f"../out/104_{args.arm}.parquet"
    df.to_parquet(out, index=False)
    meta = {"arm": args.arm, "pool_start": str(FEATURE_POOL_START),
            "materialized": path, "manifest_data_from": man["data_from"],
            "manifest_rows": man["n_rows"], "recipe_hash": fac.recipe_hash,
            "gate_config_hash_file": "specs/104-historical-data-backfill/gate-config.hash.txt",
            "n_valid_races": len(valid), "n_rows": len(df)}
    json.dump(meta, open(out.replace(".parquet", ".meta.json"), "w"), indent=2)
    print(f"  wrote {out} ({len(df):,} 行 / {df['race_id'].nunique():,} レース)")


if __name__ == "__main__":
    main()
