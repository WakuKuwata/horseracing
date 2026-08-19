"""適用集合(opportunity set)の race_id 一覧を作る。

定義(2026-07 の 3 レンズ提案そのまま): **候補特徴が利用可能で、かつレース内に変動がある race**。

  - 利用可能  = そのレースの出走全馬でその列が非欠損
  - 変動がある = そのレース内で値が 2 種類以上ある

レース内で全馬同値の列は、目的関数がレース内 softmax なので**寄与が完全に相殺される**
([[race-constant-features-need-interaction]])。そこを適用集合に含めると、効果がゼロと分かって
いるレースで薄めるだけになる。

このスクリプトは **eval ではなく呼び出し側**に置く。eval は features を import しない(020 の
境界)ため、マスクは特徴を知っている側で作って注入する契約になっている。

**出力は判定の入力になる。** 窓を見てから列や条件を選ぶのは選択そのものなので、列名と
expected_coverage は gate-config に凍結してから作ること。実測被覆率が宣言範囲の外なら
paired-eval が fail-closed で止まる。

使い方:
    cd training && uv run python ../scripts/build_opportunity_mask.py \
        --column asof_spdfig_last --from 2019-01-01 --to 2024-12-31 \
        --out ../out/opportunity-spdfig.txt
"""

from __future__ import annotations

import argparse
import datetime

import pandas as pd
from sqlalchemy import create_engine, text

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--column", required=True, help="候補特徴の列名(materialized parquet の列)")
    ap.add_argument("--from", dest="date_from", type=datetime.date.fromisoformat, required=True)
    ap.add_argument("--to", dest="date_to", type=datetime.date.fromisoformat, required=True)
    ap.add_argument("--parquet", default="../artifacts/features.parquet")
    ap.add_argument("--out", required=True)
    ap.add_argument("--database-url", default=DB)
    args = ap.parse_args()

    engine = create_engine(args.database_url)
    dates = pd.read_sql(
        text("select race_id, race_date from races where race_date between :a and :b"),
        engine, params={"a": args.date_from, "b": args.date_to},
    )
    df = pd.read_parquet(args.parquet, columns=["race_id", args.column])
    df = df.merge(dates, on="race_id", how="inner")
    if df.empty:
        raise SystemExit(f"error: no rows for {args.column} in {args.date_from}..{args.date_to}")

    g = df.groupby("race_id")[args.column]
    complete = g.apply(lambda s: s.notna().all())          # 出走全馬で非欠損
    varies = g.nunique(dropna=True) > 1                    # レース内に 2 種類以上
    mask = complete & varies

    total = int(mask.size)
    n_in = int(mask.sum())
    coverage = n_in / total if total else 0.0
    n_incomplete = int((~complete).sum())
    n_constant = int((complete & ~varies).sum())

    print(f"列 {args.column}  窓 {args.date_from}..{args.date_to}")
    print(f"  対象レース          {total:,}")
    print(f"  欠損を含む          {n_incomplete:,} ({n_incomplete/total:.1%})")
    print(f"  全馬同値(効果が相殺) {n_constant:,} ({n_constant/total:.1%})")
    print(f"  **適用集合**         {n_in:,} (**被覆率 {coverage:.3f}**)")

    ids = sorted(mask[mask].index)
    with open(args.out, "w") as fh:
        fh.write(f"# opportunity set: {args.column} が全馬非欠損かつレース内に変動\n")
        fh.write(f"# window {args.date_from}..{args.date_to}\n")
        fh.write(f"# coverage {coverage:.6f} ({n_in}/{total})\n")
        for rid in ids:
            fh.write(f"{rid}\n")
    print(f"  wrote {args.out}")
    print(f"\n  gate-config の opportunity_set.expected_coverage には、この被覆率 {coverage:.3f} を"
          f"\n  含む範囲を**結果を見る前に**凍結すること。外れると paired-eval が fail-closed。")


if __name__ == "__main__":
    main()
