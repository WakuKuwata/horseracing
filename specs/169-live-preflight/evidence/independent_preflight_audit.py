"""Direct JSON/SHA/catalog audit; no 169 implementation, DB, HTTP or model imports."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import re
import stat
from pathlib import Path
from urllib.robotparser import RobotFileParser

ROOT = Path(__file__).resolve().parents[3]
ORIGIN = "https://race.netkeiba.com"
UA = "horseracing-live/0.1 (personal use; contact via repo)"


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def at(stamp):
    value = dt.datetime.fromisoformat(stamp)
    assert value.tzinfo is not None
    return value.astimezone(dt.UTC)


def no_mutating_sql(sql):
    bare = re.sub(r"'(?:''|[^'])*'", "''", sql)
    bare = re.sub(r'"(?:""|[^"])*"', '""', bare)
    normalized = " ".join(bare.upper().split()).rstrip(";")
    assert "--" not in bare and "/*" not in bare and ";" not in normalized
    if normalized.startswith(("SELECT ", "SHOW ")):
        assert not re.search(
            r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|ALTER|DROP|TRUNCATE|COPY|CALL|GRANT|REVOKE|INTO|SET_CONFIG)\b",
            normalized,
        )
        assert "FOR UPDATE" not in normalized
    else:
        assert normalized in (
            "SET TRANSACTION READ ONLY",
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        )


def check_cache(result):
    cache = result["robots"]
    assert cache["can_collect"] is False and cache["cache_refreshed"] is False
    if not cache.get("sha256"):
        return
    path = Path(cache["path"])
    assert sha(path) == cache["sha256"], (
        "existing cache changed after preflight; retain prior report as point-in-time only"
    )
    doc = read(path)
    assert set(doc) == {
        "schema_version",
        "origin",
        "outcome",
        "status",
        "fetched_at",
        "body",
    }
    assert doc["schema_version"] == 1 and doc["origin"] == ORIGIN
    stamp, now = at(doc["fetched_at"]), at(cache["checked_at"])
    body = base64.b64decode(doc["body"], validate=True)
    assert len(body) <= 512 * 1024
    if doc["outcome"] == "rules":
        assert doc["status"] == 200
        parser = RobotFileParser()
        parser.parse(body.decode("utf-8", errors="replace").splitlines())
        allowed = parser.can_fetch(UA, ORIGIN + "/api/api_get_jra_odds.html")
    else:
        assert (
            doc["outcome"] == "absent" and doc["status"] in (404, 410) and body == b""
        )
        allowed = True
    fresh = 0 <= (now - stamp).total_seconds() < 86400
    assert fresh == cache["current_fresh"] and allowed == cache["allowed"]
    assert cache["current_ready"] == (fresh and allowed)
    if cache.get("launch_at"):
        launch = at(cache["launch_at"])
        assert cache["launch_ready"] == (
            fresh and allowed and stamp <= launch < stamp + dt.timedelta(seconds=86400)
        )


def audit_packet(root):
    manifest = read(root / "manifest.json")
    assert (
        manifest["mode"] == "live_preflight_only" and manifest["can_collect"] is False
    )
    assert set(manifest["files"]) | {"manifest.json"} == {
        str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()
    }
    for name, value in manifest["files"].items():
        relative = Path(name)
        assert not relative.is_absolute() and ".." not in relative.parts
        assert sha(root / relative) == value
    for name, value in read(root / "source-pins.json").items():
        assert sha(ROOT / name) == value
    result = read(root / "result.json")
    for name in (
        "can_collect",
        "new_collection",
        "database_write",
        "role_grants_applied",
        "cache_refreshed",
        "slot_reserved",
        "model_inference",
        "live_plan_created",
    ):
        assert result[name] is False, name
    assert result["observe_invocations"] == 0
    assert result["quote_max_age_seconds"] == 1800
    assert result["both_predictions_durable_before_decision_required"] is True
    assert result["network_boundary"]["attempts"] == []
    assert result["native_default_measured"] is False, (
        "current actual artifacts have no valid private live profile"
    )
    text = (root / "result.json").read_text()
    assert "postgresql://" not in text and "postgresql+psycopg://" not in text
    count = 0
    for audit in read(root / "database-audits.json"):
        assert audit["database_write"] is False and audit["new_collection"] is False
        for statement in audit["statements"]:
            no_mutating_sql(statement["sql"])
            assert (
                hashlib.sha256(statement["sql"].encode()).hexdigest()
                == statement["sha256"]
            )
            assert statement["status"] == "completed"
            count += 1
        for transaction in audit["transactions"]:
            assert transaction["event"] == "rollback"
        for snapshot in audit["snapshots"]:
            assert snapshot["rolled_back"] is True
            proof = snapshot["proof"]
            assert proof["default_readonly"] == proof["transaction_readonly"] == "on"
            assert proof["isolation"] == "repeatable read"
    check_cache(result)
    if result["scope"] == "administrative_inventory_not_live_profile":
        data = result["database"]
        assert (
            data["live_native_default_measured"] is False
            and data["forced_inspection_readonly"] is True
        )
        identity = data["catalog"]["identity"]
        assert identity["transaction_readonly"] == "on"
        assert identity["isolation"] == "repeatable read"
        for item in data["role_permission_checks"]:
            assert item["evaluation"]["can_collect"] is False
            assert (
                "live_native_default_not_measured_for_target_profile"
                in item["evaluation"]["blocking_reasons"]
            )
    else:
        assert result["scope"] == "explicit_live_profile_only"
        assert result["native_profile_connection"] == "NOT_RUN_CONFIGURATION_BLOCKED"
        assert (
            result["configuration_problems"]
            and read(root / "database-audits.json") == []
        )
        assert (
            result["profile"]["fallback_used"]
            is result["profile"]["owner_url_used"]
            is False
        )
        private_path = result["profile"].get("profile_path")
        if private_path:
            private = Path(private_path)
            assert private.is_file() and not private.is_symlink()
            assert stat.S_IMODE(private.stat().st_mode) == 0o600
        assert (
            result["throttle"]["status"]
            == "NOT_CHECKED_CONFIGURATION_OR_CONNECTION_BLOCKED"
        )
    return dict(
        path=str(root.relative_to(ROOT)),
        scope=result["scope"],
        state=result["state"],
        statements=count,
        manifest_sha256=sha(root / "manifest.json"),
        native_profile_not_measured=True,
        can_collect=False,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    packets = [
        audit_packet(p.parent)
        for p in sorted(args.root.resolve().rglob("manifest.json"))
    ]
    assert packets
    assert not (ROOT / "artifacts/manual_observation_registry_165").exists()
    result = dict(
        status="independently_verified",
        new_collection=False,
        database_write=False,
        database_requery_performed=False,
        model_inference=False,
        cache_refreshed=False,
        role_grants_applied=False,
        can_collect=False,
        packets=packets,
        total_sql_statements=sum(p["statements"] for p in packets),
    )
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
