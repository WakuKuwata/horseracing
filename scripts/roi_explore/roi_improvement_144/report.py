"""Frozen same-budget comparison against current baseline and original143③."""
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


models = load("roi144_report_models", HERE / "models.py")
research = load("roi144_report_run", HERE / "run.py")
prior = load("roi144_score143", HERE.parent / "roi_improvement_143/report.py")


def paired(tables, contrasts, repetitions=4000, seed=20261024, family_size=4):
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


def ensemble(out, arm, protocol):
    frames, details = [], []
    for year in protocol["window"]["model_outer"]:
        first, total = None, None
        for seed in protocol["seeds"]:
            fit = out / "models" / f"seed_{seed:02d}/year_{year}" / arm
            rec = json.loads((fit / "receipt.json").read_text())
            recipe = rec["recipe"]
            identity = recipe["identity"]
            if identity["protocol_sha256"] != models.sha(out / "protocol.json") or recipe["seed"] != seed:
                raise ValueError("Candidate fit identity differs")
            if identity.get("research_arm") != arm or identity.get("weight_transform") != models.TRANSFORMS[arm]:
                raise ValueError("Candidate weight arm differs")
            if recipe["arm"] != models.KERNEL_ARM or recipe["weights"].get("arm") != arm:
                raise ValueError("Candidate weight kernel differs")
            expected_params = dict(models.base.BASE_PARAMS, seed=seed, objective="binary", metric="binary_logloss")
            if recipe["params"] != expected_params or recipe["features"] != models.base.FEATURES:
                raise ValueError("Candidate model recipe differs")
            if recipe["train_end"] >= recipe["target_start"] or recipe["rounds"] != 300:
                raise ValueError("Candidate chronology/rounds differ")
            for name, digest in rec["hashes"].items():
                if models.sha(fit / name) != digest:
                    raise ValueError("Candidate artifact changed")
            current = pd.read_parquet(fit / "predictions.parquet")
            keys = models.parent.PRED_KEYS
            if not current.year.eq(year).all():
                raise ValueError("Candidate prediction year differs")
            if first is None:
                first = current[keys].copy()
                total = np.zeros(len(first))
            elif not first.equals(current[keys]):
                raise ValueError("Candidate seed populations differ")
            total += current.p.to_numpy()
            details.append(dict(arm=arm, year=year, seed=seed, elapsed_s=rec["elapsed_s"],
                roundtrip_max_abs_raw_difference=rec["roundtrip_max_abs_raw_difference"],
                weights=recipe["weights"], receipt_sha256=models.sha(fit / "receipt.json")))
        first["p"] = total / len(protocol["seeds"])
        frames.append(first)
    return pd.concat(frames, ignore_index=True), details


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


LABELS = dict(current_reference="現行保存予測（参照）", baseline="同条件の現行基準77列",
              weighted_baseline="既存③の行重み", within_race="レース内平均1の重み（主候補）",
              race_only="レース平均の重みのみ（機序対照）")


