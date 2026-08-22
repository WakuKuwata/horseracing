"""Feature 097 T014: coverage audit of the new columns, year × column, from the materialised
parquet (features-022). SC-002: on 2026 rows that HAVE a past race, both columns >= 95% non-missing.
"""

from __future__ import annotations

import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
COLS = ["asof_rel_early_mid_avg", "asof_rel_early_mid_best"]


def main() -> int:
    path = ROOT / "artifacts/features.parquet"
    df = pd.read_parquet(path)
    df["year"] = df["race_id"].astype(str).str[:4].astype(int)
    past = df["has_past_race"].astype(float) > 0 if "has_past_race" in df.columns \
        else pd.to_numeric(df["career_starts"], errors="coerce").fillna(0) > 0
    lines = [f"rows={len(df):,}  years={df.year.min()}..{df.year.max()}", "",
             f"{'year':<6}{'n':>8}" + "".join(f"{c:>28}" for c in COLS) + "   (all rows / has_past_race rows)"]
    for y, g in df.groupby("year"):
        gp = g[past.loc[g.index]]
        lines.append(f"{y:<6}{len(g):>8}" + "".join(
            f"{g[c].notna().mean()*100:>13.1f}% /{gp[c].notna().mean()*100:>6.1f}%" for c in COLS))
    y26 = df[(df.year == 2026) & past]
    rates = {c: float(y26[c].notna().mean()) for c in COLS}
    ok = all(r >= 0.95 for r in rates.values())
    lines += ["", f"2026 has_past_race rows n={len(y26):,}: " +
              ", ".join(f"{c}={r*100:.1f}%" for c, r in rates.items()),
              "SC-002 >= 95%: " + ("PASS" if ok else "FAIL")]
    out = "\n".join(lines); print(out)
    (ROOT / "specs/097-early-mid-pace/evidence-coverage.md").write_text(
        "# 097 T014 coverage audit (SC-002)\n\n```\n" + out + "\n```\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
