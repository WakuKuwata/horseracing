"""Freeze the single original143 weighted binary74 candidate and all controls."""

from __future__ import annotations
import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sys
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / "artifacts/153-market-three-column-ablation"
PREVIOUS = ROOT / "artifacts/143-existing-feature-roi"
OLD = ROOT / "artifacts/148-postprediction-normalization"
ROWS = ROOT / "artifacts/market_ev/rows_2007.parquet"
ADDITIONS = (
    PREVIOUS / "data/additions.parquet"
)  # bound parent provenance; never model input in153
WEIGHTS = PREVIOUS / "weights.parquet"
CANDIDATE = "normalized_market_drop3"
CONTROLS = ["baseline", "weighted_baseline", "normalized_weighted_baseline"]
CONTRASTS = [[CANDIDATE, b] for b in CONTROLS]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    sys.modules[name] = obj
    spec.loader.exec_module(obj)
    return obj


models = module("roi153_run_models", HERE / "models.py")
legacy = module("roi153_run148", HERE.parent / "roi_improvement_148/run.py")
common = module("roi153_common148", HERE.parent / "roi_improvement_148/report.py")
write = legacy.write
POLICY = legacy.POLICY
BOOTSTRAP = dict(legacy.BOOTSTRAP, family_size=3)
NORMALIZATION = dict(
    parent="market_drop3",
    candidate=CANDIDATE,
    parent_features=74,
    parent_weights="exact original143 annual vectors",
    formula="p_i / sum_all_starters_in_race(p)",
    parent_ensemble="arithmetic raw probability mean of seeds1..15 FIRST",
    before_ev=True,
    denominator="all complete-field starters before selection; no odds or outcomes",
    clipping_before_normalization=False,
    zero_sum="reject",
    fitted_parameters=0,
)


def references():
    legacy.verify(OLD)
    frames, paths = legacy.references()
    frames["normalized_weighted_baseline"] = common.normalize(
        frames["weighted_baseline"]
    )
    for arm, frame in frames.items():
        pd.testing.assert_frame_equal(
            frame,
            pd.read_parquet(OLD / "comparison" / f"{arm}_ensemble.parquet"),
            check_exact=True,
        )
        paths += [
            OLD / "comparison" / f"{arm}_{kind}.parquet"
            for kind in ("ensemble", "races", "bets")
        ]
    return frames, paths


def verify(out):
    doc = models.verify_protocol(out / "protocol.json", ROWS, ADDITIONS, WEIGHTS)
    expected = dict(
        normalization=NORMALIZATION,
        policy=POLICY,
        bootstrap=BOOTSTRAP,
        contrasts=CONTRASTS,
        candidate_features=models.FEATURES,
        dropped_features=models.DROPPED,
        new_fits=120,
        reused_control_fits=240,
        eligible_for_verdict=False,
    )
    for k, v in expected.items():
        if models.canonical(doc.get(k)) != models.canonical(v):
            raise ValueError(f"Protocol constraint differs: {k}")
    return doc


def freeze(out):
    _, inputs = references()
    old = json.loads((OLD / "protocol.json").read_text())
    specdir = ROOT / "specs/153-market-three-column-ablation"
    paths = [ROOT / p for p in old["inputs_and_source_sha256"]]
    paths += [
        *inputs,
        *HERE.glob("*.py"),
        ROOT / "scripts/roi_explore/roi_improvement_145/models.py",
        OLD / "protocol.json",
        OLD / "protocol.sha256",
        OLD / "comparison/summary.json",
        HERE.parent / "roi_improvement_148/run.py",
        HERE.parent / "roi_improvement_148/report.py",
        ROOT / "docs/roi-after-149-150-20261025/report.md",
        ROOT / "scripts/tests/test_roi_models_153.py",
        ROOT / "scripts/tests/test_roi_report_153.py",
        *(
            specdir / p
            for p in (
                "spec.md",
                "plan.md",
                "quickstart.md",
                "tasks.md",
                "evidence/design-second-opinion.md",
                "evidence/preflight-review.json",
            )
        ),
    ]
    doc = dict(
        feature="153-market-three-column-ablation",
        version=1,
        frozen_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        rows=str(ROWS.relative_to(ROOT)),
        additions=str(ADDITIONS.relative_to(ROOT)),
        weights=str(WEIGHTS.relative_to(ROOT)),
        window=dict(
            training_from=2007,
            through="2026-06-26",
            model_outer=list(range(2019, 2027)),
        ),
        seeds=list(range(1, 16)),
        max_workers=2,
        threads_each=1,
        arms=list(models.ARMS),
        bundles=models.BUNDLES,
        models=dict(objective="binary", rounds=300, params=models.base.BASE_PARAMS),
        base_features=models.base.FEATURES,
        candidate_features=models.FEATURES,
        dropped_features=models.DROPPED,
        weight_recipe=models.WEIGHT_RECIPE,
        normalization=NORMALIZATION,
        policy=POLICY,
        bootstrap=BOOTSTRAP,
        contrasts=CONTRASTS,
        new_fits=120,
        reused_control_fits=240,
        can_adopt=False,
        untouched_holdout=False,
        eligible_for_verdict=False,
        sensitivity=dict(
            known_influence_race="202608030111",
            same_calendar_through="06-21",
            top_difference_race="retrospective sensitivity only; never a trading exclusion",
        ),
        inputs_and_source_sha256={
            str(p.relative_to(ROOT)): models.sha(p) for p in sorted(set(paths))
        },
        limitations=[
            *[
                x
                for x in old["limitations"]
                if not x.startswith("Race normalization formula")
            ],
            "Normalization has no fitted parameters; candidate requires120 fixed annual/seed fits; no threshold or hyperparameter search.",
            "Only odds,popularity,odds_rank removed; q and remaining nine market columns retained. Not market-independent.",
            "Original143 teacher weights, including neutral pre2019 rows, preserved exactly; no new OOF.",
            "Only one binary74/full-normalization candidate; no raw-candidate policy or hyperparameter search.",
            "Past feature ablations and repeated historical selection remain part of research history.",
        ],
    )
    out.mkdir(parents=True, exist_ok=True)
    write(out / "protocol.json", doc)
    (out / "protocol.sha256").write_text(models.sha(out / "protocol.json") + "\n")
    return verify(out)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("command", choices=["freeze", "verify"])
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    freeze(args.out) if args.command == "freeze" else verify(args.out)
    print(
        json.dumps(dict(command=args.command, status="complete", out=str(args.out))),
        flush=True,
    )


if __name__ == "__main__":
    main()
