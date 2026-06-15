"""Alembic environment for the CoStaff core database.

Online-only. The DB URL is resolved programmatically:

  1. ``COSTAFF_ALEMBIC_URL`` — set by the host CLI (`costaff database
     migrate`) to a localhost-reachable URL, since the host can't resolve
     the ``postgres`` compose hostname.
  2. else ``ADK_SESSION_SERVICE_URI`` / ``SQLALCHEMY_DATABASE_URL`` — the
     value the containers use.

The async driver suffix is stripped (``+asyncpg`` → sync) because alembic
runs synchronously.

We deliberately DO NOT call ``logging.config.fileConfig()``: doing so at
import time can deadlock when this env is driven from a process that already
holds Python's global logging lock (e.g. the MCP container startup). Alembic's
INFO output piggybacks on the host logger fine without it.
"""
import os
import sys

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the repo root importable so `core.models` resolves whether alembic is
# invoked from the container CMD, the host CLI, or pytest.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from core.models import Base  # noqa: E402

config = context.config


def _db_url() -> str:
    url = (
        os.getenv("COSTAFF_ALEMBIC_URL")
        or os.getenv("ADK_SESSION_SERVICE_URI")
        or os.getenv("SQLALCHEMY_DATABASE_URL")
        or config.get_main_option("sqlalchemy.url")
    )
    if url:
        url = url.replace("+asyncpg", "")
    return url


config.set_main_option("sqlalchemy.url", _db_url())
target_metadata = Base.metadata


def run_migrations_online() -> None:
    # When driven from core.database (stamp / upgrade at boot) a live
    # connection is handed in via attributes, so we don't open a second one.
    existing = config.attributes.get("connection")
    if existing is not None:
        context.configure(connection=existing, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return

    section = config.get_section(config.config_ini_section) or {}
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
