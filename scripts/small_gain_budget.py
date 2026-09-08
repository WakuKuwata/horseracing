"""Prepare a prospective small-gain confirmation and reserve its non-refundable slot.

This does not train, evaluate, or promote a model. Use one shared persistent ledger;
copies or deletions of that ledger cannot be detected by a local file protocol.
"""
from __future__ import annotations

import copy
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile


class BudgetError(ValueError):
    """Invalid registration or exhausted confirmation budget."""


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def _hash(value: object) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise BudgetError(message)


def _positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _validate(manifest: dict, policy: dict) -> dict:
    _require(policy.get("version") == "small-gains-v1", "unknown policy version")
    plan = policy["confirmation"]
    # Freeze the operational budget for this policy version, not merely its arithmetic.
    expected = {"max_submissions_per_year": 8, "annual_one_sided_alpha": .05,
                "bootstrap_alpha": .0125, "bootstrap_b": 4000,
                "required_seed_count": 3}
    _require(all(plan.get(k) == v for k, v in expected.items()), "confirmation plan changed")
    _require(type(plan["max_submissions_per_year"]) is int
             and type(plan["bootstrap_b"]) is int
             and type(plan["required_seed_count"]) is int, "plan counts must be integers")
    _require(math.isclose(plan["bootstrap_alpha"] / 2 * plan["max_submissions_per_year"],
                         plan["annual_one_sided_alpha"], rel_tol=0, abs_tol=1e-12),
             "annual alpha budget mismatch")
    _require(isinstance(manifest.get("bundle_id"), str)
             and bool(manifest["bundle_id"].strip()), "bundle_id required")
    for key in ("bundle_manifest_sha256", "anchor_model_sha256"):
        _require(isinstance(manifest.get(key), str)
                 and re.fullmatch(r"[0-9a-f]{64}", manifest[key]) is not None,
                 f"{key} must be a lowercase SHA-256")
    used = dt.date.fromisoformat(manifest["selection_used_through"])
    frozen = dt.datetime.fromisoformat(manifest["frozen_at"].replace("Z", "+00:00"))
    _require(frozen.tzinfo is not None and frozen.utcoffset() == dt.timedelta(0),
             "frozen_at must be UTC with timezone")
    now = _now()
    _require(frozen <= now, "frozen_at cannot be in the future")
    window = manifest["eval_window"]
    start, end = dt.date.fromisoformat(window["from"]), dt.date.fromisoformat(window["to"])
    _require(start > used and start > frozen.date(), "confirmation must start after selection and freeze")
    _require(start > now.date(), "confirmation must start after actual reservation date")
    _require(end >= start, "invalid confirmation window")
    _require(_positive_int(window.get("min_eval_days")), "min_eval_days must be positive integer")
    _require(window["min_eval_days"] <= (end - start).days + 1, "min_eval_days exceeds window")
    seeds = manifest["seed_check"]
    values = seeds["seeds"]
    _require(isinstance(values, list) and all(type(s) is int and s >= 0 for s in values)
             and len(values) >= plan["required_seed_count"] and len(set(values)) == len(values),
             "seeds must contain at least three unique nonnegative integers")
    mean = seeds["mean_diff"]
    _require(type(mean) in (int, float) and math.isfinite(mean) and mean < 0,
             "seed mean_diff must be finite and negative")
    _require(type(seeds.get("selected_seed")) is int and seeds["selected_seed"] in values,
             "selected_seed must be in seeds")
    _require(seeds.get("fixed_before_results") is True, "seed must be fixed before results")
    for key in ("primary_regime", "power_plan_ref"):
        _require(isinstance(manifest.get(key), str) and bool(manifest[key].strip()), f"{key} required")
    cfg = copy.deepcopy(policy["gate_template"])
    _require(cfg.get("evaluation_contract_version") == "v4", "template must use v4")
    _require(type(cfg.get("min_effect_delta")) in (int, float)
             and cfg["min_effect_delta"] == 0, "template must use zero delta")
    _require(cfg.get("delta_derivation_ref") == "specs/112-small-gain-adoption/delta-derivation.json",
             "unexpected delta provenance")
    _require(cfg.get("primary_metric") == "winner_nll", "primary metric must be winner_nll")
    _require(not cfg.get("opportunity_set"), "opportunity set is not supported by this policy")
    _require(type((cfg.get("arms") or {}).get("seed")) is int
             and cfg["arms"]["seed"] == seeds["selected_seed"],
             "template arms.seed must match the preselected seed")
    _require((cfg.get("seed_noise") or {}).get("k_seeds") == 1,
             "single preselected model requires seed_noise.k_seeds=1")
    cfg["eval_window"] = copy.deepcopy(window)
    cfg.setdefault("bootstrap", {}).update(alpha=plan["bootstrap_alpha"], b=plan["bootstrap_b"])
    _require(cfg["bootstrap"].get("block") == "race_day", "bootstrap must cluster race days")
    # All values, including metadata outside the required fields, must be stable JSON.
    _json_bytes(manifest)
    _json_bytes(policy)
    return cfg


