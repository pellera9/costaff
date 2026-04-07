import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from .models import Base
from dotenv import load_dotenv

load_dotenv()

# 優先讀取 mate onboard 設定的資料庫 (ADK_SESSION_SERVICE_URI)
# 如果沒有設定，才使用 SQLALCHEMY_DATABASE_URL 或預設的 mate_agent.db
uri = os.getenv("ADK_SESSION_SERVICE_URI") or os.getenv("SQLALCHEMY_DATABASE_URL") or "sqlite:///./data/mate_agent.db"

# 轉換為 SQLAlchemy 同步模式路徑 (移除 +aiosqlite 或 +asyncpg)
if "+aiosqlite" in uri:
    uri = uri.replace("+aiosqlite", "")
if "+asyncpg" in uri:
    uri = uri.replace("+asyncpg", "")

SQLALCHEMY_DATABASE_URL = uri

_is_sqlite = "sqlite" in SQLALCHEMY_DATABASE_URL
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    **({} if _is_sqlite else {"pool_size": 10, "max_overflow": 20, "pool_pre_ping": True})
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    from sqlalchemy import inspect, text
    inspector = inspect(engine)

    # Migration: identity_maps — add session_id if missing
    if "identity_maps" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("identity_maps")]
        if "session_id" not in columns:
            from .models import IdentityMap
            IdentityMap.__table__.drop(engine)

    # Migration: task_logs — add user_id if missing
    if "task_logs" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("task_logs")]
        if "user_id" not in columns:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE task_logs ADD COLUMN user_id VARCHAR NOT NULL DEFAULT ''"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS ix_task_logs_user_id ON task_logs (user_id)"))
                conn.commit()

    # Migration: identity_maps — add is_approved if missing
    if "identity_maps" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("identity_maps")]
        if "is_approved" not in columns:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE identity_maps ADD COLUMN is_approved BOOLEAN NOT NULL DEFAULT FALSE"))
                conn.commit()

    # Migration: api_configs — add agent_ids if missing
    if "api_configs" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("api_configs")]
        if "agent_ids" not in columns:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE api_configs ADD COLUMN agent_ids VARCHAR DEFAULT '__all__'"))
                conn.commit()

    # Migration: skill_configs — add agent_ids if missing
    if "skill_configs" in inspector.get_table_names():
        columns = [c["name"] for c in inspector.get_columns("skill_configs")]
        if "agent_ids" not in columns:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE skill_configs ADD COLUMN agent_ids VARCHAR DEFAULT '__all__'"))
                conn.commit()

    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
