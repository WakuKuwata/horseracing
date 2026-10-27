"""Independent fresh-30-minute audit; imports no primary 159/158/157 implementation."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "artifacts/158-prerace-roi-comparison"
NEW = ROOT / "artifacts/159-prerace-fresh30"
HERE = Path(__file__).resolve().parent
COEF = ROOT / "artifacts/151-roi-prediction-ablation/comparison/summary.json"


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def close(a, b, tolerance=1e-11):
    np.testing.assert_allclose(a, b, atol=tolerance, rtol=0, equal_nan=True)


def read_population():
    protocol = json.loads((BASE / "protocol.json").read_text())
    assert sha(BASE / "protocol.json") == (BASE / "protocol.sha256").read_text().strip()
    for path, expected in protocol["inputs_and_source_sha256"].items():
        assert sha(ROOT / path) == expected, path
    d = pd.read_parquet(BASE / "prepared/judgments.parquet")
    assert not d.duplicated(["race_id", "horse_id"]).any()
    d["_observed"] = pd.to_datetime(d.quote_observed_at, utc=True, errors="coerce")
    d["_decision"] = pd.to_datetime(d.decision_at, utc=True, errors="coerce")
    d["_post"] = pd.to_datetime(d.post_time, utc=True, errors="coerce")
    d["_age"] = (d._decision - d._observed).dt.total_seconds()
    selected, rejected = [], []
    for rid, g in d.groupby("race_id", sort=True):
        assert g.horse_number.notna().all() and not g.horse_number.duplicated().any()
        for column in ["_decision", "_observed", "_post"]:
            assert g[column].notna().all() and g[column].nunique() == 1
        assert g._decision.lt(g._post).all()
        age = float(g._age.iloc[0])
        assert np.isfinite(age) and g._age.eq(age).all()
        close(g.quote_age_seconds, g._age)
        (selected if 0 <= age <= 1800 else rejected).append(rid)
    fresh = d.loc[d.race_id.isin(selected)].copy().reset_index(drop=True)
    return fresh, dict(source_races=d.race_id.nunique(), selected_races=selected,
        excluded_races=rejected, races=fresh.race_id.nunique(), horses=len(fresh),
        days=fresh.race_date.nunique(), min_age_seconds=float(fresh._age.min()),
        max_age_seconds=float(fresh._age.max()),
        date_counts=fresh.groupby("race_date").race_id.nunique().to_dict(),
        base_protocol_sha256=sha(BASE / "protocol.json"),
        inherited_file_shas_verified=len(protocol["inputs_and_source_sha256"]))


def score():
    fresh, coverage = read_population()
    coefficients = json.loads(COEF.read_text())["coefficients"]["2026"]
    # Only timestamp-qualified prices/probabilities are used before labels enter below.
    components = {"roi_only": ["q", "p_mev"],
                  "plus_prediction": ["q", "p_mev", "p_pred_preweight"]}
    probabilities, selected = {}, {}
    for rid, g in fresh.groupby("race_id", sort=False):
        expected_q = (1 / g.judgment_odds) / (1 / g.judgment_odds).sum()
        close(g.q, expected_q)
        assert (g.judgment_odds >= 1).all() and np.isfinite(g.judgment_odds).all()
        close(g.p_pred_preweight.sum(), 1., 1e-8)
        for name, columns in components.items():
            assert coefficients[name]["fit_through"] < g.race_date.iloc[0]
            theta = np.array([coefficients[name]["coefficients"][c] for c in columns])
            z = np.log(g[columns].to_numpy(float)) @ theta
            p = np.exp(z - z.max()); p /= p.sum()
            probabilities.setdefault(name, pd.Series(index=fresh.index, dtype=float)).loc[g.index] = p
    for name, p in probabilities.items():
        choices = fresh[["race_id", "horse_id", "horse_number"]].copy()
        choices["ev"] = p.to_numpy() * fresh.judgment_odds.to_numpy()
        rank = choices.loc[choices.ev > 1.].sort_values(
            ["race_id", "ev", "horse_number", "horse_id"],
            ascending=[True, False, True, True], kind="stable")
        mask = pd.Series(False, index=fresh.index)
        mask.loc[rank.groupby("race_id", sort=False).head(10).index] = True
        selected[name] = mask
    # Outcome access starts only here.
    labels = pd.read_parquet(BASE / "prepared/settlements.parquet")
    labels = labels.set_index(["race_id", "horse_id"]).reindex(
        pd.MultiIndex.from_frame(fresh[["race_id", "horse_id"]]))
    assert labels.won.notna().all() and labels.official_payout_yen.notna().all()
    close(labels.official_payout_yen.gt(0).astype(int), labels.won.astype(int))
    accounts, summaries = {}, {}
    for name, mask in selected.items():
        cash = fresh[["race_id", "race_date", "post_time"]].copy()
        cash["stake"] = mask.to_numpy().astype(int) * 100
        cash["payout"] = mask.to_numpy().astype(int) * labels.official_payout_yen.to_numpy()
        cash["tickets"] = mask.to_numpy().astype(int)
        cash["hits"] = mask.to_numpy().astype(int) * labels.won.to_numpy().astype(int)
        by_race = cash.groupby(["race_id", "race_date", "post_time"], dropna=False)[
            ["stake", "payout", "tickets", "hits"]].sum().reset_index()
        by_race["profit"] = by_race.payout - by_race.stake
        by_race["budget"] = 1000
        assert by_race.stake.le(1000).all()
        by_race = by_race.sort_values(["race_date", "post_time", "race_id"])
        accounts[name] = by_race
        stake, payout = int(by_race.stake.sum()), int(by_race.payout.sum())
        nll = float(-np.log(probabilities[name].to_numpy()[labels.won.to_numpy(bool)]).mean())
        summaries[name] = dict(tickets=int(mask.sum()), hit_tickets=int(by_race.hits.sum()),
            stake_yen=stake, payout_yen=payout, profit_yen=payout-stake,
            roi=payout/stake if stake else None, no_bet_races=int(by_race.stake.eq(0).sum()),
            winner_nll=nll, profit_per_common_budget=(payout-stake)/(len(by_race)*1000))
    a, b = accounts["roi_only"], accounts["plus_prediction"]
    paired = a.merge(b, on=["race_id", "race_date"], suffixes=("_a", "_b"), validate="one_to_one")
    daily = paired.groupby("race_date", sort=True)[
        ["stake_a", "payout_a", "profit_a", "budget_a", "stake_b", "payout_b", "profit_b"]].sum().to_numpy(float)
    rng = np.random.default_rng(20261026)
    totals = daily[rng.integers(0, len(daily), size=(4000, len(daily)))].sum(axis=1)
    good = (totals[:, 0] > 0) & (totals[:, 4] > 0)
    delta_budget = (totals[:, 6]-totals[:, 2])/totals[:, 3]
    delta_roi = totals[good, 5]/totals[good, 4]-totals[good, 1]/totals[good, 0]
    result = dict(coverage=coverage, arms=summaries,
        changed_decisions=int(selected["roi_only"].ne(selected["plus_prediction"]).sum()),
        profit_difference_yen=int(b.profit.sum()-a.profit.sum()),
        roi_difference=(summaries["plus_prediction"]["roi"]-summaries["roi_only"]["roi"]
            if summaries["plus_prediction"]["roi"] is not None
            and summaries["roi_only"]["roi"] is not None else None),
        profit_common_budget_difference=float((b.profit.sum()-a.profit.sum())/a.budget.sum()),
        undefined_roi_replicates=int((~good).sum()),
        roi_difference_interval=np.quantile(delta_roi,[.0125,.9875]).tolist() if good.all() else None,
        profit_common_budget_interval=np.quantile(delta_budget,[.0125,.9875]).tolist())
    result["status"] = "INDEPENDENT_RECALCULATION_COMPLETE"
    (HERE/"independent_expected.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,default=int)+'\n')
    return fresh, probabilities, selected, accounts, result


def compare_main():
    fresh, probabilities, masks, accounts, result = score()
    document=json.loads((NEW/"protocol.json").read_text())
    assert sha(NEW/"protocol.json")== (NEW/"protocol.sha256").read_text().strip()
    for name, checksum in document["inputs_and_source_sha256"].items():
        assert sha(ROOT/name)==checksum,name
    decision=pd.read_parquet(NEW/"comparison/decisions.parquet").set_index(["race_id","horse_id"])
    index=pd.MultiIndex.from_frame(fresh[["race_id","horse_id"]])
    assert set(decision.index)==set(index)
    decision=decision.reindex(index)
    assert not any(c in decision for c in ["won","official_payout_yen","final_odds"])
    close(decision.judgment_odds,fresh.judgment_odds)
    summary=json.loads((NEW/"comparison/summary.json").read_text())
    for arm in masks:
        close(decision[arm],probabilities[arm])
        assert np.array_equal(decision[arm+"_selected"].to_numpy(),masks[arm].to_numpy())
        close(decision[arm+"_ev"],probabilities[arm].to_numpy()*fresh.judgment_odds.to_numpy())
        actual=pd.read_parquet(NEW/"comparison"/(arm+"_races.parquet")).set_index("race_id").sort_index()
        expected=accounts[arm].set_index("race_id").sort_index()
        assert set(actual.index)==set(expected.index)
        for c in ["stake","payout","profit","tickets","hits","budget"]:
            close(actual[c],expected[c])
        for key,value in result["arms"][arm].items():
            main=summary["arms"][arm][key]
            if value is None:
                assert main is None
            else:
                close(main,value)
    for key in ["profit_difference_yen","roi_difference","profit_common_budget_difference",
                "undefined_roi_replicates","roi_difference_interval","profit_common_budget_interval"]:
        if result[key] is None:
            assert summary["paired"][key] is None
        else:
            close(summary["paired"][key],result[key])
    assert summary["changed_decisions"]==result["changed_decisions"]
    assert summary["config"]["can_adopt"] is False
    assert "保留" in summary["conclusion"]
    result.update(status="PASS",main_protocol_sha256=sha(NEW/"protocol.json"),
                  main_summary_sha256=sha(NEW/"comparison/summary.json"),
                  new_protocol_file_count=len(document["inputs_and_source_sha256"]),
                  conclusion="保留: 少標本の記述比較、同等性/優劣を証明しない",
                  audit_code_sha256=sha(__file__))
    (HERE/"independent_final.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,default=int)+'\n')
    return result


if __name__ == "__main__":
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument("--score",action="store_true")
    parser.add_argument("--compare",action="store_true")
    args=parser.parse_args()
    if args.compare:
        print(json.dumps(compare_main(),ensure_ascii=False,indent=2,default=int))
    elif args.score:
        *_, result=score(); print(json.dumps(result,ensure_ascii=False,indent=2,default=int))
    else:
        _, result=read_population()
        (HERE/"independent_coverage.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,default=int)+'\n')
        print(json.dumps(result,ensure_ascii=False,indent=2,default=int))
