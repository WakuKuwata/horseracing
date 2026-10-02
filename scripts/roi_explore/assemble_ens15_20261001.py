"""Assemble the 15-seed market-ev ensemble artifact (specs/138-attention-conditions plan 0.1・T003).

Input:  artifacts/market_ev/mev-ens15-v1/seed_NN/{model.spec.json, model_YYYY.txt} (s = 1..15, written by
        direct_return_model.py --threads 1 --deterministic --seed s --tag _ens15 --save-last-model …/seed_NN/model)
        and artifacts/roi_explore/results/<tag>/result.json of the same runs.
Output: artifacts/market_ev/mev-ens15-v1/ensemble.spec.json and ensemble_YYYY.json (one manifest per booster year,
        member paths relative to the ensemble dir + sha256 of the exact booster bytes).

Fail-closed: every member must share features / cats / cat_maps / feature_hash / input_rows_sha256 / rounds /
objective / train_from / drop_groups, be single-threaded and deterministic, carry its own seed, and have the same
set of booster years. Nothing is written unless all checks pass.

    cd training && uv run python ../scripts/roi_explore/assemble_ens15_20261001.py [--ens-dir ABS] [--version mev-ens15-v1]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_ENS_DIR = ROOT / "artifacts" / "market_ev" / "mev-ens15-v1"
RESULTS = ROOT / "artifacts" / "roi_explore" / "results"
SEEDS = tuple(range(1, 16))
SHARED_KEYS = ("objective", "features", "cats", "cat_maps", "feature_hash", "input_rows_sha256", "rounds",
               "train_from", "drop_groups", "trained_for_year", "train_through", "training_cutoff_by_year")
_BOOSTER = re.compile(r"^model_(\d{4})\.txt$")


class AssembleError(RuntimeError):
    pass


def result_tag(seed: int) -> str:
    """direct_return_model.py tag rule: seed 1 has no ``_seed1`` segment."""
    return f"armC_binary{'' if seed == 1 else f'_seed{seed}'}_drop-sameday+weightlive_from2007_ens15"


def sha256_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_member(ens_dir: pathlib.Path, results_dir: pathlib.Path, seed: int) -> dict:
    d = ens_dir / f"seed_{seed:02d}"
    spec_path = d / "model.spec.json"
    if not spec_path.exists():
        raise AssembleError(f"missing {spec_path}")
    spec = json.loads(spec_path.read_text())
    res_path = results_dir / result_tag(seed) / "result.json"
    if not res_path.exists():
        raise AssembleError(f"missing {res_path}")
    params = json.loads(res_path.read_text())["params"]
    if spec.get("seed") != seed or params.get("seed") != seed:
        raise AssembleError(f"seed mismatch for seed_{seed:02d}: spec={spec.get('seed')} result={params.get('seed')}")
    if spec.get("num_threads") != 1 or params.get("num_threads") != 1:
        raise AssembleError(f"seed_{seed:02d} was not trained single-threaded")
    if spec.get("deterministic") is not True or params.get("deterministic") is not True:
        raise AssembleError(f"seed_{seed:02d} was not trained with deterministic=True")
    if spec.get("objective") != "binary":
        raise AssembleError(f"seed_{seed:02d} objective is {spec.get('objective')!r}, expected binary")
    years = sorted(int(m.group(1)) for p in d.glob("model_*.txt") if (m := _BOOSTER.match(p.name)))
    if not years:
        raise AssembleError(f"seed_{seed:02d} has no model_YYYY.txt boosters")
    return {"seed": seed, "dir": d, "spec": spec, "years": years}


def assemble(ens_dir: pathlib.Path, results_dir: pathlib.Path, version: str, seeds=SEEDS) -> dict:
    members = [load_member(ens_dir, results_dir, s) for s in seeds]
    ref = members[0]
    for m in members[1:]:
        for k in SHARED_KEYS:
            if m["spec"].get(k) != ref["spec"].get(k):
                raise AssembleError(f"seed_{m['seed']:02d} differs from seed_{ref['seed']:02d} on {k!r}")
        if m["years"] != ref["years"]:
            raise AssembleError(f"seed_{m['seed']:02d} booster years {m['years']} != {ref['years']}")
    spec = {k: ref["spec"][k] for k in SHARED_KEYS}
    spec |= {"version": version, "seeds": [m["seed"] for m in members], "num_threads": 1, "deterministic": True,
             "members": [f"seed_{m['seed']:02d}" for m in members], "booster_years": ref["years"],
             "combine": "arithmetic mean of member win probabilities"}
    manifests = {}
    for y in ref["years"]:
        manifests[y] = {"version": version, "year": y, "members": [
            {"seed": m["seed"], "path": f"seed_{m['seed']:02d}/model_{y}.txt",
             "sha256": sha256_file(m["dir"] / f"model_{y}.txt")} for m in members]}
    return {"spec": spec, "manifests": manifests}


def write(ens_dir: pathlib.Path, assembled: dict) -> list[pathlib.Path]:
    out = [ens_dir / "ensemble.spec.json"]
    out[0].write_text(json.dumps(assembled["spec"], ensure_ascii=False, sort_keys=True, indent=1))
    for y, man in assembled["manifests"].items():
        p = ens_dir / f"ensemble_{y}.json"
        p.write_text(json.dumps(man, ensure_ascii=False, sort_keys=True, indent=1))
        out.append(p)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ens-dir", default=str(DEFAULT_ENS_DIR))
    ap.add_argument("--results-dir", default=str(RESULTS))
    ap.add_argument("--version", default="mev-ens15-v1")
    args = ap.parse_args(argv)
    ens_dir = pathlib.Path(args.ens_dir)
    if not ens_dir.is_absolute():
        raise SystemExit("--ens-dir must be absolute")
    try:
        assembled = assemble(ens_dir, pathlib.Path(args.results_dir), args.version)
    except AssembleError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    paths = write(ens_dir, assembled)
    print(f"OK: members={len(assembled['spec']['members'])} years={assembled['spec']['booster_years']} "
          f"wrote={len(paths)} → {ens_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