def _read_ledger(path: Path, policy_hash: str) -> dict:
    if not path.exists():
        return {"schema_version": 1, "policy_sha256": policy_hash, "reservations": []}
    ledger = json.loads(path.read_text())
    _require(ledger.get("schema_version") == 1, "unknown ledger schema")
    _require(ledger.get("policy_sha256") == policy_hash, "ledger policy/config changed")
    entries = ledger.get("reservations")
    _require(isinstance(entries, list), "invalid ledger reservations")
    seen_ids, seen_hashes, counts = set(), set(), {}
    for entry in entries:
        identity, digest, year = entry["bundle_id"], entry["bundle_manifest_sha256"], entry["year"]
        _require(identity not in seen_ids and digest not in seen_hashes, "duplicate ledger reservation")
        seen_ids.add(identity)
        seen_hashes.add(digest)
        counts[year] = counts.get(year, 0) + 1
        _require(entry["slot"] == counts[year] and counts[year] <= 8, "invalid ledger slot sequence")
        _require(_hash(entry["manifest"]) == entry["registration_sha256"], "ledger manifest changed")
        _require(entry["manifest"]["bundle_id"] == identity
                 and entry["manifest"]["bundle_manifest_sha256"] == digest
                 and dt.date.fromisoformat(entry["manifest"]["eval_window"]["from"]).year == year,
                 "ledger identity changed")
    return ledger


def _atomic_write(path: Path, payload: bytes) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def prepare_confirmation(manifest: dict, policy: dict, ledger_path: Path) -> dict:
    """Validate, reserve one annual slot atomically, then return the frozen config.

    A retry of an already reserved bundle fails. A lost response still consumes the
    slot: there is deliberately no refund or overwrite API. The caller must persist
    the returned config and its gate_config_hash before examining confirmation data.
    """
    try:
        cfg = _validate(manifest, policy)
        policy_hash = _hash(policy)
        path = Path(ledger_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_name(path.name + ".lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            ledger = _read_ledger(path, policy_hash)
            entries = ledger["reservations"]
            _require(not any(e["bundle_id"] == manifest["bundle_id"]
                             or e["bundle_manifest_sha256"] == manifest["bundle_manifest_sha256"]
                             for e in entries), "bundle already reserved (including renamed bundles)")
            year = dt.date.fromisoformat(manifest["eval_window"]["from"]).year
            reserved_at = _now()
            _require(dt.date.fromisoformat(manifest["eval_window"]["from"]) > reserved_at.date(),
                     "confirmation must start after actual reservation date")
            slot = sum(e["year"] == year for e in entries) + 1
            _require(slot <= policy["confirmation"]["max_submissions_per_year"], "annual budget exhausted")
            entries.append({"bundle_id": manifest["bundle_id"],
                            "bundle_manifest_sha256": manifest["bundle_manifest_sha256"],
                            "registration_sha256": _hash(manifest), "year": year, "slot": slot,
                            "reserved_at": reserved_at.isoformat(),
                            "manifest": copy.deepcopy(manifest)})
            cfg["adoption_policy"] = {"version": policy["version"], "policy_sha256": policy_hash,
                                      "manifest": copy.deepcopy(manifest), "year": year, "slot": slot,
                                      "ledger_sha256": _hash(ledger)}
            _atomic_write(path, _json_bytes(ledger) + b"\n")
        return cfg
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        if isinstance(exc, BudgetError):
            raise
        raise BudgetError(f"invalid confirmation registration: {exc}") from exc
