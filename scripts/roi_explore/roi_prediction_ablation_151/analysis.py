"""151: frozen annual ablation of market-independent prediction inside the ROI stack."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import numpy as np
import pandas as pd
import scipy
from scipy.optimize import minimize
import threadpoolctl

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "artifacts/151-roi-prediction-ablation"
SPEC = ROOT / "specs/151-roi-prediction-ablation"
YEARS = list(range(2022, 2027))
STACKS = {
    "roi_only": ["q", "p_mev"],
    "plus_prediction": ["q", "p_mev", "p_pred_preweight"],
    "plus_prediction_full": ["q", "p_mev", "p_pred_full"],
}
CONTRASTS = [
    ["plus_prediction_ev1", "roi_only_ev1"],
    ["plus_prediction_full_ev1", "roi_only_ev1"],
    ["plus_prediction_ev1_2", "roi_only_ev1_2"],
]
CONFIG = dict(
    feature="151-roi-prediction-ablation", version=1, training_from=2020,
    fit_only_years=[2020, 2021], evaluation_years=YEARS, through="2026-06-26",
    stacks=STACKS, coefficient_bounds=[0., 5.], fit_objective="race winner NLL",
    primary="plus_prediction_ev1 minus roi_only_ev1", contrasts=CONTRASTS,
    policies=dict(thresholds=[1., 1.2], comparison="strict >", stake_yen=100,
                  max_horses=10, budget_yen_per_race=1000, odds_cap=None,
                  rank=["EV descending", "horse_number ascending", "horse_id ascending"],
                  uninvested_budget="cash; all eligible no-bet races remain in denominator"),
    bootstrap=dict(repetitions=4000, seed=20261025, unit="common eligible race date",
                   profit_family=3, nll_family=2, adjustment="Bonferroni per metric"),
    decision="historical support only if both primary profit/budget and ROI adjusted lower CI >0",
    new_booster_fits=0, can_adopt=False, eligible_for_verdict=False, untouched_holdout=False,
    limitations=[
        "Closing-odds proxy settlement, not executable pre-race prices or realized official payouts.",
        "Historical data and recipes were repeatedly inspected: annual OOS is not untouched confirmation.",
        "Same 2020+ common calibration data for both arms; this differs from 141's 2010+ fit.",
        "Saved prediction recipes and data retain their original feature/provenance limitations.",
        "No future prediction-family search is covered by these conditional confidence intervals.",
        "Within-day risk order is race_id, not verified post-time order.",
    ],
)


def load_inputs_module():
    spec = importlib.util.spec_from_file_location("roi151_inputs", HERE / "inputs.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def freeze(out):
    if (out / "protocol.json").exists():
        raise FileExistsError("Protocol already exists")
    loader = load_inputs_module()
    paths = loader.input_paths() + [HERE / "analysis.py", HERE / "inputs.py",
        ROOT / "scripts/tests/test_roi_prediction_ablation_151.py", SPEC / "spec.md", SPEC / "plan.md"]
    hashes = {str(p.relative_to(ROOT)): sha(p) for p in sorted(set(paths))}
    out.mkdir(exist_ok=True, parents=True)
    document = dict(config=CONFIG, frozen_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                    inputs_and_source_sha256=hashes,
                    runtime=dict(python=sys.version, numpy=np.__version__, pandas=pd.__version__,
                                 scipy=scipy.__version__, threadpoolctl=threadpoolctl.__version__))
    write(out / "protocol.json", document)
    (out / "protocol.sha256").write_text(sha(out / "protocol.json") + "\n")
    print(json.dumps(dict(status="frozen", files=len(hashes), protocol=sha(out / "protocol.json"))), flush=True)


def verify(out):
    path = out / "protocol.json"
    if sha(path) != (out / "protocol.sha256").read_text().strip():
        raise ValueError("Protocol SHA changed")
    doc = json.loads(path.read_text())
    if doc["config"] != CONFIG:
        raise ValueError("Protocol config changed")
    for name, digest in doc["inputs_and_source_sha256"].items():
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT) or sha(path) != digest:
            raise ValueError(f"Input/source changed: {name}")
    return doc


def validate(frame):
    keys = ["race_id", "horse_id"]
    required = keys + ["race_date", "year", "horse_number", "field_size", "won", "odds"]
    if not set(required).issubset(frame) or frame.empty or frame[keys].isna().any().any():
        raise ValueError("Missing population identity")
    if frame.duplicated(keys).any() or frame.duplicated(["race_id", "horse_number"]).any():
        raise ValueError("Duplicate starter identity")
    if not frame.race_id.str.fullmatch(r"\d{12}").all():
        raise ValueError("Invalid race ID")
    groups = frame.groupby("race_id", sort=False)
    if (groups.field_size.nunique() != 1).any() or not groups.size().eq(groups.field_size.first()).all():
        raise ValueError("Incomplete race field")
    if (groups.race_date.nunique() != 1).any() or not groups.won.sum().eq(1).all():
        raise ValueError("Race date or complete result differs")
    if not frame.won.isin([0, 1, False, True]).all():
        raise ValueError("Invalid outcome")
    dates = pd.to_datetime(frame.race_date)
    if not np.array_equal(dates.dt.year.to_numpy(), frame.year.to_numpy()):
        raise ValueError("Year/date mismatch")
    if (dates < pd.Timestamp("2007-01-01")).any() or (dates > pd.Timestamp(CONFIG["through"])).any():
        raise ValueError("Outside fixed time window")
    if not np.isfinite(frame.odds).all() or (frame.odds < 1.).any():
        raise ValueError("Invalid odds")


def group_structure(frame):
    rid = frame.race_id.to_numpy()
    starts = np.flatnonzero(np.r_[True, rid[1:] != rid[:-1]])
    if len(starts) != frame.race_id.nunique():
        raise ValueError("Race rows are not contiguous")
    lengths = np.diff(np.r_[starts, len(frame)])
    return starts, np.repeat(np.arange(len(starts)), lengths)


def log_softmax(scores, starts, ridx):
    maximum = np.maximum.reduceat(scores, starts)
    exp = np.exp(scores - maximum[ridx])
    return scores - maximum[ridx] - np.log(np.add.reduceat(exp, starts))[ridx]


def objective(theta, x, starts, ridx, wins):
    lp = log_softmax(x @ theta, starts, ridx)
    prob = np.exp(lp)
    return float(-lp[wins].mean()), (x.T @ prob - x[wins].sum(axis=0)) / len(wins)


def fit_stack(frame, columns, initial):
    starts, ridx = group_structure(frame)
    values = frame[columns].to_numpy(float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("Invalid component probability")
    x = np.log(values)
    wins = np.flatnonzero(frame.won.to_numpy(bool))
    result = minimize(objective, np.asarray(initial), args=(x, starts, ridx, wins),
        jac=True, method="L-BFGS-B", bounds=[(0., 5.)] * len(columns),
        options=dict(ftol=1e-13, gtol=1e-8, maxiter=1000))
    projected = result.x - np.clip(result.x - result.jac, 0., 5.)
    if not result.success or np.max(np.abs(projected)) > 2e-6:
        raise ValueError(f"Stack fit did not converge: {result.message}, gradient={projected}")
    return result.x, dict(coefficients=dict(zip(columns, result.x.tolist(), strict=True)),
        train_nll=float(result.fun), converged=bool(result.success), iterations=int(result.nit),
        projected_gradient_max=float(np.max(np.abs(projected))))


def predict_stack(frame, columns, theta):
    starts, ridx = group_structure(frame)
    prob = np.exp(log_softmax(np.log(frame[columns].to_numpy(float)) @ theta, starts, ridx))
    if not np.allclose(np.add.reduceat(prob, starts), 1., atol=1e-12, rtol=0):
        raise ValueError("Prediction sum differs")
    return prob


def annual_predictions(frame):
    validate(frame)
    d = frame.sort_values(["race_date", "race_id", "horse_number", "horse_id"]).reset_index(drop=True)
    target = d[d.year.isin(YEARS)].copy().reset_index(drop=True)
    for arm in STACKS:
        target[arm] = np.nan
    records = {}
    for year in YEARS:
        train = d[(d.year >= CONFIG["training_from"]) & (d.year < year)].copy()
        test = target[target.year == year].copy()
        if train.empty or test.empty or sorted(train.year.unique()) != list(range(2020, year)):
            raise ValueError("Incomplete chronological fit/evaluation years")
        if train.race_date.max() >= test.race_date.min():
            raise ValueError("Training crosses target date")
        fitted, records[str(year)] = {}, {}
        for arm, columns in STACKS.items():
            initial = [1., 0.] if arm == "roi_only" else [*fitted["roi_only"], 0.]
            theta, info = fit_stack(train, columns, initial)
            if arm != "roi_only" and info["train_nll"] > records[str(year)]["roi_only"]["train_nll"] + 1e-9:
                raise ValueError("Nested training loss worse than reduced model")
            fitted[arm] = theta
            target.loc[test.index, arm] = predict_stack(test, columns, theta)
            records[str(year)][arm] = dict(**info, fit_from=train.race_date.min(),
                fit_through=train.race_date.max(), fit_races=int(train.race_id.nunique()),
                fit_rows=len(train), score_from=test.race_date.min(), score_through=test.race_date.max())
        print(f"FIT {year}: " + str({a: r["coefficients"] for a, r in records[str(year)].items()}), flush=True)
    target["market_reference"] = target.q
    sums = target.groupby("race_id").p_mev.transform("sum")
    target["mev_normalized_reference"] = target.p_mev / sums
    return target, records


def select_tickets(frame, probabilities, threshold):
    """Outcome-free selection: complete field, strict threshold, flat100, cap10."""
    p = np.asarray(probabilities, dtype=float)
    if len(p) != len(frame) or not np.isfinite(p).all() or (p <= 0).any() or (p >= 1).any():
        raise ValueError("Invalid decision probabilities")
    if not np.allclose(pd.Series(p).groupby(frame.race_id.to_numpy()).sum(), 1., atol=1e-10, rtol=0):
        raise ValueError("Incomplete decision field")
    choices = frame[["race_id", "horse_id", "horse_number", "odds"]].copy().reset_index(drop=True)
    choices["ev"] = p * choices.odds.to_numpy(float)
    ranked = choices.loc[choices.ev > threshold].sort_values(
        ["race_id", "ev", "horse_number", "horse_id"], ascending=[True, False, True, True], kind="stable")
    selected = ranked.groupby("race_id", sort=False).head(10).index
    mask = np.zeros(len(frame), dtype=bool)
    mask[selected] = True
    return mask


def quality(frame, p):
    p = np.asarray(p, float)
    y = frame.won.to_numpy(float)
    bins = np.minimum((p * 10).astype(int), 9)
    ece = sum(abs(float((y[bins == k] - p[bins == k]).sum())) for k in range(10)) / len(p)
    return dict(winner_nll=float(-np.log(p[y == 1]).mean()),
        binary_log_loss=float(-(y*np.log(p)+(1-y)*np.log1p(-p)).mean()),
        brier=float(np.mean((p-y)**2)), ece10=float(ece),
        rows=len(frame), races=int(frame.race_id.nunique()))


def point(races, tickets):
    stake, payout = float(races.stake_yen.sum()), float(races.payout_yen.sum())
    budget = float(races.budget_yen.sum())
    profits = races.profit_yen.to_numpy(float)
    equity = np.r_[0., np.cumsum(profits)]
    longest = run = 0
    for loss in profits < 0:
        run = run + 1 if loss else 0
        longest = max(longest, run)
    return dict(stake_yen=stake, payout_yen=payout, profit_yen=payout-stake,
        deployed_roi=payout/stake if stake else None, profit_per_budget=(payout-stake)/budget,
        n_bets=len(tickets), n_hits=int(tickets.won.sum()),
        hit_rate=float(tickets.won.mean()) if len(tickets) else None,
        n_betting_races=int((races.stake_yen > 0).sum()),
        skip_fraction=float((races.stake_yen == 0).mean()), cash_yen=budget-stake,
        max_drawdown_yen=float(np.max(np.maximum.accumulate(equity)-equity)),
        max_consecutive_loss_races=int(longest),
        profit_without_top5_payouts_yen=payout-stake-float(tickets.payout_yen.nlargest(5).sum()),
        selected_wins_over_sum_p=float(tickets.won.sum()/tickets.p.sum()) if len(tickets) else None)


def score(frame, column, threshold):
    p = frame[column].to_numpy(float)
    mask = select_tickets(frame, p, threshold)
    all_rows = frame[["race_id", "horse_id", "horse_number", "race_date", "year", "odds", "won"]].copy()
    all_rows["p"], all_rows["ev"] = p, p * frame.odds.to_numpy(float)
    all_rows["stake_yen"] = mask.astype(int) * 100
    all_rows["payout_yen"] = all_rows.stake_yen * all_rows.odds * all_rows.won
    races = all_rows.groupby(["race_date", "race_id", "year"], sort=True).agg(
        stake_yen=("stake_yen", "sum"), payout_yen=("payout_yen", "sum")).reset_index()
    races["budget_yen"] = 1000
    races["profit_yen"] = races.payout_yen-races.stake_yen
    winners = all_rows.loc[all_rows.won.astype(bool)].set_index("race_id")
    races["winner_nll"] = races.race_id.map(-np.log(winners.p))
    tickets = all_rows[mask].reset_index(drop=True)
    result = dict(quality=quality(frame, p), policy=point(races, tickets), by_year={})
    for year, yearly in races.groupby("year"):
        chosen = frame.year.eq(year)
        result["by_year"][str(year)] = dict(policy=point(yearly, tickets[tickets.year == year]),
            quality=quality(frame[chosen], p[chosen]))
    return result, races, tickets


def interval(values, alpha):
    finite = np.asarray(values)[np.isfinite(values)]
    return np.quantile(finite, [alpha/2, 1-alpha/2]).tolist() if len(finite) else [None, None]


def paired(tables, contrasts=CONTRASTS, repetitions=4000, seed=20261025):
    baseline = tables["roi_only_ev1"]
    identity = ["race_date", "race_id", "budget_yen"]
    days = sorted(baseline.race_date.unique())
    draws = np.random.default_rng(seed).multinomial(len(days), np.full(len(days), 1/len(days)), size=repetitions)
    daily = {}
    for name, table in tables.items():
        if not table[identity].equals(baseline[identity]):
            raise ValueError("Paired populations differ")
        daily[name] = table.groupby("race_date").agg(stake=("stake_yen", "sum"),
            payout=("payout_yen", "sum"), profit=("profit_yen", "sum"), budget=("budget_yen", "sum"),
            nll=("winner_nll", "sum"), races=("race_id", "size")).reindex(days)
    output = {}
    for a, b in contrasts:
        x, y = daily[a], daily[b]
        xs, ys = draws @ x.stake, draws @ y.stake
        arrays = dict(profit_per_budget=(draws @ (x.profit-y.profit))/(draws @ y.budget),
            deployed_roi=np.divide(draws @ x.payout, xs, out=np.full(repetitions, np.nan), where=xs > 0)-
                np.divide(draws @ y.payout, ys, out=np.full(repetitions, np.nan), where=ys > 0))
        points = dict(profit_per_budget=float((x.profit-y.profit).sum()/y.budget.sum()),
            deployed_roi=float(x.payout.sum()/x.stake.sum()-y.payout.sum()/y.stake.sum())
            if x.stake.sum() and y.stake.sum() else None)
        if a.endswith("ev1"):
            arrays["winner_nll"] = (draws @ (x.nll-y.nll))/(draws @ y.races)
            points["winner_nll"] = float((x.nll-y.nll).sum()/y.races.sum())
        output[f"{a}_minus_{b}"] = {metric: dict(difference=points[metric],
            ci95=interval(values, .05), adjusted95_ci=interval(values, .05/(2 if metric == "winner_nll" else 3)),
            valid_replicates=int(np.isfinite(values).sum())) for metric, values in arrays.items()}
    return output


def sensitivity(tables, tickets):
    output = {}
    for a, b in CONTRASTS:
        delta = tables[a].profit_yen-tables[b].profit_yen
        top = delta.idxmax()
        ka = set(map(tuple, tickets[a][["race_id", "horse_id"]].to_numpy()))
        kb = set(map(tuple, tickets[b][["race_id", "horse_id"]].to_numpy()))
        output[f"{a}_minus_{b}"] = dict(ticket_overlap=len(ka & kb), candidate_only=len(ka-kb),
            baseline_only=len(kb-ka), total_profit_difference_yen=float(delta.sum()),
            largest_positive_difference_race=tables[a].loc[top, "race_id"],
            largest_positive_difference_yen=float(delta.iloc[top]),
            without_largest_positive_difference_yen=float(delta.sum()-delta.iloc[top]),
            year_profit_differences={str(y):float(delta[tables[a].year.eq(y)].sum()) for y in YEARS})
    return output


def verdict(comparisons):
    main = comparisons["plus_prediction_ev1_minus_roi_only_ev1"]
    profit, roi, nll = (main[key]["adjusted95_ci"] for key in ("profit_per_budget", "deployed_roi", "winner_nll"))
    if any(record.get("valid_replicates", 4000) != CONFIG["bootstrap"]["repetitions"]
           for record in main.values()):
        status = "insufficient_defined_bootstrap_replicates"
    elif profit[0] is not None and roi[0] is not None and profit[0] > 0 and roi[0] > 0:
        status = "historical_incremental_value_supported"
    elif profit[1] is not None and profit[1] < 0:
        status = "historical_primary_profit_deterioration"
    elif nll[1] is not None and nll[1] < 0:
        status = "incremental_probability_information_only_roi_unproven"
    else:
        status = "incremental_value_not_demonstrated"
    return dict(status=status, historical_roi_support=status == "historical_incremental_value_supported",
        prospective_retention_evidence_required=True,
        can_adopt=False, delete_prediction_model=False,
        interpretation="Historical evidence only. A wide interval is not proof of no value; no production change.")


LABELS = dict(roi_only="ROI系のみ", plus_prediction="予測追加・馬体重前", plus_prediction_full="予測追加・馬体重後",
    market_reference="市場のみ（参照）", mev_normalized_reference="mev正規化のみ（参照）")


def markdown(result):
    lines = ["# 151 ROI系への予測モデル追加価値", "",
        f"対象: {result['population']['evaluation_from']}〜{result['population']['evaluation_through']}、"
        f"{result['population']['evaluation_races']:,}レース、{result['population']['evaluation_rows']:,}頭。",
        "同じ共通過去年で係数をfitし翌年を評価。確定オッズによる仮想精算、各馬100円・最大10頭。", "",
        "| 条件（EV>1.0） | 購入数 | 投資額 | 払戻 | 利益 | 回収率 | winner NLL |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for name in LABELS:
        record = result["arms"][name+"_ev1"]
        p = record["policy"]
        roi = f"{p['deployed_roi']*100:.2f}%" if p["deployed_roi"] is not None else "—"
        lines.append(f"| {LABELS[name]} | {p['n_bets']:,} | {p['stake_yen']:,.0f}円 | {p['payout_yen']:,.0f}円 | "
            f"{p['profit_yen']:+,.0f}円 | {roi} | {record['quality']['winner_nll']:.6f} |")
    lines += ["", "## 同開催日paired比較", "", "4000回、収益3比較・NLL2比較を指標別Bonferroni補正。"]
    for name, record in result["paired"].items():
        lines += ["", f"- {name}"]
        for metric, number in record.items():
            ci = number["adjusted95_ci"]
            if number["difference"] is None or ci[0] is None:
                lines.append(f"  - {metric}: 算出不可（無購入による比率未定義）")
            else:
                lines.append(f"  - {metric}: 差 {number['difference']:+.6f}、補正95%区間 [{ci[0]:+.6f}, {ci[1]:+.6f}]、"
                    f"有効bootstrap {number['valid_replicates']}/4000")
    lines += ["", "## 年別（主政策EV>1.0）", "", "| 年 | 条件 | 利益 | 回収率 |", "|---|---|---:|---:|"]
    for year in map(str, YEARS):
        for arm in STACKS:
            p = result["arms"][arm+"_ev1"]["by_year"][year]["policy"]
            roi = f"{p['deployed_roi']*100:.2f}%" if p["deployed_roi"] is not None else "—"
            lines.append(f"| {year} | {LABELS[arm]} | {p['profit_yen']:+,.0f}円 | {roi} |")
    lines += ["", "## 年別係数", "", "係数は対数確率に掛ける指数であり、寄与率ではない。", ""]
    for year, records in result["coefficients"].items():
        lines.append(f"- {year}: "+" / ".join(f"{LABELS[a]} {r['coefficients']}" for a,r in records.items()))
    lines += ["", "## 判定", "", result["verdict"]["status"], "", "## 限界", ""]
    lines += [f"- {item}" for item in CONFIG["limitations"]]
    lines += ["", "詳細な購入・レース別損益、EV>1.2感度、選択重複、年別差、リスクはsummary.jsonとparquetに保存。", "",
        f"Protocol SHA256: `{result['protocol_sha256']}`", ""]
    return "\n".join(lines)


def run(out):
    verify(out)
    destination = out / "comparison"
    if destination.exists():
        raise FileExistsError("Comparison already exists; never overwrite")
    loader = load_inputs_module()
    data, audit = loader.load_inputs()
    predictions, coefficients = annual_predictions(data)
    destination.mkdir()
    write(out / "input_audit.json", audit)
    predictions.to_parquet(destination / "predictions.parquet", index=False)
    arms, tables, tickets = {}, {}, {}
    for column in [*STACKS, "market_reference", "mev_normalized_reference"]:
        for threshold, suffix in [(1., "ev1"), (1.2, "ev1_2")]:
            name = column+"_"+suffix
            arms[name], tables[name], tickets[name] = score(predictions, column, threshold)
            tables[name].to_parquet(destination / f"{name}_races.parquet", index=False)
            tickets[name].to_parquet(destination / f"{name}_bets.parquet", index=False)
    comparisons = paired(tables)
    result = dict(population=dict(evaluation_from=predictions.race_date.min(),
        evaluation_through=predictions.race_date.max(), evaluation_rows=len(predictions),
        evaluation_races=int(predictions.race_id.nunique()), evaluation_days=int(predictions.race_date.nunique())),
        coefficients=coefficients, arms=arms, paired=comparisons, sensitivity=sensitivity(tables,tickets),
        verdict=verdict(comparisons), protocol_sha256=sha(out / "protocol.json"), config=CONFIG)
    write(destination / "summary.json", result)
    (destination / "report.md").write_text(markdown(result))
    print(json.dumps(result["verdict"], ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("command", choices=["freeze", "verify", "run"])
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    with threadpoolctl.threadpool_limits(limits=1):
        {"freeze": freeze, "verify": verify, "run": run}[args.command](args.out)


if __name__ == "__main__":
    main()
