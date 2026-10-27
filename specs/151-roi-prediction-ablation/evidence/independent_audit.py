"""Independent 151 output audit; does not import the experiment or its loader."""
from __future__ import annotations

import hashlib
import json
import os
import argparse
from pathlib import Path

for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[key] = "1"

# Set BLAS thread limits before importing numerical libraries.
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from threadpoolctl import threadpool_limits  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "artifacts/151-roi-prediction-ablation"
HERE = Path(__file__).resolve().parent
COMPARISON = OUT / "comparison"
STACKS = {
    "roi_only": ["q", "p_mev"],
    "plus_prediction": ["q", "p_mev", "p_pred_preweight"],
    "plus_prediction_full": ["q", "p_mev", "p_pred_full"],
}
REFERENCES = ["market_reference", "mev_normalized_reference"]
CONTRASTS = [("plus_prediction_ev1", "roi_only_ev1"),
             ("plus_prediction_full_ev1", "roi_only_ev1"),
             ("plus_prediction_ev1_2", "roi_only_ev1_2")]
checks = []
max_errors = {}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def same(actual, expected, name, atol=1e-9):
    if actual is None or expected is None:
        assert actual is None and expected is None, (name, actual, expected)
    else:
        a, b = np.asarray(actual, float), np.asarray(expected, float)
        assert np.allclose(a, b, rtol=1e-10, atol=atol, equal_nan=False), (name, actual, expected)
        error = float(np.max(np.abs(a-b))) if a.size else 0.
        max_errors[name.split("/")[0]] = max(max_errors.get(name.split("/")[0], 0.), error)


def compare_mapping(actual, expected, name):
    assert set(actual) == set(expected), (name, set(actual) ^ set(expected))
    for key, value in actual.items():
        same(value, expected[key], name+"/"+key)


def quality(d, p):
    y = d.won.to_numpy(float)
    bins = np.minimum(np.floor(10*p).astype(int), 9)
    return dict(winner_nll=float(np.mean(-np.log(p[y == 1]))),
        binary_log_loss=float(np.mean(-(y*np.log(p)+(1-y)*np.log1p(-p)))),
        brier=float(np.mean((y-p)**2)),
        ece10=float(sum(abs(np.sum(y[bins == k]-p[bins == k])) for k in range(10))/len(d)),
        rows=len(d), races=int(d.race_id.nunique()))


def accounting(d, p, threshold):
    # An independently expressed rank and cap, without experiment helper calls.
    selected = d[["race_id", "horse_id", "horse_number", "odds", "won", "race_date", "year"]].copy()
    selected["p"] = p
    selected["ev"] = p*selected.odds
    selected = selected[selected.ev > threshold].sort_values(
        ["race_id", "ev", "horse_number", "horse_id"], ascending=[True, False, True, True])
    rank = selected.groupby("race_id").cumcount()
    selected = selected[rank < 10].copy()
    selected["stake_yen"] = 100
    selected["payout_yen"] = 100*selected.odds*selected.won
    # Retain every eligible race, including all cash allocations.
    r = d[["race_date", "race_id", "year"]].drop_duplicates().sort_values(["race_date", "race_id"])
    amounts = selected.groupby("race_id")[["stake_yen", "payout_yen"]].sum()
    r = r.set_index("race_id").join(amounts).fillna({"stake_yen": 0., "payout_yen": 0.}).reset_index()
    r["budget_yen"] = 1000
    r["profit_yen"] = r.payout_yen-r.stake_yen
    w = pd.Series(-np.log(p[d.won.to_numpy(bool)]), index=d.loc[d.won.astype(bool), "race_id"])
    r["winner_nll"] = r.race_id.map(w)
    return r.reset_index(drop=True), selected.sort_index().reset_index(drop=True)


def policy(r, t):
    stake, payout = float(r.stake_yen.sum()), float(r.payout_yen.sum())
    profit = r.profit_yen.to_numpy(float)
    equity = np.r_[0., np.cumsum(profit)]
    streak = longest = 0
    for value in profit:
        streak = streak+1 if value < 0 else 0
        longest = max(longest, streak)
    return dict(stake_yen=stake, payout_yen=payout, profit_yen=payout-stake,
        deployed_roi=payout/stake if stake else None,
        profit_per_budget=(payout-stake)/float(r.budget_yen.sum()),
        n_bets=len(t), n_hits=int(t.won.sum()), hit_rate=float(t.won.mean()) if len(t) else None,
        n_betting_races=int((r.stake_yen > 0).sum()), skip_fraction=float((r.stake_yen == 0).mean()),
        cash_yen=float(r.budget_yen.sum())-stake,
        max_drawdown_yen=float(np.max(np.maximum.accumulate(equity)-equity)),
        max_consecutive_loss_races=int(longest),
        profit_without_top5_payouts_yen=payout-stake-float(t.payout_yen.nlargest(5).sum()),
        selected_wins_over_sum_p=float(t.won.sum()/t.p.sum()) if len(t) else None)


