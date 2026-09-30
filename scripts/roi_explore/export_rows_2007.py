"""Export the product feature table built from 2007+ data only (constitution I) for walk-forward validation.

    cd training && uv run python ../scripts/roi_explore/export_rows_2007.py
"""
import os, pathlib, sys
import numpy as np, pandas as pd
from sqlalchemy import create_engine
from horseracing_training import market_ev

REPO = pathlib.Path(__file__).resolve().parents[2]
with create_engine(os.environ.get("DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")).connect() as c:
    raw = market_ev.load_rows(c, through="2026-09-22", since="2007-01-01")
f = market_ev.build_features(raw)
f["n_winners"] = f.groupby("race_id")["won"].transform("sum").astype(int)
f["dead_heat"] = f["n_winners"] != 1
f["race_date"] = f["race_date"].dt.strftime("%Y-%m-%d")
for c in f.columns:
    if f[c].dtype == object:
        f[c] = f[c].astype(object).where(f[c].notna(), None)
out = REPO / "artifacts/market_ev/rows_2007.parquet"
f.drop(columns=["horse_row_updated_at"]).to_parquet(out, index=False)
print("rows", len(f), "races", f.race_id.nunique(), "years", f.year.min(), f.year.max(), "->", out)
