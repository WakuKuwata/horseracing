"""Score the fixed binary74 normalized candidate and three original controls."""

from __future__ import annotations
import argparse
import json
from pathlib import Path
import time
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


research = load("roi153_report_run", HERE / "run.py")
models = research.models
common = research.common
normalize = common.normalize
agg = load("roi153_ensemble", HERE / "ensemble.py")
experiment = load("roi153_experiment", HERE / "experiment.py")
LABELS = dict(
    current_reference="現行保存予測（参照）",
    baseline="現行77特徴・同条件基準",
    weighted_baseline="③・77特徴raw",
    normalized_weighted_baseline="③・完全正規化",
    normalized_market_drop3="市場3列除去・74特徴・完全正規化",
)


def markdown(result):
    lines = [
        "# 153 市場3列除去・74特徴モデル",
        "",
        "2019〜2026-06-21、24,855レース・343,883頭・807日。単勝EV>1.2、1レース予算1,000円。",
        "元③からodds/popularity/odds_rankのみ除去。重み・300round・15seed維持、raw平均後全頭で完全正規化。",
        "確定オッズproxy仮想精算。q等の市場情報は残る。",
        "",
        "| 条件 | 投資額 | 払戻 | 回収率 | 利益 | winner NLL |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for arm, r in result["arms"].items():
        p = r["policy"]
        lines.append(
            f"| {LABELS[arm]} | {p['stake_yen']:,.0f}円 | {p['payout_yen']:,.0f}円 | {p['deployed_roi'] * 100:.2f}% | {p['profit_yen']:+,.0f}円 | {r['quality']['winner_nll']:.6f} |"
        )
    lines += [
        "",
        "## 3固定比較",
        "",
        "開催日4000回paired bootstrap・各指標内family3 Bonferroni。主指標は共通予算利益差。",
    ]
    for key, r in result["paired"]["results"].items():
        lo, hi = r["profit_per_budget"]["bonferroni_familywise95_ci"]
        lines.append(
            f"- {key}: 共通予算利益差{r['profit_per_budget']['difference'] * 100:+.4f}pt、補正95%区間[{lo * 100:+.4f},{hi * 100:+.4f}]pt。"
        )
    lines += [
        "",
        "## 年別",
        "",
        "| 年 | 条件 | 投資額 | 回収率 | 利益 |",
        "|---|---|---:|---:|---:|",
    ]
    for year in result["arms"]["baseline"]["by_year"]:
        for arm, r in result["arms"].items():
            p = r["by_year"][year]["policy"]
            lines.append(
                f"| {year} | {LABELS[arm]} | {p['stake_yen']:,.0f}円 | {p['deployed_roi'] * 100:.2f}% | {p['profit_yen']:+,.0f}円 |"
            )
    lines += ["", "## 感度・リスク", ""]
    for key, r in result["sensitivity"].items():
        lines.append(
            f"- {key}: 既知105.7倍双方除外利益差{r['without_known_race_delta_yen']:+,.0f}円、最大正差レース{r['top_difference_race']}双方除外{r['without_top_difference_yen']:+,.0f}円。診断のみ。"
        )
    for arm, r in result["arms"].items():
        p = r["policy"]
        lines.append(
            f"- {LABELS[arm]}: 最大DD{p['max_race_settlement_drawdown_yen']:,.0f}円、上位5払戻0の利益{p['profit_without_top5_payouts_yen']:+,.0f}円。"
        )
    lines += [
        "",
        *[f"- {x}" for x in result["limitations"]],
        "",
        f"Protocol SHA256: `{result['protocol_sha256']}`",
        "",
    ]
    return "\n".join(lines)


def main():
    started = time.monotonic()
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--out", type=Path, default=research.OUT)
    args = parser.parse_args()
    out = args.out
    proto = research.verify(out)
    marker = json.loads((out / "models_complete.json").read_text())
    if (
        marker["protocol_sha256"] != models.sha(out / "protocol.json")
        or marker["fits"] != 120
        or marker["jobs"] != 120
        or len(marker["hashes"]) != 360
    ):
        raise ValueError("Completion marker differs")
    for name, digest in marker["hashes"].items():
        p = (out / name).resolve()
        if not p.is_relative_to(out.resolve()) or models.sha(p) != digest:
            raise ValueError("Completion artifact changed")
    _, paths = experiment.configuration(out)
    expected = experiment.expected_recipes(
        paths, proto["window"]["model_outer"], proto["bundles"]
    )
    for year in proto["window"]["model_outer"]:
        for seed in proto["seeds"]:
            experiment.verify_completed(
                experiment.job_args(out, year, seed, paths), proto, expected[year]
            )
    frames, _ = research.references()
    raw, details = agg.ensemble(out, proto, models, research)
    frames[research.CANDIDATE] = normalize(raw)
    dest = out / "comparison"
    dest.mkdir(exist_ok=False)
    scores, tables = {}, {}
    previous = json.loads((research.OLD / "comparison/summary.json").read_text())
    for arm, frame in frames.items():
        pd.testing.assert_frame_equal(
            frames["baseline"][models.PRED_KEYS],
            frame[models.PRED_KEYS],
            check_exact=True,
        )
        scores[arm], tables[arm], bets = common.prior.score(frame)
        if arm != research.CANDIDATE:
            if models.canonical(scores[arm]) != models.canonical(previous["arms"][arm]):
                raise ValueError("Frozen control score differs")
            saved = pd.read_parquet(research.OLD / "comparison" / f"{arm}_bets.parquet")
            pd.testing.assert_frame_equal(
                bets.reset_index(drop=True),
                saved.reset_index(drop=True),
                check_exact=True,
            )
        frame.to_parquet(dest / f"{arm}_ensemble.parquet", index=False)
        tables[arm].to_parquet(dest / f"{arm}_races.parquet", index=False)
        bets.to_parquet(dest / f"{arm}_bets.parquet", index=False)
    quality = scores[research.CANDIDATE]["quality"]
    if quality["races"] != 24855 or quality["rows"] != 343883:
        raise ValueError("Frozen population differs")
    boot = proto["bootstrap"]
    result = dict(
        arms=scores,
        paired=common.paired(
            tables,
            proto["contrasts"],
            boot["repetitions"],
            boot["seed"],
            boot["family_size"],
        ),
        sensitivity=common.sensitivity(tables, proto["contrasts"]),
        mass_diagnostics={a: common.mass_diagnostic(f) for a, f in frames.items()},
        fits=details,
        new_fits=len(details),
        reused_control_fits=240,
        normalization=proto["normalization"],
        worker_fit_seconds=sum(x["elapsed_s"] for x in details),
        wall_seconds=marker["elapsed_s"],
        report_wall_seconds=time.monotonic() - started,
        protocol_sha256=models.sha(out / "protocol.json"),
        ensemble_rule="mean15 raw binary74 probabilities FIRST then all-starter full normalization",
        settlement="closing_odds_proxy",
        can_adopt=False,
        untouched_holdout=False,
        eligible_for_verdict=False,
        limitations=proto["limitations"],
    )
    research.verify(out)
    research.write(dest / "summary.json", result)
    (dest / "report.md").write_text(markdown(result))
    print(
        json.dumps(
            dict(status="complete", out=str(dest), paired=result["paired"]),
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
