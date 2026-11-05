-- AASTAR Price Dashboard — database roles (run once per environment as the RDS privileged account).
-- Keep the passwords in KMS Secrets Manager / the server's env file, then run (values are quoted by psql):
--   psql "host=<rds-endpoint> port=5432 dbname=postgres user=<privileged> sslmode=require" \
--        -v owner_pw="$OWNER_PW" -v app_pw="$APP_PW" -v ro_pw="$RO_PW" -f bootstrap_roles.sql
-- Afterwards run `alembic upgrade head` with MIGRATION_DATABASE_URL (aastar_owner); the migration grants
-- least-privilege rights to aastar_app / aastar_ro (TD §7.4).
\set ON_ERROR_STOP on

CREATE ROLE aastar_owner LOGIN PASSWORD :'owner_pw';
CREATE ROLE aastar_app   LOGIN PASSWORD :'app_pw' CONNECTION LIMIT 40;
CREATE ROLE aastar_ro    LOGIN PASSWORD :'ro_pw'  CONNECTION LIMIT 5;

-- PostgreSQL 16+: the RDS privileged account is not a superuser, so it needs membership in the owner role
-- before CREATE DATABASE … OWNER aastar_owner is allowed.
GRANT aastar_owner TO CURRENT_USER;

CREATE DATABASE aastar OWNER aastar_owner ENCODING 'UTF8' TEMPLATE template0;
\connect aastar
REVOKE ALL ON DATABASE aastar FROM PUBLIC;
GRANT CONNECT ON DATABASE aastar TO aastar_app, aastar_ro;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER DATABASE aastar SET timezone TO 'UTC';
ALTER ROLE aastar_app SET statement_timeout = '120s';
ALTER ROLE aastar_ro  SET default_transaction_read_only = on;
