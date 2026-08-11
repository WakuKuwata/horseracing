"""T015 / FR-028 / SC-005: who actually gets a prev_weight, and who does not.

Two populations, and they are not interchangeable:
  settled  - what the model trains and is evaluated on
  pending  - the rows that will actually be predicted. SC-005's 85% floor is measured HERE.

The debut breakdown matters because a horse can lack a proxy for two very different reasons: it
truly has no past (nothing to fix), or its history is split across an unresolved `nk:` surrogate
(067, unimplemented). Reporting them together would overstate how much of the gap is irreducible.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text
from sqlalchemy.orm import Session

from horseracing_db.session import create_db_engine

OUT = Path(__file__).parent / "coverage_audit.json"
SC005_FLOOR = 0.85

SQL = text("""
WITH runs AS (
  SELECT rh.race_id, rh.horse_id, r.race_date, rh.entry_status, rh.weight,
         EXISTS (SELECT 1 FROM race_results rr WHERE rr.race_id = rh.race_id) AS settled
  FROM race_horses rh JOIN races r ON r.race_id = rh.race_id
  WHERE rh.entry_status = 'started'
), src AS (
  SELECT horse_id, race_date, weight FROM runs
  WHERE weight IS NOT NULL AND weight BETWEEN 200 AND 800
)
SELECT a.race_id, a.horse_id, a.race_date, a.settled, a.weight IS NULL AS weight_missing,
       (SELECT max(s.race_date) FROM src s
         WHERE s.horse_id = a.horse_id AND s.race_date < a.race_date) AS src_date,
       EXISTS (SELECT 1 FROM runs b
                WHERE b.horse_id = a.horse_id AND b.race_date < a.race_date) AS has_past_start
FROM runs a
""")


def _bucket(days):
    if pd.isna(days):
        return "no_source"
    d = int(days)
    return "<=45" if d <= 45 else "46-120" if d <= 120 else "121-365" if d <= 365 else ">365"


def main() -> int:
    os.environ.setdefault(
        "DATABASE_URL", "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing"
    )
    with Session(create_db_engine()) as s:
        df = pd.read_sql(SQL, s.connection())
    df["race_date"] = pd.to_datetime(df["race_date"])
    df["has_prev"] = df["src_date"].notna()
    df["age_days"] = (df["race_date"] - pd.to_datetime(df["src_date"])).dt.days
    df["band"] = df["age_days"].map(_bucket)
    df["ns"] = df["horse_id"].str.startswith("nk:").map({True: "nk", False: "canonical"})

    settled, pending = df[df["settled"]], df[~df["settled"]]
    rep: dict = {
        "settled_rows": int(len(settled)),
        "pending_rows": int(len(pending)),
        "by_year": {
            str(y): {"rows": int(len(g)), "prev_weight_coverage": float(g["has_prev"].mean())}
            for y, g in settled.groupby(settled["race_date"].dt.year)
        },
        "staleness_band": {
            b: int(n) for b, n in settled["band"].value_counts().sort_index().items()
        },
        "id_namespace": {
            ns: {"rows": int(len(g)), "prev_weight_coverage": float(g["has_prev"].mean())}
            for ns, g in df.groupby("ns")
        },
    }

    # why a row has no proxy: genuinely first start, vs history that exists but is unusable
    no_proxy = df[~df["has_prev"]]
    rep["no_proxy_breakdown"] = {
        "true_debut_no_past_start": int((~no_proxy["has_past_start"]).sum()),
        "has_past_start_but_no_weighed_one": int(no_proxy["has_past_start"].sum()),
        "nk_namespace_share": float((no_proxy["ns"] == "nk").mean()) if len(no_proxy) else 0.0,
        "note": "a `nk:` split shows up as a MISSING proxy, not a wrong one (067 unimplemented). "
                "It is a coverage loss, never a mis-join.",
    }

    # per-race coverage shape (all / some / none) — the softmax couples horses within a race
    cov = settled.groupby("race_id")["has_prev"].mean()
    rep["race_level_coverage"] = {
        "all_horses": int((cov == 1.0).sum()),
        "some_horses": int(((cov > 0) & (cov < 1)).sum()),
        "no_horses": int((cov == 0).sum()),
    }

    # --- SC-005: measured on the PENDING cohort, not the settled one ---
    pend_missing = pending[pending["weight_missing"]]
    covered = float(pend_missing["has_prev"].mean()) if len(pend_missing) else float("nan")
    rep["sc005"] = {
        "population": "result-pending started rows whose same-day weight is missing",
        "n_rows": int(len(pend_missing)),
        "n_with_prev_weight": int(pend_missing["has_prev"].sum()),
        "coverage": covered,
        "floor": SC005_FLOOR,
        "pass": bool(len(pend_missing) and covered >= SC005_FLOOR),
        "note": "volatile: weights are published horse by horse as post time approaches, so this "
                "number depends on when it is measured.",
    }

    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=float))
    print(f"settled={rep['settled_rows']} pending={rep['pending_rows']}")
    print(f"race coverage all/some/none: {rep['race_level_coverage']}")
    print(f"no-proxy: {rep['no_proxy_breakdown']['true_debut_no_past_start']} true debut / "
          f"{rep['no_proxy_breakdown']['has_past_start_but_no_weighed_one']} past-start-but-unweighed")
    sc = rep["sc005"]
    print(f"SC-005 (pending cohort): {sc['n_with_prev_weight']}/{sc['n_rows']} = "
          f"{sc['coverage']:.1%} vs floor {SC005_FLOOR:.0%} -> {'PASS' if sc['pass'] else 'FAIL'}")
    return 0 if sc["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
