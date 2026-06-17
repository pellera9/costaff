"""Multi-CoStaff core switcher API.

GET  /api/cores         → registered cores + which is active
POST /api/cores/active  → switch the active core (all read endpoints follow)
"""
import logging

from fastapi import APIRouter, HTTPException, Depends

from services.auth import AuthManager
from services import cores as core_svc

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/cores")
def get_cores(auth: bool = Depends(AuthManager.verify_token)):
    return core_svc.list_cores()


@router.post("/api/cores/active")
def set_active_core(req: dict, auth: bool = Depends(AuthManager.verify_token)):
    name = (req or {}).get("name")
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    try:
        core_svc.set_active(name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "ok", "active": name}
