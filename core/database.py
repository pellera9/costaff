import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from .models import Base
from dotenv import load_dotenv

load_dotenv()

uri = os.getenv("ADK_SESSION_SERVICE_URI") or os.getenv("SQLALCHEMY_DATABASE_URL")
if not uri:
    raise RuntimeError(
        "Database URI not configured. "
        "Set ADK_SESSION_SERVICE_URI=postgresql+asyncpg://user:pass@host:5432/db in .env"
    )

# Strip async driver prefix — SQLAlchemy ORM needs the sync driver
if "+asyncpg" in uri:
    uri = uri.replace("+asyncpg", "")
if "+aiosqlite" in uri:
    raise RuntimeError(
        "SQLite is not supported. Use PostgreSQL: "
        "ADK_SESSION_SERVICE_URI=postgresql+asyncpg://user:pass@host:5432/db"
    )

SQLALCHEMY_DATABASE_URL = uri

# Pool sizing args are PostgreSQL-only; SQLite uses SingletonThreadPool
# and rejects them. Skip the kwargs for SQLite so unit tests can use
# `sqlite:///:memory:` without a TypeError on engine creation.
_engine_kwargs = {"pool_pre_ping": True}
if not uri.startswith("sqlite"):
    _engine_kwargs["pool_size"] = int(os.getenv("DB_POOL_SIZE", "10"))
    _engine_kwargs["max_overflow"] = int(os.getenv("DB_MAX_OVERFLOW", "20"))

engine = create_engine(SQLALCHEMY_DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE_REVISION = "0001_baseline"
# Dedicated alembic version table — keeps the core's migration bookkeeping
# separate from any other alembic environment sharing the same database
# (e.g. webchat-enterprise owns the default `alembic_version`). MUST match
# migrations/env.py VERSION_TABLE.
CORE_VERSION_TABLE = "costaff_alembic_version"


def _legacy_fixups(conn, inspector, existing):
    """Historical in-place migrations for pre-alembic databases.

    These ran on every boot before alembic existed; they bring an old schema
    up to the baseline shape so it can be safely stamped as the alembic
    baseline. Idempotent — guarded by column/table existence checks.
    """
    # Drop legacy tables replaced by the new schema
    for legacy in ("tasks", "task_logs"):
        if legacy in existing:
            conn.execute(text(f"DROP TABLE IF EXISTS {legacy} CASCADE"))
            conn.commit()

    # Migrate reminders: drop and recreate if it uses the old schema
    if "reminders" in existing:
        old_cols = {c["name"] for c in inspector.get_columns("reminders")}
        if "prompt" in old_cols or "cron" in old_cols:
            conn.execute(text("DROP TABLE IF EXISTS reminders CASCADE"))
            conn.commit()

    # identity_maps migrations
    if "identity_maps" in existing:
        cols = {c["name"] for c in inspector.get_columns("identity_maps")}
        if "session_id" not in cols:
            from .models import IdentityMap
            IdentityMap.__table__.drop(engine)
        else:
            if "is_approved" not in cols:
                conn.execute(text(
                    "ALTER TABLE identity_maps ADD COLUMN is_approved BOOLEAN NOT NULL DEFAULT FALSE"
                ))
                conn.commit()
            if "updated_at" not in cols:
                conn.execute(text(
                    "ALTER TABLE identity_maps ADD COLUMN updated_at TIMESTAMP"
                ))
                conn.commit()
            # active_session_id / last_message_id are defined on the
            # IdentityMap model but only get auto-created by create_all on a
            # brand-new table; an identity_maps created by an older build
            # (or by a non-enterprise install that never ran the WebChat
            # Enterprise alembic) lacks them, and require_approved /
            # get_user_channel_info / project_task SELECT these columns ->
            # psycopg2 UndefinedColumn -> the Manager improvises a
            # "maintenance" excuse. Backfill them idempotently here.
            if "active_session_id" not in cols:
                conn.execute(text(
                    "ALTER TABLE identity_maps ADD COLUMN active_session_id VARCHAR"
                ))
                conn.commit()
            if "last_message_id" not in cols:
                conn.execute(text(
                    "ALTER TABLE identity_maps ADD COLUMN last_message_id VARCHAR"
                ))
                conn.commit()

    # api_configs / skill_configs: add agent_ids if missing
    for tbl in ("api_configs", "skill_configs"):
        if tbl in existing:
            cols = {c["name"] for c in inspector.get_columns(tbl)}
            if "agent_ids" not in cols:
                conn.execute(text(
                    f"ALTER TABLE {tbl} ADD COLUMN agent_ids VARCHAR DEFAULT '__all__'"
                ))
                conn.commit()


def _alembic_config():
    from alembic.config import Config

    cfg = Config(os.path.join(_REPO_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_REPO_ROOT, "migrations"))
    cfg.set_main_option("sqlalchemy.url", SQLALCHEMY_DATABASE_URL)
    return cfg


def _run_alembic(action: str, *args):
    """Run an alembic command against the live engine in one transaction."""
    from alembic import command

    cfg = _alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        getattr(command, action)(cfg, *args)


def _bootstrap_schema():
    """Bring the database schema to head.

    - SQLite (unit tests) or ``COSTAFF_DISABLE_ALEMBIC`` set → plain
      ``create_all`` (alembic / Postgres DDL is not needed there).
    - Pre-alembic Postgres deployment (core tables exist, no
      ``alembic_version``) → apply the historical fixups, ensure all baseline
      tables exist, then *stamp* the baseline and upgrade (adopts the current
      schema without re-creating it).
    - Fresh DB or already tracked → ``alembic upgrade head`` owns it.
    """
    from sqlalchemy import inspect

    inspector = inspect(engine)
    existing = set(inspector.get_table_names())

    if engine.dialect.name == "sqlite" or os.getenv("COSTAFF_DISABLE_ALEMBIC"):
        Base.metadata.create_all(bind=engine)
        return

    has_alembic = CORE_VERSION_TABLE in existing
    has_core = "identity_maps" in existing

    if not has_alembic and has_core:
        with engine.connect() as conn:
            _legacy_fixups(conn, inspector, existing)
        Base.metadata.create_all(bind=engine)
        _run_alembic("stamp", BASELINE_REVISION)
        _run_alembic("upgrade", "head")
        return

    _run_alembic("upgrade", "head")


def init_db():
    _bootstrap_schema()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
