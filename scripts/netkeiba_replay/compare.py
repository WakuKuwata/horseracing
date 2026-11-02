"""Compare two replay dumps (old main vs new branch) table by table, column by column."""

from __future__ import annotations

import collections
import json
import sys


def _key(row, key):
    return tuple(json.dumps(row[k], sort_keys=True) for k in key)


def compare(old: dict, new: dict, max_examples: int = 5) -> dict:
    report: dict = {"tables": {}, "ingestion_jobs": None}
    for table, o in old.items():
        if table.startswith("_") or table == "ingestion_jobs":
            continue
        n = new[table]
        common_cols = [c for c in o["columns"] if c in n["columns"]]
        key = o["key"]
        orows = {_key(r, key): r for r in o["rows"]}
        nrows = {_key(r, key): r for r in n["rows"]}
        only_old = sorted(set(orows) - set(nrows))
        only_new = sorted(set(nrows) - set(orows))
        col_diffs: dict[str, list] = collections.defaultdict(list)
        n_diff_rows = 0
        for k in sorted(set(orows) & set(nrows)):
            diff = [c for c in common_cols if orows[k][c] != nrows[k][c]]
            if diff:
                n_diff_rows += 1
            for c in diff:
                col_diffs[c].append({"key": k, "old": orows[k][c], "new": nrows[k][c]})
        report["tables"][table] = {
            "rows_old": len(orows),
            "rows_new": len(nrows),
            "only_old": len(only_old),
            "only_new": len(only_new),
            "only_old_examples": only_old[:max_examples],
            "only_new_examples": only_new[:max_examples],
            "rows_differing": n_diff_rows,
            "columns": {
                c: {"n": len(v), "examples": v[:max_examples]} for c, v in sorted(col_diffs.items())
            },
            "new_only_columns": [c for c in n["columns"] if c not in o["columns"]],
        }
    oj = {(j, s): c for j, s, c in old["ingestion_jobs"]}
    nj = {(j, s): c for j, s, c in new["ingestion_jobs"]}
    report["ingestion_jobs"] = {
        f"{j}/{s}": [oj.get((j, s), 0), nj.get((j, s), 0)]
        for j, s in sorted(set(oj) | set(nj))
        if oj.get((j, s), 0) != nj.get((j, s), 0)
    }
    report["meta"] = {"old": old["_meta"], "new": new["_meta"]}
    return report


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    old = json.load(open(argv[0], encoding="utf-8"))
    new = json.load(open(argv[1], encoding="utf-8"))
    rep = compare(old, new)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
