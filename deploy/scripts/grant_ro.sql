-- Feature 018: idempotent read-only serving role (FR-008). The API connects as this role so
-- serving is read-only by DB PRIVILEGE (not app convention); migrations run as the owner.
-- Parameters via psql -v: ro_user, ro_password, db_name.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'ro_user') THEN
        EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', :'ro_user', :'ro_password');
    ELSE
        EXECUTE format('ALTER ROLE %I LOGIN PASSWORD %L', :'ro_user', :'ro_password');
    END IF;
END $$;

GRANT CONNECT ON DATABASE :"db_name" TO :"ro_user";
GRANT USAGE ON SCHEMA public TO :"ro_user";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"ro_user";
-- future tables created by the owner also become SELECT-able by the read-only role.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO :"ro_user";
-- explicitly ensure NO write privileges (revoke any default write grants).
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public FROM :"ro_user";
