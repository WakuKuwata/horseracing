"""Parity (specs/138-attention-conditions plan 0.1 G1・D19・T020a): the production ensemble path ==
the research 15 runs.

The frozen statistics come from the research predictions; the picks on screen come from the
production path (``market_ev.build_features`` + ``predict_ensemble`` + ``attention_picks``). This
script runs the production path WITHOUT writing anything over [--from, --to] and compares it with
the research runs by (race_id, horse_id):

  (a) common rows: |p_prod − p_research| < 1e-9 on at least 99.99% of them; every mismatch is
      listed with a reason (p_research = mean over the 15 seeds of EV ÷ odds, EV = 1 + pred/100).
  (b) rows only on one side, counted in both directions, each with a reason; a reason outside
      KNOWN_REASONS (research snapshot timing, the research's exclusion of races without exactly
      one winner, ID re-key / duplicate registration — the 137 parity's known case) fails.
  (c) the rule selections agree both ways: every research (race, horse, rule) that
      ``attention_rules.match_mask`` selects has a production row that the production path
      selects too, and the production path selects no (race, horse, rule) on a common row that
      the research does not (100% — the displayed backtest must describe the production picks).

Features are built once through --to (every feature is as-of the race date, so this equals the
per-day builds of the ops job); --per-day-sample N re-builds N dates the way the job does and
checks that their predictions are identical. Result: specs/138-attention-conditions/evidence/
parity_ens15.json (commit it). Exit 0 only when (a), (b), (c) and the per-day sample pass.

    cd training && uv run python ../scripts/roi_explore/parity_ens15_20261001.py \
        [--from 2025-01-01] [--to 2026-09-30] [--ensemble-dir ABS] [--model-dir ABS] \
        [--per-day-sample 3] [--out ABS]
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

import numpy as np
import pandas as pd
from horseracing_eval import attention_rules as ar
from sqlalchemy import create_engine, text

from horseracing_training import attention_picks, market_ev

ROOT = pathlib.Path(__file__).resolve().parents[2]
RES = ROOT / "artifacts" / "roi_explore" / "results"
ROWS = ROOT / "artifacts" / "market_ev" / "rows_2007.parquet"
ENS_RUNS = {1: "armC_binary_drop-sameday+weightlive_from2007_ens15"} | {
    s: f"armC_binary_seed{s}_drop-sameday+weightlive_from2007_ens15" for s in range(2, 16)}
SINGLE_RUN = "armC_binary_drop-sameday+weightlive_serving_v2_2007"  # = mev-binary-v2 (137)
DEFAULT_ENS_DIR = ROOT / "artifacts" / "market_ev" / ar.DISPLAYED_MARKET_EV_MODEL_VERSION
DEFAULT_MODEL_DIR = ROOT / "artifacts" / "market_ev" / ar.SINGLE_SEED_MODEL_VERSION
DEFAULT_OUT = ROOT / "specs" / "138-attention-conditions" / "evidence" / "parity_ens15.json"
DB_URL = os.environ.get("DATABASE_URL",
                        "postgresql+psycopg://aiuma:aiuma@localhost:15432/horseracing")

TOL = 1e-9
MIN_MATCH_RATE = 0.9999
LIST_CAP = 200
#: one-side rows that are expected (anything else fails acceptance (b))
KNOWN_REASONS = frozenset({
    "after_research_snapshot",             # raced after the research rows' last date
    "changed_after_research_snapshot",     # the entry was ingested / updated after the snapshot
    "research_excludes_races_without_one_winner",  # direct_return_model drops dead_heat races
    "horse_id_rekeyed",                    # same race + horse number, another id (067 merge)
    "duplicate_registration",              # one horse twice in a race (137's known case)
})


def sha(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001 — provenance only
        return "unknown"


# ------------------------------------------------------------------------------------ research


def load_research(d_from: str, d_to: str) -> tuple[pd.DataFrame, dict]:
    """One row per research (race, horse) in range: p_r (mean p̂), ens_ev_r, single_ev_r, odds_r,
    horse_number_r, days_since_last_r. Every seed must cover exactly the same rows."""
    frames, inputs, keys = [], [], None
    for s, tag in ENS_RUNS.items():
        f = RES / tag / "predictions.parquet"
        v = pd.read_parquet(f, columns=["race_id", "horse_id", "race_date", "odds", "pred"])
        inputs.append({"seed": s, "path": str(f.relative_to(ROOT)), "sha256": sha(f),
                       "row_count": int(len(v))})
        v = v[(v["race_date"] >= d_from) & (v["race_date"] <= d_to)]
        v = v.sort_values(["race_id", "horse_id"]).reset_index(drop=True)
        k = list(zip(v["race_id"], v["horse_id"], strict=True))
        if keys is None:
            keys = k
        elif k != keys:
            raise SystemExit(f"FAIL: research seed {s} covers different rows than seed 1")
        frames.append(v)
    base = frames[0][["race_id", "horse_id", "race_date", "odds"]].rename(
        columns={"odds": "odds_r"})
    ev = np.vstack([1.0 + fr["pred"].to_numpy(float) / 100.0 for fr in frames])
    base["ens_ev_r"] = ev.mean(axis=0)  # = odds × mean p̂ (the freeze script's definition)
    base["p_r"] = np.mean(ev / base["odds_r"].to_numpy(float), axis=0)
    f = RES / SINGLE_RUN / "predictions.parquet"
    single = pd.read_parquet(f, columns=["race_id", "horse_id", "pred"])
    single_input = {"path": str(f.relative_to(ROOT)), "sha256": sha(f),
                    "row_count": int(len(single))}
    single["single_ev_r"] = 1.0 + single["pred"].to_numpy(float) / 100.0
    base = base.merge(single[["race_id", "horse_id", "single_ev_r"]], on=["race_id", "horse_id"],
                      how="left", validate="one_to_one")
    rows = pd.read_parquet(ROWS, columns=["race_id", "horse_id", "race_date", "horse_number",
                                          "days_since_last"])
    # the research snapshot's last race date (rows_2007.parquet was exported through it)
    max_race_date = str(rows["race_date"].max())
    rows = rows.drop(columns=["race_date"]).rename(
        columns={"horse_number": "horse_number_r", "days_since_last": "days_since_last_r"})
    base = base.merge(rows, on=["race_id", "horse_id"], how="left", validate="one_to_one")
    snapshot = datetime.datetime.fromtimestamp(ROWS.stat().st_mtime, tz=datetime.UTC)
    meta = {"ens_prediction_inputs": inputs, "single_prediction_input": single_input,
            "rows": {"path": str(ROWS.relative_to(ROOT)), "sha256": sha(ROWS),
                     "mtime_utc": snapshot.isoformat()},
            "snapshot_utc": snapshot.isoformat(),
            "max_race_date": max_race_date}
    return base, meta


def research_matches(research: pd.DataFrame) -> pd.DataFrame:
    """(race_id, horse_id, rule_id) the research side selects (the freeze script's inputs)."""
    out = []
    for d in ar.RULE_DEFINITIONS:
        mask = ar.match_mask(d, ens_ev=research["ens_ev_r"].to_numpy(float),
                             single_ev=research["single_ev_r"].to_numpy(float),
                             odds=research["odds_r"].to_numpy(float),
                             days_since_last=research["days_since_last_r"].to_numpy(float))
        hit = research.loc[mask, ["race_id", "horse_id"]].copy()
        hit["rule_id"] = d.id
        out.append(hit)
    return pd.concat(out, ignore_index=True)


# ---------------------------------------------------------------------------------- production


def production(conn, d_from: str, d_to: str, ens_model, single_model):
    """The ops job's path without writes: features, both versions, the same filters, the
    production rule matches (``attention_picks.candidate_picks``)."""
    raw = market_ev.load_rows(conn, through=d_to)
    feats = market_ev.build_features(raw)
    dates = feats["race_date"].dt.date
    target = feats[(dates >= datetime.date.fromisoformat(d_from))
                   & (dates <= datetime.date.fromisoformat(d_to))]
    pred_single, inv_single = market_ev.drop_invalid_odds_races(
        market_ev.predict(single_model, target))
    pred_ens, inv_ens = market_ev.drop_invalid_odds_races(
        market_ev.predict_ensemble(ens_model, target))
    if inv_ens != inv_single or market_ev._row_keys(pred_ens) != market_ev._row_keys(pred_single):
        raise SystemExit("FAIL: production versions disagree on the computed rows")
    by_race, skipped = attention_picks.candidate_picks(pred_ens, pred_single, target)
    matched = pd.DataFrame(
        [(p["race_id"], p["horse_id"], p["rule_id"]) for ps in by_race.values() for p in ps],
        columns=["race_id", "horse_id", "rule_id"],
    )
    winners = target.groupby("race_id")["won"].sum()
    prod = pred_ens[["race_id", "horse_id", "horse_number", "odds_used", "win_prob"]].rename(
        columns={"horse_number": "horse_number_p", "odds_used": "odds_p", "win_prob": "p_p"})
    prod["race_date"] = prod["race_id"].map(
        target.drop_duplicates("race_id").set_index("race_id")["race_date"].dt.strftime(
            "%Y-%m-%d"))
    prod["one_winner"] = prod["race_id"].map(winners).fillna(0).astype(int).eq(1)
    return prod, matched, {"invalid_odds_races": sorted(inv_single),
                           "skipped_no_horse_number": skipped,
                           "race_ok_false_races": sorted(
                               set(target.loc[~target["race_ok"].astype(bool), "race_id"]))}


def entries(conn, race_ids: list[str]) -> pd.DataFrame:
    """Every race_horses row (any status) of the races involved, with its timestamps."""
    if not race_ids:
        return pd.DataFrame(columns=["race_id", "horse_id", "horse_number", "entry_status",
                                     "created_at", "updated_at"])
    return pd.read_sql(
        text("select race_id, horse_id, horse_number, entry_status, created_at, updated_at "
             "from race_horses where race_id = any(:ids)"),
        conn, params={"ids": race_ids},
    )


# ---------------------------------------------------------------------------------- comparison


def _race_flags(research: pd.DataFrame, prod: pd.DataFrame, ent: pd.DataFrame) -> dict:
    """race_id → {"duplicate", "rekeyed"} flags derived from both sides' horse numbers."""
    flags: dict[str, set[str]] = {}
    for frame, col in ((research, "horse_number_r"), (prod, "horse_number_p"),
                       (ent[ent["entry_status"] == "started"], "horse_number")):
        dup = frame[frame.duplicated(["race_id", col], keep=False) & frame[col].notna()]
        for rid in dup["race_id"].unique():
            flags.setdefault(str(rid), set()).add("duplicate_registration")
    r_keys = research[["race_id", "horse_number_r", "horse_id"]].rename(
        columns={"horse_number_r": "n", "horse_id": "h_r"})
    p_keys = prod[["race_id", "horse_number_p", "horse_id"]].rename(
        columns={"horse_number_p": "n", "horse_id": "h_p"})
    both = r_keys.merge(p_keys, on=["race_id", "n"], how="inner")
    for rid in both.loc[both["h_r"] != both["h_p"], "race_id"].unique():
        flags.setdefault(str(rid), set()).add("horse_id_rekeyed")
    return flags


def one_side_reason(row, *, side: str, flags: dict, research_max_date: str,
                    snapshot: pd.Timestamp, ent_idx: dict, research_races: set,
                    prod_races: set) -> str:
    rid = str(row["race_id"])
    f = flags.get(rid, set())
    if "horse_id_rekeyed" in f:
        return "horse_id_rekeyed"
    if "duplicate_registration" in f:
        return "duplicate_registration"
    if side == "production":
        if str(row["race_date"]) > research_max_date:
            return "after_research_snapshot"
        if not bool(row["one_winner"]):
            return "research_excludes_races_without_one_winner"
    entry = ent_idx.get((rid, str(row["horse_id"])))
    if entry is not None and max(entry["created_at"], entry["updated_at"]) > snapshot:
        return "changed_after_research_snapshot"
    if side == "research":
        if rid not in prod_races:
            return "race_not_computed_in_production"
        return "horse_not_computed_in_production"
    if rid not in research_races:
        return "race_not_in_research"
    return "horse_not_in_research"


def mismatch_reason(row, *, flags: dict, odds_changed_races: set) -> str:
    rid = str(row["race_id"])
    if flags.get(rid):
        return "race_field_differs:" + ",".join(sorted(flags[rid]))
    if float(row["odds_r"]) != float(row["odds_p"]):
        return "odds_differ"
    if rid in odds_changed_races:
        return "race_odds_differ"
    return "unexplained"


def compare(research: pd.DataFrame, prod: pd.DataFrame, ent: pd.DataFrame,
            r_matched: pd.DataFrame, p_matched: pd.DataFrame, *, research_max_date: str,
            snapshot: pd.Timestamp) -> dict:
    """The three acceptance checks. Pure (no I/O) so it can be exercised on synthetic frames."""
    m = research.merge(prod, on=["race_id", "horse_id"], how="outer", indicator=True,
                       suffixes=("", "_prod"))
    if "race_date_prod" in m:
        m["race_date"] = m["race_date"].fillna(m["race_date_prod"])
    flags = _race_flags(research, prod, ent)
    ent_idx = {(str(r.race_id), str(r.horse_id)): {"created_at": r.created_at,
                                                    "updated_at": r.updated_at}
               for r in ent.itertuples(index=False)}
    research_races, prod_races = set(research["race_id"]), set(prod["race_id"])

    common = m[m["_merge"] == "both"].copy()
    common["dp"] = (common["p_p"].astype(float) - common["p_r"].astype(float)).abs()
    ok = common["dp"] < TOL
    odds_changed = set(common.loc[common["odds_r"].astype(float)
                                  != common["odds_p"].astype(float), "race_id"])
    bad = common[~ok].copy()
    bad["reason"] = [mismatch_reason(r, flags=flags, odds_changed_races=odds_changed)
                     for _, r in bad.iterrows()]
    rate = float(ok.mean()) if len(common) else 0.0

    sides = {}
    one_side_reasons: dict[tuple[str, str], str] = {}
    for side, tag in (("research", "left_only"), ("production", "right_only")):
        only = m[m["_merge"] == tag].copy()
        only["reason"] = [
            one_side_reason(r, side=side, flags=flags, research_max_date=research_max_date,
                            snapshot=snapshot, ent_idx=ent_idx, research_races=research_races,
                            prod_races=prod_races)
            for _, r in only.iterrows()
        ]
        if side == "research":
            one_side_reasons = dict(zip(zip(only["race_id"], only["horse_id"], strict=True),
                                        only["reason"], strict=True))
        unknown = only[~only["reason"].isin(KNOWN_REASONS)]
        sides[side] = {
            "count": int(len(only)),
            "by_reason": {k: int(v) for k, v in only["reason"].value_counts().sort_index().items()},
            "unknown": int(len(unknown)),
            "rows": _records(only, ["race_id", "horse_id", "race_date", "reason"]),
        }

    prod_keys = set(zip(prod["race_id"], prod["horse_id"], strict=True))
    common_keys = set(zip(common["race_id"], common["horse_id"], strict=True))
    p_set = set(zip(p_matched["race_id"], p_matched["horse_id"], p_matched["rule_id"],
                    strict=True))
    rules: dict[str, dict] = {}
    failures = []
    for d in ar.RULE_DEFINITIONS:
        rm = r_matched[r_matched["rule_id"] == d.id]
        n = len(rm)
        has_row = agree = 0
        for rid, hid in zip(rm["race_id"], rm["horse_id"], strict=True):
            if (rid, hid) not in prod_keys:
                # strict (plan 0.1 (c)): even an explained missing row fails; the reason is kept
                # so the operator can see whether it is a known one (re-key / duplicate)
                failures.append({"rule_id": d.id, "race_id": rid, "horse_id": hid,
                                 "reason": "no_production_row",
                                 "row_reason": one_side_reasons.get((rid, hid))})
                continue
            has_row += 1
            if (rid, hid, d.id) in p_set:
                agree += 1
            else:
                failures.append({"rule_id": d.id, "race_id": rid, "horse_id": hid,
                                 "reason": "production_not_selected"})
        r_set = set(zip(rm["race_id"], rm["horse_id"], strict=True))
        # the other direction: production over-selecting on a row both sides computed
        extra = sorted((rid, hid) for (rid, hid, rule) in p_set
                       if rule == d.id and (rid, hid) not in r_set and (rid, hid) in common_keys)
        failures.extend({"rule_id": d.id, "race_id": rid, "horse_id": hid,
                         "reason": "research_not_selected"} for rid, hid in extra)
        rules[d.id] = {"research_selected": n, "production_has_row": has_row,
                       "production_agrees": agree, "agreement_rate": agree / n if n else None,
                       "production_only_selected_on_common_rows": len(extra)}

    acceptance = {
        "a_common_match_rate": rate >= MIN_MATCH_RATE,
        "b_one_side_rows_explained": sides["research"]["unknown"] == 0
        and sides["production"]["unknown"] == 0,
        "c_rule_selections_agree": not failures,
    }
    acceptance["passed"] = all(acceptance.values())
    return {
        "common": {"rows": int(len(common)), "within_tol": int(ok.sum()), "tol": TOL,
                   "match_rate": rate, "min_match_rate": MIN_MATCH_RATE,
                   "max_abs_dp": float(common["dp"].max()) if len(common) else None},
        "mismatches": {
            "count": int(len(bad)),
            "by_reason": {k: int(v) for k, v in bad["reason"].value_counts().sort_index().items()},
            "rows": _records(bad, ["race_id", "horse_id", "race_date", "odds_r", "odds_p", "p_r",
                                   "p_p", "dp", "reason"]),
        },
        "research_only": sides["research"],
        "production_only": sides["production"],
        "rules": rules,
        "rule_failures": failures[:LIST_CAP],
        "rule_failure_count": len(failures),
        "rule_failures_by_reason": {k: sum(1 for f in failures if f["reason"] == k)
                                    for k in sorted({f["reason"] for f in failures})},
        "rule_failures_without_known_row_reason": sum(
            1 for f in failures if f.get("row_reason") not in KNOWN_REASONS),
        "known_reasons": sorted(KNOWN_REASONS),
        "acceptance": acceptance,
    }


def _records(frame: pd.DataFrame, cols: list[str]) -> list[dict]:
    out = []
    for rec in frame.sort_values(["race_id", "horse_id"])[cols].head(LIST_CAP).to_dict("records"):
        out.append({k: (None if isinstance(v, float) and np.isnan(v) else
                        v.item() if isinstance(v, np.generic) else v) for k, v in rec.items()})
    return out


def per_day_sample(conn, prod_all: pd.DataFrame, n: int, ens_model) -> dict:
    """Re-run N dates exactly like the ops job (features through that date only)."""
    days = sorted(prod_all["race_date"].dropna().unique())
    if n <= 0 or not days:
        return {"days": [], "max_abs_dp": None, "rows": 0, "passed": True}
    pick = [days[int(i)] for i in np.linspace(0, len(days) - 1, num=min(n, len(days)))]
    worst, rows = 0.0, 0
    for day in pick:
        raw = market_ev.load_rows(conn, through=day)
        feats = market_ev.build_features(raw)
        target = feats[feats["race_date"].dt.strftime("%Y-%m-%d") == day]
        pred, _ = market_ev.drop_invalid_odds_races(market_ev.predict_ensemble(ens_model, target))
        same_day = prod_all[prod_all["race_date"] == day]
        j = pred.merge(same_day, on=["race_id", "horse_id"], how="outer", indicator=True)
        if (j["_merge"] != "both").any():
            return {"days": pick, "max_abs_dp": None, "rows": rows, "passed": False,
                    "failed_day": day, "reason": "row sets differ"}
        worst = max(worst, float((j["win_prob"] - j["p_p"]).abs().max()))
        rows += len(j)
    return {"days": pick, "max_abs_dp": worst, "rows": rows, "passed": worst == 0.0}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d_from", default="2025-01-01")
    ap.add_argument("--to", dest="d_to", default="2026-09-30")
    ap.add_argument("--ensemble-dir", default=str(DEFAULT_ENS_DIR))
    ap.add_argument("--model-dir", default=str(DEFAULT_MODEL_DIR))
    ap.add_argument("--per-day-sample", type=int, default=3)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    t0 = time.time()
    edir = market_ev.validate_model_dir(args.ensemble_dir, flag="--ensemble-dir")
    mdir = market_ev.validate_model_dir(args.model_dir)
    ens_model = market_ev.EnsembleMarketEvModel.load(edir, edir.name)
    single_model = market_ev.MarketEvModel.load(mdir, mdir.name)

    research, rmeta = load_research(args.d_from, args.d_to)
    r_matched = research_matches(research)
    with create_engine(DB_URL).connect() as conn:
        prod, p_matched, pmeta = production(conn, args.d_from, args.d_to, ens_model, single_model)
        ent = entries(conn, sorted(set(research["race_id"]) | set(prod["race_id"])))
        sample = per_day_sample(conn, prod, args.per_day_sample, ens_model)
    result = compare(research, prod, ent, r_matched, p_matched,
                     research_max_date=rmeta["max_race_date"],
                     snapshot=pd.Timestamp(rmeta["snapshot_utc"]))
    result["acceptance"]["per_day_sample"] = sample["passed"]
    result["acceptance"]["passed"] = result["acceptance"]["passed"] and sample["passed"]
    manifests = {p.name: sha(p) for p in sorted(edir.glob("ensemble_*.json"))}
    report = {
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "git_commit": git_commit(),
        "script_sha256": sha(pathlib.Path(__file__)),
        "range": {"from": args.d_from, "to": args.d_to},
        "research": rmeta | {
            "rows_in_range": int(len(research)),
            "selected": {k: int(v) for k, v in
                         r_matched["rule_id"].value_counts().sort_index().items()},
        },
        "production": {
            "ensemble_dir": str(edir), "ensemble_version": ens_model.version,
            "ensemble_spec_sha256": sha(edir / market_ev.ENSEMBLE_SPEC_NAME),
            "manifests": manifests, "logic_version": market_ev.ENSEMBLE_LOGIC_VERSION,
            "model_dir": str(mdir), "single_version": single_model.version,
            "rows": int(len(prod)), "races": int(prod["race_id"].nunique()), **pmeta,
            "rule_set_version": ar.RULE_SET_VERSION,
            "definitions_sha256": ar.definitions_sha256(),
        },
        "per_day_sample": sample,
        **result,
        "numpy_version": np.__version__,
        "elapsed_s": round(time.time() - t0, 1),
    }
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True, default=str))
    summary = {"common": result["common"], "acceptance": result["acceptance"],
               "research_only": result["research_only"]["by_reason"],
               "production_only": result["production_only"]["by_reason"], "out": str(out)}
    print(json.dumps(summary, ensure_ascii=False, default=str))
    return 0 if result["acceptance"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
