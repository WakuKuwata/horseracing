"""136: assemble the candidate mixture bundle (profile U) from artifacts that already exist.

What this does
--------------
Writes ``artifacts/136-candidate-bundle/{bundle.json, members/<id>/...}`` describing twelve
members: the six shipped production members plus the six candidates fitted on 2026-09-20.

No fitting, no database, no change to the production bundle or the ACTIVE model. Member files
are COPIED rather than symlinked because ``mixture_model.contained_path`` refuses symlinks and
paths that escape the manifest's directory; the manifest pins every file's SHA256, so a copy
that ever drifted would fail to load.

Weights
-------
Profile ``136_candidate_allseeds_registered_alloc_v1``: joint and anchor seeds at 1/8 each,
candidate seeds at 1/24 each. That keeps each BRANCH at the exposure the pair pre-registration
measured (joint .375 / anchor .375 / A .125 / B .125) while including all three seeds of the new
branches, so no seed has to be chosen. See configuration-rule-amendment.json for why.

Corrections
-----------
Every candidate member carries **joint-42's** coefficients, not its own seed's. That is the
transfer convention C1/C2 were measured under (``member_kill_test.py:216``
``TRANSFER_SOURCE = {"joint_transfer": "joint-42"}``). Shipping matching-seed coefficients would
ship a configuration nobody measured.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "serving/src"))
from horseracing_serving import mixture_model as m  # noqa: E402

PROD = ROOT / "artifacts/129-candidate-mixture-serving"
CAND = ROOT / "artifacts/136-candidate-members"
OUT = ROOT / "artifacts/136-candidate-bundle"
SPEC = ROOT / "specs/136-eight-model-candidate"
BUNDLE_ID = "136-candidate-allseeds-registered-alloc-v1"


def digest(path: Path) -> str:
    return m.sha256(path)


def member_source(member_id: str) -> Path:
    """Where a member's four files live today."""
    if (PROD / "members" / member_id).is_dir():
        return PROD / "members" / member_id
    return CAND / "members" / member_id


def build():
    if OUT.exists():
        raise FileExistsError(f"{OUT} exists; bundles are create-only (remove it deliberately)")
    prod = json.loads((PROD / "bundle.json").read_text())
    prod_corrections = {item["id"]: item["correction"] for item in prod["members"]}
    transfer = prod_corrections["joint-42"]

    specs = m.profile_members(m.CANDIDATE_PROFILE)
    (OUT / "members").mkdir(parents=True)
    members = []
    for spec in specs:
        src = member_source(spec["id"])
        if not src.is_dir():
            raise FileNotFoundError(f"member artifacts missing: {src}")
        dst = OUT / "members" / spec["id"]
        dst.mkdir()
        files = {}
        for name in m.FILES:
            shutil.copy2(src / name, dst / name)
            before, after = digest(src / name), digest(dst / name)
            if before != after:
                raise ValueError(f"copy changed {spec['id']}/{name}")
            files[name] = after
        correction = (prod_corrections[spec["id"]] if spec["id"] in prod_corrections
                      else dict(transfer))
        members.append({
            "id": spec["id"], "branch": spec["branch"], "seed": spec["seed"],
            "weight": spec["weight"], "artifact_dir": "members/" + spec["id"],
            "files": files, "correction": correction,
        })

    manifest = {
        "schema_version": 1, "artifact_kind": "candidate_mixture_bundle",
        "profile": m.CANDIDATE_PROFILE, "mode": "shadow",
        "can_adopt": False, "eligible_for_verdict": False,
        "bundle_id": BUNDLE_ID,
        "created_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "train_through": "2026-08-23", "coefficient_train_through": "2025-12-31",
        "feature_profile": m.feature_profile(), "inference": dict(m.INFERENCE),
        "members": members,
        "sources": {
            "production_bundle": {
                "path": str((PROD / "bundle.json").relative_to(ROOT)),
                "sha256": digest(PROD / "bundle.json"),
                "note": "the six shipped members and their own correction coefficients",
            },
            "candidate_members": {
                str((CAND / "receipts" / f"{spec['id']}.json").relative_to(ROOT)):
                    digest(CAND / "receipts" / f"{spec['id']}.json")
                for spec in specs if (CAND / "receipts" / f"{spec['id']}.json").is_file()
            },
            "correction_transfer": {
                "source_member": "joint-42",
                "applies_to": [s["id"] for s in specs if s["id"] not in prod_corrections],
                "convention": "member_kill_test.py:216 TRANSFER_SOURCE joint_transfer -> joint-42",
                "note": ("candidate members do NOT use their own seed's coefficients; this is the "
                         "convention C1/C2 were measured under"),
            },
            "configuration_decision": {
                "file": str((SPEC / "configuration-rule-amendment.json").relative_to(ROOT)),
                "sha256": digest(SPEC / "configuration-rule-amendment.json"),
            },
            "efficacy_note": ("No efficacy measurement of THIS bundle exists. The historical "
                              "numbers were measured on annual walk-forward fits; these members "
                              "are full-window fits through 2026-08-23 and every build year is "
                              "development period."),
        },
    }
    path = OUT / "bundle.json"
    with path.open("x") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        fh.write("\n")
    return path


def verify(path: Path):
    """Load it the way production would, and report what the manifest actually says."""
    bundle = m.load_mixture_bundle(path, expected_sha256=digest(path))
    specs = m.profile_members(bundle.manifest["profile"])
    weights = [item["weight"] for item in bundle.manifest["members"]]
    by_branch: dict[str, float] = {}
    for spec, w in zip(specs, weights, strict=True):
        by_branch[spec["branch"]] = by_branch.get(spec["branch"], 0.0) + w
    print(f"bundle_id  : {bundle.manifest['bundle_id']}")
    print(f"sha256     : {bundle.sha256}")
    print(f"profile    : {bundle.manifest['profile']}")
    print(f"members    : {len(bundle.members)} loaded")
    print(f"weight sum : {sum(weights)!r}")
    for branch, w in by_branch.items():
        print(f"  branch {branch:26s} {w:.6f}")
    transferred = [i["id"] for i in bundle.manifest["members"]
                   if i["correction"]["coefficients"]
                   == bundle.manifest["members"][0]["correction"]["coefficients"]]
    print(f"members carrying joint-42's coefficients: {transferred}")
    return bundle


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "verify":
        verify(OUT / "bundle.json")
    else:
        verify(build())
