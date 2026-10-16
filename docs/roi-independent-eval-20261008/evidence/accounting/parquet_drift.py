"""Compare stored odds in research parquets vs current DB for race_date >= 2025-10-11 (139 repair impact)."""
import pandas as pd, pyarrow.parquet as pq, hashlib, json
from sqlalchemy import create_engine, text
URL = "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
eng = create_engine(URL)
out = {}
with eng.connect() as con:
    con.execute(text("SET TRANSACTION READ ONLY"))
    db = pd.read_sql(text("""select rh.race_id, rh.horse_id, rh.horse_number, rh.odds::float as db_odds, rh.popularity db_pop, rh.entry_status, r.race_date
        from race_horses rh join races r using(race_id) where r.race_date >= '2025-10-11'"""), con)
    pay = pd.read_sql(text("select race_id, horse_number, payout_yen from official_win_payouts"), con)
print("db rows", len(db), "races", db.race_id.nunique(), "payout races", pay.race_id.nunique())
for name, path in [("rows_2007", "/Users/kuwatawaku/workspace/horseracing/artifacts/market_ev/rows_2007.parquet"),
                   ("strict2007_rows", "/Users/kuwatawaku/workspace/horseracing/artifacts/roi_strict2007_20260930/input/rows.parquet")]:
    sch = pq.read_schema(path)
    cols = sch.names
    want = [c for c in ["race_id", "horse_id", "horse_number", "odds", "odds_used", "race_date", "popularity", "won", "finish_order", "entry_status"] if c in cols]
    df = pq.read_table(path, columns=want).to_pandas()
    df["race_date"] = pd.to_datetime(df["race_date"]).dt.date
    df = df[df["race_date"] >= pd.Timestamp("2025-10-11").date()]
    ocol = "odds" if "odds" in df else "odds_used"
    m = df.merge(db, on=["race_id", "horse_id"], how="left", suffixes=("", "_db"))
    m["diff"] = (m[ocol].astype(float) - m["db_odds"]).abs() > 1e-9
    byrace = m.groupby("race_id")["diff"].any()
    res = {
        "columns": cols[:40], "rows_ge_20251011": int(len(df)), "races": int(df.race_id.nunique()),
        "max_race_date": str(df.race_date.max()), "rows_odds_differ": int(m["diff"].sum()),
        "races_with_any_diff": int(byrace.sum()), "rows_db_missing": int(m["db_odds"].isna().sum()),
    }
    # diff by month
    m["month"] = pd.to_datetime(m["race_date"]).dt.to_period("M").astype(str)
    res["diff_by_month"] = m.groupby("month")["diff"].agg(["sum", "count"]).astype(int).to_dict("index")
    # winner rows: parquet odds*100 vs official payout
    if "won" in m or "finish_order" in m:
        w = m[(m.get("won", m.get("finish_order") == 1)) == True] if "won" in m else m[m["finish_order"] == 1]
        wp = w.merge(pay, on=["race_id", "horse_number"], how="inner")
        wp["pq_vs_pay"] = (wp[ocol].astype(float) * 100 - wp["payout_yen"]).abs() > 0.5
        wp["db_vs_pay"] = (wp["db_odds"] * 100 - wp["payout_yen"]).abs() > 0.5
        res["winner_rows_with_payout"] = int(len(wp))
        res["winner_parquet_odds_ne_payout"] = int(wp["pq_vs_pay"].sum())
        res["winner_db_odds_ne_payout"] = int(wp["db_vs_pay"].sum())
        res["winner_mean_ratio_pq_over_pay"] = float((wp[ocol].astype(float) * 100 / wp["payout_yen"]).mean())
        res["winner_mean_ratio_db_over_pay"] = float((wp["db_odds"] * 100 / wp["payout_yen"]).mean())
    out[name] = res
    print(json.dumps({name: res}, ensure_ascii=False, indent=1, default=str))
json.dump(out, open("/private/tmp/claude-501/-Users-kuwatawaku-workspace-horseracing/76c2ac6f-96e3-4c6c-8f6a-7693441ba707/scratchpad/eval1007/accounting/parquet_drift.json", "w"), indent=1, default=str)
