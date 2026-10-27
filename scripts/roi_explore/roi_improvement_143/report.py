"""Same-policy profits and probability quality, with day-paired uncertainty."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    sys.modules[name] = obj
    spec.loader.exec_module(obj)
    return obj


models = load_module("roi143_report_models", HERE / "models.py")
policies = load_module("roi143_report_policies", HERE.parent / "roi_improvement_142/policies.py")
research = load_module("roi143_report_run", HERE / "run.py")


def score(frame: pd.DataFrame):
    d = frame.sort_values(["race_date", "race_id", "horse_number", "horse_id"]).reset_index(drop=True)
    check = d.sort_values(["race_id", "horse_number"])
    models.base.validate_races(check)
    if not np.isfinite(d.p).all() or not d.p.between(0, 1).all():
        raise ValueError("Invalid ensemble probabilities")
    p = np.clip(d.p.to_numpy(float), 1e-15, 1-1e-15)
    decisions = d[models.PRED_KEYS].copy()
    decisions["p_raw"] = p
    q = 1 / d.odds
    decisions["p_mix"] = q / q.groupby(d.race_id).transform("sum")
    allocation = policies.allocate(decisions, "raw_ev_gt_1_2")
    allocation["race_date"] = d.race_date.to_numpy()
    allocation["won"] = d.won.to_numpy()
    allocation["payout_yen"] = allocation.units.to_numpy() * d.won.to_numpy(float) * d.odds.to_numpy(float) * 100
    races = allocation.groupby(["race_date", "race_id"], sort=True).agg(
        stake_yen=("stake_yen", "sum"), payout_yen=("payout_yen", "sum")).reset_index()
    races["profit_yen"] = races.payout_yen - races.stake_yen
    races["budget_yen"] = 1000
    pn = p / pd.Series(p).groupby(d.race_id).transform("sum").to_numpy()
    nll = pd.Series(-np.log(pn[d.won.astype(bool)]), index=d.loc[d.won.astype(bool), "race_id"])
    races["winner_nll"] = races.race_id.map(nll)
    bought = allocation.loc[allocation.stake_yen > 0].copy()
    summary = {"quality": models.base.metrics(check, check.p.to_numpy()),
               "policy": policies._point(races, bought), "by_year": {}, "ev_band_calibration": {}}
    ev = p * d.odds.to_numpy(float)
    for label, low, high in (("le1", -np.inf, 1.), ("1to1_2", 1., 1.2), ("gt1_2", 1.2, np.inf)):
        mask = (ev > low) & (ev <= high)
        sum_p = float(p[mask].sum())
        wins = int(d.won.to_numpy()[mask].sum())
        summary["ev_band_calibration"][label] = dict(n=int(mask.sum()), won=wins, sum_p=sum_p,
                                                     won_over_p=wins/sum_p if sum_p else None)
    for year, sub in d.groupby("year"):
        subset = sub.sort_values(["race_id", "horse_number"])
        summary["by_year"][str(year)] = {
            "quality": models.base.metrics(subset, subset.p.to_numpy()),
            "policy": policies._point(races[races.race_id.isin(sub.race_id)], bought[bought.race_id.isin(sub.race_id)])}
    return summary, races, bought


def paired(tables: dict, repetitions=4000, seed=20261019, family_size=3):
    baseline = tables["baseline"]
    days = sorted(baseline.race_date.unique())
    rng = np.random.default_rng(seed)
    draws = rng.multinomial(len(days), np.full(len(days), 1 / len(days)), size=repetitions)
    result = {}
    base_daily = baseline.groupby("race_date").agg(
        stake=("stake_yen", "sum"), payout=("payout_yen", "sum"), budget=("budget_yen", "sum"),
        profit=("profit_yen", "sum"), nll=("winner_nll", "sum"), races=("race_id", "size")).reindex(days)
    for arm, candidate in tables.items():
        if arm in ("baseline", "current_reference"):
            continue
        if not baseline[["race_id", "race_date", "budget_yen"]].equals(candidate[["race_id", "race_date", "budget_yen"]]):
            raise ValueError("Paired evaluation populations differ")
        daily = candidate.groupby("race_date").agg(stake=("stake_yen", "sum"), payout=("payout_yen", "sum"),
                    budget=("budget_yen", "sum"), profit=("profit_yen", "sum"), nll=("winner_nll", "sum")).reindex(days)
        nll_delta = (draws @ (daily.nll-base_daily.nll)) / (draws @ base_daily.races)
        profit_delta = (draws @ (daily.profit-base_daily.profit)) / (draws @ base_daily.budget)
        base_stake, new_stake = draws @ base_daily.stake, draws @ daily.stake
        roi_delta = np.divide(draws @ daily.payout, new_stake, out=np.full(repetitions, np.nan), where=new_stake > 0) - np.divide(
            draws @ base_daily.payout, base_stake, out=np.full(repetitions, np.nan), where=base_stake > 0)
        point = {
            "winner_nll": float((daily.nll-base_daily.nll).sum()/base_daily.races.sum()),
            "profit_per_budget": float((daily.profit-base_daily.profit).sum()/base_daily.budget.sum()),
            "deployed_roi": (float(daily.payout.sum()/daily.stake.sum()-base_daily.payout.sum()/base_daily.stake.sum())
                             if daily.stake.sum() and base_daily.stake.sum() else None)}
        result[arm] = {}
        for metric, values in (("winner_nll", nll_delta), ("profit_per_budget", profit_delta), ("deployed_roi", roi_delta)):
            result[arm][metric] = dict(difference=point[metric], ci95=policies._interval(values, .05),
                bonferroni_familywise95_ci=policies._interval(values, .05/family_size),
                valid_replicates=int(np.isfinite(values).sum()))
    return dict(results=result, repetitions=repetitions, seed=seed, family_size_per_metric=family_size,
                unit="all eligible race dates, same draws and population", vs="baseline")


def ensemble(out: Path, arm: str, protocol: dict):
    parts, receipts = [], []
    directory = "weighted_models" if arm == "weighted_baseline" else "models"
    for year in protocol["window"]["model_outer"]:
        first, total = None, None
        for seed in protocol["seeds"]:
            fit = out / directory / f"seed_{seed:02d}" / f"year_{year}" / arm
            rec = json.loads((fit / "receipt.json").read_text())
            recipe = rec["recipe"]
            if recipe["seed"] != seed or recipe["arm"] != arm or recipe["identity"]["protocol_sha256"] != models.sha(out / "protocol.json"):
                raise ValueError("Saved fit identity differs")
            if recipe["train_end"] >= recipe["target_start"] or recipe["rounds"] != 300:
                raise ValueError("Saved fit chronology/rounds differ")
            for name, digest in rec["hashes"].items():
                if models.sha(fit / name) != digest:
                    raise ValueError("Saved fit artifact changed")
            pred = pd.read_parquet(fit / "predictions.parquet")
            if not pred.year.eq(year).all():
                raise ValueError("Saved fit year differs")
            if first is None:
                first = pred[models.PRED_KEYS].copy()
                total = np.zeros(len(pred))
            elif not first.equals(pred[models.PRED_KEYS]):
                raise ValueError("Saved seed populations differ")
            total += pred.p.to_numpy(float)
            receipts.append(dict(year=year, seed=seed, arm=arm, elapsed_s=rec["elapsed_s"],
                actual_iterations=rec["actual_iterations"], roundtrip_max_abs_raw_difference=rec["roundtrip_max_abs_raw_difference"],
                weights=recipe["weights"], receipt_sha256=models.sha(fit / "receipt.json")))
        first["p"] = total / len(protocol["seeds"])
        parts.append(first)
    return pd.concat(parts, ignore_index=True), receipts


def markdown(result: dict):
    labels = {"current_reference": "現行保存15seed（参照）", "baseline": "同条件の基準77特徴", "speed": "時計・速度10特徴追加",
              "pace": "展開・コーナー14特徴追加", "weighted_baseline": "購入判断付近の重み付き77特徴"}
    lines = ["# 143 既存特徴による回収率比較", "", "2019〜2026-06-21、全案同じ対象・15seed平均・raw EV>1.2・1レース予算1,000円。",
             "確定オッズ由来の仮想精算であり、発走前の実行収益ではない。", "",
             "| 条件 | 購入数 | 投資額 | 払戻 | 利益 | 回収率 | winner NLL |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for arm, values in result["arms"].items():
        p = values["policy"]
        roi = f"{p['deployed_roi']*100:.2f}%" if p["deployed_roi"] is not None else "—"
        lines.append(f"| {labels[arm]} | {p['n_bets']:,} | {p['stake_yen']:,.0f}円 | {p['payout_yen']:,.0f}円 | {p['profit_yen']:+,.0f}円 | {roi} | {values['quality']['winner_nll']:.6f} |")
    lines += ["", "差の区間は開催日単位paired bootstrap4,000回、事前固定3候補にBonferroni補正。利益差の主指標は共通予算に対する差。", ""]
    for arm, comp in result["paired"]["results"].items():
        ci = comp["profit_per_budget"]["bonferroni_familywise95_ci"]
        lines.append(f"- {labels[arm]}: 基準比の予算利益差 {comp['profit_per_budget']['difference']*100:+.4f}ポイント、補正95%区間 [{ci[0]*100:+.4f}, {ci[1]*100:+.4f}]ポイント。")
    lines += ["", "## 年別", "", "| 年 | 条件 | 利益 | 回収率 |", "|---|---|---:|---:|"]
    for year in result["arms"]["baseline"]["by_year"]:
        for arm, values in result["arms"].items():
            p = values["by_year"][year]["policy"]
            roi = f"{p['deployed_roi']*100:.2f}%" if p["deployed_roi"] is not None else "—"
            lines.append(f"| {year} | {labels[arm]} | {p['profit_yen']:+,.0f}円 | {roi} |")
    lines += ["", "## 払戻依存・研究上の限界", ""]
    for arm, values in result["arms"].items():
        p = values["policy"]
        lines.append(f"- {labels[arm]}: 上位5払戻をゼロにした利益 {p['profit_without_top5_payouts_yen']:+,.0f}円、最大DD {p['max_race_settlement_drawdown_yen']:,.0f}円。")
    lines += ["", *[f"- {item}" for item in result["limitations"]], "", f"Protocol SHA256: `{result['protocol_sha256']}`", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("stage", choices=["features", "weighted"])
    parser.add_argument("--out", type=Path, default=research.OUT)
    args = parser.parse_args()
    out = args.out
    path = out / "protocol.json"
    proto = json.loads(path.read_text())
    models.verify_protocol(path, models.ROOT / proto["rows"], models.ROOT / proto["additions"], models.ROOT / proto["weights"])
    if args.stage == "weighted":
        models.verify_comparison_marker(out / "comparison_marker.json", path)
    completed = json.loads((out / f"{args.stage}_models_complete.json").read_text())
    if completed["protocol_sha256"] != models.sha(path):
        raise ValueError("Model stage completion protocol differs")
    dest = out / f"{args.stage}_comparison"
    dest.mkdir(exist_ok=False)
    baseline, _ = research.baseline_ensemble()
    current = pd.read_parquet(research.OLD / "existing_policies/inputs.parquet")
    current = current.loc[current.year >= 2019, models.PRED_KEYS + ["p_raw"]].rename(columns={"p_raw": "p"})
    current = current.sort_values(["race_id", "horse_number"]).reset_index(drop=True)
    frames = {"current_reference": current, "baseline": baseline}
    receipts = []
    for arm in (["speed", "pace"] if args.stage == "features" else ["weighted_baseline"]):
        frames[arm], records = ensemble(out, arm, proto)
        receipts += records
    summaries, tables = {}, {}
    for arm, frame in frames.items():
        if not baseline[models.PRED_KEYS].equals(frame[models.PRED_KEYS]):
            raise ValueError(f"Candidate/comparator starter populations differ: {arm}")
        summaries[arm], tables[arm], bets = score(frame)
        frame.to_parquet(dest / f"{arm}_ensemble.parquet", index=False)
        tables[arm].to_parquet(dest / f"{arm}_races.parquet", index=False)
        bets.to_parquet(dest / f"{arm}_bets.parquet", index=False)
    if args.stage == "weighted":
        # With no pre2019 mentor, identical recipe/seed must reproduce2019.
        old = baseline.loc[baseline.year == 2019, "p"].to_numpy()
        new = frames["weighted_baseline"].loc[frames["weighted_baseline"].year == 2019, "p"].to_numpy()
        if not np.array_equal(old, new):
            raise ValueError("Neutral-weight2019 parity failed")
    result = dict(stage=args.stage, arms=summaries, paired=paired(tables), fits=receipts,
        worker_fit_seconds=sum(r["elapsed_s"] for r in receipts), wall_seconds=completed["elapsed_s"],
        new_fits=len(receipts), baseline_reused_fits=120, ensemble_rule="mean15 probabilities BEFORE normalization and scoring",
        protocol_sha256=models.sha(path), can_adopt=False, eligible_for_verdict=False, untouched_holdout=False,
        settlement="closing_odds_proxy", limitations=proto["limitations"])
    models.verify_protocol(path, models.ROOT / proto["rows"], models.ROOT / proto["additions"], models.ROOT / proto["weights"])
    research.write(dest / "summary.json", result)
    (dest / "report.md").write_text(markdown(result))
    if args.stage == "features":
        marker = out / "comparison_marker.json"
        research.write(marker, dict(stage="features_complete", protocol_sha256=models.sha(path),
            summary_path=str((dest / "summary.json").relative_to(models.ROOT)), summary_sha256=models.sha(dest / "summary.json"),
            hashes={str((dest / "report.md").relative_to(models.ROOT)): models.sha(dest / "report.md")}))
        marker.with_suffix(".sha256").write_text(models.sha(marker) + "\n")
    print(json.dumps({"stage": args.stage, "paired": result["paired"], "out": str(dest)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
