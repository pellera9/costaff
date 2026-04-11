from fastapi import APIRouter, Depends
from sqlalchemy import text

from managers.auth import AuthManager
from managers.database import DatabaseManager
from utils.helpers import _serialize_row

router = APIRouter()


@router.get("/api/diary")
def list_diary(days: int = 7, auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine:
        return []
    try:
        with engine.connect() as conn:
            res = conn.execute(text(
                "SELECT id, user_id, agent_name, date, type, done, blocker, next, created_at "
                "FROM diary ORDER BY date DESC, agent_name ASC LIMIT :lim"
            ), {"lim": days * 10})
            return [_serialize_row(dict(r._mapping)) for r in res]
    except Exception:
        return []
