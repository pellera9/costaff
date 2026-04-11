from fastapi import APIRouter, HTTPException

from managers.auth import AuthManager
from models.requests import LoginRequest, SetupRequest
from utils.helpers import VERSION

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok", "version": VERSION}


@router.get("/api/check-setup")
def check_setup():
    return {"is_setup": AuthManager.get_auth() is not None}


@router.post("/api/setup")
def setup_account(req: SetupRequest):
    if AuthManager.get_auth() is not None:
        raise HTTPException(status_code=400, detail="Account already exists")
    AuthManager.save_auth(req.username, req.password)
    return {"status": "success"}


@router.post("/api/login")
def login(req: LoginRequest):
    auth = AuthManager.get_auth()
    if auth and req.username == auth["username"] and AuthManager.hash_password(req.password, auth["salt"])[0] == auth["hashed"]:
        return {"token": AuthManager.SESSION_TOKEN}
    raise HTTPException(status_code=401, detail="Invalid credentials")
