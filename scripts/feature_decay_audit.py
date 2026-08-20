"""稼働モデルが依存している特徴のうち、現行レジームで死んでいる/薄いものを出す。

このプロジェクトで最近効いた改善はすべてこの系統だった — 091 体重マスク -0.0106、本賞金 backfill
-0.0098、重賞ラベル修正 -0.0129。いずれも**新しい情報をゼロ**で、既にある情報がモデルに届いて
いなかっただけである。新規特徴の screening は生存ゼロ(feature-axis-audit-2026-08)なので、探す
べきはこちら。

出力は「モデルの split 重要度」×「2026 の充足率が学習窓に対してどれだけ落ちたか」で並べる。
重要度が低い列が枯れていても実害は小さく、重要度が高い列が枯れていれば静かに壊れている。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import lightgbm as lgb
import pandas as pd
from horseracing_db.session import create_db_engine
from sqlalchemy import text

DB = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", default="artifacts/features.parquet")
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args()

    eng = create_db_engine(DB)
    with eng.connect() as c:
        mv, uri = c.execute(text(
            "select model_version, weights_uri from model_versions where adoption_status='active'"
        )).one()
    print(f"active = {mv}")
    booster = lgb.Booster(model_file=uri)
    meta = json.loads((Path(uri).parent / "metadata.json").read_text())
    train_through = str(meta.get("training", {}).get("train_through") or meta.get("train_through"))
    print(f"booster の学習終端 = {train_through}")

    names = booster.feature_name()
    split = booster.feature_importance(importance_type="split")
    imp = pd.Series(split, index=names).sort_values(ascending=False)
    imp_share = imp / imp.sum()

    df = pd.read_parquet(args.parquet)
    # parquet は race_id しか持たない。JRA-VAN の 12 桁 id は先頭 4 桁が年。
    df["year"] = df["race_id"].astype(str).str[:4].astype(int)
    cols = [c for c in names if c in df.columns]
    missing_cols = [c for c in names if c not in df.columns]

    # 学習窓(booster が実際に見た期間)と現行レジームで、非欠損率を比べる
    # arm E 以降 booster の学習終端は現在に届くが、現行レジームの行は窓の末端にわずかしか無い。
    # 「モデルが厚く学んだ期間」と「いま予測している期間」を比べたいので、末端 2 年を除いて基準にする。
    tt_year = min(int(train_through[:4]) if train_through[:4].isdigit() else 2024,
                  int(df["year"].max()) - 2)
    train_mask = df["year"] <= tt_year
    now_mask = df["year"] == df["year"].max()
    cov_train = df.loc[train_mask, cols].notna().mean()
    cov_now = df.loc[now_mask, cols].notna().mean()
    drop = cov_train - cov_now

    out = pd.DataFrame({
        "split_share": imp_share.reindex(cols),
        f"充足_学習窓(~{tt_year})": cov_train,
        f"充足_{int(df['year'].max())}": cov_now,
        "低下": drop,
    })
    out["影響"] = out["split_share"] * out["低下"].clip(lower=0)
    out = out.sort_values("影響", ascending=False)

    print(f"\n=== 重要度×低下 の上位 {args.top} ===")
    show = out[out["影響"] > 0].head(args.top)
    if show.empty:
        print("  低下している列は無い(重要度を持つ列の充足率は現行レジームでも維持されている)")
    else:
        print(show.to_string(float_format=lambda v: f"{v:8.4f}"))

    print(f"\n=== 参考: 現行レジームで充足率 <50% の列(重要度順) ===")
    thin = out[out[f"充足_{int(df['year'].max())}"] < 0.5].sort_values("split_share", ascending=False)
    print(thin.head(15).to_string(float_format=lambda v: f"{v:8.4f}") if not thin.empty else "  なし")

    if missing_cols:
        print(f"\n=== parquet に存在しないモデル入力列(要調査) ===\n  {missing_cols}")


if __name__ == "__main__":
    main()
