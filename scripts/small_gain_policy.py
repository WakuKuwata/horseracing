"""Prepare a new, prospective confirmation config under small-gains-v1."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from horseracing_eval.decision import assert_confirmatory, gate_config_hash
from horseracing_eval.delta_provenance import assert_delta_provenance
from small_gain_budget import prepare_confirmation

ROOT = Path(__file__).resolve().parents[1]
POLICY_DIR = ROOT / "specs/112-small-gain-adoption"
LEDGER = ROOT / "artifacts/small-gain-policy/confirmation-ledger.json"


def load_policy() -> dict:
    policy = json.loads((POLICY_DIR / "policy.json").read_text())
    if gate_config_hash(policy) != (POLICY_DIR / "policy.hash.txt").read_text().strip():
        raise ValueError("policy hash mismatch")
    derivation = assert_delta_provenance(policy["gate_template"], root=ROOT)
    plan = policy["confirmation"]
    if (derivation.get("judgments_per_year") != plan["max_submissions_per_year"]
            or derivation.get("ci_alpha") != plan["bootstrap_alpha"]
            or derivation.get("acceptable_net_harm_prob") != plan["annual_one_sided_alpha"]
            or derivation.get("per_judgment_fp_budget") != plan["bootstrap_alpha"] / 2):
        raise ValueError("delta provenance budget mismatch")
    return policy


def prepare(manifest: dict, output: Path) -> dict:
    """Preflight before reservation; a write failure after reservation spends the slot."""
    output = Path(output)
    hash_path = output.with_suffix(".hash.txt")
    if output == hash_path or output.exists() or hash_path.exists():
        raise FileExistsError("config and hash destinations must be new, distinct files")
    if not output.parent.is_dir():
        raise ValueError("output parent directory must already exist")
    policy = load_policy()
    preview = copy.deepcopy(policy["gate_template"])
    preview["eval_window"] = manifest["eval_window"]
    assert_delta_provenance(preview, root=ROOT)
    assert_confirmatory(preview, expected_hash=gate_config_hash(preview),
                        eval_window=preview["eval_window"])
    config = prepare_confirmation(manifest, policy, LEDGER)
    digest = gate_config_hash(config)
    assert_confirmatory(config, expected_hash=digest, eval_window=config["eval_window"])
    encoded = json.dumps(config, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with output.open("x") as stream:
        stream.write(encoded)
    with hash_path.open("x") as stream:
        stream.write(digest + "\n")
    return config


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show", help="read current policy without reserving a slot")
    command = sub.add_parser("prepare", help="reserve a non-refundable final submission")
    command.add_argument("--manifest", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "show":
        print(json.dumps(load_policy(), ensure_ascii=False, indent=2))
        return
    config = prepare(json.loads(args.manifest.read_text()), args.output)
    print(json.dumps({"config": str(args.output.resolve()),
                      "gate_config_hash": gate_config_hash(config),
                      "year": config["adoption_policy"]["year"],
                      "slot": config["adoption_policy"]["slot"]}))


if __name__ == "__main__":
    main()
