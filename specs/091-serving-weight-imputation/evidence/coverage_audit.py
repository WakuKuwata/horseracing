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
-- Window functions, not correlated subqueries: the latter are quadratic over ~956k rows and the
-- first version of this audit ran for over an hour before being killed.
WITH runs AS (
  SELECT rh.race_id, rh.horse_id, r.race_date, rh.weight,
         EXISTS (SELECT 1 FROM race_results rr WHERE rr.race_id = rh.race_id) AS settled
  FROM race_horses rh JOIN races r ON r.race_id = rh.race_id
  WHERE rh.entry_status = 'started'
), marked AS (
  SELECT *,
         CASE WHEN weight BETWEEN 200 AND 800 THEN race_date END AS usable_date,
         row_number() OVER (PARTITION BY horse_id ORDER BY race_date, race_id) AS seq
  FROM runs
)
SELECT race_id, horse_id, race_date, settled,
       weight IS NULL AS weight_missing,
       max(usable_date) OVER w AS src_date,
       (seq > 1)                AS has_past_start
FROM marked
WINDOW w AS (PARTITION BY horse_id ORDER BY race_date, race_id
             ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
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
    measurable = len(pend_missing) > 0
    covered = float(pend_missing["has_prev"].mean()) if measurable else None
    rep["sc005"] = {
        "population": "result-pending started rows whose same-day weight is missing",
        "n_rows": int(len(pend_missing)),
        "n_with_prev_weight": int(pend_missing["has_prev"].sum()),
        "coverage": covered,
        "floor": SC005_FLOOR,
        # NOT_MEASURABLE is distinct from FAIL. The pending cohort is volatile: races settle and
        # weights get published horse by horse, so between two runs minutes apart it can go from
        # hundreds of unweighed rows to none. Calling an empty cohort a failure would be reading a
        # verdict out of an absent measurement.
        "status": ("PASS" if covered is not None and covered >= SC005_FLOOR
                   else "FAIL" if measurable else "NOT_MEASURABLE"),
        "reference_measurement": {
            "when": "2026-08-09",
            "n_rows": 451,
            "n_with_prev_weight": 405,
            "coverage": 0.898,
            "note": "taken when a full race day was pending; retained because the live cohort is "
                    "usually too small or too fully-weighed to measure.",
        },
    }

    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=float))
    print(f"settled={rep['settled_rows']} pending={rep['pending_rows']}")
    print(f"race coverage all/some/none: {rep['race_level_coverage']}")
    print(f"no-proxy: {rep['no_proxy_breakdown']['true_debut_no_past_start']} true debut / "
          f"{rep['no_proxy_breakdown']['has_past_start_but_no_weighed_one']} past-start-but-unweighed")
    sc = rep["sc005"]
    if sc["status"] == "NOT_MEASURABLE":
        ref = sc["reference_measurement"]
        print(f"SC-005: NOT_MEASURABLE right now (pending weight-missing rows = 0). "
              f"Reference {ref['when']}: {ref['n_with_prev_weight']}/{ref['n_rows']} = "
              f"{ref['coverage']:.1%} vs floor {SC005_FLOOR:.0%}")
    else:
        print(f"SC-005 (pending cohort): {sc['n_with_prev_weight']}/{sc['n_rows']} = "
              f"{sc['coverage']:.1%} vs floor {SC005_FLOOR:.0%} -> {sc['status']}")
    return 0 if sc["status"] in ("PASS", "NOT_MEASURABLE") else 1


if __name__ == "__main__":
    sys.exit(main())
