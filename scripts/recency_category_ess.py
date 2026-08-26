"""主要カテゴリ別の ESS（ラベル非参照）。TE 対象の jockey/trainer と venue を見る。"""
import os
import numpy as np
from sqlalchemy import create_engine, text
import sys
sys.path.insert(0, "training/src")
from horseracing_training.recency import (RecencyWeightSpec, build_recency_weights,
                                          effective_sample_size)
DB = os.environ.get("DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")
eng = create_engine(DB)
with eng.connect() as c:
    rows = list(c.execute(text("""
        SELECT r.race_id, r.race_date, r.venue_code, rh.jockey_id, rh.trainer_id
        FROM races r JOIN race_horses rh ON rh.race_id=r.race_id AND rh.entry_status='started'
        ORDER BY r.race_date, r.race_id""")))
rids = [x[0] for x in rows]
dates = [x[1] for x in rows]
cats = {"venue_code": [x[2] for x in rows], "jockey_id": [x[3] for x in rows],
        "trainer_id": [x[4] for x in rows]}
cutoff = max(dates)
print(f"rows={len(rows):,} cutoff={cutoff}\n")
print(f"{'半減期':>7} {'ESS全体':>11} | " + " | ".join(f"{k:>12}" for k in cats)
      + "   ← カテゴリ別 ESS の最小値 / 中央値")
for hl in (365, 730, 1095):
    w = build_recency_weights(rids, dates, cutoff=cutoff, spec=RecencyWeightSpec(half_life_days=hl))
    line = f"{hl:>7} {effective_sample_size(w):>11,.0f} | "
    parts = []
    for name, vals in cats.items():
        by = {}
        for v, ww in zip(vals, w):
            by.setdefault(v, []).append(ww)
        ess = sorted(effective_sample_size(np.array(a)) for a in by.values() if len(a) > 0)
        parts.append(f"{ess[0]:>5.1f}/{ess[len(ess)//2]:>6.0f}")
    print(line + " | ".join(f"{p:>12}" for p in parts))
print("\n（各セルは「そのカテゴリ群の ESS の 最小値 / 中央値」）")
