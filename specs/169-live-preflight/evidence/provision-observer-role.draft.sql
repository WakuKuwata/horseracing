-- REVIEWABLE DRAFT ONLY. This file has not been executed.
-- For the current unmodified 165 live backend, after separate authorization to provision a role.
-- Supply psql variables observer_role and database_name. Do not use a general owner/API role.
-- CREATE fails if the role already exists: do not silently alter an existing account.
-- Do not place passwords in this file, command arguments, console logs or research artifacts.
-- Use an administrator-controlled secret mechanism or interactive \password after creation.

\set ON_ERROR_STOP on

-- Refuse missing psql variables before any provisioning. The deliberate read-only SQL error
-- makes a failed guard exit nonzero under ON_ERROR_STOP, without a role or grant mutation.
\if :{?observer_role}
\else
  \echo 'Required psql variable observer_role is not set; draft refused.'
  SELECT 1 / 0;
\endif
\if :{?database_name}
\else
  \echo 'Required psql variable database_name is not set; draft refused.'
  SELECT 1 / 0;
\endif

BEGIN;

-- GRANT CONNECT names a database, but the public table grants act on the CURRENT connection.
-- Require both to identify the same database before CREATE ROLE or any other mutation.
SELECT current_database() = :'database_name' AS target_database_matches,
       octet_length(:'observer_role') BETWEEN 1 AND
         current_setting('max_identifier_length')::integer AS role_name_supported
\gset live_observer_guard_

\if :live_observer_guard_target_database_matches
\else
  \echo 'Connected database does not match database_name; draft refused.'
  SELECT 1 / 0;
\endif
\if :live_observer_guard_role_name_supported
\else
  \echo 'observer_role is empty or exceeds the identifier byte limit; draft refused.'
  SELECT 1 / 0;
\endif

CREATE ROLE :"observer_role"
  LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;

GRANT CONNECT ON DATABASE :"database_name" TO :"observer_role";
GRANT USAGE ON SCHEMA public TO :"observer_role";
GRANT SELECT ON TABLE
  public.races,
  public.race_horses,
  public.race_results,
  public.horses,
  public.id_mappings,
  public.fetch_throttle_state
TO :"observer_role";

-- Table-level SELECT above is conservative; permission-contract.json lists consumed columns.
-- Only the operational source reservation/cooldown table is writable.
GRANT INSERT (domain, next_allowed_at, updated_at)
  ON TABLE public.fetch_throttle_state TO :"observer_role";
GRANT UPDATE (next_allowed_at, blocked_until, block_reason, updated_at)
  ON TABLE public.fetch_throttle_state TO :"observer_role";

-- Current capture fetcher opens ordinary transactions for throttle state; it does not issue
-- SET TRANSACTION READ WRITE. Feature/guard transactions explicitly issue READ ONLY themselves.
ALTER ROLE :"observer_role" IN DATABASE :"database_name"
  SET default_transaction_read_only = off;

-- An existing role makes CREATE fail; ON_ERROR_STOP leaves this whole transaction uncommitted.
-- No partial provisioning survives any failed guard/CREATE/GRANT/ALTER statement.
COMMIT;

-- No sequence, DELETE, TRUNCATE, CREATE, model-prediction persistence or race-data write grant.
-- No ALTER DEFAULT PRIVILEGES: future relations are not automatically granted.
-- Builtin EXECUTE is normally available through PUBLIC, but verify actual ACLs for
-- pg_catalog.clock_timestamp() and pg_catalog.txid_current_snapshot(). If unavailable, review
-- separate narrowly scoped GRANT EXECUTE with the administrator; do not grant all functions.
-- PostgreSQL PUBLIC/inherited/database-owner privileges are effective independently of these
-- grants. Audit effective ACLs, membership, role flags, RLS, triggers/rules and search_path.
-- A successful ACL check is not an actual operational write test or authorization to collect.
