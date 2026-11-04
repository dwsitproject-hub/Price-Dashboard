#!/bin/sh
# Runs once, on first start of an empty data volume (postgres image /docker-entrypoint-initdb.d).
# Creates the least-privilege runtime role; Alembic (run as the owner role) grants it table rights.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE aastar_app LOGIN PASSWORD '${APP_DB_PASSWORD}' CONNECTION LIMIT 40;
GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO aastar_app;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER DATABASE ${POSTGRES_DB} SET timezone TO 'UTC';
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO aastar_app;
SQL