def run(verify_existing=False):
    protocol = json.loads((OUT / "protocol.json").read_text())
    assert digest(OUT / "protocol.json") == (OUT / "protocol.sha256").read_text().strip()
    for name, expected in protocol["inputs_and_source_sha256"].items():
        assert digest(ROOT / name) == expected, name
    cfg = protocol["config"]
    assert cfg["primary"] == "plus_prediction_ev1 minus roi_only_ev1"
    assert cfg["can_adopt"] is False and cfg["eligible_for_verdict"] is False
    assert cfg["untouched_holdout"] is False
    checks.append("Protocol and all frozen input/source hashes verified; primary remains preweight.")
    result = json.loads((COMPARISON / "summary.json").read_text())
    d = pd.read_parquet(COMPARISON / "predictions.parquet")
    assert not d.duplicated(["race_id", "horse_id"]).any()
    assert not d.duplicated(["race_id", "horse_number"]).any()
    groups = d.groupby("race_id")
    assert groups.size().eq(groups.field_size.first()).all()
    assert groups.field_size.nunique().eq(1).all()
    assert groups.won.sum().eq(1).all()
    assert pd.to_datetime(d.race_date).dt.year.eq(d.year).all()
    assert d.race_date.min() >= "2022-01-01" and d.race_date.max() <= "2026-06-26"
    assert set(d.year.unique()) == set(range(2022, 2027))
    assert np.isfinite(d.odds).all() and d.odds.ge(1).all()
    same(d.groupby("race_id").q.sum().to_numpy(), 1., "field/q_sum")
    same(d.q.to_numpy()*d.groupby("race_id").odds.transform(lambda s: (1/s).sum()).to_numpy(),
         1/d.odds.to_numpy(), "field/q_definition")
    assert d.race_id.nunique() == result["population"]["evaluation_races"]
    checks.append("Complete fields, unique starter IDs and horse numbers, one winner, date/odds contracts verified.")
    # Recompute every annual probability directly from the recorded coefficients.
    for year, records in result["coefficients"].items():
        part = d[d.year == int(year)]
        for arm, columns in STACKS.items():
            record = records[arm]
            assert record["fit_from"].startswith("2020-")
            assert record["fit_through"] < f"{year}-01-01"
            assert record["fit_through"].startswith(str(int(year)-1))
            assert record["score_from"] == part.race_date.min()
            assert record["score_through"] == part.race_date.max()
            theta = np.array([record["coefficients"][c] for c in columns])
            assert np.isfinite(theta).all() and ((theta >= 0) & (theta <= 5)).all()
            logits = pd.Series(np.log(part[columns].to_numpy()) @ theta, index=part.index)
            maximum = logits.groupby(part.race_id).transform("max")
            exps = np.exp(logits-maximum)
            expected = exps/exps.groupby(part.race_id).transform("sum")
            same(part[arm].to_numpy(), expected.to_numpy(), "softmax/"+arm, atol=2e-12)
            assert record["converged"] and record["projected_gradient_max"] <= 2e-6
            if arm != "roi_only":
                assert record["train_nll"] <= records["roi_only"]["train_nll"]+1e-9
                assert record["fit_rows"] == records["roi_only"]["fit_rows"]
                assert record["fit_races"] == records["roi_only"]["fit_races"]
    same(d.market_reference, d.q, "references/market")
    same(d.mev_normalized_reference, d.p_mev/d.groupby("race_id").p_mev.transform("sum"), "references/mev")
    checks.append("All annual softmax predictions, coefficient bounds, nested train losses and recorded cutoffs verified.")
    tables, tickets = {}, {}
    for arm in [*STACKS, *REFERENCES]:
        p = d[arm].to_numpy(float)
        assert np.isfinite(p).all() and (p > 0).all() and (p < 1).all()
        same(d.groupby("race_id")[arm].sum().to_numpy(), 1., "field/"+arm)
        for threshold, suffix in [(1., "ev1"), (1.2, "ev1_2")]:
            name = arm+"_"+suffix
            r, t = accounting(d, p, threshold)
            sr = pd.read_parquet(COMPARISON / f"{name}_races.parquet")
            st = pd.read_parquet(COMPARISON / f"{name}_bets.parquet")
            assert list(zip(st.race_id, st.horse_id)) == list(zip(t.race_id, t.horse_id)), name
            assert sr.race_id.tolist() == r.race_id.tolist(), name
            for c in ("stake_yen", "payout_yen", "profit_yen", "budget_yen", "winner_nll"):
                same(sr[c], r[c], "accounting/"+name+"/"+c)
            for c in ("p", "ev", "stake_yen", "payout_yen"):
                same(st[c], t[c], "tickets/"+name+"/"+c)
            assert r.stake_yen.le(1000).all() and t.groupby("race_id").size().le(10).all()
            saved = result["arms"][name]
            compare_mapping(saved["policy"], policy(r,t), "policy/"+name)
            compare_mapping(saved["quality"], quality(d,p), "quality/"+name)
            for year in range(2022, 2027):
                keep = d.year.eq(year).to_numpy()
                compare_mapping(saved["by_year"][str(year)]["policy"],
                    policy(r[r.year == year],t[t.year == year]), "policy_year/"+name)
                compare_mapping(saved["by_year"][str(year)]["quality"], quality(d[keep],p[keep]), "quality_year/"+name)
            tables[name], tickets[name] = r,t
    checks.append("All 10 arms: exact tickets/rank/cap/strict thresholds, full-population accounting, overall/yearly quality and risk summaries verified.")
    days = sorted(d.race_date.unique())
    weights = np.random.default_rng(20261025).multinomial(len(days), np.repeat(1/len(days),len(days)),size=4000)
    daily = {name:r.groupby("race_date")[["stake_yen", "payout_yen", "profit_yen", "budget_yen", "winner_nll"]].sum().reindex(days)
             for name,r in tables.items()}
    counts = tables["roi_only_ev1"].groupby("race_date").size().reindex(days).to_numpy()
    all_ci = {}
    for a,b in CONTRASTS:
        x,y = daily[a],daily[b]
        xs,ys = weights@x.stake_yen,weights@y.stake_yen
        arrays = {
            "profit_per_budget": (weights@(x.profit_yen-y.profit_yen))/(weights@y.budget_yen),
            "deployed_roi": np.divide(weights@x.payout_yen,xs,out=np.full(4000,np.nan),where=xs>0)-
                            np.divide(weights@y.payout_yen,ys,out=np.full(4000,np.nan),where=ys>0),
        }
        points = {"profit_per_budget":float((x.profit_yen-y.profit_yen).sum()/y.budget_yen.sum()),
            "deployed_roi":float(x.payout_yen.sum()/x.stake_yen.sum()-y.payout_yen.sum()/y.stake_yen.sum())
                if x.stake_yen.sum() and y.stake_yen.sum() else None}
        if a.endswith("ev1"):
            arrays["winner_nll"]=(weights@(x.winner_nll-y.winner_nll))/(weights@counts)
            points["winner_nll"]=float((x.winner_nll-y.winner_nll).sum()/counts.sum())
        label=a+"_minus_"+b
        all_ci[label]={}
        for metric,values in arrays.items():
            expected=result["paired"][label][metric]
            finite=values[np.isfinite(values)]
            assert len(finite)==expected["valid_replicates"]
            same(expected["difference"],points[metric],"bootstrap/point")
            for key,alpha in [("ci95",.05),("adjusted95_ci",.05/(2 if metric=="winner_nll" else 3))]:
                q=np.quantile(finite,[alpha/2,1-alpha/2]).tolist() if len(finite) else [None,None]
                if len(finite):
                    same(expected[key],q,"bootstrap/"+key)
                else:
                    assert expected[key]==q
            all_ci[label][metric]=expected
        delta=tables[a].profit_yen-tables[b].profit_yen
        sensitivity=result["sensitivity"][label]
        same(sensitivity["total_profit_difference_yen"],float(delta.sum()),"sensitivity/total")
        same(sensitivity["without_largest_positive_difference_yen"],float(delta.sum()-delta.max()),"sensitivity/outlier")
    checks.append("All paired points, 4000 same-day resamples, unadjusted/Bonferroni intervals and outlier sensitivity reproduced.")
    main=all_ci[CONTRASTS[0][0]+"_minus_"+CONTRASTS[0][1]]
    profit,roi,nll=(main[k]["adjusted95_ci"] for k in ("profit_per_budget","deployed_roi","winner_nll"))
    if any(v["valid_replicates"]!=4000 for v in main.values()):
        status="insufficient_defined_bootstrap_replicates"
    elif profit[0] is not None and roi[0] is not None and profit[0]>0 and roi[0]>0:
        status="historical_incremental_value_supported"
    elif profit[1] is not None and profit[1]<0:
        status="historical_primary_profit_deterioration"
    elif nll[1] is not None and nll[1]<0:
        status="incremental_probability_information_only_roi_unproven"
    else:
        status="incremental_value_not_demonstrated"
    assert result["verdict"]["status"]==status
    assert result["verdict"]["can_adopt"] is False
    assert result["verdict"]["delete_prediction_model"] is False
    assert result["verdict"]["prospective_retention_evidence_required"] is True
    assert result["verdict"]["historical_roi_support"] == (status=="historical_incremental_value_supported")
    checks.append("Primary preweight decision and historical-only retention limitations verified.")
    inverse_sums=d.groupby("race_id").odds.apply(lambda odds:float((1/odds).sum()))
    underround=inverse_sums[inverse_sums<1]
    underround_profit={}
    for name in ("roi_only_ev1","plus_prediction_ev1"):
        table=tables[name]
        underround_profit[name]=float(table.loc[table.race_id.isin(underround.index),"profit_yen"].sum())
    odds_diagnostic=dict(post_scoring_diagnostic_only=True, changes_selection_or_verdict=False,
        n_underround_races=len(underround),minimum_inverse_odds_sum=float(underround.min()),
        inverse_odds_sum_by_race={str(k):float(v) for k,v in underround.items()},
        primary_profit_by_arm_yen=underround_profit,
        primary_profit_difference_yen=underround_profit["plus_prediction_ev1"]-underround_profit["roi_only_ev1"],
        interpretation="Stored odds include underround fields; no post hoc exclusion or adoption decision was made.")
    audit=dict(status="PASS", protocol_sha256=digest(OUT/"protocol.json"),
        summary_sha256=digest(COMPARISON/"summary.json"),prediction_sha256=digest(COMPARISON/"predictions.parquet"),
        source_sha256=digest(__file__),frozen_files_verified=len(protocol["inputs_and_source_sha256"]),
        evaluation_rows=len(d),evaluation_races=int(d.race_id.nunique()),evaluation_days=len(days),
        checks=checks,max_abs_errors=max_errors,primary_verdict=status,stored_odds_diagnostic=odds_diagnostic,
        limits=["No import of analysis.py or inputs.py; all output calculations independently implemented.",
                "Fit cutoff and convergence records checked; historical train-only coefficients were not independently refitted.",
                "The separate input audit covers source eligibility, label completeness and 130 reconstruction parity.",
                "Conditional historical closing-odds evidence; does not establish executable pre-race profitability or justify deletion."])
    target=HERE/"independent_audit.json"
    if verify_existing:
        existing=json.loads(target.read_text())
        assert existing == audit, "Stored audit differs from independently recomputed values or current source SHA"
        print(json.dumps(dict(status="PASS", mode="verify-existing", source_sha256=audit["source_sha256"],
            audit_sha256=digest(target), checks=len(checks), frozen_files_verified=audit["frozen_files_verified"]),
            ensure_ascii=False, indent=2))
        return
    if target.exists():
        raise FileExistsError(target)
    target.write_text(json.dumps(audit,ensure_ascii=False,indent=2)+"\n")
    (HERE/"independent-review.md").write_text("# 151 独立監査\n\n判定: PASS。\n\n"+
        "\n".join("- "+c for c in checks)+"\n\n主比較の判定: `"+status+"`。\n\n"+
        "analysis.py / inputs.py をimportせず独立計算。係数の再fitは行わず、年次cutoffと保存係数による全頭予測を確認した。"
        "入力来歴と130再構成の確認は別の入力監査と合わせて読む。確定オッズ・既視期間の条件付き結果であり、本番採用や削除の証拠にはしない。\n\n"
        "## 保存オッズの診断\n\n"
        f"採点後の診断のみ。逆数和1未満のレースは{len(underround)}件、最小{underround.min():.9f}。"
        f"当該レースの主追加利益差は{odds_diagnostic['primary_profit_difference_yen']:+,.0f}円。"
        "保存オッズの品質限界であり、事前条件外の除外・採用選別・主判定変更はしない。\n\n"+
        "\n".join(f"- `{rid}`: {value:.9f}" for rid,value in underround.items())+"\n")
    print(json.dumps(audit,ensure_ascii=False,indent=2))


if __name__=="__main__":
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument("--verify-existing", action="store_true",
        help="Recompute and exactly verify existing evidence, including source SHA; write nothing.")
    args=parser.parse_args()
    with threadpool_limits(limits=1):
        run(verify_existing=args.verify_existing)
