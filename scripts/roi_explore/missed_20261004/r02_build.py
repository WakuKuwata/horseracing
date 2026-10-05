"""R02 step 1: build production features (market_ev.build_features, same as export_rows_2007.py) through
2026-10-04 and extract the read-only DB inputs needed for the judged/final odds pairs.

Read-only: SELECT only. Writes only under artifacts/roi_explore/missed_20261004/R02_odds_drift/.

    cd training && uv run python ../scripts/roi_explore/missed_20261004/r02_build.py
"""
from __future__ import annotations

import json
import os
import pathlib
import time

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

from horseracing_training import market_ev

ROOT = pathlib.Path(__file__).resolve().parents[3]
OUT = ROOT / "artifacts/roi_explore/missed_20261004/R02_odds_drift"
OUT.mkdir(parents=True, exist_ok=True)
DB_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
D_FROM, D_TO = "2026-08-01", "2026-10-04"


def main() -> None:
    eng = create_engine(DB_URL)
    t0 = time.time()
    with eng.connect() as c:
        c.execute(text("SET TRANSACTION READ ONLY"))
        raw = market_ev.load_rows(c, through=D_TO, since="2007-01-01")
        print("rows loaded", len(raw), round(time.time() - t0, 1), "s", flush=True)
        chaos = pd.read_sql(text("""
            select cs.chaos_snapshot_id::text as snap_id, cs.race_id, cs.captured_at, cs.seconds_to_post,
                   cs.status, cs.capture_trigger, cs.field::text as field_json
            from chaos_snapshots cs"""), c)
        rh = pd.read_sql(text("""
            select rh.race_id, rh.horse_id, rh.horse_number, rh.entry_status, rh.odds, rh.popularity,
                   r.race_date, r.track_type, r.post_time
            from race_horses rh join races r using(race_id)
            where r.race_date between :a and :b"""), c, params={"a": D_FROM, "b": D_TO})
        res = pd.read_sql(text("""
            select rs.race_id, rs.horse_id, rs.finish_order, rs.result_status
            from race_results rs join races r using(race_id)
            where r.race_date between :a and :b"""), c, params={"a": D_FROM, "b": D_TO})
        mev = pd.read_sql(text("""
            select race_id, horse_id, model_version, horse_number, win_prob, odds_used, expected_return,
                   odds_observed_at, result_pending_at_compute, booster, computed_at
            from market_ev_predictions where result_pending_at_compute"""), c)
        picks = pd.read_sql(text("""
            select pick_id::text as pick_id, race_id, horse_id, horse_number, rule_id, kind, ens_expected_return,
                   single_expected_return, odds_used, odds_observed_at, days_since_last, post_time,
                   seconds_to_post, result_pending_at_compute, computed_at
            from attention_picks"""), c)
    feats = market_ev.build_features(raw)
    print("features built", len(feats), round(time.time() - t0, 1), "s", flush=True)
    feats["race_date"] = feats["race_date"].dt.strftime("%Y-%m-%d")
    cand = feats[(feats["race_date"] >= D_FROM) & (feats["race_date"] <= D_TO)].copy()
    for col in cand.columns:
        if cand[col].dtype == object:
            cand[col] = cand[col].astype(object).where(cand[col].notna(), None)
    cand = cand.drop(columns=["horse_row_updated_at"])
    cand.to_parquet(OUT / "feats_cand.parquet", index=False)
    chaos.to_parquet(OUT / "db_chaos.parquet", index=False)
    rh.to_parquet(OUT / "db_race_horses.parquet", index=False)
    res.to_parquet(OUT / "db_results.parquet", index=False)
    mev.to_parquet(OUT / "db_mev_pending.parquet", index=False)
    picks.to_parquet(OUT / "db_attention_picks.parquet", index=False)
    meta = {"db_url_host": DB_URL.split("@")[-1], "range": [D_FROM, D_TO], "n_raw": int(len(raw)),
            "n_feats": int(len(feats)), "n_cand": int(len(cand)), "n_chaos": int(len(chaos)),
            "n_mev_pending": int(len(mev)), "n_picks": int(len(picks)),
            "built_at_unix": time.time(), "seconds": round(time.time() - t0, 1)}
    (OUT / "build_meta.json").write_text(json.dumps(meta, indent=1))
    print(meta)


if __name__ == "__main__":
    main()
