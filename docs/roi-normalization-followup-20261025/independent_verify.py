"""Independent saved-result attribution; no scoring, fitting, or root imports."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OLD = ROOT / "artifacts/148-postprediction-normalization"
COMP = OLD / "comparison"
KNOWN = "202608030111"
PAIRS = {"baseline": "normalized_baseline", "weighted_baseline": "normalized_weighted_baseline"}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def empty():
    return dict(n_rows=0, n_wins=0, n_raw_bets=0, n_normalized_bets=0,
                raw_stake=0.0, normalized_stake=0.0, raw_payout=0.0,
                normalized_payout=0.0, raw_profit=0.0, normalized_profit=0.0,
                delta_stake=0.0, delta_payout=0.0, delta_profit=0.0)


def bump(dest, raw, norm, payoff):
    dest["n_rows"] += 1
    dest["n_wins"] += int(payoff > 0)
    dest["n_raw_bets"] += int(raw > 0)
    dest["n_normalized_bets"] += int(norm > 0)
    dest["raw_stake"] += raw
    dest["normalized_stake"] += norm
    dest["raw_payout"] += raw * payoff
    dest["normalized_payout"] += norm * payoff
    dest["raw_profit"] += raw * (payoff - 1)
    dest["normalized_profit"] += norm * (payoff - 1)
    dest["delta_stake"] += norm - raw
    dest["delta_payout"] += (norm - raw) * payoff
    dest["delta_profit"] += (norm - raw) * (payoff - 1)


def category(raw, norm):
    if raw == 0 and norm > 0:
        return "added"
    if raw > 0 and norm == 0:
        return "removed"
    if raw > 0 and norm > 0 and raw != norm:
        return "common_stake_changed"
    return "same" if raw > 0 else "neither"


def bets(arm):
    frame = pd.read_parquet(COMP / f"{arm}_bets.parquet")
    result = {}
    for r in frame.itertuples(index=False):
        k = (str(r.race_id), str(r.horse_id))
        assert k not in result
        assert abs(float(r.payout_yen) - int(r.stake_yen) * bool(r.won) * float(r.odds)) < 1e-6
        result[k] = int(r.stake_yen)
    return result


def verify_hashes():
    audit_path = ROOT / "specs/148-postprediction-normalization/evidence/independent_final.json"
    audit = json.loads(audit_path.read_text())
    proto = json.loads((OLD / "protocol.json").read_text())
    assert audit["summary_sha256"] == sha(COMP / "summary.json")
    assert audit["protocol_sha256"] == sha(OLD / "protocol.json")
    assert (OLD / "protocol.sha256").read_text().split()[0] == audit["protocol_sha256"]
    assert audit["audit_script_sha256"] == sha(ROOT / "specs/148-postprediction-normalization/evidence/independent_audit.py")
    assert audit["independent143_helpers_sha256"] == sha(ROOT / "specs/143-existing-feature-roi/evidence/independent_audit.py")
    for name, digest in proto["inputs_and_source_sha256"].items():
        assert sha(ROOT / name) == digest, name
    assert not audit["blockers"]
    return dict(protocol_sha256=audit["protocol_sha256"], summary_sha256=audit["summary_sha256"],
                independent_final_sha256=sha(audit_path), frozen_files_verified=len(proto["inputs_and_source_sha256"]))


def calculate(parent, candidate, summary):
    raw = pd.read_parquet(COMP / f"{parent}_ensemble.parquet")
    norm = pd.read_parquet(COMP / f"{candidate}_ensemble.parquet")
    assert raw.shape[0] == norm.shape[0] == 343883
    assert raw.drop(columns="p").equals(norm.drop(columns="p"))
    sums = defaultdict(float)
    for r in raw.itertuples(index=False):
        sums[str(r.race_id)] += float(r.p)
    assert len(sums) == 24855
    raw_stakes, norm_stakes = bets(parent), bets(candidate)
    periods = defaultdict(empty)
    groups = defaultdict(lambda: defaultdict(empty))
    races = defaultdict(empty)
    race_meta = {}
    known = []
    max_norm_error = 0.0
    seen = set()
    for r, n in zip(raw.itertuples(index=False), norm.itertuples(index=False), strict=True):
        k = (str(r.race_id), str(r.horse_id))
        assert k not in seen
        seen.add(k)
        mass = sums[k[0]]
        assert mass > 0
        expected = float(r.p) / mass
        max_norm_error = max(max_norm_error, abs(expected - float(n.p)))
        assert abs(expected - float(n.p)) < 2e-15
        rs, ns = raw_stakes.get(k, 0), norm_stakes.get(k, 0)
        payoff = bool(r.won) * float(r.odds)
        scopes = ("all", str(r.year), "2026" if r.year == 2026 else "pre2026")
        # 2026 is one scope, rather than counting the duplicated string twice.
        scopes = tuple(dict.fromkeys(scopes))
        if str(r.race_date)[5:] <= "06-21":
            scopes += (f"jan1_jun21_{r.year}",)
        for scope in scopes:
            bump(periods[scope], rs, ns, payoff)
            bump(groups[scope][category(rs, ns)], rs, ns, payoff)
        bump(races[k[0]], rs, ns, payoff)
        race_meta[k[0]] = dict(race_id=k[0], race_date=str(r.race_date), year=int(r.year), mass=mass)
        if k[0] == KNOWN:
            known.append(dict(horse_id=k[1], horse_number=int(r.horse_number), won=bool(r.won),
                              odds=float(r.odds), p_raw=float(r.p), p_normalized=float(n.p), mass=mass,
                              ev_raw=float(r.p) * float(r.odds), ev_normalized=float(n.p) * float(r.odds),
                              raw_stake=rs, normalized_stake=ns,
                              delta_profit=(ns - rs) * (payoff - 1)))
    assert set(raw_stakes).issubset(seen) and set(norm_stakes).issubset(seen)
    for scope, stats in periods.items():
        for arm, prefix in ((parent, "raw"), (candidate, "normalized")):
            if scope.startswith("jan1_jun21_"):
                continue
            if scope == "pre2026":
                target = {k: sum(summary["arms"][arm]["by_year"][str(y)]["policy"][k] for y in range(2019, 2026))
                          for k in ("stake_yen", "payout_yen", "profit_yen")}
            else:
                target = summary["arms"][arm]["policy"] if scope == "all" else summary["arms"][arm]["by_year"][scope]["policy"]
            for metric in ("stake", "payout", "profit"):
                assert abs(stats[f"{prefix}_{metric}"] - target[f"{metric}_yen"]) < 1e-5, (arm, scope, metric)
        for metric in stats:
            assert abs(stats[metric] - sum(g[metric] for g in groups[scope].values())) < 1e-5
    for arm, prefix in ((parent, "raw"), (candidate, "normalized")):
        saved = pd.read_parquet(COMP / f"{arm}_races.parquet")
        for r in saved.itertuples(index=False):
            actual = races[str(r.race_id)]
            for metric in ("stake", "payout", "profit"):
                assert abs(actual[f"{prefix}_{metric}"] - getattr(r, f"{metric}_yen")) < 1e-6
    ranked = [dict(**race_meta[k], **v) for k, v in races.items() if race_meta[k]["year"] == 2026]
    ranked.sort(key=lambda row: (row["delta_profit"], row["race_date"], row["race_id"]))
    positive = sorted(ranked, key=lambda row: (-row["delta_profit"], row["race_date"], row["race_id"]))
    return dict(periods=dict(periods), groups={k: dict(v) for k, v in groups.items()},
                max_abs_normalization_error=max_norm_error, known_race=known,
                known_race_totals=races[KNOWN], top_minus_2026=ranked[:10],
                top_plus_2026=positive[:10],
                profit_delta_2026_without_known=periods["2026"]["delta_profit"] - races[KNOWN]["delta_profit"],
                all_race_count=len(races), all_horse_count=len(seen))


def root_crosscheck(comparisons):
    path = ROOT / "artifacts/roi-normalization-followup-20261025/attribution.json"
    root = json.loads(path.read_text())
    assert root["new_fits"] == root["new_policies_scored"] == 0
    count = 0
    for parent, independent in comparisons.items():
        def lookup(scope):
            r = root["parents"][parent]
            if scope == "all":
                return r["all"]
            if scope == "pre2026":
                return r["before2026"]
            if scope.startswith("jan1_jun21_"):
                return r["jan1_jun21_by_year"][scope.rsplit("_", 1)[1]]
            return r["by_year"][scope]

        def compare(ours, theirs):
            nonlocal count
            assert ours["n_rows"] == theirs["rows"]
            assert ours["n_wins"] == theirs["wins"]
            assert abs(ours["delta_profit"] - theirs["delta_yen"]) < 1e-6
            for prefix, root_prefix in (("raw", "raw"), ("normalized", "normalized")):
                assert ours[f"n_{prefix}_bets"] == theirs[root_prefix]["n_bets"]
                for metric in ("stake", "payout", "profit"):
                    assert abs(ours[f"{prefix}_{metric}"] - theirs[root_prefix][f"{metric}_yen"]) < 1e-6
                    count += 1

        for scope, totals in independent["periods"].items():
            r = lookup(scope)
            compare(totals, r["totals"])
            for group, stats in independent["groups"][scope].items():
                compare(stats, r["groups"]["common_same_stake" if group == "same" else group])
        y26 = root["parents"][parent]["year2026"]
        assert abs(independent["profit_delta_2026_without_known"] - y26["without_known_race_delta_yen"]) < 1e-6
        assert abs(independent["known_race_totals"]["delta_profit"] - y26["known_race_delta_yen"]) < 1e-6
        for ours, theirs in zip(independent["top_minus_2026"], y26["top_negative_races"], strict=True):
            assert ours["race_id"] == theirs["race_id"]
            assert abs(ours["delta_profit"] - theirs["delta"]) < 1e-6
        for ours, theirs in zip(independent["top_plus_2026"], y26["top_positive_races"], strict=True):
            assert ours["race_id"] == theirs["race_id"]
            assert abs(ours["delta_profit"] - theirs["delta"]) < 1e-6
    return dict(root_attribution_sha256=sha(path), matched_monetary_values=count,
                periods="all/pre2026/eachyear/Jan1-Jun21_eachyear", independent_code_imported_root=False,
                matched_2026_top_negative=10, matched_2026_top_positive=10,
                rank_ties="date ascending then race_id ascending")


def main():
    provenance = verify_hashes()
    summary = json.loads((COMP / "summary.json").read_text())
    result = dict(provenance=provenance, new_fits=0, new_policy_scores=0,
                  original_saved_policies_reconciled=4,
                  comparisons={p: calculate(p, c, summary) for p, c in PAIRS.items()})
    inputs = [COMP / "summary.json", OLD / "protocol.json"]
    inputs += [COMP / f"{a}_{kind}.parquet" for p, c in PAIRS.items() for a in (p, c)
               for kind in ("ensemble", "bets", "races")]
    result["input_sha256"] = {str(p.relative_to(ROOT)): sha(p) for p in inputs}
    result["root_crosscheck"] = root_crosscheck(result["comparisons"])
    result["script_sha256"] = sha(__file__)
    path = HERE / "independent_verify.json"
    with path.open("w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, allow_nan=False)
        f.write("\n")
    print(json.dumps({p: {"2026": r["periods"]["2026"], "known": r["known_race_totals"],
                         "without_known": r["profit_delta_2026_without_known"]}
                      for p, r in result["comparisons"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
