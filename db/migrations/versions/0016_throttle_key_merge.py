"""093: fold per-hostname throttle rows into one row per source.

The throttle key used to be ``_domain(url)`` — scheme + host, so ``https://race.netkeiba.com``
and ``https://db.netkeiba.com`` were separate budgets although they are one operator, and the
site saw twice the intended rate. The key is now the registrable source, ``netkeiba.com``.

Renaming the key without moving the rows would silently discard whatever those rows were
holding. The row carries two things worth keeping:

  * ``next_allowed_at`` — drop it and the first request after deploy goes out inside an interval
    that had not elapsed;
  * ``blocked_until`` — drop it and we resume hammering a source that is *currently blocking us*,
    which is the exact failure this table exists to prevent.

So: take the strictest value across the rows being merged (the furthest-future instant wins in
both cases), write it to the canonical key, and delete the hostname rows. Data-only; no DDL.

Operator note: restart any long-running scrape worker after this runs. A process still on the
old code keeps writing hostname keys, and the two key spaces do not coordinate.

Revision ID: 0016_throttle_key_merge
Revises: 0015_exotic_quotes
"""

from __future__ import annotations

from alembic import op

revision = "0016_throttle_key_merge"
down_revision = "0015_exotic_quotes"
branch_labels = None
depends_on = None

#: Hosts whose rows belong to one source. Kept in lockstep with `throttle_key()` in
#: scrape/politeness.py — that function is the runtime authority; this is the one-time catch-up.
_SOURCE = "netkeiba.com"


def upgrade() -> None:
    # One statement, so the merge and the delete cannot land apart. `GREATEST` over the group
    # keeps the strictest restriction; `max(updated_at)` keeps the freshest bookkeeping.
    op.execute(
        f"""
        WITH legacy AS (
            SELECT
                max(next_allowed_at) AS next_allowed_at,
                max(blocked_until)   AS blocked_until,
                max(updated_at)      AS updated_at
            FROM fetch_throttle_state
            WHERE domain <> '{_SOURCE}'
              AND (domain LIKE '%%{_SOURCE}' OR domain LIKE '%%{_SOURCE}/%%')
        ), reason AS (
            -- the reason belonging to the block we are actually keeping
            SELECT block_reason
            FROM fetch_throttle_state
            WHERE domain <> '{_SOURCE}'
              AND (domain LIKE '%%{_SOURCE}' OR domain LIKE '%%{_SOURCE}/%%')
              AND blocked_until IS NOT NULL
            ORDER BY blocked_until DESC
            LIMIT 1
        )
        INSERT INTO fetch_throttle_state
            (domain, next_allowed_at, blocked_until, block_reason, updated_at)
        SELECT '{_SOURCE}', legacy.next_allowed_at, legacy.blocked_until,
               (SELECT block_reason FROM reason), legacy.updated_at
        FROM legacy
        WHERE legacy.next_allowed_at IS NOT NULL OR legacy.blocked_until IS NOT NULL
        ON CONFLICT (domain) DO UPDATE SET
            next_allowed_at = GREATEST(
                fetch_throttle_state.next_allowed_at, EXCLUDED.next_allowed_at),
            blocked_until = GREATEST(
                fetch_throttle_state.blocked_until, EXCLUDED.blocked_until),
            block_reason = COALESCE(EXCLUDED.block_reason, fetch_throttle_state.block_reason),
            updated_at = GREATEST(fetch_throttle_state.updated_at, EXCLUDED.updated_at)
        """
    )
    op.execute(
        f"""
        DELETE FROM fetch_throttle_state
        WHERE domain <> '{_SOURCE}'
          AND (domain LIKE '%%{_SOURCE}' OR domain LIKE '%%{_SOURCE}/%%')
        """
    )


def downgrade() -> None:
    # Splitting one budget back into per-host budgets would hand out request slots that were
    # never granted. The merged row is a strictly safer state, so leave it in place.
    pass
