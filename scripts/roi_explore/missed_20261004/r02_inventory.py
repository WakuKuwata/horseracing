"""R02 inventory (outcome-free, pre-prereg): counts of judged/final odds pairs and time-to-post distributions.
No final odds values, no outcomes are read. Read-only DB."""
import json, pathlib
import numpy as np, pandas as pd
from sqlalchemy import create_engine, text
ROOT = pathlib.Path(__file__).resolve().parents[3]
R05 = ROOT / "artifacts/roi_explore/missed_20261004/R05_settlement"
e = create_engine("postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
aff = pd.read_csv(R05 / "affected_races.csv", dtype={"race_id": str})
with e.connect() as c:
    cs = pd.read_sql(text("""select cs.race_id, cs.captured_at, cs.seconds_to_post, cs.status, cs.capture_trigger,
        jsonb_array_length(cs.field) as n_field, r.race_date, r.track_type, r.post_time,
        (select count(*) from race_horses rh where rh.race_id=cs.race_id and rh.entry_status='started') as n_started_now,
        (select array_agg(x->>'horse_id' order by x->>'horse_id') from jsonb_array_elements(cs.field) x) as snap_ids,
        (select array_agg(rh.horse_id order by rh.horse_id) from race_horses rh where rh.race_id=cs.race_id and rh.entry_status='started') as now_ids,
        (select count(*) from race_results rs where rs.race_id=cs.race_id) as n_res
        from chaos_snapshots cs join races r using(race_id)"""), c)
cs["field_match"] = [list(a or []) == list(b or []) for a, b in zip(cs.snap_ids, cs.now_ids)]
cs = cs.merge(aff[["race_id", "affected", "correctable", "timing_class", "staleness_min"]], on="race_id", how="left")
flat = cs[(cs.track_type != "障") & (cs.status == "active")]
print("chaos total", len(cs), "flat active", len(flat))
print("field_match", flat.field_match.value_counts().to_dict())
print("has results", (flat.n_res > 0).sum())
flat = flat.assign(final_src=np.where(flat.n_res == 0, "no_results",
                    np.where(flat.affected.fillna(0) == 0, "db_stored", np.where(flat.correctable.fillna(False), "archive", "none"))))
print(flat.final_src.value_counts().to_dict())
ok = flat[(flat.final_src.isin(["db_stored", "archive"])) & flat.field_match]
print("usable chaos pairs", len(ok), "race-days", ok.race_date.nunique())
h = ok.seconds_to_post / 3600
print("chaos hours to post quantiles", np.round(np.quantile(h, [0, .05, .1, .25, .5, .75, .9, .95, 1]), 2).tolist())
for lo, hi in [(0, 1), (1, 4), (4, 10), (10, 99)]:
    print(f"  chaos [{lo},{hi})h:", int(((h >= lo) & (h < hi)).sum()))
# affected-correctable stored pre-race pairs
a = aff[(aff.affected == 1) & (aff.correctable == True) & (aff.track_type != "障")]
print("affected correctable flat", len(a), "timing classes", a.timing_class.value_counts().to_dict())
sh = a.staleness_min.astype(float) / 60
print("stored-pre hours to post quantiles", np.round(np.nanquantile(sh, [0, .05, .1, .25, .5, .75, .9, .95, 1]), 2).tolist())
for lo, hi in [(0, 1), (1, 4), (4, 10), (10, 99)]:
    print(f"  stored [{lo},{hi})h:", int(((sh >= lo) & (sh < hi)).sum()))
both = set(a.race_id) & set(ok.race_id)
print("races with both chaos and stored-pre pair", len(both))
print("date range chaos ok", ok.race_date.min(), ok.race_date.max(), "stored", a.race_date.min(), a.race_date.max())
rows = pd.read_parquet(ROOT / "artifacts/market_ev/rows_2007.parquet", columns=["race_id", "race_date"])
print("rows_2007 max date", rows.race_date.max())
print("chaos ok races in rows_2007", ok.race_id.isin(rows.race_id).sum(), "stored-pre in rows_2007", a.race_id.isin(rows.race_id).sum())
