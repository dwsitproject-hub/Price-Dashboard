"""Alembic environment. Migrations are SQL-first (see versions/); no autogenerate."""
from alembic import context
from sqlalchemy import create_engine, pool

from app.core.config import get_settings

settings = get_settings()
url = settings.migration_database_url or settings.database_url


def run_migrations_offline() -> None:
    context.configure(url=url, literal_binds=True, version_table_schema="public")
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, version_table_schema="public")
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
