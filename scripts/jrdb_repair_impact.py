"""178 T058 — the repair's effect on the expected-return model, measured the 139 way (FR-013).

READ ONLY. Nothing here writes to any DB; ``decision_quotes`` and ``attention_picks`` are never
touched (the repaired-input view of a past judgement is a counterfactual, reported apart).

Two passes, each against a DB (the second one is the REPAIRED copy):

    python scripts/jrdb_repair_impact.py pre  --db-url URL_BEFORE --from F --to T --out DIR
    python scripts/jrdb_repair_impact.py post --db-url URL_AFTER  --from F --to T --out DIR

``pre`` saves the target races' own odds/popularity and the ens15 / single expected returns +
S1〜S5 membership. ``post`` recomputes on the repaired DB twice: raw, and with the TARGET races'
own odds/popularity held at the pre values (isolating the shift that comes only through past-race
inputs, 139's ``past_inputs_only``), then writes ``repair-impact.json`` with the median / p90 of
the expected-return difference and the S1〜S5 membership diffs. Model artifacts are read from
``--model-root`` (default ``artifacts/market_ev``), the same ones the product uses.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_MODEL_ROOT = ROOT / "artifacts" / "market_ev"
RULES = ("S1", "S2", "S3", "S4", "S5")


def _has_rule(col: pd.Series, rid: str) -> pd.Series:
    return col.fillna("").str.split(",").apply(lambda x, r=rid: r in x)


def compare(pre: pd.DataFrame, post: pd.DataFrame, label: str) -> dict:
    """139's compare(): paired (race, horse) rows; EV difference stats + rule-set diffs."""

    j = pre.merge(post, on=["race_id", "horse_id"], suffixes=("_pre", "_post"))
    d = j["expected_return_post"].astype(float) - j["expected_return_pre"].astype(float)
    sets = {}
    for rid in RULES:
        pa = set(j.loc[_has_rule(j["rules_pre"], rid), "horse_id"])
        pb = set(j.loc[_has_rule(j["rules_post"], rid), "horse_id"])
        sets[rid] = {
            "pre": len(pa),
            "post": len(pb),
            "both": len(pa & pb),
            "only_pre": len(pa - pb),
            "only_post": len(pb - pa),
        }
    if j.empty:
        stats = {
            k: None
            for k in (
                "er_diff_abs_mean",
                "er_diff_abs_median",
                "er_diff_abs_p90",
                "er_diff_abs_max",
                "er_diff_mean",
                "share_changed_gt_0.01",
            )
        }
    else:
        stats = {
            "er_diff_abs_mean": float(d.abs().mean()),
            "er_diff_abs_median": float(d.abs().median()),
            "er_diff_abs_p90": float(d.abs().quantile(0.9)),
            "er_diff_abs_max": float(d.abs().max()),
            "er_diff_mean": float(d.mean()),
            "share_changed_gt_0.01": float((d.abs() > 0.01).mean()),
        }
    return {
        "label": label,
        "horses": int(len(j)),
        "races": int(j["race_id"].nunique()),
        **stats,
        "rule_sets": sets,
    }


def _run(me, ar, single, ens, raw: pd.DataFrame, d_from: str, d_to: str) -> pd.DataFrame:
    feats = me.build_features(raw)
    d = feats["race_date"].dt.date.astype(str)
    target = feats[(d >= d_from) & (d <= d_to)]
    ps = me.predict(single, target)
    pe = me.predict_ensemble(ens, target)
    m = pe[
        ["race_id", "horse_id", "horse_number", "odds_used", "win_prob", "expected_return"]
    ].merge(
        ps[["race_id", "horse_id", "expected_return"]].rename(
            columns={"expected_return": "single_er"}
        ),
        on=["race_id", "horse_id"],
    )
    days = target.set_index(["race_id", "horse_id"])["days_since_last"]
    m["days_since_last"] = [days.get((r, h)) for r, h in zip(m.race_id, m.horse_id, strict=True)]
    m["rules"] = [
        ",".join(
            ar.applicable_rules(
                ens_ev=float(e),
                single_ev=float(s),
                odds=float(o),
                days_since_last=None if pd.isna(g) else float(g),
            )
        )
        for e, s, o, g in zip(
            m.expected_return, m.single_er, m.odds_used, m.days_since_last, strict=True
        )
    ]
    return m


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("mode", choices=["pre", "post"])
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--from", dest="d_from", required=True)
    ap.add_argument("--to", dest="d_to", required=True)
    ap.add_argument(
        "--out", required=True, help="directory (pre writes parquet here, post reads it)"
    )
    ap.add_argument("--model-root", default=str(DEFAULT_MODEL_ROOT))
    ap.add_argument("--single", default="mev-binary-v2")
    ap.add_argument("--ensemble", default="mev-ens15-v1")
    args = ap.parse_args(argv)

    from horseracing_eval import attention_rules as ar
    from horseracing_training import market_ev as me
    from sqlalchemy import create_engine, text

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    root = pathlib.Path(args.model_root)
    single = me.MarketEvModel.load(root / args.single, args.single)
    ens = me.EnsembleMarketEvModel.load(root / args.ensemble, args.ensemble)
    eng = create_engine(args.db_url)
    with eng.connect() as c:
        c.execute(text("SET TRANSACTION READ ONLY"))
        raw = me.load_rows(c, through=args.d_to)
        c.rollback()
    dates = pd.to_datetime(raw["race_date"]).dt.date.astype(str)
    tmask = (dates >= args.d_from) & (dates <= args.d_to)
    if args.mode == "pre":
        raw.loc[tmask, ["race_id", "horse_id", "odds", "popularity"]].to_parquet(
            out / "pre_target_inputs.parquet"
        )
        _run(me, ar, single, ens, raw, args.d_from, args.d_to).to_parquet(out / "pre.parquet")
        print(f"pre: target rows {int(tmask.sum())} → {out}")
        return 0
    post_raw = _run(me, ar, single, ens, raw, args.d_from, args.d_to)
    post_raw.to_parquet(out / "post_raw.parquet")
    pre_in = pd.read_parquet(out / "pre_target_inputs.parquet").set_index(["race_id", "horse_id"])
    raw2 = raw.copy()
    idx = list(zip(raw2.loc[tmask, "race_id"], raw2.loc[tmask, "horse_id"], strict=True))
    raw2.loc[tmask, "odds"] = [pre_in["odds"].get(k) for k in idx]
    raw2.loc[tmask, "popularity"] = [pre_in["popularity"].get(k) for k in idx]
    post = _run(me, ar, single, ens, raw2, args.d_from, args.d_to)
    post.to_parquet(out / "post_fixed_target.parquet")
    pre = pd.read_parquet(out / "pre.parquet")
    res = {
        "target_races": f"{args.d_from}..{args.d_to}",
        "models": {"single": args.single, "ensemble": args.ensemble},
        "past_inputs_only": compare(pre, post, "target odds/popularity held at pre-repair values"),
        "raw": compare(pre, post_raw, "raw (target races' own repaired values too)"),
        "note": "decision_quotes / attention_picks are not rewritten (FR-013); "
        "this is a counterfactual",
    }
    (out / "repair-impact.json").write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
