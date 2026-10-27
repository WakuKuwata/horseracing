"""Fixed old-OOF backfill comparison against baseline and original143 weights."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

import importlib.util
import sys

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    sys.modules[name] = obj
    spec.loader.exec_module(obj)
    return obj


models = load("roi146_report_models", HERE / "models.py")
research = load("roi146_report_run", HERE / "run.py")
prior = load("roi146_score143", HERE.parent / "roi_improvement_143/report.py")


def paired(tables, contrasts, repetitions=4000, seed=20261025, family_size=2):
    calendar = tables["baseline"][["race_id", "race_date", "budget_yen"]]
    days = sorted(calendar.race_date.unique())
    draws = np.random.default_rng(seed).multinomial(len(days), np.full(len(days), 1/len(days)), size=repetitions)
    daily = {}
    for arm, frame in tables.items():
        if not calendar.equals(frame[["race_id", "race_date", "budget_yen"]]):
            raise ValueError("Paired populations differ")
        daily[arm] = frame.groupby("race_date").agg(
            stake=("stake_yen", "sum"), payout=("payout_yen", "sum"), profit=("profit_yen", "sum"),
            budget=("budget_yen", "sum"), nll=("winner_nll", "sum"), races=("race_id", "size")).reindex(days)
    result = {}
    if len(contrasts) != family_size:
        raise ValueError("Contrast family differs")
    for a, b in contrasts:
        da, db = daily[a], daily[b]
        sa, sb = draws @ da.stake, draws @ db.stake
        arrays = dict(winner_nll=(draws @ (da.nll-db.nll))/(draws @ db.races),
            profit_per_budget=(draws @ (da.profit-db.profit))/(draws @ db.budget),
            deployed_roi=np.divide(draws @ da.payout, sa, out=np.full(repetitions, np.nan), where=sa > 0)-
                np.divide(draws @ db.payout, sb, out=np.full(repetitions, np.nan), where=sb > 0))
        points = dict(winner_nll=float((da.nll-db.nll).sum()/db.races.sum()),
            profit_per_budget=float((da.profit-db.profit).sum()/db.budget.sum()),
            deployed_roi=(float(da.payout.sum()/da.stake.sum()-db.payout.sum()/db.stake.sum())
                          if da.stake.sum() and db.stake.sum() else None))
        result[f"{a}_minus_{b}"] = {name: dict(difference=points[name],
            ci95=prior.policies._interval(array, .05),
            bonferroni_familywise95_ci=prior.policies._interval(array, .05/family_size),
            valid_replicates=int(np.isfinite(array).sum())) for name, array in arrays.items()}
    return dict(results=result, contrasts=contrasts, repetitions=repetitions, seed=seed,
                family_size_per_metric=family_size, unit="all common eligible race dates")


def sensitivity(tables, contrasts):
    result = {}
    for a, b in contrasts:
        x, y = tables[a], tables[b]
        d = pd.DataFrame(dict(race_id=x.race_id, race_date=x.race_date, delta=x.profit_yen-y.profit_yen))
        known = d.race_id.eq("202608030111")
        top = d.loc[d.delta.idxmax()]
        result[f"{a}_minus_{b}"] = dict(known_race_delta_yen=float(d.loc[known, "delta"].sum()),
            without_known_race_delta_yen=float(d.loc[~known, "delta"].sum()),
            top_difference_race=top.race_id, top_difference_yen=float(top.delta),
            without_top_difference_yen=float(d.delta.sum()-top.delta),
            same_calendar_jan1_jun21_by_year=d[d.race_date.str[5:] <= "06-21"].assign(
                year=lambda t: t.race_date.str[:4]).groupby("year").delta.sum().to_dict(),
            before2026_delta_yen=float(d.loc[d.race_date.str[:4] < "2026", "delta"].sum()),
            year2026_delta_yen=float(d.loc[d.race_date.str[:4] == "2026", "delta"].sum()))
    return result


def ensemble(out, protocol):
    experiment = load("roi146_report_experiment", HERE / "experiment.py")
    paths = dict(rows=research.ROWS, weights=out / "weights.parquet")
    expected = experiment.expected_recipes(paths, protocol["window"]["model_outer"], "candidate")
    parts, details = [], []
    for year in protocol["window"]["model_outer"]:
        first, total = None, None
        for seed in protocol["seeds"]:
            args = experiment.job_args(out, year, seed, paths, "candidate")
            experiment.verify_completed(args, protocol, expected[year])
            fit = args.out / "weighted_backfill"
            rec = json.loads((fit / "receipt.json").read_text())
            pred = pd.read_parquet(fit / "predictions.parquet")
            if not pred.year.eq(year).all() or len(pred) != rec["recipe"]["target_rows"]:
                raise ValueError("Candidate target differs")
            if first is None:
                first, total = pred[models.PRED_KEYS].copy(), np.zeros(len(pred))
            elif not first.equals(pred[models.PRED_KEYS]):
                raise ValueError("Candidate seed population differs")
            total += pred.p.to_numpy(float)
            details.append(dict(arm="weighted_backfill", year=year, seed=seed, elapsed_s=rec["elapsed_s"],
                roundtrip_max_abs_raw_difference=rec["roundtrip_max_abs_raw_difference"],
                weights=rec["recipe"]["weights"], receipt_sha256=models.sha(fit / "receipt.json")))
        first["p"] = total / len(protocol["seeds"])
        parts.append(first)
    return pd.concat(parts, ignore_index=True), details


LABELS = dict(current_reference="現行保存予測（参照）", baseline="現行モデル・同条件基準77列",
    weighted_baseline="③・元の重み", weighted_backfill="2013〜2018年OOFを補完した重み")


def markdown(result):
    lines = ["# 146 古いOOFを補完した重みの比較", "",
        "2019〜2026-06-21、24,855レース・343,883頭・807日、15seed確率平均。",
        "単勝raw EV>1.2、1レース予算1,000円。確定オッズ由来の仮想精算。", "",
        "| 条件 | 購入数 | 投資額 | 払戻 | 利益 | 回収率 | winner NLL |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for arm, record in result["arms"].items():
        p = record["policy"]
        roi = f"{p['deployed_roi']*100:.2f}%" if p["deployed_roi"] is not None else "—"
        lines.append(f"| {LABELS[arm]} | {p['n_bets']:,} | {p['stake_yen']:,.0f}円 | {p['payout_yen']:,.0f}円 | {p['profit_yen']:+,.0f}円 | {roi} | {record['quality']['winner_nll']:.6f} |")
    lines += ["", "## 2比較", "", "全開催日の4000回paired bootstrap、各指標内で2比較Bonferroni補正。"]
    for key, contrast in result["paired"]["results"].items():
        a, b = key.split("_minus_")
        ci = contrast["profit_per_budget"]["bonferroni_familywise95_ci"]
        lines.append(f"- {LABELS[a]} − {LABELS[b]}: 予算利益差 {contrast['profit_per_budget']['difference']*100:+.4f}pt、補正95%区間 [{ci[0]*100:+.4f},{ci[1]*100:+.4f}]pt。")
    lines += ["", "## 年別", "", "| 年 | 条件 | 利益 | 回収率 |", "|---|---|---:|---:|"]
    for year in result["arms"]["baseline"]["by_year"]:
        for arm, record in result["arms"].items():
            p = record["by_year"][year]["policy"]
            roi = f"{p['deployed_roi']*100:.2f}%" if p["deployed_roi"] is not None else "—"
            lines.append(f"| {year} | {LABELS[arm]} | {p['profit_yen']:+,.0f}円 | {roi} |")
    lines += ["", "## 感度診断", ""]
    for key, record in result["sensitivity"].items():
        lines.append(f"- {key}: 既知の202608030111を両案から除いた利益差 {record['without_known_race_delta_yen']:+,.0f}円。最大差のレース {record['top_difference_race']} を除いた差 {record['without_top_difference_yen']:+,.0f}円。結果後の診断であり購入ルールではない。")
    lines += ["", "## リスクと限界", ""]
    for arm, record in result["arms"].items():
        p = record["policy"]
        lines.append(f"- {LABELS[arm]}: 上位5払戻を0にした利益 {p['profit_without_top5_payouts_yen']:+,.0f}円、最大DD {p['max_race_settlement_drawdown_yen']:,.0f}円。")
    lines += ["", *[f"- {x}" for x in result["limitations"]], "", f"Protocol SHA256: `{result['protocol_sha256']}`", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--out", type=Path, default=research.OUT)
    args = parser.parse_args()
    out = args.out
    proto = models.verify_protocol(out / "protocol.json", research.ROWS, out / "weights.parquet")
    marker = research.verify_marker(out, "candidate", models)
    mentor_marker = research.verify_marker(out, "mentor", models)
    frames, _ = research.references()
    frames["weighted_backfill"], details = ensemble(out, proto)
    dest = out / "comparison"
    dest.mkdir(exist_ok=False)
    scores, tables = {}, {}
    for arm, frame in frames.items():
        if not frames["baseline"][models.PRED_KEYS].equals(frame[models.PRED_KEYS]):
            raise ValueError("Candidate/reference population differs")
        scores[arm], tables[arm], bets = prior.score(frame)
        frame.to_parquet(dest / f"{arm}_ensemble.parquet", index=False)
        tables[arm].to_parquet(dest / f"{arm}_races.parquet", index=False)
        bets.to_parquet(dest / f"{arm}_bets.parquet", index=False)
    if scores["weighted_backfill"]["quality"]["races"] != 24855 or scores["weighted_backfill"]["quality"]["rows"] != 343883:
        raise ValueError("Frozen primary population differs")
    boot = proto["bootstrap"]
    manifest = json.loads((out / "weights_manifest.json").read_text())
    result = dict(arms=scores, paired=paired(tables, proto["contrasts"], boot["repetitions"], boot["seed"], boot["family_size"]),
        sensitivity=sensitivity(tables, proto["contrasts"]), fits=details, new_fits=210, new_candidate_fits=120,
        new_mentor_fits=90, reused_control_fits=240, weights_manifest=manifest,
        worker_fit_seconds=sum(x["elapsed_s"] for x in details), wall_seconds=marker["elapsed_s"],
        mentor_worker_seconds=manifest["mentor_worker_seconds"], mentor_wall_seconds=mentor_marker["elapsed_s"],
        protocol_sha256=models.sha(out / "protocol.json"), mentor_protocol_sha256=models.sha(out / "mentor_protocol.json"),
        ensemble_rule="mean15 raw probabilities BEFORE normalization/scoring or EV weight construction",
        settlement="closing_odds_proxy", can_adopt=False, untouched_holdout=False, eligible_for_verdict=False,
        limitations=proto["limitations"])
    models.verify_protocol(out / "protocol.json", research.ROWS, out / "weights.parquet")
    research.write(dest / "summary.json", result)
    (dest / "report.md").write_text(markdown(result))
    print(json.dumps(dict(status="complete", out=str(dest), paired=result["paired"]), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()


