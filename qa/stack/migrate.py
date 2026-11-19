"""Apply pending migrations to the QA database (after code changes): backend/.venv/Scripts/python qa/stack/migrate.py"""
import qa_env

qa_env.load()
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

cfg = Config(str(qa_env.BACKEND / "alembic.ini"))
cfg.set_main_option("script_location", str(qa_env.BACKEND / "migrations"))
command.upgrade(cfg, "head")
print("QA database at head")