def markdown(result):
    lines = ["# 144 レース内の平均を1にした重みの比較", "", "2019〜2026-06-21、24,855レース、15seed確率平均、raw EV>1.2・1レース予算1,000円。",
        "確定オッズ由来の仮想精算。2（展開併用）と3（古いOOF補完）は保留。", "",
        "| 条件 | 購入数 | 投資額 | 払戻 | 利益 | 回収率 | winner NLL |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for arm, rec in result["arms"].items():
        p = rec["policy"]
        roi = f"{p['deployed_roi']*100:.2f}%" if p["deployed_roi"] is not None else "—"
        lines.append(f"| {LABELS[arm]} | {p['n_bets']:,} | {p['stake_yen']:,.0f}円 | {p['payout_yen']:,.0f}円 | {p['profit_yen']:+,.0f}円 | {roi} | {rec['quality']['winner_nll']:.6f} |")
    lines += ["", "## 比較", "", "開催日4000回paired bootstrap、4比較を各指標内でBonferroni補正。"]
    for key, contrasts in result["paired"]["results"].items():
        a, b = key.split("_minus_")
        ci = contrasts["profit_per_budget"]["bonferroni_familywise95_ci"]
        lines.append(f"- {LABELS[a]} − {LABELS[b]}: 予算利益差 {contrasts['profit_per_budget']['difference']*100:+.4f}pt、補正95%区間 [{ci[0]*100:+.4f},{ci[1]*100:+.4f}]pt。")
    lines += ["", "## 年別", "", "| 年 | 条件 | 利益 | 回収率 |", "|---|---|---:|---:|"]
    for year in result["arms"]["baseline"]["by_year"]:
        for arm, rec in result["arms"].items():
            p = rec["by_year"][year]["policy"]
            roi = f"{p['deployed_roi']*100:.2f}%" if p["deployed_roi"] is not None else "—"
            lines.append(f"| {year} | {LABELS[arm]} | {p['profit_yen']:+,.0f}円 | {roi} |")
    lines += ["", "## 感度診断", ""]
    for key, rec in result["sensitivity"].items():
        lines.append(f"- {key}: 既知の202608030111を両案から除いた利益差 {rec['without_known_race_delta_yen']:+,.0f}円。最大差のレース {rec['top_difference_race']} を除いた差 {rec['without_top_difference_yen']:+,.0f}円。結果後の診断であり購入ルールではない。")
    lines += ["", "## 限界", "", *[f"- {x}" for x in result["limitations"]], "",
              f"Protocol SHA256: `{result['protocol_sha256']}`", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--out", type=Path, default=research.OUT)
    args = parser.parse_args()
    out = args.out
    proto = models.verify_protocol(out / "protocol.json", research.ROWS, research.WEIGHTS)
    marker = json.loads((out / "models_complete.json").read_text())
    if marker["protocol_sha256"] != models.sha(out / "protocol.json"):
        raise ValueError("Completion protocol differs")
    if marker["jobs"] != 120 or marker["fits"] != 240 or len(marker["hashes"]) != 480:
        raise ValueError("Completion inventory differs")
    for name, digest in marker["hashes"].items():
        artifact = (out / name).resolve()
        if not artifact.is_relative_to(out.resolve()) or models.sha(artifact) != digest:
            raise ValueError("Completion artifact changed")
    frames, _ = research.references()
    details = []
    for arm in proto["arms"]:
        frames[arm], receipts = ensemble(out, arm, proto)
        details.extend(receipts)
    base = frames["baseline"]
    for arm in proto["arms"]:
        if not np.array_equal(base.loc[base.year == 2019, "p"], frames[arm].loc[frames[arm].year == 2019, "p"]):
            raise ValueError("Neutral2019 parity failed")
    dest = out / "comparison"
    dest.mkdir(exist_ok=False)
    scores, tables = {}, {}
    for arm, frame in frames.items():
        if not base[models.parent.PRED_KEYS].equals(frame[models.parent.PRED_KEYS]):
            raise ValueError("Candidate/reference population differs")
        scores[arm], tables[arm], bets = prior.score(frame)
        frame.to_parquet(dest / f"{arm}_ensemble.parquet", index=False)
        tables[arm].to_parquet(dest / f"{arm}_races.parquet", index=False)
        bets.to_parquet(dest / f"{arm}_bets.parquet", index=False)
    boot = proto["bootstrap"]
    result = dict(arms=scores, paired=paired(tables, proto["contrasts"], boot["repetitions"], boot["seed"], boot["family_size"]),
        sensitivity=sensitivity(tables, proto["contrasts"]), fits=details,
        new_fits=len(details), reused_control_fits=240, worker_fit_seconds=sum(x["elapsed_s"] for x in details),
        wall_seconds=marker["elapsed_s"], protocol_sha256=models.sha(out / "protocol.json"),
        ensemble_rule="mean15 probabilities BEFORE normalization/scoring", settlement="closing_odds_proxy",
        can_adopt=False, untouched_holdout=False, eligible_for_verdict=False, limitations=proto["limitations"])
    models.verify_protocol(out / "protocol.json", research.ROWS, research.WEIGHTS)
    research.write(dest / "summary.json", result)
    (dest / "report.md").write_text(markdown(result))
    print(json.dumps(dict(status="complete", out=str(dest), paired=result["paired"]), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
