"""136: fit the two kill-test candidates as DISTRIBUTABLE bundle members.

Why this exists
---------------
The kill-test saved per-year PREDICTIONS only -- no booster, no preprocessor, no calibrator --
and each of its fits is a walk-forward annual fold.  A bundle member is a different object: one
model trained through the bundle's ``train_through`` (2026-08-23), serialised as the four files
``model.txt`` / ``calibrator.pkl`` / ``preprocessor.pkl`` / ``metadata.json``.  Nothing in the
kill-test can be converted into one, so the candidates cannot be shipped, shadowed, or compared
prospectively until they are fitted again in that form.  This script does that and nothing else.

What it does NOT decide
-----------------------
Six members are fitted (2 candidates x seeds 42/43/44) because six is the superset of every
configuration under discussion: the pre-registered EIGHT-member mixture uses one seed of each
candidate, and the production-consistent TWELVE-member mixture uses all three.  Fitting six
therefore does not presuppose the answer -- see
``specs/136-eight-model-candidate/configuration-rule.json``.  No mixture is assembled here, no
model is promoted, no ACTIVE model is touched, and nothing is written to the database.

How it is built
---------------
The 129 builder ``scripts/candidate_mixture_build.py`` already contains the verified full-window
fit: frozen inputs, the started-all OOF outcome patch, fit validation, artifact writers, and a
serialisation round-trip that requires a bit-exact prediction match.  Re-implementing that would
be re-implementing the part that must not drift.  Instead this script REBINDS that module's
member table, recipe factory, work directory and config, and calls its functions.  It never
edits the 129 builder and never writes into the 129 artifact tree.

The recipe is the production joint member's recipe with the candidate's LightGBM parameters
added: same 125 columns (16 drops), pl_topk, OOF isotonic over 8 blocks, weight mask 0.5 /
seed 20260810, target encoding on jockey_id + trainer_id with smoothing 10.  That is exactly
``member_kill_test.recipe_for`` -- the same recipe the 42 saved member-years were fitted with --
so the new members are the kill-test candidates, at the bundle's training boundary.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import candidate_mixture_build as B  # noqa: E402
from horseracing_training.recipe import ModelRecipe  # noqa: E402

# Deliberately a TOP-LEVEL artifacts directory, not a subdirectory of the 136 evidence tree:
# ``assert_no_other_training`` globs ``artifacts/*/running.lock`` at exactly one level, so a lock
# nested any deeper would be invisible to it and two research trainings could run at once.
WORK = ROOT / "artifacts/136-candidate-members"

#: The joint branch's 16 dropped columns, read off the SHIPPED production member rather than
#: retyped, so a divergence is impossible by construction.
JOINT_DROPS = tuple(
    json.loads(
        (ROOT / "artifacts/129-candidate-mixture-serving/members/joint-42/metadata.json").read_text()
    )["recipe"]["drop_features"]
)

#: Verbatim from the frozen kill-test candidate table (member_prereg_v2.json / member_kill_test.py).
CANDIDATES = {
    "extra_trees_joint": {
        "params": {"extra_trees": True},
        "why": "random split thresholds - the strongest decorrelator available",
    },
    "leaves63_child100_joint": {
        "params": {"num_leaves": 63, "min_child_samples": 100},
        "why": "wider trees with a larger leaf floor - deeper interactions, guarded",
    },
}
SEEDS = (42, 43, 44)
BASE_ROUNDS = 900
MASK_RATE = 0.5
MASK_SEED = 20260810

MEMBERS = [
    dict(id=f"{name}-{seed}", branch=name, seed=seed)
    for name in ("extra_trees_joint", "leaves63_child100_joint")
    for seed in SEEDS
]


def make_recipe(member, smoke=False):
    """The production joint recipe plus the candidate's LightGBM parameters.

    Mirrors ``member_kill_test.recipe_for`` exactly: ``n_estimators`` is the base rounds unless
    the candidate overrides it, the parameter tuple is sorted, and every other field is the
    shipped joint member's.  ``member_for`` is called first so an unregistered id cannot reach
    a fit through this factory.
    """
    if member != B.member_for(member.get("id")):
        raise ValueError("Member identity changed")
    spec = CANDIDATES[member["branch"]]
    params = dict(spec["params"])
    rounds = params.pop("n_estimators", BASE_ROUNDS)
    params = {"n_estimators": 5 if smoke else rounds, **params}
    return ModelRecipe(
        objective="pl_topk", calibration="none", calib_frac=0.0, seed=member["seed"],
        drop_features=list(JOINT_DROPS),
        params=tuple(sorted(params.items())),
        weight_mask_rate=MASK_RATE, weight_mask_seed=MASK_SEED,
        target_encode_cols=("jockey_id", "trainer_id"), te_smoothing=10.0,
    )


def build_config():
    """The 129 config shape with this feature's identity and the candidate parameters recorded.

    ``full_outer_fits`` / ``full_boosters`` keep the same arithmetic as 129 (6 outer fits, each
    producing 8 OOF boosters) because the member count and OOF block count are the same.
    """
    return dict(
        schema_version=1, artifact_kind="candidate_mixture_build_config",
        profile="136_candidate_members_v1", mode="shadow",
        train_from="2007-01-01", train_through=str(B.CUTOFF), coefficient_train_through="2025-12-31",
        members=deepcopy(MEMBERS), full_outer_fits=6, full_boosters=48,
        n_estimators=BASE_ROUNDS, n_oof_blocks=8, num_threads=1, max_workers=2,
        weight_mask_rate=MASK_RATE, weight_mask_seed=MASK_SEED, base_eps=1e-6,
        db_registration=False, additional_coefficient_fits=0,
        can_adopt=False, eligible_for_verdict=False,
        candidate_params={n: CANDIDATES[n]["params"] for n in sorted(CANDIDATES)},
        joint_drop_features=list(JOINT_DROPS),
        smoke={"train_through": "2007-12-31", "probe_from": "2008-01-01",
               "probe_through": "2008-01-31", "n_estimators": 5, "n_oof_blocks": 2,
               "outer_fits": 6, "boosters": 12},
        parity={"scope": "frozen raw inputs, all started horses and all three heads on fixed year representatives",
                "absolute_tolerance": 0.,
                "purpose": "serialization only; no efficacy or serving-regime claim"},
    )


PRESERVED = ROOT / "artifacts/136-eight-model-candidate/preserved-killtest-2cced7b2"
SNAPSHOT = ROOT / "artifacts/111-ability-observation/snapshot.pkl"
PROD_JOINT = ROOT / "artifacts/129-candidate-mixture-serving/members/joint-42/metadata.json"


def _fit_code_paths():
    """Every source file that can change what a fit produces.

    Same set the 118 research driver binds: the five packages whose code builds the matrix,
    fits the booster, runs the OOF calibration and assembles the heads, plus the serving loader
    used by the serialisation round-trip and this driver.
    """
    paths = [Path(__file__).resolve(), ROOT / "scripts/candidate_mixture_build.py",
             ROOT / "serving/src/horseracing_serving/__init__.py",
             ROOT / "serving/src/horseracing_serving/model_loader.py"]
    for package in ("db", "features", "training", "eval", "probability"):
        paths.extend((ROOT / package / "src").rglob("*.py"))
    return sorted(set(paths))


def source_hash():
    """Hash of the code that fits -- NOT a re-use of the 129 build's source hash.

    129's ``source_hash`` folds in ``joint_residual_stack.source_hash()``, which requires the
    whole 123/124/125 research chain to verify.  That chain is the evidence for WHY the six
    production members were chosen.  These two candidates were chosen by the kill-test instead,
    so demanding the 125 chain would bind them to provenance that is not theirs -- and it no
    longer verifies on this working tree anyway, because commit bdc789e changed
    training/ and eval/ source that the chain digests.

    What matters for a fit is that the fitting code does not move between ``prepare`` and the
    last member: this value is frozen at prepare and re-checked before and after every fit.
    """
    return B.s.p.stable_hash(
        {str(path.relative_to(ROOT)): B.digest(path) for path in _fit_code_paths()}
    )


def upstream_state():
    """Bind the inputs and the SELECTION evidence these candidates actually rest on.

    Three things, all content-addressed:
      * the sealed 111 snapshot -- the training inputs, shared with 130's annual caches;
      * the shipped production joint member -- where the recipe's 16 drops are read from, so a
        change there cannot silently redefine "the joint branch";
      * the frozen kill-test -- its pre-registrations and all 42 member-year receipts, i.e. the
        evidence that selected these two candidates.
    """
    cache = PRESERVED / "s2/cache/joint125"
    receipts = {}
    for name in sorted(CANDIDATES):
        for seed in SEEDS:
            for year in range(2020, 2027):
                rel = f"{name}-s{seed}-{year}.json"
                receipts[rel] = B.digest(cache / rel)
    if len(receipts) != 42:
        raise ValueError("kill-test receipt set is not the frozen 42 member-years")
    freezes = {
        name: B.digest(PRESERVED / name)
        for name in ("member_prereg_v2.json", "member_prereg_freeze.json",
                     "member_build_addendum.json", "member_build_addendum_freeze.json",
                     "pair_prereg.json", "pair_prereg_freeze.json")
    }
    return {
        "snapshot_sha256": B.digest(SNAPSHOT),
        "production_joint_member_metadata_sha256": B.digest(PROD_JOINT),
        "killtest_preregistrations": freezes,
        "killtest_member_year_receipts": receipts,
        "not_bound_to": ("the 123/124/125 research selection chain -- that is the six production "
                         "members' provenance, not these candidates'"),
    }


#: Captured BEFORE the rebinding below: the replacement calls it, so reading it through the
#: module afterwards would recurse into itself.
_B_SOURCE_PATHS = B.build_source_paths


def build_source_paths():
    """129's bound sources plus this driver, so the freeze covers the code that fits."""
    return [*_B_SOURCE_PATHS(), Path(__file__).resolve()]


