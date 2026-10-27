"""Actual observer SELECT smoke and negative ACL catalog checks; no business writes."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from sqlalchemy import text
from scripts.roi_explore.live_preflight_169.config import (
    load_profile,
    profile_environment,
)
from scripts.roi_explore.live_preflight_169.database import (
    native_readonly_probe,
    CONTRACT,
)
from scripts.roi_explore.db_runtime_166.database import database_only
from scripts.roi_explore.prospective_rehearsal_162.store import publish


def run(profile_path, root):
    root = Path(root)
    profile = load_profile(profile_path)
    if profile.problems:
        raise ValueError("invalid_observer_profile")
    checks = []
    with (
        database_only() as network,
        profile_environment(profile),
        native_readonly_probe(profile) as (conn, identity, audit),
    ):
        for table, columns in CONTRACT["select_relations"].items():
            statement = (
                "SELECT "
                + ",".join('"' + name + '"' for name in columns)
                + ' FROM public."'
                + table
                + '" LIMIT 0'
            )
            result = conn.execute(text(statement))
            if list(result.keys()) != columns or result.fetchall():
                raise ValueError("read_projection_smoke_failed")
            checks.append(
                dict(table=table, selected_columns=columns, rows=0, status="passed")
            )
        count = conn.execute(
            text("SELECT count(*) FROM public.race_results WHERE race_id=:race_id"),
            dict(race_id="202605040907"),
        ).scalar_one()
        if count != 15:
            raise ValueError("count_with_column_acl_failed")
        acl = [
            dict(r)
            for r in conn.execute(
                text("""SELECT c.relname,c.relkind,
            has_any_column_privilege(current_user,c.oid,'SELECT') AS any_select,
            has_any_column_privilege(current_user,c.oid,'INSERT') AS any_insert,
            has_any_column_privilege(current_user,c.oid,'UPDATE') AS any_update,
            has_table_privilege(current_user,c.oid,'DELETE') AS can_delete,
            has_table_privilege(current_user,c.oid,'TRUNCATE') AS can_truncate
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f') ORDER BY c.relname""")
            ).mappings()
        ]
        for row in acl:
            if (
                row["can_delete"]
                or row["can_truncate"]
                or (
                    (row["any_insert"] or row["any_update"])
                    and row["relname"] != "fetch_throttle_state"
                )
            ):
                raise ValueError("unneeded_business_write_privilege")
            if row["any_select"] and row["relname"] not in CONTRACT["select_relations"]:
                raise ValueError("unneeded_public_relation_read_privilege")
        sequences = [
            dict(r)
            for r in conn.execute(
                text("""SELECT c.relname,
            has_sequence_privilege(current_user,c.oid,'USAGE') AS usage,
            has_sequence_privilege(current_user,c.oid,'SELECT') AS select_sequence,
            has_sequence_privilege(current_user,c.oid,'UPDATE') AS update_sequence
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relkind='S' ORDER BY c.relname""")
            ).mappings()
        ]
        if any(
            row["usage"] or row["select_sequence"] or row["update_sequence"]
            for row in sequences
        ):
            raise ValueError("unneeded_sequence_privilege")
    payload = dict(
        checked_at=dt.datetime.now(dt.UTC).isoformat(),
        status="passed",
        identity=identity,
        projection_checks=checks,
        count_star_with_column_select=15,
        public_relation_acls=acl,
        sequence_acls=sequences,
        new_collection=False,
        business_write_probe=False,
        source_slot_reserved=False,
        network_boundary=network,
    )
    publish(root / "observer-read-smoke.json", payload)
    publish(root / "observer-read-smoke-audit.json", audit)
    return dict(
        status="passed",
        relation_projection_checks=len(checks),
        extra_business_writes=False,
        new_collection=False,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.profile, args.root), indent=2))
    except Exception as error:
        print(
            json.dumps(
                dict(
                    status="blocked",
                    error_class=type(error).__name__,
                    private_details_not_printed=True,
                )
            )
        )
        raise SystemExit(1) from None
