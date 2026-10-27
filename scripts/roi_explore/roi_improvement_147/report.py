"""Fixed half probability blend comparison with cash-budget uncertainty."""
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


models = load("roi147_report_models", HERE.parent / "roi_improvement_143/models.py")
research = load("roi147_report_run", HERE / "run.py")
prior = load("roi147_score143", HERE.parent / "roi_improvement_143/report.py")


def paired(tables, contrasts, repetitions=4000, seed=20261025, family_size=3):
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


def half_blend(baseline, pace):
    pd.testing.assert_frame_equal(baseline[models.PRED_KEYS], pace[models.PRED_KEYS], check_exact=True)
    if baseline.empty or baseline.duplicated(models.KEYS).any():
        raise ValueError("Invalid blend population")
    for parent in (baseline, pace):
        if not np.isfinite(parent.p).all() or not parent.p.between(0, 1).all():
            raise ValueError("Invalid parent probabilities")
    result = baseline[models.PRED_KEYS].copy()
    result["p"] = .5*baseline.p.to_numpy(float) + .5*pace.p.to_numpy(float)
    return result


LABELS = dict(current_reference="現行保存予測（参照）", baseline="現行モデル・同条件基準77特徴",
    weighted_baseline="③・元の重み", pace="重みなし展開91特徴", half_blend="現行77＋展開91の50:50平均")


def markdown(result):
    lines = ["# 147 現行77特徴と展開91特徴の固定50:50平均", "",
        "2019〜2026-06-21、24,855レース・343,883頭・807日。単勝raw EV>1.2、1レース予算1,000円。",
        "各親の15seed raw確率平均を0.5ずつ平均してから購入判定。確定オッズ由来の仮想精算。案2の予測後正規化は保留。", "",
        "| 条件 | 購入数 | 投資額 | 払戻 | 利益 | 回収率 | winner NLL |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for arm, record in result["arms"].items():
        p = record["policy"]
        roi = f"{p['deployed_roi']*100:.2f}%" if p["deployed_roi"] is not None else "—"
        lines.append(f"| {LABELS[arm]} | {p['n_bets']:,} | {p['stake_yen']:,.0f}円 | {p['payout_yen']:,.0f}円 | {p['profit_yen']:+,.0f}円 | {roi} | {record['quality']['winner_nll']:.6f} |")
    lines += ["", "## 3比較", "", "全開催日の4000回paired bootstrap、各指標内で3比較Bonferroni補正。"]
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
    import time
    started = time.monotonic()
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--out", type=Path, default=research.OUT)
    args = parser.parse_args()
    out = args.out
    proto = research.verify(out)
    frames, _ = research.references()
    frames["half_blend"] = half_blend(frames["baseline"], frames["pace"])
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
    if scores["half_blend"]["quality"]["races"] != 24855 or scores["half_blend"]["quality"]["rows"] != 343883:
        raise ValueError("Frozen primary population differs")
    boot = proto["bootstrap"]
    result = dict(arms=scores, paired=paired(tables, proto["contrasts"], boot["repetitions"], boot["seed"], boot["family_size"]),
        sensitivity=sensitivity(tables, proto["contrasts"]), new_fits=0, reused_control_fits=360,
        blend=proto["blend"], protocol_sha256=models.sha(out / "protocol.json"),
        ensemble_rule="mean15 raw probabilities per parent FIRST, then fixed0.5/0.5; rawEV selection",
        settlement="closing_odds_proxy", can_adopt=False, untouched_holdout=False, eligible_for_verdict=False,
        proposal2="held", limitations=proto["limitations"], wall_seconds=time.monotonic()-started)
    research.verify(out)
    research.write(dest / "summary.json", result)
    (dest / "report.md").write_text(markdown(result))
    print(json.dumps(dict(status="complete", out=str(dest), paired=result["paired"]), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()