# The 129 module resolves these as globals at call time, so rebinding redirects every one of its
# functions (prepare / verify / completed / fit_member / verify_members) at once.  The 129 source
# file itself is never modified, and WORK points outside its artifact tree.
B.MEMBERS = MEMBERS
B.WORK = WORK
B.make_recipe = make_recipe
B.build_config = build_config
B.build_source_paths = build_source_paths
B.source_hash = source_hash
B.upstream_state = upstream_state


def run_jobs(workers=2, smoke=False):
    """129's ``run_jobs``, except the worker subprocess is THIS file.

    The 129 version launches ``candidate_mixture_build.py`` by its own ``__file__``; that
    subprocess would start without the rebindings above and fit the wrong members.  Everything
    else -- the completion check before scheduling, the parent lock, the failure handling that
    refuses to start a new fit after one fails -- is 129's and is called here.
    """
    B.check_workers(workers)
    B.assert_no_other_training()
    cfg, frozen = B.verify()
    pending = [m for m in MEMBERS if not B.completed(m, cfg, frozen, smoke)]
    if not smoke and not all(B.completed(m, cfg, frozen, True) for m in MEMBERS):
        raise ValueError("All six smoke receipts required before the full fit")
    if not pending:
        print(f"NOTHING TO DO smoke={smoke}: every member already complete", flush=True)
        return B.verify_members(smoke)
    lock = WORK / "running.lock"
    B.write_json(lock, {"pid": os.getpid(), "workers": workers, "smoke": smoke,
                        "source_hash": frozen["source_hash"], "members": pending})
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    logs = WORK / "logs" / stamp
    logs.mkdir(parents=True, exist_ok=False)

    def launch(m):
        command = [sys.executable, str(Path(__file__).resolve()), "worker", "--member", m["id"]]
        if smoke:
            command.append("--smoke")
        with (logs / (m["id"] + ".log")).open("x") as fh:
            result = subprocess.run(
                command, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT,
                env={**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                     "MKL_NUM_THREADS": "1"},
            )
        if result.returncode:
            raise RuntimeError(f"Member failed; preserve files and inspect {logs / (m['id'] + '.log')}")

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            queue = iter(pending)
            running = {pool.submit(launch, m)
                       for m in [next(queue, None) for _ in range(workers)] if m is not None}
            while running:
                done, running = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    future.result()
                for _ in done:
                    member = next(queue, None)
                    if member is not None:
                        running.add(pool.submit(launch, member))
    finally:
        lock.unlink()
    return B.verify_members(smoke)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",
                        choices=["prepare", "smoke", "train", "verify-members", "worker", "show"])
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--member", choices=[m["id"] for m in MEMBERS])
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    if args.command == "show":
        for m in MEMBERS:
            r = make_recipe(m)
            print(m["id"], "params=", r.params, "n_drop=", len(r.drop_features), "seed=", r.seed)
        return
    if args.command == "prepare":
        B.prepare()
    elif args.command == "smoke":
        run_jobs(args.workers, True)
    elif args.command == "train":
        run_jobs(args.workers, False)
    elif args.command == "verify-members":
        B.verify_members(args.smoke)
    else:
        if args.member is None:
            parser.error("worker requires --member")
        B.fit_member(args.member, args.smoke)


if __name__ == "__main__":
    main()
