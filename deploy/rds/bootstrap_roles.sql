-- AASTAR Price Dashboard — database roles (run once per environment as the RDS privileged account).
-- Replace the passwords (store them in KMS Secrets Manager), then run:
--   psql "host=<rds-endpoint> dbname=postgres user=<privileged> sslmode=require" \
--        -v owner_pw="'...'" -v app_pw="'...'" -v ro_pw="'...'" -f bootstrap_roles.sql
-- Afterwards run `alembic upgrade head` with MIGRATION_DATABASE_URL (aastar_owner); the migration grants
-- least-privilege rights to aastar_app / aastar_ro (TD §7.4).

CREATE ROLE aastar_owner LOGIN PASSWORD :owner_pw;
CREATE ROLE aastar_app   LOGIN PASSWORD :app_pw CONNECTION LIMIT 40;
CREATE ROLE aastar_ro    LOGIN PASSWORD :ro_pw  CONNECTION LIMIT 5;

CREATE DATABASE aastar OWNER aastar_owner ENCODING 'UTF8' TEMPLATE template0;
\connect aastar
REVOKE ALL ON DATABASE aastar FROM PUBLIC;
GRANT CONNECT ON DATABASE aastar TO aastar_app, aastar_ro;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER DATABASE aastar SET timezone TO 'UTC';
ALTER ROLE aastar_app SET statement_timeout = '120s';
ALTER ROLE aastar_ro  SET default_transaction_read_only = on;
